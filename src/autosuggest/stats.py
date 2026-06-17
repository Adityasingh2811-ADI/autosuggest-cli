"""
Usage statistics — queries the command history database and displays
metrics about usage patterns, top commands, and workflow efficiency.
"""

import sqlite3
import sys
import time
from pathlib import Path

from autosuggest.paths import db_path

DB_PATH = db_path()


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("No history database found. Run some commands first!")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
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
    print(f"\n  \033[1;36m{title}\033[0m")
    print(f"  {'─' * 50}")


def run_stats() -> None:
    conn = _connect()
    now = time.time()

    # Overall counts
    total = conn.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    unique = conn.execute("SELECT COUNT(DISTINCT command) FROM command_history").fetchone()[0]
    directories = conn.execute("SELECT COUNT(DISTINCT cwd) FROM command_history").fetchone()[0]
    success = conn.execute(
        "SELECT COUNT(*) FROM command_history WHERE exit_status = 0"
    ).fetchone()[0]

    if total == 0:
        print("\n  No commands recorded yet. Start using the shell to build history!\n")
        conn.close()
        return

    # Time range
    first_ts = conn.execute("SELECT MIN(timestamp) FROM command_history").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(timestamp) FROM command_history").fetchone()[0]
    tracking_duration = last_ts - first_ts if first_ts and last_ts else 0

    # Top commands
    top_commands = conn.execute("""
        SELECT command, COUNT(*) as cnt
        FROM command_history
        WHERE exit_status = 0
        GROUP BY command
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

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
    print("\n  \033[1;33m╔══════════════════════════════════════════════════════╗\033[0m")
    print("  \033[1;33m║\033[0m       \033[1mautosuggest-cli\033[0m — Usage Statistics           \033[1;33m║\033[0m")
    print("  \033[1;33m╚══════════════════════════════════════════════════════╝\033[0m")

    _print_section("Overview")
    success_rate = (success / total * 100) if total > 0 else 0
    print(f"  Total commands recorded:    \033[1m{total:,}\033[0m")
    print(f"  Unique commands:            \033[1m{unique:,}\033[0m")
    print(f"  Directories tracked:        \033[1m{directories:,}\033[0m")
    print(f"  Success rate:               \033[1;32m{success_rate:.1f}%\033[0m")
    print(f"  Tracking for:               \033[1m{_format_duration(tracking_duration)}\033[0m")

    _print_section("Activity")
    print(f"  Last 24 hours:              \033[1m{today_count:,}\033[0m commands")
    print(f"  Last 7 days:                \033[1m{week_count:,}\033[0m commands")
    if tracking_duration > 86400:
        daily_avg = total / (tracking_duration / 86400)
        print(f"  Daily average:              \033[1m{daily_avg:.0f}\033[0m commands/day")

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
            print(f"  {i:>2}. {from_cmd} \033[36m→\033[0m {to_cmd}  ({cnt}x)")

    print()


def run() -> None:
    run_stats()


if __name__ == "__main__":
    run_stats()
