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


CASES = [
    ("one dip+revert -> exactly 1 trade, hand-checked", case_one_trade),
    ("clean rising series -> 0 trades", case_no_trade),
    ("enter, never revert -> position stays OPEN", case_open_position),
    ("LOOKAHEAD GUARD: band at i excludes bar i", case_lookahead_guard),
    ("pip size per-pair (JPY 0.01, others 0.0001)", case_pip_size),
    ("cost shifts net by EXACT hand amount", case_cost_is_exact),
    ("spread_pips=0 reproduces gross EXACTLY", case_costs_off_equals_2a),
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
