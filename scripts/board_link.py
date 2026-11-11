"""The serial protocol of synth/book_board.v, shared by the board replay and its testbench.

Everything sent is the ITCH length-prefixed stream, plus two kinds of
frame the parser skips:

    00 00                 report request
    00 08 'Z' flags base locate
                          config: flags bit 0 resets the book; cfg_base
                          (4 bytes) and cfg_locate (2 bytes), big-endian

The board answers a report request with one line once the book is idle;
parse_report() reads it and expected() builds the same fields from the
Python model, so the two compare with ==.
"""

import re

from golden.hw_book import HwBook

REPORT_REQUEST = b"\x00\x00"
LINE = re.compile(r"n=(\w{8}) b=(\w{8}):(\w{5}) (\w{8}):(\w{5}) a=(\w{8}):(\w{5}) (\w{8}):(\w{5}) "
                  r"f=(\w{2}) q=(\w{3}) lat=(\w{8}) (\w{10}) (\w{4}) (\w{4}) c=(\w{8}) (\w{8}) r=(\w{8}) (\w{8})")
FLAG_BITS = ("stash_full", "unknown_ref", "bad_reduce", "shares_overflow", "level_overflow",
             "fifo_overflow", "parse_error", "ready")


def frame(raw):
    return len(raw).to_bytes(2, "big") + raw


def config(base, locate, reset=True):
    return frame(b"Z" + bytes([int(reset)]) + base.to_bytes(4, "big") + locate.to_bytes(2, "big"))


def parse_report(line):
    m = LINE.fullmatch(line.strip())
    if not m:
        raise ValueError(f"bad report line {line!r}")
    v = [int(x, 16) for x in m.groups()]
    flags = {name for k, name in enumerate(FLAG_BITS) if v[9] >> k & 1}
    return {"n": v[0], "top": [(v[1], v[2]), (v[3], v[4]), (v[5], v[6]), (v[7], v[8])],
            "errors": flags - {"ready"}, "ready": "ready" in flags, "fifo_peak": v[10],
            "lat_n": v[11], "lat_sum": v[12], "lat_max": v[13], "lat_min": v[14], "sums": (v[15], v[16]),
            "rx_sums": (v[17], v[18])}


class ByteSums:
    """The board's sums over received bytes: s1 += byte, s2 += s1, mod 2^32."""

    def __init__(self):
        self.s1 = self.s2 = 0

    def add(self, data):
        s1, s2 = self.s1, self.s2
        for b in data:
            s1 = (s1 + b) & 0xFFFFFFFF
            s2 = (s2 + s1) & 0xFFFFFFFF
        self.s1, self.s2 = s1, s2


def level_sums(model: HwBook):
    """The board's Fletcher sums over the level RAM: {side, slot} ascending, s1 += size, s2 += s1."""
    s1 = s2 = 0
    for side in "BS":
        for size in model.size[side]:
            s1 = (s1 + size) & 0xFFFFFFFF
            s2 = (s2 + s1) & 0xFFFFFFFF
    return s1, s2


def expected(model: HwBook, n):
    """What the board should report after n book messages."""
    return {"n": n, "top": model.top2("B") + model.top2("S"), "errors": set(model.errors),
            "sums": level_sums(model)}


def mismatch(report, model, n):
    """None if the report agrees with the model, else a description."""
    want = expected(model, n)
    got = {k: report[k] for k in want}
    return None if got == want else f"board {got}\nmodel {want}"
