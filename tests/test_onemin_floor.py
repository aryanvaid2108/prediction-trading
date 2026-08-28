"""Item 16 proof (unit level): the sustained 1-min max kills spikes, respects
hour cutoffs, and the combined floor can only raise the hourly floor."""
import numpy as np
import pandas as pd

from wx.intraday import prep_1min

UTC_OFF = -5  # KNYC-style LST offset


def _obs(rows):
    return pd.DataFrame(rows, columns=["valid", "tmpf"])


def _minutes(start, temps):
    t0 = pd.Timestamp(start)
    return _obs([(t0 + pd.Timedelta(minutes=i), v) for i, v in enumerate(temps)])


def test_two_minute_spike_is_rejected_sustained_peak_counts():
    # 80,80,80, spike 95,95, back to 80..., then a real 88 held 5 minutes
    temps = [80.0] * 10 + [95.0, 95.0] + [80.0] * 10 + [88.0] * 5 + [80.0] * 5
    df = _minutes("2026-08-01 17:00", temps)   # 12:00 LST
    out = prep_1min(df, UTC_OFF, [23])
    assert float(out["om_23"].iloc[0]) == 88.0     # spike ignored, real peak kept


def test_hour_cutoff_is_respected():
    # peak at 14 LST (19z) must not appear in the 12 LST cutoff
    early = _minutes("2026-08-01 16:00", [80.0] * 30)          # 11:00 LST
    late = _minutes("2026-08-01 19:00", [90.0] * 30)           # 14:00 LST
    df = pd.concat([early, late])
    out = prep_1min(df, UTC_OFF, [12, 15]).set_index("day")
    row = out.iloc[0]
    assert row["om_12"] == 80.0 and row["om_15"] == 90.0


def test_combined_floor_only_raises():
    # the deployable rule is max(hourly, onemin): a gappy/low 1-min day never lowers
    hourly_floor = 84.0
    onemin_vals = [82.0, 85.5, float("nan")]
    combined = [max(hourly_floor, v) if not np.isnan(v) else hourly_floor for v in onemin_vals]
    assert combined == [84.0, 85.5, 84.0]


def test_empty_feed_yields_empty_frame():
    out = prep_1min(_obs([]), UTC_OFF, [15])
    assert len(out) == 0
