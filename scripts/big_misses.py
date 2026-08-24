"""Print the days where our forecast missed most (and least), for the AFD study."""
import sys
from datetime import date

from wx import backtest, stations


def main(ic="KNYC", start="2026-05-20", end="2026-08-22"):
    st = stations.get(ic)
    table, cols = backtest.build_archive_table_wide(st, date.fromisoformat(start), date.fromisoformat(end))
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    scored["miss"] = scored["realized"] - scored["mu"]
    scored["abs"] = scored["miss"].abs()
    s = scored.sort_values("abs", ascending=False)
    print("BIG MISSES (forecast wrong):")
    for _, r in s.head(8).iterrows():
        print(f"  {r['day'].date()}  mu={r['mu']:.1f}  actual={r['realized']:.0f}  miss={r['miss']:+.1f}")
    print("SMALL MISSES (forecast right — controls):")
    for _, r in s.tail(3).iterrows():
        print(f"  {r['day'].date()}  mu={r['mu']:.1f}  actual={r['realized']:.0f}  miss={r['miss']:+.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
