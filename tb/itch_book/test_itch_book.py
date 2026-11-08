"""cocotb tests for rtl/book/itch_book.v: real ITCH bytes in, top 2 out.

The bytes of data/vectors/aapl_15k.itch go through the parser (filtered
to AAPL), the FIFO and the order book, with the SDRAM model as memory.
The expected book is golden/hw_book.py fed the same messages. Bytes go
in at one per clock, the fastest the parser takes them, so the FIFO sees
the worst backlog the data can make.
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
FLAGS = ("stash_full", "unknown_ref", "bad_reduce", "shares_overflow", "level_overflow")


def load():
    with open_itch(VECTOR) as f:
        raws = list(read_frames(f))
    msgs = [decode(r) for r in raws]
    locate = next(m["stock_locate"] for m in msgs if m["message_type"] == "R" and m["stock"] == "AAPL")
    prices = sorted(m["price"] for m in msgs if m["message_type"] in "AF")
    return raws, msgs, locate, prices[len(prices) // 2]


def top(dut):
    return [(int(dut.bid1_price.value), int(dut.bid1_size.value)), (int(dut.bid2_price.value), int(dut.bid2_size.value)),
            (int(dut.ask1_price.value), int(dut.ask1_size.value)), (int(dut.ask2_price.value), int(dut.ask2_size.value))]


async def setup(dut, locate, center):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    sdram = SdramModel(dut, T)
    sdram.start()
    dut.rst.value = 1
    dut.rd_lat.value = T.cl + 1
    dut.cfg_base.value = window_base(center)
    dut.filter_en.value = 1
    dut.cfg_locate.value = locate
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.sd_dq_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    for _ in range(T.init_wait + 20 * (1 << BUCKET_BITS) + 10000):
        await FallingEdge(dut.clk)
        if int(dut.ready.value):
            return sdram
    raise AssertionError("SDRAM init and table clear never finished")


async def send(dut, raws, gap=0.0, rng=None):
    """Length-prefixed bytes, one per clock unless a random gap is drawn."""
    for raw in raws:
        for b in len(raw).to_bytes(2, "big") + raw:
            while rng is not None and rng.random() < gap:
                dut.in_valid.value = 0
                await FallingEdge(dut.clk)
            dut.in_data.value = b
            dut.in_valid.value = 1
            await FallingEdge(dut.clk)
    dut.in_valid.value = 0


async def settle(dut):
    for _ in range(3):                                  # the parser's last message reaches the FIFO
        await FallingEdge(dut.clk)
    while not int(dut.idle.value):
        await FallingEdge(dut.clk)


def check(dut, model, sdram, where):
    want = model.top2("B") + model.top2("S")
    assert top(dut) == want, f"{where}: got {top(dut)}, want {want}"
    assert {f for f in FLAGS if int(getattr(dut, f).value)} == model.errors, f"{where}: flags"
    assert not int(dut.fifo_overflow.value), f"{where}: FIFO overflow"
    assert not int(dut.parse_error.value), f"{where}: parse error"
    assert not sdram.errors, "SDRAM model violations:\n" + "\n".join(sdram.errors)


@cocotb.test()
async def test_real_bytes_in_chunks(dut):
    """1,000 messages at a time at full speed, then compare."""
    raws, msgs, locate, center = load()
    sdram = await setup(dut, locate, center)
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    for start in range(0, len(raws), 1000):
        await send(dut, raws[start:start + 1000])
        for m in msgs[start:start + 1000]:
            model.apply(m)
        await settle(dut)
        check(dut, model, sdram, f"after message {start + 1000}")
    dut._log.info(f"FIFO peak {int(dut.fifo_peak.value)} of 513")


@cocotb.test()
async def test_real_bytes_back_to_back(dut):
    """The whole slice without a pause: the worst backlog this data can make."""
    raws, msgs, locate, center = load()
    sdram = await setup(dut, locate, center)
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    for m in msgs:
        model.apply(m)
    await send(dut, raws)
    await settle(dut)
    check(dut, model, sdram, "end")
    dut._log.info(f"FIFO peak {int(dut.fifo_peak.value)} of 513, {len(raws)} messages, "
                  f"{sum(len(r) + 2 for r in raws):,} bytes")


@cocotb.test()
async def test_replace_burst_fills_the_fifo(dut):
    """Replaces back to back, then deletes: a backlog worse than either real day.

    A replace takes 41 clocks and arrives in 37, so 150 of them leave about
    15 queued; the deletes then arrive every 21 clocks while those drain,
    and the queue reaches 34 messages, past the 33 a 32-entry FIFO would hold. The real slice is the quiet start of
    the day and never queues more than a couple, so this checks the FIFO
    under load: the book must stay exact while it drains.
    """
    from golden.itch.parser import encode
    _, _, locate, center = load()
    h = {"stock_locate": locate, "tracking_number": 0, "timestamp": 0}
    msgs = [{"message_type": "A", "order_reference_number": 1000 + k, "buy_sell_indicator": "BS"[k % 2],
             "shares": 100 + k, "stock": "AAPL", "price": center + (k % 40 - 20) * 100, **h} for k in range(300)]
    msgs += [{"message_type": "U", "original_order_reference_number": 1000 + k, "new_order_reference_number": 5000 + k,
              "shares": 50 + k, "price": center + (k % 30 - 15) * 100, **h} for k in range(150)]
    msgs += [{"message_type": "D", "order_reference_number": 5000 + k, **h} for k in range(0, 150, 2)]
    sdram = await setup(dut, locate, center)
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    for m in msgs:
        model.apply(m)
    await send(dut, [encode(m) for m in msgs])
    await settle(dut)
    check(dut, model, sdram, "end")
    peak = int(dut.fifo_peak.value)
    dut._log.info(f"replace burst: FIFO peak {peak} of 513")
    assert peak > 33


@cocotb.test()
async def test_real_bytes_with_gaps(dut):
    """Random pauses between bytes, as when the bytes come in slower than the clock."""
    raws, msgs, locate, center = load()
    sdram = await setup(dut, locate, center)
    model = HwBook(center, bucket_bits=BUCKET_BITS)
    raws, msgs = raws[:5000], msgs[:5000]
    for m in msgs:
        model.apply(m)
    await send(dut, raws, gap=0.3, rng=random.Random(4))
    await settle(dut)
    check(dut, model, sdram, "end")
