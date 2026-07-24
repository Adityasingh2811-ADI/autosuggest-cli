"""
Usage statistics — queries the command history database and displays
metrics about usage patterns, top commands, and workflow efficiency.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from autosuggest.paths import db_path, apply_journal_mode


def _use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


_COLOR = _use_color()
_BOLD = "\033[1m" if _COLOR else ""
_CYAN = "\033[1;36m" if _COLOR else ""
_GREEN = "\033[1;32m" if _COLOR else ""
_YELLOW = "\033[1;33m" if _COLOR else ""
_LCYAN = "\033[36m" if _COLOR else ""
_RESET = "\033[0m" if _COLOR else ""

DB_PATH = db_path()


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("No history database found. Run some commands first!")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000;")
    apply_journal_mode(conn, DB_PATH)
    conn.execute("PRAGMA query_only=ON;")
    return conn


def _format_duration(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int(seconds // 60)
    if hours > 0:
        return f"{hours}h {minutes % 60}m"
    return f"{minutes}m"


def _print_section(title: str) -> None:
    print(f"\n  {_CYAN}{title}{_RESET}")
    print(f"  {'─' * 50}")


def _print_after(conn: sqlite3.Connection, cmd: str, n: int) -> None:
    rows = conn.execute("""
        SELECT next.command, COUNT(*) AS cnt
        FROM command_history curr
        JOIN command_history next
          ON next.cwd = curr.cwd
          AND next.id = (SELECT MIN(x.id) FROM command_history x
                         WHERE x.cwd = curr.cwd AND x.id > curr.id)
        WHERE curr.command = ? AND curr.exit_status = 0 AND next.exit_status = 0
          AND next.command != curr.command
        GROUP BY next.command ORDER BY cnt DESC LIMIT ?
    """, (cmd, n)).fetchall()
    conn.close()
    if not rows:
        print(f"\n  No follow-up commands recorded after: {cmd}\n")
        return
    _print_section(f"After '{cmd}' you usually run")
    for i, (to_cmd, cnt) in enumerate(rows, 1):
        print(f"  {i:>2}. {to_cmd}  ({cnt}x)")
    print()


def _emit_json(conn: sqlite3.Connection, n: int, where: str, params: tuple) -> None:
    top = conn.execute(
        f"SELECT command, COUNT(*) c FROM command_history {where} "
        f"GROUP BY command ORDER BY c DESC LIMIT ?", (*params, n)).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) FROM command_history {where}", params).fetchone()[0]
    conn.close()
    print(json.dumps({"total": total, "top": [{"command": c, "count": n} for c, n in top]}))


def run_stats(n: int = 10, dir_filter: str | None = None) -> None:
    conn = _connect()
    now = time.time()

    where = "WHERE 1=1"
    params: tuple = ()
    if dir_filter:
        where += " AND cwd = ?"
        params = (dir_filter,)

    # Overall counts
    total = conn.execute(
        f"SELECT COUNT(*) FROM command_history {where}", params).fetchone()[0]
    unique = conn.execute(
        f"SELECT COUNT(DISTINCT command) FROM command_history {where}", params).fetchone()[0]
    directories = conn.execute("SELECT COUNT(DISTINCT cwd) FROM command_history").fetchone()[0]
    success = conn.execute(
        f"SELECT COUNT(*) FROM command_history {where} AND exit_status = 0", params
    ).fetchone()[0]

    if total == 0:
        print("\n  No commands recorded yet.")
        print("  Use your shell for a few minutes — the daemon records commands automatically.")
        print("  Run 'suggest-stats --check' to verify DB health.\n")
        conn.close()
        return

    # Time range
    first_ts = conn.execute("SELECT MIN(timestamp) FROM command_history").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(timestamp) FROM command_history").fetchone()[0]
    tracking_duration = last_ts - first_ts if first_ts and last_ts else 0

    # Top commands
    top_commands = conn.execute(f"""
        SELECT command, COUNT(*) as cnt
        FROM command_history
        {where} AND exit_status = 0
        GROUP BY command
        ORDER BY cnt DESC
        LIMIT ?
    """, params + (int(n),)).fetchall()

    # Top directories
    top_dirs = conn.execute("""
        SELECT cwd, COUNT(*) as cnt
        FROM command_history
        GROUP BY cwd
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()

    # Commands in last 24h
    day_ago = now - 86400
    today_count = conn.execute(
        "SELECT COUNT(*) FROM command_history WHERE timestamp > ?", (day_ago,)
    ).fetchone()[0]

    # Commands in last 7 days
    week_ago = now - 604800
    week_count = conn.execute(
        "SELECT COUNT(*) FROM command_history WHERE timestamp > ?", (week_ago,)
    ).fetchone()[0]

    # Most common sequences (next-step patterns)
    sequences = conn.execute("""
        SELECT
            curr.command AS from_cmd,
            next.command AS to_cmd,
            COUNT(*) as cnt
        FROM command_history curr
        JOIN command_history next
          ON next.cwd = curr.cwd
          AND next.id = (
              SELECT MIN(n.id) FROM command_history n
              WHERE n.cwd = curr.cwd AND n.id > curr.id
          )
        WHERE curr.exit_status = 0
          AND next.exit_status = 0
          AND curr.command != next.command
        GROUP BY curr.command, next.command
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    # Display
    print(f"\n  {_YELLOW}╔══════════════════════════════════════════════════════╗{_RESET}")
    print(f"  {_YELLOW}║{_RESET}       {_BOLD}autosuggest-cli{_RESET} — Usage Statistics           {_YELLOW}║{_RESET}")
    print(f"  {_YELLOW}╚══════════════════════════════════════════════════════╝{_RESET}")

    _print_section("Overview")
    success_rate = (success / total * 100) if total > 0 else 0
    print(f"  Total commands recorded:    {_BOLD}{total:,}{_RESET}")
    print(f"  Unique commands:            {_BOLD}{unique:,}{_RESET}")
    print(f"  Directories tracked:        {_BOLD}{directories:,}{_RESET}")
    print(f"  Success rate:               {_GREEN}{success_rate:.1f}%{_RESET}")
    print(f"  Tracking for:               {_BOLD}{_format_duration(tracking_duration)}{_RESET}")

    _print_section("Activity")
    print(f"  Last 24 hours:              {_BOLD}{today_count:,}{_RESET} commands")
    print(f"  Last 7 days:                {_BOLD}{week_count:,}{_RESET} commands")
    if tracking_duration > 86400:
        daily_avg = total / (tracking_duration / 86400)
        print(f"  Daily average:              {_BOLD}{daily_avg:.0f}{_RESET} commands/day")

    _print_section("Top Commands")
    for i, (cmd, cnt) in enumerate(top_commands, 1):
        bar_len = int(cnt / top_commands[0][1] * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {i:>2}. {cmd:<30} {bar} {cnt:>5}")

    _print_section("Top Directories")
    home = str(Path.home())
    for i, (d, cnt) in enumerate(top_dirs, 1):
        display = "~" + d[len(home):] if d.startswith(home) else d
        print(f"  {i:>2}. {display:<40} ({cnt:,})")

    if sequences:
        _print_section("Top Workflows (learned sequences)")
        for i, (from_cmd, to_cmd, cnt) in enumerate(sequences, 1):
            print(f"  {i:>2}. {from_cmd} {_LCYAN}→{_RESET} {to_cmd}  ({cnt}x)")

    print()


def run_check() -> None:
    """Validate DB existence, schema, and row count."""
    print(f"\n  {_BOLD}suggest-stats --check{_RESET}\n")
    if not DB_PATH.exists():
        print(f"  DB path:    {DB_PATH}")
        print(f"  Status:     {_YELLOW}NOT FOUND{_RESET}")
        print("  The history database has not been created yet.")
        print("  Ensure the daemon is running: suggest-daemon start\n")
        return
    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000;")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        has_history = "command_history" in tables
        row_count = 0
        if has_history:
            row_count = conn.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        print(f"  DB path:    {DB_PATH}")
        print(f"  Status:     {_YELLOW}ERROR — {e}{_RESET}\n")
        return

    print(f"  DB path:    {DB_PATH}")
    print(f"  DB size:    {DB_PATH.stat().st_size / 1024:.1f} KB")
    print(f"  Schema:     {'command_history table present' if has_history else _YELLOW + 'MISSING command_history table' + _RESET}")
    print(f"  Rows:       {row_count:,}")
    if row_count == 0:
        print(f"  Status:     {_YELLOW}EMPTY — use the shell to build history{_RESET}")
    else:
        print(f"  Status:     {_GREEN}HEALTHY{_RESET}")
    print()


def run() -> None:
    ap = argparse.ArgumentParser(prog="suggest-stats", description="autosuggest usage stats")
    ap.add_argument("-n", "--top", type=int, default=10, help="number of top commands to show")
    ap.add_argument("--dir", metavar="PATH", help="only count commands run in this directory")
    ap.add_argument("--after", metavar="CMD", help="show commands usually run after CMD")
    ap.add_argument("--json", action="store_true", help="machine-readable top commands")
    ap.add_argument("--check", action="store_true", help="validate DB health and report status")
    ap.add_argument("--metrics", action="store_true", help="show install/invocation counters")
    from autosuggest import __version__

    ap.add_argument(
        "--version", action="version", version=f"suggest-stats {__version__}"
    )
    args = ap.parse_args()

    if args.check:
        run_check()
        return
    if args.metrics:
        from autosuggest.metrics import get_metrics
        data = get_metrics()
        print(f"\n  {_BOLD}Metrics{_RESET}\n")
        print(f"  Installs:            {_BOLD}{data.get('installs', 0)}{_RESET}")
        print(f"  Suggestions served:  {_BOLD}{data.get('suggestions_served', 0)}{_RESET}")
        print(f"  Daemon starts:       {_BOLD}{data.get('daemon_starts', 0)}{_RESET}")
        print(f"  Sessions:            {_BOLD}{data.get('sessions', 0)}{_RESET}")
        if data.get("first_install"):
            from datetime import datetime
            ts = datetime.fromtimestamp(data["first_install"]).strftime("%Y-%m-%d %H:%M")
            print(f"  First install:       {ts}")
        if data.get("last_active"):
            from datetime import datetime
            ts = datetime.fromtimestamp(data["last_active"]).strftime("%Y-%m-%d %H:%M")
            print(f"  Last active:         {ts}")
        print()
        return
    if args.after:
        _print_after(_connect(), args.after, args.top)
        return
    if args.json:
        where, params = ("WHERE cwd = ?", (args.dir,)) if args.dir else ("", ())
        _emit_json(_connect(), args.top, where, params)
        return
    run_stats(n=args.top, dir_filter=args.dir)


if __name__ == "__main__":
    run()
