// itch_parser_top.v
//
// Synthesis-only wrapper for itch_parser, to measure its size and speed
// on the GW2AR-18 before the real top level exists (iteration 9).
//
// The parser has about 330 output bits and the chip has about 70 user
// pins, and unconnected outputs would be optimised away. So every
// decoded message is folded into a 32-bit checksum (rotate, then XOR in
// all the fields), and only a 16-bit fold of it reaches pins. Inputs and the
// checksum are registered, so the timing report measures the parser's
// own paths, not pin delays.
//
// cfg_locate is a fixed parameter here; in the real design it will be a
// register loaded over UART.

`default_nettype none

module itch_parser_top #(
    parameter [15:0] LOCATE = 16'd14
) (
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  in_data,
    input  wire        in_valid,
    input  wire        filter_en,
    output wire [15:0] checksum,
    output reg         seen_error
);

    reg  [7:0] data_q;
    reg        valid_q, filter_q, rst_q;

    always @(posedge clk) begin
        data_q   <= in_data;
        valid_q  <= in_valid;
        filter_q <= filter_en;
        rst_q    <= rst;
    end

    wire        msg_valid, msg_error, side_buy, in_ready;
    wire [7:0]  msg_type, event_code, trading_state;
    wire [15:0] stock_locate;
    wire [47:0] timestamp;
    wire [63:0] order_ref, new_order_ref;
    wire [31:0] shares, price;

    itch_parser parser (
        .clk(clk), .rst(rst_q),
        .in_data(data_q), .in_valid(valid_q), .in_ready(in_ready),
        .filter_en(filter_q), .cfg_locate(LOCATE),
        .msg_valid(msg_valid), .msg_error(msg_error),
        .msg_type(msg_type), .stock_locate(stock_locate), .timestamp(timestamp),
        .order_ref(order_ref), .new_order_ref(new_order_ref), .side_buy(side_buy),
        .shares(shares), .price(price), .event_code(event_code), .trading_state(trading_state)
    );

    wire [31:0] fold = {msg_type, event_code, trading_state, 7'd0, side_buy}
                     ^ {stock_locate, timestamp[47:32]} ^ timestamp[31:0]
                     ^ order_ref[63:32] ^ order_ref[31:0]
                     ^ new_order_ref[63:32] ^ new_order_ref[31:0]
                     ^ shares ^ price;

    reg [31:0] sum;
    assign checksum = sum[31:16] ^ sum[15:0];

    always @(posedge clk) begin
        if (rst_q) begin
            sum        <= 32'd0;
            seen_error <= 1'b0;
        end else begin
            if (msg_valid)
                sum <= {sum[30:0], sum[31]} ^ fold;
            if (msg_error)
                seen_error <= 1'b1;
        end
    end

endmodule

`default_nettype wire
