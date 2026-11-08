// itch_book_top.v
//
// Synthesis-only wrapper for itch_book (parser, FIFO, order book) with the
// real SDRAM pins and the 64.8 MHz PLL, to measure size and speed on the
// GW2AR-18. The byte stream comes from a 32-bit LFSR, and the top 2
// outputs and flags are folded into the 6 LEDs so nothing is optimised away.

`default_nettype none

module itch_book_top (
    input  wire        XTAL_IN,
    output wire [5:0]  led,

    output wire        O_sdram_clk,
    output wire        O_sdram_cke,
    output wire        O_sdram_cs_n,
    output wire        O_sdram_cas_n,
    output wire        O_sdram_ras_n,
    output wire        O_sdram_wen_n,
    output wire [1:0]  O_sdram_ba,
    output wire [10:0] O_sdram_addr,
    output wire [3:0]  O_sdram_dqm,
    inout  wire [31:0] IO_sdram_dq
);

    wire clk, lock;
    rPLL #(
        .FCLKIN("27"), .IDIV_SEL(4), .FBDIV_SEL(11), .ODIV_SEL(8),
        .DYN_IDIV_SEL("false"), .DYN_FBDIV_SEL("false"), .DYN_ODIV_SEL("false"),
        .PSDA_SEL("1010"), .DYN_DA_EN("false"), .DUTYDA_SEL("1000"),
        .CLKOUT_FT_DIR(1'b1), .CLKOUTP_FT_DIR(1'b1), .CLKOUT_DLY_STEP(0), .CLKOUTP_DLY_STEP(0),
        .CLKFB_SEL("internal"), .CLKOUT_BYPASS("false"), .CLKOUTP_BYPASS("false"),
        .CLKOUTD_BYPASS("false"), .DYN_SDIV_SEL(2), .CLKOUTD_SRC("CLKOUT"),
        .CLKOUTD3_SRC("CLKOUT"), .DEVICE("GW2AR-18C")
    ) pll (
        .CLKOUT(clk), .LOCK(lock), .CLKOUTP(O_sdram_clk), .CLKOUTD(), .CLKOUTD3(),
        .RESET(1'b0), .RESET_P(1'b0), .CLKIN(XTAL_IN), .CLKFB(1'b0),
        .FBDSEL(6'd0), .IDSEL(6'd0), .ODSEL(6'd0), .PSDA(4'd0), .DUTYDA(4'd0), .FDLY(4'd0)
    );

    reg [7:0] por = 8'd0;
    always @(posedge clk)
        if (!lock)        por <= 8'd0;
        else if (!por[7]) por <= por + 8'd1;
    wire rst = !por[7];

    reg [31:0] lfsr;
    always @(posedge clk)
        if (rst) lfsr <= 32'hACE1_2468;
        else     lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};

    wire        in_ready, ready, idle, parse_error, fifo_overflow;
    wire        stash_full, unknown_ref, bad_reduce, shares_overflow, level_overflow;
    wire [10:0] fifo_peak;
    wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price;
    wire [19:0] bid1_size, bid2_size, ask1_size, ask2_size;
    wire [31:0] dq_out;
    wire        dq_oe;

    itch_book book (
        .clk(clk), .rst(rst), .in_data(lfsr[7:0]), .in_valid(lfsr[31]), .in_ready(in_ready),
        .filter_en(1'b1), .cfg_locate(16'd14), .rd_lat(4'd3), .cfg_base(32'd1_896_800),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .ready(ready), .idle(idle), .parse_error(parse_error), .fifo_overflow(fifo_overflow), .fifo_peak(fifo_peak),
        .stash_full(stash_full), .unknown_ref(unknown_ref), .bad_reduce(bad_reduce),
        .shares_overflow(shares_overflow), .level_overflow(level_overflow),
        .sd_cke(O_sdram_cke), .sd_cs_n(O_sdram_cs_n), .sd_ras_n(O_sdram_ras_n),
        .sd_cas_n(O_sdram_cas_n), .sd_we_n(O_sdram_wen_n), .sd_ba(O_sdram_ba),
        .sd_addr(O_sdram_addr), .sd_dqm(O_sdram_dqm), .sd_dq_out(dq_out), .sd_dq_oe(dq_oe),
        .sd_dq_in(IO_sdram_dq)
    );
    assign IO_sdram_dq = dq_oe ? dq_out : 32'bz;

    wire [31:0] fold = bid1_price ^ bid2_price ^ ask1_price ^ ask2_price
                     ^ {12'd0, bid1_size} ^ {bid2_size, 12'd0} ^ {6'd0, ask1_size, 6'd0} ^ {ask2_size[9:0], 2'd0, ask2_size}
                     ^ {21'd0, fifo_peak};
    reg  [31:0] sum;
    always @(posedge clk) sum <= rst ? 32'd0 : {sum[30:0], sum[31]} ^ fold;

    assign led = ~{^sum[31:16], ^sum[15:0], stash_full | unknown_ref | parse_error, bad_reduce | shares_overflow,
                   level_overflow | fifo_overflow, idle & ready & in_ready};

endmodule

`default_nettype wire
