"""
THE GATE. No API calls. Synthetic bars with known ground truth.

GREEN: a clean ~15-bar run over a couple of weeks -> ZERO flags from every check.
RED:   inject one planted defect at a time -> the matching check flags EXACTLY that
       defect while every other check stays silent.

Run directly:  python tests/test_integrity.py   (prints PASS/FAIL per fixture, exits
nonzero on any failure). Also importable by pytest (test_* functions).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrity as ig

PAIR = "EUR/USD"
SOURCE = "twelvedata"

# 15 consecutive weekday dates across three trading weeks (no weekends, Fri->Mon = 3d).
CLEAN_DATES = [
    "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",  # Mon-Fri
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",  # Mon-Fri
    "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",  # Mon-Fri
]
# 15 distinct closes, tiny day-to-day moves (all returns well under 10%).
CLEAN_CLOSES = [
    1.0800, 1.0815, 1.0792, 1.0825, 1.0810,
    1.0788, 1.0801, 1.0779, 1.0822, 1.0795,
    1.0830, 1.0812, 1.0784, 1.0808, 1.0820,
]


def make_clean_bars():
    """Sane, strictly-increasing, weekday-only bars with no two alike."""
    bars = []
    prev_close = None
    for d, c in zip(CLEAN_DATES, CLEAN_CLOSES):
        o = prev_close if prev_close is not None else round(c - 0.0010, 5)
        hi = round(max(o, c) + 0.0020, 5)
        lo = round(min(o, c) - 0.0020, 5)
        bars.append({
            "pair": PAIR, "date": d, "source": SOURCE,
            "open": round(o, 5), "high": hi, "low": lo, "close": c,
            "volume": None,
        })
        prev_close = c
    return bars


def _raise(msg):
    raise AssertionError(msg)


def run_all(bars):
    """{check_name: [flags]} across every registered check."""
    return {fn.__name__: fn(bars) for fn in ig.ALL_CHECKS}


def counts(bars):
    return {name: len(flags) for name, flags in run_all(bars).items()}


# --- assertions used by both pytest and the __main__ harness ---------------------

def assert_green():
    c = counts(make_clean_bars())
    bad = {k: v for k, v in c.items() if v != 0}
    assert not bad, f"clean fixture produced flags: {bad}"
    return c


def assert_only(check_name, bars, want_dates=None):
    """Exactly `check_name` fires; all others silent. Optionally verify flagged dates."""
    res = run_all(bars)
    others = {k: len(v) for k, v in res.items() if k != check_name and v}
    assert not others, f"expected only {check_name}; also fired: {others}"
    assert res[check_name], f"{check_name} did not fire on its planted defect"
    if want_dates is not None:
        got = sorted({f["date"] for f in res[check_name]})
        assert got == sorted(want_dates), \
            f"{check_name} flagged dates {got}, expected {sorted(want_dates)}"
    return res[check_name]


# --- (a) high < close -> check_ohlc_sanity ---------------------------------------

def make_red_a():
    bars = make_clean_bars()
    b = bars[7]
    b["high"] = round(b["close"] - 0.0050, 5)  # high now below close: impossible
    return bars, [b["date"]]


def test_red_a_ohlc_sanity():
    bars, dates = make_red_a()
    assert_only("check_ohlc_sanity", bars, dates)


# --- (b) +30% close-to-close jump -> check_absurd_jump ----------------------------

def make_red_b():
    bars = make_clean_bars()
    b = bars[-1]                      # last bar: only one jump, no downstream bar
    b["close"] = round(b["close"] * 1.30, 5)
    b["high"] = round(b["close"] + 0.0020, 5)        # keep OHLC self-consistent so
    b["low"] = round(min(b["open"], b["close"]) - 0.0020, 5)  # ONLY the jump shows
    # jump flags both bars of the pair (prev + this)
    return bars, [bars[-2]["date"], bars[-1]["date"]]


def test_red_b_absurd_jump():
    bars, dates = make_red_b()
    assert_only("check_absurd_jump", bars, dates)


# --- (c) OHLC identical to prior bar, and separately O==H==L==C -> check_stale_bar -

def make_red_c_frozen():
    bars = make_clean_bars()
    p = bars[6]
    f = bars[7]
    for k in ("open", "high", "low", "close"):
        f[k] = p[k]                   # frozen feed: full OHLC copy of previous bar
    return bars, [f["date"]]


def make_red_c_zero_range():
    bars = make_clean_bars()
    b = bars[9]
    val = 1.07995                     # distinct from neighbours so ONLY zero-range fires
    b["open"] = b["high"] = b["low"] = b["close"] = val
    return bars, [b["date"]]


def test_red_c_frozen():
    bars, dates = make_red_c_frozen()
    flags = assert_only("check_stale_bar", bars, dates)
    assert any("frozen feed" in f["detail"] for f in flags)


def test_red_c_zero_range():
    bars, dates = make_red_c_zero_range()
    flags = assert_only("check_stale_bar", bars, dates)
    assert any("zero-range" in f["detail"] for f in flags)


# --- (d) Saturday-dated bar -> check_weekend_bar ----------------------------------

def make_red_d():
    bars = make_clean_bars()
    # INSERT a Saturday bar (2024-01-13) between Fri 01-12 and Mon 01-15 without
    # removing any trading day. The expected Fri->Mon progression is preserved around
    # it (Fri->Sat is "before expected" = monotonic's call but still increasing;
    # Sat->Mon hits the expected next trading day), so ONLY the weekend check fires.
    sat = {"pair": PAIR, "date": "2024-01-13", "source": SOURCE,
           "open": 1.0795, "high": 1.0822, "low": 1.0775, "close": 1.0802,
           "volume": None}
    idx = next(i for i, b in enumerate(bars) if b["date"] == "2024-01-15")
    bars.insert(idx, sat)
    return bars, ["2024-01-13"]


def test_red_d_weekend():
    bars, dates = make_red_d()
    assert_only("check_weekend_bar", bars, dates)


# --- (e) missing expected trading day(s) -> check_gaps ----------------------------
# The check now compares against the expected next trading day, so a SINGLE missing
# trading day flags (skipped=1). Each fixture below removes bar(s) from the clean run;
# OHLC stays sane, closes barely move, dates stay weekday/strictly-increasing, so only
# check_gaps fires. We assert the reported skipped count, not just that a flag exists.

def _drop(dates_to_drop):
    return [b for b in make_clean_bars() if b["date"] not in dates_to_drop]


def assert_gap(bars, flagged_date, skipped):
    flags = assert_only("check_gaps", bars, [flagged_date])
    detail = flags[0]["detail"]
    assert f"{skipped} expected trading day(s) missing" in detail, \
        f"expected skipped={skipped}, got detail: {detail!r}"


def test_green_gap_normal_weekend():
    """A clean run with normal Fri->Mon weekends yields ZERO gap flags."""
    assert ig.check_gaps(make_clean_bars()) == []


def test_red_e_missing_midweek():
    # drop Wed 01-03: Tue 01-02 -> Thu 01-04, one expected weekday (Wed) missing
    assert_gap(_drop({"2024-01-03"}), "2024-01-04", skipped=1)


def test_red_e_missing_friday():
    # drop Fri 01-05: Thu 01-04 -> Mon 01-08, one expected weekday (Fri) missing
    assert_gap(_drop({"2024-01-05"}), "2024-01-08", skipped=1)


def test_red_e_holiday_monday():
    # drop Mon 01-08: Fri 01-05 -> Tue 01-09, one expected weekday (Mon) missing
    assert_gap(_drop({"2024-01-08"}), "2024-01-09", skipped=1)


def test_red_e_two_dropped():
    # drop Mon 01-08 + Tue 01-09: Fri 01-05 -> Wed 01-10, two expected weekdays missing
    assert_gap(_drop({"2024-01-08", "2024-01-09"}), "2024-01-10", skipped=2)


# --- (f) duplicate / out-of-order date -> check_monotonic -------------------------

def make_red_f():
    bars = make_clean_bars()
    # INSERT a second bar dated 2024-01-09 (duplicate date) with its OWN distinct OHLC,
    # right after the real 01-09 bar. No trading day is removed, so the gap check stays
    # quiet; distinct OHLC keeps the stale check quiet; only check_monotonic fires.
    idx = next(i for i, b in enumerate(bars) if b["date"] == "2024-01-09")
    dup = dict(bars[idx])
    dup["open"], dup["high"], dup["low"], dup["close"] = 1.0801, 1.0826, 1.0779, 1.0805
    bars.insert(idx + 1, dup)
    return bars, ["2024-01-09"]


def test_red_f_monotonic():
    bars, dates = make_red_f()
    assert_only("check_monotonic", bars, dates)


# --- standalone harness ----------------------------------------------------------

def _main():
    fixtures = [
        ("GREEN  clean run (zero flags)",        lambda: (assert_green(), None)),
        ("RED (a) high < close",                 lambda: ("check_ohlc_sanity", make_red_a())),
        ("RED (b) +30% close-to-close jump",     lambda: ("check_absurd_jump", make_red_b())),
        ("RED (c1) frozen OHLC == prior bar",    lambda: ("check_stale_bar", make_red_c_frozen())),
        ("RED (c2) zero-range O==H==L==C",       lambda: ("check_stale_bar", make_red_c_zero_range())),
        ("RED (d) Saturday-dated bar",           lambda: ("check_weekend_bar", make_red_d())),
        ("GREEN gap: normal Fri->Mon weekend",   lambda: (ig.check_gaps(make_clean_bars()) == [] or _raise("gap flags on clean run"), None)),
        ("RED (e1) missing midweek day (skip=1)", lambda: ("check_gaps", (_drop({"2024-01-03"}), ["2024-01-04"]))),
        ("RED (e2) missing Friday (skip=1)",     lambda: ("check_gaps", (_drop({"2024-01-05"}), ["2024-01-08"]))),
        ("RED (e3) holiday Monday (skip=1)",     lambda: ("check_gaps", (_drop({"2024-01-08"}), ["2024-01-09"]))),
        ("RED (e4) two weekdays dropped (skip=2)", lambda: ("check_gaps", (_drop({"2024-01-08", "2024-01-09"}), ["2024-01-10"]))),
        ("RED (f) duplicate / out-of-order date", lambda: ("check_monotonic", make_red_f())),
    ]
    passed = 0
    for label, fn in fixtures:
        try:
            spec = fn()
            if spec[0] is None or not isinstance(spec[0], str):
                pass  # green path already asserted inside lambda
            else:
                check_name, (bars, dates) = spec
                assert_only(check_name, bars, dates)
            print(f"PASS  {label}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {label}: {e}")
    total = len(fixtures)
    print(f"\n{passed}/{total} fixtures passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(_main())
