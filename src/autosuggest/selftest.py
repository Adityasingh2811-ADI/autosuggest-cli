"""
Self-test — verifies the autosuggest installation is healthy.

Checks: database, daemon, hook presence, PATH, and environment.
"""

import os
import socket
import sqlite3
import sys
from pathlib import Path

from autosuggest import __version__
from autosuggest.paths import (
    db_path,
    pid_path,
    socket_path,
    token_path,
    apply_journal_mode,
)


def _check_pass(label: str) -> None:
    print(f"  \u2714 {label}")


def _check_fail(label: str, detail: str = "") -> None:
    msg = f"  \u2718 {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def _check_skip(label: str, reason: str = "") -> None:
    msg = f"  - {label}"
    if reason:
        msg += f"  ({reason})"
    print(msg)


def run() -> None:
    print(f"autosuggest self-test  v{__version__}\n")
    failures = 0

    # 1. Database
    print("[database]")
    dbp = db_path()
    if dbp.exists():
        _check_pass(f"history db exists: {dbp}")
        try:
            conn = sqlite3.connect(str(dbp), check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=3000;")
            apply_journal_mode(conn, dbp)
            row = conn.execute("SELECT COUNT(*) FROM history").fetchone()
            _check_pass(f"history table readable: {row[0]} rows")
            conn.close()
        except Exception as exc:
            _check_fail("database query", str(exc))
            failures += 1
    else:
        _check_fail("history db missing", str(dbp))
        failures += 1

    # 2. Daemon
    print("\n[daemon]")
    pp = pid_path()
    if pp.exists():
        pid_text = pp.read_text().strip()
        try:
            pid = int(pid_text)
            os.kill(pid, 0)
            _check_pass(f"daemon running (pid {pid})")
        except (ValueError, ProcessLookupError, PermissionError):
            _check_fail("stale pidfile", f"pid={pid_text}")
            failures += 1
    else:
        _check_fail("daemon not running", "no pidfile")
        failures += 1

    sp = socket_path()
    if sp and Path(sp).exists():
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(sp)
            s.close()
            _check_pass(f"socket connectable: {sp}")
        except Exception as exc:
            _check_fail("socket connect failed", str(exc))
            failures += 1
    elif sp:
        _check_fail("socket missing", sp)
        failures += 1
    else:
        _check_skip("socket", "not applicable on this platform")

    # 3. Auth token
    print("\n[auth]")
    tp = token_path()
    if tp.exists():
        _check_pass(f"auth token exists: {tp}")
    else:
        _check_fail("auth token missing", str(tp))
        failures += 1

    # 4. PATH / entry points
    print("\n[path]")
    from shutil import which
    for cmd in ("suggest", "suggest-start", "suggest-daemon", "suggest-hook"):
        loc = which(cmd)
        if loc:
            _check_pass(f"{cmd} -> {loc}")
        else:
            _check_fail(f"{cmd} not in PATH")
            failures += 1

    # 5. Environment
    print("\n[environment]")
    shell = os.environ.get("SHELL", "")
    if shell:
        _check_pass(f"SHELL={shell}")
    else:
        _check_fail("SHELL not set")
        failures += 1

    xdg_data = os.environ.get("XDG_DATA_HOME", "")
    if xdg_data:
        _check_pass(f"XDG_DATA_HOME={xdg_data}")
    else:
        _check_skip("XDG_DATA_HOME not set (using default)")

    # Summary
    print()
    if failures == 0:
        print(f"All checks passed.")
    else:
        print(f"{failures} check(s) failed.")
    sys.exit(0 if failures == 0 else 1)
