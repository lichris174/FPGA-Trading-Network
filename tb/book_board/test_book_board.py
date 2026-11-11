"""cocotb tests for synth/book_board.v: the board replay protocol end to end.

Bytes go in on uart_rxd as serial bits (CLKS_PER_BIT clocks each), report
lines come back on uart_txd, and scripts/board_link.py reads them and
compares them with golden/hw_book.py, the same code the board replay
uses. The SDRAM is tb/sdram/sdram_model.py.
"""

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from board_link import REPORT_REQUEST, config, frame, mismatch, parse_report
from golden.hw_book import HwBook, window_base
from golden.itch.parser import decode, open_itch, read_frames
from sdram_model import SdramModel, Timing

VECTOR = Path(__file__).resolve().parents[2] / "data" / "vectors" / "aapl_15k.itch"
BUCKET_BITS = int(os.environ.get("BOOK_BUCKET_BITS", "14"))
BIT = int(os.environ.get("BOARD_CLKS_PER_BIT", "8"))
T = Timing(init_wait=int(os.environ.get("SDRAM_INIT_WAIT", "100")),
           ref_interval=int(os.environ.get("SDRAM_REF_INTERVAL", "500")))
MESSAGES = 1000
CHUNK = 250


class Board:
    def __init__(self, dut):
        self.dut, self.lines = dut, []

    async def start(self):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        self.sdram = SdramModel(dut, T)
        self.sdram.start()
        dut.rst.value = 1
        dut.uart_rxd.value = 1
        dut.sd_dq_in.value = 0
        await ClockCycles(dut.clk, 3)
        dut.rst.value = 0
        cocotb.start_soon(self._listen())

    async def send(self, data):
        dut = self.dut
        for b in data:
            for level in [0] + [b >> i & 1 for i in range(8)] + [1]:
                dut.uart_rxd.value = level
                for _ in range(BIT):
                    await FallingEdge(dut.clk)

    async def _listen(self):
        dut, buf = self.dut, ""
        while True:
            await FallingEdge(dut.clk)
            if int(dut.uart_txd.value):
                continue
            for _ in range(BIT // 2):
                await FallingEdge(dut.clk)
            byte = 0
            for i in range(8):
                for _ in range(BIT):
                    await FallingEdge(dut.clk)
                byte |= int(dut.uart_txd.value) << i
            for _ in range(BIT):
                await FallingEdge(dut.clk)
            buf += chr(byte)
            if buf.endswith("\r\n"):
                self.lines.append(buf)
                buf = ""

    async def report(self):
        """Ask for a report and wait for the line."""
        n = len(self.lines)
        await self.send(REPORT_REQUEST)
        for _ in range(T.init_wait + 20 * (1 << BUCKET_BITS) + 100000):
            await FallingEdge(self.dut.clk)
            if len(self.lines) > n:
                return parse_report(self.lines[-1])
        raise AssertionError("no report")


def load():
    with open_itch(VECTOR) as f:
        raws = list(read_frames(f))[:MESSAGES]
    msgs = [decode(r) for r in raws]
    locate = next(m["stock_locate"] for m in msgs if m["message_type"] == "R" and m["stock"] == "AAPL")
    prices = sorted(m["price"] for m in msgs if m["message_type"] in "AF")
    return raws, msgs, locate, prices[len(prices) // 2]


async def replay(board, raws, msgs, model, n=0):
    for start in range(0, len(raws), CHUNK):
        await board.send(b"".join(frame(r) for r in raws[start:start + CHUNK]))
        for m in msgs[start:start + CHUNK]:
            n += model.apply(m)
        r = await board.report()
        diff = mismatch(r, model, n)
        assert diff is None, f"after {start + CHUNK} messages:\n{diff}"
        assert r["fifo_peak"] <= 2
    return n, r


@cocotb.test()
async def test_replay_matches_model(dut):
    raws, msgs, locate, center = load()
    board = Board(dut)
    await board.start()
    await board.send(config(window_base(center), locate))
    r = await board.report()                            # waits for the table clear after the reset
    assert r["ready"] and r["n"] == 0 and r["top"] == [(0, 0)] * 4
    n, r = await replay(board, raws, msgs, HwBook(center, bucket_bits=BUCKET_BITS))
    assert n > 0 and r["lat_n"] == n, "every book message arrived with the book idle, so every one is timed"
    dut._log.info(f"{n} book messages, latency mean {r['lat_sum'] / r['lat_n']:.1f} clocks, "
                  f"min {r['lat_min']}, max {r['lat_max']}")
    assert 1 <= r["lat_min"] <= r["lat_max"] < 100


@cocotb.test()
async def test_config_reset_starts_over(dut):
    """A second config with the reset bit clears the book, the counters and the flags."""
    raws, msgs, locate, center = load()
    board = Board(dut)
    await board.start()
    await board.send(config(window_base(center), locate))
    await board.report()
    await board.send(b"".join(frame(r) for r in raws[:300]))
    # A delete of order 0, which doesn't exist: sets unknown_ref.
    await board.send(frame(b"D" + locate.to_bytes(2, "big") + bytes(16)))
    r = await board.report()
    assert r["n"] > 0 and "unknown_ref" in r["errors"]

    await board.send(config(window_base(center), locate))
    r = await board.report()
    assert r["ready"] and r["n"] == 0 and r["top"] == [(0, 0)] * 4 and not r["errors"] and r["lat_n"] == 0
    await replay(board, raws[:500], msgs[:500], HwBook(center, bucket_bits=BUCKET_BITS))
