"""Diagnose WHY losing trades lost: were they big model misses (fixable) or the
outcome landing just outside a well-centered forecast (unavoidable variance)?

Uses the cached candlestick prices, so it's fast. Usage: python -m scripts.loss_analysis [ICAO]
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from wx import backtest, kalshi, stations, trading
from wx.paper import settle_pnl, Fill

CACHE = json.loads((Path(__file__).resolve().parent.parent / ".cache" / "candles.json").read_text())
DECISION_UTC_HOUR = 15


def price_at(ticker):
    return CACHE.get(ticker, [None, None])


def main(ic="KNYC", start="2026-05-20", end="2026-08-22"):
    st = stations.get(ic)
    table, cols = backtest.build_archive_table_wide(st, date.fromisoformat(start), date.fromisoformat(end))
    scored = backtest.rolling_score_mixed(table, cols, min_train=45, window=45)
    rows = []
    for _, r in scored.iterrows():
        day = r["day"].date()
        prob = trading.gaussian_prob(r["mu"], r["sigma"])
        try:
            ms = kalshi.markets(st.kalshi, day)
        except Exception:
            continue
        for m in ms:
            ya, yb = price_at(m["ticker"])
            if ya is None:
                continue
            m2 = dict(m, yes_ask=ya, no_ask=(1 - yb) if yb is not None else None)
            d = trading.decide(m2, prob, 1000, min_edge=0.03, kelly_frac=0.25)
            if not d:
                continue
            lo, hi = trading.market_bounds(m2["strike_type"], m2.get("floor"), m2.get("cap"))
            pnl = settle_pnl(Fill(d.ticker, ic, day.isoformat(), d.side, lo, hi, d.price, min(d.count, 25)), r["realized"])
            rows.append({"day": day, "side": d.side, "prob": d.model_prob, "price": d.price,
                         "mu": r["mu"], "realized": r["realized"], "err": r["realized"] - r["mu"],
                         "won": pnl > 0, "pnl": pnl})
    df = pd.DataFrame(rows)
    W, L = df[df.won], df[~df.won]
    print(f"\n{ic}: {len(df)} trades — {len(W)} won, {len(L)} lost ({len(L)/len(df):.0%} loss rate)\n")

    print("Forecast error |realized - model mean|:")
    print(f"  winners: {W['err'].abs().mean():.2f}F   losers: {L['err'].abs().mean():.2f}F")
    print(f"  signed err  winners {W['err'].mean():+.2f}F   losers {L['err'].mean():+.2f}F  (bias check)\n")

    print("Loss rate by how big the model's miss was that day:")
    df["abserr"] = df["err"].abs()
    for lo_, hi_ in [(0, 1), (1, 2), (2, 3), (3, 99)]:
        m = (df.abserr >= lo_) & (df.abserr < hi_)
        if m.sum():
            print(f"  miss {lo_}-{hi_ if hi_<99 else '∞'}F:  {m.sum():>3} trades, loss rate {1-df[m].won.mean():.0%}")

    print("\nWere we 'confidently wrong'? loss rate by our stated probability:")
    for lo_, hi_ in [(0.5, 0.7), (0.7, 0.85), (0.85, 1.01)]:
        m = (df.prob >= lo_) & (df.prob < hi_)
        if m.sum():
            print(f"  model {lo_:.0%}-{hi_:.0%} conf: {m.sum():>3} trades, actually lost {1-df[m].won.mean():.0%}")

    print("\nWorst 6 losing days (biggest model misses):")
    for _, x in L.reindex(L.err.abs().sort_values(ascending=False).index).head(6).iterrows():
        print(f"  {x['day']}  model said {x['mu']:.1f}F, actual {x['realized']:.0f}F  "
              f"(miss {x['err']:+.1f}F)  {x['side'].upper()}@{x['price']:.2f}  ${x['pnl']:+.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
