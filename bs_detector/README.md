# Trading BS-Detector — Brick 1a

First brick of a backtesting validation engine. It does ONE thing: pull clean daily
OHLC for three forex majors (EUR/USD, GBP/USD, USD/JPY) from a **single source**
(Twelve Data) and prove, with a deterministic test, that we can detect dirty bars.

> **Non-negotiable rule:** this brick **detects and flags** data defects. It never
> deletes, repairs, interpolates, or silently "cleans" a bar. Defects are recorded in
> the `flags` table alongside the untouched raw bars in `bars`.

This is **not** cross-validation — that's a second source, later. Nothing here builds
ahead of that.

## Stack

Python 3, stdlib + `requests` only. **No pandas** — every integrity check is explicit,
plain-Python, auditable line by line. Storage: SQLite (`bsdetector.db`). API key from
env var `TWELVEDATA_API_KEY`, loaded from `.env` (gitignored, never hardcoded).

## Files

| File | Role |
|---|---|
| `integrity.py` | Six pure-function integrity checks + named thresholds |
| `store.py` | SQLite schema, idempotent bar insert, flag storage |
| `ingest.py` | Twelve Data fetch: paging, rate-limit retry, `.env` loader |
| `run.py` | Gate → ingest → check → report |
| `tests/test_integrity.py` | The gate: synthetic fixtures, ground-truth assertions |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then put your free Twelve Data key in .env
```

## Run the gate (no API key, no network)

```bash
python tests/test_integrity.py
```

Prints PASS/FAIL per fixture. The clean run must produce zero flags; each planted
defect must be caught by exactly its check.

## Full run (needs a key)

```bash
python run.py
```

Runs the gate first (aborts if it fails), then ingests all three pairs, runs every
check over the stored bars, writes flags, and emits `data/integrity_report.txt`.

## Integrity checks & thresholds

| Check | What it flags | Threshold |
|---|---|---|
| `check_ohlc_sanity` | `NOT (low≤open≤high AND low≤close≤high AND high≥low AND all > 0)` | — |
| `check_absurd_jump` | close-to-close move beyond `MAX_DAILY_RETURN` (flags both bars) | `0.10` |
| `check_stale_bar` | OHLC frozen == previous bar; **and** zero-range `O==H==L==C` | — |
| `check_weekend_bar` | bar dated Saturday or Sunday | — |
| `check_gaps` | any **expected trading day** missing between adjacent bars (CANDIDATE); reports the count of skipped weekdays | — (no magnitude threshold) |
| `check_monotonic` | duplicate or out-of-order date for a pair/source | — |

`MAX_DAILY_RETURN` is fixed **before** seeing real data and is not tuned to it.

`check_gaps` reasons about the **expected next trading day** (Mon–Thu → +1 day,
Fri → +3 to Mon) rather than a calendar-distance cutoff, so it catches a *single*
missing trading day — the common real defect that a ">N calendar days" rule misses.
It deliberately over-flags holidays as candidates: at this stage we cannot distinguish
a benign holiday from a real gap without a holiday calendar (a later sub-brick). Candidate
gaps are surfaced, not swept.

## Scope boundary

No backtest logic. No second source. No strategy code. Brick stops here.
