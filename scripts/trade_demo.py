"""DRY-RUN trade plan: intraday-conditioned Mixed EMOS vs live Kalshi prices.

Blends the forecast with today's observed temperatures, floors at the observed
max, prices every bucket, and sizes fractional-Kelly orders. Never places orders.

Usage: python -m scripts.trade_demo [ICAO] [bankroll]
"""
import sys
from datetime import date

from wx import kalshi, pipeline, trading
from wx.stations import get


def main(icao="KNYC", bankroll="1000"):
    st = get(icao)
    bankroll = float(bankroll)
    target = date.today()

    q = pipeline.quote_live(st, target)
    tag = (f"intraday-conditioned (obs max {q.observed_max:.0f}F @ {q.hour_lst:02d}:00 LST)"
           if q.intraday_active else "forecast-only (no usable obs yet)")
    print(f"{icao} ({st.name})  {target}  {tag}")
    print(f"  forecast prior N({q.mu_prior:.1f}, {q.sigma_prior:.1f}^2)  [sigma x{q.calib:.2f} calibrated]")
    print(f"  live predictive N({q.mu:.1f}, {q.sigma:.1f}^2)")
    print(f"  Kalshi {kalshi.event_ticker(st.kalshi, target)}   bankroll ${bankroll:.0f}\n")

    ms = kalshi.markets(st.kalshi, target)
    print(f"{'bucket':16} {'model_P':>8} {'yes_ask':>8} {'no_ask':>8} {'edge*':>7}")
    for m in sorted(ms, key=lambda x: (x.get('floor') is None, x.get('floor') or -999)):
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        p = q.prob_fn(lo, hi)
        ya, na = m.get("yes_ask"), m.get("no_ask")
        best = max([e for e in (
            (p - ya - trading.fee(ya)) if ya and 0 < ya < 1 else None,
            ((1 - p) - na - trading.fee(na)) if na and 0 < na < 1 else None,
        ) if e is not None], default=float("nan"))
        print(f"{m['subtitle']:16} {p:8.1%} {str(ya):>8} {str(na):>8} {best:+7.3f}")

    print("\nDRY-RUN ORDERS (fractional-Kelly, net-of-fee edge >= min, 25% exposure cap):")
    plan = trading.plan(ms, q.prob_fn, bankroll, min_edge=0.03, kelly_frac=0.25, max_total_frac=0.25)
    if not plan:
        print("  (no market clears the fee-adjusted edge threshold)")
    total_ev = total_cost = 0.0
    for d in plan:
        cost = d.count * d.price
        total_ev += d.ev
        total_cost += cost
        print(f"  BUY {d.side.upper():3} {d.count:>4} @ ${d.price:.2f}  '{d.subtitle}'  "
              f"modelP={d.model_prob:.0%} edge={d.edge_net:+.3f}/ct  EV=${d.ev:.2f}  cost=${cost:.0f}")
        kalshi.place_order(d.ticker, d.side, d.count, d.price, live=False)  # dry-run only
    if plan:
        print(f"\n  total staked ${total_cost:.0f}  expected profit ${total_ev:.2f}  "
              f"({100 * total_ev / max(total_cost, 1):.1f}% on stake)")
    print("\n  * edge = net-of-fee EV per contract on the better side. No orders were placed.")


if __name__ == "__main__":
    main(*sys.argv[1:])
