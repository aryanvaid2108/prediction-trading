"""Paper-trading ledger: record dry-run positions, settle against the official
CLI high, and report realized P&L. Lets the strategy be forward-tested for real
money outcomes without ever placing a live order.
"""
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from . import cli, trading
from .stations import get

LEDGER_DIR = Path(__file__).resolve().parent.parent / ".cache"


def arm_ledger(arm: str) -> Path:
    """One paper ledger per strategy arm (see wx.strategies.ARMS)."""
    return LEDGER_DIR / f"paper_{arm}.json"


@dataclass
class Fill:
    ticker: str
    icao: str
    target: str        # ISO date the market settles on
    side: str          # yes / no
    lo: float          # inclusive bucket bounds (None = open)
    hi: float
    price: float
    count: int
    maker: bool = False
    realized: float = None   # filled in at settlement
    pnl: float = None
    fee: float = None        # total dollars of fees actually paid at fill (None = estimate)
    p_model: float = None    # raw model P(side wins) at decision time — calibration ledger
    p_market: float = None   # market mid for the side at decision time


def settle_pnl(f: Fill, realized_high: float) -> float:
    """Realized dollars for one position given the settling daily high."""
    in_bucket = ((f.lo is None or realized_high >= f.lo) and
                 (f.hi is None or realized_high <= f.hi))
    win = in_bucket if f.side == "yes" else not in_bucket
    gross = f.count * (1 - f.price) if win else -f.count * f.price
    charge = f.fee if f.fee is not None else (0.0 if f.maker else trading.fee(f.price, f.count))
    return round(gross - charge, 2)


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.fills = []
        if self.path.exists():
            self.fills = [Fill(**d) for d in json.loads(self.path.read_text())]

    def add(self, f: Fill):
        self.fills.append(f)

    def has_positions(self, icao: str, target: str) -> bool:
        """True if this station/day was already recorded (keeps the daily job idempotent)."""
        return any(f.icao == icao and f.target == target for f in self.fills)

    def held_markets(self, icao: str, target: str) -> set:
        """(ticker, side) already held for a station/day — so intraday ticks don't
        re-enter the same bucket as the distribution sharpens through the day."""
        return {(f.ticker, f.side) for f in self.fills if f.icao == icao and f.target == target}

    def staked_on(self, icao: str, target: str) -> float:
        """Cumulative dollars staked on a station/day, to cap exposure across ticks."""
        return sum(f.count * f.price for f in self.fills if f.icao == icao and f.target == target)

    def save(self):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps([asdict(f) for f in self.fills], indent=2))

    def settle_due(self, today: date = None) -> dict:
        """Settle any unresolved positions whose target day is over, via CLI."""
        today = today or date.today()
        settled, realized_total = 0, 0.0
        by_station = {}
        for f in self.fills:
            if f.pnl is not None or date.fromisoformat(f.target) >= today:
                continue
            by_station.setdefault(f.icao, {})
        for icao in by_station:
            days = [date.fromisoformat(f.target) for f in self.fills
                    if f.icao == icao and f.pnl is None]
            if not days:
                continue
            highs = cli.settlement_high(icao, min(days), max(days))
            by_station[icao] = {d.date(): v for d, v in highs.items()}
        for f in self.fills:
            if f.pnl is not None or date.fromisoformat(f.target) >= today:
                continue
            rh = by_station.get(f.icao, {}).get(date.fromisoformat(f.target))
            if rh is None:
                continue
            f.realized = float(rh)
            f.pnl = settle_pnl(f, f.realized)
            settled += 1
            realized_total += f.pnl
        self.save()
        return {"settled": settled, "realized_pnl": round(realized_total, 2),
                "open": sum(1 for f in self.fills if f.pnl is None)}

    def summary(self) -> dict:
        closed = [f for f in self.fills if f.pnl is not None]
        wins = sum(1 for f in closed if f.pnl > 0)
        staked = sum(f.count * f.price for f in closed)
        realized = sum(f.pnl for f in closed)
        return {
            "positions": len(self.fills),
            "closed": len(closed),
            "open": len(self.fills) - len(closed),
            "win_rate": round(wins / len(closed), 3) if closed else None,
            "staked": round(staked, 2),
            "realized_pnl": round(realized, 2),
            "roi": round(realized / staked, 3) if staked else None,
        }
