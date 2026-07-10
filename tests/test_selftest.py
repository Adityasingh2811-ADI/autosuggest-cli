"""Tests for the suggest-selftest command."""

import pytest
from unittest.mock import patch

from autosuggest import selftest

_FUNCTIONAL_CHECKS = [
    (name, fn) for name, fn in selftest._CHECKS
    if fn not in (
        selftest._check_daemon_running,
        selftest._check_socket_connectable,
        selftest._check_auth_token,
        selftest._check_path_entries,
    )
]


def test_functional_checks_pass():
    for name, fn in _FUNCTIONAL_CHECKS:
        fn()


def test_run_reports_results(capsys):
    with patch.object(selftest, "_CHECKS", _FUNCTIONAL_CHECKS):
        with pytest.raises(SystemExit) as exc:
            selftest.run()
        assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "self-test" in out
    assert "passed" in out
