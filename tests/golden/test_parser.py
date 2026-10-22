"""Tests for golden/itch/spec.py and golden/itch/parser.py."""

import io
import random

import pytest

from golden.itch.parser import ItchError, decode, encode, read_frames, write_frames
from golden.itch.spec import ALPHA, MESSAGES

# Message lengths from the ITCH 5.0 spec, written out separately from
# spec.py so a mistake has to be made twice to slip through.
SPEC_LENGTHS = {
    "S": 12, "R": 39, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12, "K": 28,
    "J": 35, "h": 21, "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19,
    "U": 35, "P": 44, "Q": 40, "B": 19, "I": 50, "N": 20, "O": 48,
}


def test_all_types_present_with_spec_lengths():
    assert {c: m.length for c, m in MESSAGES.items()} == SPEC_LENGTHS


def test_add_order_offsets_match_spec():
    f = {x.name: (x.offset, x.size) for x in MESSAGES["A"].fields}
    assert f["timestamp"] == (5, 6)
    assert f["order_reference_number"] == (11, 8)
    assert f["buy_sell_indicator"] == (19, 1)
    assert f["shares"] == (20, 4)
    assert f["stock"] == (24, 8)
    assert f["price"] == (32, 4)


def test_known_bytes_decode():
    """A hand-assembled Add Order: buy 100 AAPL at $150.2500."""
    msg = (b"A" + (7).to_bytes(2, "big") + (0).to_bytes(2, "big")
           + (34_200_000_000_000).to_bytes(6, "big")     # 09:30:00
           + (123456).to_bytes(8, "big") + b"B" + (100).to_bytes(4, "big")
           + b"AAPL    " + (1_502_500).to_bytes(4, "big"))
    d = decode(msg)
    assert d == {
        "message_type": "A", "stock_locate": 7, "tracking_number": 0,
        "timestamp": 34_200_000_000_000, "order_reference_number": 123456,
        "buy_sell_indicator": "B", "shares": 100, "stock": "AAPL", "price": 1_502_500,
    }
    assert encode(d) == msg


def random_fields(rng, code):
    out = {}
    for f in MESSAGES[code].fields:
        if f.name == "message_type":
            out[f.name] = code
        elif f.kind == ALPHA:
            out[f.name] = "".join(rng.choice("ABCXYZ019") for _ in range(rng.randint(1, f.size)))
        else:
            out[f.name] = rng.randrange(1 << (8 * f.size))
    return out


@pytest.mark.parametrize("code", sorted(MESSAGES))
def test_roundtrip_every_type(code):
    rng = random.Random(code)
    for _ in range(50):
        fields = random_fields(rng, code)
        msg = encode(fields)
        assert len(msg) == MESSAGES[code].length
        assert decode(msg) == fields


def test_framing_across_chunk_boundaries():
    rng = random.Random(1)
    msgs = [encode(random_fields(rng, rng.choice(sorted(MESSAGES)))) for _ in range(500)]
    buf = io.BytesIO()
    write_frames(buf, msgs)
    for chunk in (1, 2, 3, 7, 64, 1 << 20):
        assert list(read_frames(io.BytesIO(buf.getvalue()), chunk_size=chunk)) == msgs


def test_truncated_file_raises():
    buf = io.BytesIO()
    write_frames(buf, [encode({"message_type": "D", "order_reference_number": 5})])
    with pytest.raises(ItchError):
        list(read_frames(io.BytesIO(buf.getvalue()[:-1])))


def test_bad_messages_raise():
    with pytest.raises(ItchError):
        decode(b"Z" + bytes(10))
    with pytest.raises(ItchError):
        decode(encode({"message_type": "D"}) + b"\x00")
