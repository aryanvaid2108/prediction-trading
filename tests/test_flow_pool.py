"""Fix 07 unit proof: the flow-conditioned pool selects the matching climb
tercile and falls back to the full pool whenever it can't be trusted."""
import numpy as np
import pandas as pd

from wx.intraday import flow_pool

IDX = pd.date_range("2026-06-01", periods=60)
# fast-warming days finish with big remaining climbs; slow days with small ones
CLIMBS = pd.Series(np.r_[np.full(20, 2.0), np.full(20, 6.0), np.full(20, 10.0)], index=IDX)
RES = pd.Series(np.r_[np.full(20, 0.5), np.full(20, 2.0), np.full(20, 4.0)], index=IDX)


def test_fast_warming_day_draws_from_fast_tercile():
    pool = flow_pool(RES, CLIMBS, today_climb=11.0)
    assert len(pool) == 20 and pool.min() >= 4.0     # only the big-residual days


def test_slow_day_draws_from_slow_tercile():
    pool = flow_pool(RES, CLIMBS, today_climb=1.0)
    assert len(pool) == 20 and pool.max() <= 0.5


def test_middle_day_draws_from_middle():
    pool = flow_pool(RES, CLIMBS, today_climb=6.0)
    assert set(pool.unique()) == {2.0}


def test_fallbacks_return_full_pool():
    assert len(flow_pool(RES, CLIMBS, today_climb=None)) == 60          # unknown climb
    assert len(flow_pool(RES.head(20), CLIMBS.head(20), 5.0)) == 20     # too few days
    thin = flow_pool(RES, CLIMBS.where(CLIMBS < 7), 11.0)               # tercile too thin
    assert len(thin) == len(RES)
