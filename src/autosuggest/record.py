"""
One-shot telemetry sender — delivers a single command record to the running
daemon (Unix socket first, TCP localhost fallback) and falls back to a direct
SQLite insert if the daemon is unreachable.

Used by the tcsh hook, where bash's inline /dev/tcp + socat plumbing is not
available, so recording is funnelled through a short Python invocation instead.

Usage: python -m autosuggest.record <command> <cwd> [exit_status]
"""

import json
import socket
import sys

from autosuggest.paths import IS_WINDOWS, socket_path, token_path, TCP_PORT
from autosuggest.redact import redact

# Resolved here (not imported from daemon) so this hot per-prompt path avoids
# importing asyncio and the rest of the daemon module.
SOCKET_PATH = socket_path()


def _read_token() -> str:
    """Read the daemon auth token (required by the TCP transport)."""
    try:
        return token_path().read_text().strip()
    except OSError:
        return ""


def _send_socket(payload: bytes) -> bool:
    """Try the Unix socket (POSIX) then TCP localhost. True if delivered."""
    if not IS_WINDOWS:
        try:
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(0.2)
            s.connect(str(SOCKET_PATH))
            s.sendall(payload)
            s.close()
            return True
        except OSError:
            pass
    try:
        s = socket.socket()
        s.settimeout(0.2)
        s.connect(("127.0.0.1", TCP_PORT))
        s.sendall(payload)
        s.close()
        return True
    except OSError:
        return False


def _send_db(command: str, cwd: str, status: int) -> None:
    """Fallback path: write straight to the database when no daemon answers."""
    import time

    from autosuggest.daemon import init_db

    conn = init_db()
    try:
        conn.execute(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (command, cwd, status, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def send_record(command: str, cwd: str, status: int = 0) -> None:
    """Record one command via the daemon, falling back to a direct DB insert."""
    command = redact(command.strip())
    if not command:
        return
    payload = json.dumps(
        {
            "command": command,
            "cwd": cwd,
            "exit_status": status,
            "token": _read_token(),
        }
    ).encode("utf-8")
    if not _send_socket(payload):
        try:
            _send_db(command, cwd, status)
        except Exception:
            pass


def main() -> None:
    """CLI entry point: python -m autosuggest.record <command> <cwd> [status]."""
    args = sys.argv[1:]
    if len(args) < 2:
        return
    try:
        status = int(args[2]) if len(args) >= 3 else 0
    except ValueError:
        status = 0
    send_record(args[0], args[1], status)


if __name__ == "__main__":
    main()
