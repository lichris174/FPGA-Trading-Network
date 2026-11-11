// order_book_top.v
//
// Synthesis-only wrapper for order_book with the real SDRAM pins and the
// 64.8 MHz PLL of the SDRAM board test, to measure size and speed on the
// GW2AR-18. Messages come from a 32-bit LFSR (a mix of adds, executes,
// deletes and replaces over a few thousand references), and the top 2
// outputs and flags are folded into the 6 LEDs so nothing is optimised away.

`default_nettype none

module order_book_top (
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

    wire [7:0] types [0:7];
    assign types[0] = "A"; assign types[1] = "A"; assign types[2] = "D"; assign types[3] = "D";
    assign types[4] = "E"; assign types[5] = "X"; assign types[6] = "U"; assign types[7] = "F";

    wire        msg_ready, idle, stash_full, unknown_ref, bad_reduce, shares_overflow, level_overflow;
    wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price;
    wire [19:0] bid1_size, bid2_size, ask1_size, ask2_size;
    wire [31:0] dq_out;
    wire        dq_oe;

    order_book book (
        .clk(clk), .rst(rst), .rd_lat(4'd3), .cfg_base(32'd1_896_800),
        .msg_valid(lfsr[31]), .msg_ready(msg_ready), .msg_type(types[lfsr[2:0]]),
        .msg_ref({52'd0, lfsr[14:3]}), .msg_new_ref({52'd0, lfsr[26:15]}), .msg_side_buy(lfsr[27]),
        .msg_shares({23'd0, lfsr[30:22]}), .msg_price(32'd2_000_000 + {18'd0, lfsr[13:0]}),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .idle(idle), .stash_full(stash_full), .unknown_ref(unknown_ref), .bad_reduce(bad_reduce),
        .shares_overflow(shares_overflow), .level_overflow(level_overflow),
        .dbg_en(1'b0), .dbg_addr(13'd0), .dbg_data(),
        .sd_cke(O_sdram_cke), .sd_cs_n(O_sdram_cs_n), .sd_ras_n(O_sdram_ras_n),
        .sd_cas_n(O_sdram_cas_n), .sd_we_n(O_sdram_wen_n), .sd_ba(O_sdram_ba),
        .sd_addr(O_sdram_addr), .sd_dqm(O_sdram_dqm), .sd_dq_out(dq_out), .sd_dq_oe(dq_oe),
        .sd_dq_in(IO_sdram_dq)
    );
    assign IO_sdram_dq = dq_oe ? dq_out : 32'bz;

    wire [31:0] fold = bid1_price ^ bid2_price ^ ask1_price ^ ask2_price
                     ^ {12'd0, bid1_size} ^ {bid2_size, 12'd0} ^ {6'd0, ask1_size, 6'd0} ^ {ask2_size[9:0], 2'd0, ask2_size};
    reg  [31:0] sum;
    always @(posedge clk) sum <= rst ? 32'd0 : {sum[30:0], sum[31]} ^ fold;

    assign led = ~{^sum[31:16], ^sum[15:0], stash_full | unknown_ref, bad_reduce | shares_overflow,
                   level_overflow, idle & msg_ready};

endmodule

`default_nettype wire
