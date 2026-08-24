import numpy as np
import pandas as pd
from scipy import integrate
from scipy.stats import norm

from wx import emos, intraday, paper, settlement, trading


def test_crps_matches_numerical():
    mu, sigma, y = 72.0, 3.5, 74.0
    closed = emos.crps_gaussian(y, mu, sigma)

    def integrand(x):
        return (norm.cdf(x, mu, sigma) - (x >= y)) ** 2

    numeric, _ = integrate.quad(integrand, mu - 40, mu + 40)
    assert abs(closed - numeric) < 1e-4, (closed, numeric)


def test_emos_calibrates_underdispersed_ensemble():
    rng = np.random.default_rng(0)
    n = 4000
    truth = rng.normal(75, 6, n)
    ens_mean = truth + 2.0 + rng.normal(0, 1, n)   # biased +2F
    ens_std = np.full(n, 1.0)                       # underdispersed (real error ~ bigger)
    y = truth

    model = emos.fit(ens_mean, ens_std, y)
    raw = emos.crps_gaussian(y, ens_mean, ens_std).mean()
    mu, sigma = model.predict(ens_mean, ens_std)
    fitted = emos.crps_gaussian(y, mu, sigma).mean()
    assert fitted < raw * 0.8, (raw, fitted)          # >20% CRPS improvement

    # PIT should be ~uniform => central 90% interval covers ~90%
    lo, hi = mu - 1.645 * sigma, mu + 1.645 * sigma
    cover = np.mean((y >= lo) & (y <= hi))
    assert 0.86 < cover < 0.94, cover


def test_mixed_emos_learns_weights():
    rng = np.random.default_rng(3)
    n = 1500
    truth = rng.normal(70, 7, n)
    x1 = truth + rng.normal(0, 1.0, n)          # good predictor
    x2 = truth + rng.normal(0, 5.0, n) + 3.0    # noisy + biased predictor
    Xmean = np.column_stack([x1, x2])
    Xvar = (np.abs(x1 - x2) ** 2).reshape(-1, 1)

    m = emos.fit_mixed(Xmean, Xvar, truth, ridge=0.01)
    assert m.b[0] > m.b[1]  # leans on the better predictor

    # beats the equal-weight single-predictor EMOS
    single = emos.fit((x1 + x2) / 2, np.abs(x1 - x2), truth)
    mu_m, sig_m = m.predict(Xmean, Xvar)
    mu_s, sig_s = single.predict((x1 + x2) / 2, np.abs(x1 - x2))
    assert emos.crps_gaussian(truth, mu_m, sig_m).mean() < emos.crps_gaussian(truth, mu_s, sig_s).mean()

    # strong shrinkage collapses back to the equal-weight blend
    m2 = emos.fit_mixed(Xmean, Xvar, truth, ridge=1e6)
    assert abs(m2.b[0] - 0.5) < 0.02 and abs(m2.b[1] - 0.5) < 0.02


def test_bucket_probs_sum_to_one():
    model = emos.EMOS(0, 1, 4, 0)  # N(mean, 2^2)
    p = sum(model.prob_bucket(80, 0, t, t) for t in range(60, 101))
    assert abs(p - 1.0) < 1e-3, p


def test_crps_samples_matches_gaussian():
    rng = np.random.default_rng(1)
    s = rng.normal(72, 4, 60000)
    assert abs(emos.crps_samples(s, 75.0) - emos.crps_gaussian(75.0, 72.0, 4.0)) < 0.02


def test_intraday_floor_and_upside():
    # Residuals all ~0 => once the day is in, distribution sits on the floor:
    # the floor degree is the modal, high-confidence bucket and CRPS is tiny.
    m = intraday.IntradayModel(np.zeros(50))
    assert m.crps(90.0, 90.0) < 0.05
    assert m.prob_bucket(90.0, 90, 90) > 0.7
    assert m.prob_bucket(90.0, 90, 90) > m.prob_bucket(90.0, 91, 91)
    # Negative residuals are clipped: final can't be below observed max.
    m2 = intraday.IntradayModel(np.array([-3.0, -1.0, 0.0, 2.0]))
    assert m2.samples(80.0).min() >= 80.0


def test_fee_dome_and_bounds():
    assert trading.fee(0.50, 1) == 0.02      # peak ~1.75c -> 2c
    assert trading.fee(0.05, 1) == 0.01      # shrinks toward the extremes
    assert trading.fee(0.50, 100) == 1.75    # scales with contracts
    assert trading.market_bounds("between", 78, 79) == (78, 79)
    assert trading.market_bounds("less", None, 78) == (None, 77)
    assert trading.market_bounds("greater", 85, None) == (86, None)
    assert abs(trading.prob_range(80, 2, None, None) - 1.0) < 1e-9


def test_decide_takes_edge_and_skips_none():
    prob = trading.gaussian_prob(79.5, 1.0)  # P(79-80) ~ 0.68
    mkt = {"ticker": "T", "strike_type": "between", "floor": 79, "cap": 80,
           "yes_ask": 0.50, "no_ask": 0.50, "subtitle": "79 to 80"}
    d = trading.decide(mkt, prob, bankroll=1000, min_edge=0.03)
    assert d.side == "yes" and d.count >= 1 and d.edge_net > 0 and d.ev > 0

    fair = dict(mkt, yes_ask=0.69, no_ask=0.33)
    assert trading.decide(fair, prob, bankroll=1000, min_edge=0.03) is None

    for m in (mkt, fair):
        r = trading.decide(m, prob, 1000, min_edge=0.03)
        assert r is None or r.edge_net >= 0.03


def test_floored_predictive_kills_stale_tail():
    # High already at 80 today; a "77 or below" bucket must be ~impossible.
    floored = trading.floored_gaussian_prob(78.6, 1.5, floor=80)
    assert floored(None, 77) < 1e-6
    # mass sits at/above the floor and integrates to ~1 over the open top
    assert floored(80, None) > 0.99


def test_afd_sigma_factor():
    from wx import afd
    low = afd.AfdSignal(temp_confidence="low", lean="cooler", risks=["cold front"], rationale="x")
    hi = afd.AfdSignal(temp_confidence="high", lean="neutral", risks=[], rationale="x")
    assert afd.sigma_factor(low) == 1.4      # widen on low confidence
    assert afd.sigma_factor(hi) == 1.0       # no change on high confidence


def test_maker_price_joins_or_improves():
    assert trading.maker_price({"yes_bid": 0.37, "yes_ask": 0.38}, "yes") == 0.37  # 1c spread: join
    assert trading.maker_price({"yes_bid": 0.50, "yes_ask": 0.53}, "yes") == 0.51  # wide: improve
    assert trading.maker_price({"no_bid": None, "no_ask": 0.9}, "no") is None


def test_cap_exposure_and_dedup():
    d = lambda tk, ev, price, count: trading.Decision(tk, "yes", price, count, 0.6, 0.05, ev)
    decs = [d("A", 30, 0.50, 100), d("B", 20, 0.50, 100), d("C", 10, 0.50, 100)]
    kept = trading.cap_exposure(decs, budget_dollars=75)   # room for 1.5 positions
    assert sum(x.count * x.price for x in kept) <= 75 + 1e-9
    assert kept[0].ticker == "A"                            # highest EV kept first

    led = paper.Ledger(path="/tmp/kw_test_ledger.json")
    led.fills = []
    led.add(paper.Fill("A", "KNYC", "2025-07-15", "yes", 78, 79, 0.40, 50))
    assert led.held_markets("KNYC", "2025-07-15") == {("A", "yes")}
    assert led.staked_on("KNYC", "2025-07-15") == 20.0
    assert led.held_markets("KNYC", "2025-07-16") == set()


def test_settle_pnl():
    yes = paper.Fill("T", "KNYC", "2025-07-15", "yes", 78, 79, 0.40, 10, maker=True)
    assert paper.settle_pnl(yes, 78) == 6.0    # in bucket, win: 10*(1-0.4)
    assert paper.settle_pnl(yes, 81) == -4.0   # out: lose 10*0.4
    no = paper.Fill("T", "KNYC", "2025-07-15", "no", 78, 79, 0.60, 10, maker=True)
    assert paper.settle_pnl(no, 81) == 4.0     # out of bucket -> NO wins
    taker = paper.Fill("T", "KNYC", "2025-07-15", "yes", 78, 79, 0.50, 100, maker=False)
    assert paper.settle_pnl(taker, 78) == 100 * 0.5 - trading.fee(0.50, 100)


def test_plan_caps_total_exposure():
    mkts = [
        {"ticker": "A", "strike_type": "between", "floor": 79, "cap": 80,
         "yes_ask": 0.30, "no_ask": 0.70, "subtitle": "a"},
        {"ticker": "B", "strike_type": "between", "floor": 80, "cap": 81,
         "yes_ask": 0.30, "no_ask": 0.70, "subtitle": "b"},
    ]
    plan = trading.plan(mkts, trading.gaussian_prob(79.8, 1.0), bankroll=1000,
                        min_edge=0.03, max_total_frac=0.10)
    spent = sum(d.count * d.price for d in plan)
    assert spent <= 0.10 * 1000 + 1e-6


def test_lst_binning_crosses_local_midnight():
    # A spike at 04:00Z. For an EST station (offset -5) that is 23:00 LST the
    # previous day, so it must land on the earlier LST day, not the UTC day.
    obs = pd.DataFrame({
        "valid": pd.to_datetime(["2026-01-02 04:00", "2026-01-02 18:00"]),
        "tmpf": [95.0, 40.0],
    })
    highs = settlement.daily_high(obs, std_utc_offset=-5)
    by_day = dict(zip(highs["day"].astype(str), highs["high"]))
    assert by_day["2026-01-01"] == 95
    assert by_day["2026-01-02"] == 40
