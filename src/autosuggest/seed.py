"""
Cheatsheet seeder — populates the autosuggest database with commands from
~/.cheatsheets/sheets/ markdown files, or from the bundled seed_commands.json
when cheatsheets aren't installed locally.
"""

import json
import re
import sqlite3
import time
from pathlib import Path

from autosuggest.daemon import init_db
from autosuggest.importer import BATCH_SIZE

SHEETS_DIR = Path.home() / ".cheatsheets" / "sheets"
BUNDLED_SEEDS = Path(__file__).parent / "seed_commands.json"
SEED_CWD = "/home/user"
CODE_FENCE_RE = re.compile(r"```(?:bash|sh)\s*\n(.*?)```", re.DOTALL)
COMMENT_RE = re.compile(r"\s+#\s.*$")


def _load_bundled_commands() -> list[str]:
    """Load commands from the bundled JSON seed file."""
    if not BUNDLED_SEEDS.exists():
        return []
    data = json.loads(BUNDLED_SEEDS.read_text(encoding="utf-8"))
    commands = []
    for category, cmds in data.items():
        print(f"  [{category}] {len(cmds)} commands")
        commands.extend(cmds)
    return commands


def _parse_commands_from_sheet(path: Path) -> list[str]:
    """Extract commands from fenced code blocks in a markdown sheet."""
    text = path.read_text(encoding="utf-8", errors="replace")
    commands = []

    for match in CODE_FENCE_RE.finditer(text):
        block = match.group(1)
        for line in block.splitlines():
            cmd = COMMENT_RE.sub("", line).strip()
            if not cmd:
                continue
            if cmd.startswith("#"):
                continue
            if re.fullmatch(r"<[^>]+>", cmd):
                continue
            commands.append(cmd)

    return commands


def _get_existing_commands(conn: sqlite3.Connection) -> set[str]:
    """Return set of commands already in the DB with the seed cwd."""
    rows = conn.execute(
        "SELECT DISTINCT command FROM command_history WHERE cwd = ?",
        (SEED_CWD,),
    ).fetchall()
    return {row[0] for row in rows}


def _spread_timestamps(count: int) -> list[float]:
    """Generate timestamps spread over the last 30 days."""
    now = time.time()
    span = 30 * 24 * 3600
    start = now - span
    if count <= 1:
        return [now]
    return [start + (i / (count - 1)) * span for i in range(count)]


def seed_from_cheatsheets(sheets_dir: Path | None = None) -> int:
    """Parse all cheatsheet markdown files and seed the DB. Returns count inserted."""
    sheets_dir = sheets_dir or SHEETS_DIR

    all_commands: list[str] = []

    if sheets_dir.exists():
        md_files = sorted(sheets_dir.rglob("*.md"))
        if md_files:
            for md_file in md_files:
                category = md_file.parent.name
                commands = _parse_commands_from_sheet(md_file)
                if commands:
                    print(f"  [{category}/{md_file.stem}] {len(commands)} commands")
                    all_commands.extend(commands)

    if not all_commands:
        print("[seed] cheatsheets not found locally, using bundled seed data")
        all_commands = _load_bundled_commands()

    if not all_commands:
        print("[seed] no commands extracted from sheets")
        return 0

    conn = init_db()
    try:
        existing = _get_existing_commands(conn)
        new_commands = [cmd for cmd in all_commands if cmd not in existing]

        if not new_commands:
            print(f"[seed] all {len(all_commands)} commands already in database")
            return 0

        timestamps = _spread_timestamps(len(new_commands))
        inserted = 0
        for i in range(0, len(new_commands), BATCH_SIZE):
            batch = [
                (new_commands[j], SEED_CWD, 0, timestamps[j])
                for j in range(i, min(i + BATCH_SIZE, len(new_commands)))
            ]
            conn.executemany(
                "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
                "VALUES (?, ?, ?, ?)",
                batch,
            )
            conn.commit()
            inserted += len(batch)

        print(f"[seed] inserted {inserted} new commands ({len(existing)} already existed)")
        return inserted
    finally:
        conn.close()


def run_seed() -> None:
    """CLI entry point for suggest-seed."""
    print("[seed] scanning cheatsheets...")
    total = seed_from_cheatsheets()
    if total:
        print(f"[seed] done — {total} commands seeded")
    else:
        print("[seed] nothing new to seed")


if __name__ == "__main__":
    run_seed()
