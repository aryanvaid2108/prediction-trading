"""Stale-quote fix proof: re-price every order against the live book.

Fixture 1 is a real V2 orderbook (KXHIGHNY-26SEP04-B84.5, fetched Sep 4): the
summary said yes 0.83/0.86, no 0.14/0.17, and the book must reproduce that touch
plus the depth resting there. Fixture 2 is the Sep 3 KMDW trade: the plan saw a
16.5c mid (ask 0.19) but the book's real ask was 10.5c — below the 15c floor and
6x the model's 0.67 — and it must now be refused instead of sent.
"""
import json
from pathlib import Path

from wx import kalshi, strategies, trading

BOOK = kalshi.parse_orderbook({"orderbook_fp": {
    "no_dollars": [["0.0100", "1850.12"], ["0.1000", "340.53"], ["0.1200", "55.00"],
                   ["0.1300", "25.00"], ["0.1400", "17.00"]],
    "yes_dollars": [["0.0100", "619.00"], ["0.8000", "500.00"], ["0.8100", "67.00"],
                    ["0.8200", "73.00"], ["0.8300", "26.00"]]}})


def test_touch_matches_the_market_summary():
    assert trading.book_touch(BOOK, "yes") == (0.86, 17)      # 1 - best NO bid 0.14
    assert trading.book_touch(BOOK, "no") == (0.17, 26)       # 1 - best YES bid 0.83


def test_depth_within_the_cross_adds_the_next_level():
    ask, depth = trading.book_touch(BOOK, "yes", cross=0.01)   # takes 0.86 and 0.87
    assert ask == 0.86 and depth == 17 + 25
    assert trading.book_touch({"yes": [], "no": []}, "yes") == (None, 0)


def test_refresh_market_rewrites_all_four_quotes():
    m = trading.refresh_market({"ticker": "T", "yes_ask": 0.5, "no_ask": 0.5}, BOOK)
    assert (m["yes_ask"], m["yes_bid"], m["no_ask"], m["no_bid"]) == (0.86, 0.83, 0.17, 0.14)


class _Q:
    def __init__(self, mu, sigma):
        self.mu, self.sigma = mu, sigma
        self.prob_fn = trading.gaussian_prob(mu, sigma)
        self.shift_fn = lambda d: trading.gaussian_prob(mu + d, sigma)


def test_sep3_kmdw_is_refused_on_the_real_book():
    # KMDW 2026-09-03 13:56 ET: μ=92.4 σ=1.03, YES 92-93 summary 0.14/0.19 (mid 0.165)
    summary = {"ticker": "KXHIGHCHI-26SEP03-B92.5", "strike_type": "between", "floor": 92, "cap": 93,
               "yes_bid": 0.14, "yes_ask": 0.19, "no_bid": 0.81, "no_ask": 0.86}
    q = _Q(92.4, 1.03)
    pick, _ = strategies.select([summary], q, 150, strategies.CONTROL)
    assert pick and pick.side == "yes" and pick.price == 0.19       # the plan the engine built
    book = {"yes": [(0.10, 100.0)], "no": [(0.895, 65.0)]}          # what filled: ask 10.5c
    pick2, cands = strategies.select([trading.refresh_market(summary, book)], q, 150, strategies.CONTROL)
    assert pick2 is None and cands == []                            # below floor + over ratio cap


def test_unchanged_book_keeps_the_order():
    summary = {"ticker": "T", "strike_type": "between", "floor": 92, "cap": 93,
               "yes_bid": 0.83, "yes_ask": 0.86, "no_bid": 0.14, "no_ask": 0.17}
    q = _Q(93.5, 1.0)                                               # NO 92-93 is the edge
    pick, _ = strategies.select([summary], q, 150, strategies.CONTROL)
    pick2, _ = strategies.select([trading.refresh_market(summary, BOOK)], q, 150, strategies.CONTROL)
    assert pick and pick2 and pick.side == pick2.side == "no" and pick2.price == 0.17
