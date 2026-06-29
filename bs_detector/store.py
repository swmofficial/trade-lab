"""
SQLite storage for raw bars and integrity flags.

Source-tagged from the start: UNIQUE(pair, date, source) means a second data source
is pure insertion, never a schema change. INSERT OR IGNORE keeps ingest idempotent.
Raw bars are never mutated by integrity work — defects live only in `flags`.
"""

import sqlite3

DB_PATH = "bsdetector.db"


def connect(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bars (
            pair    TEXT NOT NULL,
            date    TEXT NOT NULL,            -- 'YYYY-MM-DD'
            source  TEXT NOT NULL,
            open    REAL NOT NULL,
            high    REAL NOT NULL,
            low     REAL NOT NULL,
            close   REAL NOT NULL,
            volume  REAL,                     -- NULL/0 allowed for forex
            UNIQUE(pair, date, source)
        );

        CREATE TABLE IF NOT EXISTS flags (
            pair       TEXT NOT NULL,
            date       TEXT,
            source     TEXT,
            check_name TEXT NOT NULL,
            detail     TEXT
        );
        """
    )
    conn.commit()


def insert_bars(conn, bars):
    """
    INSERT OR IGNORE each bar. Returns (inserted, skipped_duplicates).
    """
    inserted = 0
    cur = conn.cursor()
    for b in bars:
        before = conn.total_changes
        cur.execute(
            """
            INSERT OR IGNORE INTO bars
                (pair, date, source, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (b["pair"], b["date"], b["source"],
             b["open"], b["high"], b["low"], b["close"], b["volume"]),
        )
        if conn.total_changes > before:
            inserted += 1
    conn.commit()
    skipped = len(bars) - inserted
    return inserted, skipped


def load_bars(conn, pair, source):
    """All bars for one pair/source, ordered by date ascending."""
    rows = conn.execute(
        "SELECT pair, date, source, open, high, low, close, volume "
        "FROM bars WHERE pair = ? AND source = ? ORDER BY date ASC",
        (pair, source),
    ).fetchall()
    return [dict(r) for r in rows]


def clear_flags(conn, source):
    """Flags are derived data; wipe a source's flags before a fresh check run."""
    conn.execute("DELETE FROM flags WHERE source = ?", (source,))
    conn.commit()


def insert_flags(conn, flags):
    conn.executemany(
        "INSERT INTO flags (pair, date, source, check_name, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        [(f["pair"], f["date"], f["source"], f["check_name"], f["detail"])
         for f in flags],
    )
    conn.commit()
