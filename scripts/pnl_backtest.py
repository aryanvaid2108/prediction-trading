"""REAL P&L backtest against historical Kalshi prices.

For each past day: take the market's real prices at a decision hour, run our
model's decisions (edge vs price, fractional Kelly, 25% exposure cap), settle on
the actual CLI outcome, and aggregate weekly. This is the first thing that
measures *trading profit* rather than forecast skill.

Caveats: assumes we could transact at the quoted ask (taker) — thin books may not
have filled; uses the morning forecast model vs morning prices (a fair, if
conservative-vs-intraday, comparison).

Usage: python -m scripts.pnl_backtest [start] [end] [ICAO ...]
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from wx import backtest, kalshi, stations, trading
from wx.paper import Fill, settle_pnl

BANKROLL = 1000.0
DECISION_UTC_HOUR = 15   # ~11am ET, in-window, book is open
MAX_CONTRACTS = int(__import__("os").environ.get("MAXC", "0"))  # 0 = unlimited; else cap fills
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "candles.json"
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
SESSION = kalshi._session()   # reuse one keep-alive connection (fewer resets)
_since_save = [0]


def price_at(series, ticker, day):
    """(yes_ask, yes_bid) at the decision hour, or (None, None). Disk-cached."""
    key = f"{ticker}"
    if key not in _cache:
        start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        end = start + 86400
        cs = kalshi.candlesticks(series, ticker, start, end, 60, session=SESSION)
        target = start + DECISION_UTC_HOUR * 3600
        best = None
        for c in cs:
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            if ya is None:
                continue
            d = abs(c.get("end_period_ts", 0) - target)
            if best is None or d < best[0]:
                best = (d, float(ya), float(yb) if yb is not None else None)
        _cache[key] = [best[1], best[2]] if best else [None, None]
        _since_save[0] += 1
        if _since_save[0] % 25 == 0:      # persist progress so a kill/resume is cheap
            CACHE.write_text(json.dumps(_cache))
    return _cache[key]


AFD_PATH = Path(__file__).resolve().parent.parent / ".cache" / "afd_signals.json"
AFD = json.loads(AFD_PATH.read_text()) if (AFD_PATH.exists() and os.environ.get("AFD")) else {}
AFD_WIDEN = float(os.environ.get("AFD_WIDEN", "1.4"))


def run_station(ic, start, end):
    st = stations.get(ic)
    table, cols = backtest.build_archive_table_wide(st, start, end)
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    rows = []
    for _, r in scored.iterrows():
        day = r["day"].date()
        sigma = r["sigma"] * (AFD_WIDEN if AFD.get(day.isoformat()) else 1.0)  # widen on AFD override
        prob = trading.gaussian_prob(r["mu"], sigma)
        try:
            ms = kalshi.markets(st.kalshi, day, session=SESSION)
        except Exception:
            continue          # skip a day on transient network failure, keep going
        decs = []
        for m in ms:
            try:
                ya, yb = price_at(st.kalshi, m["ticker"], day)
            except Exception:
                continue
            if ya is None:
                continue
            m2 = dict(m, yes_ask=ya, no_ask=(1 - yb) if yb is not None else None)
            d = trading.decide(m2, prob, BANKROLL, min_edge=0.03, kelly_frac=0.25)
            if d:
                decs.append((d, m2))
        # cap total daily exposure like the live loop
        kept = trading.cap_exposure([d for d, _ in decs], 0.25 * BANKROLL)
        keptset = {(d.ticker, d.side) for d in kept}
        for d, m2 in decs:
            if (d.ticker, d.side) not in keptset:
                continue
            lo, hi = trading.market_bounds(m2["strike_type"], m2.get("floor"), m2.get("cap"))
            count = min(d.count, MAX_CONTRACTS) if MAX_CONTRACTS else d.count
            f = Fill(d.ticker, ic, day.isoformat(), d.side, lo, hi, d.price, count, maker=False)
            rows.append({"day": pd.Timestamp(day), "icao": ic,
                         "cost": count * d.price, "pnl": settle_pnl(f, r["realized"])})
    return rows


def main(start="2025-06-15", end="2025-08-20", *icaos):
    icaos = list(icaos) or ["KNYC", "KMDW", "KAUS", "KLAX"]
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    rows = []
    for ic in icaos:
        try:
            r = run_station(ic, s, e)
            rows += r
            print(f"  {ic}: {len(r)} trades")
            CACHE.write_text(json.dumps(_cache))
        except Exception as ex:
            print(f"  {ic}: ERROR {ex}")
    CACHE.write_text(json.dumps(_cache))
    if not rows:
        print("no trades"); return
    df = pd.DataFrame(rows)
    df["week"] = df["day"].dt.to_period("W").apply(lambda p: p.start_time.date())
    df["month"] = df["day"].dt.to_period("M").astype(str)

    print(f"\n=== {len(df)} trades, {start}..{end}, stations {icaos} (taker, morning model vs morning prices) ===")
    tot_cost, tot_pnl = df["cost"].sum(), df["pnl"].sum()
    print(f"total staked ${tot_cost:.0f}  ·  net P&L ${tot_pnl:+.2f}  ·  ROI {100*tot_pnl/tot_cost:+.1f}% on stake")
    wins = (df["pnl"] > 0).mean()
    print(f"win rate {wins:.0%}  ·  avg trade ${df['pnl'].mean():+.2f}  ·  trades/day ~{len(df)/df['day'].nunique():.1f}\n")

    print("Weekly:")
    wk = df.groupby("week").agg(trades=("pnl", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"))
    wk["cum"] = wk["pnl"].cumsum()
    for w, row in wk.iterrows():
        print(f"  {w}  trades {int(row['trades']):>3}  staked ${row['staked']:>5.0f}  "
              f"P&L ${row['pnl']:>+7.2f}  cum ${row['cum']:>+8.2f}")
    print("\nMonthly:")
    mo = df.groupby("month").agg(trades=("pnl", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"))
    for m, row in mo.iterrows():
        print(f"  {m}  trades {int(row['trades']):>3}  staked ${row['staked']:>6.0f}  "
              f"P&L ${row['pnl']:>+8.2f}  ROI {100*row['pnl']/row['staked']:+.1f}%")


if __name__ == "__main__":
    main(*sys.argv[1:])
