"""Tests for argument-aware completion parsing and dispatch."""

import subprocess

import pytest

from autosuggest import arg_completers
from autosuggest.arg_completers import (
    ArgCompletion,
    _make_targets,
    _run,
    get_arg_completions,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    arg_completers._cache.clear()
    yield
    arg_completers._cache.clear()


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_run_strips_and_drops_blank_lines(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeProc("  main  \n\n feature/x \n")
    )
    assert _run(["git", "branch"], "/proj") == ["main", "feature/x"]


def test_run_returns_empty_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeProc("data", returncode=1)
    )
    assert _run(["git", "branch"], "/proj") == []


def test_run_handles_missing_executable(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _run(["nope"], "/proj") == []


def test_make_targets_parsing(tmp_path):
    (tmp_path / "Makefile").write_text(
        "build: deps\n\techo hi\n"
        "test:\n\tpytest\n"
        ".PHONY: build test\n"      # dot-target excluded
        "VAR = 1\n"                  # not a target
    )
    targets = _make_targets(str(tmp_path))
    assert "build" in targets
    assert "test" in targets
    assert ".PHONY" not in targets


def test_make_targets_missing_makefile(tmp_path):
    assert _make_targets(str(tmp_path)) == []


def test_get_arg_completions_filters_by_partial(monkeypatch):
    monkeypatch.setattr(
        arg_completers, "_run", lambda *a, **k: ["main", "feature/x", "fix"]
    )
    out = get_arg_completions("git checkout f", "/proj")
    texts = [c.text for c in out]
    assert texts == ["git checkout feature/x", "git checkout fix"]
    assert all(isinstance(c, ArgCompletion) and c.source == "git" for c in out)


def test_get_arg_completions_no_partial_returns_all(monkeypatch):
    monkeypatch.setattr(arg_completers, "_run", lambda *a, **k: ["main", "dev"])
    out = get_arg_completions("git switch ", "/proj")
    assert [c.text for c in out] == ["git switch main", "git switch dev"]


def test_get_arg_completions_unknown_prefix():
    assert get_arg_completions("ls -la", "/proj") == []


def test_cache_reuses_result_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _counting_run(*a, **k):
        calls["n"] += 1
        return ["main"]

    monkeypatch.setattr(arg_completers, "_run", _counting_run)
    get_arg_completions("git checkout ", "/proj")
    get_arg_completions("git checkout ", "/proj")
    assert calls["n"] == 1  # second lookup served from cache
