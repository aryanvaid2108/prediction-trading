"""Model-side sweep: retrain window, archive model set, walk-forward bias
correction, PIT-shrink clip. Each variant re-quotes every station/tick with the
live selector (CONTROL arm) and is scored on Brier vs the market by tick, PIT
coverage, and P&L as-is / under 25%-of-volume fill caps, design + holdout.

Usage: python -m scripts.model_sweep [ICAO ...]   (writes .cache/model_sweep.csv)
"""
import sys

import numpy as np
import pandas as pd

from wx import stations, strategies
from scripts import honest_backtest as hb
from scripts.liquidity_check import rescore
from scripts.pressure_test import DESIGN, HOLDOUT, daily

BASE = hb.BASE_VARIANT["models"]
VARIANTS = [
    ("base (live quote)",      {}),
    ("window 30",              {"window": 30}),
    ("window 60",              {"window": 60}),
    ("window 90",              {"window": 90}),
    ("bias 30d",               {"bias_days": 30}),
    ("bias 60d",               {"bias_days": 60}),
    ("shrink clip 0.5-1.6",    {"shrink_clip": (0.5, 1.6)}),
    ("+ukmo",                  {"models": hb.backtest.fetch_members_archive.__defaults__[0] + ",ukmo_seamless"}),
    ("+nbm",                   {"models": hb.backtest.fetch_members_archive.__defaults__[0] + ",ncep_nbm_conus"}),
    ("+hrrr",                  {"models": hb.backtest.fetch_members_archive.__defaults__[0] + ",ncep_hrrr_conus"}),
    ("+aifs+graphcast",        {"models": hb.backtest.fetch_members_archive.__defaults__[0] + ",ecmwf_aifs025_single,gfs_graphcast025"}),
]


def coverage(recs):
    """Fraction of outcomes inside the quote's central 90% (target 0.90)."""
    inside, n = 0, 0
    for r in recs:
        if r["samples"] is None:
            continue
        lo, hi = np.percentile(r["samples"], [5, 95])
        inside += lo - 0.5 <= r["y"] <= hi + 0.5; n += 1
    return round(inside / n, 3) if n else None


def score(name, variant, icaos):
    recs = []
    for ic in icaos:
        for s, e in (DESIGN, HOLDOUT):
            recs += hb.load_snapshot(ic, s, e, variant=variant)[0]
    rows, _, cal = hb.simulate(recs, strategies.CONTROL, calibration=True)
    vol = rescore(rows, "vol 25%")
    c = pd.DataFrame(cal)
    out = {"variant": name, "ticks": len(recs), "coverage90": coverage(recs), "trades": len(rows),
           "total": round(daily(rows, *DESIGN).sum() + daily(rows, *HOLDOUT).sum(), 2),
           "total_vol25": round(daily(vol, *DESIGN).sum() + daily(vol, *HOLDOUT).sum(), 2),
           "holdout": round(daily(rows, *HOLDOUT).sum(), 2)}
    for h, g in c.groupby("hour_utc"):
        bm = ((g["p_model"] - g["outcome"]) ** 2).mean(); bk = ((g["p_market"] - g["outcome"]) ** 2).mean()
        out[f"lead_{h}Z"] = round(bk - bm, 4)          # + = model beats market
    return out


def main(*icaos):
    icaos = list(icaos) or stations.ACTIVE
    out = []
    for name, v in VARIANTS:
        try:
            out.append(score(name, v, icaos))
            print(out[-1], flush=True)
        except Exception as e:
            print(f"{name}: ERROR {type(e).__name__}: {str(e)[:120]}", flush=True)
    t = pd.DataFrame(out)
    t.to_csv(".cache/model_sweep.csv", index=False)
    pd.set_option("display.width", 220)
    print("\n" + t.to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
