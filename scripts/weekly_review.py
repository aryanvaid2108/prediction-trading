"""Monday review: arms vs control, live week, calibration + bias, refreshed
pressure test. Prints the report and writes .cache/weekly_review.txt for ntfy.
Promotion of a paper arm to live is decided from this, never from a good day.

Usage: python -m scripts.weekly_review
"""
import io
import os
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta

from wx import paper, strategies
from scripts import calibration_report, pressure_test
from scripts.run_live import LIVE_LEDGER

PAT_EXPIRES = date.fromisoformat(os.environ.get("PAT_EXPIRES", "2026-10-04"))


def _fill(led):
    fr = calibration_report.fill_rate(led)
    tot_c = sum(v[0] for v in fr.values()); tot_w = sum(v[1] for v in fr.values())
    return f"{tot_c / tot_w:4.0%}" if tot_w else "  — "


def arms_table():
    ctrl = paper.Ledger(paper.arm_ledger("control")).summary()
    L = ["arm          closed  win   realized   vs control   fill"]
    for name in strategies.ARMS:
        led = paper.Ledger(paper.arm_ledger(name))
        s = led.summary()
        wr = f"{s['win_rate']*100:3.0f}%" if s["win_rate"] is not None else "  — "
        L.append(f"{name:12} {s['closed']:6}  {wr}  ${s['realized_pnl']:+8.2f}   "
                 f"{s['realized_pnl'] - ctrl['realized_pnl']:+8.2f}   {_fill(led)}")
    return "\n".join(L)


def morning_alert(days=7):
    """Days in the last week where the 15Z live tick ran but placed nothing."""
    from scripts.run_live import TICK_DIR
    recs = calibration_report._tick_records(TICK_DIR)
    since = (date.today() - timedelta(days=days)).isoformat()
    by_day = {}
    for r in recs:
        if r.get("slot") == 15 and r["target"] >= since:
            by_day.setdefault(r["target"], False)
            if r.get("pick"):
                by_day[r["target"]] = True
    quiet = [d for d, traded in sorted(by_day.items()) if not traded]
    if len(quiet) >= 4:
        return f"⚠️ morning slot found no trade on {len(quiet)} of {len(by_day)} days — the morning edge may have moved."
    return f"morning slot: traded on {sum(by_day.values())} of {len(by_day)} days it ran."


def live_week():
    led = paper.Ledger(LIVE_LEDGER)
    since = (date.today() - timedelta(days=7)).isoformat()
    wk = [f for f in led.fills if f.target >= since and f.pnl is not None]
    tot = sum(f.pnl for f in wk)
    return (f"live last 7d: {len(wk)} settled, ${tot:+.2f}, "
            f"{sum(f.pnl > 0 for f in wk)} wins; cumulative ${sum(f.pnl for f in led.fills if f.pnl is not None):+.2f}; "
            f"fill rate {_fill(led)}")


def main():
    out = io.StringIO()
    with redirect_stdout(out):
        print(f"WEEKLY REVIEW {date.today()}\n")
        print(live_week(), "\n")
        print("paper arms (each differs from control by one parameter):")
        print(arms_table(), "\n")
        print("calibration (live fills, model vs market Brier):")
        calibration_report.main()
        print("\nmodel bias by station (tick ledger vs CLI):")
        calibration_report.bias()
        print("\nlive Brier by slot, all priced buckets (edge-decay monitor):")
        calibration_report.brier_by_hour()
        print("\n" + morning_alert())
        days_left = (PAT_EXPIRES - date.today()).days
        if days_left <= 14:
            print(f"\n⚠️ cron-job.org GitHub token expires in {days_left} days ({PAT_EXPIRES}) — regenerate it.")
    short = out.getvalue()
    print(short)
    (paper.LEDGER_DIR / "weekly_review.txt").write_text(short[:3800])
    print("\npressure test (design Jul 1-Aug 24, holdout Aug 25-yesterday):")
    pressure_test.main()


if __name__ == "__main__":
    main()
