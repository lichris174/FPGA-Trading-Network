"""Tests for golden/features.py."""

import random

import pytest

from golden.features import (
    FEATURE_NAMES, IMB_MAX, LAG, FeatureEngine, imbalance, ofi_event,
)


def add(ref, side, price, shares):
    return {"message_type": "A", "order_reference_number": ref,
            "buy_sell_indicator": side, "price": price, "shares": shares}


def open_market():
    return {"message_type": "S", "event_code": "Q"}


def warm_engine():
    """An engine in market hours with a two-sided book and a full lag history."""
    e = FeatureEngine()
    e.step(open_market())
    e.step(add(1, "B", 500000, 100))
    e.step(add(2, "S", 500100, 100))
    ref = 3
    for _ in range(LAG):
        e.step(add(ref, "B", 499000, 1)); ref += 1
    return e, ref


# --- imbalance ----------------------------------------------------------

def test_imbalance_fixed_points():
    assert imbalance(100, 100) == 0
    assert imbalance(100, 0) == IMB_MAX
    assert imbalance(0, 100) == -IMB_MAX
    assert abs(imbalance(300, 100) - 16384) <= 2


def test_imbalance_close_to_exact_division():
    rng = random.Random(1)
    for _ in range(5000):
        b = rng.randint(1, 2_000_000)
        a = rng.randint(1, 2_000_000)
        exact = 32768 * (b - a) / (b + a)
        # 11-bit normalisation: worst case about 0.1% of full scale.
        assert abs(imbalance(b, a) - exact) <= 40, (b, a)


def test_imbalance_is_antisymmetric():
    for b, a in [(7, 3), (1000, 1), (123456, 654321)]:
        assert imbalance(b, a) == -imbalance(a, b)


# --- order flow imbalance event (Cont, Kukanov and Stoikov 2014) ---------

@pytest.mark.parametrize("prev, new, expected", [
    # (bid_price, bid_size, ask_price, ask_size)
    ((100, 10, 200, 20), (100, 15, 200, 20), +5),    # bid size grows
    ((100, 10, 200, 20), (101, 7, 200, 20), +7),     # bid price improves
    ((100, 10, 200, 20), (99, 30, 200, 20), -10),    # best bid level gone
    ((100, 10, 200, 20), (100, 10, 200, 25), -5),    # ask size grows
    ((100, 10, 200, 20), (100, 10, 199, 4), -4),     # ask price improves
    ((100, 10, 200, 20), (100, 10, 201, 50), +20),   # best ask level gone
    ((100, 10, 200, 20), (101, 3, 199, 4), +3 - 4),  # both sides move
])
def test_ofi_event(prev, new, expected):
    assert ofi_event(prev, new) == expected


# --- when rows are written ---------------------------------------------

def test_no_rows_before_market_open():
    e = FeatureEngine()
    e.step(add(1, "B", 500000, 100))
    rows = [e.step(add(2 + i, "S", 500100, 1)) for i in range(LAG + 5)]
    assert rows == [None] * len(rows)


def test_rows_after_warm_up_and_none_after_close():
    e = FeatureEngine()
    e.step(open_market())
    e.step(add(1, "B", 500000, 100))
    rows = [e.step(add(2 + i, "S", 500100, 1)) for i in range(LAG + 3)]
    # The first LAG valid updates only fill the history.
    assert rows[:LAG] == [None] * LAG
    assert all(r is not None for r in rows[LAG:])
    e.step({"message_type": "S", "event_code": "M"})
    assert e.step(add(100, "S", 500100, 1)) is None


def test_no_rows_while_halted():
    e, ref = warm_engine()
    assert e.step(add(ref, "B", 499000, 1)) is not None
    e.step({"message_type": "H", "stock": "AAPL", "trading_state": "H"})
    assert e.step(add(ref + 1, "B", 499000, 1)) is None
    e.step({"message_type": "H", "stock": "AAPL", "trading_state": "T"})
    assert e.step(add(ref + 2, "B", 499000, 1)) is not None


def test_no_rows_for_one_sided_locked_or_crossed_book():
    e, ref = warm_engine()
    e.step({"message_type": "D", "order_reference_number": 2})       # no asks
    assert e.step(add(ref, "B", 499000, 1)) is None
    assert e.step(add(ref + 1, "S", 500000, 5)) is None              # locked
    e.step({"message_type": "D", "order_reference_number": ref + 1})
    assert e.step(add(ref + 2, "S", 500100, 5)) is not None


def test_non_book_messages_give_no_row():
    e, _ = warm_engine()
    assert e.step({"message_type": "R", "stock": "AAPL"}) is None


# --- feature values ------------------------------------------------------

def test_static_features():
    e, ref = warm_engine()
    r = e.step(add(ref, "S", 500100, 200))      # bid 50.0000 x 100, ask 50.0100 x 300
    f = dict(zip(FEATURE_NAMES, r.features))
    assert r.mid2 == 500000 + 500100
    assert f["spread"] == 100
    assert f["imbalance"] == imbalance(100, 300)
    assert f["micro_minus_mid2"] == (100 * imbalance(100, 300)) >> 15
    assert f["micro_minus_mid2"] < 0            # more sellers: fair price below mid


def test_lagged_features_use_history():
    e, ref = warm_engine()
    rows = []
    for i in range(LAG):
        rows.append(e.step(add(ref + i, "S", 500100, 50)))
    # Move the mid up and check the change against the row LAG updates back.
    r = e.step({"message_type": "A", "order_reference_number": ref + LAG,
                "buy_sell_indicator": "B", "price": 500050, "shares": 10})
    f = dict(zip(FEATURE_NAMES, r.features))
    old = dict(zip(FEATURE_NAMES, rows[0].features))
    assert f["mid2_change"] == r.mid2 - rows[0].mid2 == 50
    assert f["imbalance_lag"] == old["imbalance"]


def test_trade_flow_sign_and_hidden_trades():
    e, ref = warm_engine()
    # Seller hits the resting bid: seller-initiated, negative.
    r = e.step({"message_type": "E", "order_reference_number": 1, "executed_shares": 40})
    assert dict(zip(FEATURE_NAMES, r.features))["trade_flow"] == -40
    # Hidden (P) buy-side order executed, then a book update picks it up.
    e.step({"message_type": "P", "buy_sell_indicator": "B", "shares": 8})
    r = e.step(add(ref, "B", 499000, 1))
    assert dict(zip(FEATURE_NAMES, r.features))["trade_flow"] == -40 - (-40 >> 6) - 8


def test_ofi_decays_with_arithmetic_shift():
    e, ref = warm_engine()
    r = e.step(add(ref, "B", 500000, 64))        # bid size grows by 64
    f = dict(zip(FEATURE_NAMES, r.features))
    assert f["ofi_s3"] == f["ofi_s6"] == f["ofi_s9"] == 64
    r = e.step({"message_type": "X", "order_reference_number": ref, "cancelled_shares": 64})
    f = dict(zip(FEATURE_NAMES, r.features))
    assert f["ofi_s3"] == 64 - 64 - (64 >> 3)
    assert f["ofi_s9"] == 64 - 64 - (64 >> 9)


# --- no look-ahead --------------------------------------------------------

def random_stream(rng, n):
    """A random but valid message stream for one stock, starting at the open."""
    out = [open_market()]
    live = {}
    ref = 1
    for _ in range(n):
        if len(live) < 4 or rng.random() < 0.5:
            side = rng.choice("BS")
            # Bids at or below 50.00, asks at or above 50.01: never crossed.
            step = rng.randint(0, 5) * 100
            price = 500000 - step if side == "B" else 500100 + step
            shares = rng.randint(1, 500)
            out.append(add(ref, side, price, shares))
            live[ref] = shares
            ref += 1
        else:
            r = rng.choice(list(live))
            k = rng.choice("ECXD")
            if k == "D":
                out.append({"message_type": "D", "order_reference_number": r})
                del live[r]
                continue
            n_sh = rng.randint(1, live[r])
            key = "cancelled_shares" if k == "X" else "executed_shares"
            out.append({"message_type": k, "order_reference_number": r, key: n_sh})
            live[r] -= n_sh
            if live[r] == 0:
                del live[r]
    return out


def run(msgs):
    e = FeatureEngine()
    return [e.step(m) for m in msgs]


def test_rows_do_not_depend_on_future_messages():
    rng = random.Random(7)
    msgs = random_stream(rng, 3000)
    full = run(msgs)
    for cut in (50, 700, 2000):
        assert run(msgs[:cut]) == full[:cut]
    assert sum(r is not None for r in full) > 1000
