"""cocotb tests for rtl/common/fifo.v against a Python deque.

The Makefile builds a 4-entry RAM (5 entries with dout) so filling it
and overflowing it take a few clocks.
"""

import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

DEPTH = 4 + 1


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.push.value = 0
    dut.pop.value = 0
    dut.din.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await FallingEdge(dut.clk)


async def run(dut, rng, cycles, p_push, p_pop):
    """Random pushes and pops; everything that went in must come out in order, except pushes when full."""
    ref, out, dropped, n = deque(), [], 0, 0
    for _ in range(cycles):
        push = rng.random() < p_push
        pop = int(dut.out_valid.value) and rng.random() < p_pop
        level = int(dut.level.value)
        if pop:
            out.append(int(dut.dout.value))
            assert out[-1] == ref.popleft(), f"popped {out[-1]}"
        if push:
            if int(dut.ram_level.value) == DEPTH - 1:
                dropped += 1                        # RAM full: the push is dropped
            else:
                ref.append(n)
        assert level <= DEPTH
        dut.push.value, dut.din.value, dut.pop.value = push, n, pop
        n += push
        await FallingEdge(dut.clk)
    dut.push.value = dut.pop.value = 0
    return ref, out, dropped


@cocotb.test()
async def test_order_without_overflow(dut):
    await setup(dut)
    ref, out, dropped = await run(dut, random.Random(1), 3000, 0.15, 0.8)   # it drains one entry per 2 clocks
    assert dropped == 0 and len(out) > 300
    assert int(dut.overflow.value) == 0
    assert 1 <= int(dut.peak.value) <= DEPTH


@cocotb.test()
async def test_overflow_drops_and_sticks(dut):
    await setup(dut)
    ref, out, dropped = await run(dut, random.Random(2), 3000, 0.7, 0.3)
    assert dropped > 0
    assert int(dut.overflow.value) == 1
    assert int(dut.peak.value) == DEPTH


@cocotb.test()
async def test_drains_completely(dut):
    await setup(dut)
    for k in range(4):
        dut.push.value, dut.din.value = 1, 100 + k
        await FallingEdge(dut.clk)
    dut.push.value = 0
    got = []
    for _ in range(30):
        if int(dut.out_valid.value):
            got.append(int(dut.dout.value))
            dut.pop.value = 1
        else:
            dut.pop.value = 0
        await FallingEdge(dut.clk)
    assert got == [100, 101, 102, 103]
    assert int(dut.level.value) == 0 and int(dut.out_valid.value) == 0
