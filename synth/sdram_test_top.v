// sdram_test_top.v
//
// Board top for the SDRAM test on the Tang Nano 20K: PLL, power-on reset,
// the tristate DQ bus and synth/sdram_memtest.v, which sweeps the read
// latency and reports each round on the USB serial port (115200 8N1).
//
// Clock: 27 MHz crystal -> rPLL -> 64.8 MHz (27 * 12 / 5), with the
// SDRAM clock from CLKOUTP shifted 225 degrees, the setting known to
// work on this board (nand2mario/nestang).
//
// Measured 2026-10-31: rd_lat = 3 (CL + 1) is the only latency with zero
// errors over the full 8 MB, including the rmw write-back; 1, 2 and 4 to
// 7 fail every round.

`default_nettype none

module sdram_test_top (
    input  wire        XTAL_IN,
    output wire [5:0]  led,
    output wire        UART_TXD,

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

    wire [31:0] dq_out;
    wire        dq_oe;

    sdram_memtest test (
        .clk(clk), .rst(!por[7]), .led(led), .uart_tx(UART_TXD),
        .sd_cke(O_sdram_cke), .sd_cs_n(O_sdram_cs_n), .sd_ras_n(O_sdram_ras_n),
        .sd_cas_n(O_sdram_cas_n), .sd_we_n(O_sdram_wen_n), .sd_ba(O_sdram_ba),
        .sd_addr(O_sdram_addr), .sd_dqm(O_sdram_dqm), .sd_dq_out(dq_out), .sd_dq_oe(dq_oe),
        .sd_dq_in(IO_sdram_dq)
    );
    assign IO_sdram_dq = dq_oe ? dq_out : 32'bz;

endmodule

`default_nettype wire
