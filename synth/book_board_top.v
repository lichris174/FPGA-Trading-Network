// book_board_top.v
//
// Board top for replaying ITCH into the order book over USB serial on the
// Tang Nano 20K: PLL (54 MHz, SDRAM clock shifted 180 degrees), power-on
// reset, the tristate SDRAM bus, and synth/book_board.v, which holds the
// serial protocol (scripts/board_link.py, scripts/board_replay.py).
// Serial runs at 460800 baud: 54 MHz / 117 clocks per bit, 0.2% fast.
//
// 54 MHz, not the 64.8 MHz of the other tops: at 64.8 MHz (Fmax about
// 66 MHz) a level went wrong every 150,000 or so messages on the board;
// at 54 MHz both days replay clean. At 54 MHz the SDRAM needs the clock
// shifted 180 degrees and read latency 2 (225 degrees fails at every
// latency), as the memtest found with the same PLL settings.

`default_nettype none

module book_board_top (
    input  wire        XTAL_IN,
    output wire [5:0]  led,
    input  wire        UART_RXD,
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
        .FCLKIN("27"), .IDIV_SEL(0), .FBDIV_SEL(1), .ODIV_SEL(16),
        .DYN_IDIV_SEL("false"), .DYN_FBDIV_SEL("false"), .DYN_ODIV_SEL("false"),
        .PSDA_SEL("1000"), .DYN_DA_EN("false"), .DUTYDA_SEL("1000"),
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

    wire [31:0] dq_out;
    wire        dq_oe;

    book_board #(.CLKS_PER_BIT(117), .TX_GAP_CLKS(1170), .REF_INTERVAL(400), .RD_LAT(2)) board (
        .clk(clk), .rst(rst), .uart_rxd(UART_RXD), .uart_txd(UART_TXD), .led(led),
        .sd_cke(O_sdram_cke), .sd_cs_n(O_sdram_cs_n), .sd_ras_n(O_sdram_ras_n),
        .sd_cas_n(O_sdram_cas_n), .sd_we_n(O_sdram_wen_n), .sd_ba(O_sdram_ba),
        .sd_addr(O_sdram_addr), .sd_dqm(O_sdram_dqm), .sd_dq_out(dq_out), .sd_dq_oe(dq_oe),
        .sd_dq_in(IO_sdram_dq)
    );
    assign IO_sdram_dq = dq_oe ? dq_out : 32'bz;

endmodule

`default_nettype wire
