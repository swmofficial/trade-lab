"""
Transaction-cost model: a spread debited per completed round trip.

Scope of THIS brick: spread only. NOT slippage beyond spread, not commissions,
not financing/carry — those are later bricks. One number, charged honestly, audited.

A pip is a PER-PAIR size, not a constant. Yen pairs quote to two decimals, so a pip
is 0.01; the other majors quote to four decimals, so a pip is 0.0001. Hardcoding
0.0001 everywhere would undercharge yen pairs by 100x — the defect this module exists
to prevent.
"""

# Default spread; the runner SWEEPS a fixed band [0,1,2,3] and does not rely on this.
SPREAD_PIPS = 1.0

JPY_PIP = 0.01
DEFAULT_PIP = 0.0001


def pip_size(pair):
    """Pip size for the pair's QUOTE currency (the part after '/').
    'USD/JPY' -> 0.01 ; 'EUR/USD','GBP/USD',... -> 0.0001."""
    quote = pair.split("/")[-1].upper()
    return JPY_PIP if quote == "JPY" else DEFAULT_PIP


def round_trip_cost_fraction(pair, entry_price, spread_pips=SPREAD_PIPS):
    """
    Round-trip spread cost as a fraction of entry price.

    We approximate buying at the ask and selling at the bid by debiting the full
    round-trip spread (in pips) once from the realized return:
        cost_fraction = (spread_pips * pip_size(pair)) / entry_price
    Charging it as a fraction of entry price keeps it commensurate with the gross
    price-ratio return it is subtracted from.
    """
    return (spread_pips * pip_size(pair)) / entry_price
