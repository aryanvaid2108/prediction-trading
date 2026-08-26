"""Layer-1 diagnostic: is our predicted daily high systematically below the CLI
value that actually settles? Splits the error into two measurable components:

  fbias = CLI_high - forecast_mu      (the model's own cool bias vs settlement)
  gap   = CLI_high - hourly_max       (the 1-min-vs-hourly data blind spot)

If both are positive and sum to ~+2F, that's the Day-1 loss explained and the
size of the correction we need.

Usage: python -m scripts.bias_diag [start] [end] [ICAO ...]
"""
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from wx import backtest, cli, obs, settlement, stations


def diag(ic, start, end):
    st = stations.get(ic)
    table, cols = backtest.build_archive_table_wide(st, start, end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45).dropna(subset=["mu", "realized"])
    fbias = scored["realized"].to_numpy() - scored["mu"].to_numpy()   # +ve => forecast too cool

    o = obs.fetch_asos(st.iem_id, start - timedelta(days=1), end + timedelta(days=2))
    hourly = settlement.daily_high(o, st.std_utc_offset).set_index("day")["high"]
    hourly.index = pd.to_datetime(hourly.index)
    try:
        cli_h = cli.settlement_high(st.icao, start, end)
    except Exception:
        cli_h = pd.Series(dtype=float)
    gap = (cli_h - hourly).dropna().to_numpy()                        # +ve => CLI hotter than hourly

    return {
        "ic": ic, "n": len(fbias),
        "fbias": float(np.mean(fbias)) if len(fbias) else float("nan"),
        "fbias_sd": float(np.std(fbias)) if len(fbias) else float("nan"),
        "gap_n": len(gap),
        "gap": float(np.mean(gap)) if len(gap) else float("nan"),
        "gap_sd": float(np.std(gap)) if len(gap) else float("nan"),
    }


def main(start=None, end=None, *icaos):
    end = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    start = date.fromisoformat(start) if start else end - timedelta(days=120)
    icaos = list(icaos) or stations.ACTIVE
    print(f"Layer-1 bias diagnostic  {start}..{end}\n")
    print(f"{'stn':5}{'n':>4}  {'fbias(CLI-μ)':>13}  {'gap(CLI-hourly)':>16}  {'TOTAL':>7}")
    rows = []
    for ic in icaos:
        try:
            d = diag(ic, start, end)
        except Exception as e:
            print(f"{ic:5} ERROR {type(e).__name__}: {str(e)[:50]}")
            continue
        total = d["fbias"] + d["gap"]
        rows.append(d)
        print(f"{d['ic']:5}{d['n']:>4}  {d['fbias']:+6.2f} ±{d['fbias_sd']:.2f}  "
              f"{d['gap']:+6.2f} ±{d['gap_sd']:.2f} (n={d['gap_n']})  {total:+6.2f}")
    if rows:
        mf = np.mean([r["fbias"] for r in rows]); mg = np.mean([r["gap"] for r in rows])
        print(f"\n  mean forecast cool-bias: {mf:+.2f}F   mean hourly-vs-CLI gap: {mg:+.2f}F   "
              f"=> total cool bias ~{mf+mg:+.2f}F")
        print("  (+ve = we predict/observe COLDER than what settles — bet buckets too low)")


if __name__ == "__main__":
    main(*sys.argv[1:])
