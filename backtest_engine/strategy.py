"""
Mean-reversion signal generator. PURE — no I/O, no DB, no globals.

The cardinal sin of backtesting is lookahead: letting a bar influence its own signal.
We guard against it structurally. The band at bar i is computed from the `lookback`
closes STRICTLY PRIOR to i (bars[i-lookback:i]) — bar i is never in its own window.

The signal is position-aware: it only proposes an entry when flat and an exit when
in a position. The engine mirrors this same state machine, so the two stay in lockstep
while remaining independently testable. This function is deterministic given the bars.
"""


def _mean(xs):
    return sum(xs) / len(xs)


def _pop_std(xs):
    """Population standard deviation (divide by N, not N-1). Fixed choice, stated up
    front: the window is the whole population of the lookback, not a sample of it."""
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / len(xs)
    return var ** 0.5


def mean_reversion_signals(bars, lookback=20, k=2.0):
    """
    Return a list of signals, one per bar, aligned 1:1 with `bars`.

    For each bar i >= lookback:
      band  = SMA(prior lookback closes) - k * popstd(prior lookback closes)
      enter_long  if flat        and close <  SMA - k*std   (a dip below the band)
      exit        if in position  and close >= SMA           (reverted to the mean)
      hold        otherwise
    Bars before index `lookback` cannot form a band and are always 'hold'.
    """
    signals = []
    in_position = False
    for i, bar in enumerate(bars):
        if i < lookback:
            signals.append("hold")
            continue

        window = [b.close for b in bars[i - lookback:i]]  # STRICTLY prior — never i
        sma = _mean(window)
        lower_band = sma - k * _pop_std(window)
        close = bar.close

        if not in_position:
            if close < lower_band:
                signals.append("enter_long")
                in_position = True
            else:
                signals.append("hold")
        else:
            if close >= sma:
                signals.append("exit")
                in_position = False
            else:
                signals.append("hold")
    return signals
