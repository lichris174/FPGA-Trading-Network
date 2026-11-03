"""Cycle model of the GW2AR-18's SDR SDRAM for cocotb, with timing checks.

The model samples the controller's pins on each falling edge, which is
what the SDRAM sees at the next rising edge, so "cycle n" below is SDRAM
clock edge n. It keeps the memory contents, returns read bursts at CAS
latency CL, and records a violation (instead of raising inside the
coroutine) for anything a real part would get wrong or refuse:

    power-up    commands before the wait is over, ACT/READ/WRITE before
                the mode register, fewer than 2 refreshes before it
    bank state  ACT on an open bank, READ/WRITE/PRE on a closed one
    timing      tRCD, tRP, tRC, tRAS, tWR, tRFC, tMRD, refresh interval
    bus         controller driving DQ while the SDRAM is driving a burst
    mode        anything but burst 8, sequential, CL, single-location writes

Unwritten words read as init_word(addr), so a read of the wrong address
or a dropped write shows up as a data mismatch.
"""

import random
from dataclasses import dataclass

import cocotb
from cocotb.triggers import FallingEdge

NOP, ACT, READ, WRITE, PRE, REF, MRS = "NOP", "ACT", "READ", "WRITE", "PRE", "REF", "MRS"
COMMANDS = {0b0111: NOP, 0b0011: ACT, 0b0101: READ, 0b0100: WRITE, 0b0010: PRE, 0b0001: REF, 0b0000: MRS}
BL = 8
NEVER = -10**9


@dataclass(frozen=True)
class Timing:
    cl: int = 2
    t_rcd: int = 2
    t_rp: int = 2
    t_rc: int = 7
    t_ras: int = 5
    t_rfc: int = 7
    t_wr: int = 2
    t_mrd: int = 2
    init_wait: int = 100
    ref_interval: int = 780


def init_word(addr):
    return (addr * 0x9E3779B1 + 0x7F4A7C15) & 0xFFFFFFFF


def word_addr(bank, row, col):
    """The controller's word address layout: {row, bank, col}."""
    return (row << 10) | (bank << 8) | col


class Bank:
    def __init__(self):
        self.row = None          # open row, or None
        self.act = NEVER         # cycle of the last ACT
        self.idle_at = 0         # first cycle an ACT may follow the last precharge
        self.last_write = NEVER
        self.auto_pre = None     # cycle an auto precharge starts, while one is pending


class SdramModel:
    def __init__(self, dut, timing=Timing()):
        self.dut, self.t = dut, timing
        self.mem = {}
        self.banks = [Bank() for _ in range(4)]
        self.cycle = 0
        self.drive = {}          # cycle -> word the SDRAM drives for that edge
        self.mode_set = False
        self.refs = []           # cycles of every REF
        self.acts = []           # cycles of every ACT
        self.last_ref = NEVER
        self.last_mrs = NEVER
        self.errors = []
        self.counts = {c: 0 for c in COMMANDS.values()}

    def word(self, addr):
        return self.mem.get(addr, init_word(addr))

    def error(self, msg):
        if len(self.errors) < 20:
            self.errors.append(f"cycle {self.cycle}: {msg}")

    def start(self):
        return cocotb.start_soon(self._run())

    async def _run(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            if int(dut.rst.value):
                self.cycle = 0
                continue
            self.cycle += 1
            self._settle()
            cmd = COMMANDS[(int(dut.sd_cs_n.value) << 3) | (int(dut.sd_ras_n.value) << 2)
                           | (int(dut.sd_cas_n.value) << 1) | int(dut.sd_we_n.value)]
            self.counts[cmd] += 1
            if cmd != NOP:
                self._command(cmd, int(dut.sd_ba.value), int(dut.sd_addr.value))
            n = self.cycle
            if int(dut.sd_dq_oe.value) and n in self.drive:
                self.error("controller drives DQ during a read burst")
            dut.sd_dq_in.value = self.drive.pop(n, random.getrandbits(32))

    def _settle(self):
        """Finish auto precharges whose start time has come."""
        for b in self.banks:
            if b.auto_pre is not None and b.auto_pre <= self.cycle:
                b.row, b.idle_at, b.auto_pre = None, b.auto_pre + self.t.t_rp, None

    def _command(self, cmd, ba, a):
        t, n = self.t, self.cycle
        if n < t.init_wait:
            self.error(f"{cmd} before the {t.init_wait}-cycle power-up wait")
        if n - self.last_ref < t.t_rfc:
            self.error(f"{cmd} {n - self.last_ref} cycles after REF (tRFC {t.t_rfc})")
        if n - self.last_mrs < t.t_mrd:
            self.error(f"{cmd} {n - self.last_mrs} cycles after MRS (tMRD {t.t_mrd})")
        if cmd in (ACT, READ, WRITE) and not self.mode_set:
            self.error(f"{cmd} before the mode register is set")
        b = self.banks[ba]

        if cmd == ACT:
            if b.row is not None or b.auto_pre is not None:
                self.error(f"ACT on open bank {ba}")
            if n < b.idle_at:
                self.error(f"ACT bank {ba} {n - b.idle_at + t.t_rp} cycles after precharge (tRP {t.t_rp})")
            if n - b.act < t.t_rc:
                self.error(f"ACT bank {ba} {n - b.act} cycles after ACT (tRC {t.t_rc})")
            b.row, b.act = a, n
            self.acts.append(n)
        elif cmd in (READ, WRITE):
            if b.row is None or b.auto_pre is not None:
                self.error(f"{cmd} on closed bank {ba}")
                return
            if n - b.act < t.t_rcd:
                self.error(f"{cmd} bank {ba} {n - b.act} cycles after ACT (tRCD {t.t_rcd})")
            col, ap = a & 0xFF, (a >> 10) & 1
            if cmd == READ:
                for i in range(BL):
                    c = (col & ~7) | ((col + i) & 7)
                    self.drive[n + t.cl + i] = self.word(word_addr(ba, b.row, c))
                start = n + BL
            else:
                if int(self.dut.sd_dqm.value) != 0:
                    self.error("WRITE with DQM set")
                if not int(self.dut.sd_dq_oe.value):
                    self.error("WRITE without driving DQ")
                self.mem[word_addr(ba, b.row, col)] = int(self.dut.sd_dq_out.value)
                b.last_write = n
                start = n + t.t_wr
            if ap:
                b.auto_pre = max(start, b.act + t.t_ras)
        elif cmd == PRE:
            targets = range(4) if (a >> 10) & 1 else [ba]
            for i in targets:
                bk = self.banks[i]
                if bk.row is None:
                    if not (a >> 10) & 1:
                        self.error(f"PRE on closed bank {i}")
                    continue
                if n - bk.act < t.t_ras:
                    self.error(f"PRE bank {i} {n - bk.act} cycles after ACT (tRAS {t.t_ras})")
                if n - bk.last_write < t.t_wr:
                    self.error(f"PRE bank {i} {n - bk.last_write} cycles after WRITE (tWR {t.t_wr})")
                bk.row, bk.idle_at = None, n + t.t_rp
        elif cmd == REF:
            for i, bk in enumerate(self.banks):
                if bk.row is not None or bk.auto_pre is not None or n < bk.idle_at:
                    self.error(f"REF with bank {i} not idle")
                if n - bk.act < t.t_rc:
                    self.error(f"REF {n - bk.act} cycles after ACT bank {i} (tRC {t.t_rc})")
            self.refs.append(n)
            self.last_ref = n
        elif cmd == MRS:
            if any(bk.row is not None for bk in self.banks):
                self.error("MRS with a bank open")
            if len(self.refs) < 2:
                self.error(f"MRS after only {len(self.refs)} refreshes")
            want = (1 << 9) | (t.cl << 4) | 0b011
            if a != want:
                self.error(f"mode register {a:#05x}, expected {want:#05x}")
            self.mode_set = True
            self.last_mrs = n

    def max_refresh_gap(self):
        """Longest gap between refreshes after power-up."""
        gaps = [b - a for a, b in zip(self.refs, self.refs[1:])]
        return max(gaps, default=0)
