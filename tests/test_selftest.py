"""Tests for the suggest-selftest command."""

import pytest

from autosuggest import selftest


def test_all_checks_pass():
    # Each individual check must succeed on a healthy install.
    for name, fn in selftest._CHECKS:
        fn()  # raises AssertionError/Exception on failure


def test_run_exits_zero_when_healthy(capsys):
    with pytest.raises(SystemExit) as exc:
        selftest.run()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "self-test" in out
    assert "passed" in out
