"""
Brick 2a runner. Wires the pieces over real EUR/USD daily bars and prints a report.

Order of operations mirrors bs_detector's discipline:
  1. Run the gate (tests/test_engine.py). If it fails, STOP — we do not trust the
     engine enough to run it over real data.
  2. Load EUR/USD daily bars (read-only) from bs_detector's store, excluding weekend
     and stale-flagged bars.
  3. Generate mean-reversion signals (lookback=20, k=2.0), run the backtest.
  4. Print trades, final equity, and honest metrics.

These results are OPTIMISTIC by construction (no costs, no slippage, close-to-close
fills, one position at a time). This brick proves the plumbing, not an edge.
"""

import os
import subprocess
import sys

import data
import metrics as mx
from strategy import mean_reversion_signals
from engine import run_backtest

PAIR = "EUR/USD"
LOOKBACK = 20
K = 2.0
STARTING_EQUITY = 10000.0
SHOW_FIRST_TRADES = 8


def run_gate():
    print("=== GATE: synthetic engine test ===")
    here = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(here, "tests", "test_engine.py")
    result = subprocess.run([sys.executable, test_path])
    if result.returncode != 0:
        print("GATE FAILED -- aborting before touching real data.")
        sys.exit(1)
    print()


def main():
    run_gate()

    print("=" * 64)
    print("PLUMBING PROOF ONLY -- results EXCLUDE costs, slippage, and realistic")
    print("fills. They are OPTIMISTIC by construction and are NOT an edge estimate.")
    print("=" * 64)

    bars, excluded = data.load_bars(PAIR)
    print(f"\n{PAIR} daily")
    print(f"  bars loaded   : {len(bars)}")
    print(f"  bars excluded : {excluded}  (weekend/stale flags from bs_detector)")
    if bars:
        print(f"  date range    : {bars[0].date} -> {bars[-1].date}")

    signals = mean_reversion_signals(bars, lookback=LOOKBACK, k=K)
    result = run_backtest(bars, signals, starting_equity=STARTING_EQUITY)

    print(f"\nstrategy        : mean reversion (lookback={LOOKBACK}, k={K})")
    print(f"starting equity : {STARTING_EQUITY:,.2f}")
    print(f"final equity    : {result.final_equity:,.2f}")
    print(f"closed trades   : {len(result.trades)}")

    print(f"\nfirst {SHOW_FIRST_TRADES} trades:")
    print(
        f"  {'entry date':<12} {'entry':>9}  {'exit date':<12} {'exit':>9}  {'ret %':>8}"
    )
    for t in result.trades[:SHOW_FIRST_TRADES]:
        print(
            f"  {str(t.entry_date):<12} {t.entry_price:>9.5f}  "
            f"{str(t.exit_date):<12} {t.exit_price:>9.5f}  {t.return_pct:>+8.2f}"
        )
    if result.open_position is not None:
        op = result.open_position
        print(
            f"  OPEN position still held: entered {op.entry_date} @ {op.entry_price:.5f}"
        )

    print("\nmetrics:")
    print(mx.format_metrics(mx.compute_metrics(result)))
    print()


if __name__ == "__main__":
    main()
