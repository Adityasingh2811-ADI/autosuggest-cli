"""
Telemetry daemon — listens for JSON packets and writes them to SQLite
without blocking the connected client.

Uses a Unix domain socket on Linux/macOS, TCP localhost on Windows.
Supports subcommands: start (default), stop, status.
"""

import asyncio
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from autosuggest.paths import db_path, pid_path, socket_path, token_path
from autosuggest.redact import redact
from autosuggest.paths import IS_WINDOWS, apply_journal_mode, TCP_HOST, TCP_PORT
from autosuggest.engine import _SCHEMA

DB_PATH = db_path()
PID_PATH = pid_path()
SOCKET_PATH = socket_path()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), isolation_level="DEFERRED", check_same_thread=False)
    # Set the busy timeout FIRST so the journal-mode switch below can wait for
    # a lock instead of failing immediately when another connection is open.
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    # WAL is unsafe on network filesystems (NFS/CIFS); fall back where needed.
    # Tolerant: if the daemon already holds the DB, keep the current mode.
    apply_journal_mode(conn, DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


# Keep the history table bounded so it can't grow without limit on long-lived
# accounts (NFS homes especially). Tunable via env for power users.
MAX_HISTORY_ROWS = int(os.environ.get("AUTOSUGGEST_MAX_ROWS", "50000"))
MAX_HISTORY_AGE_DAYS = int(os.environ.get("AUTOSUGGEST_MAX_AGE_DAYS", "365"))


def _prune_db(conn: sqlite3.Connection) -> int:
    """Trim old/excess rows. Returns the number of rows deleted.

    Deletes anything older than MAX_HISTORY_AGE_DAYS, then caps the table at
    MAX_HISTORY_ROWS most-recent rows. Runs once at daemon startup.
    """
    deleted = 0
    try:
        if MAX_HISTORY_AGE_DAYS > 0:
            cutoff = time.time() - MAX_HISTORY_AGE_DAYS * 86400
            cur = conn.execute(
                "DELETE FROM command_history WHERE timestamp < ?", (cutoff,)
            )
            deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if MAX_HISTORY_ROWS > 0:
            cur = conn.execute(
                "DELETE FROM command_history WHERE id NOT IN "
                "(SELECT id FROM command_history ORDER BY timestamp DESC LIMIT ?)",
                (MAX_HISTORY_ROWS,),
            )
            deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
    except sqlite3.Error as e:
        print(f"[daemon] prune failed: {e}", file=sys.stderr)
    return deleted


def dedupe_history(conn: sqlite3.Connection) -> int:
    """Collapse exact-duplicate rows and return how many were removed.

    Pre-dedup versions of ``suggest-import`` inserted without a uniqueness
    guard, so re-running it multiplied identical (command, cwd, timestamp)
    rows and inflated frecency scores. This keeps one row per
    (command, cwd, timestamp): distinct occurrences within a single import
    (which get spread-out timestamps) are preserved, and live-recorded rows
    (unique high-resolution timestamps) are never touched. Idempotent and
    safe to run on every startup — a clean DB yields zero deletions.
    """
    removed = 0
    try:
        cur = conn.execute(
            "DELETE FROM command_history WHERE id NOT IN "
            "(SELECT MIN(id) FROM command_history "
            " GROUP BY command, cwd, timestamp)"
        )
        removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
    except sqlite3.Error as e:
        print(f"[daemon] dedupe failed: {e}", file=sys.stderr)
    return removed


def _load_or_create_token() -> str:
    """Return the per-user auth token, creating it privately if absent.

    The token gates the TCP transport so that other local processes on a shared
    host cannot inject commands into the user's history over 127.0.0.1.
    """
    tp = token_path()
    try:
        existing = tp.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    tok = secrets.token_hex(16)
    old_umask = None if IS_WINDOWS else os.umask(0o077)
    try:
        tp.write_text(tok)
        if not IS_WINDOWS:
            try:
                tp.chmod(0o600)
            except OSError:
                pass
    finally:
        if old_umask is not None:
            os.umask(old_umask)
    return tok


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    db: sqlite3.Connection,
    require_token: bool = False,
    token: str | None = None,
) -> None:
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        if not data:
            return
        payload = json.loads(data.decode("utf-8"))
        if require_token and not secrets.compare_digest(
            str(payload.get("token", "")), token or ""
        ):
            print("[daemon] rejected packet: bad or missing token", file=sys.stderr)
            return
        await asyncio.get_running_loop().run_in_executor(
            None, _insert_row, db, payload
        )
        writer.write(b'{"status":"ok"}\n')
        await writer.drain()
    except (asyncio.TimeoutError, json.JSONDecodeError, KeyError) as e:
        print(f"[daemon] bad packet: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[daemon] error: {e}", file=sys.stderr)
    finally:
        writer.close()
        await writer.wait_closed()


def _insert_row(db: sqlite3.Connection, payload: dict) -> None:
    command = redact(payload["command"])
    if not command:
        return
    db.execute(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (command, payload["cwd"], payload.get("exit_status", 0), time.time()),
    )
    db.commit()


def _reap_stale_socket() -> None:
    """Remove a leftover socket file only if nothing is listening on it.

    Guards against deleting a live daemon's socket while still clearing stale
    files left by an unclean shutdown or NFS lock.
    """
    if not os.path.exists(SOCKET_PATH):
        return
    probe = socket.socket(socket.AF_UNIX)
    probe.settimeout(0.2)
    try:
        probe.connect(SOCKET_PATH)
        # Something is already listening — leave it alone.
        return
    except OSError:
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            pass
    finally:
        try:
            probe.close()
        except OSError:
            pass


async def main() -> None:
    db = init_db()
    removed = dedupe_history(db)
    if removed:
        print(f"[daemon] removed {removed} duplicate rows from earlier imports")
    _prune_db(db)
    token = _load_or_create_token()

    if IS_WINDOWS:
        server = await asyncio.start_server(
            lambda r, w: handle_client(r, w, db, require_token=True, token=token),
            host=TCP_HOST,
            port=TCP_PORT,
        )
        print(f"[daemon] listening on {TCP_HOST}:{TCP_PORT} | db: {DB_PATH}")
    else:
        _reap_stale_socket()
        # Create the socket with a restrictive umask so there is no window in
        # which it is world-connectable before we chmod it (TOCTOU fix).
        old_umask = os.umask(0o177)
        try:
            server = await asyncio.start_unix_server(
                lambda r, w: handle_client(r, w, db),
                path=SOCKET_PATH,
            )
        finally:
            os.umask(old_umask)
        try:
            os.chmod(SOCKET_PATH, 0o600)
        except OSError:
            pass
        print(f"[daemon] listening on {SOCKET_PATH} | db: {DB_PATH}")

    async with server:
        await server.serve_forever()


# Held for the daemon's lifetime to enforce a single running instance.
_singleton_lock_fh = None


def _write_pidfile() -> None:
    """Write the pidfile atomically (write-temp + rename) to avoid torn reads."""
    tmp = PID_PATH.with_name(f"{PID_PATH.name}.{os.getpid()}.tmp")
    tmp.write_text(str(os.getpid()))
    os.replace(tmp, PID_PATH)


def _acquire_singleton_lock() -> bool:
    """Take an exclusive lock so two concurrent starts can't both run.

    Returns True if we got the lock (and should proceed), False if another
    instance already holds it. POSIX-only; Windows relies on the pid check.
    """
    global _singleton_lock_fh
    if IS_WINDOWS:
        return True
    import fcntl

    lock_file = PID_PATH.with_suffix(".lock")
    fh = open(lock_file, "w")
    try:
        fcntl.lockf(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _singleton_lock_fh = fh
    return True


def _remove_pidfile() -> None:
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _read_pid() -> int | None:
    """Read PID from pidfile. Returns None if file missing or invalid."""
    try:
        return int(PID_PATH.read_text().strip())
    except (OSError, ValueError):
        return None


def is_daemon_running() -> tuple[bool, int | None]:
    """Check if daemon is running. Returns (is_running, pid)."""
    pid = _read_pid()
    if pid is None:
        return False, None
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True,
            )
            alive = str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            alive = True
    except (OSError, ProcessLookupError):
        alive = False

    if not alive:
        _remove_pidfile()
    return alive, pid if alive else None


def _redirect_stdio_to_log() -> None:
    """Point stdout/stderr at the daemon log so a detached daemon's output and
    errors are captured instead of lost to a closed terminal."""
    from autosuggest.paths import runtime_dir

    try:
        log = runtime_dir() / "daemon.log"
        fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
    except OSError:
        pass


def _cmd_start(foreground: bool = False) -> None:
    """Start the daemon (default subcommand).

    By default this detaches into the background (double-fork on POSIX) so a
    manual ``suggest-daemon start`` returns immediately instead of blocking the
    shell. Pass ``foreground=True`` (``--foreground``) to run in the current
    process, e.g. under a supervisor.
    """
    running, pid = is_daemon_running()
    if running:
        print(f"[daemon] already running (PID {pid})")
        return

    if not foreground and not IS_WINDOWS:
        # Double-fork so the daemon outlives the launching shell and does not
        # keep it blocked. The original process returns to the caller.
        try:
            pid1 = os.fork()
        except OSError:
            pid1 = -1  # fork unavailable — fall back to foreground
        if pid1 > 0:
            return  # parent: hand control straight back to the shell
        if pid1 == 0:
            os.setsid()
            try:
                if os.fork() > 0:
                    os._exit(0)  # first child exits; grandchild is the daemon
            except OSError:
                pass
            _redirect_stdio_to_log()
        # pid1 == -1 falls through and runs in the foreground.

    if not _acquire_singleton_lock():
        print("[daemon] another instance is starting", file=sys.stderr)
        if not foreground and not IS_WINDOWS:
            os._exit(0)
        return

    try:
        _write_pidfile()
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if not IS_WINDOWS and os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        _remove_pidfile()
        print("\n[daemon] shutdown complete.")


def _cmd_stop() -> None:
    """Stop the running daemon."""
    running, pid = is_daemon_running()
    if not running:
        print("[daemon] not running")
        return

    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        _remove_pidfile()
        print(f"[daemon] stopped (PID {pid})")
    except OSError as e:
        print(f"[daemon] failed to stop: {e}")


def _cmd_status() -> None:
    """Print daemon status."""
    running, pid = is_daemon_running()
    if running:
        print(f"[daemon] running (PID {pid})")
        print(f"  db: {DB_PATH}")
        if IS_WINDOWS:
            print(f"  socket: {TCP_HOST}:{TCP_PORT}")
        else:
            print(f"  socket: {SOCKET_PATH}")
    else:
        print("[daemon] not running")


def run_daemon() -> None:
    """CLI entry point with subcommand routing."""
    args = sys.argv[1:]
    cmd = args[0] if args else "start"

    if cmd == "start":
        foreground = "--foreground" in args or "-f" in args
        _cmd_start(foreground=foreground)
    elif cmd == "stop":
        _cmd_stop()
    elif cmd == "status":
        _cmd_status()
    elif cmd in ("--version", "-V"):
        from autosuggest import __version__

        print(f"suggest-daemon {__version__}")
    elif cmd in ("--help", "-h"):
        print("Usage: suggest-daemon [start [--foreground]|stop|status]")
        print()
        print("  start   start the telemetry daemon (default; detaches)")
        print("    --foreground, -f   run in the foreground (do not detach)")
        print("  stop    stop the running daemon")
        print("  status  check if the daemon is running")
    else:
        print(f"[daemon] unknown command: {cmd}")
        print("Usage: suggest-daemon [start|stop|status]")


if __name__ == "__main__":
    run_daemon()
