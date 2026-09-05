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
    robust_delta: float = 1.0     # edge must survive a ±delta° mean miss; 0 = gate off (was 1.5 until Sep 4)
    toward_market: bool = True    # also shift the mean toward the book's implied mean
    ticks: tuple = SLOTS_UTC      # slots this arm may enter at
    about: str = ""               # one plain-English line for the dashboard

    def decide_kw(self) -> dict:
        return dict(min_edge=self.min_edge, kelly_frac=self.kelly_frac,
                    min_price=self.min_price, ratio_cap=self.ratio_cap,
                    model_weight=self.model_weight)


CONTROL = Arm("control")                                  # == the live configuration
# Each arm changes ONE thing. The Sep 4 pressure test (scripts.pressure_test,
# Jul 1-Aug 24 design + Aug 25-Sep 3 holdout) picked them: the robust gate was
# the costliest rule in the backtest (no gate +$4,291 vs ±1.5° +$758, positive
# in both months and in the holdout), ±1.0° kept most of that under real hourly
# volume caps and live fill ratios (scripts.liquidity_check) and went LIVE on
# Sep 4 evening; ±1.5° stays as the counterfactual. w=0.25 had the smallest
# drawdown, and the morning slot was the only one positive out of sample.
ARMS = {
    "control": Arm("control", about="Exactly the live rules. Every other arm is judged against this one."),
    "no_gate": Arm("no_gate", robust_delta=0.0,
                   about="No forecast-error check at all. Backtest's best result; takes ~3 trades a day."),
    "gate_15": Arm("gate_15", robust_delta=1.5,
                   about="The stricter 1.5°F check that was live until Sep 4."),
    "model_w1": Arm("model_w1", model_weight=1.0,
                    about="Trusts the model fully, no blending with the market price."),
    "model_w025": Arm("model_w025", model_weight=0.25,
                      about="Leans 75% on the market price. Fewest trades, smallest drawdown in backtest."),
    "early": Arm("early", ticks=(15, 17),
                 about="Enters only at the 11:00 and 13:00 ET ticks, never the afternoon."),
}

# What changed in the LIVE rules and why — newest first. Shown on the dashboard.
CHANGES = [
    ("2026-09-05", "Nine cities added (Houston, Atlanta, Dallas, Las Vegas, Minneapolis, New Orleans, "
                   "Oklahoma City, Phoenix, Seattle): each passed the live-rules backtest with real-volume "
                   "fill caps and matched Kalshi's settlement 41 of 41 days. Daily budget now goes to the "
                   "best-EV trades first."),
    ("2026-09-04", "Forecast-error check loosened from 1.5°F to 1.0°F: it was blocking most of the "
                   "backtest's profit, and the looser check held up under real hourly volume caps."),
    ("2026-09-04", "Trades only inside three tick slots (11:00, 13:00, 15:00 ET); orders re-priced on "
                   "the live order book and capped at resting depth before sending."),
    ("2026-08-27", "15¢ price floor, 2.5x disagreement cap, 50/50 blend with the market price, "
                   "$15 daily loss kill-switch — after the Aug 25-26 losses."),
    ("2026-08-25", "Live with real money at a $150 canary bankroll."),
]

ET = {15: "11:00", 17: "13:00", 19: "15:00"}


def describe(arm: Arm, bankroll: float, loss_cap: float, station_frac: float = 0.25,
             slots=SLOTS_UTC, stations=()) -> list:
    """The rules in plain English, generated from the values that actually run."""
    L = []
    if stations:
        L.append(f"Trades Kalshi daily high-temperature markets for {len(stations)} cities: "
                 + ", ".join(stations) + ".")
    L.append("Looks for trades at " + ", ".join(ET.get(h, f"{h:02d}Z") for h in slots)
             + f" ET, and may place them for {SLOT_MINUTES} minutes after each.")
    L.append(f"Bankroll ${bankroll:,.0f}. A day that loses more than ${loss_cap:,.0f} halts trading "
             f"until you resume it by hand.")
    L.append(f"At most one position per city per day, and no more than {station_frac:.0%} of bankroll "
             f"(${bankroll * station_frac:,.2f}) on any city.")
    L.append(f"The model's probability is blended {arm.model_weight:.0%} model / {1 - arm.model_weight:.0%} "
             f"market price before deciding.")
    L.append(f"Buys only when that blended probability beats the ask by at least {arm.min_edge * 100:.0f} points "
             f"or {35}% of the ask, whichever is larger, after fees.")
    L.append(f"Never buys below {arm.min_price * 100:.0f}¢"
             + (f", and never when the model says the market is wrong by more than {arm.ratio_cap:g}×." if arm.ratio_cap else "."))
    if arm.robust_delta:
        L.append(f"Rejects a trade that would stop being profitable if the forecast were off by "
                 f"{arm.robust_delta:g}°F" + (" in either direction or toward the market's own view." if arm.toward_market else "."))
    else:
        L.append("No forecast-error check.")
    L.append(f"Sizes at {arm.kelly_frac:g}× Kelly. Orders are immediate-or-cancel at the live order book's "
             f"ask, capped at what is actually resting there.")
    return L



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
