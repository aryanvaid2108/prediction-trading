"""Real walk-forward backtest: multi-model EMOS vs raw ensemble, per station.

Usage: python -m scripts.backtest_demo [ICAO] [start] [end]
"""
import sys
from datetime import date

from wx import backtest
from wx.stations import get


def main(icao="KNYC", start="2025-03-01", end="2025-08-15"):
    st = get(icao)
    table = backtest.build_archive_table(st, date.fromisoformat(start), date.fromisoformat(end))
    print(f"{icao} ({st.name})  {start}..{end}  days with forecast+realized: {len(table)}")

    scored = backtest.rolling_score(table, min_train=30)
    s = backtest.summarize(scored)
    print("\nWalk-forward summary:")
    for k, v in s.items():
        print(f"  {k:22} {v}")

    if not scored.empty:
        hit = (scored["mu"].round() == scored["realized"]).mean()
        within1 = ((scored["mu"].round() - scored["realized"]).abs() <= 1).mean()
        print(f"  exact_degree_hit_rate  {hit:.1%}")
        print(f"  within_1F_rate         {within1:.1%}")
        print("\n  Calibration check (PIT mean~0.5, std~0.29 if well calibrated).")


if __name__ == "__main__":
    main(*sys.argv[1:])
