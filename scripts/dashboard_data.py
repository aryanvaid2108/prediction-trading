"""Emit docs/data.json for the live HTML dashboard (docs/index.html).

For every position: entry price, current mark (what our side could exit at now),
and — per station — the temperature observed so far today vs the bucket we traded.
Network calls are best-effort: a failed fetch just drops the live field, the rest
still renders. Regenerated every tick by the paper-loop workflow.

Usage: python -m scripts.dashboard_data
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wx import kalshi, obs, paper, settlement, stations

BANKROLL = 1000.0
OUT = Path(__file__).resolve().parent.parent / "docs" / "data.json"


def bucket(f):
    if f.lo is None:
        return f"≤{int(f.hi)}°"
    if f.hi is None:
        return f"≥{int(f.lo)}°"
    return f"{int(f.lo)}–{int(f.hi)}°"


def temp_today(st, target):
    """(latest temp, running max so far) for the target LST day, or (None, None)."""
    try:
        o = obs.fetch_asos(st.iem_id, target - timedelta(days=1), target + timedelta(days=1))
    except Exception:
        return None, None
    o = o.dropna(subset=["tmpf"])
    o = o[settlement.lst_day(o["valid"], st.std_utc_offset) == target]
    if o.empty:
        return None, None
    o = o.sort_values("valid")
    return round(float(o["tmpf"].iloc[-1]), 1), round(float(o["tmpf"].max()), 1)


def current_mark(session, cache, st, target, ticker, side):
    """What our side could be sold at right now (current bid for our side)."""
    key = (st.kalshi, target)
    if key not in cache:
        try:
            cache[key] = {m["ticker"]: m for m in kalshi.markets(st.kalshi, target, session=session)}
        except Exception:
            cache[key] = {}
    m = cache[key].get(ticker)
    if not m:
        return None
    return m["yes_bid"] if side == "yes" else m["no_bid"]


PAPER_LEDGER = paper.LEDGER
LIVE_LEDGER = paper.LEDGER.parent / "live_ledger.json"


def build_env(led, session, mkt_cache, temp_cache, today):
    """summary + stations + positions + equity for one ledger (an environment)."""
    s = led.summary()
    positions = []
    for f in led.fills:
        st = stations.get(f.icao)
        target = date.fromisoformat(f.target)
        settled = f.pnl is not None
        if target not in temp_cache.setdefault(f.icao, {}):
            temp_cache[f.icao][target] = temp_today(st, target)
        tnow, tmax = temp_cache[f.icao][target]
        # only mark against a live book; past-day markets are expired (settle instead)
        mark = (current_mark(session, mkt_cache, st, target, f.ticker, f.side)
                if not settled and target >= today else None)
        unreal = round(f.count * (mark - f.price), 2) if mark is not None else None
        positions.append({
            "icao": f.icao, "name": st.name, "lat": st.lat, "lon": st.lon,
            "target": f.target, "bucket": bucket(f), "lo": f.lo, "hi": f.hi,
            "side": f.side, "entry": f.price, "current": mark, "qty": f.count,
            "maker": f.maker, "settled": settled, "realized_high": f.realized,
            "pnl": f.pnl, "unrealized": unreal, "temp_now": tnow, "temp_max": tmax,
        })

    # per-station rollup for the map
    st_map = {}
    for p in positions:
        d = st_map.setdefault(p["icao"], {
            "icao": p["icao"], "name": p["name"], "lat": p["lat"], "lon": p["lon"],
            "temp_now": p["temp_now"], "temp_max": p["temp_max"],
            "open": 0, "closed": 0, "pnl": 0.0, "unrealized": 0.0,
        })
        if p["temp_now"] is not None:
            d["temp_now"], d["temp_max"] = p["temp_now"], p["temp_max"]
        if p["settled"]:
            d["closed"] += 1
            d["pnl"] += p["pnl"] or 0.0
        else:
            d["open"] += 1
            d["unrealized"] += p["unrealized"] or 0.0
    for d in st_map.values():
        d["pnl"] = round(d["pnl"], 2)
        d["unrealized"] = round(d["unrealized"], 2)

    # equity curve: cumulative realized P&L by settle day
    by_day = {}
    for f in led.fills:
        if f.pnl is not None:
            by_day[f.target] = by_day.get(f.target, 0.0) + f.pnl
    cum, equity = 0.0, []
    for d in sorted(by_day):
        cum += by_day[d]
        equity.append({"day": d, "cum": round(cum, 2)})

    open_stake = sum(f.count * f.price for f in led.fills if f.pnl is None)
    open_unreal = sum(p["unrealized"] or 0.0 for p in positions if not p["settled"])
    return {
        "summary": {
            "realized": s["realized_pnl"], "roi": s["roi"], "win_rate": s["win_rate"],
            "closed": s["closed"], "open": s["open"],
            "open_stake": round(open_stake, 2), "open_unrealized": round(open_unreal, 2),
        },
        "stations": sorted(st_map.values(), key=lambda x: x["icao"]),
        "positions": sorted(positions, key=lambda p: (not p["settled"], p["target"], p["icao"])),
        "equity": equity,
    }


def main():
    session = kalshi._session()
    mkt_cache, temp_cache = {}, {}
    today = date.today()
    envs = {
        "live": build_env(paper.Ledger(LIVE_LEDGER), session, mkt_cache, temp_cache, today),
        "paper": build_env(paper.Ledger(PAPER_LEDGER), session, mkt_cache, temp_cache, today),
    }
    data = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "bankroll": BANKROLL, "today": today.isoformat(), "active": stations.ACTIVE,
        "envs": envs,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT}  (live {len(envs['live']['positions'])} pos, "
          f"paper {len(envs['paper']['positions'])} pos)")


if __name__ == "__main__":
    main()
