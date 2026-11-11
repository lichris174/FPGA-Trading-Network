"""cocotb tests for rtl/book/order_book.v against golden/hw_book.py.

The SDRAM is tb/sdram/sdram_model.py, which also checks every SDRAM
timing rule. Messages go in one at a time; after each one the top 2 of
both sides and the five sticky error flags must equal the model's. The
Makefile builds 2^14 buckets so clearing the table at reset is quick; the
model is built with the same size, so orders spill to the stash alike.
"""

import os
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from golden.hw_book import HwBook, window_base
from golden.itch.parser import decode, open_itch, read_frames
from sdram_model import SdramModel, Timing

VECTOR = Path(__file__).resolve().parents[2] / "data" / "vectors" / "aapl_15k.itch"
BUCKET_BITS = int(os.environ.get("BOOK_BUCKET_BITS", "14"))
T = Timing(init_wait=int(os.environ.get("SDRAM_INIT_WAIT", "100")),
           ref_interval=int(os.environ.get("SDRAM_REF_INTERVAL", "500")))
CENTER = 2_000_000
FLAGS = ("stash_full", "unknown_ref", "bad_reduce", "shares_overflow", "level_overflow")


def fields(m):
    """Decoded message -> the order book's input ports."""
    t = m["message_type"]
    ref = m.get("original_order_reference_number", m.get("order_reference_number", 0))
    shares = m.get("shares", m.get("executed_shares", m.get("cancelled_shares", 0)))
    return {"msg_type": ord(t), "msg_ref": ref, "msg_new_ref": m.get("new_order_reference_number", 0),
            "msg_side_buy": int(m.get("buy_sell_indicator") == "B"), "msg_shares": shares,
            "msg_price": m.get("price", 0)}


def top(dut):
    return [(int(dut.bid1_price.value), int(dut.bid1_size.value)), (int(dut.bid2_price.value), int(dut.bid2_size.value)),
            (int(dut.ask1_price.value), int(dut.ask1_size.value)), (int(dut.ask2_price.value), int(dut.ask2_size.value))]


async def setup(dut, center):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    sdram = SdramModel(dut, T)
    sdram.start()
    dut.rst.value = 1
    dut.rd_lat.value = T.cl + 1
    dut.cfg_base.value = window_base(center)
    dut.msg_valid.value = 0
    dut.sd_dq_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    for _ in range(T.init_wait + 20 * (1 << BUCKET_BITS) + 10000):
        await FallingEdge(dut.clk)
        if int(dut.msg_ready.value) and int(dut.idle.value):
            return sdram
    raise AssertionError("SDRAM init and table clear never finished")


async def send(dut, m):
    """Give one message to the book and wait until it is fully applied.

    Returns (clocks until the next message could be taken, clocks until idle).
    The difference is the level update, which overlaps the next message.
    """
    for k, v in fields(m).items():
        getattr(dut, k).value = v
    dut.msg_valid.value = 1
    await FallingEdge(dut.clk)                  # msg_ready was high: taken at this rising edge
    dut.msg_valid.value = 0
    clocks, ready_at = 1, None
    while not int(dut.idle.value):
        if ready_at is None and int(dut.msg_ready.value):
            ready_at = clocks
        await FallingEdge(dut.clk)
        clocks += 1
    return (ready_at or clocks), clocks


async def run(dut, msgs, center, check_every=True):
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    sdram = await setup(dut, center)
    clocks = {}
    for i, m in enumerate(msgs):
        n = await send(dut, m)
        if model.apply(m):
            clocks.setdefault(m["message_type"], []).append(n)
        if check_every or i == len(msgs) - 1:
            want = model.top2("B") + model.top2("S")
            assert top(dut) == want, f"message {i} {m['message_type']}: got {top(dut)}, want {want}"
            got_flags = {f for f in FLAGS if int(getattr(dut, f).value)}
            assert got_flags == model.errors, f"message {i} {m['message_type']}: flags {got_flags}, want {model.errors}"
    assert not sdram.errors, "SDRAM model violations:\n" + "\n".join(sdram.errors)
    return model, clocks


def add(ref, side, price, shares, t="A"):
    return {"message_type": t, "order_reference_number": ref, "buy_sell_indicator": side,
            "price": price, "shares": shares}


def delete(ref):
    return {"message_type": "D", "order_reference_number": ref}


def execute(ref, n):
    return {"message_type": "E", "order_reference_number": ref, "executed_shares": n}


def replace(old, new, price, shares):
    return {"message_type": "U", "original_order_reference_number": old,
            "new_order_reference_number": new, "price": price, "shares": shares}


@cocotb.test()
async def test_real_data(dut):
    with open_itch(VECTOR) as f:
        msgs = [decode(raw) for raw in read_frames(f)]
    prices = sorted(m["price"] for m in msgs if m["message_type"] in "AF")
    model, clocks = await run(dut, msgs, prices[len(prices) // 2])
    for t, c in sorted(clocks.items()):
        nxt, done = sorted(x for x, _ in c), sorted(y for _, y in c)
        dut._log.info(f"{t}: {len(c):>6} messages, clocks to next message median {nxt[len(nxt) // 2]} max {nxt[-1]}, "
                      f"to idle median {done[len(done) // 2]} max {done[-1]}")
    dut._log.info(f"stash peak {model.stash_peak}, flags {sorted(model.errors)}")
    assert not model.errors


@cocotb.test(skip=not os.environ.get("BOOK_DAY_FILE"))
async def test_day_prefix_levels(dut):
    """Run with BOOK_DAY_FILE=<itch file> BOOK_DAY_MESSAGES=<n> BOOK_BUCKET_BITS=18.

    After each book message, every level the model changed for it is read
    straight from the level RAM and compared, so a wrong level is caught
    when it goes wrong, not when it reaches the top 2.
    """
    import sys
    from golden.itch.tools import filter_stock
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from book_stats import opening_mid

    class Recorder(HwBook):
        def _level(self, side, slot, delta):
            super()._level(side, slot, delta)
            self.touched.append((side, slot))

    path, limit = os.environ["BOOK_DAY_FILE"], int(os.environ.get("BOOK_DAY_MESSAGES", "60000"))
    center = opening_mid(path, "AAPL")
    model = Recorder(center, bucket_bits=BUCKET_BITS)
    await setup(dut, center)
    ram = dut.levels.size_mem
    for i, raw in enumerate(filter_stock(path, "AAPL"), 1):
        if i > limit:
            break
        m = decode(raw)
        model.touched = []
        await send(dut, m)
        if not model.apply(m):
            continue
        for sd, s in model.touched:
            got = int(ram[(sd == "S") << 12 | s].value)
            assert got == model.size[sd][s], f"message {i} {m}: level {sd} slot {s} RAM {got}, model {model.size[sd][s]}"
        assert top(dut) == model.top2("B") + model.top2("S"), f"message {i}: top 2"
        if i % 5000 == 0:
            dut._log.info(f"{i} messages ok")


@cocotb.test()
async def test_back_to_back_throughput(dut):
    """Messages as fast as the book takes them. Checks the end state and measures clocks per message.

    Every book message opens one SDRAM row (U opens two), so the gaps between
    row openings, as the SDRAM model saw them, are the cost of each message.
    """
    with open_itch(VECTOR) as f:
        msgs = [decode(raw) for raw in read_frames(f)]
    prices = sorted(m["price"] for m in msgs if m["message_type"] in "AF")
    center = prices[len(prices) // 2]
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    sdram = await setup(dut, center)
    first_act = len(sdram.acts)
    kinds = []
    for m in msgs:
        if model.apply(m):
            kinds += [m["message_type"]] * (2 if m["message_type"] == "U" else 1)
        for k, v in fields(m).items():
            getattr(dut, k).value = v
        dut.msg_valid.value = 1
        while True:
            taken = int(dut.msg_ready.value)
            await FallingEdge(dut.clk)
            if taken:
                break
        dut.msg_valid.value = 0
    while not int(dut.idle.value):
        await FallingEdge(dut.clk)
    assert top(dut) == model.top2("B") + model.top2("S")
    assert {f for f in FLAGS if int(getattr(dut, f).value)} == model.errors
    assert not sdram.errors, "SDRAM model violations:\n" + "\n".join(sdram.errors)

    acts = sdram.acts[first_act:]
    assert len(acts) == len(kinds)
    gaps = {}
    for kind, a, b in zip(kinds, acts, acts[1:]):
        if not any(a < r < b for r in sdram.refs):              # leave out gaps a refresh landed in
            gaps.setdefault(kind, []).append(b - a)
    for kind, g in sorted(gaps.items()):
        g.sort()
        dut._log.info(f"back to back {kind}: {len(g):>6} row openings, gap median {g[len(g) // 2]} max {g[-1]}")
    # The longest gaps include non-book messages taken in between, a clock each.
    # D, the shortest message (21 clocks at one byte per clock), must take at most 20;
    # A and F write 2 words and take one more.
    median = {k: g[len(g) // 2] for k, g in gaps.items()}
    assert median["D"] <= 20 and median["E"] <= 20 and median["A"] <= 21, median


@cocotb.test()
async def test_stash_and_every_flag(dut):
    """One bucket overfilled into the stash; orders in the stash reduced, replaced and deleted; every flag set."""
    same = [9 + k * (1 << BUCKET_BITS) for k in range(4 + 16 + 1)]       # 21 orders, one bucket
    msgs = [add(r, "B" if k % 2 else "S", CENTER + (k - 10) * 100, 100 + k) for k, r in enumerate(same)]
    msgs += [execute(same[5], 30),                       # in the stash
             execute(same[6], 1000),                     # more than it has: bad_reduce, all taken
             replace(same[7], 77, CENTER - 300, 55),     # stash order replaced into a normal bucket
             delete(same[8]),
             add(same[-1] + (1 << BUCKET_BITS), "B", CENTER, 5),  # same bucket; the stash has room again
             delete(123456),                             # unknown_ref
             replace(654321, 999, CENTER, 1),            # unknown_ref, nothing added
             add(500, "S", CENTER + 50, 10),             # sub-penny: in the table, not the levels
             add(501, "B", 10_000, 10),                  # far outside the window
             replace(501, 502, CENTER - 100, 10),        # moves into the window as a bid
             add(600, "S", CENTER + 100, 200_000),       # shares_overflow
             execute(500, 4)]
    msgs += [delete(r) for r in same[:5] + same[9:20] + [77, 500, 502, 600]]
    model, _ = await run(dut, msgs, CENTER)
    assert model.errors == {"stash_full", "unknown_ref", "bad_reduce", "shares_overflow"}
    assert model.stash_peak == 16


@cocotb.test()
async def test_random_with_collisions(dut):
    rng = random.Random(11)
    msgs, live, ref = [], [], 1
    for _ in range(2500):
        r = rng.random()
        if r < 0.45 or not live:
            ref += rng.choice([1, 1 << BUCKET_BITS, 3 << BUCKET_BITS])     # many orders share buckets
            side = rng.choice("BS")
            price = CENTER + (-rng.randint(0, 30) if side == "B" else rng.randint(0, 30)) * 100
            msgs.append(add(ref, side, price, rng.randint(1, 400), rng.choice("AF")))
            live.append(ref)
        elif r < 0.75:
            msgs.append(delete(live.pop(rng.randrange(len(live)))))
        elif r < 0.9:
            msgs.append(execute(rng.choice(live), rng.randint(1, 50)))
        else:
            old = live.pop(rng.randrange(len(live)))
            ref += 1 << BUCKET_BITS
            msgs.append(replace(old, ref, CENTER + rng.randint(-30, 30) * 100, rng.randint(1, 400)))
            live.append(ref)
    await run(dut, msgs, CENTER)
