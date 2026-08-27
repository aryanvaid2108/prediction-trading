"""Fix 07 proof: walk-forward CRPS of the intraday residual pool, unconditional
vs flow-conditioned (same window, same days — the pool is the ONLY difference).

For each day and tick hour: predictive = running_max + residual_pool, scored
against the official CLI high. Conditioning selects the residuals of days whose
morning→cutoff climb sat in today's tercile.

Usage: python -m scripts.flow_eval [start] [end] [ICAO ...]
"""
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from wx import cli, intraday, obs, stations
from wx.emos import crps_samples

TICKS_UTC = [15, 17, 19]
EARLY = 9
WINDOW = 60
MIN_TRAIN = 45


def run_station(ic, start, end):
    st = stations.get(ic)
    hours = sorted({EARLY} | {h + st.std_utc_offset for h in TICKS_UTC})
    o = obs.fetch_asos(st.iem_id, start - timedelta(days=WINDOW + MIN_TRAIN), end + timedelta(days=1))
    tab = intraday.prep(o, st.std_utc_offset, hours)
    tab["day"] = pd.to_datetime(tab["day"])
    tab = tab.set_index("day")
    finals = cli.settlement_high(st.icao, start - timedelta(days=WINDOW + MIN_TRAIN), end)
    rows = []
    for h_utc in TICKS_UTC:
        h = h_utc + st.std_utc_offset
        rm, rme = tab[f"rm_{h}"], tab[f"rm_{EARLY}"]
        res_all = (finals - rm).dropna()
        climbs = (rm - rme).dropna()
        days = [d for d in res_all.index if d >= pd.Timestamp(start)]
        for d in days:
            past_res = res_all[res_all.index < d].tail(WINDOW)
            past_climbs = climbs.reindex(past_res.index)
            if len(past_res) < MIN_TRAIN or d not in climbs.index or pd.isna(rm.loc[d]):
                continue
            today_climb = float(climbs.loc[d])
            y, base = float(finals.loc[d]), float(rm.loc[d])
            pool_u = past_res.to_numpy()
            pool_c = intraday.flow_pool(past_res, past_climbs, today_climb).to_numpy()
            rows.append({
                "icao": ic, "hour_utc": h_utc, "day": d,
                "conditioned": len(pool_c) < len(pool_u),
                "crps_u": crps_samples(base + pool_u, y),
                "crps_c": crps_samples(base + pool_c, y),
                "bias_u": float(base + pool_u.mean() - y),
                "bias_c": float(base + pool_c.mean() - y),
            })
    return pd.DataFrame(rows)


def main(start="2026-07-01", end="2026-08-24", *icaos):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    frames = []
    for ic in (list(icaos) or stations.ACTIVE):
        try:
            df = run_station(ic, s, e)
            frames.append(df)
            g = df.groupby("hour_utc")[["crps_u", "crps_c"]].mean()
            gain = (1 - g["crps_c"] / g["crps_u"]) * 100
            line = "  ".join(f"{h:02d}Z {u:.3f}->{c:.3f} ({p:+.1f}%)"
                             for h, u, c, p in zip(g.index, g["crps_u"], g["crps_c"], gain))
            print(f"{ic}: n={len(df)}  cond={df['conditioned'].mean():.0%}  {line}")
        except Exception as ex:
            print(f"{ic}: ERROR {type(ex).__name__}: {str(ex)[:60]}")
    if not frames:
        return
    a = pd.concat(frames)
    u, c = a["crps_u"].mean(), a["crps_c"].mean()
    print(f"\nALL ({len(a)} day-ticks): CRPS {u:.3f} -> {c:.3f}  ({(1 - c / u) * 100:+.1f}%)  "
          f"|bias| {a['bias_u'].abs().mean():.3f} -> {a['bias_c'].abs().mean():.3f}")
    # the failure mode that motivated this: fast-warming days (top climb tercile)
    fast = a[a["conditioned"]]
    print(f"conditioned subset ({len(fast)}): CRPS {fast['crps_u'].mean():.3f} -> "
          f"{fast['crps_c'].mean():.3f}  bias {fast['bias_u'].mean():+.3f} -> {fast['bias_c'].mean():+.3f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
