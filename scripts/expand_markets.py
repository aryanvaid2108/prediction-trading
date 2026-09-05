"""Score candidate cities for activation, with the same evidence the live seven had.

Input: candidates.json (list of {series, icao, iem_id, name, lat, lon,
std_utc_offset, wfo, parity_matches, parity_total, live, ...}) produced by the
market-discovery research. Each candidate is registered at runtime (NOT in
wx/stations.py), snapshotted over the design + holdout windows with the live
configuration, and scored:
  parity      Kalshi settled result == NWS CLI high (>= 90%)
  total       P&L as-is and with fills capped at 25% of the hour's traded volume (> 0)
  brier       model beats the market mid on every priced bucket
  worst_day   > -$150
GO only when every gate passes. Nothing here activates a station; that is a
one-line edit to stations.ACTIVE after reading this table.

Usage: python -m scripts.expand_markets candidates.json   (writes .cache/expand_markets.csv)
"""
import json
import sys

import pandas as pd

from wx import stations, strategies
from wx.stations import Station
from scripts import honest_backtest as hb
from scripts.liquidity_check import rescore
from scripts.pressure_test import DESIGN, HOLDOUT, daily, max_drawdown


def register(c):
    st = Station(c["icao"], c["series"], c["iem_id"], c.get("name") or c.get("city") or c["icao"],
                 float(c["lat"]), float(c["lon"]), int(c["std_utc_offset"]), c.get("wfo", ""))
    stations.STATIONS[st.icao] = st
    return st


def score(c):
    st = register(c)
    recs = []
    for s, e in (DESIGN, HOLDOUT):
        recs += hb.load_snapshot(st.icao, s, e)[0]
    rows, kills, cal = hb.simulate(recs, strategies.CONTROL, calibration=True)
    vol = rescore(rows, "vol 25%")
    d_all = pd.concat([daily(rows, *DESIGN), daily(rows, *HOLDOUT)])
    d_vol = pd.concat([daily(vol, *DESIGN), daily(vol, *HOLDOUT)])
    cdf = pd.DataFrame(cal)
    bm = ((cdf["p_model"] - cdf["outcome"]) ** 2).mean() if len(cdf) else float("nan")
    bk = ((cdf["p_market"] - cdf["outcome"]) ** 2).mean() if len(cdf) else float("nan")
    parity = (c.get("parity_matches") or 0) / (c.get("parity_total") or 1)
    r = {"icao": st.icao, "series": st.kalshi, "name": st.name, "live": c.get("live"),
         "parity": round(parity, 2), "ticks": len(recs), "trades": len(rows),
         "win": round(sum(x["win"] for x in rows) / len(rows), 2) if rows else None,
         "total": round(d_all.sum(), 2), "total_vol25": round(d_vol.sum(), 2),
         "holdout": round(daily(rows, *HOLDOUT).sum(), 2),
         "median_day": round(d_all.median(), 2), "worst_day": round(d_all.min(), 2),
         "max_dd": round(max_drawdown(d_all), 2),
         "brier_model": round(bm, 4), "brier_market": round(bk, 4)}
    r["verdict"] = "GO" if (c.get("live") and parity >= 0.9 and r["total"] > 0 and r["total_vol25"] > 0
                            and bm < bk and r["worst_day"] > -150) else "no"
    return r


def main(path="candidates.json"):
    cands = json.loads(open(path).read())
    out = []
    for c in cands:
        if not c.get("icao") or not c.get("iem_id"):
            continue
        try:
            out.append(score(c))
            print(out[-1], flush=True)
        except Exception as e:
            print(f"{c.get('icao')}: ERROR {type(e).__name__}: {str(e)[:100]}", flush=True)
    t = pd.DataFrame(out)
    t.to_csv(".cache/expand_markets.csv", index=False)
    pd.set_option("display.width", 220)
    print("\n" + t.to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
