"""Model-vs-market calibration from the live ledger (Fix 06).

Every live fill records (p_model, p_market) at decision time; settlement fills
in the outcome. This reports both sides' Brier scores — LIVE_MODEL_WEIGHT may
rise above 0.5 only when the model's rolling Brier beats the market's over
`--window` settled trades (default 60).

Usage: python -m scripts.calibration_report [window]
"""
import sys

from wx import paper
from scripts.run_live import LIVE_LEDGER


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


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
