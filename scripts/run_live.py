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
MAX_ORDERS = int(os.environ.get("LIVE_MAX_ORDERS") or "0")                # 0 = unlimited (smoke-test with 1)
MIN_EDGE, KELLY = 0.03, 0.25
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)
LIVE_LEDGER = paper.LEDGER.parent / "live_ledger.json"
NOTIFY_FILE = paper.LEDGER.parent / "live_notify.txt"
DEFAULT_STATIONS = stations.ACTIVE


def _write_notify(text):
    """Stash a phone-notification body for the workflow's ntfy step."""
    NOTIFY_FILE.write_text(text)
    print("\n--- notify ---\n" + text)


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


def build_plan(icaos, target, now_utc, led):
    """The full model order set — ledger-aware so repeated runs never double-buy a
    held bucket or stake past the per-station / total caps for the day."""
    key = target.isoformat()
    plan = []
    for icao in icaos:
        try:
            st = get(icao)
            held = led.held_markets(st.icao, key)                     # skip what we already hold
            station_left = PER_STATION_FRAC * BANKROLL - led.staked_on(st.icao, key)
            if station_left < 1:
                continue
            q = pipeline.quote_live(st, target, now_utc=now_utc)
            ms = kalshi.markets(st.kalshi, target)
            by = {m["ticker"]: m for m in ms}
            fresh = [d for d in trading.decisions_for(ms, q.prob_fn, BANKROLL, min_edge=MIN_EDGE, kelly_frac=KELLY)
                     if (d.ticker, d.side) not in held]
            for d in trading.cap_exposure(fresh, station_left):
                m = by[d.ticker]
                lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
                # taker price (the ask decide() approved) — placed marketable/IOC so it fills now
                plan.append(Order(icao, d.ticker, d.side, lo, hi, d.price, d.count, maker=False))
        except Exception:
            print(f"  {icao}: ERROR\n{traceback.format_exc()}")
    # global cap across all stations, net of what is already staked live today
    total_left = MAX_TOTAL_STAKE - sum(f.count * f.price for f in led.fills if f.target == key)
    kept, spent = [], 0.0
    for o in plan:
        stake = o.count * o.price
        if spent + stake > total_left:
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


def _filled_count(response):
    """Actual contracts filled, from the V2 create-order response (None if unreadable)."""
    o = (response or {}).get("order", response or {})
    try:
        return int(round(float(o.get("fill_count_fp"))))
    except (TypeError, ValueError):
        return None


def place(plan, target, led, now_et):
    key = target.isoformat()
    session = kalshi._session()
    cross = float(os.environ.get("LIVE_CROSS_CENTS", "1")) / 100   # cross the touch to fill now
    placed, failed, total = [], [], 0
    print(f"\nPLACING {len(plan)} LIVE marketable/IOC orders…\n")
    for o in plan:
        px = max(0.01, min(0.99, round(o.price + cross, 2)))       # marketable limit
        try:
            res = kalshi.place_order(o.ticker, o.side, o.count, px, live=True,
                                     session=session, time_in_force="immediate_or_cancel")
            filled = _filled_count(res.response)
            if filled is None:
                print(f"     (raw response: {res.response})")      # shape check, first run
                filled = o.count
            print(f"  ✓ {o.icao} {o.side.upper()} {bucket(o)} filled {filled}/{o.count} @ ~{o.price:.2f}")
            if filled > 0:
                led.add(paper.Fill(o.ticker, o.icao, key, o.side, o.lo, o.hi, o.price, filled, maker=False))
                placed.append((o, filled)); total += filled
        except Exception as e:
            print(f"  ✗ {o.icao} {o.side.upper()} {bucket(o)} FAILED: {type(e).__name__}: {e}")
            failed.append(o)
    led.save()
    staked = sum(f * o.price for o, f in placed)
    print(f"\nfilled {total} contracts across {len(placed)}/{len(plan)} markets (${staked:.2f}).")

    # rich phone notification (actual fills)
    byst = {}
    for o, f in placed:
        byst[o.icao] = byst.get(o.icao, 0) + 1
    L = [f"🟢 {len(placed)} markets · {total} contracts · ${staked:.0f} filled  ({now_et:%H:%M} ET)"]
    if byst:
        L.append("  " + " · ".join(f"{ic.replace('K','')} {n}" for ic, n in sorted(byst.items())))
    if failed:
        L.append(f"⚠️ {len(failed)} failed to place")
    try:
        bal = kalshi.balance(session=session).get("balance")
        if bal is not None:
            L.append(f"💰 balance ${bal/100:,.2f}")
    except Exception:
        pass
    _write_notify("\n".join(L))


def reconcile():
    """Print ACTUAL Kalshi state — orders by status (fills) + positions (ground truth)."""
    for status in ("resting", "executed", "canceled"):
        os_ = kalshi.orders(status=status)
        print(f"\n{status}: {len(os_)}")
        for o in os_:
            print(f"  {o.get('ticker'):24} {str(o.get('outcome_side')).upper():3} "
                  f"book={o.get('book_side'):4} init={o.get('initial_count_fp')} "
                  f"filled={o.get('fill_count_fp')} rem={o.get('remaining_count_fp')} "
                  f"yes=${o.get('yes_price_dollars')} no=${o.get('no_price_dollars')}")
    pos = [p for p in kalshi.positions() if p.get("position")]
    print(f"\npositions: {len(pos)}")
    for p in pos:
        print(f"  {p.get('ticker')}  position={p.get('position')}  "
              f"exposure=${(p.get('market_exposure') or 0)/100:.2f}  "
              f"realized=${(p.get('realized_pnl') or 0)/100:.2f}")

    # --- realized P&L from actual fills vs actual settlements (all our weather bets) ---
    from datetime import date, timedelta
    settled = {}
    for d in (date.today(), date.today() - timedelta(days=1)):
        for ic in stations.ACTIVE:
            try:
                for m in kalshi.markets(get(ic).kalshi, d):
                    ya, yb = m.get("yes_ask"), m.get("yes_bid")
                    if ya is not None and yb is not None:
                        settled[m["ticker"]] = 1.0 if (ya + yb) / 2 >= 0.5 else 0.0
            except Exception:
                pass
    allo = kalshi.orders(status="executed") + kalshi.orders(status="resting")
    tot = 0.0
    print("\n--- realized P&L (filled contracts vs settlement) ---")
    for o in sorted(allo, key=lambda x: x.get("ticker", "")):
        t = o.get("ticker", "")
        if t not in settled:
            continue
        fill = float(o.get("fill_count_fp") or 0)
        if fill <= 0:
            continue
        os_ = o.get("outcome_side")
        cost = float((o.get("yes_price_dollars") if os_ == "yes" else o.get("no_price_dollars")) or 0)
        sy = settled[t]
        win = (sy >= 0.5) if os_ == "yes" else (sy < 0.5)
        p = fill * ((1.0 if win else 0.0) - cost)
        tot += p
        print(f"  {t:26} {str(os_).upper():3} x{fill:>4.0f} @{cost:.2f}  {'WIN ' if win else 'LOSE'} ${p:+8.2f}")
    print(f"\n=== REALIZED P&L (gross, pre-fee): ${tot:+.2f} ===")


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

    led = paper.Ledger(LIVE_LEDGER)
    if live and not inwin:
        # scheduled at the morning tick; a delayed cron firing off-window should NOT
        # place real orders unattended. Manual dispatch can still preview any time.
        print("outside 10:00-16:00 ET window — skipping live placement (safety).")
        _write_notify(f"🕙 {now_et:%H:%M} ET — outside trading window, no live orders placed.")
        return
    if not inwin:
        print("WARNING: outside the window — preview only; edges are weaker.")

    plan, spent = build_plan(icaos, target, now_utc, led)
    if MAX_ORDERS and len(plan) > MAX_ORDERS:
        plan = plan[:MAX_ORDERS]
        spent = sum(o.count * o.price for o in plan)
        print(f"LIVE_MAX_ORDERS={MAX_ORDERS} — capping to the first {MAX_ORDERS} order(s).")
    if not plan:
        print("no qualifying edges (or caps already reached) — nothing to do.")
        if live:
            _write_notify(f"🟡 {now_et:%H:%M} ET — no new edge, nothing placed (already hold today's picks or caps hit).")
        return
    if live:
        place(plan, target, led, now_et)
    else:
        preview(plan, spent)


if __name__ == "__main__":
    main(*sys.argv[1:])
