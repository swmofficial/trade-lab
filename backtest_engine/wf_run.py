"""
Brick 2c runner. Walk-forward out-of-sample validation on EUR/USD.

Optimizes params (lookback x k, 9 combos) on each 5yr TRAIN window, freezes them, and
measures on the following 2yr TEST window. The stitched test-window curve is the ONLY
result that counts. The in-sample 2b number (lookback=20/k=2.0 over everything) is shown
for contrast: out-of-sample SHOULD be worse — if OOS beats IS, that's a flag to
investigate, not celebrate.

Order: gate -> load EUR/USD (read-only) -> walk-forward -> per-fold table -> headline.

Still EXCLUDES slippage beyond spread; close-to-close fills; one position full in/out.
The 9-combo grid is NOT yet multiple-test-corrected (next brick). NOT an edge estimate.
"""

import os
import subprocess
import sys

import data
import metrics as mx
import walkforward as wf
from strategy import mean_reversion_signals
from engine import run_backtest

PAIR = "EUR/USD"
IS_LOOKBACK = 20   # Brick 2b in-sample params, for contrast only
IS_K = 2.0


def run_gate():
    print("=== GATE: synthetic engine + cost + walk-forward leakage test ===")
    here = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(here, "tests", "test_engine.py")
    result = subprocess.run([sys.executable, test_path])
    if result.returncode != 0:
        print("GATE FAILED -- aborting before touching real data.")
        sys.exit(1)
    print()


def in_sample_2b(bars):
    """Brick 2b: fixed lookback=20/k=2.0 over ALL bars at spread 2. For contrast."""
    sigs = mean_reversion_signals(bars, lookback=IS_LOOKBACK, k=IS_K)
    res = run_backtest(
        bars, sigs, PAIR,
        starting_equity=wf.STARTING_EQUITY, spread_pips=wf.SPREAD_PIPS,
    )
    return wf.net_total_return(res)


def main():
    run_gate()

    print("=" * 78)
    print("OUT-OF-SAMPLE WALK-FORWARD -- the stitched TEST curve is the only number that")
    print("counts. EXCLUDES slippage beyond spread; close-to-close fills; 9-combo grid")
    print("NOT yet multiple-test-corrected. NOT an edge estimate.")
    print("=" * 78)

    bars, excluded = data.load_bars(PAIR)
    print(f"\n{PAIR} daily")
    print(f"  bars loaded   : {len(bars)}")
    print(f"  bars excluded : {excluded}  (weekend/stale flags from bs_detector)")
    if bars:
        print(f"  date range    : {bars[0].date} -> {bars[-1].date}")
    print(
        f"\nscheme: train={wf.TRAIN_YEARS}yr test={wf.TEST_YEARS}yr step={wf.STEP_YEARS}yr "
        f"| grid lookback{list(wf.LOOKBACKS)} x k{list(wf.KS)} (9 combos) "
        f"| select on TRAIN net return @ spread={wf.SPREAD_PIPS} pips"
    )

    result = wf.walk_forward(bars)

    for f in result.dropped:
        print(
            f"\n[dropped] final fold test {f.test_start}->{f.test_end} "
            f"has < {wf.MIN_TEST_YEARS}yr of data; excluded."
        )

    print(f"\nper-fold ({len(result.fold_results)} folds):")
    head = (
        f"  {'#':>2}  {'train window':<25} {'params(lb,k)':>12}  {'train%':>8}  "
        f"{'test window':<25} {'test%':>8}  {'trades':>6}"
    )
    print(head)
    print("  " + "-" * (len(head) - 2))
    for i, fr in enumerate(result.fold_results):
        f = fr.fold
        tw = f"{f.train_start} -> {f.train_end}"
        ew = f"{f.test_start} -> {f.test_end}" + (" *" if f.truncated else "")
        print(
            f"  {i:>2}  {tw:<25} {str(fr.params):>12}  {fr.train_return:>+8.2f}  "
            f"{ew:<25} {fr.test_return:>+8.2f}  {len(fr.test_result.trades):>6}"
        )
    print("  (* = truncated final test window, kept because >= min test years)")

    # Stitched out-of-sample headline.
    oos_total = (
        (result.final_equity - result.starting_equity) / result.starting_equity * 100.0
    )
    oos_trades = [t for fr in result.fold_results for t in fr.test_result.trades]
    n = len(oos_trades)
    oos_wins = [t for t in oos_trades if t.net_return_pct > 0]
    oos_win_rate = (len(oos_wins) / n * 100.0) if n else 0.0
    oos_maxdd = mx._max_drawdown_pct(result.stitched_curve)

    is_total = in_sample_2b(bars)

    print("\n" + "=" * 78)
    print("HEADLINE")
    print(f"  OUT-OF-SAMPLE (stitched test windows, frozen params):")
    print(f"    total net return : {oos_total:+.2f}%")
    print(f"    trades           : {n}")
    print(f"    win rate (net)   : {oos_win_rate:.1f}%")
    print(f"    max drawdown     : {oos_maxdd:.2f}%")
    print(f"  IN-SAMPLE (Brick 2b, lookback={IS_LOOKBACK}/k={IS_K} over all bars):")
    print(f"    total net return : {is_total:+.2f}%")
    gap = is_total - oos_total
    print(f"  IS - OOS gap       : {gap:+.2f} pts", end="  ")
    if oos_total > is_total:
        print("<-- OOS BEAT IS: red flag, investigate (not celebrate)")
    else:
        print("(OOS worse than IS, as expected)")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
