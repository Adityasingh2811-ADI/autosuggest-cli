CREATE TABLE IF NOT EXISTS command_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    cwd TEXT NOT NULL,
    exit_status INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL DEFAULT ((julianday('now') - 2440587.5) * 86400.0)
);

-- Frecency reads filter by cwd then sort by timestamp desc.
-- Covering index: lookup by cwd, scan in recency order, access command without touching the table.
CREATE INDEX IF NOT EXISTS idx_frecency
    ON command_history(cwd, timestamp DESC, command);

-- Global recency queries (cross-directory suggestions).
CREATE INDEX IF NOT EXISTS idx_global_recency
    ON command_history(timestamp DESC);

-- Fast frequency counts per (command, cwd) pair.
CREATE INDEX IF NOT EXISTS idx_frequency
    ON command_history(command, cwd);

-- Sequential pattern queries: find what command follows another in the same directory.
CREATE INDEX IF NOT EXISTS idx_sequence
    ON command_history(cwd, command, timestamp);
