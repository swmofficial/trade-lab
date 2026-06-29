"""
Brick 1a runner.

Order of operations:
  1. Run the gate (tests/test_integrity.py). If it fails, STOP — we do not trust the
     checks enough to run them over real data.
  2. Ingest all three pairs from Twelve Data and store raw bars (idempotent).
  3. Run every integrity check over the stored bars, record flags.
  4. Emit an integrity report (stdout + data/integrity_report.txt).

Raw bars are never altered. Flags are derived and rewritten each run.
"""

import os
import subprocess
import sys
from datetime import date, timedelta

import ingest
import integrity as ig
import store

REPORT_PATH = os.path.join("data", "integrity_report.txt")


def run_gate():
    print("=== GATE: fixture integrity test ===")
    here = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(here, "tests", "test_integrity.py")
    result = subprocess.run([sys.executable, test_path])
    if result.returncode != 0:
        print("GATE FAILED — aborting before touching real data.")
        sys.exit(1)
    print()


def weekday_count(first, last):
    """Rough count of Mon-Fri days inclusive between two ISO dates."""
    d0 = ig._parse_date(first)
    d1 = ig._parse_date(last)
    n = 0
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def ingest_all(conn, api_key):
    print("=== INGEST: Twelve Data ===")
    for pair in ingest.PAIRS:
        print(f"{pair}:")
        bars = ingest.fetch_pair(pair, api_key)
        inserted, skipped = store.insert_bars(conn, bars)
        print(f"  fetched={len(bars)} inserted={inserted} duplicates_skipped={skipped}\n")


def check_all(conn):
    print("=== CHECKS: intra-source integrity over stored bars ===")
    store.clear_flags(conn, ingest.SOURCE)
    for pair in ingest.PAIRS:
        bars = store.load_bars(conn, pair, ingest.SOURCE)
        for fn in ig.ALL_CHECKS:
            flags = fn(bars)
            if flags:
                store.insert_flags(conn, flags)
        print(f"  {pair}: checked {len(bars)} bars")
    print()


def build_report(conn):
    lines = []
    w = lines.append
    w("=" * 70)
    w("TRADING BS-DETECTOR — Brick 1a intra-source integrity report")
    w(f"source: {ingest.SOURCE}   interval: {ingest.INTERVAL}")
    w("=" * 70)

    for pair in ingest.PAIRS:
        bars = store.load_bars(conn, pair, ingest.SOURCE)
        w("")
        w(f"### {pair}")
        if not bars:
            w("  NO BARS STORED")
            continue
        first, last = bars[0]["date"], bars[-1]["date"]
        wd = weekday_count(first, last)
        cov = (len(bars) / wd * 100) if wd else 0.0
        w(f"  first date : {first}")
        w(f"  last date  : {last}")
        w(f"  total bars : {len(bars)}")
        w(f"  coverage   : {len(bars)} bars vs ~{wd} weekdays in span "
          f"({cov:.1f}%)  [gaps below are candidates for review]")

        flag_rows = conn.execute(
            "SELECT check_name, date, detail FROM flags "
            "WHERE pair = ? AND source = ? ORDER BY check_name, date",
            (pair, ingest.SOURCE),
        ).fetchall()

        by_check = {}
        for r in flag_rows:
            by_check.setdefault(r["check_name"], []).append(r)

        w("  flag counts by check:")
        for fn in ig.ALL_CHECKS:
            name = fn.__name__
            w(f"    {name:<22} {len(by_check.get(name, []))}")

        if flag_rows:
            w("  flagged rows:")
            for r in flag_rows:
                w(f"    [{r['check_name']}] {r['date']}: {r['detail']}")
        else:
            w("  flagged rows: none")

    report = "\n".join(lines)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nReport written to {REPORT_PATH}")


def main():
    run_gate()

    ingest.load_env()
    api_key = ingest.get_api_key()  # raises loudly if missing

    conn = store.connect()
    store.init_schema(conn)

    ingest_all(conn, api_key)
    check_all(conn)
    build_report(conn)
    conn.close()


if __name__ == "__main__":
    main()
