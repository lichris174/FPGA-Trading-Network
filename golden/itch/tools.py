"""Command-line tools for ITCH files.

    python -m golden.itch.tools stats  FILE
        Count messages by type and check every one decodes.

    python -m golden.itch.tools filter FILE SYMBOL OUT
        Keep only the messages for one stock (plus system events), and
        write them to OUT in the same length-prefixed format.

    python -m golden.itch.tools replay FILE SYMBOL
        Filter to one stock and run it through the order book, printing
        the final top of book. Any book error stops with the message index.

Messages for one stock are found by stock locate. The Stock Directory
(R) message maps each symbol to its locate code for that day. Order
messages E, C, X, D and U carry no symbol, only the locate, so this
is the only reliable way to filter.
"""

import argparse
import sys
from collections import Counter

from ..book import OrderBook
from .parser import decode, open_itch, read_frames, write_frames


def stats(path):
    counts = Counter()
    with open_itch(path) as f:
        for msg in read_frames(f):
            decode(msg)  # raises on any malformed message
            counts[chr(msg[0])] += 1
    return counts


def filter_stock(path, symbol):
    """Yield raw messages for one stock: its own messages plus system events."""
    locate = None
    with open_itch(path) as f:
        for msg in read_frames(f):
            t = chr(msg[0])
            if t == "S":
                yield msg
                continue
            if t == "R" and locate is None:
                d = decode(msg)
                if d["stock"] == symbol:
                    locate = d["stock_locate"]
            if locate is not None and int.from_bytes(msg[1:3], "big") == locate:
                yield msg
    if locate is None:
        raise SystemExit(f"symbol {symbol!r} not found in stock directory")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m golden.itch.tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stats"); s.add_argument("file")
    s = sub.add_parser("filter"); s.add_argument("file"); s.add_argument("symbol"); s.add_argument("out")
    s = sub.add_parser("replay"); s.add_argument("file"); s.add_argument("symbol")
    a = p.parse_args(argv)

    if a.cmd == "stats":
        counts = stats(a.file)
        for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{t}  {n:>12,}")
        print(f"total {sum(counts.values()):>10,}")
    elif a.cmd == "filter":
        n = 0
        with open(a.out, "wb") as out:
            for msg in filter_stock(a.file, a.symbol):
                write_frames(out, [msg]); n += 1
        print(f"wrote {n:,} messages to {a.out}")
    elif a.cmd == "replay":
        book = OrderBook()
        for i, msg in enumerate(filter_stock(a.file, a.symbol)):
            try:
                book.apply(decode(msg))
            except Exception as e:
                sys.exit(f"message {i}: {e}")
        t = book.top()
        print(f"live orders {len(book.orders):,}")
        print(f"best bid {t.bid_price / 1e4:.4f} x {t.bid_size}   best ask {t.ask_price / 1e4:.4f} x {t.ask_size}")


if __name__ == "__main__":
    main()
