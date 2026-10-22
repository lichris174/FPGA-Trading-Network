"""Tests for golden/book.py."""

import random

import pytest

from golden.book import BookError, OrderBook


def add(ref, side, price, shares, t="A"):
    return {"message_type": t, "order_reference_number": ref,
            "buy_sell_indicator": side, "price": price, "shares": shares}


def test_best_bid_ask_and_next_level():
    b = OrderBook()
    b.apply(add(1, "B", 500000, 100))
    b.apply(add(2, "B", 499900, 300))
    b.apply(add(3, "S", 500200, 200))
    b.apply(add(4, "B", 500000, 50, t="F"))
    t = b.top()
    assert (t.bid_price, t.bid_size, t.ask_price, t.ask_size) == (500000, 150, 500200, 200)

    # Removing the whole best bid level exposes the next one.
    b.apply({"message_type": "D", "order_reference_number": 1})
    b.apply({"message_type": "E", "order_reference_number": 4, "executed_shares": 50, "match_number": 1})
    assert b.best_bid() == (499900, 300)
    assert 4 not in b.orders


def test_execute_with_price_uses_order_price():
    b = OrderBook()
    b.apply(add(1, "S", 500200, 100))
    b.apply({"message_type": "C", "order_reference_number": 1, "executed_shares": 40,
             "execution_price": 500100})
    assert b.best_ask() == (500200, 60)


def test_partial_cancel_and_replace():
    b = OrderBook()
    b.apply(add(1, "B", 500000, 100))
    b.apply({"message_type": "X", "order_reference_number": 1, "cancelled_shares": 30})
    assert b.best_bid() == (500000, 70)
    b.apply({"message_type": "U", "original_order_reference_number": 1,
             "new_order_reference_number": 9, "shares": 200, "price": 500100})
    assert b.best_bid() == (500100, 200)
    assert 1 not in b.orders and b.orders[9].side == "B"
    assert 500000 not in b.levels["B"]


def test_non_book_messages_ignored():
    b = OrderBook()
    assert b.apply({"message_type": "P"}) is False
    assert b.top().bid_price == 0


@pytest.mark.parametrize("bad", [
    {"message_type": "D", "order_reference_number": 99},
    {"message_type": "X", "order_reference_number": 1, "cancelled_shares": 101},
    add(1, "B", 500000, 10),
    add(2, "Q", 500000, 10),
])
def test_strict_errors(bad):
    b = OrderBook()
    b.apply(add(1, "B", 500000, 100))
    with pytest.raises(BookError):
        b.apply(bad)


def naive_top(orders):
    """Recompute the top of book from scratch from a {ref: (side, price, shares)} dict."""
    bids = [(p, s) for side, p, s in orders.values() if side == "B"]
    asks = [(p, s) for side, p, s in orders.values() if side == "S"]
    bp = max((p for p, _ in bids), default=0)
    ap = min((p for p, _ in asks), default=0)
    return (bp, sum(s for p, s in bids if p == bp), ap, sum(s for p, s in asks if p == ap))


def test_random_against_naive_reference():
    rng = random.Random(42)
    b = OrderBook()
    ref_orders = {}
    next_ref = 1
    for _ in range(20000):
        live = list(ref_orders)
        op = rng.choice("AAFECXDU") if live else "A"
        if op in "AF":
            side = rng.choice("BS")
            price = 500000 + rng.randint(-20, 20) * 100
            shares = rng.randint(1, 500)
            b.apply(add(next_ref, side, price, shares, t=op))
            ref_orders[next_ref] = (side, price, shares)
            next_ref += 1
        else:
            ref = rng.choice(live)
            side, price, shares = ref_orders[ref]
            if op in "ECX":
                n = rng.randint(1, shares)
                key = "cancelled_shares" if op == "X" else "executed_shares"
                b.apply({"message_type": op, "order_reference_number": ref, key: n})
                if n == shares:
                    del ref_orders[ref]
                else:
                    ref_orders[ref] = (side, price, shares - n)
            elif op == "D":
                b.apply({"message_type": "D", "order_reference_number": ref})
                del ref_orders[ref]
            else:
                new_price = 500000 + rng.randint(-20, 20) * 100
                new_shares = rng.randint(1, 500)
                b.apply({"message_type": "U", "original_order_reference_number": ref,
                         "new_order_reference_number": next_ref,
                         "shares": new_shares, "price": new_price})
                del ref_orders[ref]
                ref_orders[next_ref] = (side, new_price, new_shares)
                next_ref += 1
        t = b.top()
        assert (t.bid_price, t.bid_size, t.ask_price, t.ask_size) == naive_top(ref_orders)
    assert len(b.orders) == len(ref_orders)
