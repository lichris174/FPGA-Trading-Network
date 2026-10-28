// itch_parser.v
//
// Nasdaq ITCH 5.0 parser, one byte per clock.
//
// Input is the same length-prefixed stream as the sample files and the
// golden tools: [len hi][len lo][message bytes ...], repeated. The
// parser always accepts (in_ready is high), so a byte is taken on every
// clock where in_valid is high. Gaps in in_valid are fine anywhere,
// including inside a message.
//
// It decodes the messages the order book and features need:
//
//   A, F  add order          order_ref, side_buy, shares, price
//   E     executed           order_ref, shares (executed)
//   C     executed w/ price  order_ref, shares (executed), price (execution)
//   X     partial cancel     order_ref, shares (cancelled)
//   D     delete             order_ref
//   U     replace            order_ref (original), new_order_ref, shares, price
//   P     trade, hidden      order_ref, side_buy, shares, price
//   S     system event       event_code
//   H     trading action     trading_state
//
// Every message also gives msg_type, stock_locate and timestamp. Fields
// a type doesn't have read zero. All other types are skipped using the
// length prefix.
//
// Output: msg_valid is high for one cycle, the cycle after the message's
// last byte. The fields hold until the next message's first byte, so
// downstream should capture them on msg_valid. A decoded type whose
// length doesn't match the spec is dropped and pulses msg_error instead
// (the golden parser raises an error on the same input).
//
// Stock filter: with filter_en high, only messages whose stock locate
// equals cfg_locate come out, plus system events (S), which apply to
// every stock.
//
// Byte offsets come from itch_spec.vh, which is generated from
// golden/itch/spec.py. Don't write offsets by hand here.

`default_nettype none

module itch_parser (
    input  wire        clk,
    input  wire        rst,            // synchronous, active high

    // Byte stream in
    input  wire [7:0]  in_data,
    input  wire        in_valid,
    output wire        in_ready,

    // Stock filter
    input  wire        filter_en,
    input  wire [15:0] cfg_locate,

    // Decoded message out
    output reg         msg_valid,
    output reg         msg_error,
    output reg  [7:0]  msg_type,
    output reg  [15:0] stock_locate,
    output reg  [47:0] timestamp,
    output reg  [63:0] order_ref,
    output reg  [63:0] new_order_ref,
    output reg         side_buy,
    output reg  [31:0] shares,
    output reg  [31:0] price,
    output reg  [7:0]  event_code,
    output reg  [7:0]  trading_state
);

    `include "itch_spec.vh"

    localparam [1:0] S_LEN_HI = 2'd0,
                     S_LEN_LO = 2'd1,
                     S_BODY   = 2'd2;

    reg  [1:0]  state;
    reg  [7:0]  len_hi;
    reg  [15:0] len;
    reg  [15:0] offset;     // index of the current byte within the message

    assign in_ready = 1'b1;

    wire take = in_valid & in_ready;
    wire last = (offset == len - 16'd1);

    // True when byte number `off` is inside a field at offset `o`, `s` bytes long.
    function in_field;
        input [15:0] off;
        input integer o;
        input integer s;
        begin
            in_field = (off >= o[15:0]) && (off < o[15:0] + s[15:0]);
        end
    endfunction

    // Types this parser decodes, and the spec length of each.
    function decoded;
        input [7:0] t;
        begin
            case (t)
                ITCH_ADD_ORDER_TYPE, ITCH_ADD_ORDER_MPID_TYPE,
                ITCH_ORDER_EXECUTED_TYPE, ITCH_ORDER_EXECUTED_WITH_PRICE_TYPE,
                ITCH_ORDER_CANCEL_TYPE, ITCH_ORDER_DELETE_TYPE,
                ITCH_ORDER_REPLACE_TYPE, ITCH_TRADE_TYPE,
                ITCH_SYSTEM_EVENT_TYPE, ITCH_STOCK_TRADING_ACTION_TYPE:
                    decoded = 1'b1;
                default:
                    decoded = 1'b0;
            endcase
        end
    endfunction

    function [15:0] spec_len;
        input [7:0] t;
        begin
            case (t)
                ITCH_ADD_ORDER_TYPE:                 spec_len = ITCH_ADD_ORDER_LEN;
                ITCH_ADD_ORDER_MPID_TYPE:            spec_len = ITCH_ADD_ORDER_MPID_LEN;
                ITCH_ORDER_EXECUTED_TYPE:            spec_len = ITCH_ORDER_EXECUTED_LEN;
                ITCH_ORDER_EXECUTED_WITH_PRICE_TYPE: spec_len = ITCH_ORDER_EXECUTED_WITH_PRICE_LEN;
                ITCH_ORDER_CANCEL_TYPE:              spec_len = ITCH_ORDER_CANCEL_LEN;
                ITCH_ORDER_DELETE_TYPE:              spec_len = ITCH_ORDER_DELETE_LEN;
                ITCH_ORDER_REPLACE_TYPE:             spec_len = ITCH_ORDER_REPLACE_LEN;
                ITCH_TRADE_TYPE:                     spec_len = ITCH_TRADE_LEN;
                ITCH_SYSTEM_EVENT_TYPE:              spec_len = ITCH_SYSTEM_EVENT_LEN;
                ITCH_STOCK_TRADING_ACTION_TYPE:      spec_len = ITCH_STOCK_TRADING_ACTION_LEN;
                default:                             spec_len = 16'd0;
            endcase
        end
    endfunction

    // Decision for the message ending on this byte. msg_type and
    // stock_locate are already complete: they arrive in bytes 0 to 2,
    // and every decoded type is longer than that.
    wire passes = !filter_en
               || msg_type == ITCH_SYSTEM_EVENT_TYPE
               || stock_locate == cfg_locate;
    wire ok_len = (len == spec_len(msg_type));

    // Framing: length prefix, then count through the message body.
    always @(posedge clk) begin
        if (rst) begin
            state     <= S_LEN_HI;
            len_hi    <= 8'd0;
            len       <= 16'd0;
            offset    <= 16'd0;
            msg_valid <= 1'b0;
            msg_error <= 1'b0;
        end else begin
            msg_valid <= 1'b0;
            msg_error <= 1'b0;
            if (take) begin
                case (state)
                    S_LEN_HI: begin
                        len_hi <= in_data;
                        state  <= S_LEN_LO;
                    end
                    S_LEN_LO: begin
                        len    <= {len_hi, in_data};
                        offset <= 16'd0;
                        // An empty message has no body; go straight to the next prefix.
                        state  <= ({len_hi, in_data} == 16'd0) ? S_LEN_HI : S_BODY;
                    end
                    default: begin
                        if (last) begin
                            state <= S_LEN_HI;
                            // The type byte is only in msg_type from byte 1 on;
                            // a 1-byte message is never a decoded type.
                            if (offset != 16'd0 && decoded(msg_type)) begin
                                msg_valid <= ok_len && passes;
                                msg_error <= !ok_len;
                            end
                        end else begin
                            offset <= offset + 16'd1;
                        end
                    end
                endcase
            end
        end
    end

    // Fields: each body byte shifts into the field it belongs to. Fields
    // are big-endian, so shifting left byte by byte leaves them in order.
    always @(posedge clk) begin
        if (rst) begin
            msg_type      <= 8'd0;
            stock_locate  <= 16'd0;
            timestamp     <= 48'd0;
            order_ref     <= 64'd0;
            new_order_ref <= 64'd0;
            side_buy      <= 1'b0;
            shares        <= 32'd0;
            price         <= 32'd0;
            event_code    <= 8'd0;
            trading_state <= 8'd0;
        end else if (take && state == S_BODY) begin
            if (offset == ITCH_HDR_MESSAGE_TYPE_OFF) begin
                msg_type      <= in_data;
                stock_locate  <= 16'd0;
                timestamp     <= 48'd0;
                order_ref     <= 64'd0;
                new_order_ref <= 64'd0;
                side_buy      <= 1'b0;
                shares        <= 32'd0;
                price         <= 32'd0;
                event_code    <= 8'd0;
                trading_state <= 8'd0;
            end else begin
                if (in_field(offset, ITCH_HDR_STOCK_LOCATE_OFF, ITCH_HDR_STOCK_LOCATE_SIZE))
                    stock_locate <= {stock_locate[7:0], in_data};
                if (in_field(offset, ITCH_HDR_TIMESTAMP_OFF, ITCH_HDR_TIMESTAMP_SIZE))
                    timestamp <= {timestamp[39:0], in_data};

                case (msg_type)
                    ITCH_ADD_ORDER_TYPE: begin
                        if (in_field(offset, ITCH_ADD_ORDER_ORDER_REFERENCE_NUMBER_OFF, ITCH_ADD_ORDER_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (offset == ITCH_ADD_ORDER_BUY_SELL_INDICATOR_OFF)
                            side_buy <= (in_data == "B");
                        if (in_field(offset, ITCH_ADD_ORDER_SHARES_OFF, ITCH_ADD_ORDER_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                        if (in_field(offset, ITCH_ADD_ORDER_PRICE_OFF, ITCH_ADD_ORDER_PRICE_SIZE))
                            price <= {price[23:0], in_data};
                    end
                    ITCH_ADD_ORDER_MPID_TYPE: begin
                        if (in_field(offset, ITCH_ADD_ORDER_MPID_ORDER_REFERENCE_NUMBER_OFF, ITCH_ADD_ORDER_MPID_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (offset == ITCH_ADD_ORDER_MPID_BUY_SELL_INDICATOR_OFF)
                            side_buy <= (in_data == "B");
                        if (in_field(offset, ITCH_ADD_ORDER_MPID_SHARES_OFF, ITCH_ADD_ORDER_MPID_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                        if (in_field(offset, ITCH_ADD_ORDER_MPID_PRICE_OFF, ITCH_ADD_ORDER_MPID_PRICE_SIZE))
                            price <= {price[23:0], in_data};
                    end
                    ITCH_ORDER_EXECUTED_TYPE: begin
                        if (in_field(offset, ITCH_ORDER_EXECUTED_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_EXECUTED_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (in_field(offset, ITCH_ORDER_EXECUTED_EXECUTED_SHARES_OFF, ITCH_ORDER_EXECUTED_EXECUTED_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                    end
                    ITCH_ORDER_EXECUTED_WITH_PRICE_TYPE: begin
                        if (in_field(offset, ITCH_ORDER_EXECUTED_WITH_PRICE_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_EXECUTED_WITH_PRICE_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (in_field(offset, ITCH_ORDER_EXECUTED_WITH_PRICE_EXECUTED_SHARES_OFF, ITCH_ORDER_EXECUTED_WITH_PRICE_EXECUTED_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                        if (in_field(offset, ITCH_ORDER_EXECUTED_WITH_PRICE_EXECUTION_PRICE_OFF, ITCH_ORDER_EXECUTED_WITH_PRICE_EXECUTION_PRICE_SIZE))
                            price <= {price[23:0], in_data};
                    end
                    ITCH_ORDER_CANCEL_TYPE: begin
                        if (in_field(offset, ITCH_ORDER_CANCEL_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_CANCEL_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (in_field(offset, ITCH_ORDER_CANCEL_CANCELLED_SHARES_OFF, ITCH_ORDER_CANCEL_CANCELLED_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                    end
                    ITCH_ORDER_DELETE_TYPE: begin
                        if (in_field(offset, ITCH_ORDER_DELETE_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_DELETE_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                    end
                    ITCH_ORDER_REPLACE_TYPE: begin
                        if (in_field(offset, ITCH_ORDER_REPLACE_ORIGINAL_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_REPLACE_ORIGINAL_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (in_field(offset, ITCH_ORDER_REPLACE_NEW_ORDER_REFERENCE_NUMBER_OFF, ITCH_ORDER_REPLACE_NEW_ORDER_REFERENCE_NUMBER_SIZE))
                            new_order_ref <= {new_order_ref[55:0], in_data};
                        if (in_field(offset, ITCH_ORDER_REPLACE_SHARES_OFF, ITCH_ORDER_REPLACE_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                        if (in_field(offset, ITCH_ORDER_REPLACE_PRICE_OFF, ITCH_ORDER_REPLACE_PRICE_SIZE))
                            price <= {price[23:0], in_data};
                    end
                    ITCH_TRADE_TYPE: begin
                        if (in_field(offset, ITCH_TRADE_ORDER_REFERENCE_NUMBER_OFF, ITCH_TRADE_ORDER_REFERENCE_NUMBER_SIZE))
                            order_ref <= {order_ref[55:0], in_data};
                        if (offset == ITCH_TRADE_BUY_SELL_INDICATOR_OFF)
                            side_buy <= (in_data == "B");
                        if (in_field(offset, ITCH_TRADE_SHARES_OFF, ITCH_TRADE_SHARES_SIZE))
                            shares <= {shares[23:0], in_data};
                        if (in_field(offset, ITCH_TRADE_PRICE_OFF, ITCH_TRADE_PRICE_SIZE))
                            price <= {price[23:0], in_data};
                    end
                    ITCH_SYSTEM_EVENT_TYPE: begin
                        if (offset == ITCH_SYSTEM_EVENT_EVENT_CODE_OFF)
                            event_code <= in_data;
                    end
                    ITCH_STOCK_TRADING_ACTION_TYPE: begin
                        if (offset == ITCH_STOCK_TRADING_ACTION_TRADING_STATE_OFF)
                            trading_state <= in_data;
                    end
                    default: ;
                endcase
            end
        end
    end

endmodule

`default_nettype wire
