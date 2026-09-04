"""Model-vs-market calibration from the live ledger (Fix 06).

Every live fill records (p_model, p_market) at decision time; settlement fills
in the outcome. This reports both sides' Brier scores — LIVE_MODEL_WEIGHT may
rise above 0.5 only when the model's rolling Brier beats the market's over
`--window` settled trades (default 60).

Also reports the model's bias per station from the tick ledger (every live
quote's μ/σ vs the official CLI high): the Aug 28-Sep 3 retro found a +0.35°F
warm bias and 6/42 misses beyond 2σ, with KAUS at +1.35°F — the bench rule.

Usage: python -m scripts.calibration_report [window]
"""
import json
import sys
from datetime import date

from wx import cli, paper
from scripts.run_live import LIVE_LEDGER, TICK_LOG


def main(window: int = 60):
    led = paper.Ledger(LIVE_LEDGER)
    rows = [(f, 1.0 if f.pnl > 0 else 0.0) for f in led.fills
            if f.pnl is not None and f.p_model is not None and f.p_market is not None]
    rows = rows[-int(window):]
    if not rows:
        print("no settled trades carry (p_model, p_market) yet — ledger predates Fix 06")
        return None
    bm = sum((f.p_model - y) ** 2 for f, y in rows) / len(rows)
    bk = sum((f.p_market - y) ** 2 for f, y in rows) / len(rows)
    print(f"{len(rows)} settled trades  Brier model {bm:.4f}  vs market {bk:.4f}  "
          f"-> {'MODEL leads (weight may rise)' if bm < bk else 'market leads (keep weight <= 0.5)'}")
    return {"n": len(rows), "brier_model": round(bm, 4), "brier_market": round(bk, 4)}


def bias(path=TICK_LOG):
    """Per-station mean (CLI high − μ) and >2σ miss rate, first quote of each day."""
    if not path.exists():
        print("no tick ledger yet")
        return {}
    first = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if "mu" in r and (r["icao"], r["target"]) not in first:
            first[(r["icao"], r["target"])] = r
    out = {}
    for icao in sorted({k[0] for k in first}):
        days = sorted(date.fromisoformat(k[1]) for k in first if k[0] == icao)
        try:
            highs = {d.date(): v for d, v in cli.settlement_high(icao, days[0], days[-1]).items()}
        except Exception as e:
            print(f"  {icao}: CLI unavailable ({type(e).__name__})")
            continue
        errs = [(highs[d] - first[(icao, d.isoformat())]["mu"], first[(icao, d.isoformat())]["sigma"])
                for d in days if d in highs]
        if not errs:
            continue
        mean = sum(e for e, _ in errs) / len(errs)
        big = sum(abs(e) > 2 * s for e, s in errs)
        out[icao] = {"n": len(errs), "bias": round(mean, 2), "over_2sigma": big}
        print(f"  {icao}: n={len(errs):3}  bias {mean:+.2f}F  >2σ misses {big}/{len(errs)}"
              f"{'  <- warm-bias watch' if mean >= 1.0 and len(errs) >= 5 else ''}")
    return out


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
    print("\nmodel bias by station (first quote of each day vs CLI high):")
    bias()
