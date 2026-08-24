"""Show intraday sharpening: CRPS of the daily-high forecast as the day fills in.

Usage: python -m scripts.intraday_demo [ICAO] [start] [end]
"""
import sys
from datetime import date

from wx import intraday, obs
from wx.stations import get

HOURS = [8, 10, 12, 14, 16, 18]


def main(icao="KNYC", start="2025-05-01", end="2025-09-01"):
    st = get(icao)
    o = obs.fetch_asos(st.iem_id, date.fromisoformat(start), date.fromisoformat(end))
    res = intraday.backtest_by_hour(o, st.std_utc_offset, HOURS)
    print(f"{icao} ({st.name})  {start}..{end}\n")
    print("Daily-high CRPS by observation cutoff (lower = sharper):")
    print("  morning EMOS baseline ~ 0.9-1.0 F  (from scripts.backtest_demo)\n")
    print(res.to_string(index=False))
    print("\n  crps_intraday : model = observed-max-so-far + climatological residual")
    print("  crps_naive_floor: assume high = observed max so far (no more warming)")
    print("  mean_residual : avg further warming after that hour (F)")


if __name__ == "__main__":
    main(*sys.argv[1:])
