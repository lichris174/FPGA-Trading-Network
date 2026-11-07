// book_levels_top.v
//
// Synthesis-only wrapper for book_levels, to measure its size and speed
// on the GW2AR-18. The module has about 70 input and 210 output bits, so
// the updates come from a 32-bit LFSR inside the wrapper and the top 2
// outputs are folded into a 32-bit checksum, of which 16 bits reach
// pins. Outputs are registered, so the timing report measures the
// module's own paths.

`default_nettype none

module book_levels_top (
    input  wire        clk,
    input  wire        rst,
    output wire [15:0] checksum,
    output reg         overflow
);

    reg rst_q;
    always @(posedge clk) rst_q <= rst;

    reg [31:0] lfsr;
    always @(posedge clk)
        if (rst_q) lfsr <= 32'hACE1_2468;
        else       lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};

    wire        upd_ready, level_overflow;
    wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price;
    wire [19:0] bid1_size, bid2_size, ask1_size, ask2_size;

    book_levels levels (
        .clk(clk), .rst(rst_q), .cfg_base(32'd1_795_200),
        .upd_valid(lfsr[31]), .upd_ready(upd_ready), .upd_side(lfsr[30]), .upd_slot(lfsr[29:18]),
        .upd_add(lfsr[17]), .upd_shares(lfsr[16:0]),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .level_overflow(level_overflow)
    );

    wire [31:0] fold = bid1_price ^ bid2_price ^ ask1_price ^ ask2_price
                     ^ {12'd0, bid1_size} ^ {bid2_size, 12'd0} ^ {6'd0, ask1_size, 6'd0} ^ {ask2_size[9:0], 2'd0, ask2_size};

    reg [31:0] sum;
    assign checksum = sum[31:16] ^ sum[15:0];

    always @(posedge clk) begin
        sum      <= rst_q ? 32'd0 : {sum[30:0], sum[31]} ^ fold;
        overflow <= level_overflow;
    end

endmodule

`default_nettype wire
