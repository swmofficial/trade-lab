"""
Brick 2d runner. Permutation noise null for the EUR/USD walk-forward.

Runs the ENTIRE existing walk-forward (2c, unchanged) on N memoryless surrogates of the
real series, building a null distribution for the stitched OOS return. real-vs-null is
the edge test; running the full 9-combo optimize+freeze+test on each surrogate also
absorbs the multiple-testing inflation automatically.

Guardrails FIXED IN ADVANCE (not tunable to move the verdict):
  N = 1000, RNG seed = 1234, one-sided, p computed ONCE.
  Pre-set bar: real must beat the null's 95th percentile (p <= 0.05) to beat noise.
  Cost = 2 pips throughout.

A labeled N=50 SMOKE TEST runs first for runtime/correctness; the reported verdict uses
the N=1000 run only.

LIMITATIONS (printed): the permutation null also destroys volatility clustering, so it
removes slightly MORE structure than pure serial correlation (a touch generous to the
strategy). Close-to-close fills; no slippage beyond spread; no sizing/stops; 9-combo grid.
"""

import os
import subprocess
import sys
import time

import data
import noise_null as nn
import walkforward as wf

SEED = 1234
N_SMOKE = 50
N_FULL = 1000
PAIR = "EUR/USD"


def run_gate():
    print("=== GATE: engine + cost + walk-forward + noise-null tests ===")
    here = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(here, "tests", "test_engine.py")
    result = subprocess.run([sys.executable, test_path])
    if result.returncode != 0:
        print("GATE FAILED -- aborting before touching real data.")
        sys.exit(1)
    print()


def summarize(label, real, null, elapsed):
    p5, p50, p95 = (nn.percentile(null, q) for q in (0.05, 0.50, 0.95))
    mean = sum(null) / len(null)
    pct = nn.percentile_of(real, null) * 100.0
    pval = nn.empirical_p_value(real, null)
    print(f"--- {label} (N={len(null)}, seed={SEED}, {elapsed:.1f}s) ---")
    print(f"  real OOS            : {real:+.2f}%")
    print(f"  null mean           : {mean:+.2f}%")
    print(f"  null 5th / 50th / 95th : {p5:+.2f}% / {p50:+.2f}% / {p95:+.2f}%")
    print(f"  real percentile     : {pct:.1f}th")
    print(f"  empirical p-value   : {pval:.4f}")
    return pval, p95


def main():
    run_gate()

    print("=" * 78)
    print("PERMUTATION NOISE NULL -- real OOS vs IID-log-return-shuffle null.")
    print("Null also removes volatility clustering (a touch generous). 9-combo grid;")
    print("close-to-close fills; no slippage beyond spread; no sizing/stops.")
    print("=" * 78)

    bars, excluded = data.load_bars(PAIR)
    real, wf_result = nn.walk_forward_oos(bars)  # default scheme == Brick 2c
    print(f"\n{PAIR} daily: {len(bars)} bars ({excluded} excluded), "
          f"{bars[0].date} -> {bars[-1].date}")
    print(f"real (Brick 2c) stitched OOS total return = {real:+.2f}% @ 2 pips\n")

    # --- SMOKE (runtime/correctness only) ---
    t0 = time.perf_counter()
    smoke = nn.null_distribution(bars, N_SMOKE, SEED)
    summarize("SMOKE TEST", real, smoke, time.perf_counter() - t0)
    per_iter = (time.perf_counter() - t0) / N_SMOKE
    print(f"  (~{per_iter*1000:.0f} ms/iteration -> N={N_FULL} ~= "
          f"{per_iter*N_FULL:.0f}s)\n")

    # --- FULL null (the reported verdict) ---
    t0 = time.perf_counter()
    null = nn.null_distribution(bars, N_FULL, SEED)
    full_elapsed = time.perf_counter() - t0
    pval, p95 = summarize("FULL NULL", real, null, full_elapsed)

    beats = real > p95
    print(f"\n  VERDICT (pre-set bar: beat null 95th pct, p<=0.05): "
          f"{'BEATS NOISE' if (beats and pval <= 0.05) else 'DOES NOT BEAT NOISE'}")
    if not (beats and pval <= 0.05):
        print("  -> the +5% OOS is consistent with what best-of-9 extracts from a series")
        print("     with EUR/USD's return distribution but no serial structure.")

    # --- SECONDARY (weaker) bootstrap CI on trade net returns ---
    trade_rets = nn.oos_trade_net_returns(wf_result)
    lo, hi, mean_ret = nn.bootstrap_mean_ci(trade_rets, 1000, SEED)
    print("\n" + "-" * 78)
    print("SECONDARY (WEAKER) -- bootstrap 95% CI on mean OOS trade net return.")
    print("Takes the trades AS GIVEN; corrects for neither the best-of-9 search nor")
    print("serial structure. Strictly weaker than the permutation null above.")
    print(f"  OOS trades          : {len(trade_rets)}")
    print(f"  mean net return     : {mean_ret:+.3f}%")
    print(f"  95% CI (1000x boot) : [{lo:+.3f}%, {hi:+.3f}%]   "
          f"{'(excludes 0)' if lo > 0 or hi < 0 else '(includes 0)'}")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
