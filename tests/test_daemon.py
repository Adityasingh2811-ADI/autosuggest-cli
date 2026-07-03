"""Tests for the telemetry daemon's client handler and DB insertion."""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from autosuggest import daemon, paths


class _FakeReader:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self, _n: int) -> bytes:
        return self._data


class _FakeWriter:
    def __init__(self):
        self.buffer = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _mem_db():
    # check_same_thread=False mirrors the daemon, which inserts from an executor
    # thread via run_in_executor.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(daemon._SCHEMA)
    return conn


def _run(coro):
    return asyncio.run(coro)


def test_handle_client_inserts_valid_packet():
    db = _mem_db()
    payload = json.dumps(
        {"command": "git status", "cwd": "/proj", "exit_status": 0}
    ).encode()
    writer = _FakeWriter()

    _run(daemon.handle_client(_FakeReader(payload), writer, db))

    rows = db.execute("SELECT command, cwd FROM command_history").fetchall()
    assert rows == [("git status", "/proj")]
    assert b'"status":"ok"' in writer.buffer
    assert writer.closed


def test_handle_client_rejects_bad_token():
    db = _mem_db()
    payload = json.dumps(
        {"command": "rm -rf /", "cwd": "/proj", "exit_status": 0, "token": "wrong"}
    ).encode()
    writer = _FakeWriter()

    _run(
        daemon.handle_client(
            _FakeReader(payload), writer, db, require_token=True, token="right"
        )
    )

    count = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert count == 0  # injection rejected


def test_handle_client_accepts_matching_token():
    db = _mem_db()
    payload = json.dumps(
        {"command": "ls", "cwd": "/proj", "exit_status": 0, "token": "right"}
    ).encode()
    writer = _FakeWriter()

    _run(
        daemon.handle_client(
            _FakeReader(payload), writer, db, require_token=True, token="right"
        )
    )

    count = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert count == 1


def test_handle_client_ignores_malformed_json():
    db = _mem_db()
    writer = _FakeWriter()

    # Should not raise, just log and drop.
    _run(daemon.handle_client(_FakeReader(b"not json {"), writer, db))

    count = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert count == 0


def test_insert_row_drops_redacted_secret():
    db = _mem_db()
    # A pure-secret command redacts to "" and must not be stored.
    daemon._insert_row(db, {"command": "p4 -u me -P tok sync", "cwd": "/x"})
    count = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert count == 0


def test_prune_caps_row_count(monkeypatch):
    db = _mem_db()
    for i in range(10):
        db.execute(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
            "VALUES (?, ?, 0, ?)",
            (f"cmd{i}", "/x", float(i)),
        )
    db.commit()

    monkeypatch.setattr(daemon, "MAX_HISTORY_ROWS", 3)
    monkeypatch.setattr(daemon, "MAX_HISTORY_AGE_DAYS", 0)
    deleted = daemon._prune_db(db)

    remaining = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert remaining == 3
    assert deleted == 7
    # The most-recent (highest timestamp) rows survive.
    kept = db.execute(
        "SELECT command FROM command_history ORDER BY timestamp DESC"
    ).fetchall()
    assert kept[0][0] == "cmd9"


def test_apply_journal_mode_sets_requested_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "journal_mode_for", lambda _p: "TRUNCATE")
    dbfile = tmp_path / "h.db"
    conn = sqlite3.connect(str(dbfile))
    conn.execute("PRAGMA busy_timeout=1000;")
    paths.apply_journal_mode(conn, dbfile)
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "truncate"
    conn.close()


def test_apply_journal_mode_tolerates_locked_switch(monkeypatch):
    # Regression: switching a WAL DB to TRUNCATE while the daemon holds it open
    # raises "database is locked". apply_journal_mode must swallow that and keep
    # the current mode rather than crashing suggest-import.
    monkeypatch.setattr(paths, "journal_mode_for", lambda _p: "TRUNCATE")

    class _LockedConn:
        def execute(self, _sql):
            raise sqlite3.OperationalError("database is locked")

    # Must not raise.
    paths.apply_journal_mode(_LockedConn(), Path("/nfs/home/history.db"))


def test_dedupe_collapses_repeated_imports():
    # Simulate a pre-dedup DB: the same import ran 3x, so every
    # (command, cwd, timestamp) triple appears 3 times.
    db = _mem_db()
    rows = [("git status", "~", 100.0), ("git status", "~", 101.0), ("make", "~", 102.0)]
    for _ in range(3):
        db.executemany(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) "
            "VALUES (?, ?, 0, ?)",
            rows,
        )
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0] == 9

    removed = daemon.dedupe_history(db)

    assert removed == 6  # 3 unique triples kept, 6 duplicates removed
    remaining = db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert remaining == 3


def test_dedupe_preserves_within_import_frequency_and_live_rows():
    db = _mem_db()
    # Two distinct occurrences of the same command in one import (different
    # spread timestamps) — legitimate frequency, must be kept.
    db.executemany(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES (?, ?, 0, ?)",
        [("ls", "~", 1.0), ("ls", "~", 2.0),
         # a live-recorded row with a real cwd + unique timestamp
         ("ls", "/home/u/proj", 12345.678)],
    )
    db.commit()

    removed = daemon.dedupe_history(db)

    assert removed == 0
    assert db.execute("SELECT COUNT(*) FROM command_history").fetchone()[0] == 3


def test_insert_row_does_not_depend_on_timestamp_default():
    # Regression: on older SQLite (RHEL7/8) unixepoch() is missing, so relying
    # on the column's timestamp DEFAULT makes every live insert fail. We must
    # always pass an explicit timestamp. Simulate that by giving the column a
    # default that would error if evaluated.
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE command_history ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " command TEXT NOT NULL, cwd TEXT NOT NULL,"
        " exit_status INTEGER NOT NULL DEFAULT 0,"
        " timestamp REAL NOT NULL DEFAULT (this_function_does_not_exist()));"
    )
    # Must not raise, and must populate a real timestamp.
    daemon._insert_row(conn, {"command": "ls", "cwd": "/p", "exit_status": 0})
    row = conn.execute("SELECT command, timestamp FROM command_history").fetchone()
    assert row[0] == "ls"
    assert row[1] and row[1] > 0
    conn.close()


def test_dedupe_is_idempotent_on_clean_db():
    db = _mem_db()
    db.execute(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES ('git', '~', 0, 1.0)"
    )
    db.commit()
    assert daemon.dedupe_history(db) == 0
    assert daemon.dedupe_history(db) == 0


def test_run_daemon_start_detaches_by_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(daemon, "_cmd_start", lambda foreground=False: captured.update(fg=foreground))
    monkeypatch.setattr(daemon.sys, "argv", ["suggest-daemon", "start"])
    daemon.run_daemon()
    assert captured["fg"] is False


def test_run_daemon_start_foreground_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(daemon, "_cmd_start", lambda foreground=False: captured.update(fg=foreground))
    monkeypatch.setattr(daemon.sys, "argv", ["suggest-daemon", "start", "--foreground"])
    daemon.run_daemon()
    assert captured["fg"] is True


