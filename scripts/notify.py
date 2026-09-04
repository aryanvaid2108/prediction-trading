"""Print a short per-arm status for a phone notification (used by the Actions loop)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from wx import paper, strategies


def line() -> str:
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    parts = []
    for name in strategies.ARMS:
        led = paper.Ledger(paper.arm_ledger(name))
        s = led.summary()
        n_today = sum(1 for f in led.fills if f.target == today)
        roi = f" ROI {s['roi']*100:+.0f}%" if s["roi"] is not None else ""
        parts.append(f"{name}: today {n_today} · open {s['open']} · ${s['realized_pnl']:+.2f}{roi}")
    return "\n".join(parts)


if __name__ == "__main__":
    print(line())
