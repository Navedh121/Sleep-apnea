# database.py — SQLite connection and schema initialisation.
#
# The schema here is copied EXACTLY from spec §10. Do not rename columns.
# SQLite has no real BOOLEAN type, so 'insufficient' is stored as 0 or 1.

import sqlite3
import os

# The database file lives in the Sleep-apnea/ root directory (one level up
# from this backend/ folder).
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "spo2.db")


def get_connection() -> sqlite3.Connection:
    """
    Open and return a connection to spo2.db.
    row_factory = sqlite3.Row makes rows behave like dicts so you can do
    row["session_id"] instead of row[0].
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us access columns by name
    conn.execute("PRAGMA foreign_keys = ON")  # enforce the FK on samples.session_id
    return conn


def init_db() -> None:
    """
    Create the two tables (nights + samples) if they don't exist yet.
    Safe to call every time the server starts — CREATE TABLE IF NOT EXISTS
    does nothing when the tables are already there.
    """
    conn = get_connection()
    try:
        conn.executescript("""
            -- One row per night session.
            -- Exact schema from spec §10 — do not alter column names or types.
            CREATE TABLE IF NOT EXISTS nights (
                session_id          INTEGER PRIMARY KEY,
                received_date       TEXT NOT NULL,
                duration_s          INTEGER NOT NULL,
                valid_duration_s    INTEGER NOT NULL,
                sample_count        INTEGER NOT NULL,
                valid_sample_count  INTEGER NOT NULL,
                spo2_baseline       INTEGER,
                spo2_min            INTEGER,
                spo2_mean           REAL,
                time_below_90_s     INTEGER,
                time_below_88_s     INTEGER,
                events              INTEGER,
                odi                 REAL,
                band                TEXT NOT NULL,
                insufficient        INTEGER NOT NULL,
                rf_index            REAL,
                rf_confidence       REAL,
                hourly_json         TEXT,
                event_list_json     TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            -- One row per raw sample.
            -- The index makes "give me all samples for session X in order" fast.
            CREATE TABLE IF NOT EXISTS samples (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES nights(session_id),
                t           INTEGER NOT NULL,
                spo2        INTEGER NOT NULL,
                hr          INTEGER NOT NULL,
                flag        TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_samples_session_t
                ON samples(session_id, t);
        """)
        conn.commit()
    finally:
        conn.close()
