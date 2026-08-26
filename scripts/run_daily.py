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

from wx import kalshi, paper, pipeline, stations, trading
from wx.stations import get

BANKROLL = 1000.0
MAX_TOTAL_FRAC = 0.25            # cap on daily stake per station
MIN_EDGE = 0.05                  # above measured calibration slack (mirrors live)
ROBUST_DELTA = 1.5               # edge must survive a ±1.5° mean miss (mirrors live)
DEFAULT_STATIONS = stations.ACTIVE
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)            # record only 10:00-15:59 ET (intraday-active, sharp)
WATCH_INTERVAL_MIN = 30


def in_window(now_et) -> bool:
    return WINDOW_ET[0] <= now_et.hour < WINDOW_ET[1]


def record_station(led: paper.Ledger, icao: str, target, now_utc):
    """ONE robust thesis per station per day (mirrors the live strategy exactly:
    no bucket stacking, ±1.5° robustness gate, taker pricing like the IOC path)."""
    st = get(icao)
    key = target.isoformat()
    q = pipeline.quote_live(st, target, now_utc=now_utc)
    if led.has_positions(st.icao, key):
        return 0, q
    ms = kalshi.markets(st.kalshi, target)
    by = {m["ticker"]: m for m in ms}
    cands = trading.decisions_for(ms, q.prob_fn, BANKROLL, min_edge=MIN_EDGE, kelly_frac=0.25)
    gated = [d for d in cands
             if q.shift_fn is None
             or trading.robust_edge(by[d.ticker], d.side, q.shift_fn, ROBUST_DELTA) > 0]
    if not gated:
        return 0, q
    d = max(gated, key=lambda x: x.ev)
    cap = MAX_TOTAL_FRAC * BANKROLL
    if d.count * d.price > cap:
        d.count = int(cap / d.price)
    if d.count < 1:
        return 0, q
    m = by[d.ticker]
    lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
    led.add(paper.Fill(d.ticker, st.icao, key, d.side, lo, hi, d.price, d.count, maker=False))
    return 1, q


def tick(icaos, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(MARKET_TZ)
    target = now_et.date()
    print(f"\n--- tick {now_utc.isoformat(timespec='seconds')}  ({now_et:%H:%M} ET, "
          f"{'in' if in_window(now_et) else 'out of'} window)  target={target} ---")

    led = paper.Ledger()
    try:
        print("settle:", led.settle_due())
    except Exception as e:
        print(f"settle failed (non-fatal, will retry next tick): {type(e).__name__}: {e}")
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
