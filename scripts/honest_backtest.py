"""Honest backtest of the FULL current strategy, judged on the median day.

Simulates exactly what run_live now does — honest mixture quote (walk-forward
sharpening), ONE highest-EV robust bet per station per day (±1.5° gate,
min_edge 0.05), IOC taker fills at real candle asks + fees — over history, and
reports per-station median/worst-day stats. This is the go-live gate: a station
earns full size only if its edge survives THIS test (mean-based validation is
banned; Day 1 taught us why).

Usage: python -m scripts.honest_backtest [start] [end] [ICAO ...]
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from wx import backtest, cli, intraday, kalshi, obs, stations, trading

BANKROLL = 750.0
MIN_EDGE, KELLY, ROBUST_DELTA = 0.05, 0.25, 1.5
import os
TICKS_UTC = [int(h) for h in os.environ.get("TICKS", "15,17,19").split(",")]
MAXC = 300                      # crude liquidity cap per order

# BT_NEW_FILTERS=1 applies the live engine's post-Aug-26 selection rules
# (15c price floor, p/ask ratio cap, proportional robust gate shifted toward
# the market-implied mean). BT_DEPTH=1 replaces fantasy full fills with caps
# calibrated to the live IOC record and adds the observed ~1c slip.
NEW_FILTERS = os.environ.get("BT_NEW_FILTERS", "1") == "1"
DEPTH_AWARE = os.environ.get("BT_DEPTH", "1") == "1"
FLOW = os.environ.get("BT_FLOW", "0") == "1"   # flow residual pool: evaluated a wash, off by default
MODEL_W = float(os.environ.get("BT_MODEL_W", "1"))   # 0.5 = live shrinkage setting
MIN_PRICE, RATIO_CAP = 0.15, 2.5
SLIP = 0.01
EARLY = 9


def depth_cap(ask: float) -> int:
    """Max contracts one IOC can realistically lift, from the live fill record
    (Aug 25-26: sub-10c books filled 18-318, median ~140; 10-20c filled 18-93;
    20c+ filled 46-100% of 71-84-lot orders). Deliberately conservative."""
    if ask < 0.10:
        return 120
    if ask < 0.20:
        return 90
    return 150
N_MIX, BW = 2000, 0.25
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "candles_full.json"
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
SESSION = kalshi._session()


def candle_price(series, ticker, day, hr_utc):
    """(yes_ask, yes_bid) from the candle ending at hr_utc, disk-cached."""
    if ticker not in _cache:
        start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        cs = kalshi.candlesticks(series, ticker, start, start + 86400, 60, session=SESSION)
        out = {}
        for c in cs:
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            hr = int((c.get("end_period_ts", 0) - start) / 3600)
            out[str(hr)] = [float(yb) if yb is not None else None,
                            float(ya) if ya is not None else None]
        _cache[ticker] = out
        CACHE.write_text(json.dumps(_cache))
    v = _cache[ticker].get(str(hr_utc))
    return (v[1], v[0]) if v else (None, None)


def run_station(ic, start, end, rng):
    st = stations.get(ic)
    hours_lst = sorted({EARLY} | {h + st.std_utc_offset for h in TICKS_UTC})
    table, cols = backtest.build_archive_table_wide(st, start - timedelta(days=50), end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    calib = backtest.calibration_factor(scored)
    sc = scored.set_index(pd.to_datetime(scored["day"]))
    o = obs.fetch_asos(st.iem_id, start - timedelta(days=51), end + timedelta(days=2))
    prep = intraday.prep(o, st.std_utc_offset, hours_lst)
    prep = prep.assign(day=pd.to_datetime(prep["day"])).set_index("day")
    finals = cli.settlement_high(st.icao, start - timedelta(days=50), end)

    zhist = {h: [] for h in hours_lst}
    rows, gate_kills, cal = [], 0, []
    days = sorted(sc.index.intersection(prep.index).intersection(finals.index))
    days = [d for d in days if d >= pd.Timestamp(start)]
    for d in days:
        mu0 = float(sc.loc[d, "mu"])
        s0 = float(sc.loc[d, "sigma"]) * calib
        y = float(finals.loc[d])
        try:
            ms = kalshi.markets(st.kalshi, d.date(), session=SESSION)
        except Exception:
            continue
        traded = False
        for h_utc in TICKS_UTC:
            h = h_utc + st.std_utc_offset
            rm_col = f"rm_{h}"
            rm_d = prep.loc[d, rm_col] if rm_col in prep.columns else np.nan
            if pd.isna(rm_d):
                continue
            past_rm = prep.loc[prep.index < d, rm_col].dropna()
            res_all = (finals.reindex(past_rm.index) - past_rm).dropna()
            if FLOW and f"rm_{EARLY}" in prep.columns:
                climbs = (prep[rm_col] - prep[f"rm_{EARLY}"])
                tc = climbs.get(d)
                res_all = intraday.flow_pool(res_all, climbs.reindex(res_all.index),
                                             None if pd.isna(tc) else float(tc))
            res = res_all.tail(60).to_numpy()

            # quote: honest mixture (mirrors pipeline.quote_live) or prior fallback
            if len(res) >= 15:
                s_i = max(float(res.std()), 0.3)
                w0, wi = 1.0 / s0**2, 1.0 / s_i**2
                n0 = int(round(N_MIX * w0 / (w0 + wi)))
                raw = np.concatenate([rng.normal(mu0, s0, n0),
                                      float(rm_d) + rng.choice(res, N_MIX - n0, replace=True)])
                raw = raw + rng.normal(0, BW, N_MIX)
                clipped = np.clip(raw, round(float(rm_d)) - 0.5, None)
                pit = float(np.clip((clipped < y).mean(), 1e-4, 1 - 1e-4))
                zh = zhist[h]
                shrink = float(np.clip(np.std(zh) * 1.1, 0.7, 1.3)) if len(zh) >= 25 else 1.0
                zh.append(float(norm.ppf(pit)))
                samples = raw.mean() + (raw - raw.mean()) * shrink
                samples = np.clip(samples, round(float(rm_d)) - 0.5, None)
                prob_fn = trading.sample_prob(samples)
                shift_fn = lambda dd, _s=samples: trading.sample_prob(_s + dd)
            else:
                prob_fn = trading.gaussian_prob(mu0, s0)
                shift_fn = lambda dd: trading.gaussian_prob(mu0 + dd, s0)

            # prices at this hour
            priced = []
            for m in ms:
                ya, yb = candle_price(st.kalshi, m["ticker"], d.date(), h_utc)
                if ya is None:
                    continue
                priced.append(dict(m, yes_ask=ya, yes_bid=yb,
                                   no_ask=(1 - yb) if yb is not None else None))

            # model-vs-market calibration on every priced bucket (Item 10)
            for m in priced:
                if m.get("yes_bid") is None:
                    continue
                lo_, hi_ = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
                yy = 1.0 if ((lo_ is None or y >= lo_) and (hi_ is None or y <= hi_)) else 0.0
                cal.append({"day": d, "icao": ic, "hour_utc": h_utc, "ticker": m["ticker"],
                            "p_model": prob_fn(lo_, hi_),
                            "p_market": (m["yes_ask"] + m["yes_bid"]) / 2, "outcome": yy})

            if traded:
                continue                       # one thesis; keep looping only to feed zhist
            extra = (dict(min_price=MIN_PRICE, ratio_cap=RATIO_CAP, model_weight=MODEL_W)
                     if NEW_FILTERS else {})
            cands = trading.decisions_for(priced, prob_fn, BANKROLL, min_edge=MIN_EDGE,
                                          kelly_frac=KELLY, **extra)
            by = {m["ticker"]: m for m in priced}
            toward = None
            if NEW_FILTERS:
                imp = trading.market_implied_mean(priced)
                mu_q = float(samples.mean()) if len(res) >= 15 else mu0
                toward = (imp - mu_q) if imp is not None else None
            gated = [c for c in cands
                     if trading.robust_edge(by[c.ticker], c.side, shift_fn, ROBUST_DELTA,
                                            toward=toward) > 0]
            gate_kills += len(cands) - len(gated)
            if not gated:
                continue
            dbest = max(gated, key=lambda x: x.ev)
            cnt = min(dbest.count, MAXC)
            px = dbest.price
            dropped = 0
            if DEPTH_AWARE:
                dcap = depth_cap(px)
                dropped = max(0, cnt - dcap)
                cnt = min(cnt, dcap)
                px = min(0.99, round(px + SLIP, 4))
            m = by[dbest.ticker]
            lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
            inb = (lo is None or y >= lo) and (hi is None or y <= hi)
            win = inb if dbest.side == "yes" else not inb
            gross = cnt * (1 - px) if win else -cnt * px
            pnl = round(gross - trading.fee(px, cnt), 2)
            rows.append({"day": d, "icao": ic, "hour_utc": h_utc, "side": dbest.side,
                         "price": px, "count": cnt, "dropped": dropped, "win": win, "pnl": pnl})
            traded = True
    return rows, gate_kills, len(days), cal


def main(start="2026-07-01", end="2026-08-24", *icaos):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    icaos = list(icaos) or stations.ACTIVE
    rng = np.random.default_rng(3)
    allrows, allcal = [], []
    print(f"Honest backtest {start}..{end}  (one robust thesis/station/day, "
          f"min_edge {MIN_EDGE}, ±{ROBUST_DELTA}° gate, MAXC {MAXC}, bankroll ${BANKROLL:.0f})\n")
    for ic in icaos:
        try:
            rows, kills, ndays, cal = run_station(ic, s, e, rng)
        except Exception as ex:
            print(f"{ic}: ERROR {type(ex).__name__}: {str(ex)[:70]}")
            continue
        allrows += rows
        allcal += cal
        df = pd.DataFrame(rows)
        daily = df.groupby("day")["pnl"].sum() if len(df) else pd.Series(dtype=float)
        daily = daily.reindex(pd.date_range(s, e), fill_value=0.0)
        wr = df["win"].mean() if len(df) else float("nan")
        print(f"{ic}: {len(df)} trades over {ndays} days (gate killed {kills})  "
              f"total ${df['pnl'].sum() if len(df) else 0:+8.2f}  win {wr:.0%}  "
              f"median-day ${daily.median():+6.2f}  worst-day ${daily.min():+8.2f}")
    tag = (f"{'new' if NEW_FILTERS else 'old'}_{'depth' if DEPTH_AWARE else 'naive'}"
           + (f"_w{MODEL_W:g}" if NEW_FILTERS and MODEL_W != 1 else ""))
    if not allrows:
        print("no trades anywhere")
        _report_cal(allcal, tag)
        return
    a = pd.DataFrame(allrows)
    a.to_csv(CACHE.parent / f"honest_bt_trades_{tag}.csv", index=False)
    daily = a.groupby("day")["pnl"].sum().reindex(pd.date_range(s, e), fill_value=0.0)
    print(f"\nALL [{tag}]: {len(a)} trades  total ${a['pnl'].sum():+.2f}  "
          f"median-day ${daily.median():+.2f}  p10-day ${daily.quantile(0.10):+.2f}  "
          f"worst-day ${daily.min():+.2f}  green-days {(daily > 0).mean():.0%}")
    if "dropped" in a and a["dropped"].sum():
        print(f"depth caps dropped {int(a['dropped'].sum())} contracts "
              f"across {(a['dropped'] > 0).sum()} trades (no silent truncation)")
    print("\nby price tier:")
    for name, g in [("<0.15 (tails)", a[a["price"] < 0.15]), (">=0.15", a[a["price"] >= 0.15])]:
        if len(g):
            print(f"  {name:14} {len(g):3} trades  ${g['pnl'].sum():+8.2f}  win {g['win'].mean():.0%}")
    print("\nby entry tick:")
    for h, g in a.groupby("hour_utc"):
        print(f"  {h:02d}Z: {len(g)} trades  ${g['pnl'].sum():+8.2f}  win {g['win'].mean():.0%}")

    _report_cal(allcal, tag)


def _brier(g):
    return (((g["p_model"] - g["outcome"]) ** 2).mean(),
            ((g["p_market"] - g["outcome"]) ** 2).mean())


def _report_cal(allcal, tag):
    """Item 10: who forecasts better, by hour — the trade window should follow this."""
    if not allcal:
        return
    c = pd.DataFrame(allcal)
    c.to_csv(CACHE.parent / f"brier_by_hour_{tag}.csv", index=False)
    print("\nBrier by hour (model vs market mid, all priced buckets):")
    for h, g in c.groupby("hour_utc"):
        bm, bk = _brier(g)
        print(f"  {h:02d}Z: n={len(g):4}  model {bm:.4f}  market {bk:.4f}  "
              f"{'MODEL' if bm < bk else 'market'} leads by {abs(bm - bk):.4f}")
    for ic, g in c.groupby("icao"):
        bm, bk = _brier(g)
        print(f"  {ic}: model {bm:.4f}  market {bk:.4f}  {'MODEL' if bm < bk else 'market'} leads")


if __name__ == "__main__":
    main(*sys.argv[1:])
