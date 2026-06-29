"""
Twelve Data ingest for daily forex OHLC.

Single source (Twelve Data), single interval (1day). Pages the requested range in
windows of <= 10 years (a single request caps at 5000 points). Stores bars exactly
as received — no cleaning, no repair. Defect detection happens later, in integrity.py.

Rate limits (free tier): 8 req/min, 800/day. On HTTP 429 or JSON body code 429 we
sleep 60s and retry (max 3). On code 401 we fail loud — a bad key is not retryable.
"""

import os
import time
from datetime import date, timedelta

import requests

BASE_URL = "https://api.twelvedata.com/time_series"
SOURCE = "twelvedata"
INTERVAL = "1day"
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

CHUNK_YEARS = 10          # <= 10yr per request (5000-point cap)
HISTORY_YEARS = 20        # how far back to attempt on the free tier
MAX_RETRIES = 3
RETRY_SLEEP_S = 60
INTER_REQUEST_SLEEP_S = 8  # 8 req/min -> ~1 req every 7.5s; 8s is a safe pace


class IngestError(Exception):
    pass


def load_env(path=".env"):
    """Minimal .env loader (KEY=VALUE per line) -> sets os.environ. No deps."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


def get_api_key():
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key or key == "your_key_here":
        raise IngestError(
            "TWELVEDATA_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return key


def _date_windows(start, end, chunk_years):
    """Yield (start_date, end_date) windows of <= chunk_years, oldest first."""
    windows = []
    cur = start
    while cur <= end:
        win_end = date(min(cur.year + chunk_years, end.year + 1), cur.month, cur.day)
        win_end = min(win_end, end)
        windows.append((cur, win_end))
        cur = win_end + timedelta(days=1)
    return windows


def _request(params):
    """One HTTP call with 429 retry. Returns parsed JSON dict."""
    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code == 429:
            print(f"  HTTP 429 (rate limit); sleeping {RETRY_SLEEP_S}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_SLEEP_S)
            continue
        try:
            body = resp.json()
        except ValueError:
            raise IngestError(f"Non-JSON response (HTTP {resp.status_code}): "
                              f"{resp.text[:200]}")

        code = body.get("code")
        if code == 429:
            print(f"  body code 429 (rate limit); sleeping {RETRY_SLEEP_S}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_SLEEP_S)
            continue
        if code == 401:
            raise IngestError(f"401 Unauthorized — bad API key: "
                              f"{body.get('message')}")
        return body

    raise IngestError("Exceeded retries on 429 rate limit")


def _parse_values(symbol, body):
    """Twelve Data 'values' rows -> normalized bar dicts."""
    status = body.get("status")
    if status == "error":
        # Common benign case: no data in window. Surface message, return nothing.
        print(f"  API status=error for {symbol}: "
              f"{body.get('code')} {body.get('message')}")
        return []

    bars = []
    for v in body.get("values", []):
        dt = v["datetime"][:10]  # 'YYYY-MM-DD' (1day bars have date-only datetimes)
        vol = v.get("volume")
        volume = None
        if vol not in (None, "", "0", "0.0"):
            try:
                volume = float(vol)
            except (TypeError, ValueError):
                volume = None
        bars.append({
            "pair": symbol,
            "date": dt,
            "source": SOURCE,
            "open": float(v["open"]),
            "high": float(v["high"]),
            "low": float(v["low"]),
            "close": float(v["close"]),
            "volume": volume,
        })
    return bars


def fetch_pair(symbol, api_key, history_years=HISTORY_YEARS, today=None):
    """
    Fetch all available daily bars for one symbol across the history window.
    `today` injectable for determinism/testing. Returns list of bar dicts
    (de-duplicated across windows, date-ascending).
    """
    if today is None:
        today = date.today()
    start = date(today.year - history_years, today.month, today.day)
    windows = _date_windows(start, today, CHUNK_YEARS)

    seen_dates = set()
    out = []
    for i, (ws, we) in enumerate(windows):
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "apikey": api_key,
            "start_date": ws.isoformat(),
            "end_date": we.isoformat(),
            "outputsize": 5000,
            "format": "JSON",
        }
        print(f"  window {i + 1}/{len(windows)}: {ws} -> {we}")
        body = _request(params)
        for b in _parse_values(symbol, body):
            if b["date"] in seen_dates:
                continue
            seen_dates.add(b["date"])
            out.append(b)
        if i < len(windows) - 1:
            time.sleep(INTER_REQUEST_SLEEP_S)

    out.sort(key=lambda b: b["date"])
    return out
