"""
Non-interactive self-test — validates the core pieces of autosuggest-cli work
on the current host and prints a pass/fail report.

Run:  suggest-selftest
Exit code is 0 when every check passes, 1 otherwise, so it is safe to use in
scripts and CI. It never modifies the real history database (DB-touching checks
use throwaway temp databases; host state is only read for the INFO lines).
"""

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_GREEN = "\033[32m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _check_version() -> None:
    from autosuggest import __version__

    assert isinstance(__version__, str) and __version__, "no version string"


def _check_redaction() -> None:
    from autosuggest.redact import redact

    assert redact("login --password=hunter2") == "login --password=***"
    assert redact("git status") == "git status"
    assert redact("p4 -u me -P tok sync") == ""
    assert redact("sshpass -p secret ssh host") == "sshpass -p *** ssh host"


def _check_database() -> None:
    from autosuggest.engine import _SCHEMA
    from autosuggest.paths import apply_journal_mode

    p = Path(tempfile.mkdtemp()) / "t.db"
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA busy_timeout=2000;")
    apply_journal_mode(conn, p)
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
        "VALUES ('x', '/p', 0, ?)",
        (time.time(),),
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    conn.close()
    assert n == 1, "insert/read roundtrip failed"


def _check_engine() -> None:
    from autosuggest.engine import PredictionEngine, _SCHEMA
    from autosuggest.next_steps import NextStepResolver

    p = Path(tempfile.mkdtemp()) / "e.db"
    c = sqlite3.connect(str(p))
    c.executescript(_SCHEMA)
    now = time.time()
    c.executemany(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
        "VALUES (?, ?, 0, ?)",
        [
            ("git status", "/p", now - 4),
            ("git add .", "/p", now - 3),
            ("git commit -m x", "/p", now - 2),
            ("git status", "/p", now - 1),
        ],
    )
    c.commit()
    c.close()
    engine = PredictionEngine(p)
    try:
        suggestions = engine.get_suggestions("git s", "/p")
        assert any(s.command == "git status" for s in suggestions), "no frecency suggestion"
        steps = NextStepResolver(engine).suggest("git status", "/p", limit=3)
        assert steps, "no next-step suggestions"
    finally:
        engine.close()


def _check_workflows() -> None:
    import yaml

    from autosuggest.paths import workflows_path

    with open(workflows_path(), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    names = {w["name"] for w in (data or {}).get("workflows", [])}
    for req in ("vivado", "perforce", "simulation", "modules"):
        assert req in names, f"missing EDA workflow: {req}"


def _check_record_fallback() -> None:
    from autosuggest import daemon, record
    from autosuggest.engine import _SCHEMA

    p = Path(tempfile.mkdtemp()) / "r.db"

    def fake_init():
        conn = sqlite3.connect(str(p))
        conn.executescript(_SCHEMA)
        return conn

    orig_init = daemon.init_db
    orig_sock = record._send_socket
    try:
        daemon.init_db = fake_init
        record._send_socket = lambda _payload: False  # force the DB fallback
        record.send_record("selftest cmd", "/p", 0)
    finally:
        record._send_socket = orig_sock
        daemon.init_db = orig_init

    conn = sqlite3.connect(str(p))
    n = conn.execute(
        "SELECT COUNT(*) FROM command_history WHERE command='selftest cmd'"
    ).fetchone()[0]
    conn.close()
    assert n == 1, "record.py fallback did not write to the DB"


def _check_arg_completers() -> None:
    from autosuggest.arg_completers import get_arg_completions

    assert get_arg_completions("ls -la", "/p") == []


_CHECKS = [
    ("version metadata", _check_version),
    ("redaction masks secrets", _check_redaction),
    ("database open + schema", _check_database),
    ("engine suggestions + next-steps", _check_engine),
    ("EDA workflow rules loaded", _check_workflows),
    ("telemetry record -> DB fallback", _check_record_fallback),
    ("argument completers", _check_arg_completers),
]


def _daemon_info() -> str:
    try:
        from autosuggest.daemon import is_daemon_running

        running, pid = is_daemon_running()
        if running:
            return f"running (PID {pid})"
        return "not running (telemetry falls back to a direct DB write)"
    except Exception as e:  # pragma: no cover - defensive
        return f"status unknown: {e}"


def _history_info() -> str:
    try:
        from autosuggest.paths import db_path

        dbp = db_path()
        if not dbp.exists():
            return "no database yet (run some commands or suggest-import)"
        conn = sqlite3.connect(str(dbp))
        conn.execute("PRAGMA query_only=ON;")
        total = conn.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
        imported = conn.execute(
            "SELECT COUNT(*) FROM command_history WHERE cwd='~'"
        ).fetchone()[0]
        conn.close()
        return f"{total} total ({imported} imported, {total - imported} live)"
    except Exception as e:
        return f"unavailable: {e}"


def run() -> None:
    """CLI entry point for suggest-selftest."""
    from autosuggest import __version__

    results: list[tuple[str, str, str]] = []
    for name, fn in _CHECKS:
        try:
            fn()
            results.append(("PASS", name, ""))
        except AssertionError as e:
            results.append(("FAIL", name, str(e)))
        except Exception as e:
            results.append(("FAIL", name, f"{type(e).__name__}: {e}"))

    infos = [
        ("daemon", _daemon_info()),
        ("history rows", _history_info()),
    ]

    print(f"\n  {_BOLD}autosuggest-cli self-test{_RESET}  (v{__version__})\n")
    passed = sum(1 for level, _, _ in results if level == "PASS")
    failed = sum(1 for level, _, _ in results if level == "FAIL")
    for level, name, detail in results:
        color = _GREEN if level == "PASS" else _RED
        line = f"  [{color}{level}{_RESET}] {name}"
        if detail:
            line += f"  {_RED}\u2014 {detail}{_RESET}"
        print(line)
    for name, detail in infos:
        print(f"  [{_CYAN}INFO{_RESET}] {name}: {detail}")

    summary_color = _RED if failed else _GREEN
    print(f"\n  {summary_color}{passed} passed, {failed} failed{_RESET}\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()
