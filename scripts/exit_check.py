"""Should we ever exit before settlement? Price-based rules on real hourly bids.

For every trade the live configuration makes in the backtest, walk the hourly
candle bids after entry (already cached for those markets) and apply simple
exit rules, executing at the bid minus taker fee. Compared with holding to
settlement. Model-based exits need re-quotes at every hour; this answers the
cheaper question first: does the price path itself carry an exit signal?

Rules:  hold | take-profit at bid>=0.80 / 0.90 | stop at bid<=50% of entry |
        stop + take-profit 0.90 | sell at the last hour if still below entry (cut)
Usage: python -m scripts.exit_check   (writes .cache/exit_check.csv)
"""
import pandas as pd

from wx import stations, strategies, trading
from scripts import honest_backtest as hb
from scripts.pressure_test import DESIGN, HOLDOUT

RULES = ["hold", "tp80", "tp90", "stop50", "stop50+tp90", "cut_late"]


def side_bid(ticker, hr, side):
    v = hb._cache.get(ticker, {}).get(str(hr))
    if not v:
        return None
    yb, ya = v
    if side == "yes":
        return yb
    return None if ya is None else round(1 - ya, 4)


def exit_pnl(r, rule):
    """P&L under a rule; None if no exit fired (falls back to the hold P&L)."""
    entry, cnt = r["price"], r["count"]
    for hr in range(r["hour_utc"] + 1, 24):
        b = side_bid(r["ticker"], hr, r["side"])
        if b is None:
            continue
        fire = ((rule == "tp80" and b >= 0.80) or (rule == "tp90" and b >= 0.90)
                or (rule == "stop50" and b <= 0.5 * entry)
                or (rule == "stop50+tp90" and (b <= 0.5 * entry or b >= 0.90))
                or (rule == "cut_late" and hr == 23 and b < entry))
        if fire:
            return round(cnt * (b - entry) - trading.fee(entry, cnt) - trading.fee(b, cnt), 2)
    return None


def main():
    recs = []
    for ic in stations.ACTIVE:
        for s, e in (DESIGN, HOLDOUT):
            recs += hb.load_snapshot(ic, s, e)[0]
    rows, _, _ = hb.simulate(recs, strategies.CONTROL)
    out = []
    for rule in RULES:
        tot, fired, hold_when_fired = 0.0, 0, 0.0
        for r in rows:
            p = None if rule == "hold" else exit_pnl(r, rule)
            if p is None:
                tot += r["pnl"]
            else:
                tot += p; fired += 1; hold_when_fired += r["pnl"]
        out.append({"rule": rule, "trades": len(rows), "exits_fired": fired,
                    "total": round(tot, 2), "vs_hold": round(tot - sum(r["pnl"] for r in rows), 2),
                    "hold_pnl_on_exited": round(hold_when_fired, 2)})
    t = pd.DataFrame(out)
    t.to_csv(".cache/exit_check.csv", index=False)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
