"""cocotb tests for rtl/sdram/sdram_ctrl.v against the timing-checking model in sdram_model.py.

Every test ends by asserting the model saw no timing or protocol
violation, so data tests also check the command sequence behind them.
The rmw budget test checks the order book's case: one bucket read plus
a 2-word write-back has to fit in the 21 clocks of the shortest message.
"""

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from sdram_model import Timing, SdramModel

T = Timing(init_wait=int(os.environ.get("SDRAM_INIT_WAIT", "100")),
           ref_interval=int(os.environ.get("SDRAM_REF_INTERVAL", "780")))
OP_READ, OP_WRITE, OP_RMW = 0, 1, 2
WORDS = 1 << 21
BUDGET = 21
RD_LAT = int(os.environ.get("SDRAM_RD_LAT", T.cl + 1))    # set to 2 or 4 to see the data checks fail


class Driver:
    """Drives the controller's user side on falling edges, one operation at a time."""

    def __init__(self, dut):
        self.dut = dut
        dut.cmd_valid.value = 0
        dut.cmd_op.value = 0
        dut.cmd_addr.value = 0
        dut.wr_len.value = 0
        dut.wdata.value = 0
        dut.wb_valid.value = 0
        dut.wb_off.value = 0
        dut.wb_len.value = 0
        dut.wb_skip.value = 0
        dut.rd_lat.value = RD_LAT

    async def _handshake(self, valid, ready):
        """Raise valid on a falling edge; it is taken at the first rising edge where ready is high."""
        valid.value = 1
        while True:
            taken = int(ready.value)    # ready comes from registers, stable until the next rising edge
            await FallingEdge(self.dut.clk)
            if taken:
                break
        valid.value = 0

    async def _issue(self, op, addr, wr_len=0, first=0):
        dut = self.dut
        dut.cmd_op.value, dut.cmd_addr.value, dut.wr_len.value = op, addr, wr_len
        dut.wdata.value = first
        await self._handshake(dut.cmd_valid, dut.cmd_ready)

    async def _stream(self, words):
        """Hold word 0 on wdata, advance after every cycle wnext was high."""
        dut = self.dut
        i = 0
        dut.wdata.value = words[0]
        while i < len(words):
            if int(dut.wnext.value):
                i += 1
                await FallingEdge(dut.clk)
                if i < len(words):
                    dut.wdata.value = words[i]
            else:
                await FallingEdge(dut.clk)

    async def _collect(self):
        dut, out = self.dut, []
        while len(out) < 8:
            await FallingEdge(dut.clk)
            if int(dut.rvalid.value):
                out.append(int(dut.rdata.value))
        return out

    async def write(self, addr, words):
        await self._issue(OP_WRITE, addr, len(words) - 1, words[0])
        await self._stream(words)

    async def read(self, addr):
        await self._issue(OP_READ, addr)
        return await self._collect()

    async def rmw(self, addr, update):
        """update(words) -> (off, new_words) or None to skip. Returns the words read."""
        dut = self.dut
        await self._issue(OP_RMW, addr)
        words = await self._collect()
        change = update(words)
        if change is None:
            dut.wb_skip.value = 1
        else:
            off, new = change
            dut.wb_skip.value, dut.wb_off.value, dut.wb_len.value = 0, off, len(new) - 1
            dut.wdata.value = new[0]
        await self._handshake(dut.wb_valid, dut.wb_ready)
        if change is not None:
            await self._stream(change[1])
        return words


class Ref:
    def __init__(self):
        self.mem = {}

    def word(self, a):
        from sdram_model import init_word
        return self.mem.get(a, init_word(a))

    def burst(self, a):
        base = a & ~7
        return [self.word(base | ((a + i) & 7)) for i in range(8)]


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    model = SdramModel(dut, T)
    model.start()
    drv = Driver(dut)
    dut.rst.value = 1
    dut.sd_dq_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    for _ in range(T.init_wait + 200):
        await FallingEdge(dut.clk)
        if int(dut.init_done.value):
            break
    assert int(dut.init_done.value), "init_done never went high"
    return model, drv


async def idle(dut, n):
    """Wait n clocks, ending on a falling edge like every Driver call."""
    for _ in range(n):
        await FallingEdge(dut.clk)


def check(model):
    assert not model.errors, "SDRAM model violations:\n" + "\n".join(model.errors)


def rand_group(rng):
    """A random aligned group of 8 words."""
    return rng.randrange(WORDS // 8) * 8


@cocotb.test()
async def test_power_up(dut):
    model, _ = await setup(dut)
    await idle(dut, 50)
    assert model.mode_set
    assert model.counts["REF"] >= 8 and model.counts["PRE"] >= 1
    check(model)


@cocotb.test()
async def test_write_then_read(dut):
    model, drv = await setup(dut)
    words = [0x11111111 * (i + 1) for i in range(8)]
    await drv.write(0x12340, words)
    got = await drv.read(0x12340)
    assert got == words, [hex(w) for w in got]
    check(model)


@cocotb.test()
async def test_random_reads_and_writes(dut):
    """Writes of 1 to 8 words and bursts at random addresses, checked against a reference memory."""
    model, drv = await setup(dut)
    rng, ref = random.Random(1), Ref()
    groups = [rand_group(rng) for _ in range(40)]         # reuse addresses so reads hit written data
    for _ in range(600):
        base = rng.choice(groups)
        if rng.random() < 0.5:
            off = rng.randrange(8)
            n = rng.randint(1, 8 - off)
            words = [rng.getrandbits(32) for _ in range(n)]
            await drv.write(base + off, words)
            for i, w in enumerate(words):
                ref.mem[base + off + i] = w
        else:
            got = await drv.read(base)
            assert got == ref.burst(base), f"read {base:#x}: {[hex(w) for w in got]}"
        if rng.random() < 0.2:
            await idle(dut, rng.randint(1, 30))
    check(model)


@cocotb.test()
async def test_rmw(dut):
    """Read a bucket, write back 1 to 8 words at an offset or skip, then verify with plain reads."""
    model, drv = await setup(dut)
    rng, ref = random.Random(2), Ref()
    groups = [rand_group(rng) for _ in range(30)]
    for _ in range(400):
        base = rng.choice(groups)
        plan = {}

        def update(words):
            assert words == ref.burst(base), f"rmw read {base:#x}"
            if rng.random() < 0.2:
                return None
            off = rng.randrange(8)
            new = [rng.getrandbits(32) for _ in range(rng.randint(1, 8))]
            plan["w"] = (off, new)
            return off, new

        await drv.rmw(base, update)
        if "w" in plan:
            off, new = plan["w"]
            for i, w in enumerate(new):
                ref.mem[base + ((off + i) & 7)] = w
        if rng.random() < 0.3:
            got = await drv.read(base)
            assert got == ref.burst(base), f"read after rmw {base:#x}"
    check(model)


@cocotb.test()
async def test_refresh_under_load(dut):
    """Back-to-back reads must not hold off refresh longer than one operation."""
    model, drv = await setup(dut)
    rng = random.Random(3)
    start = len(model.refs)
    for _ in range(1500):
        await drv.read(rand_group(rng))
    gaps = [b - a for a, b in zip(model.refs[start:], model.refs[start + 1:])]
    assert gaps, "no refreshes under load"
    assert max(gaps) <= T.ref_interval + BUDGET, f"refresh gap {max(gaps)}"
    dut._log.info(f"{len(gaps)} refreshes under load, gap {min(gaps)} to {max(gaps)} cycles")
    check(model)


@cocotb.test()
async def test_rmw_fits_message_budget(dut):
    """Bucket read + 2-word write-back, back to back, against the 21-clock budget of a D message."""
    model, drv = await setup(dut)
    rng = random.Random(4)
    first = len(model.acts)
    for _ in range(50):
        await drv.rmw(rand_group(rng), lambda w: (2, [w[2] ^ 1, w[3] ^ 1]))
    starts = model.acts[first:]
    per_op = [b - a for a, b in zip(starts, starts[1:])]
    per_op.sort()
    typical = per_op[len(per_op) // 2]
    dut._log.info(f"rmw cycle: median {typical}, max {per_op[-1]} clocks (max includes refresh)")
    assert typical <= BUDGET, f"rmw takes {typical} clocks, budget {BUDGET}"
    check(model)
