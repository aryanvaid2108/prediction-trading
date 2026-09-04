"""LIVE trading driver — places REAL Kalshi orders. Handle with care.

Same selector as the paper arms (wx.strategies), sized to a real bankroll, with
a hard total-stake cap so combined orders can never exceed your balance.
Marketable IOC orders re-priced against the live order book right before sending.

Modes (via the LIVE env var):
  LIVE unset / 0  -> PREVIEW: compute and print the exact order plan, place NOTHING.
  LIVE=1          -> place the plan for real (needs KALSHI_ACCESS_KEY + _PRIVATE_KEY_PATH).

Reconcile the Live ledger/dashboard from ACTUAL positions afterwards:
  python -m scripts.run_live reconcile

Usage:
  python -m scripts.run_live [ICAO ...]            preview (default) or place if LIVE=1
  python -m scripts.run_live reconcile             rebuild live_ledger.json from Kalshi
"""
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from wx import kalshi, paper, pipeline, stations, strategies, trading
from wx.stations import get

BANKROLL = float(os.environ.get("LIVE_BANKROLL", "750"))
MAX_TOTAL_STAKE = float(os.environ.get("LIVE_MAX_STAKE", str(BANKROLL)))  # combined cap
PER_STATION_FRAC = float(os.environ.get("LIVE_STATION_FRAC", "0.25"))     # diversify
MAX_ORDERS = int(os.environ.get("LIVE_MAX_ORDERS") or "0")                # 0 = unlimited (smoke-test with 1)
ARM = strategies.Arm(
    "live",
    min_edge=float(os.environ.get("LIVE_MIN_EDGE", "0.05")),
    min_price=float(os.environ.get("LIVE_MIN_PRICE", "0.15")),
    ratio_cap=float(os.environ.get("LIVE_RATIO_CAP", "2.5")),
    model_weight=float(os.environ.get("LIVE_MODEL_WEIGHT", "0.5")),
)
DAILY_LOSS_CAP = float(os.environ.get("LIVE_DAILY_LOSS_CAP", "15"))  # 10% of canary bankroll
BALANCE_FLOOR = float(os.environ.get("LIVE_BALANCE_FLOOR", "0"))     # dollars; 0 = check off
SLOTS_UTC = tuple(int(h) for h in os.environ.get("LIVE_SLOTS_UTC", "15,17,19").split(","))
SLOT_MINUTES = int(os.environ.get("LIVE_SLOT_MINUTES", str(strategies.SLOT_MINUTES)))
CROSS = float(os.environ.get("LIVE_CROSS_CENTS", "1")) / 100   # cross the touch to fill now
WORKERS = int(os.environ.get("WX_WORKERS", "3"))                # concurrent station quotes
MARKET_TZ = ZoneInfo("America/New_York")
WINDOW_ET = (10, 16)
LIVE_LEDGER = paper.LEDGER_DIR / "live_ledger.json"
TICK_LOG = paper.LEDGER_DIR / "ticks.jsonl"       # every quote + candidate, auditable
LOG_TICKS = os.environ.get("LIVE") in ("1", "true", "yes")   # previews stay out of the committed log
NOTIFY_FILE = paper.LEDGER_DIR / "live_notify.txt"
DEFAULT_STATIONS = stations.ACTIVE


def _write_notify(text):
    """Stash a phone-notification body for the workflow's ntfy step."""
    NOTIFY_FILE.write_text(text)
    print("\n--- notify ---\n" + text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")   # run page shows the outcome at a glance
    if summary:
        with open(summary, "a") as fh:
            fh.write("```\n" + text + "\n```\n")


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
    p_model: float = None
    p_market: float = None
    market: dict = None       # summary quote the plan was built from
    quote: object = None      # LiveQuote, to re-select on the live book


def risk_gate(led, target, loss_cap=None, resume=None):
    """(ok, reason) — refuse to place when the most recent settled day lost more
    than the cap. A bad day means either the model or the accounting broke;
    an explicit LIVE_RESUME=1 dispatch trades immediately, and the block expires
    after one day: a breach costs its next trading day, not the whole week.
    """
    loss_cap = DAILY_LOSS_CAP if loss_cap is None else loss_cap
    resume = os.environ.get("LIVE_RESUME") == "1" if resume is None else resume
    if loss_cap <= 0 or resume:
        return True, "resume override" if resume else "cap disabled"
    days = {}
    for f in led.fills:
        if f.pnl is not None and f.target < target.isoformat():
            days[f.target] = days.get(f.target, 0.0) + f.pnl
    if not days:
        return True, "no settled history"
    last = max(days)
    stale = (target - date.fromisoformat(last)).days > 1
    if days[last] <= -loss_cap and not stale:
        return False, f"last settled day {last} lost ${-days[last]:.2f} (cap ${loss_cap:.0f})"
    return True, f"last settled day {last}: ${days[last]:+.2f}" + (" (breach expired)" if stale and days[last] <= -loss_cap else "")


def quote_station(icao, target, now_utc):
    """(quote, markets) for one station — the slow, network-bound part of a tick."""
    st = get(icao)
    return pipeline.quote_live(st, target, now_utc=now_utc), kalshi.markets(st.kalshi, target)


def quote_all(icaos, target, now_utc, workers=None):
    """{icao: (quote, markets) | Exception} — stations quoted concurrently."""
    with ThreadPoolExecutor(max_workers=workers or WORKERS) as ex:
        futs = {ic: ex.submit(quote_station, ic, target, now_utc) for ic in icaos}
    out = {}
    for ic, fu in futs.items():
        try:
            out[ic] = fu.result()
        except Exception as e:      # surfaced in the log, the tick ledger and the phone
            out[ic] = e
    return out


def log_tick(rec):
    """Append one station-tick record — μ/σ/floor and every candidate with its
    gate verdict — so 'cands=1 gated=0' is auditable after the fact."""
    if not LOG_TICKS:
        return
    TICK_LOG.parent.mkdir(exist_ok=True)
    with open(TICK_LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def build_plan(icaos, target, now_utc, led, quotes=None, slot=None):
    """ONE robust thesis per station per day. Returns (orders, stake, errors).

    A station that already holds any position today is skipped (no bucket
    stacking, no YES-low+NO-high double-hits — one directional error can only
    cost one bet). Candidates must clear the arm's filters AND keep positive edge
    with the predictive mean shifted ±robust_delta (a bet that dies from a 1.5°
    miss isn't a bet). The single highest-EV survivor is taken."""
    key = target.isoformat()
    todo = [ic for ic in icaos if not led.has_positions(ic, key)]     # one thesis per day
    quotes = quotes if quotes is not None else quote_all(todo, target, now_utc)
    plan, errors = [], {}
    for icao in todo:
        res = quotes.get(icao)
        base = {"ts": now_utc.isoformat(timespec="seconds"), "slot": slot, "target": key, "icao": icao}
        if isinstance(res, Exception) or res is None:
            err = f"{type(res).__name__}: {str(res)[:80]}" if res is not None else "no quote"
            print(f"  {icao}: ERROR {err}")
            errors[icao] = type(res).__name__ if res is not None else "no quote"
            log_tick({**base, "error": err})
            continue
        q, ms = res
        by = {m["ticker"]: m for m in ms}
        pick, cands = strategies.select(ms, q, BANKROLL, ARM)
        print(f"  {icao}: μ={q.mu:.1f} σ={q.sigma:.2f} obs_max={q.observed_max} "
              f"intraday={q.intraday_active} cands={len(cands)} gated={sum(c.gated for c in cands)}")
        for c in cands:
            d = c.decision
            print(f"      {d.ticker} {d.side.upper():3} ask={d.price:.2f} p_model={d.model_prob:.2f} "
                  f"p_mkt={d.market_prob if d.market_prob is None else round(d.market_prob, 2)} "
                  f"ev=${d.ev:.2f} gate={'PASS' if c.gated else 'KILL'} ({c.worst_edge:+.3f})")
        log_tick({**base, "mu": round(q.mu, 2), "sigma": round(q.sigma, 3),
                  "obs_max": q.observed_max, "intraday": q.intraday_active,
                  "cands": [{"ticker": c.decision.ticker, "side": c.decision.side,
                             "ask": c.decision.price, "p_model": round(c.decision.model_prob, 4),
                             "p_market": c.decision.market_prob, "ev": round(c.decision.ev, 2),
                             "worst_edge": None if c.worst_edge == float("inf") else round(c.worst_edge, 4),
                             "gated": c.gated} for c in cands],
                  "pick": pick.ticker if pick else None})
        if not pick:
            continue
        d = pick
        station_cap = PER_STATION_FRAC * BANKROLL
        if d.count * d.price > station_cap:
            d.count = int(station_cap / d.price)
        if d.count < 1:
            continue
        m = by[d.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        plan.append(Order(icao, d.ticker, d.side, lo, hi, d.price, d.count, maker=False,
                          p_model=d.model_prob, p_market=d.market_prob, market=m, quote=q))
    # global cap across all stations, net of what is already staked live today
    total_left = MAX_TOTAL_STAKE - sum(f.count * f.price for f in led.fills if f.target == key)
    kept, spent = [], 0.0
    for o in plan:
        stake = o.count * o.price
        if spent + stake > total_left:
            continue
        kept.append(o); spent += stake
    return kept, spent, errors


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


def _parse_fill(response, side, planned_price):
    """(filled, avg_cost, fee_total) from the V2 create-order response.

    The API reports `average_fill_price` in YES terms; a NO fill's cost is its
    complement (verified against runs 32879236519 / 33004128510: NO planned at
    0.89/0.15 came back as 0.1000/0.8500). An unreadable fill_count records 0 —
    never assume the order filled (Day-2 ledger recorded 448 when 140 filled).
    """
    o = (response or {}).get("order", response or {})
    try:
        filled = int(round(float(o.get("fill_count", o.get("fill_count_fp")))))
    except (TypeError, ValueError):
        print(f"     UNREADABLE fill_count — recording 0, reconcile required. raw: {response}")
        return 0, planned_price, 0.0
    try:
        avg_yes = float(o.get("average_fill_price"))
        avg_cost = avg_yes if side == "yes" else round(1.0 - avg_yes, 4)
    except (TypeError, ValueError):
        avg_cost = planned_price
    try:
        fee_total = round(float(o.get("average_fee_paid")) * filled, 4)
    except (TypeError, ValueError):
        fee_total = None
    return filled, avg_cost, fee_total


def reprice(o, book):
    """Re-select on the live book right before sending; None if the order no
    longer qualifies there. The plan was built from the market summary, which
    lags the book: Sep 3 a 16.5c mid filled at 10.5c (below the price floor,
    p/ask 6x over the ratio cap) and two other orders found no ask at all.
    Size is capped at the contracts actually resting within the cross."""
    m2 = trading.refresh_market(o.market, book)
    pick, _ = strategies.select([m2], o.quote, BANKROLL, ARM)
    ask, depth = trading.book_touch(book, o.side, CROSS)
    if pick is None or pick.side != o.side:
        print(f"  ✗ {o.icao} {o.side.upper()} {bucket(o)} book moved: touch {ask} "
              f"(planned {o.price:.2f}) — no longer qualifies, not sent")
        return None
    o.price, o.p_market = pick.price, pick.market_prob
    o.count = min(o.count, pick.count, depth, int(PER_STATION_FRAC * BANKROLL / o.price))
    if o.count < 1:
        print(f"  ✗ {o.icao} {o.side.upper()} {bucket(o)} no depth at {ask} — not sent")
        return None
    return o


def place(plan, target, led, now_et, errors=None):
    key = target.isoformat()
    session = kalshi._session()
    placed, failed, total = [], [], 0
    print(f"\nPLACING {len(plan)} LIVE marketable/IOC orders…\n")
    for o in plan:
        try:
            o = reprice(o, kalshi.orderbook(o.ticker, session=session))
            if o is None:
                continue
            px = max(0.01, min(0.99, round(o.price + CROSS, 2)))       # marketable limit
            res = kalshi.place_order(o.ticker, o.side, o.count, px, live=True,
                                     session=session, time_in_force="immediate_or_cancel")
            filled, avg_cost, fee_total = _parse_fill(res.response, o.side, o.price)
            print(f"  ✓ {o.icao} {o.side.upper()} {bucket(o)} filled {filled}/{o.count} @ {avg_cost:.4f}")
            if filled > 0:
                led.add(paper.Fill(o.ticker, o.icao, key, o.side, o.lo, o.hi, avg_cost, filled,
                                   maker=False, fee=fee_total,
                                   p_model=o.p_model, p_market=o.p_market))
                placed.append((o, filled, avg_cost)); total += filled
        except Exception as e:
            print(f"  ✗ {o.icao} {o.side.upper()} {bucket(o)} FAILED: {type(e).__name__}: {e}")
            failed.append(o)
    led.save()
    staked = sum(f * cost for _, f, cost in placed)
    print(f"\nfilled {total} contracts across {len(placed)}/{len(plan)} markets (${staked:.2f}).")

    # rich phone notification (actual fills)
    byst = {}
    for o, f, _ in placed:
        byst[o.icao] = byst.get(o.icao, 0) + 1
    L = [f"🟢 {len(placed)} markets · {total} contracts · ${staked:.0f} filled  ({now_et:%H:%M} ET)"]
    if byst:
        L.append("  " + " · ".join(f"{ic.replace('K','')} {n}" for ic, n in sorted(byst.items())))
    if failed:
        L.append(f"⚠️ {len(failed)} failed to place")
    L += _error_lines(errors)
    try:
        bal = kalshi.balance(session=session).get("balance")
        if bal is not None:
            L.append(f"💰 balance ${bal/100:,.2f}")
    except Exception:
        pass
    _write_notify("\n".join(L))


def _error_lines(errors):
    """A station that failed to quote must never read as 'no edge' on the phone."""
    if not errors:
        return []
    return ["⚠️ quote ERROR " + " · ".join(f"{ic} ({e})" for ic, e in sorted(errors.items()))]


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
                    r = m.get("result")
                    if r in ("yes", "no"):
                        settled[m["ticker"]] = 1.0 if r == "yes" else 0.0
                        continue
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
    force = os.environ.get("LIVE_FORCE") == "1" or os.environ.get("LIVE_RESUME") == "1"   # manual dispatch: ignore the slot, keep the window
    inwin = WINDOW_ET[0] <= now_et.hour < WINDOW_ET[1]
    slot = strategies.slot_for(now_utc, SLOTS_UTC, SLOT_MINUTES)
    print(f"run_live {now_utc.isoformat(timespec='seconds')} ({now_et:%H:%M} ET, "
          f"{'in' if inwin else 'OUT OF'} window, slot={slot}) target={target} mode={'LIVE' if live else 'PREVIEW'}")

    led = paper.Ledger(LIVE_LEDGER)
    try:
        print("settle:", led.settle_due(target))   # realize finished days -> dashboard Live tab
    except Exception as e:
        print(f"settle failed (non-fatal, will retry next run): {type(e).__name__}: {e}")
    if live and not inwin:
        # a delayed cron firing off-window should NOT place real orders unattended
        print("outside 10:00-16:00 ET window — skipping live placement (safety).")
        _write_notify(f"🕙 {now_et:%H:%M} ET — outside trading window, no live orders placed.")
        return
    if live and slot is None and not force:
        # the edge is morning-shaped; a cron that lands between slots is not a tick
        slots = "/".join(f"{s:02d}Z" for s in SLOTS_UTC)
        print(f"outside every tick slot ({slots} +{SLOT_MINUTES}m) — skipping placement.")
        _write_notify(f"🕙 {now_et:%H:%M} ET — outside tick slots ({slots}), nothing placed.")
        return
    if not inwin:
        print("WARNING: outside the window — preview only; edges are weaker.")

    ok, why = risk_gate(led, target)
    print(f"risk gate: {'OK' if ok else 'HALTED'} — {why}")
    if live and not ok:
        _write_notify(f"🛑 kill-switch: {why}. No orders placed. "
                      f"Dispatch with LIVE_RESUME=1 after reviewing.")
        return
    if live and BALANCE_FLOOR > 0:
        try:
            bal = kalshi.balance().get("balance", 0) / 100
            if bal < BALANCE_FLOOR:
                print(f"balance ${bal:.2f} below floor ${BALANCE_FLOOR:.2f} — halting.")
                _write_notify(f"🛑 balance ${bal:.2f} under floor ${BALANCE_FLOOR:.2f} — no orders placed.")
                return
        except Exception as e:
            print(f"balance check failed (continuing): {type(e).__name__}: {e}")

    plan, spent, errors = build_plan(icaos, target, now_utc, led, slot=slot)
    if MAX_ORDERS and len(plan) > MAX_ORDERS:
        plan = plan[:MAX_ORDERS]
        spent = sum(o.count * o.price for o in plan)
        print(f"LIVE_MAX_ORDERS={MAX_ORDERS} — capping to the first {MAX_ORDERS} order(s).")
    if not plan:
        print("no qualifying edges (or caps already reached) — nothing to do.")
        if live:
            _write_notify("\n".join(
                [f"🟡 {now_et:%H:%M} ET — no new edge, nothing placed (already hold today's picks or caps hit)."]
                + _error_lines(errors)))
        return
    if live:
        place(plan, target, led, now_et, errors)
    else:
        preview(plan, spent)


if __name__ == "__main__":
    main(*sys.argv[1:])
