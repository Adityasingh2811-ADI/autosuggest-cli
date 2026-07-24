"""
Frecency prediction engine — scores historical commands by combining
frequency, exponential recency decay, and directory-context weighting.
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from autosuggest.paths import db_path as _resolve_db_path, apply_journal_mode

# Decay half-life for prefix suggestions (1 hour) — rewards recent typing.
HALF_LIFE = 3600.0
DECAY_LAMBDA = 0.693147 / HALF_LIFE  # ln(2) / half_life

# Decay half-life for next-step sequences (7 days) — rewards repeated patterns
# over weeks, not just what happened in the last hour.
NEXT_STEPS_HALF_LIFE = 604800.0

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
SELECT curr.command AS prev_cmd, next.command AS next_cmd, next.timestamp, curr.cwd
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
    -- Fractional Unix timestamp; two equivalent formulations:
    --   HEAD:     strftime('%s','now') + (strftime('%f','now') - strftime('%S','now'))
    --   incoming: (julianday('now') - 2440587.5) * 86400.0
    timestamp REAL NOT NULL DEFAULT (strftime('%s', 'now') + (strftime('%f', 'now') - strftime('%S', 'now')))
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
        home = str(Path.home())
        normalized_cwd = current_cwd.replace("~", home) if current_cwd.startswith("~") else current_cwd

        rows = self._conn.execute(
            _NEXT_STEPS_QUERY, (last_command,)
        ).fetchall()

        _SKIP_CMDS = frozenset({"exit", "quit", "clear", "ls", "pwd"})

        counts: dict[str, int] = {}
        recency_best: dict[str, float] = {}
        for _prev, next_cmd, ts, row_cwd in rows:
            if next_cmd == last_command or next_cmd in _SKIP_CMDS:
                continue
            norm_row_cwd = row_cwd.replace("~", home) if row_cwd.startswith("~") else row_cwd
            context = CONTEXT_BOOST if norm_row_cwd == normalized_cwd else 1.0
            counts[next_cmd] = counts.get(next_cmd, 0) + 1
            recency = _recency_score_nextsteps(now, ts) * context
            if next_cmd not in recency_best or recency > recency_best[next_cmd]:
                recency_best[next_cmd] = recency

        if not counts:
            return []

        # Hybrid score: frequency dominates, recency breaks ties.
        # A command used 10 times weeks ago scores higher than one used once yesterday.
        scores: dict[str, float] = {}
        for cmd in counts:
            scores[cmd] = counts[cmd] + recency_best[cmd]

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


def _recency_score_nextsteps(now: float, timestamp: float) -> float:
    age = now - timestamp
    return 2.0 ** (-age / NEXT_STEPS_HALF_LIFE)
