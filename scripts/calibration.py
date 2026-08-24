"""Reliability / tail-calibration check.

For each walk-forward day we form Kalshi-style threshold events ("high >= T")
across a grid of T, take the model's probability and the realized yes/no, then
bin by predicted probability. Well-calibrated => predicted ~= observed. The tail
bins answer: "when the model says ~90%, does it happen ~90%?"

Usage: python -m scripts.calibration [start] [end] [ICAO ...]
"""
import sys
from datetime import date

import numpy as np
from scipy.stats import norm

from wx import backtest, stations


def pairs_for(icao, start, end):
    st = stations.get(icao)
    table, cols = backtest.build_archive_table_wide(st, start, end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    ps, ys = [], []
    for _, r in scored.iterrows():
        mu, sigma, hi = r["mu"], r["sigma"], r["realized"]
        for T in range(int(round(mu)) - 6, int(round(mu)) + 7):
            p = 1.0 - norm.cdf((T - 0.5 - mu) / sigma)   # P(high >= T)
            ps.append(p)
            ys.append(1.0 if hi >= T else 0.0)
    return np.array(ps), np.array(ys)


def main(start="2025-03-01", end="2025-08-20", *icaos):
    icaos = list(icaos) or stations.ACTIVE
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    P, Y = [], []
    for ic in icaos:
        try:
            p, y = pairs_for(ic, s, e)
            P.append(p); Y.append(y)
            print(f"  {ic}: {len(p)} threshold-events")
        except Exception as ex:
            print(f"  {ic}: skipped ({ex})")
    P, Y = np.concatenate(P), np.concatenate(Y)
    print(f"\nTotal events: {len(P)}  (across {len(icaos)} stations, {start}..{end})")
    print("Note: threshold events within a day are correlated, so treat error bars as wider than n implies.\n")

    print(f"{'predicted bin':>14} {'n':>7} {'mean pred':>10} {'observed':>9} {'gap':>7}")
    for lo in np.arange(0, 1.0, 0.1):
        hi = lo + 0.1
        m = (P >= lo) & (P < hi) if hi < 1 else (P >= lo) & (P <= 1.0)
        if m.sum() == 0:
            continue
        mp, ob = P[m].mean(), Y[m].mean()
        print(f"  {lo:.1f}-{hi:.1f}      {m.sum():>7} {mp:>10.1%} {ob:>9.1%} {ob-mp:>+7.1%}")

    print("\n--- the tails (what the strategy trades on) ---")
    for thr in (0.80, 0.90, 0.95):
        m = P >= thr
        if m.sum():
            print(f"  model said >= {thr:.0%}:  {m.sum():>6} events,  actually happened {Y[m].mean():.1%}"
                  f"   (mean pred {P[m].mean():.1%})")
    for thr in (0.20, 0.10, 0.05):
        m = P <= thr
        if m.sum():
            print(f"  model said <= {thr:.0%}:  {m.sum():>6} events,  actually happened {Y[m].mean():.1%}"
                  f"   (mean pred {P[m].mean():.1%})")


if __name__ == "__main__":
    main(*sys.argv[1:])
