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
from scripts.run_live import LIVE_LEDGER, TICK_DIR


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


def bias(tick_dir=TICK_DIR):
    """Per-station mean (CLI high − μ) and >2σ miss rate, first quote of each day."""
    files = sorted(tick_dir.glob("*.jsonl")) if tick_dir.exists() else []
    if not files:
        print("no tick ledger yet")
        return {}
    first = {}
    for line in (l for f in files for l in f.read_text().splitlines()):
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


def _tick_records(tick_dir):
    files = sorted(tick_dir.glob("*.jsonl")) if tick_dir.exists() else []
    return [json.loads(l) for f in files for l in f.read_text().splitlines()]


def brier_by_hour(tick_dir=TICK_DIR):
    """Live model-vs-market Brier on EVERY priced bucket, by slot, once the day
    has settled — the edge-decay monitor (the backtest's 15Z/17Z lead should show here)."""
    recs = [r for r in _tick_records(tick_dir) if r.get("buckets") and r.get("slot")]
    if not recs:
        print("no bucket records yet")
        return {}
    highs = {}
    for icao in sorted({r["icao"] for r in recs}):
        days = sorted(date.fromisoformat(r["target"]) for r in recs if r["icao"] == icao)
        try:
            highs[icao] = {d.date(): v for d, v in cli.settlement_high(icao, days[0], days[-1]).items()}
        except Exception as e:
            print(f"  {icao}: CLI unavailable ({type(e).__name__})")
    by = {}
    for r in recs:
        y = highs.get(r["icao"], {}).get(date.fromisoformat(r["target"]))
        if y is None:
            continue
        for b in r["buckets"]:
            hit = 1.0 if ((b["lo"] is None or y >= b["lo"]) and (b["hi"] is None or y <= b["hi"])) else 0.0
            by.setdefault(r["slot"], []).append(((b["p_model"] - hit) ** 2, (b["p_market"] - hit) ** 2))
    out = {}
    for slot in sorted(by):
        n = len(by[slot]); bm = sum(a for a, _ in by[slot]) / n; bk = sum(b for _, b in by[slot]) / n
        out[slot] = {"n": n, "model": round(bm, 4), "market": round(bk, 4)}
        print(f"  {slot:02d}Z: n={n:4}  model {bm:.4f}  market {bk:.4f}  "
              f"{'MODEL' if bm < bk else 'market'} leads by {abs(bm - bk):.4f}")
    return out


def fill_rate(led):
    """(filled, wanted) over fills that recorded a request size, by price tier."""
    tiers = {"<0.20": [0, 0], ">=0.20": [0, 0]}
    for f in led.fills:
        if f.wanted:
            t = tiers["<0.20" if f.price < 0.20 else ">=0.20"]
            t[0] += f.count; t[1] += f.wanted
    return {k: (v[0], v[1], round(v[0] / v[1], 2) if v[1] else None) for k, v in tiers.items()}


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
    print("\nmodel bias by station (first quote of each day vs CLI high):")
    bias()
    print("\nlive Brier by slot (all priced buckets):")
    brier_by_hour()
