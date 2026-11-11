// book_board.v
//
// Board logic for replaying ITCH into the order book over the USB serial
// port (synth/book_board_top.v adds the PLL and the tristate SDRAM bus;
// tb/book_board simulates this module with the SDRAM model).
//
// Every received byte goes to itch_book. A small frame tracker watches
// the same length-prefixed stream for two kinds of frame the parser
// skips anyway:
//
//   length 0      report request: once the book is ready and idle, send
//                 one report line
//   'Z', length 8 config: [1] flags (bit 0 = reset the book), [2..5]
//                 cfg_base, [6..7] cfg_locate, both big-endian. A reset
//                 clears the order table again, about 53 ms.
//
// Report line, all fields hex (scripts/board_link.py reads it):
//
//   n=<book messages>
//   b=<bid1 price>:<size> <bid2 price>:<size>
//   a=<ask1 price>:<size> <ask2 price>:<size>
//   f=<flags: ready, parse_error, fifo_overflow, level_overflow,
//      shares_overflow, bad_reduce, unknown_ref, stash_full>
//   q=<FIFO peak>
//   lat=<messages timed> <sum of clocks> <max> <min>
//   c=<s1> <s2>: Fletcher sums over the level RAM, address {side, slot}
//      ascending: s1 += size, s2 += s1, both mod 2^32 (debug)
//   r=<s1> <s2>: the same sums over every byte received since the last
//      book reset, to tell a serial link error from a book error
//
// Latency is clocks from book_msg (the clock after a book message's last
// byte) to the book being idle with the message applied, for every book
// message that arrives while the book is idle, which over a serial link
// is all of them.

`default_nettype none

module book_board #(
    parameter BUCKET_BITS  = 18,
    parameter INIT_WAIT    = 20000,
    parameter REF_INTERVAL = 500,
    parameter CLKS_PER_BIT = 32,       // clocks per serial bit
    parameter RD_LAT       = 3,
    parameter BEAT_BIT     = 25,
    parameter TX_GAP_CLKS  = 0         // idle time between report characters
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        uart_rxd,
    output wire        uart_txd,
    output wire [5:0]  led,

    output wire        sd_cke, sd_cs_n, sd_ras_n, sd_cas_n, sd_we_n,
    output wire [1:0]  sd_ba,
    output wire [10:0] sd_addr,
    output wire [3:0]  sd_dqm,
    output wire [31:0] sd_dq_out,
    output wire        sd_dq_oe,
    input  wire [31:0] sd_dq_in
);

    // --- serial in and the frame tracker ---------------------------------------

    wire [7:0] rx_data;
    wire       rx_valid, rx_frame_error;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clk(clk), .rst(rst), .rx(uart_rxd), .data(rx_data), .valid(rx_valid), .frame_error(rx_frame_error)
    );

    localparam [1:0] F_HI = 2'd0, F_LO = 2'd1, F_BODY = 2'd2;

    reg  [1:0]  fstate;
    reg  [7:0]  len_hi;
    reg  [15:0] flen, fidx;
    reg         is_cfg;
    /* verilator lint_off UNUSEDSIGNAL */    // of byte 1 only the reset bit is used
    reg  [47:0] cfg_bytes;          // config bytes 1..6; byte 7 is on rx_data at the end
    /* verilator lint_on UNUSEDSIGNAL */
    reg  [31:0] cfg_base;
    reg  [15:0] cfg_locate;
    reg         report_req;
    reg  [3:0]  book_rst_hold;
    wire        book_rst = rst || book_rst_hold != 0;
    reg         report_start;       // from the report sender below

    always @(posedge clk) begin
        if (book_rst_hold != 0)
            book_rst_hold <= book_rst_hold - 1'b1;
        if (report_start)
            report_req <= 1'b0;
        if (rx_valid) begin
            case (fstate)
            F_HI: begin
                len_hi <= rx_data;
                fstate <= F_LO;
            end
            F_LO: begin
                flen   <= {len_hi, rx_data};
                fidx   <= 16'd0;
                fstate <= {len_hi, rx_data} == 16'd0 ? F_HI : F_BODY;
                if ({len_hi, rx_data} == 16'd0)
                    report_req <= 1'b1;
            end
            default: begin
                if (fidx == 0)
                    is_cfg <= rx_data == "Z" && flen == 16'd8;
                else
                    cfg_bytes <= {cfg_bytes[39:0], rx_data};
                fidx <= fidx + 1'b1;
                if (fidx == flen - 1'b1) begin
                    fstate <= F_HI;
                    if (is_cfg && fidx == 16'd7) begin
                        cfg_base   <= cfg_bytes[39:8];
                        cfg_locate <= {cfg_bytes[7:0], rx_data};
                        if (cfg_bytes[40])                  // byte 1 (flags), bit 0
                            book_rst_hold <= 4'd15;
                    end
                end
            end
            endcase
        end
        if (rst) begin
            fstate        <= F_HI;
            report_req    <= 1'b0;
            book_rst_hold <= 4'd0;
            cfg_base      <= 32'd0;
            cfg_locate    <= 16'd0;
            is_cfg        <= 1'b0;
        end
    end

    // --- the book ------------------------------------------------------------

    wire        ready, idle, book_msg, parse_error, fifo_overflow;
    /* verilator lint_off UNUSEDSIGNAL */    // the parser always takes bytes
    wire        in_ready;
    /* verilator lint_on UNUSEDSIGNAL */
    wire        stash_full, unknown_ref, bad_reduce, shares_overflow, level_overflow;
    wire [10:0] fifo_peak;
    wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price;
    wire [19:0] bid1_size, bid2_size, ask1_size, ask2_size;

    itch_book #(.BUCKET_BITS(BUCKET_BITS), .INIT_WAIT(INIT_WAIT), .REF_INTERVAL(REF_INTERVAL)) book (
        .clk(clk), .rst(book_rst), .in_data(rx_data), .in_valid(rx_valid), .in_ready(in_ready),
        .filter_en(1'b1), .cfg_locate(cfg_locate), .rd_lat(RD_LAT[3:0]), .cfg_base(cfg_base),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .ready(ready), .idle(idle), .book_msg(book_msg), .parse_error(parse_error),
        .fifo_overflow(fifo_overflow), .fifo_peak(fifo_peak),
        .stash_full(stash_full), .unknown_ref(unknown_ref), .bad_reduce(bad_reduce),
        .shares_overflow(shares_overflow), .level_overflow(level_overflow),
        .dbg_en(dbg_en), .dbg_addr(dbg_addr), .dbg_data(dbg_data),
        .sd_cke(sd_cke), .sd_cs_n(sd_cs_n), .sd_ras_n(sd_ras_n), .sd_cas_n(sd_cas_n), .sd_we_n(sd_we_n),
        .sd_ba(sd_ba), .sd_addr(sd_addr), .sd_dqm(sd_dqm),
        .sd_dq_out(sd_dq_out), .sd_dq_oe(sd_dq_oe), .sd_dq_in(sd_dq_in)
    );

    // --- checksum of received bytes ---------------------------------------------

    reg [31:0] rx_s1, rx_s2;
    always @(posedge clk) begin
        if (rx_valid) begin
            rx_s1 <= rx_s1 + {24'd0, rx_data};
            rx_s2 <= rx_s2 + rx_s1 + {24'd0, rx_data};
        end
        if (book_rst) begin
            rx_s1 <= 32'd0;
            rx_s2 <= 32'd0;
        end
    end

    // --- message count and latency ---------------------------------------------

    reg  [31:0] n_msgs, lat_n;
    reg  [39:0] lat_sum;
    reg  [15:0] lat_max, lat_min, lat_run;
    reg         timing;

    always @(posedge clk) begin
        if (book_msg)
            n_msgs <= n_msgs + 1'b1;
        if (book_msg && !timing && idle) begin
            timing  <= 1'b1;
            lat_run <= 16'd1;
        end else if (timing) begin
            if (idle) begin
                timing  <= 1'b0;
                lat_n   <= lat_n + 1'b1;
                lat_sum <= lat_sum + {24'd0, lat_run};
                if (lat_run > lat_max) lat_max <= lat_run;
                if (lat_run < lat_min) lat_min <= lat_run;
            end else if (lat_run != 16'hFFFF)
                lat_run <= lat_run + 1'b1;
        end
        if (book_rst) begin
            n_msgs  <= 32'd0;
            lat_n   <= 32'd0;
            lat_sum <= 40'd0;
            lat_max <= 16'd0;
            lat_min <= 16'hFFFF;
            timing  <= 1'b0;
        end
    end

    // --- level RAM checksum, before each report ------------------------------

    reg         dbg_en, sum_done;
    reg  [12:0] dbg_addr;
    wire [19:0] dbg_data;
    reg  [2:0]  sum_v;          // address issued, at the level RAM, data out
    reg  [31:0] sum1, sum2;

    always @(posedge clk) begin
        sum_v    <= {sum_v[1:0], 1'b0};
        sum_done <= 1'b0;
        if (!dbg_en && !printing && report_req && !report_start && ready && idle && !book_rst && !sum_done) begin
            dbg_en   <= 1'b1;
            dbg_addr <= 13'd0;
            sum1     <= 32'd0;
            sum2     <= 32'd0;
            sum_v    <= 3'b001;
        end else if (dbg_en) begin
            if (sum_v[2]) begin
                sum1 <= sum1 + {12'd0, dbg_data};
                sum2 <= sum2 + sum1 + {12'd0, dbg_data};
            end
            if (dbg_addr != 13'h1FFF) begin
                dbg_addr <= dbg_addr + 1'b1;
                sum_v[0] <= 1'b1;
            end else if (sum_v[1:0] == 2'b00 && !sum_v[2]) begin
                dbg_en   <= 1'b0;
                sum_done <= 1'b1;
            end
        end
        if (book_rst)
            dbg_en <= 1'b0;
    end

    // --- report line -----------------------------------------------------------

    localparam LEN = 161;
    localparam [8*LEN-1:0] TPL = {"n=######## b=########:##### ########:##### a=########:##### ########:##### ",
                                  "f=## q=### lat=######## ########## #### #### c=######## ######## r=######## ########", 8'h0D, 8'h0A};
    wire [7:0]   flags = {ready, parse_error, fifo_overflow, level_overflow,
                          shares_overflow, bad_reduce, unknown_ref, stash_full};
    wire [491:0] fields = {n_msgs, bid1_price, bid1_size, bid2_price, bid2_size,
                           ask1_price, ask1_size, ask2_price, ask2_size,
                           flags, 1'b0, fifo_peak, lat_n, lat_sum, lat_max, lat_min, sum1, sum2, rx_s1, rx_s2};

    reg  [7:0]   pchar;
    reg  [6:0]   pnib;
    reg  [491:0] snap;
    reg          printing;
    wire [7:0]   tch = TPL[8*(LEN - 1 - pchar) +: 8];
    wire [3:0]   nib = snap[491 - 4*pnib -: 4];
    wire [7:0]   ch  = tch == "#" ? (nib < 10 ? 8'h30 + {4'd0, nib} : 8'h57 + {4'd0, nib}) : tch;
    wire         tx_ready;

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT), .GAP_CLKS(TX_GAP_CLKS)) u_tx (
        .clk(clk), .rst(rst), .data(ch), .valid(printing), .ready(tx_ready), .tx(uart_txd)
    );

    always @(posedge clk) begin
        report_start <= 1'b0;
        if (!printing && report_req && !report_start && sum_done && !book_rst) begin
            snap         <= fields;
            pchar        <= 8'd0;
            pnib         <= 7'd0;
            printing     <= 1'b1;
            report_start <= 1'b1;
        end else if (printing && tx_ready) begin
            pchar <= pchar + 1'b1;
            if (tch == "#")
                pnib <= pnib + 1'b1;
            if (pchar == LEN - 1)
                printing <= 1'b0;
        end
        if (rst)
            printing <= 1'b0;
    end

    // --- LEDs (on = low) -------------------------------------------------------

    reg [BEAT_BIT:0] beat;
    reg              rx_seen;
    always @(posedge clk) begin
        beat <= beat + 1'b1;
        if (rx_valid) rx_seen <= ~rx_seen;
        if (rst) begin
            beat    <= 0;
            rx_seen <= 1'b0;
        end
    end

    wire any_error = parse_error | fifo_overflow | level_overflow | shares_overflow | bad_reduce
                   | unknown_ref | stash_full | rx_frame_error;

    // 0 heartbeat, 1 ready, 2 idle, 3 any error, 4 toggles per byte received, 5 sending a report
    assign led = ~{printing, rx_seen, any_error, idle, ready, beat[BEAT_BIT]};

endmodule

`default_nettype wire
