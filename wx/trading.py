"""Trading decision engine: model distribution + Kalshi prices -> sized orders.

Pure and side-effect free so it is fully unit-testable. Order *placement* lives
in kalshi.py and defaults to dry-run.
"""
import math
from dataclasses import dataclass

import numpy as np

from scipy.stats import norm

FEE_RATE = 0.07  # Kalshi taker fee = ceil(0.07 * n * P * (1-P)) dollars


def fee(price: float, contracts: int = 1) -> float:
    """Kalshi taker fee in dollars, rounded up to the next cent.

    round() first strips floating-point noise so an exact boundary (e.g. 175.0c)
    isn't bumped up a whole cent by a 1e-13 artifact.
    """
    cents = FEE_RATE * contracts * price * (1 - price) * 100
    return math.ceil(round(cents, 6)) / 100


def market_bounds(strike_type: str, floor, cap):
    """Inclusive integer [lo, hi] the daily high must land in for YES to settle
    true. None means open-ended. Temperatures settle in whole degrees F."""
    if strike_type == "between":
        return floor, cap
    if strike_type == "less":       # e.g. cap=78 -> "77 or below"
        return None, int(cap) - 1
    if strike_type == "greater":    # e.g. floor=85 -> "86 or above"
        return int(floor) + 1, None
    raise ValueError(f"unknown strike_type {strike_type}")


def prob_range(mu: float, sigma: float, lo, hi) -> float:
    """P(lo <= high <= hi) under N(mu, sigma), integrating over +/-0.5F rounding."""
    hi_cdf = 1.0 if hi is None else norm.cdf((hi + 0.5 - mu) / sigma)
    lo_cdf = 0.0 if lo is None else norm.cdf((lo - 0.5 - mu) / sigma)
    return float(hi_cdf - lo_cdf)


@dataclass
class Decision:
    ticker: str
    side: str            # "yes" or "no"
    price: float         # taker price paid per contract, dollars
    count: int
    model_prob: float    # model P(this side settles true)
    edge_net: float      # per-contract EV after fee, dollars
    ev: float            # total expected value, dollars
    subtitle: str = ""


def _kelly_count(p: float, price: float, bankroll: float, kelly_frac: float,
                 max_frac: float) -> int:
    f_star = (p - price) / (1 - price)          # Kelly fraction of bankroll
    frac = min(kelly_frac * f_star, max_frac)
    return int(frac * bankroll / price) if frac > 0 else 0


def maker_price(market: dict, side: str):
    """Resting (maker) price for one side: join the best bid, improving by 1c when
    the spread allows. Maker orders pay ~0 fee but may not fill on thin books."""
    bid = market.get(f"{side}_bid")
    ask = market.get(f"{side}_ask")
    if bid is None:
        return None
    if ask is not None and round((ask - bid) * 100) > 1:  # cents, dodges FP noise
        return round(bid + 0.01, 2)
    return bid


def gaussian_prob(mu: float, sigma: float):
    """A prob_fn(lo, hi) for a Gaussian predictive — the simple EMOS case."""
    return lambda lo, hi: prob_range(mu, sigma, lo, hi)


def sample_prob(samples):
    """A prob_fn(lo, hi) from predictive samples. Settlement is whole degrees, so
    buckets are evaluated on rounded samples."""
    rs = np.round(np.asarray(samples, float))

    def prob(lo, hi):
        m = np.ones(len(rs), dtype=bool)
        if lo is not None:
            m &= rs >= lo
        if hi is not None:
            m &= rs <= hi
        return float(m.mean())
    return prob


def floored_gaussian_prob(mu: float, sigma: float, floor: float):
    """Gaussian predictive truncated below `floor` — the daily high can never be
    less than the max already observed today. Renormalizes over [floor-0.5, inf)."""
    a = floor - 0.5
    den = 1.0 - norm.cdf((a - mu) / sigma)

    def prob(lo, hi):
        if den <= 0:
            return 1.0 if (hi is None or hi >= floor) else 0.0
        L = a if lo is None else max(lo - 0.5, a)
        H = float("inf") if hi is None else hi + 0.5
        if H <= a:
            return 0.0
        hi_cdf = 1.0 if hi is None else norm.cdf((H - mu) / sigma)
        return float((hi_cdf - norm.cdf((L - mu) / sigma)) / den)

    return prob


def decide(market: dict, prob_fn, bankroll: float,
           min_edge: float = 0.02, kelly_frac: float = 0.25,
           max_frac: float = 0.10) -> Decision:
    """Best side (YES/NO taker) for one market, or None if no edge clears fees.

    prob_fn(lo, hi) -> P(lo <= daily high <= hi). Decoupling from the
    distribution's form lets the intraday-conditioned / floored predictive plug
    in exactly like a plain Gaussian.
    market: {ticker, strike_type, floor, cap, yes_ask, no_ask, subtitle}.
    """
    lo, hi = market_bounds(market["strike_type"], market.get("floor"), market.get("cap"))
    p_yes = prob_fn(lo, hi)

    best = None
    for side, p, ask in (("yes", p_yes, market.get("yes_ask")),
                         ("no", 1 - p_yes, market.get("no_ask"))):
        if not ask or ask <= 0 or ask >= 1:
            continue
        edge = (p - ask) - fee(ask)
        if edge < min_edge:
            continue
        count = _kelly_count(p, ask, bankroll, kelly_frac, max_frac)
        if count < 1:
            continue
        ev = edge * count
        if best is None or ev > best.ev:
            best = Decision(market["ticker"], side, ask, count, p, edge, ev,
                            market.get("subtitle", ""))
    return best


def decisions_for(markets, prob_fn, bankroll: float, **kw):
    """One best decision per market (unsorted, uncapped)."""
    return [d for d in (decide(m, prob_fn, bankroll, **kw) for m in markets) if d]


def cap_exposure(decisions, budget_dollars: float):
    """Keep the highest-EV decisions that fit a dollar budget, scaling the last."""
    kept, spent = [], 0.0
    for d in sorted(decisions, key=lambda d: d.ev, reverse=True):
        cost = d.count * d.price
        if spent + cost > budget_dollars:
            d.count = int(max(0.0, budget_dollars - spent) / d.price)
            if d.count < 1:
                continue
            d.ev, cost = d.edge_net * d.count, d.count * d.price
        kept.append(d)
        spent += cost
    return kept


def plan(markets, prob_fn, bankroll: float, max_total_frac: float = 0.25, **kw):
    """Decisions across an event's markets, capped at max_total_frac of bankroll.

    Calibration (sigma inflation, intraday floor) lives in the prob_fn; the
    portfolio control here is the aggregate exposure cap.
    """
    return cap_exposure(decisions_for(markets, prob_fn, bankroll, **kw),
                        max_total_frac * bankroll)
