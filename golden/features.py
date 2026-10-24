"""Features for the model, computed from the order book with integers only.

The Verilog feature stage has to match this module bit for bit
(iteration 8), so every value here is an integer and every operation
is one the FPGA can do cheaply: add, subtract, compare, arithmetic
shift, one lookup table and a few multiplies. No floats, no division.

Features, all signed integers (prices in ITCH units, 4 implied decimals):

    spread            ask - bid
    imbalance         (bid size - ask size) / (bid size + ask size), Q1.15,
                      clamped to +-IMB_MAX. Division done with a
                      normalise-and-lookup reciprocal, see imbalance()
    micro_minus_mid2  2 * (weighted mid - mid) = spread * imbalance >> 15.
                      Weighted mid = (bid * ask size + ask * bid size) /
                      (bid size + ask size), Stoikov's cheap microprice
    ofi_s3/s6/s9      order flow imbalance (Cont, Kukanov and Stoikov 2014),
                      averaged with decay: ofi += e - (ofi >> s)
    trade_flow        signed traded shares, same decay with s = 6.
                      + buyer-initiated, - seller-initiated
    imbalance_lag     imbalance LAG updates ago
    mid2_change       mid2 now - mid2 LAG updates ago

Mid-price is kept doubled (mid2 = bid + ask) so it never needs a
divide by two.

When rows are written. A "valid update" is a message that changes the
book while all of these hold:

    - inside market hours (System Event Q seen, M not yet seen)
    - the stock is trading (last Trading Action state was T)
    - both sides have orders and the book is not locked or crossed

All running state (OFI, trade flow, the lag history) advances only on
valid updates, so the pre-market, halts and broken books never leak
into it. A row is written for every valid update once LAG earlier
valid updates exist to fill the history.
"""

from collections import deque
from dataclasses import dataclass

from .book import OrderBook

FEATURE_NAMES = (
    "spread", "imbalance", "micro_minus_mid2",
    "ofi_s3", "ofi_s6", "ofi_s9",
    "trade_flow", "imbalance_lag", "mid2_change",
)

IMB_FRAC = 15                      # imbalance is Q1.15
IMB_MAX = (1 << IMB_FRAC) - 1
RECIP_BITS = 11                    # totals are normalised to 11 bits
RECIP_SHIFT = IMB_FRAC + RECIP_BITS
# Reciprocal table for normalised totals 1024..2047 (17-bit entries).
# This becomes a block RAM in hardware.
RECIP = {t: (1 << RECIP_SHIFT) // t for t in range(1 << (RECIP_BITS - 1), 1 << RECIP_BITS)}

OFI_SHIFTS = (3, 6, 9)
TRADE_SHIFT = 6
LAG = 10


def imbalance(bid_size, ask_size):
    """(b - a) / (b + a) in Q1.15 without a divider.

    Shift the total so it has exactly RECIP_BITS bits (left for small
    totals, which loses nothing, right for large ones), shift the
    size of the difference by the same amount, then multiply by the
    table's reciprocal. The sign is put back at the end so that
    swapping bid and ask gives exactly the negative result (shifting a
    negative number would round it the other way). In hardware:
    priority encoder, two barrel shifters, one table read, one 12 x 17
    bit multiply.
    """
    total = bid_size + ask_size
    mag = abs(bid_size - ask_size)
    shift = total.bit_length() - RECIP_BITS
    if shift > 0:
        total >>= shift
        mag >>= shift
    else:
        total <<= -shift
        mag <<= -shift
    imb = min(IMB_MAX, (mag * RECIP[total]) >> RECIP_BITS)
    return imb if bid_size >= ask_size else -imb


def ofi_event(prev, new):
    """Order flow imbalance e_n between two tops of book (bid_price, bid_size, ask_price, ask_size).

    Bid side: a higher bid adds its full size, the same bid adds the
    size change, a lower bid removes the old size. The ask side is the
    mirror image with the sign flipped.
    """
    pb, pbs, pa, pas = prev
    nb, nbs, na, nas = new
    e = 0
    if nb >= pb:
        e += nbs
    if nb <= pb:
        e -= pbs
    if na <= pa:
        e -= nas
    if na >= pa:
        e += pas
    return e


@dataclass(frozen=True)
class Row:
    timestamp: int     # nanoseconds since midnight
    mid2: int          # bid + ask, kept for labelling
    features: tuple    # values in FEATURE_NAMES order


class FeatureEngine:
    """Owns an order book; feed it every decoded message for one stock in order."""

    def __init__(self):
        self.book = OrderBook()
        self.market_open = False
        self.trading = True    # Nasdaq sends a Trading Action before the open; assume trading until told otherwise
        self.prev_top = None
        self.ofi = [0] * len(OFI_SHIFTS)
        self.trade_flow = 0
        self.pending_trades = 0
        self.history = deque(maxlen=LAG)   # (imbalance, mid2) of the last LAG valid updates

    def step(self, m):
        """Apply one message. Returns a Row, or None if no row is written for it."""
        t = m["message_type"]
        if t == "S":
            if m["event_code"] == "Q":
                self.market_open = True
            elif m["event_code"] == "M":
                self.market_open = False
        elif t == "H":
            self.trading = m["trading_state"] == "T"
        elif t == "P":
            self._trade(m["buy_sell_indicator"], m["shares"])
        elif t in ("E", "C"):
            # Look the side up before the book applies it: a full fill removes the order.
            o = self.book.orders.get(m["order_reference_number"])
            if o is not None:
                self._trade(o.side, m["executed_shares"])

        if not self.book.apply(m):
            return None
        top = self.book.top()
        bp, bs, ap, as_ = top.bid_price, top.bid_size, top.ask_price, top.ask_size
        if not (self.market_open and self.trading and bs and as_ and bp < ap):
            return None
        return self._valid_update(m.get("timestamp", 0), (bp, bs, ap, as_))

    def _trade(self, resting_side, shares):
        # The resting order is the passive side, so a resting buy means a seller hit it.
        if self.market_open and self.trading:
            self.pending_trades += shares if resting_side == "S" else -shares

    def _valid_update(self, timestamp, top):
        bp, bs, ap, as_ = top
        e = ofi_event(self.prev_top, top) if self.prev_top is not None else 0
        self.prev_top = top
        self.ofi = [o + e - (o >> s) for o, s in zip(self.ofi, OFI_SHIFTS)]
        self.trade_flow += self.pending_trades - (self.trade_flow >> TRADE_SHIFT)
        self.pending_trades = 0

        spread = ap - bp
        imb = imbalance(bs, as_)
        mid2 = bp + ap
        full = len(self.history) == LAG
        lag_imb, lag_mid2 = self.history[0] if full else (0, 0)
        self.history.append((imb, mid2))
        if not full:
            return None

        features = (
            spread, imb, (spread * imb) >> IMB_FRAC,
            *self.ofi,
            self.trade_flow, lag_imb, mid2 - lag_mid2,
        )
        return Row(timestamp, mid2, features)
