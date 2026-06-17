"""Unit tests for the frecency prediction engine."""

import sqlite3
import time

import pytest

from autosuggest.engine import (
    CONTEXT_BOOST,
    HALF_LIFE,
    PredictionEngine,
    _recency_score,
)


class TestRecencyScore:
    """Tests for the exponential decay scoring function."""

    def test_current_timestamp_scores_one(self):
        now = time.time()
        assert _recency_score(now, now) == pytest.approx(1.0)

    def test_one_half_life_ago_scores_half(self):
        now = time.time()
        assert _recency_score(now, now - HALF_LIFE) == pytest.approx(0.5, rel=1e-6)

    def test_two_half_lives_ago_scores_quarter(self):
        now = time.time()
        assert _recency_score(now, now - 2 * HALF_LIFE) == pytest.approx(0.25, rel=1e-6)

    def test_score_decreases_with_age(self):
        now = time.time()
        recent = _recency_score(now, now - 100)
        older = _recency_score(now, now - 1000)
        oldest = _recency_score(now, now - 10000)
        assert recent > older > oldest

    def test_score_never_negative(self):
        now = time.time()
        assert _recency_score(now, now - 1_000_000) > 0


class TestPredictionEngine:
    """Tests for the PredictionEngine query and scoring logic."""

    def test_empty_database_returns_no_suggestions(self, db_path):
        engine = PredictionEngine(db_path)
        results = engine.get_suggestions("git", "/home/user/project")
        assert results == []
        engine.close()

    def test_prefix_matching(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("git s", "/home/user/project")
        commands = [r.command for r in results]
        assert "git status" in commands
        assert "git push" not in commands
        engine.close()

    def test_no_match_returns_empty(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("zzz_nonexistent", "/home/user/project")
        assert results == []
        engine.close()

    def test_failed_commands_excluded(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("rm", "/home/user/project")
        commands = [r.command for r in results]
        assert "rm -rf /" not in commands
        engine.close()

    def test_context_boost_same_directory(self, populated_db):
        engine = PredictionEngine(populated_db)
        # Query from /home/user/project where "git status" lives
        results_local = engine.get_suggestions("git", "/home/user/project")
        # Query from a different directory
        results_other = engine.get_suggestions("git", "/tmp/other")

        local_scores = {r.command: r.score for r in results_local}
        other_scores = {r.command: r.score for r in results_other}

        # "git status" should score higher in its home directory
        assert local_scores.get("git status", 0) > other_scores.get("git status", 0)
        engine.close()

    def test_recent_commands_rank_higher(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("git", "/home/user/project")
        commands = [r.command for r in results]
        # "git status" was used most recently and frequently
        assert commands[0] == "git status"
        engine.close()

    def test_limit_respected(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("git", "/home/user/project", limit=2)
        assert len(results) <= 2
        engine.close()

    def test_scores_are_positive(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_suggestions("git", "/home/user/project")
        for r in results:
            assert r.score > 0
        engine.close()

    def test_special_characters_escaped(self, db_path):
        conn = sqlite3.connect(str(db_path))
        now = time.time()
        conn.execute(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES (?, ?, ?, ?)",
            ("test_cmd%weird", "/home", 0, now),
        )
        conn.execute(
            "INSERT INTO command_history (command, cwd, exit_status, timestamp) VALUES (?, ?, ?, ?)",
            ("test_cmd_normal", "/home", 0, now),
        )
        conn.commit()
        conn.close()

        engine = PredictionEngine(db_path)
        # Searching for "test_cmd%" should only match the literal % command
        results = engine.get_suggestions("test_cmd%", "/home")
        commands = [r.command for r in results]
        assert "test_cmd%weird" in commands
        assert "test_cmd_normal" not in commands
        engine.close()


class TestNextSteps:
    """Tests for the learned next-step suggestions."""

    def test_next_steps_after_known_command(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("git status", "/home/user/project")
        commands = [r.command for r in results]
        # After "git status", "git add ." should be suggested
        assert "git add ." in commands
        engine.close()

    def test_next_steps_exclude_same_command(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("git status", "/home/user/project")
        commands = [r.command for r in results]
        assert "git status" not in commands
        engine.close()

    def test_next_steps_empty_for_unknown_command(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("zzz_unknown", "/home/user/project")
        assert results == []
        engine.close()

    def test_next_steps_directory_specific(self, populated_db):
        engine = PredictionEngine(populated_db)
        # "make build" was only run in /home/user/firmware
        results = engine.get_next_steps("make build", "/home/user/firmware")
        commands = [r.command for r in results]
        assert "make test" in commands
        engine.close()

    def test_next_steps_confidence_normalized(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("git status", "/home/user/project")
        if results:
            assert results[0].confidence == 1.0
            for r in results:
                assert 0 < r.confidence <= 1.0
        engine.close()

    def test_next_steps_source_is_learned(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("git status", "/home/user/project")
        for r in results:
            assert r.source == "learned"
        engine.close()

    def test_next_steps_limit_respected(self, populated_db):
        engine = PredictionEngine(populated_db)
        results = engine.get_next_steps("git status", "/home/user/project", limit=1)
        assert len(results) <= 1
        engine.close()


class TestEscapeLike:
    """Tests for SQL LIKE pattern escaping."""

    def test_backslash_escaped(self):
        assert PredictionEngine._escape_like("a\\b") == "a\\\\b"

    def test_percent_escaped(self):
        assert PredictionEngine._escape_like("100%") == "100\\%"

    def test_underscore_escaped(self):
        assert PredictionEngine._escape_like("my_cmd") == "my\\_cmd"

    def test_normal_text_unchanged(self):
        assert PredictionEngine._escape_like("git status") == "git status"

    def test_multiple_specials(self):
        assert PredictionEngine._escape_like("%_\\") == "\\%\\_\\\\"
