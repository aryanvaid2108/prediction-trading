"""Fix 06 proof: market-prior shrinkage changes sizing/acceptance, and every
decision carries the raw (p_model, p_market) pair for the calibration ledger."""
from wx import trading


M = {"ticker": "B80.5", "strike_type": "between", "floor": 80, "cap": 81,
     "yes_ask": 0.20, "yes_bid": 0.16, "no_ask": 0.82}
PROB = trading.gaussian_prob(80.5, 1.2)   # p_yes ≈ 0.595


def test_full_weight_reproduces_unshrunk_behaviour():
    d = trading.decide(M, PROB, 150.0, min_edge=0.05, model_weight=1.0)
    assert d is not None and abs(d.model_prob - 0.595) < 0.01


def test_half_weight_blends_toward_market_mid():
    full = trading.decide(M, PROB, 150.0, min_edge=0.05, model_weight=1.0)
    half = trading.decide(M, PROB, 150.0, min_edge=0.05, model_weight=0.5)
    # p_used drops from ~0.595 to ~(0.595+0.18)/2 ≈ 0.387 -> smaller edge, smaller size
    assert half is not None
    assert half.edge_net < full.edge_net
    assert half.count < full.count
    # ledger fields: raw model prob is preserved, market mid recorded
    assert abs(half.model_prob - 0.595) < 0.01
    assert abs(half.market_prob - 0.18) < 1e-9


def test_shrinkage_plus_floor_kills_a_market_vs_model_standoff():
    # KNYC Aug 26 shape: model 52%, market mid 12% on a 13c ask. At w=0.5 the
    # blended 32% vs 13c still shows edge — the 15c floor must be the backstop.
    m = {"ticker": "T80", "strike_type": "less", "cap": 80,
         "yes_ask": 0.13, "yes_bid": 0.11, "no_ask": 0.89}
    d = trading.decide(m, trading.gaussian_prob(79.4, 1.99), 150.0,
                       min_edge=0.05, model_weight=0.5, min_price=0.15, ratio_cap=2.5)
    assert d is None or d.side != "yes"
