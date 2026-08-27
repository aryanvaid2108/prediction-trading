"""Fix 01 proof: _parse_fill against the REAL V2 responses from the Aug 25-26 runs.

Every payload below is copied verbatim from the GitHub Actions live-loop logs
(run ids in comments). The old _filled_count read `fill_count_fp` (absent),
returned None, and the caller assumed the full order filled — the ledger
recorded 448 KMDW contracts when 140 filled.
"""
from scripts.run_live import _parse_fill

# run 32986472118 — KMDW YES 81-82, planned 448 @ 0.02: only 140 filled
KMDW = {'average_fee_paid': '0.0020', 'average_fill_price': '0.0300',
        'fill_count': '140.00', 'remaining_count': '308.00', 'ts_ms': 1787760737736}
# run 32986472118 — KSFO YES <=71, planned 100 @ 0.09: only 18 filled
KSFO = {'average_fee_paid': '0.0061', 'average_fill_price': '0.0966',
        'fill_count': '18.00', 'remaining_count': '82.00', 'ts_ms': 1787760737799}
# run 33004128510 — KPHL NO 85-86, planned 18 @ 0.15: avg price reported in YES terms
KPHL = {'average_fee_paid': '0.0089', 'average_fill_price': '0.8500',
        'fill_count': '18.00', 'remaining_count': '0.00', 'ts_ms': 1787771969051}
# run 32879236519 — KNYC NO 79-80, planned 84 @ 0.89: 46 filled, YES-terms 0.1000
KNYC_NO = {'average_fee_paid': '0.0063', 'average_fill_price': '0.1000',
           'fill_count': '46.00', 'remaining_count': '38.00', 'ts_ms': 1787680177701}


def test_partial_fill_not_assumed_full():
    filled, cost, fee = _parse_fill(KMDW, "yes", 0.02)
    assert filled == 140            # old code recorded 448
    assert cost == 0.03             # old code recorded the planned 0.02
    assert abs(fee - 0.28) < 1e-6

    filled, cost, fee = _parse_fill(KSFO, "yes", 0.09)
    assert filled == 18             # old code recorded 100
    assert cost == 0.0966


def test_no_side_price_is_complement_of_yes_terms():
    filled, cost, fee = _parse_fill(KPHL, "no", 0.15)
    assert filled == 18
    assert cost == 0.15             # 1 - 0.8500
    filled, cost, _ = _parse_fill(KNYC_NO, "no", 0.89)
    assert filled == 46             # old code recorded 84
    assert cost == 0.90             # 1 - 0.1000


def test_unreadable_fill_records_zero_not_full():
    filled, cost, fee = _parse_fill({"order": {"status": "??"}}, "yes", 0.20)
    assert filled == 0              # old code returned None -> caller assumed full
    assert cost == 0.20 and fee == 0.0
    filled, _, _ = _parse_fill(None, "yes", 0.20)
    assert filled == 0


def test_wrapped_order_key_and_fp_fallback():
    filled, cost, _ = _parse_fill({"order": {"fill_count_fp": "7", "average_fill_price": "0.4"}},
                                  "yes", 0.4)
    assert filled == 7 and cost == 0.4
