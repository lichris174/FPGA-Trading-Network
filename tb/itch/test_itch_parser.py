"""cocotb tests for rtl/itch/itch_parser.v.

Every expected value comes from the golden parser (golden/itch/parser.py)
decoding the same bytes, so the Verilog is checked against the Python
model, not against numbers copied into this file.

The real-data tests replay data/vectors/aapl_15k.itch, the first 15,000
AAPL messages of a day, once with a byte on every clock and once with
random gaps in in_valid.
"""

import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from golden.itch.parser import decode, encode, read_frames

VECTORS = Path(__file__).resolve().parents[2] / "data" / "vectors" / "aapl_15k.itch"

# Parser output -> golden field name, for each decoded type.
FIELDS = {
    "A": {"order_ref": "order_reference_number", "side_buy": "buy_sell_indicator",
          "shares": "shares", "price": "price"},
    "E": {"order_ref": "order_reference_number", "shares": "executed_shares"},
    "C": {"order_ref": "order_reference_number", "shares": "executed_shares", "price": "execution_price"},
    "X": {"order_ref": "order_reference_number", "shares": "cancelled_shares"},
    "D": {"order_ref": "order_reference_number"},
    "U": {"order_ref": "original_order_reference_number", "new_order_ref": "new_order_reference_number",
          "shares": "shares", "price": "price"},
    "S": {"event_code": "event_code"},
    "H": {"trading_state": "trading_state"},
}
FIELDS["F"] = FIELDS["A"]
FIELDS["P"] = FIELDS["A"]
OUTPUTS = ("order_ref", "new_order_ref", "side_buy", "shares", "price", "event_code", "trading_state")


def expected(msg):
    """What the parser should output for one message, or None if it should stay silent."""
    d = decode(msg)
    t = d["message_type"]
    if t not in FIELDS:
        return None
    out = dict.fromkeys(OUTPUTS, 0)
    out.update(msg_type=ord(t), stock_locate=d["stock_locate"], timestamp=d["timestamp"])
    for port, name in FIELDS[t].items():
        v = d[name]
        if name == "buy_sell_indicator":
            v = int(v == "B")
        elif isinstance(v, str):
            v = ord(v)
        out[port] = v
    return out


def frame(msgs):
    """Messages -> length-prefixed bytes, the parser's input format."""
    return b"".join(len(m).to_bytes(2, "big") + m for m in msgs)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.filter_en.value = 0
    dut.cfg_locate.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    assert int(dut.in_ready.value) == 1


class Monitor:
    """Records every msg_valid and msg_error pulse."""

    def __init__(self, dut):
        self.dut, self.got, self.errors = dut, [], 0
        cocotb.start_soon(self._run())

    async def _run(self):
        dut = self.dut
        while True:
            await FallingEdge(dut.clk)
            if int(dut.msg_error.value):
                self.errors += 1
            if int(dut.msg_valid.value):
                rec = {k: int(getattr(dut, k).value) for k in ("msg_type", "stock_locate", "timestamp", *OUTPUTS)}
                self.got.append(rec)


async def send(dut, data, gap=0.0, rng=None):
    """Drive bytes on falling edges; the parser takes them on the next rising edge."""
    for b in data:
        while rng is not None and rng.random() < gap:
            dut.in_valid.value = 0
            await FallingEdge(dut.clk)
        dut.in_data.value = b
        dut.in_valid.value = 1
        await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)


def compare(got, msgs):
    want = [e for e in map(expected, msgs) if e is not None]
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, f"decoded message {i}: parser {g}, golden {w}"
    assert len(got) == len(want), f"parser gave {len(got)} messages, golden {len(want)}"


def one_of_each(locate=7):
    """Every decoded type with large field values, plus three types the parser must skip."""
    big64, big32, ts = (1 << 64) - 2, (1 << 32) - 3, (1 << 48) - 5
    h = {"stock_locate": locate, "tracking_number": 0xBEEF, "timestamp": ts}
    return [encode(m) for m in (
        {"message_type": "S", "event_code": "Q", **h},
        {"message_type": "R", "stock": "AAPL", **h},
        {"message_type": "H", "stock": "AAPL", "trading_state": "T", **h},
        {"message_type": "A", "order_reference_number": big64, "buy_sell_indicator": "B",
         "shares": big32, "stock": "AAPL", "price": 2_100_000, **h},
        {"message_type": "F", "order_reference_number": 12, "buy_sell_indicator": "S",
         "shares": 100, "stock": "AAPL", "price": 2_100_100, "attribution": "MPID", **h},
        {"message_type": "E", "order_reference_number": big64, "executed_shares": 5, "match_number": 9, **h},
        {"message_type": "C", "order_reference_number": 12, "executed_shares": 7, "match_number": 9,
         "printable": "Y", "execution_price": big32, **h},
        {"message_type": "X", "order_reference_number": 12, "cancelled_shares": 3, **h},
        {"message_type": "Q", "shares": 1 << 40, "stock": "AAPL", "cross_price": 1, "match_number": 2, **h},
        {"message_type": "U", "original_order_reference_number": 12, "new_order_reference_number": big64 - 1,
         "shares": 50, "price": 2_099_900, **h},
        {"message_type": "D", "order_reference_number": big64 - 1, **h},
        {"message_type": "P", "order_reference_number": 0, "buy_sell_indicator": "B", "shares": 1,
         "stock": "AAPL", "price": 1, "match_number": 3, **h},
        {"message_type": "I", "stock": "AAPL", **h},
    )]


@cocotb.test()
async def test_every_type(dut):
    """Each decoded type comes out with the golden fields; skipped types produce nothing."""
    await setup(dut)
    mon = Monitor(dut)
    msgs = one_of_each()
    await send(dut, frame(msgs))
    compare(mon.got, msgs)
    assert mon.errors == 0


@cocotb.test()
async def test_stock_filter(dut):
    """With the filter on, only the chosen locate and system events pass."""
    await setup(dut)
    mon = Monitor(dut)
    dut.filter_en.value = 1
    dut.cfg_locate.value = 7
    mine, other = one_of_each(locate=7), one_of_each(locate=8)
    await send(dut, frame(mine + other))
    passed = mine + [m for m in other if m[:1] == b"S"]
    compare(mon.got, passed)


@cocotb.test()
async def test_wrong_length_is_dropped(dut):
    """A decoded type with the wrong length pulses msg_error, and the next message still parses."""
    await setup(dut)
    mon = Monitor(dut)
    good = one_of_each()
    short = good[3][:-1]                   # add order one byte short
    long = good[5] + b"\x00"               # executed one byte long
    await send(dut, frame([short, long, good[3]]))
    assert mon.errors == 2
    compare(mon.got, [good[3]])


@cocotb.test()
async def test_empty_and_unknown_messages_are_skipped(dut):
    """A zero-length frame and an unknown type byte don't disturb the framing."""
    await setup(dut)
    mon = Monitor(dut)
    good = one_of_each()
    stream = frame([good[0]]) + b"\x00\x00" + frame([b"z" + bytes(20), good[3]])
    await send(dut, stream)
    compare(mon.got, [good[0], good[3]])
    assert mon.errors == 0


@cocotb.test()
async def test_reset_mid_message(dut):
    """Reset in the middle of a message starts cleanly on the next frame."""
    await setup(dut)
    mon = Monitor(dut)
    good = one_of_each()
    await send(dut, frame([good[3]])[:20])
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await FallingEdge(dut.clk)
    await send(dut, frame([good[3], good[5]]))
    compare(mon.got, [good[3], good[5]])


@cocotb.test()
async def test_real_day_back_to_back(dut):
    """15,000 real AAPL messages, one byte every clock."""
    await setup(dut)
    mon = Monitor(dut)
    data = VECTORS.read_bytes()
    msgs = list(read_frames(_Bytes(data)))
    await send(dut, data)
    compare(mon.got, msgs)
    assert mon.errors == 0
    dut._log.info(f"{len(msgs):,} messages, {len(mon.got):,} decoded, all match the golden parser")


@cocotb.test()
async def test_real_day_with_gaps(dut):
    """The first 3,000 real messages with in_valid low on about 30% of clocks."""
    await setup(dut)
    mon = Monitor(dut)
    msgs = list(read_frames(_Bytes(VECTORS.read_bytes())))[:3000]
    await send(dut, frame(msgs), gap=0.3, rng=random.Random(1))
    compare(mon.got, msgs)
    assert mon.errors == 0


class _Bytes:
    """Minimal binary stream over a bytes object, for read_frames."""

    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk
