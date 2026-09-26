"""Ledger vs exchange, order by order. Read-only (signed GETs). Run in CI where
the keys live: gh workflow run reconcile.yml

Prints: balance; every order with status / side / requested / filled / avg
price; the join to the ledger (ledger fill with no exchange order, exchange
fill with no ledger row, size or price mismatches); settled positions' realized
P&L from Kalshi vs the ledger's; and a JSON dump for offline analysis.
"""
import json
from datetime import date

from wx import kalshi, paper
from scripts.run_live import LIVE_LEDGER


def main():
    bal = kalshi.balance()
    print(f"BALANCE ${bal.get('balance', 0) / 100:,.2f}  (portfolio_value ${bal.get('portfolio_value', 0) / 100:,.2f})")
    orders = []
    for st in ("resting", "executed", "canceled"):
        for o in kalshi.orders(status=st):
            orders.append({"status": st, "ticker": o.get("ticker"), "side": o.get("outcome_side"),
                           "book": o.get("book_side"), "created": o.get("created_time"),
                           "wanted": float(o.get("initial_count_fp") or 0), "filled": float(o.get("fill_count_fp") or 0),
                           "yes_px": o.get("yes_price_dollars"), "no_px": o.get("no_price_dollars"),
                           "avg": o.get("average_fill_price") or o.get("average_fill_price_dollars"),
                           "fee": o.get("average_fee_paid") or o.get("fee_dollars"), "id": o.get("order_id")})
    orders.sort(key=lambda o: o["created"] or "")
    print(f"\nORDERS {len(orders)}  (filled>0: {sum(1 for o in orders if o['filled'] > 0)})")
    for o in orders:
        print(f"  {o['created'][:16] if o['created'] else '?':16} {o['ticker']:28} {str(o['side']).upper():3} "
              f"{o['status']:8} wanted {o['wanted']:5.0f} filled {o['filled']:5.0f} yes={o['yes_px']} no={o['no_px']} avg={o['avg']} fee={o['fee']}")
    pos = kalshi.positions()
    print(f"\nPOSITIONS {len(pos)}")
    tot_real = 0.0
    for p in pos:
        rp = (p.get("realized_pnl") or 0) / 100; tot_real += rp
        print(f"  {p.get('ticker'):28} position {p.get('position'):>5}  exposure ${(p.get('market_exposure') or 0) / 100:8.2f}  "
              f"realized ${rp:+8.2f}  fees ${(p.get('fees_paid') or 0) / 100:6.2f}  resting {p.get('resting_orders_count')}")
    print(f"KALSHI realized P&L across positions: ${tot_real:+.2f}")

    led = paper.Ledger(LIVE_LEDGER)
    print(f"\nLEDGER {len(led.fills)} fills, settled P&L ${sum(f.pnl for f in led.fills if f.pnl is not None):+.2f}")
    ex_fills = {}
    for o in orders:
        if o["filled"] > 0:
            ex_fills.setdefault((o["ticker"], o["side"]), []).append(o)
    print("\nJOIN ledger -> exchange:")
    for f in led.fills:
        m = ex_fills.get((f.ticker, f.side), [])
        ex_n = sum(o["filled"] for o in m)
        flag = "" if abs(ex_n - f.count) < 0.5 else f"  <-- SIZE MISMATCH (exchange {ex_n:.0f})"
        if not m:
            flag = "  <-- NO EXCHANGE FILL"
        print(f"  {f.target} {f.ticker:28} {f.side.upper():3} ledger {f.count:4} @ {f.price:.4f} pnl {f.pnl}{flag}")
    seen = {(f.ticker, f.side) for f in led.fills}
    orphans = [o for (t, s), os_ in ex_fills.items() if (t, s) not in seen for o in os_]
    print(f"\nexchange fills with NO ledger row: {len(orphans)}")
    for o in orphans:
        print(f"  {o['created'][:16] if o['created'] else '?'} {o['ticker']} {str(o['side']).upper()} filled {o['filled']:.0f} avg={o['avg']}")
    json.dump({"balance": bal, "orders": orders, "positions": pos,
               "ledger": [f.__dict__ for f in led.fills]}, open(".cache/audit_exchange.json", "w"), indent=1, default=str)
    print("\nwrote .cache/audit_exchange.json")


if __name__ == "__main__":
    main()
