"""Quantify settlement basis risk: ASOS reconstruction vs official NWS CLI high.

Usage: python -m scripts.cli_check [ICAO] [start] [end]
"""
import sys
from datetime import date, timedelta

import pandas as pd

from wx import cli, obs, settlement
from wx.stations import get


def main(icao="KNYC", start="2025-05-01", end="2025-08-15"):
    st = get(icao)
    s, e = date.fromisoformat(start), date.fromisoformat(end)

    o = obs.fetch_asos(st.iem_id, s, e + timedelta(days=1))
    recon = settlement.daily_high(o, st.std_utc_offset)[["day", "high"]].rename(columns={"high": "recon"})
    official = cli.fetch_range(icao, s, e)[["day", "cli_high"]]

    recon["day"] = pd.to_datetime(recon["day"])
    official["day"] = pd.to_datetime(official["day"])
    df = official.merge(recon, on="day").dropna()
    df["diff"] = df["recon"] - df["cli_high"]

    n = len(df)
    exact = (df["diff"] == 0).mean()
    within1 = (df["diff"].abs() <= 1).mean()
    print(f"{icao} ({st.name})  {start}..{end}   {n} days compared\n")
    print(f"  reconstruction == official CLI : {exact:.1%}")
    print(f"  within 1F                      : {within1:.1%}")
    print(f"  mean abs diff                  : {df['diff'].abs().mean():.2f} F")
    print(f"  bias (recon - CLI)             : {df['diff'].mean():+.2f} F")
    mism = df[df["diff"] != 0]
    if len(mism):
        print(f"\n  {len(mism)} mismatch days (sample):")
        for _, r in mism.head(8).iterrows():
            print(f"    {r['day'].date()}  recon={int(r['recon'])}  CLI={int(r['cli_high'])}  diff={int(r['diff']):+d}")
    print("\n  Implication: near a bucket boundary, trust the edge less by ~this mismatch rate.")


if __name__ == "__main__":
    main(*sys.argv[1:])
