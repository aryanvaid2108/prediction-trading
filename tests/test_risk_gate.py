"""Fix 08 proof: the kill-switch halts after a day that breached the loss cap.

The 'bad day' fixture IS Aug 26: the five real corrected fills (−$24.78). With
the $15 cap the gate must refuse to trade Aug 27 unless explicitly resumed.
"""
from datetime import date

from wx import paper
from scripts.run_live import risk_gate


def _ledger(tmp_path, rows):
    led = paper.Ledger(tmp_path / "led.json")
    for t, pnl in rows:
        led.add(paper.Fill("T", "KNYC", t, "yes", None, 79, 0.10, 10, pnl=pnl, realized=80))
    return led


def test_halts_after_the_real_aug26(tmp_path):
    aug26 = [("2026-08-26", p) for p in (-10.60, -4.48, -1.85, -4.99, -2.86)]
    led = _ledger(tmp_path, aug26)
    ok, why = risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)
    assert not ok and "24.78" in why


def test_trades_after_a_small_loss_or_win(tmp_path):
    led = _ledger(tmp_path, [("2026-08-26", -8.42)])
    ok, _ = risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)
    assert ok
    led = _ledger(tmp_path, [("2026-08-26", +12.0)])
    assert risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)[0]


def test_explicit_resume_overrides(tmp_path):
    led = _ledger(tmp_path, [("2026-08-26", -100.0)])
    ok, why = risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=True)
    assert ok and "resume" in why


def test_only_the_most_recent_settled_day_counts(tmp_path):
    # Aug 25 was a disaster but Aug 26 recovered: trading may continue
    led = _ledger(tmp_path, [("2026-08-25", -293.55), ("2026-08-26", +5.0)])
    assert risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)[0]


def test_open_positions_do_not_trip_the_gate(tmp_path):
    led = paper.Ledger(tmp_path / "led.json")
    led.add(paper.Fill("T", "KNYC", "2026-08-26", "yes", None, 79, 0.10, 10))  # unsettled
    assert risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)[0]


def test_breach_blocks_for_three_days_then_expires(tmp_path):
    led = _ledger(tmp_path, [("2026-08-26", -24.78)])
    assert not risk_gate(led, date(2026, 8, 27), loss_cap=15, resume=False)[0]
    assert not risk_gate(led, date(2026, 8, 29), loss_cap=15, resume=False)[0]
    ok, why = risk_gate(led, date(2026, 8, 30), loss_cap=15, resume=False)
    assert ok and "expired" in why
