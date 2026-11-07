"""cocotb tests for rtl/book/book_levels.v against golden/hw_book.py.

The model's level updates are recorded together with its top 2 after
each one, then replayed into the Verilog, which must show the same top 2
(price and size, both sides) after every update. Sources: the real AAPL
slice in data/vectors, and random streams built to empty top levels with
the next level far away, across bitmap words and at the window edges.
"""

import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from golden.hw_book import SLOTS, TICK, HwBook
from golden.itch.parser import decode, open_itch, read_frames

VECTOR = Path(__file__).resolve().parents[2] / "data" / "vectors" / "aapl_15k.itch"
CENTER = 2_000_000
MAX_CLOCKS = 12              # an update has to finish well inside the 20-clock bucket update


class Recorder(HwBook):
    """HwBook that logs every level update with the top 2 that follows it."""

    def __init__(self, center):
        super().__init__(center)
        self.log = []

    def _level(self, side, slot, delta):
        super()._level(side, slot, delta)
        self.log.append((side, slot, delta, self.top2("B") + self.top2("S"), "level_overflow" in self.errors))


def message_log(msgs, center):
    book = Recorder(center)
    for m in msgs:
        book.apply(m)
    return book


def add(ref, side, price, shares):
    return {"message_type": "A", "order_reference_number": ref, "buy_sell_indicator": side,
            "price": price, "shares": shares}


def delete(ref):
    return {"message_type": "D", "order_reference_number": ref}


async def setup(dut, base):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.upd_valid.value = 0
    dut.cfg_base.value = base
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    for _ in range(9000):
        await FallingEdge(dut.clk)
        if int(dut.upd_ready.value):
            return
    raise AssertionError("RAM clear never finished")


def outputs(dut):
    return [(int(dut.bid1_price.value), int(dut.bid1_size.value)), (int(dut.bid2_price.value), int(dut.bid2_size.value)),
            (int(dut.ask1_price.value), int(dut.ask1_size.value)), (int(dut.ask2_price.value), int(dut.ask2_size.value))]


async def replay(dut, log):
    """Drive each recorded update, wait for it to finish, compare. Returns the slowest update in clocks."""
    slowest = 0
    for i, (side, slot, delta, want, overflow) in enumerate(log):
        dut.upd_side.value = side == "S"
        dut.upd_slot.value = slot
        dut.upd_add.value = delta > 0
        dut.upd_shares.value = abs(delta)
        dut.upd_valid.value = 1
        await FallingEdge(dut.clk)              # upd_ready was high, so the update was taken
        dut.upd_valid.value = 0
        clocks = 1
        while not int(dut.upd_ready.value):
            await FallingEdge(dut.clk)
            clocks += 1
        slowest = max(slowest, clocks)
        got = outputs(dut)
        assert got == want, f"update {i} ({side} slot {slot} {delta:+d}): got {got}, want {want}"
        assert int(dut.level_overflow.value) == overflow, f"update {i}: level_overflow"
    return slowest


@cocotb.test()
async def test_real_data(dut):
    with open_itch(VECTOR) as f:
        msgs = [decode(raw) for raw in read_frames(f)]
    prices = sorted(m["price"] for m in msgs if m["message_type"] in "AF")
    book = message_log(msgs, prices[len(prices) // 2])
    await setup(dut, book.base)
    slowest = await replay(dut, book.log)
    dut._log.info(f"{len(book.log)} updates, {book.scans} scans, slowest {slowest} clocks")
    assert slowest <= MAX_CLOCKS


@cocotb.test()
async def test_sparse_levels_force_long_scans(dut):
    """Few levels far apart, so emptying a top level has to jump bitmap words, up to the window edges."""
    rng = random.Random(7)
    base = CENTER - SLOTS // 2 * TICK
    msgs, live, ref = [], [], 1                  # live: (ref, side, slot)
    for _ in range(3000):
        if live and rng.random() < 0.45:
            # Mostly delete an order at the best level of a side, so the top keeps emptying.
            side = rng.choice("BS")
            mine = [o for o in live if o[1] == side] or live
            o = (max if side == "B" else min)(mine, key=lambda o: o[2]) if rng.random() < 0.8 else rng.choice(live)
            live.remove(o)
            msgs.append(delete(o[0]))
            continue
        side = rng.choice("BS")
        slot = rng.choice([0, 1, 63, 64, 65, 2047, 2048, 4031, 4032, 4094, 4095, rng.randrange(SLOTS)])
        msgs.append(add(ref, side, base + slot * TICK, rng.randint(1, 3000)))
        live.append((ref, side, slot))
        ref += 1
    book = message_log(msgs, CENTER)
    await setup(dut, book.base)
    slowest = await replay(dut, book.log)
    dut._log.info(f"{len(book.log)} updates, {book.scans} scans, slowest {slowest} clocks")
    assert book.scans > 500
    assert slowest <= MAX_CLOCKS


@cocotb.test()
async def test_busy_levels_near_the_top(dut):
    """Many orders on a few levels: sizes change without the level emptying, then levels drain."""
    rng = random.Random(8)
    msgs, live, ref = [], [], 1
    for _ in range(4000):
        if live and rng.random() < 0.48:
            msgs.append(delete(live.pop(rng.randrange(len(live)))))
            continue
        side = rng.choice("BS")
        price = CENTER + (-rng.randint(1, 6) if side == "B" else rng.randint(1, 6)) * TICK
        msgs.append(add(ref, side, price, rng.randint(1, 131071)))
        live.append(ref)
        ref += 1
    msgs += [delete(r) for r in live]
    book = message_log(msgs, CENTER)
    await setup(dut, book.base)
    await replay(dut, book.log)
    assert outputs(dut) == [(0, 0)] * 4


@cocotb.test()
async def test_level_overflow_is_sticky(dut):
    msgs = [add(1 + k, "B", CENTER, 100_000) for k in range(11)] + [delete(11)]
    book = message_log(msgs, CENTER)
    assert book.errors == {"level_overflow"}
    await setup(dut, book.base)
    await replay(dut, book.log)
    assert int(dut.level_overflow.value) == 1
