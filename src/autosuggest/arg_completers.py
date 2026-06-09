"""
Argument-aware completion registry — provides live, context-specific
completions by shelling out to tools (git, docker, make, pip).
"""

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ArgCompletion:
    text: str
    source: str  # e.g. "git", "docker", "make", "pip"


@dataclass
class _CacheEntry:
    results: list[str]
    timestamp: float
    key: str = ""


_cache: dict[str, _CacheEntry] = {}
CACHE_TTL = 5.0  # seconds
SUBPROCESS_TIMEOUT = 0.5  # seconds


def _run(cmd: list[str], cwd: str) -> list[str]:
    """Run a subprocess with timeout, return stdout lines."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=cwd,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _cached(key: str, cwd: str, fn) -> list[str]:
    """Cache resolver results for CACHE_TTL seconds."""
    cache_key = f"{key}:{cwd}"
    now = time.time()
    entry = _cache.get(cache_key)
    if entry and (now - entry.timestamp) < CACHE_TTL:
        return entry.results
    results = fn()
    _cache[cache_key] = _CacheEntry(results=results, timestamp=now, key=cache_key)
    return results


# --- Git completers ---

def _git_branches(cwd: str) -> list[str]:
    return _cached("git-branches", cwd, lambda: _run(
        ["git", "branch", "--format=%(refname:short)"], cwd
    ))


def _git_remotes(cwd: str) -> list[str]:
    return _cached("git-remotes", cwd, lambda: _run(
        ["git", "remote"], cwd
    ))


def _git_tags(cwd: str) -> list[str]:
    return _cached("git-tags", cwd, lambda: _run(
        ["git", "tag", "--list"], cwd
    ))


# --- Docker completers ---

def _docker_images(cwd: str) -> list[str]:
    return _cached("docker-images", cwd, lambda: _run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], cwd
    ))


def _docker_containers(cwd: str) -> list[str]:
    return _cached("docker-containers", cwd, lambda: _run(
        ["docker", "ps", "--format", "{{.Names}}"], cwd
    ))


# --- Make completers ---

def _make_targets(cwd: str) -> list[str]:
    def _parse():
        makefile = Path(cwd) / "Makefile"
        if not makefile.exists():
            makefile = Path(cwd) / "makefile"
        if not makefile.exists():
            return []
        try:
            content = makefile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        targets = []
        for line in content.splitlines():
            match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_.-]*):', line)
            if match:
                target = match.group(1)
                if not target.startswith("."):
                    targets.append(target)
        return targets

    return _cached("make-targets", cwd, _parse)


# --- Pip completers ---

def _pip_packages(cwd: str) -> list[str]:
    def _resolve():
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=freeze"],
                capture_output=True, text=True, timeout=2.0, cwd=cwd,
            )
            if result.returncode != 0:
                return []
            return [
                line.split("==")[0]
                for line in result.stdout.splitlines()
                if "==" in line
            ]
        except (subprocess.TimeoutExpired, OSError):
            return []

    return _cached("pip-packages", cwd, _resolve)


# --- Registry ---

_COMPLETERS: list[tuple[str, str, callable]] = [
    # (prefix, source_tag, resolver_fn)
    # Git branch completers
    ("git checkout ", "git", _git_branches),
    ("git switch ", "git", _git_branches),
    ("git merge ", "git", _git_branches),
    ("git rebase ", "git", _git_branches),
    ("git branch -d ", "git", _git_branches),
    ("git branch -D ", "git", _git_branches),
    # Git remote completers
    ("git push ", "git", _git_remotes),
    ("git pull ", "git", _git_remotes),
    ("git fetch ", "git", _git_remotes),
    # Git tag completers
    ("git tag -d ", "git", _git_tags),
    # Docker image completers
    ("docker run ", "docker", _docker_images),
    ("docker rmi ", "docker", _docker_images),
    # Docker container completers
    ("docker exec ", "docker", _docker_containers),
    ("docker stop ", "docker", _docker_containers),
    ("docker logs ", "docker", _docker_containers),
    ("docker rm ", "docker", _docker_containers),
    ("docker restart ", "docker", _docker_containers),
    # Make completers
    ("make ", "make", _make_targets),
    # Pip completers (uses python -m pip internally)
    ("pip install ", "pip", _pip_packages),
    ("pip uninstall ", "pip", _pip_packages),
    ("pip show ", "pip", _pip_packages),
]


def get_arg_completions(text: str, cwd: str) -> list[ArgCompletion]:
    """Get argument-aware completions for the given input text.

    Returns completions if `text` matches a known command pattern and
    has a partial argument that can be resolved. Returns empty list
    if no pattern matches or resolver returns nothing.
    """
    for prefix, source, resolver in _COMPLETERS:
        if text.startswith(prefix):
            partial_arg = text[len(prefix):]
            candidates = resolver(cwd)
            completions = []
            for candidate in candidates:
                if not partial_arg or candidate.lower().startswith(partial_arg.lower()):
                    completions.append(ArgCompletion(
                        text=prefix + candidate,
                        source=source,
                    ))
            return completions
    return []
