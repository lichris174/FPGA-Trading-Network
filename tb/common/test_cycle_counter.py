"""cocotb tests for rtl/common/cycle_counter.v.

The Makefile in this folder builds the counter with WIDTH=8 so the
wraparound test only needs 256 cycles.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge


async def setup(dut):
    """Start the clock and hold reset for two cycles."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.en.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await FallingEdge(dut.clk)


@cocotb.test()
async def test_reset(dut):
    """Counter reads zero after reset, even if it was counting before."""
    await setup(dut)
    assert int(dut.count.value) == 0

    dut.en.value = 1
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    assert int(dut.count.value) != 0

    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    assert int(dut.count.value) == 0


@cocotb.test()
async def test_counts_up(dut):
    """Counter increments by exactly one per enabled cycle."""
    await setup(dut)
    dut.en.value = 1
    for expected in range(1, 50):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        assert int(dut.count.value) == expected


@cocotb.test()
async def test_enable_pauses(dut):
    """Counter holds its value while en is low, using a random enable pattern."""
    await setup(dut)
    rng = random.Random(1234)
    width = len(dut.count)
    expected = 0
    for _ in range(200):
        en = rng.randint(0, 1)
        dut.en.value = en
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        expected = (expected + en) % (1 << width)
        assert int(dut.count.value) == expected


@cocotb.test()
async def test_wraparound(dut):
    """Counter wraps from all ones back to zero."""
    await setup(dut)
    width = len(dut.count)
    max_value = (1 << width) - 1
    dut.en.value = 1
    await ClockCycles(dut.clk, max_value)
    await FallingEdge(dut.clk)
    assert int(dut.count.value) == max_value

    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    assert int(dut.count.value) == 0
