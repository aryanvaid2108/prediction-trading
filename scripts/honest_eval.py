"""Validate the honest-uncertainty fix BEFORE wiring it into the live quote.

Head-to-head, walk-forward, per station and decision hour:
  OLD: precision-weighted Gaussian blend of prior + intraday (what trades today).
       Multiplying precisions assumes independent evidence -> fake certainty.
  NEW: mixture of prior samples and empirical intraday residual samples, floored
       at the observed max. Mixture variance >= component spread, and prior-vs-obs
       disagreement WIDENS it. Same information, honest uncertainty.

Metrics: std(z) (want ~1), 90%-coverage (want ~90%), mean CRPS (want <= old).
Usage: python -m scripts.honest_eval [start] [end] [ICAO ...]
"""
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from wx import backtest, cli, intraday, obs, stations
from wx.emos import crps_gaussian, crps_samples

HOURS = [9, 11, 13]          # LST decision hours: morning / midday / afternoon
N_MIX = 600                  # mixture sample count
BW = 0.25                    # jitter for integer-degree discreteness


def eval_station(ic, start, end, rng):
    st = stations.get(ic)
    table, cols = backtest.build_archive_table_wide(st, start, end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    calib = backtest.calibration_factor(scored)
    scored = scored.set_index(pd.to_datetime(scored["day"]))
    o = obs.fetch_asos(st.iem_id, start - timedelta(days=1), end + timedelta(days=2))
    prep = intraday.prep(o, st.std_utc_offset, HOURS)
    prep = prep.assign(day=pd.to_datetime(prep["day"])).set_index("day")
    finals = cli.settlement_high(st.icao, start, end)

    rows = []
    zhist = {h: [] for h in HOURS}    # trailing raw z's -> walk-forward sharpening
    for d in sorted(scored.index.intersection(prep.index).intersection(finals.index)):
        mu0 = float(scored.loc[d, "mu"])
        s0 = float(scored.loc[d, "sigma"]) * calib
        y = float(finals.loc[d])
        for h in HOURS:
            rm = prep.loc[d, f"rm_{h}"]
            if pd.isna(rm):
                continue
            hist = prep.loc[prep.index < d, f"rm_{h}"].dropna().tail(60)
            res = (finals.reindex(hist.index) - hist).dropna().to_numpy()
            if len(res) < 20:
                continue
            obs_max = float(rm)

            # --- OLD: precision-weighted Gaussian blend (mirrors quote_live) ---
            mu_i = obs_max + float(res.mean())
            s_i = max(float(res.std()), 0.3)
            w0, wi = 1.0 / s0**2, 1.0 / s_i**2
            mu_b = (w0 * mu0 + wi * mu_i) / (w0 + wi)
            s_b = float(np.sqrt(1.0 / (w0 + wi)))
            z_old = (y - mu_b) / s_b
            c_old = crps_gaussian(y, mu_b, s_b)

            # --- NEW: mixture, floored at observed max ---
            n0 = int(round(N_MIX * w0 / (w0 + wi)))
            prior_s = rng.normal(mu0, s0, n0)
            intra_s = obs_max + rng.choice(res, N_MIX - n0, replace=True)
            # settlement rounds to whole degrees, so the true floor is round(obs)-0.5
            raw = np.concatenate([prior_s, intra_s]) + rng.normal(0, BW, N_MIX)

            def finish(s):
                s = np.clip(s, round(obs_max) - 0.5, None)
                pit = float(np.clip((s < y).mean(), 1e-4, 1 - 1e-4))
                return float(norm.ppf(pit)), crps_samples(s, y), float(s.std())

            z_raw, _, _ = finish(raw)
            # walk-forward sharpening: scale spread by trailing std(raw z), past only
            zh = zhist[h]
            # 1.1 safety bias: when calibration is uncertain, err wider (never tighter)
            shrink = float(np.clip(np.std(zh) * 1.1, 0.7, 1.3)) if len(zh) >= 25 else 1.0
            zh.append(z_raw)
            adj = raw.mean() + (raw - raw.mean()) * shrink
            z_new, c_new, sd_new = finish(adj)

            rows.append({"ic": ic, "h": h, "tuned": len(zh) > 25, "z_old": z_old,
                         "z_new": z_new, "c_old": c_old, "c_new": c_new,
                         "sd_old": s_b, "sd_new": sd_new})
    return pd.DataFrame(rows)


def main(start=None, end=None, *icaos):
    end = date.fromisoformat(end) if end else date(2026, 8, 24)
    start = date.fromisoformat(start) if start else end - timedelta(days=120)
    icaos = list(icaos) or stations.ACTIVE
    rng = np.random.default_rng(11)
    frames = []
    for ic in icaos:
        try:
            df = eval_station(ic, start, end, rng)
            frames.append(df)
            print(f"{ic}: {len(df)} station-day-hours evaluated")
        except Exception as e:
            print(f"{ic}: ERROR {type(e).__name__}: {str(e)[:60]}")
    a = pd.concat(frames, ignore_index=True)
    a = a[a["tuned"]]        # evaluate only where the walk-forward sharpening was active
    print(f"(evaluating {len(a)} tuned station-day-hours; warmup excluded)")

    def cov90(z):
        return float((np.abs(z) <= 1.645).mean())

    print(f"\n{'':14}{'std(z)':>16}   {'cov@90%':>15}   {'mean CRPS':>14}   {'mean σ':>12}")
    print(f"{'':14}{'old':>8}{'new':>8}   {'old':>7}{'new':>8}   {'old':>7}{'new':>7}   {'old':>6}{'new':>6}")
    for h in HOURS:
        g = a[a["h"] == h]
        print(f"hour {h:>2} LST  {g['z_old'].std():>8.2f}{g['z_new'].std():>8.2f}   "
              f"{cov90(g['z_old']):>7.0%}{cov90(g['z_new']):>8.0%}   "
              f"{g['c_old'].mean():>7.2f}{g['c_new'].mean():>7.2f}   "
              f"{g['sd_old'].mean():>6.2f}{g['sd_new'].mean():>6.2f}")
    print(f"{'ALL':<11}  {a['z_old'].std():>8.2f}{a['z_new'].std():>8.2f}   "
          f"{cov90(a['z_old']):>7.0%}{cov90(a['z_new']):>8.0%}   "
          f"{a['c_old'].mean():>7.2f}{a['c_new'].mean():>7.2f}   "
          f"{a['sd_old'].mean():>6.2f}{a['sd_new'].mean():>6.2f}")
    print("\nper station (all hours):")
    for ic in icaos:
        g = a[a["ic"] == ic]
        if not len(g):
            continue
        print(f"  {ic}: std(z) {g['z_old'].std():.2f}->{g['z_new'].std():.2f}  "
              f"cov90 {cov90(g['z_old']):.0%}->{cov90(g['z_new']):.0%}  "
              f"CRPS {g['c_old'].mean():.2f}->{g['c_new'].mean():.2f}")
    print("\n(target: std(z)≈1.0, cov90≈90%, CRPS no worse — sharpness honestly priced)")


if __name__ == "__main__":
    main(*sys.argv[1:])
