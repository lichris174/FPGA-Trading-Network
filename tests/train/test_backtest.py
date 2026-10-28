"""Tests for train/backtest.py: confidence, trade simulation and threshold choice."""

import numpy as np
import pytest

from train.backtest import bid_ask, choose_threshold, confidence, curve, simulate
from train.data import DOWN, FLAT, UP


def flat_prices(n, bid=1000, ask=1002):
    return np.full(n, bid, dtype=np.int64), np.full(n, ask, dtype=np.int64)


def test_bid_ask_recovers_both_sides():
    bid, ask = bid_ask(np.array([2001, 4000]), np.array([1, 100]))
    assert bid.tolist() == [1000, 1950]
    assert ask.tolist() == [1001, 2050]


def test_confidence_is_top_minus_second():
    scores = np.array([[5, 1, 3], [0, 9, 9], [-4, -2, -10]])
    pred, conf = confidence(scores)
    assert pred.tolist() == [0, 1, 1]          # ties go to the lower class
    assert conf.tolist() == [2, 0, 2]


def test_long_trade_buys_the_ask_and_sells_the_bid():
    bid, ask = flat_prices(5)
    bid[2], ask[2] = 1010, 1012
    pred = np.array([UP, FLAT, FLAT, FLAT, FLAT])
    r = simulate(pred, np.full(5, 9), bid, ask, threshold=0, k=2, fee=3)
    assert r["trades"] == 1
    assert r["gross"] == [10]                  # mid 1001 -> 1011
    assert r["net"] == [1010 - 1002 - 6]


def test_short_trade_sells_the_bid_and_buys_the_ask():
    bid, ask = flat_prices(5)
    bid[2], ask[2] = 990, 992
    pred = np.array([DOWN, FLAT, FLAT, FLAT, FLAT])
    r = simulate(pred, np.full(5, 9), bid, ask, threshold=0, k=2, fee=0)
    assert r["gross"] == [10]
    assert r["net"] == [1000 - 992]


def test_no_move_loses_the_spread_and_fees():
    bid, ask = flat_prices(5)
    r = simulate(np.full(5, UP), np.full(5, 9), bid, ask, threshold=0, k=2, fee=3)
    assert all(n == -2 - 6 for n in r["net"])


def test_one_position_at_a_time():
    bid, ask = flat_prices(10)
    r = simulate(np.full(10, UP), np.full(10, 9), bid, ask, threshold=0, k=3, fee=0)
    assert r["entries"] == [0, 3, 6]           # 9 has no exit row
    assert r["signals"] == 7                   # rows 0..6 could exit


def test_threshold_and_flat_mean_hold():
    bid, ask = flat_prices(6)
    pred = np.array([UP, FLAT, DOWN, UP, FLAT, FLAT])
    conf = np.array([1, 50, 5, 9, 50, 50])
    r = simulate(pred, conf, bid, ask, threshold=5, k=1, fee=0)
    assert r["entries"] == [2, 3]


def test_hit_rate_counts_moves_in_our_direction():
    mid2_bid = np.array([1000, 1000, 1010, 1010, 1000, 1000])
    bid, ask = mid2_bid, mid2_bid + 2
    pred = np.array([UP, UP, DOWN, UP, FLAT, FLAT])
    r = simulate(pred, np.full(6, 9), bid, ask, threshold=0, k=1, fee=0)
    # 0: flat, 1: up (right), 2: flat, 3: down (wrong), 4: no trade
    assert r["entries"] == [0, 1, 2, 3]
    assert r["right"] == 1 and r["wrong"] == 1 and r["flat"] == 2


def test_no_trades_gives_zeros_not_errors():
    bid, ask = flat_prices(4)
    r = simulate(np.full(4, FLAT), np.zeros(4), bid, ask, threshold=0, k=1, fee=0)
    assert r["trades"] == 0 and r["net_per_trade"] == 0.0


def test_curve_is_one_point_per_threshold():
    bid, ask = flat_prices(20)
    conf = np.arange(20)
    pts = curve(np.full(20, UP), conf, bid, ask, thresholds=[0, 10, 15], k=1, fee=0)
    assert [p["threshold"] for p in pts] == [0, 10, 15]
    assert [p["signals"] for p in pts] == [19, 9, 4]


def test_choose_threshold_needs_enough_trades():
    pts = [
        {"threshold": 0, "trades": 5000, "net_per_trade": -3.0},
        {"threshold": 5, "trades": 400, "net_per_trade": -1.0},
        {"threshold": 9, "trades": 20, "net_per_trade": 4.0},   # best, but too few trades
    ]
    assert choose_threshold(pts, min_trades=200)["threshold"] == 5


def test_choose_threshold_with_nothing_eligible():
    with pytest.raises(ValueError):
        choose_threshold([{"threshold": 0, "trades": 3, "net_per_trade": 1.0}], min_trades=200)
