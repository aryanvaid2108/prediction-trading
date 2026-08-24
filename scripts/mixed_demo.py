"""Compare single-predictor EMOS vs Mixed EMOS (learned per-model weights).

Usage: python -m scripts.mixed_demo [ICAO] [start] [end]
"""
import sys
from datetime import date

from wx import backtest
from wx.stations import get


def main(icao="KNYC", start="2025-03-01", end="2025-08-15"):
    st = get(icao)
    table, model_cols = backtest.build_archive_table_wide(st, date.fromisoformat(start), date.fromisoformat(end))
    print(f"{icao} ({st.name})  {start}..{end}  days={len(table)}  models={[c.split('_')[0] for c in model_cols]}\n")

    single = backtest.summarize(backtest.rolling_score(table, min_train=45, window=45))
    mixed = backtest.summarize(backtest.rolling_score_mixed(table, model_cols, min_train=45, window=45, ridge=1.0))

    def row(name, s):
        return (f"  {name:14} CRPS={s['crps_emos']:.3f}  improve={s['crps_improvement_pct']:>5}%  "
                f"MAE={s['mae_emos']:.2f}  PITmean={s['pit_mean']:.2f}  PITstd={s['pit_std']:.2f}  n={s['n']}")

    print("Walk-forward (vs raw equal-weight ensemble):")
    print(row("single EMOS", single))
    print(row("Mixed EMOS", mixed))
    d = single["crps_emos"] - mixed["crps_emos"]
    print(f"\n  Mixed EMOS beats single EMOS by {100 * d / single['crps_emos']:.1f}% CRPS "
          f"({single['crps_emos']:.3f} -> {mixed['crps_emos']:.3f})")


if __name__ == "__main__":
    main(*sys.argv[1:])
