"""Tests for shell-hook generation and the tcsh telemetry path."""

import json

import pytest

from autosuggest import hooks, record


class TestTcshHook:
    def test_tcsh_hook_has_precmd_and_helper(self):
        assert "alias precmd" in hooks.TCSH_HOOK
        assert "autosuggest.tcsh_precmd" in hooks.TCSH_HOOK

    def test_tcsh_hook_uses_backticks_not_dollar_paren(self):
        # csh/tcsh cannot parse $(...); the source line must use backticks.
        assert "`suggest-hook tcsh`" in hooks.TCSH_SOURCE_LINE
        assert "$(" not in hooks.TCSH_SOURCE_LINE

    @pytest.mark.parametrize("shell", ["tcsh", "csh"])
    def test_print_hook_emits_tcsh(self, shell, capsys):
        hooks._print_hook(shell)
        out = capsys.readouterr().out
        assert "alias precmd" in out

    def test_dispatch_routes_tcsh(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["suggest-hook", "tcsh"])
        hooks.run_hook()
        out = capsys.readouterr().out
        assert "autosuggest.tcsh_precmd" in out

    def test_help_warns_about_dollar_paren_in_csh(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["suggest-hook", "--help"])
        hooks.run_hook()
        out = capsys.readouterr().out
        assert "tcsh" in out
        assert "$(...)" in out


class TestRecord:
    def test_empty_command_is_noop(self, monkeypatch):
        called = False

        def fake_send(_payload):
            nonlocal called
            called = True
            return True

        monkeypatch.setattr(record, "_send_socket", fake_send)
        record.send_record("   ", "/home/user", 0)
        assert called is False

    def test_payload_shape(self, monkeypatch):
        captured = {}

        def fake_send(payload):
            captured.update(json.loads(payload.decode("utf-8")))
            return True

        monkeypatch.setattr(record, "_send_socket", fake_send)
        record.send_record("git status", "/home/user/project", 1)
        assert captured == {
            "command": "git status",
            "cwd": "/home/user/project",
            "exit_status": 1,
        }
