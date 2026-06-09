"""
History importer — parses bash and PowerShell history files and bulk-inserts
commands into the autosuggest SQLite database.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

from autosuggest.daemon import DB_PATH, init_db

BASH_HISTORY_PATH = Path.home() / ".bash_history"
PS_HISTORY_PATH = (
    Path.home()
    / "AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"
)

BATCH_SIZE = 1000


def _parse_bash_history(path: Path) -> list[tuple[str, float | None]]:
    """Parse bash history file. Returns list of (command, timestamp_or_None)."""
    entries: list[tuple[str, float | None]] = []
    if not path.exists():
        return entries

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    pending_ts: float | None = None

    for line in lines:
        if line.startswith("#") and line[1:].strip().isdigit():
            pending_ts = float(line[1:].strip())
            continue
        cmd = line.strip()
        if len(cmd) >= 2:
            entries.append((cmd, pending_ts))
            pending_ts = None

    return entries


def _parse_powershell_history(path: Path) -> list[tuple[str, float | None]]:
    """Parse PowerShell PSReadLine history. Returns list of (command, None)."""
    entries: list[tuple[str, float | None]] = []
    if not path.exists():
        return entries

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Multi-line commands use backtick continuation
        while line.endswith("`") and i + 1 < len(lines):
            i += 1
            line = line[:-1] + "\n" + lines[i]
        cmd = line.strip()
        if len(cmd) >= 2:
            entries.append((cmd, None))
        i += 1

    return entries


def _spread_timestamps(
    entries: list[tuple[str, float | None]], file_mtime: float
) -> list[tuple[str, float]]:
    """Assign timestamps to entries that don't have one.

    Spreads entries evenly between (mtime - 30 days) and mtime for entries
    without explicit timestamps, so frecency decay produces varied scores.
    """
    now = time.time()
    span = 30 * 24 * 3600  # 30 days
    start = file_mtime - span

    result: list[tuple[str, float]] = []
    no_ts_indices: list[int] = []

    for i, (cmd, ts) in enumerate(entries):
        if ts is not None:
            result.append((cmd, ts))
        else:
            result.append((cmd, 0.0))  # placeholder
            no_ts_indices.append(i)

    if no_ts_indices:
        count = len(no_ts_indices)
        for rank, idx in enumerate(no_ts_indices):
            synthetic_ts = start + (rank / max(count - 1, 1)) * span
            synthetic_ts = min(synthetic_ts, now)
            result[idx] = (result[idx][0], synthetic_ts)

    return result


def _bulk_insert(conn: sqlite3.Connection, entries: list[tuple[str, float]]) -> int:
    """Insert entries in batches. Returns number of rows inserted."""
    inserted = 0
    for i in range(0, len(entries), BATCH_SIZE):
        batch = entries[i : i + BATCH_SIZE]
        conn.executemany(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
            "VALUES (?, ?, 0, ?)",
            [(cmd, "~", ts) for cmd, ts in batch],
        )
        conn.commit()
        inserted += len(batch)
    return inserted


def import_bash(path: Path | None = None) -> int:
    """Import bash history. Returns number of commands imported."""
    path = path or BASH_HISTORY_PATH
    if not path.exists():
        print(f"[import] bash history not found: {path}")
        return 0

    entries = _parse_bash_history(path)
    if not entries:
        print(f"[import] no commands found in {path}")
        return 0

    mtime = path.stat().st_mtime
    timestamped = _spread_timestamps(entries, mtime)

    conn = init_db()
    try:
        count = _bulk_insert(conn, timestamped)
        print(f"[import] imported {count} commands from {path}")
        return count
    finally:
        conn.close()


def import_powershell(path: Path | None = None) -> int:
    """Import PowerShell history. Returns number of commands imported."""
    path = path or PS_HISTORY_PATH
    if not path.exists():
        print(f"[import] PowerShell history not found: {path}")
        return 0

    entries = _parse_powershell_history(path)
    if not entries:
        print(f"[import] no commands found in {path}")
        return 0

    mtime = path.stat().st_mtime
    timestamped = _spread_timestamps(entries, mtime)

    conn = init_db()
    try:
        count = _bulk_insert(conn, timestamped)
        print(f"[import] imported {count} commands from {path}")
        return count
    finally:
        conn.close()


def run_import() -> None:
    """CLI entry point for suggest-import."""
    args = sys.argv[1:]

    if not args:
        # Auto-detect: import all available history files
        total = 0
        if BASH_HISTORY_PATH.exists():
            total += import_bash()
        if PS_HISTORY_PATH.exists():
            total += import_powershell()
        if total == 0:
            print("[import] no history files found to import")
            print(f"  looked for: {BASH_HISTORY_PATH}")
            print(f"             {PS_HISTORY_PATH}")
        else:
            print(f"[import] total: {total} commands imported")
        return

    i = 0
    while i < len(args):
        if args[i] == "--bash" and i + 1 < len(args):
            import_bash(Path(os.path.expanduser(args[i + 1])))
            i += 2
        elif args[i] == "--powershell" and i + 1 < len(args):
            import_powershell(Path(os.path.expanduser(args[i + 1])))
            i += 2
        elif args[i] in ("--help", "-h"):
            print("Usage: suggest-import [OPTIONS]")
            print()
            print("  No arguments:  auto-detect and import all found history files")
            print("  --bash PATH    import from a bash history file")
            print("  --powershell PATH  import from a PowerShell history file")
            print("  --help         show this message")
            return
        else:
            print(f"[import] unknown argument: {args[i]}")
            return
        i  # already advanced above


if __name__ == "__main__":
    run_import()
