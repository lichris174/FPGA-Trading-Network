// sdram_ctrl.v
//
// Controller for the 64 Mbit SDR SDRAM inside the GW2AR-18 package
// (Tang Nano 20K): 32-bit words, 4 banks x 2048 rows x 256 columns.
//
// Closed page: every operation opens one row and closes it again, so
// operations take a fixed number of clocks and banks never need tracking.
// Three operations, each on one row:
//
//   read   burst of 8 words starting at addr (the burst wraps inside the
//          aligned group of 8, so keep addr[2:0] = 0 to get word 0 first)
//   write  wr_len + 1 words (1 to 8) to addr, addr + 1, ...
//   rmw    read 8 words like read, keep the row open, then take one
//          write-back request: wb_len + 1 words at offsets wb_off,
//          wb_off + 1, ... inside the same group of 8, or wb_skip to
//          close the row without writing. This is the order book's
//          "read a hash bucket, update one entry" in one row activation.
//
// Word address: addr = {row[10:0], bank[1:0], col[7:0]}, so consecutive
// groups of 256 words rotate through the banks.
//
// Handshakes
//   cmd_valid / cmd_ready   take an operation (cmd_op, cmd_addr, wr_len)
//   wdata / wnext           write data: wdata must hold word 0 when the
//                           write starts; wnext is high in each cycle a
//                           word is taken, and the next word must be on
//                           wdata in the following cycle
//   rvalid / rdata          8 read words, one per cycle while rvalid is high
//   wb_valid / wb_ready     the write-back request of an rmw
//
// Refresh runs every REF_INTERVAL clocks between operations and has
// priority over new operations. After reset the controller runs the
// power-up sequence (INIT_WAIT clocks, precharge all, 8 refreshes, mode
// register) before cmd_ready first goes high.
//
// Timing parameters are in clocks; the defaults are for 100 MHz with
// a -6/-7 speed grade part at CAS latency 2. rd_lat (an input, so a
// board test can sweep it) is the number of clocks from issuing READ
// until the input register dq_in_r holds word 0: CL + 1 with the SDRAM
// clock in phase. The right value on hardware depends on the clock phase
// shift and the I/O delays, so it is measured, not assumed.

`default_nettype none

module sdram_ctrl #(
    parameter        CL           = 2,
    parameter        T_RCD        = 2,       // ACT to READ/WRITE
    parameter        T_RP         = 2,       // PRECHARGE to ACT
    parameter        T_RC         = 7,       // ACT to ACT, REF to ACT
    parameter        T_RFC        = 7,       // REF to anything
    parameter        T_WR         = 2,       // last write data to PRECHARGE
    parameter        T_MRD        = 2,       // mode register to anything
    parameter        INIT_WAIT    = 20000,   // 200 us power-up wait
    parameter        REF_INTERVAL = 780      // 64 ms / 8192 refreshes, rounded down
) (
    input  wire        clk,
    input  wire        rst,            // synchronous, active high
    input  wire [3:0]  rd_lat,         // 1 to 15; CL + 1 in simulation

    // Operations
    input  wire        cmd_valid,
    output wire        cmd_ready,
    input  wire [1:0]  cmd_op,         // OP_READ, OP_WRITE, OP_RMW
    input  wire [20:0] cmd_addr,
    input  wire [2:0]  wr_len,         // write: words - 1
    input  wire [31:0] wdata,
    output wire        wnext,
    output reg         rvalid,
    output reg  [31:0] rdata,
    input  wire        wb_valid,
    output wire        wb_ready,
    input  wire [2:0]  wb_off,
    input  wire [2:0]  wb_len,         // write-back: words - 1
    input  wire        wb_skip,
    output wire        init_done,

    // SDRAM pins; the top level makes the tristate DQ bus
    output wire        sd_cke,
    output wire        sd_cs_n,
    output wire        sd_ras_n,
    output wire        sd_cas_n,
    output wire        sd_we_n,
    output reg  [1:0]  sd_ba,
    output reg  [10:0] sd_addr,
    output reg  [3:0]  sd_dqm,
    output reg  [31:0] sd_dq_out,
    output reg         sd_dq_oe,
    input  wire [31:0] sd_dq_in
);

    localparam [1:0] OP_READ = 2'd0, OP_WRITE = 2'd1, OP_RMW = 2'd2;

    // {CS#, RAS#, CAS#, WE#}
    localparam [3:0] CMD_NOP   = 4'b0111,
                     CMD_ACT   = 4'b0011,
                     CMD_READ  = 4'b0101,
                     CMD_WRITE = 4'b0100,
                     CMD_PRE   = 4'b0010,
                     CMD_REF   = 4'b0001,
                     CMD_MRS   = 4'b0000;

    // Burst length 8, sequential, CAS latency CL, single-location writes (A9).
    localparam [2:0]  CL3  = CL;
    localparam [10:0] MODE = {1'b0, 1'b1, 2'b00, CL3, 1'b0, 3'b011};

    localparam [2:0] S_INIT  = 3'd0,
                     S_IDLE  = 3'd1,
                     S_RCD   = 3'd2,
                     S_RMW   = 3'd3,
                     S_WRITE = 3'd4;

    localparam        BL          = 8;
    localparam [15:0] INIT_RELOAD = INIT_WAIT - 1;
    localparam [15:0] REF_RELOAD  = REF_INTERVAL - 1;

    reg  [2:0]  state;
    reg  [3:0]  cmd;
    reg  [15:0] timer;          // clocks until the state may issue its next command
    reg  [3:0]  rc_timer;       // clocks until the next ACT or REF (tRC, tRFC)
    reg  [3:0]  init_step;
    reg         ready_r;
    reg  [15:0] ref_cnt;
    reg         ref_pend;

    reg  [1:0]  op;
    reg  [1:0]  bank;
    reg  [7:0]  col;
    reg  [2:0]  left;           // words still to write after the current one

    reg  [31:0] dq_in_r;
    reg  [7:0]  rd_mark;      // high for the 8 clocks after READ, one per burst word
    reg  [15:0] rd_sh;

    assign {sd_cs_n, sd_ras_n, sd_cas_n, sd_we_n} = cmd;
    assign sd_cke    = 1'b1;
    assign init_done = ready_r;
    assign cmd_ready = ready_r && state == S_IDLE && timer == 0 && rc_timer == 0 && !ref_pend;
    assign wb_ready  = state == S_RMW && timer == 0;
    assign wnext     = state == S_WRITE || (state == S_RCD && timer == 0 && op == OP_WRITE);

    always @(posedge clk) begin
        cmd      <= CMD_NOP;
        sd_dq_oe <= 1'b0;
        if (timer != 0)
            timer <= timer - 1'b1;
        if (rc_timer != 0)
            rc_timer <= rc_timer - 1'b1;

        // Read capture: word i of a burst is in dq_in_r rd_lat clocks after
        // the READ, which is when its mark reaches rd_sh[rd_lat - 1].
        dq_in_r <= sd_dq_in;
        rd_mark <= rd_mark >> 1;
        rd_sh   <= {rd_sh[14:0], rd_mark[0]};
        rvalid  <= rd_sh[rd_lat - 4'd1];
        rdata   <= dq_in_r;

        if (ref_cnt != 0)
            ref_cnt <= ref_cnt - 1'b1;
        else if (ready_r) begin
            ref_pend <= 1'b1;
            ref_cnt  <= REF_RELOAD;
        end

        case (state)
        S_INIT: if (timer == 0) begin
            // step 0: power-up wait done -> precharge all
            // steps 1-8: refresh
            // step 9: mode register
            init_step <= init_step + 1'b1;
            if (init_step == 0) begin
                cmd     <= CMD_PRE;
                sd_addr <= 11'h400;              // A10: all banks
                timer   <= T_RP - 1;
            end else if (init_step <= 8) begin
                cmd   <= CMD_REF;
                timer <= T_RFC - 1;
            end else begin
                cmd     <= CMD_MRS;
                sd_ba   <= 2'd0;
                sd_addr <= MODE;
                timer   <= T_MRD - 1;
                sd_dqm  <= 4'h0;
                ready_r <= 1'b1;
                ref_cnt <= REF_RELOAD;
                state   <= S_IDLE;
            end
        end

        S_IDLE: if (timer == 0 && rc_timer == 0 && ready_r) begin
            if (ref_pend) begin
                cmd      <= CMD_REF;
                ref_pend <= 1'b0;
                timer    <= T_RFC - 1;
                rc_timer <= T_RFC - 1;
            end else if (cmd_valid) begin
                cmd      <= CMD_ACT;
                {bank, col} <= cmd_addr[9:0];
                sd_ba    <= cmd_addr[9:8];
                sd_addr  <= cmd_addr[20:10];
                op       <= cmd_op;
                left     <= wr_len;
                timer    <= T_RCD - 1;
                rc_timer <= T_RC - 1;
                state    <= S_RCD;
            end
        end

        S_RCD: if (timer == 0) begin
            if (op == OP_WRITE) begin
                state <= S_WRITE;
                write_word();
            end else begin
                cmd     <= CMD_READ;
                sd_ba   <= bank;
                sd_addr <= {op == OP_READ, 2'b00, col};   // A10: auto precharge
                rd_mark <= 8'hFF;
                if (op == OP_READ) begin
                    // Auto precharge starts after the burst; the next ACT
                    // waits for it (rc_timer already covers tRC).
                    timer <= BL + T_RP - 1;
                    state <= S_IDLE;
                end else begin
                    // Let the burst clear the bus before any write-back.
                    timer <= BL + CL;
                    state <= S_RMW;
                end
            end
        end

        S_RMW: if (timer == 0 && wb_valid) begin
            if (wb_skip) begin
                cmd     <= CMD_PRE;
                sd_ba   <= bank;
                sd_addr <= 11'h000;
                timer   <= T_RP - 1;
                state   <= S_IDLE;
            end else begin
                col   <= {col[7:3], wb_off};
                left  <= wb_len;
                state <= S_WRITE;
            end
        end

        S_WRITE: begin
            write_word();
        end

        default: state <= S_IDLE;
        endcase

        if (rst) begin
            state     <= S_INIT;
            cmd       <= CMD_NOP;
            timer     <= INIT_RELOAD;
            rc_timer  <= 4'd0;
            init_step <= 4'd0;
            ready_r   <= 1'b0;
            ref_cnt   <= 16'd0;
            ref_pend  <= 1'b0;
            sd_dqm    <= 4'hF;
            sd_dq_oe  <= 1'b0;
            rd_mark   <= 8'h00;
            rd_sh     <= 16'd0;
            rvalid    <= 1'b0;
        end
    end

    // One WRITE command with the word on wdata; the last one of an
    // operation auto-precharges, and the next ACT waits tWR + tRP.
    task write_word;
        begin
            cmd       <= CMD_WRITE;
            sd_ba     <= bank;
            sd_addr   <= {left == 0, 2'b00, col};
            sd_dq_out <= wdata;
            sd_dq_oe  <= 1'b1;
            if (op == OP_RMW)
                col <= {col[7:3], col[2:0] + 3'd1};     // stay inside the group of 8
            else
                col <= col + 1'b1;
            left <= left - 1'b1;
            if (left == 0) begin
                timer <= T_WR + T_RP - 1;
                state <= S_IDLE;
            end else
                state <= S_WRITE;
        end
    endtask

endmodule

`default_nettype wire
