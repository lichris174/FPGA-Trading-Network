// sdram_memtest.v
//
// Memory test for rtl/sdram/sdram_ctrl.v, the logic of the board test
// (synth/sdram_test_top.v adds the PLL and the tristate DQ bus). It is
// also simulated against tb/sdram/sdram_model.py with fewer groups.
//
// One round per read latency, rd_lat = 1, 2, ... 7, then again forever,
// with a new seed each round:
//
//   1. fill     write every group of 8 words with pat(addr)
//   2. verify   read every group back
//   3. rmw      read every group with rmw, check it, write back 2 words
//               (offset 0, 2, 4 or 6 by group) as {A5, addr}
//
// The write-back marker has no multiplier on purpose: written as ~pat,
// the Gowin build stored repeatable garbage in exactly those words while
// the simulation against the SDRAM model passed, and fill writes with pat
// were fine. Not root-caused; the controller passes with the marker.
//   4. verify   read every group back, expecting the rmw changes
//
// After each round it prints one line on the UART (115200 8N1):
//
//   lat=3 err=00000000 ph=0 addr=000000 got=00000000 exp=00000000
//
// err counts mismatched words in the round; ph/addr/got/exp describe
// the first one (ph 1 to 3 = the phase above minus one).
//
// LEDs (on = low): until the first sweep of rd_lat 1 to 7 is done, led[0]
// is a heartbeat and led[5:3] show the rd_lat running now. After that,
// led[n] is on when the last round at rd_lat = n + 1 had no errors
// (n = 0 to 5), so a working board shows exactly one LED, or a few
// neighbours if the capture window is wide.

`default_nettype none

module sdram_memtest #(
    parameter GROUP_BITS   = 18,           // 2^18 groups of 8 words = all 8 MB
    parameter CLKS_PER_BIT = 562,
    parameter INIT_WAIT    = 20000,
    parameter REF_INTERVAL = 500,
    parameter BEAT_BIT     = 25
) (
    input  wire        clk,
    input  wire        rst,
    output wire [5:0]  led,
    output wire        uart_tx,

    output wire        sd_cke,
    output wire        sd_cs_n,
    output wire        sd_ras_n,
    output wire        sd_cas_n,
    output wire        sd_we_n,
    output wire [1:0]  sd_ba,
    output wire [10:0] sd_addr,
    output wire [3:0]  sd_dqm,
    output wire [31:0] sd_dq_out,
    output wire        sd_dq_oe,
    input  wire [31:0] sd_dq_in
);

    localparam [1:0] OP_READ = 2'd0, OP_WRITE = 2'd1, OP_RMW = 2'd2;
    localparam [1:0] P_FILL = 2'd0, P_VERIFY = 2'd1, P_RMW = 2'd2, P_VERIFY2 = 2'd3;
    localparam [2:0] S_ISSUE = 3'd0, S_WDATA = 3'd1, S_RDATA = 3'd2, S_WB = 3'd3,
                     S_WBDATA = 3'd4, S_PRINT = 3'd5, S_PRINT_WAIT = 3'd6;

    reg         cmd_valid, wb_valid;
    reg  [1:0]  cmd_op;
    wire        cmd_ready, wnext, rvalid, wb_ready, init_done;
    wire [31:0] rdata;
    reg  [31:0] wdata;

    reg  [1:0]  phase;
    reg  [2:0]  state;
    reg  [17:0] group;
    reg  [2:0]  widx, ridx;
    reg  [31:0] seed;
    reg  [2:0]  lat;
    reg         round_led, swept;
    reg  [6:0]  pass_map;       // results shifted in from the top, one per round
    reg  [6:0]  pass_shown;     // pass_map as of the last rd_lat = 7 round: bit k = rd_lat k + 1 passed
    reg  [31:0] errors;
    reg  [1:0]  f_phase;
    reg  [20:0] f_addr;
    reg  [31:0] f_got, f_exp;

    wire [17:0] last_group = {{(18 - GROUP_BITS){1'b0}}, {GROUP_BITS{1'b1}}};
    wire [20:0] base   = {group, 3'b000};
    wire [2:0]  wb_off = {group[1:0], 1'b0};

    sdram_ctrl #(.INIT_WAIT(INIT_WAIT), .REF_INTERVAL(REF_INTERVAL)) ctrl (
        .clk(clk), .rst(rst), .rd_lat({1'b0, lat}),
        .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd_op(cmd_op), .cmd_addr(base),
        .wr_len(3'd7), .wdata(wdata), .wnext(wnext), .rvalid(rvalid), .rdata(rdata),
        .wb_valid(wb_valid), .wb_ready(wb_ready), .wb_off(wb_off), .wb_len(3'd1), .wb_skip(1'b0),
        .init_done(init_done),
        .sd_cke(sd_cke), .sd_cs_n(sd_cs_n), .sd_ras_n(sd_ras_n), .sd_cas_n(sd_cas_n),
        .sd_we_n(sd_we_n), .sd_ba(sd_ba), .sd_addr(sd_addr), .sd_dqm(sd_dqm),
        .sd_dq_out(sd_dq_out), .sd_dq_oe(sd_dq_oe), .sd_dq_in(sd_dq_in)
    );

    function [31:0] pat(input [20:0] a, input [31:0] s);
        pat = ({11'd0, a} ^ s) * 32'h9E3779B1;
    endfunction

    // Write-back data for address a.
    function [31:0] wbpat(input [20:0] a);
        wbpat = {8'hA5, 3'b000, a};
    endfunction

    // Expected word at offset ridx of the current group.
    wire [20:0] raddr   = base | ridx;
    wire [31:0] exp_now = (phase == P_VERIFY2 && ridx[2:1] == group[1:0]) ? wbpat(raddr) : pat(raddr, seed);

    always @(*) begin
        if (state == S_WB || state == S_WBDATA)
            wdata = wbpat(base | (wb_off + widx));
        else
            wdata = pat(base | widx, seed);
    end

    // --- report line ---------------------------------------------------------

    localparam LEN = 63;
    // Verilog strings have no \r escape, so the line ending is spelled out.
    localparam [8*LEN-1:0] TPL = {"lat=# err=######## ph=# addr=###### got=######## exp=########", 8'h0D, 8'h0A};
    wire [127:0] nibbles = {1'b0, lat, errors, 2'b00, f_phase, 3'b000, f_addr, f_got, f_exp};

    reg  [6:0]  pchar;          // character index into TPL
    reg  [4:0]  pnib;           // next nibble of the snapshot
    reg  [127:0] snap;
    reg         printing, print_done;
    wire [7:0]  tch  = TPL[8*(LEN - 1 - pchar) +: 8];
    wire [3:0]  nib  = snap[127 - 4*pnib -: 4];
    wire [7:0]  hex  = nib < 10 ? 8'h30 + nib : 8'h57 + nib;
    wire [7:0]  ch   = tch == "#" ? hex : tch;
    wire        tx_ready;

    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) tx (
        .clk(clk), .rst(rst), .data(ch), .valid(printing), .ready(tx_ready), .tx(uart_tx)
    );

    always @(posedge clk) begin
        print_done <= 1'b0;
        if (state == S_PRINT) begin
            snap     <= nibbles;
            pchar    <= 7'd0;
            pnib     <= 5'd0;
            printing <= 1'b1;
        end else if (printing && tx_ready) begin
            pchar <= pchar + 1'b1;
            if (tch == "#")
                pnib <= pnib + 1'b1;
            if (pchar == LEN - 1) begin
                printing   <= 1'b0;
                print_done <= 1'b1;
            end
        end
        if (rst) begin
            printing   <= 1'b0;
            print_done <= 1'b0;
            pchar      <= 7'd0;
        end
    end

    // --- test sequence -------------------------------------------------------

    always @(posedge clk) begin
        case (state)
        S_ISSUE: if (init_done) begin
            cmd_valid <= 1'b1;
            cmd_op    <= phase == P_FILL ? OP_WRITE : phase == P_RMW ? OP_RMW : OP_READ;
            widx      <= 3'd0;
            ridx      <= 3'd0;
            if (cmd_valid && cmd_ready) begin
                cmd_valid <= 1'b0;
                state     <= phase == P_FILL ? S_WDATA : S_RDATA;
            end
        end

        S_WDATA: if (wnext) begin
            widx <= widx + 3'd1;
            if (widx == 3'd7)
                next_group();
        end

        S_RDATA: if (rvalid) begin
            ridx <= ridx + 3'd1;
            if (rdata != exp_now) begin
                if (errors == 0) begin
                    f_phase <= phase;
                    f_addr  <= raddr;
                    f_got   <= rdata;
                    f_exp   <= exp_now;
                end
                if (errors != 32'hFFFF_FFFF)
                    errors <= errors + 1'b1;
            end
            if (ridx == 3'd7) begin
                if (phase == P_RMW) begin
                    widx     <= 3'd0;
                    wb_valid <= 1'b1;
                    state    <= S_WB;
                end else
                    next_group();
            end
        end

        S_WB: if (wb_ready) begin
            wb_valid <= 1'b0;
            state    <= S_WBDATA;
        end

        S_WBDATA: if (wnext) begin
            widx <= widx + 3'd1;
            if (widx == 3'd1)
                next_group();
        end

        // Print the round's line, then start the next round.
        S_PRINT: state <= S_PRINT_WAIT;

        S_PRINT_WAIT: if (print_done) begin
            errors    <= 32'd0;
            f_phase   <= 2'd0;
            f_addr    <= 21'd0;
            f_got     <= 32'd0;
            f_exp     <= 32'd0;
            pass_map <= {errors == 0, pass_map[6:1]};
            if (lat == 3'd7)
                pass_shown <= {errors == 0, pass_map[6:1]};
            if (lat == 3'd7)
                swept <= 1'b1;
            lat       <= lat == 3'd7 ? 3'd1 : lat + 3'd1;
            seed      <= seed + 32'h0101_0101;
            round_led <= ~round_led;
            state     <= S_ISSUE;
        end

        default: state <= S_ISSUE;
        endcase

        if (rst) begin
            state     <= S_ISSUE;
            phase     <= P_FILL;
            group     <= 18'd0;
            seed      <= 32'h1234_5678;
            lat       <= 3'd1;
            cmd_valid <= 1'b0;
            wb_valid  <= 1'b0;
            round_led <= 1'b0;
            swept     <= 1'b0;
            pass_map  <= 7'd0;
            pass_shown <= 7'd0;
            errors    <= 32'd0;
            f_phase   <= 2'd0;
            f_addr    <= 21'd0;
            f_got     <= 32'd0;
            f_exp     <= 32'd0;
        end
    end

    task next_group;
        begin
            state <= S_ISSUE;
            group <= group + 1'b1;
            if (group == last_group) begin
                group <= 18'd0;
                phase <= phase + 1'b1;
                if (phase == P_VERIFY2)
                    state <= S_PRINT;
            end
        end
    endtask

    reg [BEAT_BIT:0] beat = 0;
    always @(posedge clk) beat <= beat + 1'b1;

    assign led = swept ? ~pass_shown[5:0] : ~{lat, round_led, init_done, beat[BEAT_BIT]};

endmodule

`default_nettype wire
