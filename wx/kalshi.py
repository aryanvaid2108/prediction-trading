"""Kalshi market-data client and a dry-run order path.

Reads (markets, prices) need no auth. Order placement is DRY-RUN by default and
only ever contacts Kalshi when called with live=True and real credentials from
the environment — this module never sends an order on its own.
"""
import os
import time
from dataclasses import dataclass
from datetime import date

import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "kalshi-weather/0.1", "Accept": "application/json"})
    return s


def event_ticker(series: str, day: date) -> str:
    return f"{series}-{day:%y}{_MONTHS[day.month - 1]}{day:%d}"


def _f(v):
    return None if v is None else float(v)


def _norm(m: dict) -> dict:
    return {
        "ticker": m["ticker"],
        "strike_type": m.get("strike_type"),
        "floor": _f(m.get("floor_strike")),
        "cap": _f(m.get("cap_strike")),
        "yes_bid": _f(m.get("yes_bid_dollars")),
        "yes_ask": _f(m.get("yes_ask_dollars")),
        "no_bid": _f(m.get("no_bid_dollars")),
        "no_ask": _f(m.get("no_ask_dollars")),
        "liquidity": _f(m.get("liquidity_dollars")),
        "volume": m.get("volume_fp"),
        "subtitle": m.get("yes_sub_title", ""),
        "status": m.get("status"),
    }


def markets(series: str, day: date, session=None, timeout: int = 30, retries: int = 6):
    """Normalized markets for a station/day event (e.g. KXHIGHNY on 2026-08-24)."""
    s = session or _session()
    ev = event_ticker(series, day)
    last = None
    for attempt in range(retries):
        try:
            r = s.get(f"{BASE}/markets", params={"event_ticker": ev, "limit": 100}, timeout=timeout)
            r.raise_for_status()
            return [_norm(m) for m in r.json()["markets"]]
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))  # backoff; Kalshi resets bursty connections
    raise last


def candlesticks(series: str, ticker: str, start_ts: int, end_ts: int,
                 interval: int = 60, session=None, timeout: int = 30):
    """Historical OHLC candles for a market (yes_bid/yes_ask/price per interval)."""
    sess = session or _session()
    for _ in range(3):
        try:
            r = sess.get(f"{BASE}/series/{series}/markets/{ticker}/candlesticks",
                         params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": interval},
                         timeout=timeout)
            if r.status_code >= 400:
                return []
            return r.json().get("candlesticks", [])
        except requests.RequestException:
            time.sleep(0.4)
    return []


@dataclass
class OrderResult:
    dry_run: bool
    payload: dict
    response: dict = None


def place_order(ticker: str, side: str, count: int, price: float,
                order_type: str = "limit", live: bool = False,
                session=None, timeout: int = 30) -> OrderResult:
    """Build (and, only if live=True + creds present, send) a maker limit order.

    Defaults to dry-run: returns the payload without contacting Kalshi. Live mode
    requires KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH (RSA-PSS signing) and an
    explicit live=True from the caller.
    """
    payload = {
        "ticker": ticker,
        "action": "buy",
        "side": side,
        "count": int(count),
        "type": order_type,
        f"{side}_price": int(round(price * 100)),  # cents
        "client_order_id": f"kw-{ticker}-{side}",
    }
    if not live:
        return OrderResult(dry_run=True, payload=payload)

    key_id = os.environ.get("KALSHI_ACCESS_KEY")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        raise RuntimeError("live order requires KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY_PATH")

    import base64
    import time
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    with open(key_path, "rb") as f:
        pk = serialization.load_pem_private_key(f.read(), password=None)
    ts = str(int(time.time() * 1000))
    method, path = "POST", "/trade-api/v2/portfolio/orders"
    sig = pk.sign(
        (ts + method + path).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    s = session or _session()
    s.headers.update({
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    })
    r = s.post(f"{BASE}/portfolio/orders", json=payload, timeout=timeout)
    r.raise_for_status()
    return OrderResult(dry_run=False, payload=payload, response=r.json())
