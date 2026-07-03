"""Tests for history import parsing (tcsh/csh)."""

import sqlite3

from autosuggest.importer import _bulk_insert, _parse_tcsh_history


def test_parse_plain_tcsh_history(tmp_path):
    hist = tmp_path / ".history"
    hist.write_text("ls -l\ncd /proj\nmake\nx\n")  # 'x' is too short, dropped
    entries = _parse_tcsh_history(hist)
    cmds = [c for c, _ in entries]
    assert cmds == ["ls -l", "cd /proj", "make"]
    assert all(ts is None for _, ts in entries)


def test_parse_timestamped_tcsh_history(tmp_path):
    hist = tmp_path / ".history"
    hist.write_text("#+1700000000\nls -l\n#+1700000005\ncd /proj\n")
    entries = _parse_tcsh_history(hist)
    assert entries == [("ls -l", 1700000000.0), ("cd /proj", 1700000005.0)]


def test_parse_tcsh_backslash_continuation(tmp_path):
    hist = tmp_path / ".history"
    hist.write_text("echo one \\\ntwo\nls\n")
    entries = _parse_tcsh_history(hist)
    cmds = [c for c, _ in entries]
    assert cmds[0] == "echo one \ntwo"
    assert cmds[1] == "ls"


def test_parse_missing_tcsh_history(tmp_path):
    assert _parse_tcsh_history(tmp_path / "nope") == []


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE command_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            cwd TEXT NOT NULL,
            exit_status INTEGER NOT NULL DEFAULT 0,
            timestamp REAL NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def test_bulk_insert_dedups_repeated_import(tmp_path):
    conn = _make_db(tmp_path / "h.db")
    entries = [("git status", 1700000000.0), ("make", 1700000001.0)]

    first = _bulk_insert(conn, entries)
    second = _bulk_insert(conn, entries)  # re-import same file

    assert first == 2
    assert second == 0  # duplicates skipped, scores not inflated
    total = conn.execute("SELECT COUNT(*) FROM command_history").fetchone()[0]
    assert total == 2
    conn.close()

