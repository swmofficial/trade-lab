"""
Permutation noise null for the walk-forward result.

The question Brick 2c left open: is +5.03% OOS a real mean-reversion edge, or just what
"optimize best-of-9, then test" extracts from ANY series with EUR/USD's return
distribution? We answer it by running the ENTIRE EXISTING walk-forward (unchanged) on
many memoryless surrogates of the data and seeing where the real result falls.

THE NULL (IID daily log-return permutation):
  Take the same weekend/stale-filtered daily CLOSE series 2c used. Permute its
  close-to-close LOG returns and rebuild a price path: close[0] * exp(cumsum(perm)).
  Bars are O=H=L=C=close (the strategy is close-only, so OHLC are irrelevant). Run the
  full walk-forward on each surrogate and record the stitched OOS total return.

  A permutation keeps EUR/USD's EXACT return distribution (same multiset of daily moves)
  but destroys serial dependence — precisely what mean reversion exploits. Running the
  whole 9-combo optimize+freeze+test pipeline on each surrogate also absorbs the
  multiple-testing inflation automatically: the null sees the same best-of-9 search.

KNOWN LIMITATION (printed by the runner, not fixed here): permutation also destroys
volatility clustering, so the null removes slightly MORE structure than pure serial
correlation. The null is therefore a touch generous to the strategy.

This module ADDS a layer; it does NOT modify bs_detector, strategy, engine, costs, or
walkforward. It calls walkforward.walk_forward unchanged.
"""

import math
import random

import data
import walkforward as wf


# --- Surrogate construction -------------------------------------------------------

def log_returns(closes):
    """Close-to-close log returns; length len(closes)-1."""
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def reconstruct_closes(close0, perm_returns):
    """Rebuild a price path from a starting close and an ordered list of log returns:
    close[i] = close0 * exp(cumsum(perm_returns)[:i]). Identity returns reproduce the
    original closes to float precision."""
    out = [close0]
    cum = 0.0
    for r in perm_returns:
        cum += r
        out.append(close0 * math.exp(cum))
    return out


def identity_perm(bars):
    """The do-nothing permutation: indices in natural order."""
    return list(range(len(bars) - 1))


def permuted_bars(bars, perm):
    """
    Build surrogate bars by reordering the log returns of `bars` per `perm` (a
    permutation of range(len(bars)-1)) and reconstructing closes. Dates are preserved
    exactly, so the fold scheme is identical to the real run. O=H=L=C=close.
    """
    closes = [b.close for b in bars]
    rets = log_returns(closes)
    if len(perm) != len(rets):
        raise ValueError("perm length must equal number of returns")
    perm_rets = [rets[i] for i in perm]
    new_closes = reconstruct_closes(closes[0], perm_rets)
    return [data.Bar(b.date, c, c, c, c, 0.0) for b, c in zip(bars, new_closes)]


# --- Running the existing walk-forward on a path ----------------------------------

def walk_forward_oos(bars, **wf_kwargs):
    """Run the unchanged walk-forward and return (stitched OOS total return %, WFResult).
    With default kwargs on the real bars this IS the Brick 2c number."""
    res = wf.walk_forward(bars, **wf_kwargs)
    oos = (res.final_equity - res.starting_equity) / res.starting_equity * 100.0
    return oos, res


def null_distribution(bars, n, seed, **wf_kwargs):
    """
    N seeded IID-log-return permutations -> N stitched OOS total returns. One RNG seeded
    once, so the whole distribution is reproducible from (seed, n, bars). Same seed ->
    identical distribution.
    """
    rng = random.Random(seed)
    base = list(range(len(bars) - 1))
    out = []
    for _ in range(n):
        perm = base[:]
        rng.shuffle(perm)
        oos, _ = walk_forward_oos(permuted_bars(bars, perm), **wf_kwargs)
        out.append(oos)
    return out


# --- Null statistics --------------------------------------------------------------

def percentile_of(value, dist):
    """Fraction of dist strictly below value, in [0,1]. (Real's standing in the null.)"""
    if not dist:
        return float("nan")
    return sum(1 for x in dist if x < value) / len(dist)


def empirical_p_value(real, dist):
    """One-sided p = (#{null >= real} + 1) / (N + 1). The +1/+1 is the standard
    permutation-test guard against p=0."""
    n = len(dist)
    ge = sum(1 for x in dist if x >= real)
    return (ge + 1) / (n + 1)


def percentile(dist, q):
    """q in [0,1]; nearest-rank percentile of a copy-sorted distribution."""
    if not dist:
        return float("nan")
    s = sorted(dist)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


# --- Secondary, weaker test: trade-return bootstrap CI ----------------------------

def bootstrap_mean_ci(values, n, seed, alpha=0.05):
    """
    95% CI (default) on the MEAN of `values` by resampling with replacement. Weaker than
    the permutation null: it takes the trades AS GIVEN and corrects neither for the
    best-of-9 search nor for serial structure. Returns (lo, hi, point_mean).
    """
    if not values:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    m = len(values)
    means = []
    for _ in range(n):
        total = 0.0
        for _ in range(m):
            total += values[rng.randrange(m)]
        means.append(total / m)
    means.sort()
    lo = means[int((alpha / 2) * n)]
    hi = means[int((1 - alpha / 2) * n)]
    return lo, hi, sum(values) / m


def oos_trade_net_returns(wf_result):
    """The net-% returns of every stitched OOS trade, in time order."""
    return [
        t.net_return_pct
        for fr in wf_result.fold_results
        for t in fr.test_result.trades
    ]
