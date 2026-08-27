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
    model_prob: float    # RAW model P(this side settles true), pre-shrinkage
    edge_net: float      # per-contract EV after fee, dollars
    ev: float            # total expected value, dollars
    subtitle: str = ""
    market_prob: float = None  # market mid for this side at decision time


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
           max_frac: float = 0.10, min_price: float = 0.0,
           ratio_cap: float = None, model_weight: float = 1.0) -> Decision:
    """Best side (YES/NO taker) for one market, or None if no edge clears fees.

    prob_fn(lo, hi) -> P(lo <= daily high <= hi). Decoupling from the
    distribution's form lets the intraday-conditioned / floored predictive plug
    in exactly like a plain Gaussian.
    market: {ticker, strike_type, floor, cap, yes_ask, no_ask, subtitle}.

    min_price skips any side quoted below it: sub-15c books are where the
    market's intraday information advantage is largest (Aug 25-26: every
    sub-15c buy lost). ratio_cap rejects claims that the market is wrong by
    more than that factor (p/ask) — a disagreement that large means the model
    is missing information, not finding edge. The edge bar also scales with
    price so 5 points of edge on a 2c contract can't clear it.

    model_weight < 1 shrinks the model probability toward the market mid before
    edge/sizing (the book has won 8 of 9 large live disagreements so far); the
    Decision still carries the raw model_prob and the market_prob so every
    trade feeds the calibration ledger that will earn the weight back up.
    """
    lo, hi = market_bounds(market["strike_type"], market.get("floor"), market.get("cap"))
    p_yes = prob_fn(lo, hi)
    ya, yb = market.get("yes_ask"), market.get("yes_bid")
    mid_yes = (ya + yb) / 2 if (ya and yb) else ya

    best = None
    for side, p_raw, ask in (("yes", p_yes, market.get("yes_ask")),
                             ("no", 1 - p_yes, market.get("no_ask"))):
        if not ask or ask <= 0 or ask >= 1:
            continue
        if ask < min_price:
            continue
        p_mkt = None
        p = p_raw
        if mid_yes is not None:
            p_mkt = mid_yes if side == "yes" else 1 - mid_yes
            p = model_weight * p_raw + (1 - model_weight) * p_mkt
        if ratio_cap and p / ask > ratio_cap:
            continue
        edge = (p - ask) - fee(ask)
        required = max(min_edge, 0.35 * ask) if min_price else min_edge
        if edge < required:
            continue
        count = _kelly_count(p, ask, bankroll, kelly_frac, max_frac)
        if count < 1:
            continue
        ev = edge * count
        if best is None or ev > best.ev:
            best = Decision(market["ticker"], side, ask, count, p_raw, edge, ev,
                            market.get("subtitle", ""), market_prob=p_mkt)
    return best


def decisions_for(markets, prob_fn, bankroll: float, **kw):
    """One best decision per market (unsorted, uncapped)."""
    return [d for d in (decide(m, prob_fn, bankroll, **kw) for m in markets) if d]


def robust_edge(market: dict, side: str, shift_fn, delta: float = 1.5,
                toward: float = None) -> float:
    """Worst-case edge if the predictive mean is off by ±delta degrees.

    shift_fn(d) -> prob_fn under a mean shifted by d. A bet that dies from a
    1.5° miss (well inside our error bar) shouldn't be placed at all.

    The survival bar is proportional to price (edge must stay above fee plus
    25% of the ask) — a flat `> 0` bar passes any few-cent longshot, because
    near-zero asks survive any shift, while at-the-money bets die from one.
    That inversion selected every Aug 26 loser. `toward` additionally tests the
    mean moved toward the market's implied mean (clamped to ±delta): that is
    the disagreement actually being priced.
    """
    lo, hi = market_bounds(market["strike_type"], market.get("floor"), market.get("cap"))
    shifts = [-delta, delta]
    if toward is not None:
        shifts.append(max(-delta, min(delta, toward)))
    worst = float("inf")
    for d in shifts:
        p = shift_fn(d)(lo, hi)
        pw = p if side == "yes" else 1 - p
        ask = market.get("yes_ask") if side == "yes" else market.get("no_ask")
        if ask is None:
            return float("-inf")
        worst = min(worst, pw - ask - fee(ask) - 0.25 * ask)
    return worst


def market_implied_mean(markets) -> float:
    """The book's own expected daily high: bucket midpoints weighted by YES mids.

    Open-ended buckets use their inner bound ±1. Returns None on an empty or
    unpriced book."""
    tot_w, tot = 0.0, 0.0
    for m in markets:
        try:
            lo, hi = market_bounds(m["strike_type"], m.get("floor"), m.get("cap"))
        except (ValueError, KeyError):
            continue
        ya, yb = m.get("yes_ask"), m.get("yes_bid")
        px = None
        if ya and yb:
            px = (ya + yb) / 2
        elif ya:
            px = ya
        if not px or px <= 0:
            continue
        mid = ((lo + hi) / 2 if lo is not None and hi is not None
               else (hi - 1 if lo is None else lo + 1))
        tot_w += px
        tot += px * mid
    return tot / tot_w if tot_w > 0 else None


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
