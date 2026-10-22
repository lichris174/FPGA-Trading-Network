"""Order book for one stock, built from decoded ITCH messages.

Tracks every live order (so executions, cancels and deletes can find
the order they refer to) and the total shares at each price level.
Best bid and best ask come from the levels, so when the top level
empties the next one takes over.

How each book message changes state (ITCH 5.0 section 4.3 and 4.4):

    A, F  add order with its side, shares and price
    E     executed: reduce shares; remove the order at zero
    C     executed with price: same as E. The execution price can differ
          from the order's price, but the book changes at the order's price
    X     partial cancel: reduce shares; remove the order at zero
    D     delete: remove the whole order
    U     replace: remove the original order, add a new one with a new
          reference number, the same side, new shares and new price

Prices are kept as ITCH integers (4 implied decimals). The book is
strict: an unknown order reference, a duplicate reference or reducing
more shares than an order holds raises BookError. If the input is
correctly filtered and starts at the beginning of the day, none of
those can happen, so any error means a bug upstream.
"""

from dataclasses import dataclass


class BookError(ValueError):
    pass


@dataclass
class Order:
    side: str    # "B" or "S"
    price: int
    shares: int


@dataclass(frozen=True)
class TopOfBook:
    bid_price: int   # 0 when there are no bids
    bid_size: int
    ask_price: int   # 0 when there are no asks
    ask_size: int


class OrderBook:
    def __init__(self):
        self.orders = {}
        self.levels = {"B": {}, "S": {}}   # side -> {price: total shares}

    # --- updates -------------------------------------------------------

    def apply(self, m):
        """Apply one decoded message. Non-book messages are ignored. Returns True if the book changed."""
        t = m["message_type"]
        if t in ("A", "F"):
            self._add(m["order_reference_number"], m["buy_sell_indicator"], m["price"], m["shares"])
        elif t in ("E", "C"):
            self._reduce(m["order_reference_number"], m["executed_shares"])
        elif t == "X":
            self._reduce(m["order_reference_number"], m["cancelled_shares"])
        elif t == "D":
            o = self._get(m["order_reference_number"])
            self._reduce(m["order_reference_number"], o.shares)
        elif t == "U":
            old = self._get(m["original_order_reference_number"])
            side = old.side
            self._reduce(m["original_order_reference_number"], old.shares)
            self._add(m["new_order_reference_number"], side, m["price"], m["shares"])
        else:
            return False
        return True

    def _get(self, ref):
        o = self.orders.get(ref)
        if o is None:
            raise BookError(f"unknown order reference {ref}")
        return o

    def _add(self, ref, side, price, shares):
        if side not in ("B", "S"):
            raise BookError(f"order {ref}: bad side {side!r}")
        if ref in self.orders:
            raise BookError(f"duplicate order reference {ref}")
        if shares <= 0:
            raise BookError(f"order {ref}: non-positive shares {shares}")
        self.orders[ref] = Order(side, price, shares)
        lv = self.levels[side]
        lv[price] = lv.get(price, 0) + shares

    def _reduce(self, ref, shares):
        o = self._get(ref)
        if shares <= 0 or shares > o.shares:
            raise BookError(f"order {ref}: reduce by {shares}, order has {o.shares}")
        o.shares -= shares
        lv = self.levels[o.side]
        lv[o.price] -= shares
        if lv[o.price] == 0:
            del lv[o.price]
        if o.shares == 0:
            del self.orders[ref]

    # --- queries -------------------------------------------------------

    def best_bid(self):
        lv = self.levels["B"]
        if not lv:
            return 0, 0
        p = max(lv)
        return p, lv[p]

    def best_ask(self):
        lv = self.levels["S"]
        if not lv:
            return 0, 0
        p = min(lv)
        return p, lv[p]

    def top(self):
        bp, bs = self.best_bid()
        ap, as_ = self.best_ask()
        return TopOfBook(bp, bs, ap, as_)
