"""One idempotent daily run for the paper-trading loop: settle -> record -> report.

Meant to be invoked by a scheduler (launchd/cron) once a day, early-to-mid
afternoon ET. Safe to run more than once a day — it won't double-record a station.
Never places live orders.

Usage: python -m scripts.run_daily [ICAO ...]   (default: KNYC)
"""
import sys
import traceback
from datetime import date, datetime, timezone

from wx import kalshi, paper, pipeline, trading
from wx.stations import get

BANKROLL = 1000.0


def record_station(led: paper.Ledger, icao: str, target: date):
    st = get(icao)
    if led.has_positions(st.icao, target.isoformat()):
        print(f"  {icao}: already recorded for {target}, skipping")
        return
    q = pipeline.quote_live(st, target)
    ms = kalshi.markets(st.kalshi, target)
    by_ticker = {m["ticker"]: m for m in ms}
    n = 0
    for d in trading.plan(ms, q.prob_fn, BANKROLL, min_edge=0.03, kelly_frac=0.25):
        m = by_ticker[d.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        mk = trading.maker_price(m, d.side)
        price, maker = (mk, True) if mk else (d.price, False)
        led.add(paper.Fill(d.ticker, st.icao, target.isoformat(), d.side, lo, hi,
                           price, d.count, maker=maker))
        n += 1
    src = "intraday" if q.intraday_active else "forecast-only"
    print(f"  {icao}: {n} positions ({src}, predictive N({q.mu:.1f},{q.sigma:.2f}))")


def main(*icaos):
    icaos = icaos or ["KNYC", "KMDW", "KAUS"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target = date.today()
    print(f"\n=== run_daily {stamp} target={target} stations={list(icaos)} ===")

    led = paper.Ledger()
    print("settle:", led.settle_due())

    for icao in icaos:
        try:
            record_station(led, icao, target)
        except Exception:
            print(f"  {icao}: ERROR\n{traceback.format_exc()}")
    led.save()
    print("status:", led.summary())


if __name__ == "__main__":
    main(*sys.argv[1:])
