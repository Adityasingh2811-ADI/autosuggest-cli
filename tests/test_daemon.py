"""Tests for the telemetry daemon's client handler and DB insertion."""

import asyncio
import json
import sqlite3

import pytest

from autosuggest import daemon


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
