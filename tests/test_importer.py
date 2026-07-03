"""Tests for history import parsing (tcsh/csh)."""

from autosuggest.importer import _parse_tcsh_history


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
