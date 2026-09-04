"""Slot-gate proof, replayed on the 24 scheduled live-loop runs of Aug 28-Sep 4.

Every run started 2.5-3.3 h after its cron. Under the slot gate the runs that
landed inside 15Z/17Z/19Z (+75 min) trade and the rest — including every
evening arrival that the ET window used to be the only defence against — do not.
"""
from datetime import datetime, timezone

from wx.strategies import slot_for

RUNS = {  # start time -> slot the gate must assign
    "2026-08-28T01:26:30Z": None, "2026-08-28T23:35:53Z": None, "2026-08-29T00:56:33Z": None,
    "2026-08-29T17:46:16Z": 17, "2026-08-29T19:40:58Z": 19, "2026-08-29T21:09:36Z": None,
    "2026-08-30T18:07:38Z": 17, "2026-08-30T19:46:28Z": 19, "2026-08-30T21:29:35Z": None,
    "2026-08-31T20:14:24Z": 19, "2026-08-31T21:52:59Z": None, "2026-08-31T23:00:38Z": None,
    "2026-09-01T17:53:43Z": 17, "2026-09-01T19:53:20Z": 19, "2026-09-01T21:17:48Z": None,
    "2026-09-02T17:57:42Z": 17, "2026-09-02T19:43:26Z": 19, "2026-09-02T21:14:23Z": None,
    "2026-09-03T17:56:11Z": 17, "2026-09-03T19:45:16Z": 19, "2026-09-03T21:18:07Z": None,
    "2026-09-04T17:41:09Z": 17,
}


def _t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def test_replay_of_this_weeks_runs():
    got = {s: slot_for(_t(s)) for s in RUNS}
    assert got == RUNS


def test_no_run_this_week_ever_reached_the_morning_slot():
    assert 15 not in {slot_for(_t(s)) for s in RUNS}


def test_slot_edges():
    assert slot_for(_t("2026-09-05T15:00:00Z")) == 15
    assert slot_for(_t("2026-09-05T16:14:59Z")) == 15
    assert slot_for(_t("2026-09-05T16:15:00Z")) is None
    assert slot_for(_t("2026-09-05T14:59:59Z")) is None
