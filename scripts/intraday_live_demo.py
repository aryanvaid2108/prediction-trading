"""Show the live quote sharpening through the day on a past date, vs the truth.

Usage: python -m scripts.intraday_live_demo [ICAO] [YYYY-MM-DD]
"""
import sys
from datetime import date, datetime, timezone, timedelta

from wx import obs, pipeline, settlement
from wx.stations import get


def main(icao="KNYC", day="2025-07-15"):
    st = get(icao)
    tgt = date.fromisoformat(day)
    off = st.std_utc_offset

    o = obs.fetch_asos(st.iem_id, tgt, tgt + timedelta(days=1))
    dh = settlement.daily_high(o, off)
    actual = int(dh[dh["day"] == tgt]["high"].iloc[0])

    print(f"{icao} ({st.name})  {tgt}   ACTUAL settled high = {actual}F\n")
    print(f"{'time':>10} {'obs_max':>7} {'predictive':>15} {'P(=actual)':>10} {'P(<=77)':>8} {'MAE':>5}")
    for lst_hr in [7, 9, 11, 13, 15, 17]:
        now = datetime(tgt.year, tgt.month, tgt.day, (lst_hr - off) % 24, 30, tzinfo=timezone.utc)
        q = pipeline.quote_live(st, target=tgt, now_utc=now)
        om = f"{q.observed_max:.0f}" if q.observed_max is not None else "--"
        p_hit = q.prob_fn(actual, actual)
        print(f"{lst_hr:02d}:00 LST {om:>7} {'N(%.1f,%.2f)' % (q.mu, q.sigma):>15} "
              f"{p_hit:10.1%} {q.prob_fn(None, 77):8.1%} {abs(q.mu - actual):5.1f}")
    print("\n  The predictive tightens and its mean tracks the truth as observations arrive;")
    print("  P(=actual) rises, stale buckets go to ~0. This is the intraday trading edge.")


if __name__ == "__main__":
    main(*sys.argv[1:])
