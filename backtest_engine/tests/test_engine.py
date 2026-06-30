"""
THE GATE for the backtest engine. NO real data, NO DB. Synthetic price series with
hand-computed outcomes; the engine is only trusted once it reproduces the arithmetic.

The lookahead-guard test is the most important one: it is constructed so that an engine
which (wrongly) let bar i into its own band would produce a DIFFERENT signal. If the
guard ever regresses, that test goes red.

Run directly:  python tests/test_engine.py   (prints PASS/FAIL per case, exits nonzero
on any failure). Also importable by pytest (test_* functions).
"""

import os
import sys
from collections import namedtuple
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy import mean_reversion_signals
from engine import run_backtest
import costs
import walkforward as wf

# Minimal bar for tests — the engine/strategy only read .date and .close.
TBar = namedtuple("TBar", "date open high low close volume")


def make_bars(closes, start="2024-01-01"):
    """Build bars from a list of closes, one per consecutive day. Dates are only used
    for trade records, not signal logic, so plain consecutive days are fine."""
    y, m, d = (int(x) for x in start.split("-"))
    d0 = date(y, m, d)
    return [
        TBar(d0 + timedelta(days=i), c, c, c, c, 0.0) for i, c in enumerate(closes)
    ]


APPROX = 1e-9


def approx(a, b, tol=APPROX):
    return abs(a - b) <= tol


# --- Case 1: one dip below the band, one reversion -> exactly 1 trade -------------
#
# lookback=3, k=2.0. Closes: [10, 12, 11, 9, 11, 11, 11, 11]
#   i=3 window=[10,12,11] -> SMA=11, popstd=sqrt(2/3)=0.81650, lower=11-2*0.81650=9.36700
#        close 9.0 < 9.36700  -> ENTER at 9.0
#   i=4 window=[12,11,9]  -> SMA=10.66667; close 11.0 >= SMA -> EXIT at 11.0
#   return = (11-9)/9 = 0.222222... -> 22.2222%; remaining bars never re-breach.
def case_one_trade():
    # spread_pips=0 -> costs off -> reproduces Brick 2a's GROSS numbers exactly.
    bars = make_bars([10, 12, 11, 9, 11, 11, 11, 11])
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)
    res = run_backtest(bars, sigs, "EUR/USD", starting_equity=10000.0, spread_pips=0)

    assert len(res.trades) == 1, f"expected 1 trade, got {len(res.trades)}"
    t = res.trades[0]
    assert approx(t.entry_price, 9.0), t.entry_price
    assert approx(t.exit_price, 11.0), t.exit_price
    assert approx(t.gross_return_pct, (11 - 9) / 9 * 100), t.gross_return_pct
    # costs off: net must equal gross to the bit.
    assert approx(t.net_return_pct, t.gross_return_pct), t.net_return_pct
    assert res.open_position is None, "should be flat at end"
    # equity = 10000 * (1 + 2/9) — identical to Brick 2a.
    assert approx(res.final_equity, 10000.0 * (1 + 2 / 9)), res.final_equity
    return True


# --- Case 2: clean rising series, band never breached -> 0 trades -----------------
def case_no_trade():
    bars = make_bars([10, 11, 12, 13, 14, 15, 16, 17])  # monotonic up, never dips
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)
    res = run_backtest(bars, sigs, "EUR/USD", starting_equity=10000.0, spread_pips=0)

    assert sigs.count("enter_long") == 0, sigs
    assert len(res.trades) == 0, res.trades
    assert res.open_position is None
    assert approx(res.final_equity, 10000.0), res.final_equity
    return True


# --- Case 3: enters but never reverts -> position stays OPEN, reported -------------
#
# Closes keep falling after entry, so close never returns >= SMA. The open position
# must be reported, not silently dropped, and no closed trade should exist.
def case_open_position():
    bars = make_bars([10, 12, 11, 9, 8, 7, 6])
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)
    res = run_backtest(bars, sigs, "EUR/USD", starting_equity=10000.0, spread_pips=0)

    assert len(res.trades) == 0, f"no trade should close, got {res.trades}"
    assert res.open_position is not None, "open position must be reported"
    assert approx(res.open_position.entry_price, 9.0), res.open_position.entry_price
    # equity is realized-only, so an unclosed position leaves equity untouched.
    assert approx(res.final_equity, 10000.0), res.final_equity
    return True


# --- Case 4: LOOKAHEAD GUARD (the important one) ----------------------------------
#
# lookback=3, k=2.0. Closes: [10.0, 10.1, 10.0, 9.0]
#   CORRECT (exclusive) band at i=3 uses [10.0,10.1,10.0]:
#        SMA=10.03333, popstd=0.04714, lower=10.03333-2*0.04714=9.93905
#        close 9.0 < 9.93905  -> ENTER  (signal 'enter_long')
#   WRONG (inclusive) band would use [10.1,10.0,9.0]:
#        SMA=9.70000, popstd=0.49666, lower=9.70000-2*0.49666=8.70669
#        close 9.0 >= 8.70669 -> would HOLD (no entry)
#   So observing an entry at i=3 PROVES bar i was excluded from its own band.
def case_lookahead_guard():
    bars = make_bars([10.0, 10.1, 10.0, 9.0])
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)

    assert sigs[3] == "enter_long", (
        "lookahead leak: bar 3 did not enter, meaning its own close was used in its "
        f"band. signals={sigs}"
    )
    # And confirm the wrong (inclusive) band really would NOT have entered, so the
    # test has teeth rather than passing trivially.
    incl = [10.1, 10.0, 9.0]
    m = sum(incl) / 3
    std = (sum((x - m) ** 2 for x in incl) / 3) ** 0.5
    assert 9.0 >= m - 2.0 * std, "inclusive band would also enter; test lacks teeth"
    return True


# --- Case 5: pip size is per-pair, not a constant ---------------------------------
def case_pip_size():
    assert costs.pip_size("USD/JPY") == 0.01, costs.pip_size("USD/JPY")
    assert costs.pip_size("EUR/USD") == 0.0001, costs.pip_size("EUR/USD")
    # GBP/USD is a non-yen major -> 0.0001 like EUR/USD.
    assert costs.pip_size("GBP/USD") == 0.0001, costs.pip_size("GBP/USD")
    return True


# --- Case 6: cost moves net by EXACTLY the hand-computed amount --------------------
#
# Reuse the hand-checked one-trade case (entry 9.0, exit 11.0, gross +22.2222%).
# spread_pips=2.0, EUR/USD pip=0.0001:
#   cost_fraction = (2.0 * 0.0001) / 9.0 = 0.0002 / 9.0 = 0.0000222222...
#   net_pct = gross_pct - cost_fraction*100 = 22.2222% - 0.00222222...%
# The shift must be EXACT, not approximate.
def case_cost_is_exact():
    bars = make_bars([10, 12, 11, 9, 11, 11, 11, 11])
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)

    spread = 2.0
    res = run_backtest(bars, sigs, "EUR/USD", starting_equity=10000.0, spread_pips=spread)
    assert len(res.trades) == 1, res.trades
    t = res.trades[0]

    hand_cost_pct = (spread * 0.0001 / 9.0) * 100.0
    assert approx(t.gross_return_pct, (11 - 9) / 9 * 100), t.gross_return_pct
    assert approx(t.net_return_pct, t.gross_return_pct - hand_cost_pct), (
        t.net_return_pct,
        t.gross_return_pct - hand_cost_pct,
    )
    # And the cost actually bit (sanity: net strictly below gross).
    assert t.net_return_pct < t.gross_return_pct
    # equity compounds NET, not gross.
    assert approx(res.final_equity, 10000.0 * (1 + t.net_return_pct / 100.0))
    return True


# --- Case 7: spread_pips=0 reproduces Brick 2a gross EXACTLY -----------------------
#
# Across a non-trivial multi-trade synthetic series, costs-off net must equal gross
# trade-by-trade, and final equity must equal compounding the gross returns. Proves
# the cost layer is the ONLY thing changed.
def case_costs_off_equals_2a():
    bars = make_bars([10, 12, 11, 9, 11, 8, 11, 7, 11, 11])
    sigs = mean_reversion_signals(bars, lookback=3, k=2.0)
    res = run_backtest(bars, sigs, "EUR/USD", starting_equity=10000.0, spread_pips=0)

    assert len(res.trades) >= 2, "want multiple trades to make this meaningful"
    eq = 10000.0
    for t in res.trades:
        assert approx(t.net_return_pct, t.gross_return_pct), t  # costs off
        eq *= 1 + t.gross_return_pct / 100.0
    assert approx(res.final_equity, eq), (res.final_equity, eq)
    return True


# ==================================================================================
# WALK-FORWARD LEAKAGE GATE (Brick 2c). The most important gate in the project so far.
# ==================================================================================

# A two-combo grid: A is narrow-band (enters on small dips), B is wide-band (enters
# only on deep dips). lookback=2 keeps every band hand-computable.
A = (2, 1.0)
B = (2, 3.0)
GRID_AB = [A, B]

# A single consecutive-day series, partitioned into history / train / test by date.
#   01-01,01-02 : history (warmup for train)
#   01-03..01-06: TRAIN  -> A takes a +4% trade, B never enters  => TRAIN favors A
#   01-07..01-11: TEST   -> A enters shallow @10.18, B enters deep @10.05, both exit
#                           @10.30 => B (deeper entry) beats A   => TEST favors B
# So train-optimal (A) != test-optimal (B). An honest harness must FREEZE A onto test.
FREEZE_CLOSES = [10.0, 10.4, 9.9, 10.3, 10.2, 10.25, 10.18, 10.05, 10.30, 10.28, 10.30]
TRAIN_START = date(2024, 1, 3)
TRAIN_END = date(2024, 1, 7)   # exclusive -> train bars 01-03..01-06
TEST_START = date(2024, 1, 7)
TEST_END = date(2024, 1, 12)   # exclusive -> test bars 01-07..01-11


def _freeze_bars(closes=FREEZE_CLOSES):
    return make_bars(closes, start="2024-01-01")


# --- Case 8: PARAM FREEZE (the one that proves the harness is honest) --------------
def case_param_freeze():
    bars = _freeze_bars()

    # Train picks A.
    chosen, _train_ret, _ = wf.optimize_on_train(
        bars, TRAIN_START, TRAIN_END, grid=GRID_AB, spread_pips=0
    )
    assert chosen == A, f"train should favor A={A}, picked {chosen}"

    # TEETH: B must genuinely be test-optimal, else the test proves nothing.
    a_test = wf.net_total_return(
        wf.run_window(bars, TEST_START, TEST_END, A[0], A[1], spread_pips=0)
    )
    b_test = wf.net_total_return(
        wf.run_window(bars, TEST_START, TEST_END, B[0], B[1], spread_pips=0)
    )
    assert b_test > a_test, (
        f"test lacks teeth: B not test-optimal (a={a_test}, b={b_test})"
    )

    # The harness must use the TRAIN-chosen A on test, NOT the test-optimal B.
    fold = wf.Fold(TRAIN_START, TRAIN_END, TEST_START, TEST_END, False)
    fr = wf.run_fold(bars, fold, grid=GRID_AB, spread_pips=0)
    assert fr.params == A, f"harness leaked: used {fr.params}, must freeze {A}"
    assert approx(fr.test_return, a_test), (fr.test_return, a_test)
    assert not approx(fr.test_return, b_test), "harness produced test-optimal return!"
    return True


# --- Case 9: NO OUTCOME BLEED — test prices cannot change param selection ----------
def case_no_outcome_bleed():
    bars = _freeze_bars()
    chosen1, _, _ = wf.optimize_on_train(bars, TRAIN_START, TRAIN_END, grid=GRID_AB)

    # Rewrite ONLY the test-period closes (indices 6..10) to wildly different values.
    mutated = list(FREEZE_CLOSES)
    mutated[6:] = [5.0, 20.0, 3.0, 18.0, 4.0]
    bars2 = _freeze_bars(mutated)

    chosen2, _, _ = wf.optimize_on_train(bars2, TRAIN_START, TRAIN_END, grid=GRID_AB)
    assert chosen1 == chosen2, (
        f"LEAK: test prices changed the chosen params {chosen1} -> {chosen2}"
    )

    # run_fold on both must freeze the SAME params, but the measured test return DOES
    # change with test prices (selection frozen, outcome measured).
    fold = wf.Fold(TRAIN_START, TRAIN_END, TEST_START, TEST_END, False)
    fr1 = wf.run_fold(bars, fold, grid=GRID_AB)
    fr2 = wf.run_fold(bars2, fold, grid=GRID_AB)
    assert fr1.params == fr2.params == chosen1
    assert not approx(fr1.test_return, fr2.test_return), (
        "test prices changed but measured outcome did not — window not reading test data"
    )
    return True


# --- Case 10: BOUNDARY history OK, boundary lookahead NOT --------------------------
#
# History [10.0, 10.1, 10.0] makes a tight band; the FIRST test bar (9.0) dips below it
# and must ENTER -> proves prior history feeds the band (not wasted as 'hold'). The
# WRONG inclusive band [10.1,10.0,9.0] would NOT enter -> proves the current bar is
# excluded from its own band at the window boundary.
def case_boundary_history_not_lookahead():
    closes = [10.0, 10.1, 10.0, 9.0, 11.0, 11.0]
    bars = make_bars(closes, start="2024-02-01")
    tstart = date(2024, 2, 4)  # first test bar = the 9.0 dip
    tend = date(2024, 2, 7)

    res = wf.run_window(bars, tstart, tend, lookback=3, k=2.0, spread_pips=0)
    assert len(res.trades) >= 1, "history not used: first test bar produced no band/trade"
    assert res.trades[0].entry_date == tstart, (
        f"entry should be at the window's first bar (history-warmed band), "
        f"got {res.trades[0].entry_date}"
    )

    # TEETH: the inclusive (lookahead) band would NOT have entered.
    incl = [10.1, 10.0, 9.0]
    m = sum(incl) / 3
    std = (sum((x - m) ** 2 for x in incl) / 3) ** 0.5
    assert 9.0 >= m - 2.0 * std, "inclusive band would also enter; test lacks teeth"
    return True


# --- Case 11: NON-OVERLAP — no calendar bar in two TEST windows --------------------
def case_non_overlap():
    # Monthly bars over 16 years -> several full folds + a short final one (dropped).
    bars = []
    for y in range(2000, 2016):
        for mth in range(1, 13):
            bars.append(TBar(date(y, mth, 1), 10.0, 10.0, 10.0, 10.0, 0.0))

    folds, dropped = wf.make_folds(bars)
    assert len(folds) >= 2, f"expected several folds, got {len(folds)}"

    seen = {}
    for idx, f in enumerate(folds):
        for b in wf.slice_window(bars, f.test_start, f.test_end):
            assert b.date not in seen, (
                f"bar {b.date} appears in test windows {seen[b.date]} and {idx}"
            )
            seen[b.date] = idx
    return True


CASES = [
    ("one dip+revert -> exactly 1 trade, hand-checked", case_one_trade),
    ("clean rising series -> 0 trades", case_no_trade),
    ("enter, never revert -> position stays OPEN", case_open_position),
    ("LOOKAHEAD GUARD: band at i excludes bar i", case_lookahead_guard),
    ("pip size per-pair (JPY 0.01, others 0.0001)", case_pip_size),
    ("cost shifts net by EXACT hand amount", case_cost_is_exact),
    ("spread_pips=0 reproduces gross EXACTLY", case_costs_off_equals_2a),
    ("WF PARAM FREEZE: train params frozen onto test", case_param_freeze),
    ("WF no outcome bleed: test prices can't pick params", case_no_outcome_bleed),
    ("WF boundary: history OK, no lookahead", case_boundary_history_not_lookahead),
    ("WF non-overlap: no bar in two test windows", case_non_overlap),
]


# pytest entry points
def test_one_trade():
    assert case_one_trade()


def test_no_trade():
    assert case_no_trade()


def test_open_position():
    assert case_open_position()


def test_lookahead_guard():
    assert case_lookahead_guard()


def test_pip_size():
    assert case_pip_size()


def test_cost_is_exact():
    assert case_cost_is_exact()


def test_costs_off_equals_2a():
    assert case_costs_off_equals_2a()


def test_param_freeze():
    assert case_param_freeze()


def test_no_outcome_bleed():
    assert case_no_outcome_bleed()


def test_boundary_history_not_lookahead():
    assert case_boundary_history_not_lookahead()


def test_non_overlap():
    assert case_non_overlap()


def main():
    passed = 0
    for label, fn in CASES:
        try:
            fn()
            print(f"PASS  {label}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {label}\n        {e}")
    print(f"\n{passed}/{len(CASES)} gate cases passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
