"""Tests for the one-shot telemetry sender (record.py)."""

import sqlite3

from autosuggest import daemon, record


def test_send_record_falls_back_to_db(monkeypatch, tmp_path):
    # Socket delivery fails -> must write straight to the database.
    monkeypatch.setattr(record, "_send_socket", lambda _payload: False)

    dbfile = tmp_path / "h.db"

    def make_conn():
        conn = sqlite3.connect(str(dbfile))
        conn.executescript(daemon._SCHEMA)
        return conn

    monkeypatch.setattr(daemon, "init_db", make_conn)

    record.send_record("git status", "/proj", 0)

    conn = sqlite3.connect(str(dbfile))
    rows = conn.execute(
        "SELECT command, cwd, exit_status FROM command_history"
    ).fetchall()
    conn.close()
    assert rows == [("git status", "/proj", 0)]


def test_send_record_prefers_socket(monkeypatch):
    # When the socket delivers, the DB fallback must NOT run.
    monkeypatch.setattr(record, "_send_socket", lambda _payload: True)
    fallback_called = False

    def fake_db(*_args):
        nonlocal fallback_called
        fallback_called = True

    monkeypatch.setattr(record, "_send_db", fake_db)
    record.send_record("ls -l", "/proj", 0)
    assert fallback_called is False


def test_send_record_redacts_before_sending(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        record, "_send_socket", lambda payload: captured.update({"p": payload}) or True
    )
    record.send_record("login --password=hunter2", "/proj", 0)
    assert b"hunter2" not in captured["p"]
    assert b"***" in captured["p"]


def test_send_record_skips_pure_secret(monkeypatch):
    # A denylisted command redacts to "" and must never be delivered.
    sent = False

    def fake_send(_payload):
        nonlocal sent
        sent = True
        return True

    monkeypatch.setattr(record, "_send_socket", fake_send)
    record.send_record("p4 -u me -P secrettoken sync", "/proj", 0)
    assert sent is False
