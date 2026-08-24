"""Forward paper trading: record today's dry-run plan to a ledger, settle past
positions against the official CLI high, and report realized P&L.

Usage:
  python -m scripts.paper_trade record [ICAO] [bankroll]   # log today's plan
  python -m scripts.paper_trade settle                     # settle due positions
  python -m scripts.paper_trade status                     # show ledger summary
"""
import sys
from datetime import date

from wx import kalshi, paper, pipeline, trading
from wx.stations import get


def record(icao="KNYC", bankroll="1000"):
    st = get(icao)
    target = date.today()
    q = pipeline.quote_live(st, target)
    ms = kalshi.markets(st.kalshi, target)
    by_ticker = {m["ticker"]: m for m in ms}
    plan = trading.plan(ms, q.prob_fn, float(bankroll), min_edge=0.03, kelly_frac=0.25)

    led = paper.Ledger()
    n = 0
    for d in plan:
        m = by_ticker[d.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        mk = trading.maker_price(m, d.side)          # rest as maker (~0 fee) if possible
        price, maker = (mk, True) if mk else (d.price, False)
        led.add(paper.Fill(d.ticker, st.icao, target.isoformat(), d.side,
                           lo, hi, price, d.count, maker=maker))
        n += 1
        print(f"  logged {'MAKER' if maker else 'TAKER'} {d.side.upper()} {d.count} @ ${price:.2f}  '{d.subtitle}'")
    led.save()
    print(f"\nRecorded {n} paper positions for {target}. (no live orders placed)")


def settle():
    print("Settling due positions against official CLI...", paper.Ledger().settle_due())


def status():
    print("Ledger:", paper.Ledger().summary())


def main(cmd="status", *args):
    {"record": record, "settle": settle, "status": status}[cmd](*args)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["status"]))
