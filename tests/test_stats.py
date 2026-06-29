"""Tests for suggest-stats query helpers."""

import json
import sqlite3

from autosuggest import stats


def _conn(db_path):
    return sqlite3.connect(str(db_path))


def test_after_lists_followups(populated_db, capsys):
    stats._print_after(_conn(populated_db), "git status", 5)
    out = capsys.readouterr().out
    assert "git add ." in out


def test_after_empty(populated_db, capsys):
    stats._print_after(_conn(populated_db), "no-such-cmd", 5)
    out = capsys.readouterr().out
    assert "No follow-up" in out


def test_json_top(populated_db, capsys):
    stats._emit_json(_conn(populated_db), 5, "", ())
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 15
    assert data["top"][0]["command"] == "git status"


def test_json_dir_filter(populated_db, capsys):
    stats._emit_json(_conn(populated_db), 5, "WHERE cwd = ?", ("/home/user/firmware",))
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 3
    assert all("make" in t["command"] for t in data["top"])
