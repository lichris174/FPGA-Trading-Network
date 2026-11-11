// book_levels.v
//
// Price levels of the hardware order book and the top 2 levels per side.
// The algorithm is golden/hw_book.py (HwBook._level and what it calls);
// this module must match it update for update.
//
// Each side has 4096 one-cent slots; slot k is price cfg_base + 100 k.
// The order table works out the slot and sends one update per change:
// upd_add = 1 adds upd_shares to the level, 0 takes them off.
//
// Storage
//   size RAM    8192 x 20 bits, address {side, slot}
//   bitmap RAM  128 x 64 bits, address {side, slot[11:6]}: bit slot[5:0]
//               is set while the level is not empty
//   summary     64 bits per side in registers: bit w is set while
//               bitmap word w is not zero
//
// The tracker keeps the best and second-best slot of each side with
// their sizes. A level that becomes non-empty can only push into the top
// 2. When a top level empties, the next worse level is found from the
// bitmap: the rest of the same word first, then the summary to jump to
// the nearest non-empty word. Bids get worse going down, asks going up.
//
// Timing: the RAMs register their read address and their output, so read
// data is used two clocks after the address is set. The new size is
// registered before the bitmap and tracker use it. An update takes 4
// clocks, up to 11 when a top level empties and the next one has to be
// found. upd_ready is low meanwhile. After reset both RAMs are cleared,
// which takes 8192 clocks.
//
// Debug: while dbg_en is high and no update is running, the size RAM
// reads dbg_addr ({side, slot}); dbg_data has the size two clocks later.
// No update starts meanwhile.
//
// A level that goes above 2^20 - 1 or below 0 sets the sticky flag
// level_overflow; the stored size wraps, as in the model.

`default_nettype none

module book_levels (
    input  wire        clk,
    input  wire        rst,            // synchronous, active high
    input  wire [31:0] cfg_base,       // price of slot 0

    input  wire        upd_valid,
    output wire        upd_ready,
    input  wire        upd_side,       // 1 = sell
    input  wire [11:0] upd_slot,
    input  wire        upd_add,
    input  wire [16:0] upd_shares,

    input  wire        dbg_en,          // debug: read the level RAM while the book is idle
    input  wire [12:0] dbg_addr,
    output wire [19:0] dbg_data,

    output wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price,  // 0 when there is no level
    output wire [19:0] bid1_size,  bid2_size,  ask1_size,  ask2_size,
    output reg         level_overflow
);

    localparam [3:0] S_CLEAR = 4'd0, S_IDLE = 4'd1, S_READ = 4'd2, S_APPLY = 4'd3,
                     S_SCAN_ADDR = 4'd4, S_SCAN_WAIT = 4'd5, S_SCAN_NEXT = 4'd6,
                     S_JUMP_WAIT = 4'd7, S_JUMP = 4'd8, S_SIZE_WAIT = 4'd9, S_SIZE = 4'd10,
                     S_TRACK = 4'd11;

    reg  [3:0]  state;

    // --- RAMs, synchronous read --------------------------------------------

    reg  [19:0] size_mem [0:8191];
    reg  [63:0] bm_mem   [0:127];
    reg  [12:0] size_raddr, size_waddr;
    reg  [6:0]  bm_raddr,   bm_waddr;
    reg  [19:0] size_rdata, size_wdata;
    reg  [63:0] bm_rdata,   bm_wdata;
    reg         size_we, bm_we;

    // A Gowin block RAM read of the address being written in the same clock
    // is undefined (and can disturb the stored word), while the simulator
    // returns the old data; so the FSM moves each read address away before
    // a write lands (S_APPLY, S_TRACK).
    // Each RAM reads only in the clocks whose result is used, never while
    // it is written.
    wire size_re = state == S_READ || state == S_SIZE_WAIT || (state == S_IDLE && dbg_en);
    wire bm_re   = state == S_READ || state == S_SCAN_WAIT || state == S_JUMP_WAIT;

    always @(posedge clk) begin
        if (size_re) size_rdata <= size_mem[size_raddr];
        if (bm_re)   bm_rdata   <= bm_mem[bm_raddr];
        if (size_we) size_mem[size_waddr] <= size_wdata;
        if (bm_we)   bm_mem[bm_waddr]     <= bm_wdata;
    end

    // --- tracker -------------------------------------------------------------

    reg         best_v [0:1], second_v [0:1];      // index: side
    reg  [11:0] best   [0:1], second   [0:1];
    reg  [19:0] best_sz[0:1], second_sz[0:1];
    reg  [63:0] summary[0:1];

    // a is better than b on this side
    function better(input side, input [11:0] a, input [11:0] b);
        better = side ? a < b : a > b;
    endfunction

    function [5:0] msb64(input [63:0] x);
        integer i;
        begin
            msb64 = 6'd0;
            for (i = 0; i < 64; i = i + 1)
                if (x[i]) msb64 = i[5:0];
        end
    endfunction

    function [5:0] lsb64(input [63:0] x);
        integer i;
        begin
            lsb64 = 6'd0;
            for (i = 63; i >= 0; i = i - 1)
                if (x[i]) lsb64 = i[5:0];
        end
    endfunction

    // --- the update in flight ------------------------------------------------

    reg         side;
    reg  [11:0] slot;
    reg         add;
    reg  [16:0] shares;
    reg  [11:0] from;           // scan: find the next level worse than this slot
    reg  [12:0] clear_addr;

    // Bit 20 is set when the sum passes 2^20 - 1 or the difference goes below 0.
    wire [20:0] raw     = add ? {1'b0, size_rdata} + {4'd0, shares} : {1'b0, size_rdata} - {4'd0, shares};
    wire [19:0] new_sz  = raw[19:0];
    wire        occupy  = size_rdata == 0 && new_sz != 0;
    wire        vacate  = size_rdata != 0 && new_sz == 0;

    // Registered in S_APPLY, used in S_TRACK.
    reg  [19:0] r_new;
    reg         r_occ, r_vac;
    reg  [63:0] r_word;
    wire [63:0] bit_     = 64'd1 << slot[5:0];
    wire [63:0] word_new = r_occ ? r_word | bit_ : r_word & ~bit_;

    // Scan masks: bits of the word worse than `from`, and words worse than its word.
    wire [63:0] below_bit  = (64'd1 << from[5:0]) - 64'd1;
    wire [63:0] above_bit  = ~((64'd2 << from[5:0]) - 64'd1);
    wire [63:0] below_word = (64'd1 << from[11:6]) - 64'd1;
    wire [63:0] above_word = ~((64'd2 << from[11:6]) - 64'd1);
    wire [63:0] here  = bm_rdata & (side ? above_bit : below_bit);
    wire [63:0] rest  = summary[side] & (side ? above_word : below_word);
    wire [5:0]  here_bit  = side ? lsb64(here) : msb64(here);
    wire [5:0]  rest_word = side ? lsb64(rest) : msb64(rest);
    wire [5:0]  word_bit  = side ? lsb64(bm_rdata) : msb64(bm_rdata);

    assign upd_ready = state == S_IDLE && !dbg_en;
    assign dbg_data  = size_rdata;

    always @(posedge clk) begin
        size_we <= 1'b0;
        bm_we   <= 1'b0;

        case (state)
        S_CLEAR: begin
            size_we    <= 1'b1;
            size_waddr <= clear_addr;
            size_wdata <= 20'd0;
            bm_we      <= 1'b1;
            bm_waddr   <= clear_addr[6:0];
            bm_wdata   <= 64'd0;
            clear_addr <= clear_addr + 1'b1;
            if (clear_addr == 13'h1FFF)
                state <= S_IDLE;
        end

        S_IDLE: if (dbg_en)
            size_raddr <= dbg_addr;
        else if (upd_valid) begin
            side       <= upd_side;
            slot       <= upd_slot;
            add        <= upd_add;
            shares     <= upd_shares;
            size_raddr <= {upd_side, upd_slot};
            bm_raddr   <= {upd_side, upd_slot[11:6]};
            state      <= S_READ;
        end

        S_READ: state <= S_APPLY;

        // size_rdata and bm_rdata hold the level and its bitmap word.
        S_APPLY: begin
            if (raw[20])
                level_overflow <= 1'b1;
            size_we    <= 1'b1;
            size_waddr <= {side, slot};
            size_wdata <= new_sz;
            size_raddr <= {~side, slot};           // never read the address being written
            r_new      <= new_sz;
            r_occ      <= occupy;
            r_vac      <= vacate;
            r_word     <= bm_rdata;
            state      <= S_TRACK;
        end

        // Bitmap, summary and tracker from the registered new size.
        S_TRACK: begin
            if (best_v[side] && best[side] == slot)
                best_sz[side] <= r_new;
            if (second_v[side] && second[side] == slot)
                second_sz[side] <= r_new;
            state <= S_IDLE;

            if (r_occ || r_vac) begin
                bm_we    <= 1'b1;
                bm_waddr <= {side, slot[11:6]};
                bm_wdata <= word_new;
                bm_raddr <= {~side, slot[11:6]};   // never read the address being written
                summary[side][slot[11:6]] <= word_new != 0;
            end

            if (r_occ) begin
                if (!best_v[side] || better(side, slot, best[side])) begin
                    best_v[side]    <= 1'b1;
                    best[side]      <= slot;
                    best_sz[side]   <= r_new;
                    second_v[side]  <= best_v[side];
                    second[side]    <= best[side];
                    second_sz[side] <= best_sz[side];
                end else if (!second_v[side] || better(side, slot, second[side])) begin
                    second_v[side]  <= 1'b1;
                    second[side]    <= slot;
                    second_sz[side] <= r_new;
                end
            end else if (r_vac && best_v[side] && best[side] == slot) begin
                // The second level moves up; find a new second below it.
                best_v[side]   <= second_v[side];
                best[side]     <= second[side];
                best_sz[side]  <= second_sz[side];
                second_v[side] <= 1'b0;
                from           <= second[side];
                if (second_v[side])
                    state <= S_SCAN_ADDR;
            end else if (r_vac && second_v[side] && second[side] == slot) begin
                second_v[side] <= 1'b0;
                from           <= best[side];
                state          <= S_SCAN_ADDR;
            end
        end

        // The bitmap write from S_TRACK lands at this edge, so the read starts after it.
        S_SCAN_ADDR: begin
            bm_raddr <= {side, from[11:6]};
            state    <= S_SCAN_WAIT;
        end

        S_SCAN_WAIT: state <= S_SCAN_NEXT;

        S_SCAN_NEXT: begin
            if (here != 0) begin
                second[side] <= {from[11:6], here_bit};
                size_raddr   <= {side, from[11:6], here_bit};
                state        <= S_SIZE_WAIT;
            end else if (rest != 0) begin
                // Jump to the nearest non-empty word, then take its best bit.
                from     <= {rest_word, 6'd0};
                bm_raddr <= {side, rest_word};
                state    <= S_JUMP_WAIT;
            end else
                state <= S_IDLE;                   // nothing worse: no second level
        end

        S_JUMP_WAIT: state <= S_JUMP;

        S_JUMP: begin                              // bm_rdata is the word found
            second[side] <= {from[11:6], word_bit};
            size_raddr   <= {side, from[11:6], word_bit};
            state        <= S_SIZE_WAIT;
        end

        S_SIZE_WAIT: state <= S_SIZE;

        S_SIZE: begin
            second_v[side]  <= 1'b1;
            second_sz[side] <= size_rdata;
            state           <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase

        if (rst) begin
            state          <= S_CLEAR;
            clear_addr     <= 13'd0;
            level_overflow <= 1'b0;
            best_v[0] <= 1'b0; best_v[1] <= 1'b0;
            second_v[0] <= 1'b0; second_v[1] <= 1'b0;
            summary[0] <= 64'd0; summary[1] <= 64'd0;
        end
    end

    // --- outputs -------------------------------------------------------------

    function [31:0] price(input v, input [11:0] s);
        price = v ? cfg_base + {14'd0, s, 6'd0} + {15'd0, s, 5'd0} + {18'd0, s, 2'd0} : 32'd0;   // base + 100 s
    endfunction

    assign bid1_price = price(best_v[0],   best[0]);
    assign bid2_price = price(second_v[0], second[0]);
    assign ask1_price = price(best_v[1],   best[1]);
    assign ask2_price = price(second_v[1], second[1]);
    assign bid1_size  = best_v[0]   ? best_sz[0]   : 20'd0;
    assign bid2_size  = second_v[0] ? second_sz[0] : 20'd0;
    assign ask1_size  = best_v[1]   ? best_sz[1]   : 20'd0;
    assign ask2_size  = second_v[1] ? second_sz[1] : 20'd0;

endmodule

`default_nettype wire
