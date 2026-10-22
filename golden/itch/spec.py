"""Nasdaq TotalView-ITCH 5.0 message layouts.

This is the one place byte offsets are written down. The Python parser
uses it directly, and later a script will generate the Verilog header
from it (see docs/decisions.md).

Source: Nasdaq TotalView-ITCH 5.0 specification, section 4.
Every message starts with the same 11-byte header:

    offset 0   1 byte   message type
    offset 1   2 bytes  stock locate
    offset 3   2 bytes  tracking number
    offset 5   6 bytes  timestamp, nanoseconds since midnight

All integers are big-endian and unsigned. Prices are integers:
Price(4) has 4 implied decimals, Price(8) has 8. Alpha fields are
ASCII, left-justified and padded with spaces.

The checks at the bottom run on import, so a typo in an offset fails
loudly instead of producing quietly wrong fields.
"""

from dataclasses import dataclass

# Field kinds
UINT = "uint"     # unsigned big-endian integer (includes prices and timestamps)
ALPHA = "alpha"   # ASCII text, space padded


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    size: int
    kind: str


@dataclass(frozen=True)
class MessageType:
    code: str
    name: str
    length: int
    fields: tuple


HEADER = (
    Field("message_type", 0, 1, ALPHA),
    Field("stock_locate", 1, 2, UINT),
    Field("tracking_number", 3, 2, UINT),
    Field("timestamp", 5, 6, UINT),
)


def _msg(code, name, length, *body):
    """Build a message layout from (name, size, kind) tuples placed after the header."""
    fields = list(HEADER)
    offset = 11
    for fname, size, kind in body:
        fields.append(Field(fname, offset, size, kind))
        offset += size
    return MessageType(code, name, length, tuple(fields))


MESSAGES = {m.code: m for m in (
    # 4.1 System Event
    _msg("S", "system_event", 12,
         ("event_code", 1, ALPHA)),
    # 4.2.1 Stock Directory
    _msg("R", "stock_directory", 39,
         ("stock", 8, ALPHA),
         ("market_category", 1, ALPHA),
         ("financial_status_indicator", 1, ALPHA),
         ("round_lot_size", 4, UINT),
         ("round_lots_only", 1, ALPHA),
         ("issue_classification", 1, ALPHA),
         ("issue_sub_type", 2, ALPHA),
         ("authenticity", 1, ALPHA),
         ("short_sale_threshold_indicator", 1, ALPHA),
         ("ipo_flag", 1, ALPHA),
         ("luld_reference_price_tier", 1, ALPHA),
         ("etp_flag", 1, ALPHA),
         ("etp_leverage_factor", 4, UINT),
         ("inverse_indicator", 1, ALPHA)),
    # 4.2.2 Stock Trading Action
    _msg("H", "stock_trading_action", 25,
         ("stock", 8, ALPHA),
         ("trading_state", 1, ALPHA),
         ("reserved", 1, ALPHA),
         ("reason", 4, ALPHA)),
    # 4.2.3 Reg SHO Short Sale Price Test Restricted Indicator
    _msg("Y", "reg_sho_restriction", 20,
         ("stock", 8, ALPHA),
         ("reg_sho_action", 1, ALPHA)),
    # 4.2.4 Market Participant Position
    _msg("L", "market_participant_position", 26,
         ("mpid", 4, ALPHA),
         ("stock", 8, ALPHA),
         ("primary_market_maker", 1, ALPHA),
         ("market_maker_mode", 1, ALPHA),
         ("market_participant_state", 1, ALPHA)),
    # 4.2.5.1 MWCB Decline Level (Price(8) fields)
    _msg("V", "mwcb_decline_level", 35,
         ("level_1", 8, UINT),
         ("level_2", 8, UINT),
         ("level_3", 8, UINT)),
    # 4.2.5.2 MWCB Status
    _msg("W", "mwcb_status", 12,
         ("breached_level", 1, ALPHA)),
    # 4.2.6 IPO Quoting Period Update
    _msg("K", "ipo_quoting_period_update", 28,
         ("stock", 8, ALPHA),
         ("ipo_quotation_release_time", 4, UINT),
         ("ipo_quotation_release_qualifier", 1, ALPHA),
         ("ipo_price", 4, UINT)),
    # 4.2.7 LULD Auction Collar
    _msg("J", "luld_auction_collar", 35,
         ("stock", 8, ALPHA),
         ("auction_collar_reference_price", 4, UINT),
         ("upper_auction_collar_price", 4, UINT),
         ("lower_auction_collar_price", 4, UINT),
         ("auction_collar_extension", 4, UINT)),
    # 4.2.8 Operational Halt
    _msg("h", "operational_halt", 21,
         ("stock", 8, ALPHA),
         ("market_code", 1, ALPHA),
         ("operational_halt_action", 1, ALPHA)),
    # 4.3.1 Add Order, no MPID attribution
    _msg("A", "add_order", 36,
         ("order_reference_number", 8, UINT),
         ("buy_sell_indicator", 1, ALPHA),
         ("shares", 4, UINT),
         ("stock", 8, ALPHA),
         ("price", 4, UINT)),
    # 4.3.2 Add Order with MPID attribution
    _msg("F", "add_order_mpid", 40,
         ("order_reference_number", 8, UINT),
         ("buy_sell_indicator", 1, ALPHA),
         ("shares", 4, UINT),
         ("stock", 8, ALPHA),
         ("price", 4, UINT),
         ("attribution", 4, ALPHA)),
    # 4.4.1 Order Executed
    _msg("E", "order_executed", 31,
         ("order_reference_number", 8, UINT),
         ("executed_shares", 4, UINT),
         ("match_number", 8, UINT)),
    # 4.4.2 Order Executed With Price
    _msg("C", "order_executed_with_price", 36,
         ("order_reference_number", 8, UINT),
         ("executed_shares", 4, UINT),
         ("match_number", 8, UINT),
         ("printable", 1, ALPHA),
         ("execution_price", 4, UINT)),
    # 4.4.3 Order Cancel (partial)
    _msg("X", "order_cancel", 23,
         ("order_reference_number", 8, UINT),
         ("cancelled_shares", 4, UINT)),
    # 4.4.4 Order Delete
    _msg("D", "order_delete", 19,
         ("order_reference_number", 8, UINT)),
    # 4.4.5 Order Replace
    _msg("U", "order_replace", 35,
         ("original_order_reference_number", 8, UINT),
         ("new_order_reference_number", 8, UINT),
         ("shares", 4, UINT),
         ("price", 4, UINT)),
    # 4.5.1 Trade (non-cross), executions against hidden orders
    _msg("P", "trade", 44,
         ("order_reference_number", 8, UINT),
         ("buy_sell_indicator", 1, ALPHA),
         ("shares", 4, UINT),
         ("stock", 8, ALPHA),
         ("price", 4, UINT),
         ("match_number", 8, UINT)),
    # 4.5.2 Cross Trade (note: 8-byte shares)
    _msg("Q", "cross_trade", 40,
         ("shares", 8, UINT),
         ("stock", 8, ALPHA),
         ("cross_price", 4, UINT),
         ("match_number", 8, UINT),
         ("cross_type", 1, ALPHA)),
    # 4.5.3 Broken Trade
    _msg("B", "broken_trade", 19,
         ("match_number", 8, UINT)),
    # 4.6 Net Order Imbalance Indicator
    _msg("I", "noii", 50,
         ("paired_shares", 8, UINT),
         ("imbalance_shares", 8, UINT),
         ("imbalance_direction", 1, ALPHA),
         ("stock", 8, ALPHA),
         ("far_price", 4, UINT),
         ("near_price", 4, UINT),
         ("current_reference_price", 4, UINT),
         ("cross_type", 1, ALPHA),
         ("price_variation_indicator", 1, ALPHA)),
    # 4.7 Retail Price Improvement Indicator
    _msg("N", "rpii", 20,
         ("stock", 8, ALPHA),
         ("interest_flag", 1, ALPHA)),
    # 4.8 Direct Listing with Capital Raise Price Discovery
    _msg("O", "dlcr_price_discovery", 48,
         ("stock", 8, ALPHA),
         ("open_eligibility_status", 1, ALPHA),
         ("minimum_allowable_price", 4, UINT),
         ("maximum_allowable_price", 4, UINT),
         ("near_execution_price", 4, UINT),
         ("near_execution_time", 8, UINT),
         ("lower_price_range_collar", 4, UINT),
         ("upper_price_range_collar", 4, UINT)),
)}

# Message types that change the visible order book.
BOOK_MESSAGES = frozenset("AFECXDU")


def _check():
    for code, m in MESSAGES.items():
        assert m.code == code and len(code) == 1
        end = 0
        for f in m.fields:
            assert f.offset == end, f"{code}.{f.name}: offset {f.offset}, expected {end}"
            assert f.size > 0 and f.kind in (UINT, ALPHA)
            end += f.size
        assert end == m.length, f"{code}: fields end at {end}, spec length {m.length}"
        names = [f.name for f in m.fields]
        assert len(names) == len(set(names)), f"{code}: duplicate field name"


_check()
