"""Render a monitoring dashboard from the paper-trading ledger.

Writes DASHBOARD.md (rendered natively on the GitHub repo page) and, when running
inside GitHub Actions, a short summary to the run page. Regenerated every tick.

Usage: python -m scripts.dashboard
"""
import os
from datetime import datetime, timezone

from wx import paper

BANKROLL = 1000.0


def bucket(f) -> str:
    if f.lo is None:
        return f"≤{int(f.hi)}°"
    if f.hi is None:
        return f"≥{int(f.lo)}°"
    return f"{int(f.lo)}–{int(f.hi)}°"


def build(led: paper.Ledger) -> str:
    s = led.summary()
    fills = led.fills
    closed = [f for f in fills if f.pnl is not None]
    openf = [f for f in fills if f.pnl is None]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    roi = f"{s['roi']*100:+.1f}%" if s["roi"] is not None else "—"
    wr = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else "—"
    realized = f"${s['realized_pnl']:+.2f}"
    open_stake = sum(f.count * f.price for f in openf)

    L = []
    L.append("# 📈 Paper-trading dashboard\n")
    L.append(f"_Updated {now} · bankroll ${BANKROLL:.0f} · stations KNYC · KMDW · KAUS · "
             "no live orders placed_\n")

    L.append("| Realized P&L | ROI (on stake) | Win rate | Closed | Open | Open stake |")
    L.append("|---:|---:|---:|---:|---:|---:|")
    L.append(f"| **{realized}** | **{roi}** | **{wr}** | {s['closed']} | {s['open']} | "
             f"${open_stake:.0f} |")
    L.append("")

    if not fills:
        L.append("> No positions yet. The first in-window tick (10am–4pm ET) will record here.\n")
        return "\n".join(L)

    # per-station
    L.append("### By station\n")
    L.append("| Station | Positions | Open | Closed | Realized P&L |")
    L.append("|:--|--:|--:|--:|--:|")
    for ic in sorted({f.icao for f in fills}):
        fs = [f for f in fills if f.icao == ic]
        cp = sum(f.pnl for f in fs if f.pnl is not None)
        L.append(f"| {ic} | {len(fs)} | {sum(1 for f in fs if f.pnl is None)} | "
                 f"{sum(1 for f in fs if f.pnl is not None)} | ${cp:+.2f} |")
    L.append("")

    # equity curve (cumulative realized P&L by settle day)
    if len(closed) >= 2:
        by_day = {}
        for f in sorted(closed, key=lambda f: f.target):
            by_day[f.target] = by_day.get(f.target, 0) + f.pnl
        cum, xs, ys = 0.0, [], []
        for d in sorted(by_day):
            cum += by_day[d]
            xs.append(f'"{d[5:]}"'); ys.append(f"{cum:.2f}")
        L.append("### Cumulative realized P&L\n")
        L.append("```mermaid")
        L.append("xychart-beta")
        L.append(f'  x-axis [{", ".join(xs)}]')
        L.append(f'  y-axis "USD"')
        L.append(f'  line [{", ".join(ys)}]')
        L.append("```\n")

    # open positions
    if openf:
        L.append("### Open positions\n")
        L.append("| Station | Settles | Bucket | Side | Price | Qty | Type |")
        L.append("|:--|:--|:--|:--|--:|--:|:--|")
        for f in sorted(openf, key=lambda f: (f.target, f.icao))[-20:]:
            L.append(f"| {f.icao} | {f.target} | {bucket(f)} | {f.side.upper()} | "
                     f"${f.price:.2f} | {f.count} | {'maker' if f.maker else 'taker'} |")
        L.append("")

    # settled
    if closed:
        L.append("### Recently settled\n")
        L.append("| Station | Day | Bucket | Side | Settled high | P&L |")
        L.append("|:--|:--|:--|:--|--:|--:|")
        for f in sorted(closed, key=lambda f: f.target)[-20:]:
            hi = f"{int(f.realized)}°" if f.realized is not None else "—"
            L.append(f"| {f.icao} | {f.target} | {bucket(f)} | {f.side.upper()} | {hi} | "
                     f"${f.pnl:+.2f} |")
        L.append("")

    return "\n".join(L)


def main():
    md = build(paper.Ledger())
    with open("DASHBOARD.md", "w") as fh:
        fh.write(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(md)
    print("wrote DASHBOARD.md")


if __name__ == "__main__":
    main()
