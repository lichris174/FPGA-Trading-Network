"""cocotb test for synth/sdram_memtest.v, the SDRAM board test, against the SDRAM model.

Decodes the UART report lines and checks them: with the model's read
latency (CL + 1) the round has no errors, and every other latency in the
sweep does, so a clean line on the board means what it says.
"""

import os
import re

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from sdram_model import SdramModel, Timing

T = Timing(init_wait=int(os.environ.get("SDRAM_INIT_WAIT", "100")),
           ref_interval=int(os.environ.get("SDRAM_REF_INTERVAL", "780")))
BIT = int(os.environ.get("MEMTEST_CLKS_PER_BIT", "8"))
LINE = re.compile(r"lat=(\d) err=([0-9a-f]{8}) ph=(\d) addr=([0-9a-f]{6}) got=([0-9a-f]{8}) exp=([0-9a-f]{8})")


async def uart_lines(dut, lines):
    """Decode 8N1 on uart_tx at BIT clocks per bit, appending each finished line."""
    buf = ""
    while True:
        await FallingEdge(dut.clk)
        if int(dut.uart_tx.value):
            continue
        for _ in range(BIT // 2):                      # middle of the start bit
            await FallingEdge(dut.clk)
        byte = 0
        for i in range(8):
            for _ in range(BIT):
                await FallingEdge(dut.clk)
            byte |= int(dut.uart_tx.value) << i
        for _ in range(BIT):                           # stop bit
            await FallingEdge(dut.clk)
        buf += chr(byte)
        if buf.endswith("\r\n"):
            lines.append(buf.strip())
            buf = ""


@cocotb.test()
async def test_latency_sweep_report(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    model = SdramModel(dut, T)
    model.start()
    lines = []
    cocotb.start_soon(uart_lines(dut, lines))
    dut.rst.value = 1
    dut.sd_dq_in.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    for _ in range(400):
        await ClockCycles(dut.clk, 1000)
        if len(lines) >= 7:
            break
    for ln in lines:
        dut._log.info(ln)
    rounds = {int(m[1]): int(m[2], 16) for m in map(LINE.fullmatch, lines[:7]) if m}
    assert sorted(rounds) == list(range(1, 8)), f"bad report lines: {lines}"
    assert rounds[T.cl + 1] == 0, f"errors at the model's latency: {rounds}"
    assert all(n > 0 for lat, n in rounds.items() if lat != T.cl + 1), f"a wrong latency passed: {rounds}"
    assert not model.errors, "SDRAM model violations:\n" + "\n".join(model.errors)

    # After the full sweep the LEDs (on = low) show which latencies passed: only CL + 1,
    # and they must hold through the next rounds, not move with them.
    want = ~(1 << T.cl) & 0x3F
    for _ in range(4):
        await ClockCycles(dut.clk, 10)
        led = int(dut.led.value)
        dut._log.info(f"after {len(lines)} rounds: led = {led:06b}")
        assert led == want, f"led {led:06b}, expected {want:06b}"
        n = len(lines)
        while len(lines) == n:
            await ClockCycles(dut.clk, 1000)
