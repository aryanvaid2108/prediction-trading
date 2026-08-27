"""Fix 03+04 proof: replay the five REAL Aug 26 live trades through the new filters.

Each case uses the μ/σ the engine actually logged at placement (GitHub Actions
runs 32986472118 / 32987948682 / 33004128510) and the real ask. All five settled
as LOSSES (official CLI: NYC 81, MDW 85, SFO 77, LAX 87, PHL 86) for −$24.78.
The old parameters accepted them; the new floor/ratio/relative-edge rules must
reject every one.
"""
from wx import trading

BANKROLL = 150.0
OLD = dict(min_edge=0.05, kelly_frac=0.25)
NEW = dict(min_edge=0.05, kelly_frac=0.25, min_price=0.15, ratio_cap=2.5)

# (name, mu, sigma at placement, market dict, losing side taken live)
LIVE_TRADES = [
    ("KNYC <=79 @13c", 79.4, 1.99,
     {"ticker": "T80", "strike_type": "less", "cap": 80, "yes_ask": 0.13, "no_ask": 0.89}, "yes"),
    ("KMDW 81-82 @3c", 82.9, 2.23,
     {"ticker": "B81.5", "strike_type": "between", "floor": 81, "cap": 82, "yes_ask": 0.03, "no_ask": 0.98}, "yes"),
    ("KSFO <=71 @10c", 73.2, 3.88,
     {"ticker": "T72", "strike_type": "less", "cap": 72, "yes_ask": 0.10, "no_ask": 0.92}, "yes"),
    ("KLAX 82-83 @2c", 86.8, 1.83,
     {"ticker": "B82.5", "strike_type": "between", "floor": 82, "cap": 83, "yes_ask": 0.02, "no_ask": 0.99}, "yes"),
    ("KPHL no 85-86 @15c", 85.2, 0.71,
     {"ticker": "B85.5", "strike_type": "between", "floor": 85, "cap": 86, "yes_ask": 0.84, "no_ask": 0.15}, "no"),
]


def _decide(m, mu, sigma, params):
    return trading.decide(m, trading.gaussian_prob(mu, sigma), BANKROLL, **params)


def test_old_params_accepted_the_losing_trades():
    accepted = [name for name, mu, s, m, side in LIVE_TRADES
                if (d := _decide(m, mu, s, OLD)) and d.side == side]
    # Gaussian stand-in is thinner-tailed than the live mixture (KLAX/KPHL only
    # cleared min_edge via mixture tails) — the three fat-edge losers still accept
    assert len(accepted) >= 3, accepted


def test_new_params_reject_every_losing_trade():
    for name, mu, s, m, side in LIVE_TRADES:
        d = _decide(m, mu, s, NEW)
        assert d is None or d.side != side, f"{name} still accepted: {d}"


def test_new_params_still_accept_a_legitimate_bet():
    # mid-priced bucket at the model mean with real edge: must survive the filters
    m = {"ticker": "B80.5", "strike_type": "between", "floor": 80, "cap": 81,
         "yes_ask": 0.25, "no_ask": 0.77}
    d = _decide(m, 80.5, 1.2, NEW)   # p≈0.40 vs ask 0.25
    assert d is not None and d.side == "yes"


def test_robust_gate_demands_margin_proportional_to_price():
    # worst-case shifted edge of +0.02 on a 30c ask: flat `> 0` bar passed it,
    # the proportional bar (fee + 25% of ask) rejects — a 1.5° miss inside our
    # own error bar must not zero the bet out
    m = {"ticker": "B80.5", "strike_type": "between", "floor": 80, "cap": 81,
         "yes_ask": 0.30, "no_ask": 0.72}
    shift = lambda d: trading.gaussian_prob(80.5 + d, 1.2)
    flat_worst = min(shift(d)(80, 81) - 0.30 for d in (-1.5, 1.5))
    assert flat_worst > 0                                # old gate would pass
    assert trading.robust_edge(m, "yes", shift, 1.5) < 0  # new gate rejects


def test_toward_market_shift_tightens_the_gate():
    # KSFO-shaped disagreement: model μ=73.2, book implied ~76 (book was right).
    # Moving μ toward the market can only lower the worst case, never raise it.
    m = {"ticker": "T74", "strike_type": "less", "cap": 74, "yes_ask": 0.30, "no_ask": 0.72}
    shift = lambda d: trading.gaussian_prob(73.2 + d, 2.0)
    plain = trading.robust_edge(m, "yes", shift, 1.0)
    toward = trading.robust_edge(m, "yes", shift, 1.0, toward=76.0 - 73.2)
    assert toward <= plain


def test_robust_gate_keeps_solid_mid_priced_bets():
    m = {"ticker": "B80.5", "strike_type": "between", "floor": 80, "cap": 81,
         "yes_ask": 0.20, "no_ask": 0.82}
    shift = lambda d: trading.gaussian_prob(80.5 + d, 1.0)
    assert trading.robust_edge(m, "yes", shift, 1.0) > 0


def test_market_implied_mean():
    ms = [{"strike_type": "between", "floor": 78, "cap": 79, "yes_ask": 0.10, "yes_bid": 0.08},
          {"strike_type": "between", "floor": 80, "cap": 81, "yes_ask": 0.60, "yes_bid": 0.56},
          {"strike_type": "between", "floor": 82, "cap": 83, "yes_ask": 0.30, "yes_bid": 0.26}]
    imp = trading.market_implied_mean(ms)
    assert 80.0 < imp < 81.5
