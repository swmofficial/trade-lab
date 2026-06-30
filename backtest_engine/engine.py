"""
The backtest loop: walk bars in order, turn signals into trades and an equity curve.

Scope of THIS brick (deliberately minimal — see run.py header):
  - One position at a time, full in / full out. No sizing, no shorting, no stops.
  - Spread cost only (Brick 2b). No slippage beyond spread. A trade's GROSS return is
    the pure price ratio; its NET return has the round-trip spread debited.

FILL ASSUMPTION (a simplification — real fills differ):
  Both entry and exit execute at the CLOSE of the bar whose signal fired. In reality
  you cannot trade at a close you only know once the bar is complete; modelling that
  honestly (next-bar open, slippage) is a later brick. Stated, not hidden.

EQUITY CURVE: realized-only, compounding NET returns. Equity steps at trade CLOSES;
while a position is open the curve is flat (no mark-to-market). Drawdown is therefore
peak-to-trough of realized NET equity. Mark-to-market equity is a later-brick refinement.
"""

from collections import namedtuple

import costs

Trade = namedtuple(
    "Trade",
    "entry_date entry_price exit_date exit_price gross_return_pct net_return_pct",
)
OpenPosition = namedtuple("OpenPosition", "entry_date entry_price")
BacktestResult = namedtuple(
    "BacktestResult",
    "trades open_position equity_curve starting_equity final_equity",
)


def run_backtest(
    bars, signals, pair, starting_equity=10000.0, spread_pips=costs.SPREAD_PIPS
):
    """
    Execute `signals` against `bars` (must be equal length, same order).

    `pair` is required because the spread cost depends on the pair's pip size.
    `spread_pips` is the round-trip spread charged per completed trade; spread_pips=0
    turns costs off and reproduces gross (Brick 2a) numbers exactly.

    Returns a BacktestResult:
      trades         list of closed Trade(entry/exit date+price, gross_return_pct,
                     net_return_pct). Gross is retained so we can see what costs ate.
      open_position  OpenPosition if a trade is still open at the end, else None
                     (an unreverted position is reported, never silently dropped;
                     open positions are not costed until they close)
      equity_curve   list of (date, equity) — one point per bar, compounding NET
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
            gross_frac = (exit_price - entry_price) / entry_price
            cost_frac = costs.round_trip_cost_fraction(pair, entry_price, spread_pips)
            net_frac = gross_frac - cost_frac
            equity *= 1.0 + net_frac  # equity compounds NET of costs
            trades.append(
                Trade(
                    entry_date,
                    entry_price,
                    bar.date,
                    exit_price,
                    gross_frac * 100.0,
                    net_frac * 100.0,
                )
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
