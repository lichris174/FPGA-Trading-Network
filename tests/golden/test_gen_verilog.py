"""Tests for golden/itch/gen_verilog.py: the committed Verilog header matches spec.py."""

import re

from golden.itch.gen_verilog import OUT, render
from golden.itch.spec import MESSAGES


def test_committed_header_is_up_to_date():
    assert OUT.exists(), "run python -m golden.itch.gen_verilog"
    assert OUT.read_text() == render(), "rtl/itch/itch_spec.vh is stale, run python -m golden.itch.gen_verilog"


def test_header_has_every_message_and_field():
    text = render()
    for m in MESSAGES.values():
        p = f"ITCH_{m.name.upper()}"
        assert f"localparam [7:0] {p}_TYPE = 8'h{ord(m.code):02X};" in text
        assert f"localparam {p}_LEN = {m.length};" in text
        for f in m.fields[4:]:
            assert f"localparam {p}_{f.name.upper()}_OFF = {f.offset};" in text


def test_names_are_unique_and_valid_verilog():
    names = re.findall(r"localparam(?: \[7:0\])? (\w+) =", render())
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", n) for n in names)


def test_known_offsets():
    text = render()
    assert "localparam ITCH_ADD_ORDER_PRICE_OFF = 32;" in text
    assert "localparam ITCH_ORDER_EXECUTED_WITH_PRICE_EXECUTION_PRICE_OFF = 32;" in text
    assert "localparam ITCH_ORDER_REPLACE_NEW_ORDER_REFERENCE_NUMBER_OFF = 19;" in text
