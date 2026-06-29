"""
Intra-source integrity checks for daily OHLC bars.

NON-NEGOTIABLE: these functions DETECT and FLAG defects only. They never delete,
repair, interpolate, or otherwise mutate a bar. Each check is a pure function that
takes the bars for ONE pair (one source) and returns a list of flag dicts. The
runner — not the check — is responsible for persisting flags.

A "bar" is a dict: {pair, date, source, open, high, low, close, volume}.
'date' is an ISO string 'YYYY-MM-DD'. open/high/low/close are floats. volume may
be None or 0.0 (forex bars often omit it — that is NOT a defect).

A "flag" is a dict: {pair, date, source, check_name, detail}.

Thresholds are NAMED module-level constants, fixed BEFORE seeing real data. They are
deliberately not tuned to whatever the live data happens to show.
"""

from datetime import date as _date, timedelta


# --- Thresholds (fixed up front, with rationale) ---------------------------------

# Largest genuine single-day moves in EUR/USD, GBP/USD, USD/JPY (e.g. GBP/USD on the
# 2016 Brexit vote, ~8-9%) sit under 10%. A close-to-close move above this is almost
# certainly a bad tick, not a real market event.
MAX_DAILY_RETURN = 0.10

# NOTE: check_gaps has no magnitude threshold. It compares each bar against the
# EXPECTED next trading day rather than a calendar-distance cutoff — see check_gaps.


# --- Helpers ---------------------------------------------------------------------

def _parse_date(s):
    """'YYYY-MM-DD' -> datetime.date."""
    y, m, d = s.split("-")
    return _date(int(y), int(m), int(d))


def _next_trading_day(d):
    """Expected next trading day: Mon-Thu -> +1, Fri -> +3 (to Mon).
    Weekend inputs (shouldn't occur in a clean feed; check_weekend_bar flags them)
    roll forward to the next Monday so the gap logic stays well-defined."""
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd <= 3:        # Mon-Thu
        return d + timedelta(days=1)
    if wd == 4:        # Fri -> Mon
        return d + timedelta(days=3)
    if wd == 5:        # Sat -> Mon
        return d + timedelta(days=2)
    return d + timedelta(days=1)  # Sun -> Mon


def _weekdays_strictly_between(d0, d1):
    """Count Mon-Fri days strictly between d0 and d1 (both ends exclusive)."""
    n = 0
    cur = d0 + timedelta(days=1)
    while cur < d1:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def _flag(bar, check_name, detail):
    return {
        "pair": bar["pair"],
        "date": bar["date"],
        "source": bar["source"],
        "check_name": check_name,
        "detail": detail,
    }


# --- Checks ----------------------------------------------------------------------

def check_ohlc_sanity(bars):
    """
    Flag any bar where the OHLC relationships are impossible:
        NOT (low<=open<=high AND low<=close<=high AND high>=low AND all four > 0).
    """
    flags = []
    for b in bars:
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        ok = (
            l <= o <= h
            and l <= c <= h
            and h >= l
            and o > 0 and h > 0 and l > 0 and c > 0
        )
        if not ok:
            flags.append(_flag(
                b, "check_ohlc_sanity",
                f"impossible OHLC: open={o} high={h} low={l} close={c}",
            ))
    return flags


def check_absurd_jump(bars):
    """
    Flag any bar whose close-to-close return vs the previous bar exceeds
    MAX_DAILY_RETURN. Both bars involved in the jump are flagged.
    """
    flags = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        prev_close = prev["close"]
        if prev_close == 0:
            continue  # division guard; check_ohlc_sanity already flags non-positive closes
        ret = (cur["close"] - prev_close) / prev_close
        if abs(ret) > MAX_DAILY_RETURN:
            detail = (
                f"close-to-close return {ret:+.4f} exceeds +/-{MAX_DAILY_RETURN} "
                f"({prev['date']} close={prev_close} -> {cur['date']} close={cur['close']})"
            )
            flags.append(_flag(prev, "check_absurd_jump", detail))
            flags.append(_flag(cur, "check_absurd_jump", detail))
    return flags


def check_stale_bar(bars):
    """
    Two distinct staleness defects:
      - frozen feed: a bar whose O,H,L,C all equal the PREVIOUS bar's values.
      - zero-range bar: a bar where O==H==L==C (no intraday movement at all).
    A bar can trip both; each emits its own flag.
    """
    flags = []
    for i, b in enumerate(bars):
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]

        if i > 0:
            p = bars[i - 1]
            if (o == p["open"] and h == p["high"]
                    and l == p["low"] and c == p["close"]):
                flags.append(_flag(
                    b, "check_stale_bar",
                    f"frozen feed: OHLC identical to previous bar {p['date']} "
                    f"(O={o} H={h} L={l} C={c})",
                ))

        if o == h == l == c:
            flags.append(_flag(
                b, "check_stale_bar",
                f"zero-range bar: O==H==L==C=={o}",
            ))
    return flags


def check_weekend_bar(bars):
    """
    Flag any bar dated Saturday or Sunday. Forex daily bars should not land on a
    weekend; if one does, the date convention itself needs scrutiny.
    """
    flags = []
    for b in bars:
        wd = _parse_date(b["date"]).weekday()  # Mon=0 .. Sun=6
        if wd >= 5:
            name = "Saturday" if wd == 5 else "Sunday"
            flags.append(_flag(
                b, "check_weekend_bar",
                f"bar dated on a {name} ({b['date']})",
            ))
    return flags


def check_gaps(bars):
    """
    Walk consecutive bars; for each adjacent pair compare the later bar against the
    EXPECTED next trading day after the earlier one (Mon-Thu -> +1, Fri -> +3). If the
    later bar lands past the expected day, one or more expected trading days are
    missing -> CANDIDATE gap, with the count of skipped weekdays.

    This reasons about expected trading days directly rather than a calendar-distance
    cutoff, so it catches a SINGLE missing trading day (the common real defect) that a
    >4-calendar-day threshold misses. It deliberately over-flags holidays as candidates:
    at this stage we cannot tell a benign holiday from a real gap without a holiday
    calendar (a later sub-brick). The later bar carries the flag. An earlier-or-equal
    'next' date is left to check_monotonic.
    """
    flags = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        pd = _parse_date(prev["date"])
        cd = _parse_date(cur["date"])
        expected = _next_trading_day(pd)
        if cd == expected:
            continue
        if cd > expected:
            skipped = _weekdays_strictly_between(pd, cd)
            flags.append(_flag(
                cur, "check_gaps",
                f"CANDIDATE gap: {skipped} expected trading day(s) missing "
                f"between {prev['date']} and {cur['date']}",
            ))
        # cd < expected (incl. duplicate/out-of-order) -> check_monotonic's job
    return flags


def check_monotonic(bars):
    """
    Flag any case where dates for this pair/source are not strictly increasing,
    i.e. a duplicate date or an out-of-order date. The offending (later) bar is
    flagged.
    """
    flags = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        pd, cd = _parse_date(prev["date"]), _parse_date(cur["date"])
        if cd == pd:
            flags.append(_flag(
                cur, "check_monotonic",
                f"duplicate date {cur['date']} for this pair/source",
            ))
        elif cd < pd:
            flags.append(_flag(
                cur, "check_monotonic",
                f"out-of-order date: {cur['date']} follows later date {prev['date']}",
            ))
    return flags


# Registry so the runner can iterate every check uniformly.
ALL_CHECKS = [
    check_ohlc_sanity,
    check_absurd_jump,
    check_stale_bar,
    check_weekend_bar,
    check_gaps,
    check_monotonic,
]
