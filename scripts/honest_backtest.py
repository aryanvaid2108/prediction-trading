"""Honest backtest of the FULL current strategy, judged on the median day.

Two stages so a parameter sweep is cheap:
  snapshot_station  — the expensive part, done once per station/window: the
                      honest-mixture quote (walk-forward sharpening, 1-min floor)
                      at every tick, the candle-priced book, and the CLI outcome.
                      Pickled under .cache/bt_quotes/ (gitignored).
  simulate          — replay ANY wx.strategies.Arm over a snapshot with the same
                      selector the live loop and paper arms use, IOC taker fills
                      at real candle asks + fees, depth caps from the live fill
                      record. Milliseconds per configuration.

This is the go-live gate: a station earns full size only if its edge survives
THIS test (mean-based validation is banned; Day 1 taught us why).

Usage: python -m scripts.honest_backtest [start] [end] [ICAO ...]
Envs:  BT_MODEL_W (default 1; 0.5 = live), BT_NEW_FILTERS=0 (pre-Aug-27 rules),
       BT_DEPTH=0 (fantasy fills), BT_1MIN=0, BT_FLOW=1, TICKS=15,17,19
"""
import json
import os
import pickle
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from wx import backtest, cli, intraday, kalshi, obs, stations, strategies, trading

BANKROLL = 750.0
KELLY = 0.25
TICKS_UTC = [int(h) for h in os.environ.get("TICKS", "15,17,19").split(",")]
ALL_TICKS = (15, 17, 19)        # snapshots always carry every tick; simulate picks a subset
MAXC = 300                      # crude liquidity cap per order

NEW_FILTERS = os.environ.get("BT_NEW_FILTERS", "1") == "1"
DEPTH_AWARE = os.environ.get("BT_DEPTH", "1") == "1"
FLOW = os.environ.get("BT_FLOW", "0") == "1"   # flow residual pool: evaluated a wash, off by default
ONEMIN = os.environ.get("BT_1MIN", "1") == "1" # settlement-grade floor from sustained 1-min max
MODEL_W = float(os.environ.get("BT_MODEL_W", "1"))   # 0.5 = live shrinkage setting
SLIP = 0.01
EARLY = 9
N_MIX, BW = 2000, 0.25
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "candles_full.json"
SNAP_DIR = CACHE.parent / "bt_quotes"
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
SESSION = kalshi._session()

OLD_RULES = strategies.Arm("old", min_price=0.0, ratio_cap=None, model_weight=1.0,
                           toward_market=False)


def depth_cap(ask: float) -> int:
    """Max contracts one IOC can realistically lift, from the live fill record
    (Aug 25-26: sub-10c books filled 18-318, median ~140; 10-20c filled 18-93;
    20c+ filled 46-100% of 71-84-lot orders). Deliberately conservative."""
    if ask < 0.10:
        return 120
    if ask < 0.20:
        return 90
    return 150


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


class Quote:
    """What strategies.select needs: prob_fn, shift_fn, mu."""
    def __init__(self, samples, mu0, s0):
        if samples is not None:
            self.mu = float(samples.mean())
            self.prob_fn = trading.sample_prob(samples)
            self.shift_fn = lambda dd, _s=samples: trading.sample_prob(_s + dd)
        else:
            self.mu = mu0
            self.prob_fn = trading.gaussian_prob(mu0, s0)
            self.shift_fn = lambda dd: trading.gaussian_prob(mu0 + dd, s0)


def snapshot_station(ic, start, end, rng, ticks=ALL_TICKS):
    """One record per (day, tick): the quote's samples, the priced book, the
    next-tick book (execution research) and the CLI outcome. Returns (records, ndays)."""
    st = stations.get(ic)
    hours_lst = sorted({EARLY} | {h + st.std_utc_offset for h in ticks})
    table, cols = backtest.build_archive_table_wide(st, start - timedelta(days=50), end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    calib = backtest.calibration_factor(scored)
    sc = scored.set_index(pd.to_datetime(scored["day"]))
    o = obs.fetch_asos(st.iem_id, start - timedelta(days=51), end + timedelta(days=2))
    prep = intraday.prep(o, st.std_utc_offset, hours_lst)
    prep = prep.assign(day=pd.to_datetime(prep["day"])).set_index("day")
    finals = cli.settlement_high(st.icao, start - timedelta(days=50), end)
    om = pd.DataFrame()
    if ONEMIN:
        try:
            m1 = obs.fetch_asos_1min(st.iem_id, start, end + timedelta(days=1))
            om = intraday.prep_1min(m1, st.std_utc_offset, hours_lst)
            om = om.assign(day=pd.to_datetime(om["day"])).set_index("day")
        except Exception as ex:
            print(f"  {ic}: 1-min feed unavailable ({type(ex).__name__}) — hourly floor only")

    zhist = {h: [] for h in hours_lst}
    recs = []
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
        for h_utc in ticks:
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
            samples = None
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
                floor_d = float(rm_d)
                if f"om_{h}" in om.columns and d in om.index and pd.notna(om.loc[d, f"om_{h}"]):
                    floor_d = max(floor_d, float(om.loc[d, f"om_{h}"]))
                samples = raw.mean() + (raw - raw.mean()) * shrink
                samples = np.clip(samples, round(floor_d) - 0.5, None).astype(np.float32)

            priced, nxt = [], {}
            for m in ms:
                ya, yb = candle_price(st.kalshi, m["ticker"], d.date(), h_utc)
                if ya is None:
                    continue
                priced.append(dict(m, yes_ask=ya, yes_bid=yb,
                                   no_ask=(1 - yb) if yb is not None else None))
                nxt[m["ticker"]] = candle_price(st.kalshi, m["ticker"], d.date(), h_utc + 2)
            recs.append({"day": d, "icao": ic, "hour_utc": h_utc, "samples": samples,
                         "mu0": mu0, "s0": s0, "y": y, "priced": priced, "next": nxt})
    return recs, len(days)


def load_snapshot(ic, start, end, rng=None):
    """Disk-cached snapshot_station (the network-heavy stage)."""
    SNAP_DIR.mkdir(exist_ok=True)
    tag = f"{'flow_' if FLOW else ''}{'1min' if ONEMIN else 'hourly'}"
    p = SNAP_DIR / f"{ic}_{start}_{end}_{tag}.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    out = snapshot_station(ic, start, end, rng or np.random.default_rng(3))
    p.write_bytes(pickle.dumps(out))
    return out


def simulate(recs, arm, ticks=ALL_TICKS, depth=True, slip=SLIP, bankroll=BANKROLL,
             kelly=KELLY, maxc=MAXC, exclude=(), calibration=False):
    """Replay one arm over snapshot records. Returns (trade rows, gate kills, cal rows).
    One thesis per station per day: the first tick with a gated survivor trades."""
    rows, kills, cal = [], 0, []
    arm_kw = dict(vars(arm)); arm_kw["kelly_frac"] = kelly
    arm = strategies.Arm(**arm_kw)
    traded = set()
    for r in recs:
        if r["icao"] in exclude or r["hour_utc"] not in ticks:
            continue
        q = Quote(r["samples"], r["mu0"], r["s0"])
        priced, y, d = r["priced"], r["y"], r["day"]
        if calibration:
            for m in priced:
                if m.get("yes_bid") is None:
                    continue
                lo_, hi_ = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
                yy = 1.0 if ((lo_ is None or y >= lo_) and (hi_ is None or y <= hi_)) else 0.0
                cal.append({"day": d, "icao": r["icao"], "hour_utc": r["hour_utc"], "ticker": m["ticker"],
                            "p_model": q.prob_fn(lo_, hi_),
                            "p_market": (m["yes_ask"] + m["yes_bid"]) / 2, "outcome": yy})
        if (r["icao"], d) in traded:
            continue
        pick, cands = strategies.select(priced, q, bankroll, arm)
        kills += sum(not c.gated for c in cands)
        if pick is None:
            continue
        by = {m["ticker"]: m for m in priced}
        cnt, px, dropped = min(pick.count, maxc), pick.price, 0
        if depth:
            dcap = depth_cap(px)
            dropped = max(0, cnt - dcap)
            cnt = min(cnt, dcap)
            px = min(0.99, round(px + slip, 4))
        m = by[pick.ticker]
        lo, hi = trading.market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        inb = (lo is None or y >= lo) and (hi is None or y <= hi)
        win = inb if pick.side == "yes" else not inb
        gross = cnt * (1 - px) if win else -cnt * px
        nya, nyb = r["next"].get(pick.ticker, (None, None))
        rows.append({"day": d, "icao": r["icao"], "hour_utc": r["hour_utc"], "side": pick.side,
                     "price": px, "count": cnt, "dropped": dropped, "win": win,
                     "pnl": round(gross - trading.fee(px, cnt), 2),
                     "ticker": pick.ticker, "yes_ask": m["yes_ask"], "yes_bid": m.get("yes_bid"),
                     "next_yes_ask": nya, "next_yes_bid": nyb})
        traded.add((r["icao"], d))
    return rows, kills, cal


def main(start="2026-07-01", end="2026-08-24", *icaos):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    icaos = list(icaos) or stations.ACTIVE
    arm = (strategies.Arm("bt", model_weight=MODEL_W) if NEW_FILTERS else OLD_RULES)
    allrows, allcal = [], []
    print(f"Honest backtest {start}..{end}  (one robust thesis/station/day, "
          f"arm {arm}, ticks {TICKS_UTC}, MAXC {MAXC}, bankroll ${BANKROLL:.0f})\n")
    for ic in icaos:
        try:
            recs, ndays = load_snapshot(ic, s, e)
        except Exception as ex:
            print(f"{ic}: ERROR {type(ex).__name__}: {str(ex)[:70]}")
            continue
        rows, kills, cal = simulate(recs, arm, ticks=tuple(TICKS_UTC), depth=DEPTH_AWARE,
                                    calibration=True)
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
           + (f"_w{MODEL_W:g}" if NEW_FILTERS and MODEL_W != 1 else "")
           + ("_1min" if ONEMIN else ""))
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
