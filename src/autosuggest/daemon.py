"""
Telemetry daemon — listens for JSON packets and writes them to SQLite
without blocking the connected client.

Uses a Unix domain socket on Linux/macOS, TCP localhost on Windows.
Supports subcommands: start (default), stop, status.
"""

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = Path.home() / ".cli_autosuggest.db"
PID_PATH = Path.home() / ".cli_autosuggest.pid"
SOCKET_PATH = "/tmp/cli_autosuggest.sock"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

IS_WINDOWS = sys.platform == "win32"
TCP_HOST = "127.0.0.1"
TCP_PORT = 19526


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), isolation_level="DEFERRED", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    db: sqlite3.Connection,
) -> None:
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        if not data:
            return
        payload = json.loads(data.decode("utf-8"))
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
    db.execute(
        "INSERT INTO command_history (command, cwd, exit_status) VALUES (?, ?, ?)",
        (payload["command"], payload["cwd"], payload.get("exit_status", 0)),
    )
    db.commit()


async def main() -> None:
    db = init_db()

    if IS_WINDOWS:
        server = await asyncio.start_server(
            lambda r, w: handle_client(r, w, db),
            host=TCP_HOST,
            port=TCP_PORT,
        )
        print(f"[daemon] listening on {TCP_HOST}:{TCP_PORT} | db: {DB_PATH}")
    else:
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        server = await asyncio.start_unix_server(
            lambda r, w: handle_client(r, w, db),
            path=SOCKET_PATH,
        )
        os.chmod(SOCKET_PATH, 0o600)
        print(f"[daemon] listening on {SOCKET_PATH} | db: {DB_PATH}")

    async with server:
        await server.serve_forever()


def _write_pidfile() -> None:
    PID_PATH.write_text(str(os.getpid()))


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


def _cmd_start() -> None:
    """Start the daemon (default subcommand)."""
    running, pid = is_daemon_running()
    if running:
        print(f"[daemon] already running (PID {pid})")
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
        _cmd_start()
    elif cmd == "stop":
        _cmd_stop()
    elif cmd == "status":
        _cmd_status()
    elif cmd in ("--help", "-h"):
        print("Usage: suggest-daemon [start|stop|status]")
        print()
        print("  start   start the telemetry daemon (default)")
        print("  stop    stop the running daemon")
        print("  status  check if the daemon is running")
    else:
        print(f"[daemon] unknown command: {cmd}")
        print("Usage: suggest-daemon [start|stop|status]")


if __name__ == "__main__":
    run_daemon()
