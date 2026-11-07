"""Model of the hardware order book: the same algorithm the Verilog will run.

golden/book.py says what the book must contain. This file says how the
FPGA gets there with limited memory, so the Verilog has a reference for
every table, slot and flag, and the design can be checked on whole days
of data before any of it is written in Verilog.

Orders live in SDRAM, in a hash table on the low 18 bits of the order
reference: 2^18 buckets of 4 64-bit entries. Way w of a bucket is SDRAM
words 2w (entry bits [31:0]) and 2w + 1 (bits [63:32]).

    [63:31] tag      reference bits [50:18]
    [30]    side     1 = sell
    [29]    inside   the price is in the level window
    [28:17] slot     level slot, valid when inside
    [16:0]  shares   0 = empty entry

An order that finds its bucket full goes in a 16-entry register stash,
which is searched along with the bucket and matches on reference bits
[50:0]. Entries stay where they are
until the order goes; a free way never pulls an order out of the stash.

Levels live in block RAM: one slot per cent, 4096 slots per side around
a center price set before the day. An occupancy bitmap (64 words of 64
bits plus a summary word) finds the next level when one empties. Orders
outside the window, and sub-penny orders, are in the table but not in
the levels, so top2() is exact whenever the true top 2 is in the window.

Anything the hardware can't represent sets a sticky flag in `errors`
and carries on, as the Verilog will.
"""

BUCKET_BITS = 18
BUCKETS = 1 << BUCKET_BITS
WAYS = 4
STASH_SIZE = 16
TAG_BITS = 33
SHARE_BITS = 17
SIZE_BITS = 20           # largest level seen in the window: 448,483 shares
SLOTS = 4096
TICK = 100                # one cent in ITCH price units
WORD_BITS = 64

# floor(d / 100) for the d in the window, without a divider: d * 5243 / 2^19.
DIV_MUL, DIV_SHIFT = 5243, 19


def window_base(center):
    """Price of slot 0 for a window centered on `center`, aligned to a cent."""
    return center // TICK * TICK - SLOTS // 2 * TICK


def price_slot(price, base):
    """Level slot of a price, or None if it is outside the window or sub-penny."""
    d = price - base
    if d < 0 or d >= SLOTS * TICK:
        return None
    q = (d * DIV_MUL) >> DIV_SHIFT
    return q if q * TICK == d else None


def pack_entry(ref, side, inside, slot, shares, bucket_bits=BUCKET_BITS):
    tag = (ref >> bucket_bits) & ((1 << TAG_BITS) - 1)
    return tag << 31 | (side == "S") << 30 | inside << 29 | slot << 17 | shares


def unpack_entry(e):
    return {"tag": e >> 31, "side": "S" if (e >> 30) & 1 else "B", "inside": bool((e >> 29) & 1),
            "slot": (e >> 17) & (SLOTS - 1), "shares": e & ((1 << SHARE_BITS) - 1)}


def _msb(x):
    return x.bit_length() - 1


def _lsb(x):
    return (x & -x).bit_length() - 1


def next_worse(words, summary, side, slot):
    """First occupied slot worse than `slot`, or None. Bids get worse going down, asks going up."""
    w, b = slot // WORD_BITS, slot % WORD_BITS
    if side == "B":
        here = words[w] & ((1 << b) - 1)
        if here:
            return w * WORD_BITS + _msb(here)
        rest = summary & ((1 << w) - 1)
        return None if not rest else _msb(rest) * WORD_BITS + _msb(words[_msb(rest)])
    here = words[w] >> (b + 1)
    if here:
        return slot + 1 + _lsb(here)
    rest = summary >> (w + 1)
    if not rest:
        return None
    w2 = w + 1 + _lsb(rest)
    return w2 * WORD_BITS + _lsb(words[w2])


class HwBook:
    def __init__(self, center, bucket_bits=BUCKET_BITS):
        """bucket_bits is smaller only in simulation, where clearing 2^18 buckets takes too long."""
        self.base = window_base(center)
        self.bucket_bits = bucket_bits
        self.table = {}                                 # bucket -> list of WAYS entries; SDRAM starts cleared
        self.stash = []                                 # (reference, entry), at most STASH_SIZE
        self.stash_peak = 0
        self.size = {"B": [0] * SLOTS, "S": [0] * SLOTS}
        self.words = {"B": [0] * (SLOTS // WORD_BITS), "S": [0] * (SLOTS // WORD_BITS)}
        self.summary = {"B": 0, "S": 0}
        self.best = {"B": None, "S": None}
        self.second = {"B": None, "S": None}
        self.errors = set()
        self.scans = 0
        self.size_peak = 0

    @property
    def stash_used(self):
        return len(self.stash)

    # --- message handling ----------------------------------------------

    def apply(self, m):
        """Apply one decoded message. Non-book messages are ignored. Returns True if it was a book message."""
        t = m["message_type"]
        if t in ("A", "F"):
            self._add(m["order_reference_number"], m["buy_sell_indicator"], m["price"], m["shares"])
        elif t in ("E", "C"):
            self._reduce(m["order_reference_number"], m["executed_shares"])
        elif t == "X":
            self._reduce(m["order_reference_number"], m["cancelled_shares"])
        elif t == "D":
            self._reduce(m["order_reference_number"], None)
        elif t == "U":
            side = self._reduce(m["original_order_reference_number"], None)
            if side is not None:
                self._add(m["new_order_reference_number"], side, m["price"], m["shares"])
        else:
            return False
        return True

    def _add(self, ref, side, price, shares):
        if shares >= 1 << SHARE_BITS:
            self.errors.add("shares_overflow")
            shares &= (1 << SHARE_BITS) - 1
        slot = price_slot(price, self.base)
        inside = slot is not None
        entry = pack_entry(ref, side, inside, slot if inside else 0, shares, self.bucket_bits)
        bucket = self.table.setdefault(ref & ((1 << self.bucket_bits) - 1), [0] * WAYS)
        for way, e in enumerate(bucket):
            if e & ((1 << SHARE_BITS) - 1) == 0:
                bucket[way] = entry
                break
        else:
            if len(self.stash) == STASH_SIZE:
                self.errors.add("stash_full")
                return
            self.stash.append((ref, entry))
            self.stash_peak = max(self.stash_peak, len(self.stash))
        if inside:
            self._level(side, slot, shares)

    def _reduce(self, ref, n):
        """Take n shares off an order (None = all of it). Returns its side, or None if it isn't there."""
        tag = (ref >> self.bucket_bits) & ((1 << TAG_BITS) - 1)
        bucket = self.table.get(ref & ((1 << self.bucket_bits) - 1), ())
        where = next((w for w, e in enumerate(bucket) if e & ((1 << SHARE_BITS) - 1) and e >> 31 == tag), None)
        pos = None if where is not None else next((i for i, (r, _) in enumerate(self.stash) if r == ref), None)
        if where is None and pos is None:
            self.errors.add("unknown_ref")
            return None
        entry = bucket[where] if where is not None else self.stash[pos][1]
        e = unpack_entry(entry)
        n = e["shares"] if n is None else n
        if n > e["shares"]:
            self.errors.add("bad_reduce")
            n = e["shares"]
        left = entry - n                                # shares are the low bits
        if where is not None:
            bucket[where] = left
        elif e["shares"] == n:
            del self.stash[pos]
        else:
            self.stash[pos] = (ref, left)
        if e["inside"]:
            self._level(e["side"], e["slot"], -n)
        return e["side"]

    # --- levels and the top 2 ------------------------------------------

    def _level(self, side, slot, delta):
        old = self.size[side][slot]
        raw = old + delta
        self.size_peak = max(self.size_peak, raw)
        if not 0 <= raw < 1 << SIZE_BITS:
            self.errors.add("level_overflow")
        new = raw & ((1 << SIZE_BITS) - 1)              # the RAM word wraps
        self.size[side][slot] = new
        if old == 0 and new > 0:
            self._occupy(side, slot)
        elif old > 0 and new == 0:
            self._vacate(side, slot)

    def _better(self, side, a, b):
        return a > b if side == "B" else a < b

    def _occupy(self, side, slot):
        w = slot // WORD_BITS
        self.words[side][w] |= 1 << (slot % WORD_BITS)
        self.summary[side] |= 1 << w
        best, second = self.best[side], self.second[side]
        if best is None or self._better(side, slot, best):
            self.best[side], self.second[side] = slot, best
        elif second is None or self._better(side, slot, second):
            self.second[side] = slot

    def _vacate(self, side, slot):
        w = slot // WORD_BITS
        self.words[side][w] &= ~(1 << (slot % WORD_BITS))
        if self.words[side][w] == 0:
            self.summary[side] &= ~(1 << w)
        if slot == self.best[side]:
            self.best[side] = self.second[side]
        elif slot != self.second[side]:
            return
        self.second[side] = None if self.best[side] is None else self._scan(side, self.best[side])

    def _scan(self, side, slot):
        self.scans += 1
        return next_worse(self.words[side], self.summary[side], side, slot)

    def top2(self, side):
        """[(price, size), (price, size)], best first; (0, 0) where there is no level."""
        return [(0, 0) if s is None else (self.base + s * TICK, self.size[side][s])
                for s in (self.best[side], self.second[side])]
