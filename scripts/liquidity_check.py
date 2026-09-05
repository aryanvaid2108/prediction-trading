"""Real-liquidity check for the gate variants.

The pressure test caps fills with a depth model guessed from nine live fills.
Kalshi's hourly candles carry the contracts ACTUALLY traded in each market each
hour (volume_fp) and the ask's open/high/low/close. This re-scores control,
gate ±1.0° and no-gate under:
  as-is          depth caps as in the pressure test
  vol 25%        count <= 25% of that hour's traded contracts (0 if nothing traded)
  vol 10%        count <= 10%
  vol 25% + stale  ... and fill at the WORSE of the hour's ask open/close (+1c)
  live ratio     count x the fill fraction actually achieved live by price tier
                 (sub-20c: 140/448, 18/100 -> 0.29; 20c+: 218/386 -> 0.56)
Usage: python -m scripts.liquidity_check   (writes .cache/liquidity_check.csv)
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from wx import kalshi, stations, strategies, trading
from scripts import honest_backtest as hb
from scripts.pressure_test import DESIGN, HOLDOUT, daily

VOL = Path(".cache/candle_volume.json")
_vol = json.loads(VOL.read_text()) if VOL.exists() else {}
SESSION = kalshi._session()
CONFIGS = [("control (±1.5°)", strategies.CONTROL),
           ("gate ±1.0°", strategies.Arm("g1", robust_delta=1.0)),
           ("no gate", strategies.Arm("ng", robust_delta=0.0)),
           ("model_w 0.25", strategies.Arm("w025", model_weight=0.25))]
LIVE_RATIO = lambda px: 0.29 if px < 0.20 else 0.56


def hour_info(ticker, day):
    """{hr: [volume, ask_open, ask_close]} for one market, disk-cached."""
    if ticker not in _vol:
        series = ticker.split("-")[0]
        start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        out = {}
        for c in kalshi.candlesticks(series, ticker, start, start + 86400, 60, session=SESSION):
            hr = int((c.get("end_period_ts", 0) - start) / 3600)
            a = c.get("yes_ask") or {}
            out[str(hr)] = [float(c.get("volume_fp") or 0),
                            float(a["open_dollars"]) if a.get("open_dollars") else None,
                            float(a["close_dollars"]) if a.get("close_dollars") else None]
        _vol[ticker] = out
        VOL.write_text(json.dumps(_vol))
    return _vol[ticker]


def rescore(rows, scenario):
    out = []
    for r in rows:
        info = hour_info(r["ticker"], r["day"].date()).get(str(r["hour_utc"]))
        vol, a_open, a_close = info if info else (0.0, None, None)
        cnt, px = r["count"], r["price"]
        if scenario == "as-is":
            pass
        elif scenario.startswith("vol"):
            frac = 0.25 if "25" in scenario else 0.10
            cnt = min(cnt, int(vol * frac))
            if "stale" in scenario and a_open is not None and a_close is not None:
                ask = max(a_open, a_close) if r["side"] == "yes" else max(1 - a_open, 1 - a_close)
                # the candle is in YES terms; NO ask = 1 - YES bid ~ 1 - YES ask + spread, keep simple
                px = min(0.99, round(ask + hb.SLIP, 4)) if r["side"] == "yes" else px
        elif scenario == "live ratio":
            cnt = int(round(cnt * LIVE_RATIO(px)))
        if cnt < 1:
            continue
        gross = cnt * (1 - px) if r["win"] else -cnt * px
        out.append({**r, "count": cnt, "price": px, "pnl": round(gross - trading.fee(px, cnt), 2)})
    return out


def main():
    recs = []
    for ic in stations.ACTIVE:
        for s, e in (DESIGN, HOLDOUT):
            recs += hb.load_snapshot(ic, s, e)[0]
    table = []
    for name, arm in CONFIGS:
        base, _, _ = hb.simulate(recs, arm)
        for sc in ("as-is", "vol 25%", "vol 10%", "vol 25% + stale", "live ratio"):
            rows = rescore(base, sc)
            dd = daily(rows, DESIGN[0], DESIGN[1]); dh = daily(rows, HOLDOUT[0], HOLDOUT[1])
            table.append({"config": name, "scenario": sc, "trades": len(rows),
                          "design": round(dd.sum(), 2), "holdout": round(dh.sum(), 2),
                          "total": round(dd.sum() + dh.sum(), 2),
                          "worst_day": round(min(dd.min(), dh.min()), 2),
                          "zero_vol_hours": sum(1 for r in base if not (hour_info(r["ticker"], r["day"].date()).get(str(r["hour_utc"])) or [0])[0])})
    t = pd.DataFrame(table)
    t.to_csv(".cache/liquidity_check.csv", index=False)
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
