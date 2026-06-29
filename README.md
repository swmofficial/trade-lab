# trade-lab

A standalone research lab for systematic trading work. It is a **multi-component**
repo: each component is a self-contained subfolder with its own code, tests, and (where
relevant) its own data store. Components are developed brick by brick and are only wired
together once each one stands on its own.

This repo is fully independent — it shares no code, config, or history with any other
project.

## Components

### 1. `bs_detector/` — backtesting data BS-detector *(Brick 1a, gate green ✅)*

Pulls clean daily OHLC for three forex majors (EUR/USD, GBP/USD, USD/JPY) from a single
source (Twelve Data) into SQLite, then runs explicit, plain-Python intra-source integrity
checks that **detect and flag** dirty bars — never delete, repair, or silently clean them.
Proven by a deterministic fixture gate (12/12 fixtures: a clean run flags nothing; each
planted defect is caught by exactly its check).

Run it:

```bash
cd bs_detector
pip install -r requirements.txt
python tests/test_integrity.py     # the gate — no API key, no network
python run.py                      # gate -> ingest -> checks -> integrity report (needs a key)
```

See `bs_detector/README.md` for the full check list, thresholds, and scope boundary.

## Planned components

These are **not built yet** — noted so the lab's shape is clear:

- **`cross_validator/`** — second data source + cross-source agreement checks (the
  natural successor to the single-source bs_detector).
- **`strategy_lib/`** — a library of systematic trading strategies, each independently
  specified and testable.
- **`backtest_engine/`** — runs strategies over validated bars and reports performance.

Each lands as its own component subfolder, the same way `bs_detector/` did.

## Conventions

- One component = one subfolder, self-contained and runnable from inside its own folder.
- Components never reach across into each other's internals; any future sharing is an
  explicit, deliberate seam.
- Secrets live in per-component `.env` files (gitignored lab-wide); SQLite stores are
  local and gitignored.
