"""Check the hardware book algorithm against the golden book on whole days.

    python scripts/hw_book_check.py SYMBOL FILE [FILE ...] [--offsets 0,1500,-1500]

Replays each day through golden/book.py and golden/hw_book.py side by
side and compares the top 2 levels of both sides after every book
message. The window is centered on the 9:30 mid plus each offset in
cents, because in real use the center has to be set before the open.

    checked     comparisons where the true top 2 is in the window
    wrong       of those, how many differ (must be 0, all day)
    outside     comparisons where the true top 2 is not in the window,
                split into regular hours and the rest of the day
    stash       most orders in the stash at once
    level peak  largest level in the window, in shares
    end         slots that differ between the two books at the end of the day
    queue       most messages waiting and worst delay in clocks, with
                messages arriving back to back at one byte per clock.
                Each message takes 20 clocks (U takes 40), plus `extra`
                clocks if the level update can't overlap the next
                bucket read, and refresh takes 7 clocks every 780.
"""

import argparse
from collections import deque
from multiprocessing import Pool

from book_stats import Sides, opening_mid, touched
from golden.book import OrderBook
from golden.hw_book import SLOTS, TICK, HwBook, price_slot
from golden.itch.parser import decode
from golden.itch.tools import filter_stock

BOOK_TYPES = "AFECXDU"
RMW, REF_EVERY, REF_COST = 20, 780, 7


class Queue:
    """Message timing through the input buffer."""

    def __init__(self, extra):
        self.extra, self.now, self.free, self.next_ref = extra, 0, 0, REF_EVERY
        self.waiting, self.depth, self.delay = deque(), 0, 0

    def message(self, length, t):
        self.now += length + 2
        if t not in BOOK_TYPES:
            return
        start = max(self.now, self.free)
        end = start + (2 * RMW if t == "U" else RMW) + self.extra
        while end >= self.next_ref:
            end += REF_COST
            self.next_ref += REF_EVERY
        self.free = end
        self.delay = max(self.delay, end - self.now)
        while self.waiting and self.waiting[0] <= self.now:
            self.waiting.popleft()
        self.waiting.append(end)
        self.depth = max(self.depth, len(self.waiting))


def golden_top2(gold, sides, side):
    out = [(abs(k), gold.levels[side][abs(k)]) for k in sides.keys[side][:2]]
    return out + [(0, 0)] * (2 - len(out))


def run(job):
    path, symbol, center, offset = job
    gold, hw, sides = OrderBook(), HwBook(center + offset * TICK), Sides()
    queues = [Queue(0), Queue(4)]
    open_now, checked, wrong, first = False, 0, 0, None
    outside = {False: 0, True: 0}
    for raw in filter_stock(path, symbol):
        m = decode(raw)
        t = m["message_type"]
        for q in queues:
            q.message(len(raw), t)
        if t == "S":
            open_now = {"Q": True, "M": False}.get(m["event_code"], open_now)
        if t not in BOOK_TYPES:
            continue
        side, price = touched(gold, m)
        gold.apply(m)
        hw.apply(m)
        sides.sync(side, price, price in gold.levels[side])
        if t == "U":
            sides.sync(side, m["price"], True)
        for sd in "BS":
            want = golden_top2(gold, sides, sd)
            if any(p and price_slot(p, hw.base) is None for p, _ in want):
                outside[open_now] += 1
            else:
                checked += 1
                if hw.top2(sd) != want:
                    wrong += 1
                    first = first or (sd, m["timestamp"], want, hw.top2(sd))
    end = sum(hw.size[sd][s] != gold.levels[sd].get(hw.base + s * TICK, 0) for sd in "BS" for s in range(SLOTS))
    return {"path": path, "center": center, "offset": offset, "checked": checked, "wrong": wrong, "first": first,
            "outside_open": outside[True], "outside_other": outside[False], "stash": hw.stash_peak,
            "errors": sorted(hw.errors), "scans": hw.scans, "end": end, "size_peak": hw.size_peak,
            "queue": [(q.depth, q.delay) for q in queues]}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python scripts/hw_book_check.py")
    p.add_argument("symbol")
    p.add_argument("files", nargs="+")
    p.add_argument("--offsets", default="0", help="window center offsets from the 9:30 mid, in cents")
    a = p.parse_args(argv)
    offsets = [int(x) for x in a.offsets.split(",")]
    jobs = [(f, a.symbol, opening_mid(f, a.symbol), o) for f in a.files for o in offsets]
    with Pool(min(len(jobs), 6)) as pool:
        results = pool.map(run, jobs)
    for r in results:
        print(f"{r['path']} center ${r['center'] / 1e4:.2f} {r['offset']:+d}c: checked {r['checked']:,} "
              f"wrong {r['wrong']}  outside open {r['outside_open']:,} other {r['outside_other']:,}  "
              f"stash {r['stash']}  scans {r['scans']:,}  end {r['end']}  level peak {r['size_peak']:,}  "
              f"errors {r['errors']}")
        print(f"    queue (depth, delay): overlap {r['queue'][0]}  extra 4 clocks {r['queue'][1]}")
        if r["first"]:
            print(f"    first wrong: {r['first']}")


if __name__ == "__main__":
    main()
