"""Daily realized P&L report — actual fills vs actual settlements.

Pulls every one of our weather orders for the target day, looks up each market's
settled result directly (robust to finalized markets dropping off the listing),
and reports realized P&L per station + net (gross minus estimated fees). Writes a
phone-notification body and prints the breakdown.

Usage:
  python -m scripts.daily_pnl            # yesterday (ET) — the day that just settled
  python -m scripts.daily_pnl 2026-08-25 # a specific day
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from wx import kalshi, stations, trading

NOTIFY_FILE = Path(__file__).resolve().parent.parent / ".cache" / "pnl_notify.txt"
MARKET_TZ = ZoneInfo("America/New_York")


def _datepart(d: date) -> str:
    return f"{d:%y}{kalshi._MONTHS[d.month - 1]}{d:%d}"      # e.g. 26AUG25


def _series_to_icao():
    return {stations.get(ic).kalshi: ic for ic in stations.ACTIVE}


def compute(target: date):
    s2i = _series_to_icao()
    series = set(s2i)
    dp = _datepart(target)
    # canceled too: an IOC order fills instantly then cancels its remainder, so a
    # fully-filled market order lands in 'canceled' with fill_count > 0.
    orders = (kalshi.orders(status="executed") + kalshi.orders(status="resting")
              + kalshi.orders(status="canceled"))
    session = kalshi._session()
    mcache, rows = {}, []
    for o in orders:
        t = o.get("ticker", "")
        pref = next((s for s in series if t.startswith(s + "-")), None)
        if not pref or f"-{dp}-" not in t:
            continue
        fill = float(o.get("fill_count_fp") or 0)
        if fill <= 0:
            continue
        if t not in mcache:
            try:
                mcache[t] = kalshi.market(t, session=session)
            except Exception:
                mcache[t] = None
        m = mcache[t]
        if not m:
            continue
        # settled YES value: prefer the result field, else the pinned price (Kalshi
        # leaves result empty and status 'active' for a while after the price settles)
        res = m.get("result")
        if res == "yes":
            sy = 1.0
        elif res == "no":
            sy = 0.0
        else:
            ya, yb = m.get("yes_ask"), m.get("yes_bid")
            if ya is None or yb is None:
                continue
            mid = (ya + yb) / 2
            if mid <= 0.03:
                sy = 0.0
            elif mid >= 0.97:
                sy = 1.0
            else:
                continue                                       # not resolved yet
        side = o.get("outcome_side")
        cost = float((o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars")) or 0)
        win = (sy >= 0.5) if side == "yes" else (sy < 0.5)
        gross = round(fill * ((1.0 if win else 0.0) - cost), 2)
        fee = trading.fee(cost, int(fill))
        rows.append({"icao": s2i[pref], "side": side, "fill": int(fill), "cost": cost,
                     "win": win, "gross": gross, "fee": fee, "ticker": t})
    return rows


def main(day=None):
    target = date.fromisoformat(day) if day else (datetime.now(MARKET_TZ).date() - timedelta(days=1))
    rows = compute(target)
    if not rows:
        msg = f"📊 {target:%b %-d}: no settled weather positions."
        NOTIFY_FILE.write_text(msg); print(msg); return

    by = {}
    for r in rows:
        by.setdefault(r["icao"], 0.0)
        by[r["icao"]] += r["gross"]
    gross = sum(r["gross"] for r in rows)
    fees = sum(r["fee"] for r in rows)
    net = gross - fees
    wins = sum(1 for r in rows if r["win"])

    print(f"=== {target} realized P&L ({len(rows)} positions) ===")
    for r in sorted(rows, key=lambda x: x["gross"]):
        b = f"{r['icao']} {r['side'].upper()}"
        print(f"  {b:9} x{r['fill']:<4} @{r['cost']:.2f}  {'WIN ' if r['win'] else 'LOSE'} ${r['gross']:+8.2f}  (fee ${r['fee']:.2f})")
    print(f"\n  gross ${gross:+.2f}  ·  fees -${fees:.2f}  ·  NET ${net:+.2f}  ·  {wins}/{len(rows)} won")

    try:
        bal = kalshi.balance().get("balance")
        balline = f"\n💰 balance ${bal/100:,.2f}" if bal is not None else ""
    except Exception:
        balline = ""

    pos = " · ".join(f"{ic.replace('K','')} ${v:+.0f}" for ic, v in sorted(by.items(), key=lambda x: x[1]))
    emoji = "🟢" if net > 0 else "🔴"
    msg = (f"📊 {target:%b %-d} settled — NET {emoji} ${net:+.0f}\n"
           f"gross ${gross:+.0f}, fees -${fees:.0f} · {wins}/{len(rows)} won\n"
           f"{pos}{balline}")
    NOTIFY_FILE.write_text(msg)
    print("\n--- notify ---\n" + msg)


if __name__ == "__main__":
    main(*sys.argv[1:])
