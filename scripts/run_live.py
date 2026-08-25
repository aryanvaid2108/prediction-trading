"""LIVE trading driver — places REAL Kalshi orders. Handle with care.

Same model/decision logic as the paper runner (run_daily), sized to a real
bankroll, with a hard total-stake cap so combined orders can never exceed your
balance. Maker limit orders (they rest in the book and may not all fill).

Modes (via the LIVE env var):
  LIVE unset / 0  -> PREVIEW: compute and print the exact order plan, place NOTHING.
  LIVE=1          -> place the plan for real (needs KALSHI_ACCESS_KEY + _PRIVATE_KEY_PATH).

Reconcile the Live ledger/dashboard from ACTUAL positions afterwards:
  python -m scripts.run_live reconcile

Usage:
  python -m scripts.run_live [ICAO ...]            preview (default) or place if LIVE=1
  python -m scripts.run_live reconcile             rebuild live_ledger.json from Kalshi
"""
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from wx import kalshi, paper, pipeline, stations, trading
from wx.stations import get

BANKROLL = float(os.environ.get("LIVE_BANKROLL", "750"))
MAX_TOTAL_STAKE = float(os.environ.get("LIVE_MAX_STAKE", str(BANKROLL)))  # combined cap
PER_STATION_FRAC = float(os.environ.get("LIVE_STATION_FRAC", "0.25"))     # diversify
MIN_EDGE, KELLY = 0.03, 0.25
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)
LIVE_LEDGER = paper.LEDGER.parent / "live_ledger.json"
DEFAULT_STATIONS = stations.ACTIVE


@dataclass
class Order:
    icao: str
    ticker: str
    side: str
    lo: float
    hi: float
    price: float
    count: int
    maker: bool


def build_plan(icaos, target, now_utc):
    """The full model order set, per-station-capped then total-capped."""
    plan = []
    for icao in icaos:
        try:
            st = get(icao)
            q = pipeline.quote_live(st, target, now_utc=now_utc)
            ms = kalshi.markets(st.kalshi, target)
            by = {m["ticker"]: m for m in ms}
            decs = trading.decisions_for(ms, q.prob_fn, BANKROLL, min_edge=MIN_EDGE, kelly_frac=KELLY)
            for d in trading.cap_exposure(decs, PER_STATION_FRAC * BANKROLL):
                m = by[d.ticker]
                lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
                mk = trading.maker_price(m, d.side)
                price, maker = (mk, True) if mk else (d.price, False)
                plan.append(Order(icao, d.ticker, d.side, lo, hi, price, d.count, maker))
        except Exception:
            print(f"  {icao}: ERROR\n{traceback.format_exc()}")
    # global cap across all stations: keep in order until the combined budget is spent
    kept, spent = [], 0.0
    for o in plan:
        stake = o.count * o.price
        if spent + stake > MAX_TOTAL_STAKE:
            continue
        kept.append(o); spent += stake
    return kept, spent


def bucket(o):
    if o.lo is None:
        return f"<={int(o.hi)}"
    if o.hi is None:
        return f">={int(o.lo)}"
    return f"{int(o.lo)}-{int(o.hi)}"


def preview(plan, spent):
    print(f"\nPLAN (bankroll ${BANKROLL:.0f}, total cap ${MAX_TOTAL_STAKE:.0f}) — {len(plan)} orders:\n")
    for o in plan:
        print(f"  {o.icao} {o.side.upper():3} {bucket(o):8} {o.count:>4}x @ {o.price:.2f}  "
              f"= ${o.count*o.price:>6.2f}  ({'maker' if o.maker else 'taker'})")
    print(f"\n  total stake: ${spent:.2f}  ·  max loss if all lose: ${spent:.2f}")
    print("\nPREVIEW ONLY — no orders placed. Set LIVE=1 to submit these for real.")


def place(plan, target):
    led = paper.Ledger(LIVE_LEDGER)
    key = target.isoformat()
    session = kalshi._session()
    placed = 0
    print(f"\nPLACING {len(plan)} LIVE orders…\n")
    for o in plan:
        try:
            res = kalshi.place_order(o.ticker, o.side, o.count, o.price, live=True, session=session)
            status = (res.response or {}).get("order", {}).get("status", "?")
            print(f"  ✓ {o.icao} {o.side.upper()} {bucket(o)} {o.count}x @ {o.price:.2f} -> {status}")
            led.add(paper.Fill(o.ticker, o.icao, key, o.side, o.lo, o.hi, o.price, o.count, maker=o.maker))
            placed += 1
        except Exception as e:
            print(f"  ✗ {o.icao} {o.side.upper()} {bucket(o)} FAILED: {type(e).__name__}: {e}")
    led.save()
    print(f"\nplaced {placed}/{len(plan)} orders (logged to {LIVE_LEDGER.name}). "
          f"Run `reconcile` to sync the ledger to actual fills.")


def reconcile():
    """Rebuild live_ledger.json from ACTUAL Kalshi positions (ground truth)."""
    pos = kalshi.positions()
    live = [p for p in pos if p.get("position")]
    print(f"reconcile: {len(live)} open positions on Kalshi")
    for p in live:
        print(f"  {p.get('ticker')}  position={p.get('position')}  "
              f"exposure=${(p.get('market_exposure') or 0)/100:.2f}  "
              f"realized=${(p.get('realized_pnl') or 0)/100:.2f}")
    # NOTE: automated ticker->bucket mapping for the ledger lands once field shapes
    # are confirmed against a real position; for now this prints the truth to verify.


def main(*args):
    args = list(args)
    if args and args[0] == "reconcile":
        reconcile(); return
    icaos = args or DEFAULT_STATIONS
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(MARKET_TZ)
    target = now_et.date()
    live = os.environ.get("LIVE") in ("1", "true", "yes")
    inwin = WINDOW_ET[0] <= now_et.hour < WINDOW_ET[1]
    print(f"run_live {now_utc.isoformat(timespec='seconds')} ({now_et:%H:%M} ET, "
          f"{'in' if inwin else 'OUT OF'} window) target={target} mode={'LIVE' if live else 'PREVIEW'}")
    if not inwin:
        print("WARNING: outside the 10:00-16:00 ET window — edges are weaker; plan shown anyway.")
    plan, spent = build_plan(icaos, target, now_utc)
    if not plan:
        print("no qualifying edges right now — nothing to do."); return
    if live:
        place(plan, target)
    else:
        preview(plan, spent)


if __name__ == "__main__":
    main(*sys.argv[1:])
