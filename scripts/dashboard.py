"""Render DASHBOARD.md from the paper-arm ledgers: one comparison table (every
arm vs the control), then the control arm's detail. Regenerated every tick.

Usage: python -m scripts.dashboard
"""
import os
from datetime import datetime, timezone

from wx import paper, stations, strategies
from scripts.dashboard_data import strategy

BANKROLL = 150.0


def bucket(f) -> str:
    if f.lo is None:
        return f"≤{int(f.hi)}°"
    if f.hi is None:
        return f"≥{int(f.lo)}°"
    return f"{int(f.lo)}–{int(f.hi)}°"


def _fmt(s):
    roi = f"{s['roi']*100:+.1f}%" if s["roi"] is not None else "—"
    wr = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else "—"
    return f"${s['realized_pnl']:+.2f}", roi, wr


def build(ledgers: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st = strategy()
    L = ["# 📈 Live strategy and paper arms\n",
         "## What the live bot is doing right now\n",
         "_Generated from the settings the live loop runs with._\n"]
    L += [f"- {r}" for r in st["rules"]]
    L += ["", "**Changes to the live rules**", ""] + [f"- `{c['date']}` {c['what']}" for c in st["changes"]]
    L += ["", "## Paper arms\n",
         f"_Updated {now} · bankroll ${BANKROLL:.0f} per arm · stations "
         f"{' · '.join(stations.ACTIVE)} · fills at the live book's touch, depth-capped · "
         f"no live orders placed_\n",
         "| Arm | Differs from control | Realized P&L | ROI (on stake) | Win rate | Closed | Open |",
         "|:--|:--|---:|---:|---:|---:|---:|"]
    ctrl = strategies.CONTROL
    for name, led in ledgers.items():
        arm = strategies.ARMS[name]
        diff = ", ".join(f"{k}={v}" for k, v in vars(arm).items()
                         if k not in ("name", "about") and v != getattr(ctrl, k)) or "live config"
        s = led.summary()
        realized, roi, wr = _fmt(s)
        L.append(f"| **{name}** | {diff} — {arm.about} | **{realized}** | {roi} | {wr} | {s['closed']} | {s['open']} |")
    L.append("")

    led = ledgers["control"]
    fills = led.fills
    closed = [f for f in fills if f.pnl is not None]
    openf = [f for f in fills if f.pnl is None]
    if not fills:
        L.append("> No positions yet. The first in-slot tick (15Z / 17Z / 19Z) will record here.\n")
        return "\n".join(L)

    L.append("## Control arm\n")
    L.append("### By station\n")
    L.append("| Station | Positions | Open | Closed | Realized P&L |")
    L.append("|:--|--:|--:|--:|--:|")
    for ic in sorted({f.icao for f in fills}):
        fs = [f for f in fills if f.icao == ic]
        cp = sum(f.pnl for f in fs if f.pnl is not None)
        L.append(f"| {ic} | {len(fs)} | {sum(1 for f in fs if f.pnl is None)} | "
                 f"{sum(1 for f in fs if f.pnl is not None)} | ${cp:+.2f} |")
    L.append("")

    if len(closed) >= 2:
        by_day = {}
        for f in sorted(closed, key=lambda f: f.target):
            by_day[f.target] = by_day.get(f.target, 0) + f.pnl
        cum, xs, ys = 0.0, [], []
        for d in sorted(by_day):
            cum += by_day[d]
            xs.append(f'"{d[5:]}"'); ys.append(f"{cum:.2f}")
        L += ["### Cumulative realized P&L\n", "```mermaid", "xychart-beta",
              f'  x-axis [{", ".join(xs)}]', '  y-axis "USD"', f'  line [{", ".join(ys)}]', "```\n"]

    if openf:
        L.append("### Open positions\n")
        L.append("| Station | Settles | Bucket | Side | Price | Qty |")
        L.append("|:--|:--|:--|:--|--:|--:|")
        for f in sorted(openf, key=lambda f: (f.target, f.icao))[-20:]:
            L.append(f"| {f.icao} | {f.target} | {bucket(f)} | {f.side.upper()} | ${f.price:.2f} | {f.count} |")
        L.append("")

    if closed:
        L.append("### Recently settled\n")
        L.append("| Station | Day | Bucket | Side | Settled high | P&L |")
        L.append("|:--|:--|:--|:--|--:|--:|")
        for f in sorted(closed, key=lambda f: f.target)[-20:]:
            hi = f"{int(f.realized)}°" if f.realized is not None else "—"
            L.append(f"| {f.icao} | {f.target} | {bucket(f)} | {f.side.upper()} | {hi} | ${f.pnl:+.2f} |")
        L.append("")
    return "\n".join(L)


def main():
    md = build({name: paper.Ledger(paper.arm_ledger(name)) for name in strategies.ARMS})
    with open("DASHBOARD.md", "w") as fh:
        fh.write(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(md)
    print("wrote DASHBOARD.md")


if __name__ == "__main__":
    main()
