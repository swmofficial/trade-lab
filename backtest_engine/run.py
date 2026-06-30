"""
Brick 2b runner. Re-runs the Brick 2a mean-reversion backtest over EUR/USD while
sweeping a fixed band of round-trip spread costs, to find where the result dies.

Order of operations mirrors bs_detector's discipline:
  1. Run the gate (tests/test_engine.py). If it fails, STOP.
  2. Load EUR/USD daily bars (read-only) from bs_detector's store, excluding weekend
     and stale-flagged bars.
  3. Generate signals once (lookback=20, k=2.0) — signals do NOT depend on costs, so
     the trade COUNT is identical across the sweep; only P&L changes.
  4. Backtest at spread_pips in [0, 1, 2, 3] and print a net-vs-gross table.
  5. Report the crossover: the spread at which total return goes <= 0.

These results still EXCLUDE slippage beyond spread and walk-forward, use close-to-close
fills, one position full in/out. NOT an edge estimate — plumbing + cost sensitivity only.
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
# Fixed band: 0 = sanity (must equal Brick 2a gross), 1 = generous live,
# 2 = realistic for close-fills, 3 = stress.
SPREAD_BAND = [0, 1, 2, 3]


def run_gate():
    print("=== GATE: synthetic engine + cost test ===")
    here = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(here, "tests", "test_engine.py")
    result = subprocess.run([sys.executable, test_path])
    if result.returncode != 0:
        print("GATE FAILED -- aborting before touching real data.")
        sys.exit(1)
    print()


def main():
    run_gate()

    print("=" * 72)
    print("COST-SENSITIVITY PROOF -- results EXCLUDE slippage beyond spread and")
    print("walk-forward, use close-to-close fills, one position full in/out.")
    print("NOT an edge estimate.")
    print("=" * 72)

    bars, excluded = data.load_bars(PAIR)
    print(f"\n{PAIR} daily")
    print(f"  bars loaded   : {len(bars)}")
    print(f"  bars excluded : {excluded}  (weekend/stale flags from bs_detector)")
    if bars:
        print(f"  date range    : {bars[0].date} -> {bars[-1].date}")

    # Signals are cost-independent: generate ONCE, reuse for every spread.
    signals = mean_reversion_signals(bars, lookback=LOOKBACK, k=K)

    print(f"\nstrategy        : mean reversion (lookback={LOOKBACK}, k={K})")
    print(f"starting equity : {STARTING_EQUITY:,.2f}\n")

    header = (
        f"  {'spread':>6}  {'gross%':>8}  {'net%':>8}  {'trades':>6}  "
        f"{'win%(net)':>9}  {'avgWin%':>8}  {'avgLoss%':>9}  {'maxDD%(net)':>11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    crossover = None
    prev_positive = True
    for spread in SPREAD_BAND:
        result = run_backtest(
            bars, signals, PAIR, starting_equity=STARTING_EQUITY, spread_pips=spread
        )
        m = mx.compute_metrics(result)
        print(
            f"  {spread:>6}  {m['gross_total_return_pct']:>+8.2f}  "
            f"{m['total_return_pct']:>+8.2f}  {m['num_trades']:>6}  "
            f"{m['win_rate_pct']:>9.1f}  {m['avg_win_pct']:>+8.2f}  "
            f"{m['avg_loss_pct']:>+9.2f}  {m['max_drawdown_pct']:>11.2f}"
        )
        # First spread at which net total return goes from positive to <= 0.
        if crossover is None and prev_positive and m["total_return_pct"] <= 0:
            crossover = spread
        prev_positive = m["total_return_pct"] > 0

    print()
    if crossover is not None:
        print(
            f"CROSSOVER: net total return first goes <= 0 at spread_pips = {crossover}."
        )
    else:
        hi = SPREAD_BAND[-1]
        print(
            f"CROSSOVER: net total return stays POSITIVE across the whole band "
            f"(0..{hi} pips) -- it does not die within the tested band."
        )
    print(
        "(Trade count is constant across the band by construction: costs change the "
        "P&L of each trade, never WHEN the strategy trades.)"
    )
    print()


if __name__ == "__main__":
    main()
