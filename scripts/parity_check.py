"""Settlement parity: does Kalshi's settled bucket equal the NWS CLI high?

Kalshi's rules cite The Weather Company; our settlement engine and model use
the NWS climate report. A divergence day (Miami Aug 29: CLI 85, Kalshi settled
90-91) is basis risk that no forecast skill can fix. Run over a long window
for every traded and candidate series before sizing anything up.

Usage: python -m scripts.parity_check [start] [end] [SERIES:ICAO ...]
"""
import sys
import time
from datetime import date, timedelta

from wx import cli, kalshi, stations, trading

SESSION = kalshi._session()


def kalshi_high(series, day):
    """Settled high implied by the YES bucket, or None if not settled."""
    for _ in range(3):
        try:
            ms = kalshi.markets(series, day, session=SESSION)
            break
        except Exception:
            time.sleep(2)
    else:
        return None
    for m in ms:
        if m.get("result") == "yes":
            lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
            return (lo, hi)
    return None


def check(series, icao, start, end):
    highs = {d.date(): v for d, v in cli.settlement_high(icao, start, end).items()}
    rows, match, miss = 0, 0, []
    d = start
    while d <= end:
        kb = kalshi_high(series, d)
        y = highs.get(d)
        if kb is not None and y is not None:
            rows += 1
            lo, hi = kb
            ok = (lo is None or y >= lo) and (hi is None or y <= hi)
            match += ok
            if not ok:
                miss.append((d.isoformat(), y, kb))
        d += timedelta(days=1)
        time.sleep(0.15)
    return rows, match, miss


def main(start="2026-07-25", end="2026-09-03", *pairs):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    pairs = [p.split(":") for p in pairs] or [(stations.get(ic).kalshi, ic) for ic in stations.ACTIVE]
    for series, icao in pairs:
        try:
            n, m, miss = check(series, icao, s, e)
            print(f"{icao:5} {series:12} {m}/{n} match" + (f"  MISSES {miss}" if miss else ""), flush=True)
        except Exception as ex:
            print(f"{icao:5} {series:12} ERROR {type(ex).__name__}: {str(ex)[:80]}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
