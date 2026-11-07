"""Size the hardware order book from real data.

    python scripts/book_stats.py FILE SYMBOL [FILE ...]

Replays each day through the golden order book and measures what the
Verilog version has to hold and how far it has to search:

    orders     most live orders at once, and how evenly they spread over
               a table indexed by the low bits of the order reference
    levels     most live price levels per side, and the span from the
               best price to the worst in cents
    touched    how deep (rank from the best price) each update lands
    gaps       cents between consecutive levels near the top, which is
               how far a scan walks to find the next level when one empties
    widths     largest shares, level size and price, in bits
    window     live orders priced within WINDOW cents of the 9:30 mid, the
               levels a fixed-window level array would hold, and the
               sub-penny orders and replaces that cross into it

The whole day is counted, pre-market included, because the hardware
sees all of it.
"""

import argparse
import heapq
from bisect import bisect_left, insort
from collections import Counter

import numpy as np

from golden.book import OrderBook
from golden.itch.parser import decode
from golden.itch.tools import filter_stock

TABLE_BITS = range(10, 19)
WAYS = 4                          # entries per hash bucket read in one burst
WINDOW = 2048                     # level-array half-width in cents
TICK = 100                        # one cent in ITCH price units
OPEN, CLOSE = 34_200 * 10**9, 57_600 * 10**9     # regular hours, ns after midnight


class Sides:
    """Sorted price lists per side, best first: bids descending (stored negated), asks ascending."""

    def __init__(self):
        self.keys = {"B": [], "S": []}

    @staticmethod
    def key(side, price):
        return -price if side == "B" else price

    def rank(self, side, price):
        return bisect_left(self.keys[side], self.key(side, price))

    def sync(self, side, price, live):
        ks, k = self.keys[side], self.key(side, price)
        i = bisect_left(ks, k)
        present = i < len(ks) and ks[i] == k
        if live and not present:
            insort(ks, k)
        elif present and not live:
            del ks[i]


def touched(book, m):
    """(side, price) of the level a book message changes, read before it is applied; U gives the old one."""
    t = m["message_type"]
    if t in ("A", "F"):
        return m["buy_sell_indicator"], m["price"]
    ref = m["original_order_reference_number"] if t == "U" else m["order_reference_number"]
    o = book.orders[ref]
    return o.side, o.price


def opening_mid(path, symbol):
    """Mid price of the first two-sided book at or after 9:30."""
    book = OrderBook()
    for raw in filter_stock(path, symbol):
        m = decode(raw)
        if m["message_type"] not in "AFECXDU":
            continue
        if m["timestamp"] >= OPEN and book.levels["B"] and book.levels["S"]:
            break
        book.apply(m)
    return (book.best_bid()[0] + book.best_ask()[0]) // 2


def measure(path, symbol):
    anchor = opening_mid(path, symbol)
    inside = lambda price: price % TICK == 0 and abs(price - anchor) <= WINDOW * TICK
    book, sides = OrderBook(), Sides()
    s = {"types": Counter(), "ranks": [], "gaps": [], "spans": {"B": [], "S": []}, "max_orders": 0,
         "max_side_orders": Counter(), "max_levels": Counter(), "subpenny": 0, "max_shares": 0,
         "max_level": 0, "anchor": anchor, "top_range": {"B": (1 << 32, 0), "S": (1 << 32, 0)},
         "max_price": 0, "max_ref_window": 0, "refs": 0, "min_len": 1 << 30, "overflow": Counter(), "max_excess": Counter(),
         "win_orders": 0, "max_win_orders": 0, "subpenny_near": 0, "u_into_window": 0}
    buckets = {b: Counter() for b in TABLE_BITS}
    worst_bucket, excess = Counter(), Counter()   # excess: live orders beyond WAYS in their bucket
    side_orders = Counter()
    live_refs = []                                     # min-heap with lazy deletion
    newest = 0                                         # refs only grow, so this bounds the newest live one

    def track(ref, side, delta):
        side_orders[side] += delta
        for b in TABLE_BITS:
            h = ref & ((1 << b) - 1)
            over = buckets[b][h] > WAYS if delta < 0 else buckets[b][h] >= WAYS
            buckets[b][h] += delta
            worst_bucket[b] = max(worst_bucket[b], buckets[b][h])
            s["overflow"][b] += delta > 0 and over
            excess[b] += delta * over
            s["max_excess"][b] = max(s["max_excess"][b], excess[b])

    for raw in filter_stock(path, symbol):
        m = decode(raw)
        t = m["message_type"]
        if t not in "AFECXDU":
            continue
        s["types"][t] += 1
        s["min_len"] = min(s["min_len"], len(raw))
        side, price = touched(book, m)
        s["ranks"].append(sides.rank(side, price))

        old = None if t in ("A", "F") else (m["original_order_reference_number"] if t == "U"
                                             else m["order_reference_number"])
        book.apply(m)
        if old is not None and old not in book.orders:
            track(old, side, -1)
            s["win_orders"] -= inside(price)
        if t in ("A", "F", "U"):
            ref = m["new_order_reference_number"] if t == "U" else m["order_reference_number"]
            o = book.orders[ref]
            track(ref, o.side, +1)
            heapq.heappush(live_refs, ref)
            newest = max(newest, ref)
            s["refs"] += 1
            s["subpenny"] += o.price % TICK != 0
            s["subpenny_near"] += o.price % TICK != 0 and abs(o.price - anchor) <= WINDOW * TICK
            s["win_orders"] += inside(o.price)
            s["u_into_window"] += t == "U" and inside(o.price) and not inside(price)
            s["max_shares"] = max(s["max_shares"], o.shares)
            s["max_price"] = max(s["max_price"], o.price)
            sides.sync(o.side, o.price, True)
        sides.sync(side, price, price in book.levels[side])

        while live_refs and live_refs[0] not in book.orders:
            heapq.heappop(live_refs)
        if live_refs:
            s["max_ref_window"] = max(s["max_ref_window"], newest - live_refs[0])
        s["max_orders"] = max(s["max_orders"], len(book.orders))
        s["max_win_orders"] = max(s["max_win_orders"], s["win_orders"])
        for sd in "BS":
            ks = sides.keys[sd]
            s["max_side_orders"][sd] = max(s["max_side_orders"][sd], side_orders[sd])
            s["max_levels"][sd] = max(s["max_levels"][sd], len(ks))
            if ks:
                s["spans"][sd].append((ks[-1] - ks[0]) // TICK)
                s["max_level"] = max(s["max_level"], book.levels[sd][abs(ks[0])])
            s["gaps"] += [(ks[i + 1] - ks[i]) // TICK for i in range(min(2, len(ks) - 1))]
            for k in ks[:2] if OPEN <= m["timestamp"] < CLOSE else []:
                s["top_range"][sd] = (min(s["top_range"][sd][0], abs(k)), max(s["top_range"][sd][1], abs(k)))
    s["worst_bucket"] = worst_bucket
    return s


def pct(a, qs=(50, 99, 99.9, 99.99, 100)):
    a = np.asarray(a)
    return "  ".join(f"p{q}={int(np.percentile(a, q))}" for q in qs)


def report(path, s):
    print(f"\n=== {path}")
    print("book messages   " + "  ".join(f"{t}={n:,}" for t, n in sorted(s["types"].items())))
    print(f"shortest message {s['min_len']} bytes -> {s['min_len'] + 2} clocks between book updates at 1 byte/clock")
    print(f"orders          added {s['refs']:,}   max live {s['max_orders']:,}   "
          f"max live bid {s['max_side_orders']['B']:,}  ask {s['max_side_orders']['S']:,}")
    print(f"ref window      newest live ref - oldest live ref, max {s['max_ref_window']:,}")
    print("hash table      worst bucket (live orders sharing low ref bits):  "
          + "  ".join(f"2^{b}:{s['worst_bucket'][b]}" for b in TABLE_BITS))
    print(f"                adds landing in a bucket already holding {WAYS}:  "
          + "  ".join(f"2^{b}:{s['overflow'][b]:,}" for b in TABLE_BITS))
    print(f"                most overflowed orders live at once (stash size):  "
          + "  ".join(f"2^{b}:{s['max_excess'][b]:,}" for b in TABLE_BITS))
    print(f"levels          max live bid {s['max_levels']['B']}  ask {s['max_levels']['S']}   "
          f"sub-penny orders {s['subpenny']:,}")
    for sd in "BS":
        print(f"span {sd}, cents   best to worst live level:  {pct(s['spans'][sd])}")
    print(f"touched rank    0 = best level:  {pct(s['ranks'])}")
    r = np.asarray(s["ranks"])
    print("                share of updates at rank <= N:  "
          + "  ".join(f"N={n}:{(r <= n).mean():.4%}" for n in (1, 3, 7, 15, 31)))
    c = s["anchor"]
    for sd in "BS":
        lo, hi = s["top_range"][sd]
        print(f"top-2 range {sd}   regular hours ${lo / 1e4:,.2f} to ${hi / 1e4:,.2f}, "
              f"{(c - lo) // TICK:,} below to {(hi - c) // TICK:,} cents above the 9:30 mid ${c / 1e4:,.2f}")
    print(f"window +-{WINDOW} cents   max live orders inside {s['max_win_orders']:,}   "
          f"sub-penny orders inside {s['subpenny_near']}   replaces moving in from outside {s['u_into_window']}")
    print(f"top gaps, cents level 1->2 and 2->3:  {pct(s['gaps'])}")
    print(f"widths          shares {s['max_shares'].bit_length()} bits ({s['max_shares']:,})   "
          f"best level size {s['max_level'].bit_length()} bits ({s['max_level']:,})   "
          f"price {s['max_price'].bit_length()} bits ({s['max_price']:,})")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python scripts/book_stats.py")
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("more", nargs="*", help="more ITCH files for the same symbol")
    a = p.parse_args(argv)
    for path in [a.file, *a.more]:
        report(path, measure(path, a.symbol))


if __name__ == "__main__":
    main()
