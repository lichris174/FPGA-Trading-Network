"""Tests for golden/hw_book.py: the hardware algorithm against golden/book.py."""

import random
from pathlib import Path

from golden.book import OrderBook
from golden.hw_book import (BUCKETS, STASH_SIZE, WAYS, HwBook, next_worse, pack_entry, price_slot, unpack_entry,
                            window_base)
from golden.itch.parser import decode, open_itch, read_frames

VECTOR = Path(__file__).resolve().parents[2] / "data" / "vectors" / "aapl_15k.itch"
CENTER = 2_000_000              # $200.00
BASE = window_base(CENTER)


def add(ref, side, price, shares, t="A"):
    return {"message_type": t, "order_reference_number": ref,
            "buy_sell_indicator": side, "price": price, "shares": shares}


def delete(ref):
    return {"message_type": "D", "order_reference_number": ref}


def reduce(ref, n, t="X"):
    field = "cancelled_shares" if t == "X" else "executed_shares"
    return {"message_type": t, "order_reference_number": ref, field: n}


def replace(old, new, price, shares):
    return {"message_type": "U", "original_order_reference_number": old,
            "new_order_reference_number": new, "price": price, "shares": shares}


def golden_top2(book, side):
    prices = sorted(book.levels[side], reverse=side == "B")[:2]
    out = [(p, book.levels[side][p]) for p in prices]
    return out + [(0, 0)] * (2 - len(out))


def test_window_base_is_centered_and_cent_aligned():
    assert window_base(2_000_049) == 2_000_000 - 2048 * 100
    assert BASE % 100 == 0


def test_price_slot_matches_division_for_every_slot():
    for k in range(4096):
        assert price_slot(BASE + k * 100, BASE) == k


def test_price_slot_rejects_outside_and_subpenny():
    assert price_slot(BASE - 100, BASE) is None
    assert price_slot(BASE + 4096 * 100, BASE) is None
    assert price_slot(BASE + 150, BASE) is None
    assert price_slot(BASE + 1, BASE) is None


def test_entry_round_trip_uses_64_bits():
    ref = (1 << 51) - 1
    e = pack_entry(ref, "S", True, 4095, (1 << 17) - 1)
    assert e < 1 << 64
    assert unpack_entry(e) == {"tag": ref >> 18, "side": "S", "inside": True, "slot": 4095, "shares": (1 << 17) - 1}


def test_top2_tracks_adds_shifts_and_removals():
    b = HwBook(CENTER)
    b.apply(add(1, "B", 2_000_000, 100))
    b.apply(add(2, "B", 1_999_900, 200))
    b.apply(add(3, "B", 1_999_800, 300))
    assert b.top2("B") == [(2_000_000, 100), (1_999_900, 200)]

    b.apply(add(4, "B", 2_000_100, 50))              # new best shifts the old best down
    assert b.top2("B") == [(2_000_100, 50), (2_000_000, 100)]
    b.apply(add(5, "B", 2_000_050 - 50, 7))          # joins the second level
    assert b.top2("B") == [(2_000_100, 50), (2_000_000, 107)]

    b.apply(delete(4))                               # best empties: second moves up, scan finds the third
    assert b.top2("B") == [(2_000_000, 107), (1_999_900, 200)]
    b.apply(delete(2))                               # second empties: scan again
    assert b.top2("B") == [(2_000_000, 107), (1_999_800, 300)]


def test_ask_side_is_best_low():
    b = HwBook(CENTER)
    b.apply(add(1, "S", 2_000_200, 10))
    b.apply(add(2, "S", 2_000_100, 20))
    assert b.top2("S") == [(2_000_100, 20), (2_000_200, 10)]
    b.apply(delete(2))
    assert b.top2("S") == [(2_000_200, 10), (0, 0)]


def test_next_worse_crosses_bitmap_words():
    b = HwBook(CENTER)
    b.apply(add(1, "B", BASE + 10 * 100, 1))
    b.apply(add(2, "B", BASE + 2000 * 100, 1))
    b.apply(add(3, "S", BASE + 3000 * 100, 1))
    b.apply(add(4, "S", BASE + 3 * 100, 1))
    assert next_worse(b.words["B"], b.summary["B"], "B", 2000) == 10
    assert next_worse(b.words["B"], b.summary["B"], "B", 10) is None
    assert next_worse(b.words["S"], b.summary["S"], "S", 3) == 3000
    assert next_worse(b.words["S"], b.summary["S"], "S", 3000) is None


def test_orders_outside_the_window_keep_their_side_through_replace():
    b = HwBook(CENTER)
    far = BASE - 100_000
    b.apply(add(1, "B", far, 100))
    assert b.top2("B") == [(0, 0), (0, 0)]
    b.apply(replace(1, 2, CENTER, 40))               # moves into the window, side comes from the table
    assert b.top2("B") == [(CENTER, 40), (0, 0)]
    b.apply(replace(2, 3, far, 40))                  # and back out
    assert b.top2("B") == [(0, 0), (0, 0)]
    assert not b.errors


def test_bucket_overflow_goes_to_stash_and_comes_back():
    b = HwBook(CENTER)
    refs = [7 + k * BUCKETS for k in range(WAYS + 3)]    # all in one bucket
    for r in refs:
        b.apply(add(r, "S", CENTER, 10))
    assert b.stash_used == 3 and b.stash_peak == 3
    assert b.top2("S") == [(CENTER, 10 * len(refs)), (0, 0)]
    for r in refs:
        b.apply(delete(r))
    assert b.stash_used == 0
    assert b.top2("S") == [(0, 0), (0, 0)]
    assert not b.errors


def test_stash_full_sets_sticky_flag():
    b = HwBook(CENTER)
    refs = [7 + k * BUCKETS for k in range(WAYS + STASH_SIZE + 1)]
    for r in refs:
        b.apply(add(r, "S", CENTER, 10))
    assert b.errors == {"stash_full"}
    b.apply(delete(refs[-1]))                        # the dropped order is now unknown
    assert b.errors == {"stash_full", "unknown_ref"}


def test_level_size_overflow_sets_sticky_flag():
    b = HwBook(CENTER)
    for k in range(10):
        b.apply(add(1 + k, "B", CENTER, 100_000))    # 1.0 M shares fit in 20 bits
    assert not b.errors
    b.apply(add(11, "B", CENTER, 100_000))           # 1.1 M do not
    assert b.errors == {"level_overflow"}
    assert b.size_peak == 1_100_000


def test_replace_of_unknown_order_flags_and_adds_nothing():
    b = HwBook(CENTER)
    b.apply(replace(1, 2, CENTER, 40))
    assert b.errors == {"unknown_ref"}
    assert b.top2("B") == [(0, 0), (0, 0)] and b.top2("S") == [(0, 0), (0, 0)]


def test_random_stream_matches_golden_book():
    rng = random.Random(5)
    hw, gold = HwBook(CENTER), OrderBook()
    live, next_ref = [], 1
    for _ in range(20000):
        r = rng.random()
        if r < 0.45 or not live:
            side = rng.choice("BS")
            price = CENTER + (-rng.randint(1, 40) if side == "B" else rng.randint(1, 40)) * 100
            m = add(next_ref, side, price, rng.randint(1, 500), rng.choice("AF"))
            live.append(next_ref)
            next_ref += rng.randint(1, 300)
        elif r < 0.75:
            m = delete(live.pop(rng.randrange(len(live))))
        elif r < 0.9:
            ref = rng.choice(live)
            n = rng.randint(1, gold.orders[ref].shares)
            m = reduce(ref, n, rng.choice("EX"))
            if n == gold.orders[ref].shares:
                live.remove(ref)
        else:
            old = live.pop(rng.randrange(len(live)))
            side = gold.orders[old].side
            price = CENTER + (-rng.randint(1, 40) if side == "B" else rng.randint(1, 40)) * 100
            m = replace(old, next_ref, price, rng.randint(1, 500))
            live.append(next_ref)
            next_ref += rng.randint(1, 300)
        hw.apply(m)
        gold.apply(m)
        for side in "BS":
            assert hw.top2(side) == golden_top2(gold, side)
    assert not hw.errors


def test_real_data_matches_golden_book():
    gold, hw, n = OrderBook(), None, 0
    with open_itch(VECTOR) as f:
        msgs = [decode(raw) for raw in read_frames(f)]
    book_msgs = [m for m in msgs if m["message_type"] in "AFECXDU"]
    prices = sorted(m["price"] for m in book_msgs if m["message_type"] in "AF")
    hw = HwBook(prices[len(prices) // 2])
    for m in book_msgs:
        gold.apply(m)
        hw.apply(m)
        for side in "BS":
            want = golden_top2(gold, side)
            if all(p == 0 or price_slot(p, hw.base) is not None for p, _ in want):
                assert hw.top2(side) == want
                n += 1
    assert n > 10000
    assert not hw.errors
