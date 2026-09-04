"""Pressure test: how fragile is the live configuration?

Replays strategy variants over the quote snapshots (scripts.honest_backtest)
with the live selector, one parameter at a time, and reports for each:
  design window  Jul 1 - Aug 24 (the filters were tuned here)
  HOLDOUT        Aug 25 - Sep 3 (never seen while designing the rules)
  bootstrap      resample calendar days 4000x -> 90% CI on total P&L and P(total > 0)
  max drawdown   worst peak-to-trough of cumulative daily P&L
A variant earns a paper arm only if it is not worse in the holdout AND its
bootstrap does not look worse than control's — the sweep is for finding fragility,
not for picking the best in-sample number.

Usage: python -m scripts.pressure_test            (writes .cache/pressure_test.csv)
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from wx import stations, strategies
from scripts import honest_backtest as hb

DESIGN = (date(2026, 7, 1), date(2026, 8, 24))
HOLDOUT = (date(2026, 8, 25), date.today() - timedelta(days=1))   # grows every week
C = strategies.CONTROL
A = strategies.Arm

CONFIGS = [
    ("control (live)",     dict(arm=C)),
    ("old rules (pre-Aug27)", dict(arm=hb.OLD_RULES)),
    ("model_w 0.25",       dict(arm=A("w025", model_weight=0.25))),
    ("model_w 0.75",       dict(arm=A("w075", model_weight=0.75))),
    ("model_w 1.0",        dict(arm=A("w1", model_weight=1.0))),
    ("min_price 0.10",     dict(arm=A("mp10", min_price=0.10))),
    ("min_price 0.20",     dict(arm=A("mp20", min_price=0.20))),
    ("min_price 0.25",     dict(arm=A("mp25", min_price=0.25))),
    ("ratio_cap 2.0",      dict(arm=A("rc2", ratio_cap=2.0))),
    ("ratio_cap 3.0",      dict(arm=A("rc3", ratio_cap=3.0))),
    ("ratio_cap off",      dict(arm=A("rcoff", ratio_cap=None))),
    ("min_edge 0.03",      dict(arm=A("me03", min_edge=0.03))),
    ("min_edge 0.08",      dict(arm=A("me08", min_edge=0.08))),
    ("no robust gate",     dict(arm=A("nogate", robust_delta=0.0))),
    ("robust_delta 1.0",   dict(arm=A("rd1", robust_delta=1.0))),
    ("robust_delta 2.0",   dict(arm=A("rd2", robust_delta=2.0))),
    ("gate w/o toward",    dict(arm=A("notoward", toward_market=False))),
    ("ticks 15Z only",     dict(arm=C, ticks=(15,))),
    ("ticks 15Z+17Z",      dict(arm=C, ticks=(15, 17))),
    ("ticks 17Z+19Z",      dict(arm=C, ticks=(17, 19))),
    ("slip 2c",            dict(arm=C, slip=0.02)),
    ("fantasy fills",      dict(arm=C, depth=False)),
    ("kelly 0.125",        dict(arm=C, kelly=0.125)),
    ("bench KAUS",         dict(arm=C, exclude=("KAUS",))),
    ("bench KLAX",         dict(arm=C, exclude=("KLAX",))),
    ("bench KSFO",         dict(arm=C, exclude=("KSFO",))),
]


def daily(rows, start, end):
    s = pd.DataFrame(rows).groupby("day")["pnl"].sum() if rows else pd.Series(dtype=float)
    return s.reindex(pd.date_range(start, end), fill_value=0.0)


def max_drawdown(d):
    cum = d.cumsum()
    return float((cum - cum.cummax()).min())


def bootstrap(d, n=4000, seed=7):
    rng = np.random.default_rng(seed)
    v = d.to_numpy()
    tot = rng.choice(v, (n, len(v)), replace=True).sum(axis=1)
    return float(np.percentile(tot, 5)), float(np.percentile(tot, 95)), float((tot > 0).mean())


def run(recs, cfg):
    kw = dict(cfg)
    rows, _, _ = hb.simulate(recs, kw.pop("arm"), **kw)
    return rows


def main():
    recs = []
    for ic in stations.ACTIVE:
        for s, e in (DESIGN, HOLDOUT):
            r, _ = hb.load_snapshot(ic, s, e)
            recs += r
    out = []
    for name, cfg in CONFIGS:
        rows = run(recs, cfg)
        df = pd.DataFrame(rows)
        dd = daily(rows, DESIGN[0], DESIGN[1])
        dh = daily(rows, HOLDOUT[0], HOLDOUT[1])
        dall = pd.concat([dd, dh])
        lo, hi, p_pos = bootstrap(dall)
        des = df[df["day"] <= pd.Timestamp(DESIGN[1])] if len(df) else df
        hol = df[df["day"] >= pd.Timestamp(HOLDOUT[0])] if len(df) else df
        out.append({
            "config": name, "trades": len(df),
            "design_pnl": round(dd.sum(), 2), "design_win": round(des["win"].mean(), 2) if len(des) else None,
            "holdout_trades": len(hol), "holdout_pnl": round(dh.sum(), 2),
            "total": round(dall.sum(), 2), "ci5": round(lo, 0), "ci95": round(hi, 0), "p_pos": round(p_pos, 2),
            "worst_day": round(dall.min(), 2), "max_dd": round(max_drawdown(dall), 2),
            "jul": round(dd[dd.index.month == 7].sum(), 2), "aug": round(dd[dd.index.month == 8].sum(), 2),
        })
    t = pd.DataFrame(out)
    t.to_csv(hb.CACHE.parent / "pressure_test.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"design {DESIGN[0]}..{DESIGN[1]}  holdout {HOLDOUT[0]}..{HOLDOUT[1]}  "
          f"bankroll ${hb.BANKROLL:.0f}  depth-aware fills unless noted\n")
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
