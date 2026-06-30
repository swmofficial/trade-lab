"""
Summary statistics from a backtest result. Reporting only — no trading logic here.

Deliberately NOT computed yet (need more care / assumptions — later bricks):
  annualized return, Sharpe / Sortino, exposure-adjusted returns. Printing those now
  would imply a rigor this brick does not have.

A win is a strictly positive return; a zero-return trade counts as a loss (it is not
a win). avg_win / avg_loss are means over those subsets, in percent.
"""


def _max_drawdown_pct(equity_curve):
    """Largest peak-to-trough decline on the equity curve, as a positive percent.
    0.0 if the curve never falls below a prior peak."""
    peak = None
    max_dd = 0.0
    for _, equity in equity_curve:
        if peak is None or equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def compute_metrics(result):
    """Take a BacktestResult, return a dict of summary stats."""
    trades = result.trades
    n = len(trades)

    total_return_pct = (
        (result.final_equity - result.starting_equity)
        / result.starting_equity
        * 100.0
    )

    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]

    win_rate_pct = (len(wins) / n * 100.0) if n else 0.0
    avg_win_pct = (sum(t.return_pct for t in wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(t.return_pct for t in losses) / len(losses)) if losses else 0.0

    return {
        "num_trades": n,
        "total_return_pct": total_return_pct,
        "win_rate_pct": win_rate_pct,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "max_drawdown_pct": _max_drawdown_pct(result.equity_curve),
        "open_position": result.open_position is not None,
    }


def format_metrics(m):
    """Human-readable block for the runner. Honest: no Sharpe/annualized yet."""
    lines = [
        f"  trades            : {m['num_trades']}",
        f"  total return      : {m['total_return_pct']:+.2f}%",
        f"  win rate          : {m['win_rate_pct']:.1f}%",
        f"  avg win           : {m['avg_win_pct']:+.2f}%",
        f"  avg loss          : {m['avg_loss_pct']:+.2f}%",
        f"  max drawdown      : {m['max_drawdown_pct']:.2f}%",
    ]
    if m["open_position"]:
        lines.append("  NOTE              : a position is still OPEN at series end")
    return "\n".join(lines)
