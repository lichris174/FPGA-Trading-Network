"""End-to-end test of the filter and replay tools on a synthetic file."""

from golden.book import OrderBook
from golden.itch.parser import decode, encode, write_frames
from golden.itch.tools import filter_stock, stats


def test_filter_by_locate_and_replay(tmp_path):
    msgs = [
        encode({"message_type": "S", "event_code": "O"}),
        encode({"message_type": "R", "stock_locate": 1, "stock": "AAPL"}),
        encode({"message_type": "R", "stock_locate": 2, "stock": "MSFT"}),
        encode({"message_type": "A", "stock_locate": 1, "order_reference_number": 10,
                "buy_sell_indicator": "B", "shares": 100, "stock": "AAPL", "price": 1500000}),
        encode({"message_type": "A", "stock_locate": 2, "order_reference_number": 11,
                "buy_sell_indicator": "S", "shares": 5, "stock": "MSFT", "price": 4000000}),
        # No symbol on X: only the locate says which stock it belongs to.
        encode({"message_type": "X", "stock_locate": 1, "order_reference_number": 10,
                "cancelled_shares": 40}),
    ]
    path = tmp_path / "day.itch"
    with open(path, "wb") as f:
        write_frames(f, msgs)

    assert stats(path) == {"S": 1, "R": 2, "A": 2, "X": 1}
    kept = list(filter_stock(path, "AAPL"))
    assert [chr(m[0]) for m in kept] == ["S", "R", "A", "X"]

    book = OrderBook()
    for m in kept:
        book.apply(decode(m))
    assert book.best_bid() == (1500000, 60)
    assert book.best_ask() == (0, 0)
