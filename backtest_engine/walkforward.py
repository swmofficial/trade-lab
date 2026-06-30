"""
Walk-forward out-of-sample harness. Optimizes params on TRAIN windows only, freezes
them, and measures on UNSEEN TEST windows. The stitched test-window result is the only
number that counts.

Built BEFORE any parameter tuning, on purpose: the discipline must exist before the
temptation. A leaky walk-forward is WORSE than none — it launders an in-sample fit as
"validation". The gate (tests/test_engine.py) exists to prove no leakage.

LEAKAGE DEFENCE — how each window is run (run_window):
  A window is run on its own bars only. For the band it may read the `lookback` closes
  immediately PRIOR to the window start — that is legitimate history, not lookahead.
  We supply exactly `lookback` warmup bars, so in the warmed sequence indices
  0..lookback-1 are the warmup (always 'hold', i < lookback) and index `lookback` is
  the FIRST window bar — its band is computed from the warmup closes and the position
  is GUARANTEED flat there. No warmup-region trade can carry into the window, and no
  bar ever influences its own (or an earlier) band. Selection (optimize_on_train) never
  touches a single test bar, so a test price can change the measured outcome but never
  the chosen params.

This brick does NOT modify bs_detector, strategy.py's signals, or the cost model.
"""

from collections import namedtuple

from strategy import mean_reversion_signals
from engine import run_backtest

# --- Fixed scheme (decided before any result; do NOT tune to improve output) ------
PAIR = "EUR/USD"
SPREAD_PIPS = 2          # realistic close-fill number from Brick 2b
STARTING_EQUITY = 10000.0

TRAIN_YEARS = 5
TEST_YEARS = 2
STEP_YEARS = 2           # -> non-overlapping, contiguous test windows
MIN_TEST_YEARS = 1.0     # short final fold kept only if test >= this, else dropped

# Crude grid on purpose: 3 lookbacks x 3 k = 9 combos.
LOOKBACKS = (10, 20, 30)
KS = (1.5, 2.0, 2.5)
GRID = [(lb, k) for lb in LOOKBACKS for k in KS]

Fold = namedtuple("Fold", "train_start train_end test_start test_end truncated")
FoldResult = namedtuple(
    "FoldResult", "fold params train_return test_result test_return"
)
WFResult = namedtuple(
    "WFResult",
    "fold_results folds dropped stitched_curve starting_equity final_equity",
)


# --- Date helpers -----------------------------------------------------------------

def add_years(d, n):
    """d shifted by n calendar years; Feb 29 -> Feb 28 on non-leap targets."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def _years_between(a, b):
    return (b - a).days / 365.25


def slice_window(bars, start, end):
    """Bars with start <= date < end (half-open, so adjacent windows never overlap)."""
    return [b for b in bars if start <= b.date < end]


# --- One window run (the leakage-critical primitive) ------------------------------

def run_window(
    all_bars,
    win_start,
    win_end,
    lookback,
    k,
    pair=PAIR,
    spread_pips=SPREAD_PIPS,
    starting_equity=STARTING_EQUITY,
):
    """
    Run the strategy over the bars in [win_start, win_end), warming the band with the
    `lookback` closes immediately before win_start (legitimate history). The position is
    flat at the first window bar by construction (see module docstring). Trades and
    equity belong ONLY to the window.
    """
    window = slice_window(all_bars, win_start, win_end)
    history = [b for b in all_bars if b.date < win_start]
    warmup = history[-lookback:]  # exactly lookback bars when available, else all prior

    seq = warmup + window
    seq_signals = mean_reversion_signals(seq, lookback=lookback, k=k)
    window_signals = seq_signals[len(warmup):]  # drop the warmup, all 'hold' anyway

    return run_backtest(
        window, window_signals, pair,
        starting_equity=starting_equity, spread_pips=spread_pips,
    )


def net_total_return(result):
    """Net total return % of a BacktestResult (equity already compounds net)."""
    return (
        (result.final_equity - result.starting_equity)
        / result.starting_equity
        * 100.0
    )


# --- Optimization (TRAIN only) ----------------------------------------------------

def optimize_on_train(
    all_bars,
    train_start,
    train_end,
    grid=GRID,
    pair=PAIR,
    spread_pips=SPREAD_PIPS,
    starting_equity=STARTING_EQUITY,
):
    """
    Run every grid combo on the TRAIN window only and return the best by net total
    return. Deterministic tie-break: first combo in grid order wins (strict >).
    Returns (best_params, best_return, [(params, return), ...]). Reads NO test bars.
    """
    best_params, best_return = None, None
    scores = []
    for (lb, k) in grid:
        res = run_window(
            all_bars, train_start, train_end, lb, k, pair, spread_pips, starting_equity
        )
        r = net_total_return(res)
        scores.append(((lb, k), r))
        if best_return is None or r > best_return:
            best_params, best_return = (lb, k), r
    return best_params, best_return, scores


def run_fold(
    all_bars,
    fold,
    grid=GRID,
    pair=PAIR,
    spread_pips=SPREAD_PIPS,
    starting_equity=STARTING_EQUITY,
):
    """Optimize on the fold's TRAIN, freeze, then apply ONLY those params to TEST."""
    params, train_return, _ = optimize_on_train(
        all_bars, fold.train_start, fold.train_end, grid, pair, spread_pips, starting_equity
    )
    lb, k = params
    test_result = run_window(
        all_bars, fold.test_start, fold.test_end, lb, k, pair, spread_pips, starting_equity
    )
    return FoldResult(fold, params, train_return, test_result, net_total_return(test_result))


# --- Fold layout ------------------------------------------------------------------

def make_folds(
    bars,
    train_years=TRAIN_YEARS,
    test_years=TEST_YEARS,
    step_years=STEP_YEARS,
    min_test_years=MIN_TEST_YEARS,
):
    """
    Rolling folds: each fold's TRAIN is the `train_years` immediately before its TEST.
    Test windows are contiguous and non-overlapping (step == test). A short final fold
    is kept only if its available test span >= min_test_years, else dropped.
    Returns (folds, dropped).
    """
    folds, dropped = [], []
    if not bars:
        return folds, dropped

    t0 = bars[0].date
    data_end = bars[-1].date

    i = 0
    while True:
        train_start = add_years(t0, step_years * i)
        train_end = add_years(t0, step_years * i + train_years)
        test_start = train_end
        test_end = add_years(t0, step_years * i + train_years + test_years)

        if train_end > data_end or test_start > data_end:
            break  # no room for a full train window or any test data

        truncated = test_end > data_end
        avail_test_years = _years_between(test_start, min(test_end, data_end))
        fold = Fold(train_start, train_end, test_start, test_end, truncated)

        if truncated and avail_test_years < min_test_years:
            dropped.append(fold)
            break

        folds.append(fold)
        if truncated:
            break  # data exhausted; this was the last fold
        i += 1

    return folds, dropped


# --- Full walk-forward ------------------------------------------------------------

def walk_forward(
    bars,
    grid=GRID,
    pair=PAIR,
    spread_pips=SPREAD_PIPS,
    starting_equity=STARTING_EQUITY,
    train_years=TRAIN_YEARS,
    test_years=TEST_YEARS,
    step_years=STEP_YEARS,
    min_test_years=MIN_TEST_YEARS,
):
    """Run the whole walk-forward and stitch the out-of-sample (test) equity curve."""
    folds, dropped = make_folds(bars, train_years, test_years, step_years, min_test_years)
    fold_results = [
        run_fold(bars, f, grid, pair, spread_pips, starting_equity) for f in folds
    ]

    # Stitch test windows in time order: chain each fold's curve onto the running equity.
    stitched = []
    running = starting_equity
    for fr in fold_results:
        factor = running / starting_equity
        for (d, eq) in fr.test_result.equity_curve:
            stitched.append((d, eq * factor))
        running *= fr.test_result.final_equity / starting_equity

    return WFResult(fold_results, folds, dropped, stitched, starting_equity, running)
