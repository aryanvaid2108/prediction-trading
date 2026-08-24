"""Backtest EMOS on the real GEFS reforecast ensemble (next-day high).

Checks the thing the 6-model proxy failed at: does a real ensemble spread make
the EMOS variance slope (d) informative, and improve calibration?

Usage: python -m scripts.gefs_backtest [ICAO] [start] [end] [lead_days]
"""
import sys
from datetime import date, timedelta

import pandas as pd

from wx import backtest, emos, gefs, obs, settlement
from wx.stations import get


def main(icao="KNYC", start="2019-06-01", end="2019-08-25", lead_days="1"):
    st = get(icao)
    lead = int(lead_days)
    s, e = date.fromisoformat(start), date.fromisoformat(end)

    o = obs.fetch_asos(st.iem_id, s, e + timedelta(days=lead + 2))
    realized = settlement.daily_high(o, st.std_utc_offset).set_index("day")["high"]

    cache = gefs._load_cache()
    rows, init = [], s
    n = 0
    while init <= e:
        target = init + timedelta(days=lead)
        try:
            f = gefs.features(init, st.lat, st.lon, st.std_utc_offset, target, cache)
            r = realized.get(target)
            if r is not None and f["n_members"] >= 3:
                rows.append({"day": pd.Timestamp(target), "ens_mean": f["ens_mean"],
                             "ens_std": f["ens_std"], "high": float(r)})
        except Exception as ex:
            print(f"  skip {init}: {ex}")
        n += 1
        if n % 10 == 0:
            gefs._save_cache(cache)
            print(f"  ...ingested {n} inits (through {init})")
        init += timedelta(days=1)
    gefs._save_cache(cache)

    table = pd.DataFrame(rows).sort_values("day").reset_index(drop=True)
    print(f"\n{icao} GEFS reforecast, lead {lead}d: {len(table)} paired days")

    scored = backtest.rolling_score(table, min_train=35, window=40)
    print("Walk-forward:", backtest.summarize(scored))

    m = emos.fit(table["ens_mean"], table["ens_std"], table["high"])
    print(f"Full-sample EMOS: mu = {m.a:.2f} + {m.b:.2f}*mean, var = {m.c:.2f} + {m.d:.3f}*spread^2")
    corr = table["ens_std"].corr((table["ens_mean"] - table["high"]).abs())
    print(f"spread-vs-abs-error corr: {corr:.3f}  (positive => spread carries real signal)")


if __name__ == "__main__":
    main(*sys.argv[1:])
