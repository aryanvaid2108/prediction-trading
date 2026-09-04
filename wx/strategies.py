"""Strategy arms: one parameter set per arm and ONE selector shared by the live
loop and every paper arm, so a paper arm's P&L is a fair test of exactly one
change against the live configuration.

Tick slots live here too: the edge is morning-shaped (Brier lead at 15Z/17Z,
gone by 19Z), so placement is allowed only inside a slot — a late cron firing
at 21Z must not trade, and a 13:50 ET run must not count as the morning tick.
"""
from dataclasses import dataclass
from datetime import datetime

from . import trading

SLOTS_UTC = (15, 17, 19)
SLOT_MINUTES = 75


def slot_for(now_utc: datetime, slots=SLOTS_UTC, minutes: int = SLOT_MINUTES):
    """The slot hour this instant belongs to, or None (outside every slot)."""
    m = now_utc.hour * 60 + now_utc.minute
    for s in slots:
        if s * 60 <= m < s * 60 + minutes:
            return s
    return None


@dataclass(frozen=True)
class Arm:
    name: str
    min_edge: float = 0.05
    kelly_frac: float = 0.25
    min_price: float = 0.15       # no sub-15c longshots (Aug 25-26: all lost)
    ratio_cap: float = 2.5        # max p/ask disagreement we trust
    model_weight: float = 0.5     # shrink toward the book until Brier earns it up
    robust_delta: float = 1.5     # edge must survive a ±delta° mean miss; 0 = gate off
    toward_market: bool = True    # also shift the mean toward the book's implied mean

    def decide_kw(self) -> dict:
        return dict(min_edge=self.min_edge, kelly_frac=self.kelly_frac,
                    min_price=self.min_price, ratio_cap=self.ratio_cap,
                    model_weight=self.model_weight)


CONTROL = Arm("control")                                  # == the live configuration
ARMS = {
    "control": CONTROL,
    "model_w1": Arm("model_w1", model_weight=1.0),        # is shrinkage still needed?
    "model_w075": Arm("model_w075", model_weight=0.75),
    "no_gate": Arm("no_gate", robust_delta=0.0),          # what does the robust gate kill?
}


@dataclass
class Candidate:
    decision: trading.Decision
    worst_edge: float     # robust-gate margin (inf when the gate is off)

    @property
    def gated(self) -> bool:
        return self.worst_edge > 0


def select(markets, quote, bankroll: float, arm: Arm):
    """(pick, candidates): each market's best side with its gate verdict, and the
    single highest-EV survivor (one thesis per station per day)."""
    by = {m["ticker"]: m for m in markets}
    decisions = trading.decisions_for(markets, quote.prob_fn, bankroll, **arm.decide_kw())
    imp = trading.market_implied_mean(markets)
    toward = (imp - quote.mu) if (arm.toward_market and imp is not None) else None
    cands = []
    for d in decisions:
        if arm.robust_delta and quote.shift_fn is not None:
            w = trading.robust_edge(by[d.ticker], d.side, quote.shift_fn, arm.robust_delta,
                                    toward=toward)
        else:
            w = float("inf")
        cands.append(Candidate(d, w))
    ok = [c.decision for c in cands if c.gated]
    return (max(ok, key=lambda d: d.ev) if ok else None), cands
