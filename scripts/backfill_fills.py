"""One-off ledger repair (Fix 02): replace assumed-full IOC fills with the actual
fills from the V2 order responses captured in the GitHub Actions run logs.

Every correction below is copied from a `raw response` line in the named run.
Aug 25's nine MAKER orders printed no fill info (old code path) and are left
untouched — they need an authenticated `run_live reconcile` to verify.

Usage: python -m scripts.backfill_fills          # applies + re-settles + prints before/after
"""
from wx import paper
from scripts.run_live import LIVE_LEDGER

# ticker -> (actual_fill, actual_cost_per_contract, total_fees)   [source run id]
CORRECTIONS = {
    # run 32879236519 (Aug 25 13:42 ET)
    "KXHIGHNY-26AUG25-B79.5":   (46, 0.90,   round(46 * 0.0063, 4)),   # ledger said 84 @ 0.89
    "KXHIGHNY-26AUG25-T79":     (42, 0.8952, round(42 * 0.0065, 4)),   # ledger said 84 @ 0.89
    "KXHIGHTSFO-26AUG25-B72.5": (80, 0.20,   round(80 * 0.0112, 4)),   # full fill, fee corrected
    # run 32888552595 (Aug 25 15:24 ET)
    "KXHIGHTSFO-26AUG25-B68.5": (71, 0.2697, round(71 * 0.0137, 4)),   # ledger said 84 @ 0.26
    # run 32986472118 (Aug 26 11:58 ET)
    "KXHIGHNY-26AUG26-T80":     (93, 0.1073, round(93 * 0.0067, 4)),
    "KXHIGHCHI-26AUG26-B81.5":  (140, 0.03,  round(140 * 0.0020, 4)),  # ledger said 448 @ 0.02
    "KXHIGHTSFO-26AUG26-T72":   (18, 0.0966, round(18 * 0.0061, 4)),   # ledger said 100 @ 0.09
    # run 32987948682 (Aug 26 12:22 ET)
    "KXHIGHLAX-26AUG26-B82.5":  (318, 0.0147, round(318 * 0.0010, 4)),
    # run 33004128510 (Aug 26 15:15 ET) — NO fill, avg_fill_price 0.8500 is YES terms
    "KXHIGHPHIL-26AUG26-B85.5": (18, 0.15,  round(18 * 0.0089, 4)),
}


def main():
    led = paper.Ledger(LIVE_LEDGER)
    print("=== before ===")
    _report(led)
    for f in led.fills:
        c = CORRECTIONS.get(f.ticker)
        if not c:
            continue
        count, price, fee = c
        changed = (f.count, round(f.price, 4)) != (count, price)
        f.count, f.price, f.fee = count, price, fee
        if f.pnl is not None:               # re-settle already-closed rows in place
            f.pnl = paper.settle_pnl(f, f.realized)
        print(f"  {'FIXED ' if changed else 'fee   '} {f.ticker:28} -> {count} @ {price} fee ${fee}")
    led.save()
    print("settle:", led.settle_due())
    print("=== after ===")
    _report(led)


def _report(led):
    by = {}
    for f in led.fills:
        if f.pnl is not None:
            by[f.target] = round(by.get(f.target, 0.0) + f.pnl, 2)
    for day, pnl in sorted(by.items()):
        print(f"  {day}: ${pnl:+.2f}")


if __name__ == "__main__":
    main()
