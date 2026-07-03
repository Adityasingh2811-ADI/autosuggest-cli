"""Shared fixtures for autosuggest tests."""

import sqlite3
import time

import pytest

# Use the real production schema (including indexes) so tests exercise the same
# index-dependent query paths the app uses in production.
from autosuggest.engine import _SCHEMA as SCHEMA


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary SQLite database with the command_history schema."""
    path = tmp_path / "test_autosuggest.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.close()
    return path


@pytest.fixture
def populated_db(db_path):
    """Database pre-loaded with sample command history."""
    conn = sqlite3.connect(str(db_path))
    now = time.time()

    rows = [
        # Recent git commands in /home/user/project
        ("git status", "/home/user/project", 0, now - 60),
        ("git add .", "/home/user/project", 0, now - 50),
        ("git commit -m 'fix'", "/home/user/project", 0, now - 40),
        ("git push", "/home/user/project", 0, now - 30),
        ("git status", "/home/user/project", 0, now - 20),
        # Older git commands
        ("git status", "/home/user/project", 0, now - 7200),
        ("git log", "/home/user/project", 0, now - 7100),
        # Commands in different directory
        ("make build", "/home/user/firmware", 0, now - 100),
        ("make test", "/home/user/firmware", 0, now - 90),
        ("make deploy", "/home/user/firmware", 0, now - 80),
        # Failed commands (should be excluded from suggestions)
        ("git push", "/home/user/project", 1, now - 10),
        ("rm -rf /", "/home/user/project", 1, now - 5),
        # Python dev commands
        ("pip install pytest", "/home/user/project", 0, now - 3600),
        ("pytest", "/home/user/project", 0, now - 3500),
        ("pytest -v", "/home/user/project", 0, now - 300),
    ]

    conn.executemany(
        "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path
