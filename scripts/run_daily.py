"""Paper-trading driver — safe to run many times a day (intraday ticks).

Each invocation is one stateless tick: settle finished days against the CLI, then
(if inside the trading window) re-quote every station with the freshest
observations and record any NEW edge. It won't re-enter a bucket it already holds,
and it caps cumulative daily exposure per station. Never places live orders.

Modes:
  python -m scripts.run_daily [ICAO ...]              one tick (for cron/launchd)
  python -m scripts.run_daily watch [MINS] [ICAO ...] loop every MINS in-window
"""
import sys
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from wx import kalshi, paper, pipeline, trading
from wx.stations import get

BANKROLL = 1000.0
MAX_TOTAL_FRAC = 0.25            # cap on cumulative daily stake per station
DEFAULT_STATIONS = ["KNYC", "KMDW", "KAUS"]
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)            # record only 10:00-15:59 ET (intraday-active, sharp)
WATCH_INTERVAL_MIN = 30


def in_window(now_et) -> bool:
    return WINDOW_ET[0] <= now_et.hour < WINDOW_ET[1]


def record_station(led: paper.Ledger, icao: str, target, now_utc):
    st = get(icao)
    key = target.isoformat()
    remaining = MAX_TOTAL_FRAC * BANKROLL - led.staked_on(st.icao, key)
    q = pipeline.quote_live(st, target, now_utc=now_utc)
    if remaining < 1:
        return 0, q
    ms = kalshi.markets(st.kalshi, target)
    by = {m["ticker"]: m for m in ms}
    held = led.held_markets(st.icao, key)
    fresh = [d for d in trading.decisions_for(ms, q.prob_fn, BANKROLL, min_edge=0.03, kelly_frac=0.25)
             if (d.ticker, d.side) not in held]
    added = 0
    for d in trading.cap_exposure(fresh, remaining):
        m = by[d.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        mk = trading.maker_price(m, d.side)
        price, maker = (mk, True) if mk else (d.price, False)
        led.add(paper.Fill(d.ticker, st.icao, key, d.side, lo, hi, price, d.count, maker=maker))
        added += 1
    return added, q


def tick(icaos, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(MARKET_TZ)
    target = now_et.date()
    print(f"\n--- tick {now_utc.isoformat(timespec='seconds')}  ({now_et:%H:%M} ET, "
          f"{'in' if in_window(now_et) else 'out of'} window)  target={target} ---")

    led = paper.Ledger()
    print("settle:", led.settle_due())
    if not in_window(now_et):
        print("outside trading window — settle only, no new positions")
        print("status:", led.summary())
        return
    for icao in icaos:
        try:
            n, q = record_station(led, icao, target, now_utc)
            src = "intraday" if q.intraday_active else "forecast"
            print(f"  {icao}: +{n} new ({src}, N({q.mu:.1f},{q.sigma:.2f}))")
        except Exception:
            print(f"  {icao}: ERROR\n{traceback.format_exc()}")
    led.save()
    print("status:", led.summary())


def watch(icaos, interval_min):
    print(f"watch: every {interval_min}m while {WINDOW_ET[0]:02d}:00-{WINDOW_ET[1]:02d}:00 ET, "
          f"stations={icaos}")
    while True:
        now_et = datetime.now(MARKET_TZ)
        if not in_window(now_et):
            print(f"window closed ({now_et:%H:%M} ET) — stopping")
            break
        tick(icaos)
        time.sleep(interval_min * 60)


def main(*args):
    args = list(args)
    if args and args[0] == "watch":
        args = args[1:]
        interval = int(args[0]) if args and args[0].isdigit() else WATCH_INTERVAL_MIN
        stations = [a for a in args if not a.isdigit()] or DEFAULT_STATIONS
        watch(stations, interval)
    else:
        tick(args or DEFAULT_STATIONS)


if __name__ == "__main__":
    main(*sys.argv[1:])
