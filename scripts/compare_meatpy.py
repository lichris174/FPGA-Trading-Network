"""Cross-check the golden order book against MeatPy, message by message.

    pip install meatpy
    python scripts/compare_meatpy.py FILE SYMBOL [--max-mismatches N]

FILE can be a full day (gzipped is fine) or the output of
`python -m golden.itch.tools filter`. A filtered file is much faster,
since only one stock's messages are decoded twice.

Each raw message is fed to both books: the golden model through
golden.itch.parser.decode, MeatPy through its own ITCH 5.0 decoder.
After every message the best bid and best ask (price and size) are
compared. Prices are compared as ITCH integers, so no rounding.

MeatPy is only a cross-check, not a dependency of the golden model,
which is why this lives in scripts/ and not in golden/.
"""

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meatpy.itch50 import ITCH50MarketProcessor
from meatpy.itch50.itch50_market_message import ITCH50MarketMessage

from golden.book import OrderBook
from golden.itch.tools import filter_stock
from golden.itch.parser import decode


class Processor(ITCH50MarketProcessor):
    """MeatPy processor that counts time-priority warnings instead of raising.

    MeatPy expects every execution to hit the order at the front of its
    price level, and raises when one does not. Real feeds break that
    (the opening cross, for example). MeatPy still applies the execution
    to the right order before raising, so the book is correct and the
    warning only stops the run. ITCH execution messages name the order
    they hit, and the golden model trusts that, so these are not
    mismatches.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.priority_warnings = 0

    def create_lob(self, timestamp):
        super().create_lob(timestamp)
        self.current_lob.skip_exception = self._skip

    def _skip(self, e):
        self.priority_warnings += 1
        return True


def meatpy_top(proc):
    """(bid_price, bid_size, ask_price, ask_size) from MeatPy, 0s when a side is empty."""
    lob = proc.current_lob
    if lob is None:
        return 0, 0, 0, 0
    bid = lob.bid_levels[0] if lob.bid_levels else None
    ask = lob.ask_levels[0] if lob.ask_levels else None
    return (
        bid.price if bid else 0, bid.volume if bid else 0,
        ask.price if ask else 0, ask.volume if ask else 0,
    )


def fmt(top):
    bp, bs, ap, as_ = top
    return f"bid {bp / 1e4:.4f} x {bs}  ask {ap / 1e4:.4f} x {as_}"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("--max-mismatches", type=int, default=10)
    a = p.parse_args(argv)

    golden = OrderBook()
    # MeatPy only uses the date to build timestamps; any date works.
    proc = Processor(a.symbol, datetime.datetime(2000, 1, 1))

    n = checked = mismatches = 0
    for i, raw in enumerate(filter_stock(a.file, a.symbol)):
        n += 1
        changed = golden.apply(decode(raw))
        proc.process_message(ITCH50MarketMessage.from_bytes(raw))
        if not changed:
            continue
        checked += 1
        t = golden.top()
        ours = (t.bid_price, t.bid_size, t.ask_price, t.ask_size)
        theirs = meatpy_top(proc)
        if ours != theirs:
            mismatches += 1
            print(f"message {i} type {chr(raw[0])}")
            print(f"  golden {fmt(ours)}")
            print(f"  meatpy {fmt(theirs)}")
            if mismatches >= a.max_mismatches:
                sys.exit(f"stopped after {mismatches} mismatches")

    print(f"{n:,} messages, {checked:,} book updates compared, {mismatches} mismatches")
    print(f"{proc.priority_warnings:,} MeatPy time-priority warnings skipped (executions not at front of queue)")
    print(f"final  {fmt(meatpy_top(proc))}")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
