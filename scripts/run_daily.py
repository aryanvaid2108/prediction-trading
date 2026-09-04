"""Paper-trading driver: every strategy arm, every tick, honest fills.

Each invocation is one stateless tick: settle finished days against the CLI,
then (inside a tick slot) quote every station ONCE and let each arm in
wx.strategies.ARMS pick its own thesis from the same quote. Arms differ by
exactly one parameter from the control arm (== live config), so their ledgers
are a daily A/B of that change. Fills are honest: the touch price from the live
order book, capped at the contracts actually resting within the cross — the
old paper loop's 10,000-lot fills at 1c were how a fantasy backtest went live.
Never places live orders.

Usage:
  python -m scripts.run_daily [ICAO ...]        one tick (PAPER_FORCE=1 ignores the slot)
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from wx import kalshi, paper, stations, strategies, trading
from scripts.run_live import quote_all

BANKROLL = 150.0                 # == the live canary, so arm P&L is comparable to live
STATION_FRAC = 0.25
CROSS = 0.01                     # same 1c cross as the live IOC path
DEFAULT_STATIONS = stations.ACTIVE
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)


def paper_fill(pick, market, quote, arm, book):
    """(price, count) an IOC would actually get on this book, or None."""
    m2 = trading.refresh_market(market, book)
    pick2, _ = strategies.select([m2], quote, BANKROLL, arm)
    if pick2 is None or pick2.side != pick.side:
        return None
    _, depth = trading.book_touch(book, pick.side, CROSS)
    count = min(pick2.count, depth, int(STATION_FRAC * BANKROLL / pick2.price))
    return (pick2, count) if count >= 1 else None


def record(ledgers, icao, target, q, ms, books, session):
    """Let every arm decide on one station's quote; returns {arm: fill or None}."""
    key = target.isoformat()
    by = {m["ticker"]: m for m in ms}
    out = {}
    for name, arm in strategies.ARMS.items():
        led = ledgers[name]
        if led.has_positions(icao, key):
            continue
        pick, _ = strategies.select(ms, q, BANKROLL, arm)
        if pick is None:
            out[name] = None
            continue
        if pick.ticker not in books:
            try:
                books[pick.ticker] = kalshi.orderbook(pick.ticker, session=session)
            except Exception as e:
                print(f"    {name}: orderbook {pick.ticker} failed ({type(e).__name__}) — no fill")
                books[pick.ticker] = None
        book = books[pick.ticker]
        got = paper_fill(pick, by[pick.ticker], q, arm, book) if book else None
        if got is None:
            out[name] = None
            print(f"    {name}: {pick.side.upper()} {pick.ticker} @ {pick.price:.2f} did not survive the book")
            continue
        d, count = got
        m = by[d.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        led.add(paper.Fill(d.ticker, icao, key, d.side, lo, hi, d.price, count, maker=False,
                           p_model=d.model_prob, p_market=d.market_prob))
        out[name] = (d, count)
        print(f"    {name}: {d.side.upper()} {d.ticker} {count}x @ {d.price:.2f}")
    return out


def tick(icaos, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(MARKET_TZ)
    target = now_et.date()
    inwin = WINDOW_ET[0] <= now_et.hour < WINDOW_ET[1]
    slot = strategies.slot_for(now_utc)
    force = os.environ.get("PAPER_FORCE") == "1"
    print(f"\n--- tick {now_utc.isoformat(timespec='seconds')}  ({now_et:%H:%M} ET, "
          f"{'in' if inwin else 'out of'} window, slot={slot})  target={target} ---")

    ledgers = {name: paper.Ledger(paper.arm_ledger(name)) for name in strategies.ARMS}
    for name, led in ledgers.items():
        try:
            print(f"settle {name}:", led.settle_due(target))
        except Exception as e:
            print(f"settle {name} failed (non-fatal, will retry next tick): {type(e).__name__}: {e}")
    if not inwin or (slot is None and not force):
        print("outside the window / tick slots — settle only, no new positions")
        return
    todo = [ic for ic in icaos
            if any(not led.has_positions(ic, target.isoformat()) for led in ledgers.values())]
    quotes = quote_all(todo, target, now_utc)
    session = kalshi._session()
    books = {}
    for icao in todo:
        res = quotes[icao]
        if isinstance(res, Exception):
            print(f"  {icao}: ERROR {type(res).__name__}: {str(res)[:80]}")
            continue
        q, ms = res
        print(f"  {icao}: μ={q.mu:.1f} σ={q.sigma:.2f} obs_max={q.observed_max} intraday={q.intraday_active}")
        record(ledgers, icao, target, q, ms, books, session)
    for led in ledgers.values():
        led.save()
    for name, led in ledgers.items():
        print(f"status {name}:", led.summary())


def main(*args):
    tick(list(args) or DEFAULT_STATIONS)


if __name__ == "__main__":
    main(*sys.argv[1:])
