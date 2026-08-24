"""P&L backtest at the INTRADAY (3pm ET) decision time — the real strategy.

Same as pnl_backtest but the quote blends the forecast prior with the temperature
already observed by 3pm (floored at the observed max), and prices come from the
3pm candle. Answers: how many of the morning model's losses does timing recover?

Usage: python -m scripts.pnl_intraday [ICAO] [start] [end]
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from wx import backtest, intraday, kalshi, stations, trading
from wx.paper import Fill, settle_pnl

BANKROLL = 1000.0
DECISION_UTC_HOUR = 19          # 3pm ET
MAX_CONTRACTS = 25
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "candles_hourly.json"
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
SESSION = kalshi._session()
_n = [0]


def price_at(series, ticker, day, hour):
    """(yes_ask, yes_bid) at a given UTC hour; caches the full hourly map per ticker."""
    if ticker not in _cache:
        start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        cs = kalshi.candlesticks(series, ticker, start, start + 86400, 60, session=SESSION)
        m = {}
        for c in cs:
            h = int((c.get("end_period_ts", 0) - start) / 3600)
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            if ya is not None:
                m[h] = [float(ya), float(yb) if yb is not None else None]
        _cache[ticker] = m
        _n[0] += 1
        if _n[0] % 25 == 0:
            CACHE.write_text(json.dumps(_cache))
    hm = _cache[ticker]
    # nearest available hour at/after decision, else closest
    if not hm:
        return None, None
    hh = min(hm, key=lambda k: (abs(int(k) - hour)))
    return hm[hh][0], hm[hh][1]


def intraday_probfn(prior_mu, prior_s, obs_max, residuals):
    if obs_max is None or len(residuals) < 15:
        return trading.gaussian_prob(prior_mu, prior_s)
    mu_i = obs_max + float(np.mean(residuals))
    s_i = max(float(np.std(residuals)), 0.3)
    w0, wi = 1 / prior_s ** 2, 1 / s_i ** 2
    mu_b = (w0 * prior_mu + wi * mu_i) / (w0 + wi)
    s_b = float(np.sqrt(1 / (w0 + wi)))
    return trading.floored_gaussian_prob(mu_b, s_b, obs_max)


def main(ic="KNYC", start="2026-05-20", end="2026-08-22"):
    st = stations.get(ic)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    h_lst = (DECISION_UTC_HOUR + st.std_utc_offset) % 24    # local hour at decision

    table, cols = backtest.build_archive_table_wide(st, s, e)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    calib = backtest.calibration_factor(scored)

    o = backtest.obs.fetch_asos(st.iem_id, s, e)
    prep = intraday.prep(o, st.std_utc_offset, [h_lst]).set_index("day")
    prep.index = pd.to_datetime(prep.index)
    realized = table.set_index("day")["high"]
    runmax = prep[f"rm_{h_lst}"]

    rows = []
    for _, r in scored.iterrows():
        day = r["day"]
        obs_max = runmax.get(day)
        obs_max = None if (obs_max is None or pd.isna(obs_max)) else float(obs_max)
        prior_days = realized.index[realized.index < day][-45:]
        res = (realized.loc[prior_days] - runmax.reindex(prior_days)).dropna().to_numpy()
        prob = intraday_probfn(r["mu"], r["sigma"] * calib, obs_max, res)
        try:
            ms = kalshi.markets(st.kalshi, day.date(), session=SESSION)
        except Exception:
            continue
        decs = []
        for m in ms:
            ya, yb = price_at(st.kalshi, m["ticker"], day.date(), DECISION_UTC_HOUR)
            if ya is None:
                continue
            m2 = dict(m, yes_ask=ya, no_ask=(1 - yb) if yb is not None else None)
            d = trading.decide(m2, prob, BANKROLL, min_edge=0.03, kelly_frac=0.25)
            if d:
                decs.append((d, m2))
        kept = {(d.ticker, d.side) for d in trading.cap_exposure([d for d, _ in decs], 0.25 * BANKROLL)}
        for d, m2 in decs:
            if (d.ticker, d.side) not in kept:
                continue
            lo, hi = trading.market_bounds(m2["strike_type"], m2.get("floor"), m2.get("cap"))
            cnt = min(d.count, MAX_CONTRACTS)
            pnl = settle_pnl(Fill(d.ticker, ic, day.date().isoformat(), d.side, lo, hi, d.price, cnt), r["realized"])
            rows.append({"day": day, "won": pnl > 0, "cost": cnt * d.price, "pnl": pnl})
    CACHE.write_text(json.dumps(_cache))

    df = pd.DataFrame(rows)
    print(f"\n=== {ic} INTRADAY (3pm ET) — {len(df)} trades, {start}..{end}, cap {MAX_CONTRACTS} ===")
    print(f"net P&L ${df['pnl'].sum():+.2f}  ·  staked ${df['cost'].sum():.0f}  ·  "
          f"ROI {100*df['pnl'].sum()/df['cost'].sum():+.1f}%  ·  win rate {df['won'].mean():.0%}  ·  {len(df)} trades")
    df["week"] = df["day"].dt.to_period("W").apply(lambda p: p.start_time.date())
    wk = df.groupby("week").agg(trades=("pnl", "size"), pnl=("pnl", "sum"))
    wk["cum"] = wk["pnl"].cumsum()
    print("\nWeekly:")
    for w, row in wk.iterrows():
        print(f"  {w}  trades {int(row['trades']):>3}  P&L ${row['pnl']:>+7.2f}  cum ${row['cum']:>+8.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
