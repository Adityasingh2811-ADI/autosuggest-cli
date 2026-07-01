"""Tests for the persistent shell environment used by the REPL."""

import shutil

import pytest

from autosuggest.shell_session import CommandRunner, _parse_env0

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="persistent shell session requires bash",
)


def test_parse_env0_handles_newlines_in_values():
    data = b"A=1\x00B=line1\nline2\x00MALFORMED\x00C=3\x00"
    env = _parse_env0(data)
    assert env["A"] == "1"
    assert env["B"] == "line1\nline2"
    assert env["C"] == "3"
    assert "MALFORMED" not in env


def test_persistent_env_carries_across_commands(tmp_path):
    # No rc file -> a clean persistent bash session (still module-agnostic).
    runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing")
    assert runner.persistent

    assert runner.run("export FOO=bar") == 0
    # The exported variable must survive into the next command.
    assert runner.run('test "$FOO" = bar') == 0


def test_cd_persists_across_commands(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing")

    assert runner.run("cd sub") == 0
    assert runner.cwd == str(sub)
    # A subsequent relative command runs from the new directory.
    assert runner.run("test -d .") == 0
    assert runner.cwd == str(sub)


def test_exit_status_is_propagated(tmp_path):
    runner = CommandRunner(start_cwd=str(tmp_path), rcfile=tmp_path / "missing")
    assert runner.run("true") == 0
    assert runner.run("exit 7") == 7


def test_rcfile_environment_is_sourced_once(tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("export ADI_TOOL=loaded\n")
    runner = CommandRunner(start_cwd=str(tmp_path), rcfile=rc)

    assert runner.persistent
    # The rc-provided variable is available without re-sourcing per command.
    assert runner.run('test "$ADI_TOOL" = loaded') == 0
