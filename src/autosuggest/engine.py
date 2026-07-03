"""
Frecency prediction engine — scores historical commands by combining
frequency, exponential recency decay, and directory-context weighting.
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from autosuggest.paths import db_path as _resolve_db_path, apply_journal_mode

# Decay half-life in seconds (1 hour).
# After 1 hour a command's recency contribution halves.
HALF_LIFE = 3600.0
DECAY_LAMBDA = 0.693147 / HALF_LIFE  # ln(2) / half_life

# Multiplier applied when historical cwd matches the query cwd exactly.
CONTEXT_BOOST = 3.0

_QUERY = """
SELECT command, cwd, timestamp
FROM command_history
WHERE command LIKE ? ESCAPE '\\'
  AND exit_status = 0
ORDER BY timestamp DESC
LIMIT 500
"""

_NEXT_STEPS_QUERY = """
SELECT curr.command AS prev_cmd, next.command AS next_cmd, next.timestamp
FROM command_history curr
JOIN command_history next
  ON next.cwd = curr.cwd
  AND next.timestamp > curr.timestamp
  AND next.timestamp - curr.timestamp < 300
  AND next.id = (
      SELECT MIN(n.id) FROM command_history n
      WHERE n.cwd = curr.cwd AND n.id > curr.id
  )
WHERE curr.command = ?
  AND curr.cwd = ?
  AND curr.exit_status = 0
  AND next.exit_status = 0
ORDER BY next.timestamp DESC
LIMIT 200
"""


@dataclass(slots=True)
class Suggestion:
    command: str
    score: float


@dataclass(slots=True)
class NextStep:
    command: str
    confidence: float
    source: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS command_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    cwd TEXT NOT NULL,
    exit_status INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL DEFAULT ((julianday('now') - 2440587.5) * 86400.0)
);
CREATE INDEX IF NOT EXISTS idx_frecency
    ON command_history(cwd, timestamp DESC, command);
CREATE INDEX IF NOT EXISTS idx_global_recency
    ON command_history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_frequency
    ON command_history(command, cwd);
CREATE INDEX IF NOT EXISTS idx_sequence
    ON command_history(cwd, command, timestamp);
"""


class PredictionEngine:
    def __init__(self, db_path: str | Path | None = None) -> None:
        # Resolve lazily so importing this module never touches (or creates)
        # the real user database — important for tests and tooling.
        if db_path is None:
            db_path = _resolve_db_path()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000;")
        apply_journal_mode(self._conn, Path(db_path))
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA query_only=ON;")

    def get_suggestions(
        self,
        partial_command: str,
        current_cwd: str,
        limit: int = 5,
    ) -> list[Suggestion]:
        now = time.time()
        pattern = self._escape_like(partial_command) + "%"
        rows = self._conn.execute(_QUERY, (pattern,)).fetchall()

        scores: dict[str, float] = {}
        for command, cwd, ts in rows:
            recency = _recency_score(now, ts)
            context = CONTEXT_BOOST if cwd == current_cwd else 1.0
            scores[command] = scores.get(command, 0.0) + recency * context

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [Suggestion(cmd, score) for cmd, score in ranked]

    def get_next_steps(
        self,
        last_command: str,
        current_cwd: str,
        limit: int = 3,
    ) -> list[NextStep]:
        now = time.time()
        rows = self._conn.execute(
            _NEXT_STEPS_QUERY, (last_command, current_cwd)
        ).fetchall()

        scores: dict[str, float] = {}
        for _prev, next_cmd, ts in rows:
            if next_cmd == last_command:
                continue
            recency = _recency_score(now, ts)
            scores[next_cmd] = scores.get(next_cmd, 0.0) + recency

        if not scores:
            return []

        max_score = max(scores.values())
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [
            NextStep(cmd, round(score / max_score, 3), "learned")
            for cmd, score in ranked
        ]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _escape_like(text: str) -> str:
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _recency_score(now: float, timestamp: float) -> float:
    age = now - timestamp
    return 2.0 ** (-age / HALF_LIFE)
