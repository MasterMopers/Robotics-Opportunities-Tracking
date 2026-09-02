"""SQLite schema and helpers for state.db."""

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    class TEXT,                  -- source's declared class: contest|grant|both
    title TEXT,
    url TEXT NOT NULL,
    snippet TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',   -- new|accepted|review|rejected|expired|stale
    final_class TEXT,            -- contest|grant, once classified
    contest_score REAL,
    grant_score REAL,
    matched_signals TEXT,        -- JSON list
    reject_phrase TEXT,
    deadline_date TEXT,
    deadline_confidence TEXT,    -- explicit|relative|none
    money_raw TEXT,
    team_size TEXT,
    host_type TEXT,
    enriched INTEGER NOT NULL DEFAULT 0,
    reported INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS calendar_state (
    id TEXT PRIMARY KEY,
    last_alerted_for TEXT        -- e.g. "2026-09-25" -- so we alert once per cycle
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY,
    last_run TEXT,
    last_status TEXT,            -- OK|BROKEN|ERROR
    last_count INTEGER,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def connect(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# SQLite's ALTER TABLE has no "ADD COLUMN IF NOT EXISTS" -- check
# PRAGMA table_info first so re-running init_db on an already-migrated
# state.db is a no-op instead of a "duplicate column" error.
NEW_COLUMNS = [
    ("location", "TEXT"),
    ("location_format", "TEXT"),
    ("location_confidence", "TEXT"),
    ("participants_count", "INTEGER"),
    ("participants_confidence", "TEXT"),
]


def init_db(path: str) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
        for name, coltype in NEW_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE items ADD COLUMN {name} {coltype}")
