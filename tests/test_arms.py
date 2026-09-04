"""Paper arms + live loop share one selector; errors surface; fills are honest.

Uses the Aug 29 15:41 ET KAUS quote the live loop logged (μ=97.8 σ=1.26) and a
book of the shape the loop saw.
"""
import json
from datetime import date, datetime, timezone

from wx import paper, strategies, trading
from scripts import run_daily, run_live


class _Q:
    def __init__(self, mu, sigma, observed_max=94.0):
        self.mu, self.sigma, self.observed_max = mu, sigma, observed_max
        self.intraday_active = True
        self.prob_fn = trading.gaussian_prob(mu, sigma)
        self.shift_fn = lambda d: trading.gaussian_prob(mu + d, sigma)


MS = [{"ticker": "KXHIGHAUS-26AUG29-B99.5", "strike_type": "between", "floor": 99, "cap": 100,
       "yes_bid": 0.68, "yes_ask": 0.70, "no_bid": 0.30, "no_ask": 0.32},
      {"ticker": "KXHIGHAUS-26AUG29-B97.5", "strike_type": "between", "floor": 97, "cap": 98,
       "yes_bid": 0.20, "yes_ask": 0.24, "no_bid": 0.76, "no_ask": 0.80}]
Q = _Q(97.8, 1.26)


def test_control_arm_is_the_live_configuration():
    live = run_live.ARM
    for k in ("min_edge", "min_price", "ratio_cap", "model_weight", "robust_delta", "toward_market"):
        assert getattr(live, k) == getattr(strategies.CONTROL, k)


def test_every_candidate_carries_a_gate_verdict():
    pick, cands = strategies.select(MS, Q, 150, strategies.CONTROL)
    assert cands and all(isinstance(c.gated, bool) for c in cands)
    if pick:
        assert pick.ticker in {c.decision.ticker for c in cands if c.gated}


def test_no_gate_arm_never_kills_a_candidate():
    _, cands = strategies.select(MS, Q, 150, strategies.ARMS["no_gate"])
    assert all(c.gated for c in cands)


def test_arms_differ_from_control_by_one_parameter():
    for name, arm in strategies.ARMS.items():
        diff = [k for k in vars(arm) if k != "name" and getattr(arm, k) != getattr(strategies.CONTROL, k)]
        assert len(diff) == (0 if name == "control" else 1), (name, diff)


def test_quote_error_is_logged_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(run_live, "TICK_DIR", tmp_path / "ticks")
    monkeypatch.setattr(run_live, "LOG_TICKS", True)
    led = paper.Ledger(tmp_path / "live.json")
    quotes = {"KMDW": TimeoutError("api.open-meteo.com read timed out"), "KAUS": (Q, MS)}
    plan, spent, errors = run_live.build_plan(["KMDW", "KAUS"], date(2026, 8, 29),
                                              datetime(2026, 8, 29, 19, 41, tzinfo=timezone.utc),
                                              led, quotes=quotes, slot=19)
    assert errors == {"KMDW": "TimeoutError"}
    assert "KMDW" in run_live._error_lines(errors)[0]
    files = list((tmp_path / "ticks").glob("*.jsonl"))
    assert len(files) == 1 and files[0].name == "20260829T1941Z.jsonl"
    recs = [json.loads(l) for l in files[0].read_text().splitlines()]
    assert {r["icao"] for r in recs} == {"KMDW", "KAUS"}
    kaus = next(r for r in recs if r["icao"] == "KAUS")
    assert kaus["mu"] == 97.8 and all("gated" in c for c in kaus["cands"])


def test_paper_fill_is_capped_by_resting_depth():
    pick, _ = strategies.select(MS, Q, 150, strategies.CONTROL)
    assert pick and pick.side == "no" and pick.count > 5
    book = {"yes": [(0.68, 5.0), (0.67, 3.0)], "no": [(0.30, 40.0)]}   # only 5 at the touch, 8 within 1c
    d, count = run_daily.paper_fill(pick, MS[0], Q, strategies.CONTROL, book)
    assert d.price == 0.32 and count == 8


def test_failed_station_gets_one_serial_retry(monkeypatch):
    calls = []

    def flaky(icao, target, now_utc):
        calls.append(icao)
        if icao == "KLAX" and calls.count("KLAX") == 1:
            raise TimeoutError("mesonet 503")
        return (Q, MS)

    monkeypatch.setattr(run_live, "quote_station", flaky)
    out = run_live.quote_all(["KLAX", "KAUS"], date(2026, 9, 4), datetime(2026, 9, 4, 19, tzinfo=timezone.utc))
    assert not isinstance(out["KLAX"], Exception) and calls.count("KLAX") == 2


def test_ledger_union_keeps_both_sides_fills():
    from scripts.merge_ledger import union
    a = [{"ticker": "A", "count": 10}, {"ticker": "B", "count": 5}]
    b = [{"ticker": "A", "count": 10}, {"ticker": "C", "count": 7}]
    assert union(a, b) == [{"ticker": "A", "count": 10}, {"ticker": "B", "count": 5}, {"ticker": "C", "count": 7}]


def test_early_arm_sits_out_the_afternoon_slot(tmp_path, monkeypatch):
    ledgers = {n: paper.Ledger(tmp_path / f"{n}.json") for n in strategies.ARMS}
    monkeypatch.setattr(run_daily.kalshi, "orderbook",
                        lambda t, session=None: {"yes": [(0.68, 50.0)], "no": [(0.30, 50.0)]})
    out = run_daily.record(ledgers, "KAUS", date(2026, 8, 29), Q, MS, {}, None, slot=19)
    assert "early" not in out and "control" in out
    out = run_daily.record(ledgers, "KAUS", date(2026, 8, 30), Q, MS, {}, None, slot=15)
    assert "early" in out
