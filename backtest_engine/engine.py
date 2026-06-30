"""
The backtest loop: walk bars in order, turn signals into trades and an equity curve.

Scope of THIS brick (deliberately minimal — see run.py header):
  - One position at a time, full in / full out. No sizing, no shorting, no stops.
  - No transaction costs, no slippage. A trade's return is the pure price ratio.

FILL ASSUMPTION (a simplification — real fills differ):
  Both entry and exit execute at the CLOSE of the bar whose signal fired. In reality
  you cannot trade at a close you only know once the bar is complete; modelling that
  honestly (next-bar open, costs, slippage) is a later brick. Stated, not hidden.

EQUITY CURVE: realized-only. Equity steps at trade CLOSES; while a position is open
the curve is flat (no mark-to-market). Drawdown is therefore peak-to-trough of
realized equity. Mark-to-market equity is a later-brick refinement.
"""

from collections import namedtuple

Trade = namedtuple(
    "Trade", "entry_date entry_price exit_date exit_price return_pct"
)
OpenPosition = namedtuple("OpenPosition", "entry_date entry_price")
BacktestResult = namedtuple(
    "BacktestResult",
    "trades open_position equity_curve starting_equity final_equity",
)


def run_backtest(bars, signals, starting_equity=10000.0):
    """
    Execute `signals` against `bars` (must be equal length, same order).

    Returns a BacktestResult:
      trades         list of closed Trade(entry/exit date+price, return_pct)
      open_position  OpenPosition if a trade is still open at the end, else None
                     (an unreverted position is reported, never silently dropped)
      equity_curve   list of (date, equity) — one point per bar
      starting_equity / final_equity
    """
    if len(bars) != len(signals):
        raise ValueError("bars and signals must be the same length")

    equity = starting_equity
    equity_curve = []
    trades = []

    in_position = False
    entry_date = None
    entry_price = None

    for bar, sig in zip(bars, signals):
        if not in_position and sig == "enter_long":
            in_position = True
            entry_date = bar.date
            entry_price = bar.close
        elif in_position and sig == "exit":
            exit_price = bar.close
            ret_frac = (exit_price - entry_price) / entry_price
            equity *= 1.0 + ret_frac
            trades.append(
                Trade(entry_date, entry_price, bar.date, exit_price, ret_frac * 100.0)
            )
            in_position = False
            entry_date = None
            entry_price = None

        equity_curve.append((bar.date, equity))

    open_position = (
        OpenPosition(entry_date, entry_price) if in_position else None
    )
    return BacktestResult(
        trades, open_position, equity_curve, starting_equity, equity
    )
