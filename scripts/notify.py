"""Print a one-line status for a phone notification (used by the Actions loop)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from wx import paper

STATIONS = ["KNYC", "KMDW", "KAUS"]


def line() -> str:
    led = paper.Ledger()
    s = led.summary()
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    per = " ".join(
        f"{ic}:{sum(1 for f in led.fills if f.icao == ic and f.target == today)}"
        for ic in STATIONS
    )
    roi = f" ROI {s['roi']*100:+.0f}%" if s["roi"] is not None else ""
    return f"today {per} · open {s['open']} · realized ${s['realized_pnl']:+.2f}{roi}"


if __name__ == "__main__":
    print(line())
