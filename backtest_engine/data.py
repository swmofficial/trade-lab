"""
Read-only loader for daily bars from bs_detector's SQLite store.

This component is a SIBLING of bs_detector, not a child. It must never import
bs_detector code nor write to its database — it only opens the DB file READ-ONLY
(mode=ro) and reads the `bars` and `flags` tables. If bs_detector's schema or data
changes, that's bs_detector's concern; we adapt here, we never reach back in.

For this brick we only trade real trading days, so bars flagged weekend or stale by
bs_detector are dropped before they ever reach the strategy. The count of excluded
bars is returned alongside the bars so the runner can report it honestly.
"""

import pathlib
import sqlite3
from collections import namedtuple
from datetime import date

# A bar is intentionally a tiny, immutable value. The strategy and engine only ever
# need .date and .close, but we carry full OHLCV so later bricks need not re-load.
Bar = namedtuple("Bar", "date open high low close volume")

# Resolve the sibling DB relative to THIS file, so the loader works from any CWD.
DB_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "bs_detector"
    / "bsdetector.db"
)

# Bars carrying either of these bs_detector flags are not real trading days for us.
EXCLUDE_CHECKS = ("check_weekend_bar", "check_stale_bar")


def _parse_date(s):
    """'YYYY-MM-DD' -> datetime.date."""
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def load_bars(pair, source="twelvedata", db_path=DB_PATH):
    """
    Load all bars for one pair/source, ascending by date, EXCLUDING any bar whose
    (pair, date) is flagged weekend or stale in bs_detector's `flags` table.

    Returns (bars, excluded_count). The DB is opened strictly read-only; this loader
    cannot mutate bs_detector's store even if it tried.
    """
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        flagged = {
            row[0]
            for row in conn.execute(
                "SELECT date FROM flags "
                "WHERE pair = ? AND source = ? "
                f"AND check_name IN ({','.join('?' * len(EXCLUDE_CHECKS))})",
                (pair, source, *EXCLUDE_CHECKS),
            )
        }
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM bars "
            "WHERE pair = ? AND source = ? ORDER BY date ASC",
            (pair, source),
        ).fetchall()
    finally:
        conn.close()

    bars = []
    excluded = 0
    for date_s, o, h, l, c, v in rows:
        if date_s in flagged:
            excluded += 1
            continue
        bars.append(
            Bar(_parse_date(date_s), float(o), float(h), float(l), float(c), v)
        )
    return bars, excluded
