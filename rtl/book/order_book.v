// order_book.v
//
// The hardware order book: decoded ITCH messages in, the top 2 levels of
// each side out. The algorithm is golden/hw_book.py (HwBook.apply); this
// module must match it message for message, including the error flags.
//
// Parts
//   order table   hash table in SDRAM through sdram_ctrl: bucket = low
//                 BUCKET_BITS of the order reference, 4 ways of 64 bits,
//                 way w = words 2w (entry [31:0]) and 2w + 1 ([63:32]):
//                   [63:31] tag (reference bits above the bucket)
//                   [30] side (1 = sell)  [29] inside the level window
//                   [28:17] level slot    [16:0] shares, 0 = empty way
//   stash         16 orders in registers for full buckets, matched on
//                 reference bits [50:0], searched along with the bucket
//   book_levels   level sizes, bitmap and the top-2 tracker
//
// Each book message is one read-modify-write of its bucket: read the 4
// ways, then write back the changed entry (2 words for a new order, the
// low word for a reduce) or close the row unchanged. U is two of them:
// reduce the old order to nothing, then add the new one on its side. The
// level update goes to book_levels at the end and runs while the next
// message reads its bucket.
//
// Throughput: the next message waits only on the SDRAM, not on this
// module. The bucket read is asked for in the clock the message is taken,
// the decision is made as the last word arrives, and the next message is
// taken in the clock the write-back ends.
//
// After reset the SDRAM powers up and every bucket is cleared (2^BUCKET_BITS
// writes of 8 words, about 53 ms at 64.8 MHz with 2^18), then msg_ready
// first goes high. idle is high when nothing is in flight, so the top 2
// outputs include every message taken so far.
//
// Message fields are the parser's: msg_shares is the order's shares for
// A, F and U and the executed or cancelled shares for E, C and X; for U,
// msg_ref is the original reference and msg_new_ref the new one. Other
// message types are taken and ignored.
//
// Sticky flags, as in the model: stash_full (an order was dropped),
// unknown_ref, bad_reduce (more shares than the order has; all of them
// are taken), shares_overflow (shares above 2^17 - 1 are masked),
// level_overflow (from book_levels).

`default_nettype none

module order_book #(
    parameter BUCKET_BITS  = 18,
    parameter INIT_WAIT    = 20000,
    parameter REF_INTERVAL = 500
) (
    input  wire        clk,
    input  wire        rst,             // synchronous, active high
    input  wire [3:0]  rd_lat,          // SDRAM read latency, 3 on the Tang Nano 20K
    input  wire [31:0] cfg_base,        // price of level slot 0

    input  wire        msg_valid,
    output wire        msg_ready,
    input  wire [7:0]  msg_type,
    input  wire [63:0] msg_ref,
    input  wire [63:0] msg_new_ref,
    input  wire        msg_side_buy,
    input  wire [31:0] msg_shares,
    input  wire [31:0] msg_price,

    output wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price,
    output wire [19:0] bid1_size,  bid2_size,  ask1_size,  ask2_size,
    output wire        idle,
    output reg         stash_full,
    output reg         unknown_ref,
    output reg         bad_reduce,
    output reg         shares_overflow,
    output wire        level_overflow,
    input  wire        dbg_en,          // debug: read the level RAM while the book is idle
    input  wire [12:0] dbg_addr,
    output wire [19:0] dbg_data,

    output wire        sd_cke, sd_cs_n, sd_ras_n, sd_cas_n, sd_we_n,
    output wire [1:0]  sd_ba,
    output wire [10:0] sd_addr,
    output wire [3:0]  sd_dqm,
    output wire [31:0] sd_dq_out,
    output wire        sd_dq_oe,
    input  wire [31:0] sd_dq_in
);

    localparam BB = BUCKET_BITS;
    localparam [1:0] OP_WRITE = 2'd1, OP_RMW = 2'd2;
    localparam [3:0] S_INIT = 4'd0, S_CLEAR = 4'd1, S_CLEAR_DATA = 4'd2, S_IDLE = 4'd3,
                     S_ISSUE = 4'd4, S_RDATA = 4'd5, S_WB = 4'd6, S_WDATA = 4'd7, S_LEVEL = 4'd8;

    reg  [3:0]  state;

    // --- SDRAM controller ------------------------------------------------------

    reg         cmd_valid, wb_valid, wb_skip;
    reg  [1:0]  cmd_op;
    reg  [2:0]  wb_off, wb_len;
    wire        cmd_ready, wnext, rvalid, wb_ready, init_done;
    wire [31:0] rdata;
    reg  [31:0] wdata;
    reg  [17:0] bucket;

    sdram_ctrl #(.INIT_WAIT(INIT_WAIT), .REF_INTERVAL(REF_INTERVAL)) sdram (
        .clk(clk), .rst(rst), .rd_lat(rd_lat),
        .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd_op(cmd_op), .cmd_addr({bucket, 3'b000}),
        .wr_len(3'd7), .wdata(wdata), .wnext(wnext), .rvalid(rvalid), .rdata(rdata),
        .wb_valid(wb_valid), .wb_ready(wb_ready), .wb_off(wb_off), .wb_len(wb_len), .wb_skip(wb_skip),
        .init_done(init_done),
        .sd_cke(sd_cke), .sd_cs_n(sd_cs_n), .sd_ras_n(sd_ras_n), .sd_cas_n(sd_cas_n), .sd_we_n(sd_we_n),
        .sd_ba(sd_ba), .sd_addr(sd_addr), .sd_dqm(sd_dqm),
        .sd_dq_out(sd_dq_out), .sd_dq_oe(sd_dq_oe), .sd_dq_in(sd_dq_in)
    );

    // --- levels ----------------------------------------------------------------

    reg         upd_valid, upd_side, upd_add;
    reg  [11:0] upd_slot;
    reg  [16:0] upd_shares;
    wire        upd_ready;

    book_levels levels (
        .clk(clk), .rst(rst), .cfg_base(cfg_base),
        .upd_valid(upd_valid), .upd_ready(upd_ready), .upd_side(upd_side), .upd_slot(upd_slot),
        .upd_add(upd_add), .upd_shares(upd_shares),
        .dbg_en(dbg_en), .dbg_addr(dbg_addr), .dbg_data(dbg_data),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .level_overflow(level_overflow)
    );

    // --- the message in flight -------------------------------------------------

    reg         insert;         // add an order; otherwise reduce one
    reg         take_all;       // reduce: D, or the first half of U
    reg         second_half;    // U: the add still has to follow
    /* verilator lint_off UNUSEDSIGNAL */    // only bits [50:0] of a reference are used
    reg  [63:0] key;            // reference of the order this operation is about
    reg  [63:0] new_ref;
    /* verilator lint_on UNUSEDSIGNAL */
    reg         side;           // insert: 1 = sell
    reg  [31:0] shares, price;
    reg  [31:0] words [0:7];
    reg  [2:0]  ridx, widx;
    reg  [63:0] wb_entry;
    reg         level_pending;
    reg  [BB:0] clear_count;

    wire [32:0] key_tag = key[BB + 32:BB];

    // Level slot of `price` in three registered steps; the bucket read takes
    // longer. d / 100 is d * 5243 >> 19, exact for every d in the window.
    reg  [32:0] p_d;
    reg  [18:0] p_d2;
    reg         p_range;
    /* verilator lint_off UNUSEDSIGNAL */
    reg  [31:0] p_q;
    /* verilator lint_on UNUSEDSIGNAL */
    reg  [11:0] q_slot;
    reg         in_win;
    always @(posedge clk) begin
        p_d     <= {1'b0, price} - {1'b0, cfg_base};
        p_q     <= p_d[18:0] * 13'd5243;
        p_d2    <= p_d[18:0];
        p_range <= !p_d[32] && p_d < 33'd409600;
        q_slot  <= p_q[30:19];
        in_win  <= p_range && {1'b0, p_q[30:19], 6'd0} + {2'b0, p_q[30:19], 5'd0} + {5'b0, p_q[30:19], 2'd0} == p_d2;
    end
    wire [16:0] ins_shares = shares[16:0];

    // Each way is worked out as its words arrive: from the low word (word 2w)
    // whether it is used and what a reduce would take and leave, from the
    // high word whether the tag matches. Way 3's high word is the last one,
    // so its tag is compared in the clock of the decision.
    reg  [3:0]  w_used, w_too;
    reg  [2:0]  w_match;
    reg  [16:0] w_take [0:3];
    reg  [31:0] w_left [0:3];
    wire        low_too   = !take_all && shares > {15'd0, rdata[16:0]};
    wire [16:0] low_take  = take_all || low_too ? rdata[16:0] : shares[16:0];
    wire        hi_match  = {rdata, words[{ridx[2:1], 1'b0}][31]} == key_tag;
    wire [3:0]  used      = w_used;
    wire [3:0]  match     = {w_used[3] && hi_match, w_match};
    wire [1:0]  free_way  = !used[0] ? 2'd0 : !used[1] ? 2'd1 : !used[2] ? 2'd2 : 2'd3;
    wire [1:0]  match_way = match[0] ? 2'd0 : match[1] ? 2'd1 : match[2] ? 2'd2 : 2'd3;
    wire [13:0] hit_info  = words[{match_way, 1'b0}][30:17];    // side, window flag, slot

    // --- stash -----------------------------------------------------------------

    reg  [15:0] st_v;
    reg  [50:0] st_ref [0:15];
    reg  [30:0] st_ent [0:15];      // entry bits [30:0]: side, in_win, slot, shares
    reg         st_hit;
    reg  [3:0]  st_idx;

    reg  [3:0]  st_free;
    reg         st_has_free;
    integer     f, s;
    always @(*) begin
        st_free     = 4'd0;
        st_has_free = 1'b0;
        for (f = 15; f >= 0; f = f - 1)
            if (!st_v[f]) begin
                st_free     = f[3:0];
                st_has_free = 1'b1;
            end
    end

    // The stash entry found by the lookup, worked out while the bucket is read.
    reg  [30:0] st_cur;
    reg         st_too;
    reg  [16:0] st_take;
    always @(posedge clk) begin
        st_cur  <= st_ent[st_idx];
        st_too  <= !take_all && shares > {15'd0, st_cur[16:0]};
        st_take <= take_all || (!take_all && shares > {15'd0, st_cur[16:0]}) ? st_cur[16:0] : shares[16:0];
    end

    // Side, window flag and slot of the entry being reduced, from a way or the stash.
    wire [13:0] cur      = match != 0 ? hit_info : st_cur[30:17];
    wire        too_many = match != 0 ? w_too[match_way] : st_too;
    wire [16:0] take     = match != 0 ? w_take[match_way] : st_take;

    assign msg_ready = state == S_IDLE;
    assign idle      = state == S_IDLE && upd_ready && !upd_valid;

    always @(*) begin
        if (state == S_CLEAR || state == S_CLEAR_DATA)
            wdata = 32'd0;
        else
            wdata = widx == 3'd0 ? wb_entry[31:0] : wb_entry[63:32];
    end

    always @(posedge clk) begin
        if (upd_valid && upd_ready)
            upd_valid <= 1'b0;

        case (state)
        S_INIT: if (init_done) state <= S_CLEAR;

        // Write 8 zero words to every bucket.
        S_CLEAR: begin
            cmd_valid <= 1'b1;
            cmd_op    <= OP_WRITE;
            if (cmd_valid && cmd_ready) begin
                cmd_valid <= 1'b0;
                widx      <= 3'd0;
                state     <= S_CLEAR_DATA;
            end
        end

        S_CLEAR_DATA: if (wnext) begin
            widx <= widx + 3'd1;
            if (widx == 3'd7) begin
                bucket      <= bucket + 1'b1;
                clear_count <= clear_count + 1'b1;
                state       <= clear_count == (1 << BB) - 1 ? S_IDLE : S_CLEAR;
            end
        end

        S_IDLE: if (msg_valid) begin
            insert      <= msg_type == "A" || msg_type == "F";
            take_all    <= msg_type == "D" || msg_type == "U";
            second_half <= msg_type == "U";
            key         <= msg_ref;
            new_ref     <= msg_new_ref;
            side        <= !msg_side_buy;
            shares      <= msg_shares;
            price       <= msg_price;
            if (msg_type == "A" || msg_type == "F" || msg_type == "E" || msg_type == "C" ||
                msg_type == "X" || msg_type == "D" || msg_type == "U") begin
                // Ask for the bucket now; S_ISSUE looks in the stash while it is taken.
                bucket    <= {{(18 - BB){1'b0}}, msg_ref[BB - 1:0]};
                cmd_valid <= 1'b1;
                cmd_op    <= OP_RMW;
                state     <= S_ISSUE;
            end
        end

        S_ISSUE: begin
            if (insert && shares[31:17] != 0)
                shares_overflow <= 1'b1;
            bucket    <= {{(18 - BB){1'b0}}, key[BB - 1:0]};
            cmd_valid <= 1'b1;
            cmd_op    <= OP_RMW;
            ridx      <= 3'd0;
            // Stash lookup, while the bucket is read.
            st_hit    <= 1'b0;
            for (s = 0; s < 16; s = s + 1)
                if (st_v[s] && st_ref[s] == key[50:0]) begin
                    st_hit <= 1'b1;
                    st_idx <= s[3:0];
                end
            if (cmd_valid && cmd_ready) begin
                cmd_valid <= 1'b0;
                state     <= S_RDATA;
            end
        end

        S_RDATA: if (rvalid) begin
            words[ridx] <= rdata;
            ridx        <= ridx + 3'd1;
            if (!ridx[0]) begin
                w_used[ridx[2:1]] <= rdata[16:0] != 0;
                w_too[ridx[2:1]]  <= low_too;
                w_take[ridx[2:1]] <= low_take;
                w_left[ridx[2:1]] <= rdata - {15'd0, low_take};
            end else if (ridx != 3'd7)
                w_match[ridx[2:1]] <= w_used[ridx[2:1]] && hi_match;
            if (ridx == 3'd7)
                decide();
        end

        S_WB: if (wb_ready) begin
            wb_valid <= 1'b0;
            if (wb_skip)
                finish();
            else
                state <= S_WDATA;
        end

        S_WDATA: if (wnext) begin
            widx <= widx + 3'd1;
            if (widx == wb_len)
                finish();
        end

        // Only while book_levels is still busy with the previous update.
        S_LEVEL: finish();

        default: state <= S_IDLE;
        endcase

        if (rst) begin
            state           <= S_INIT;
            cmd_valid       <= 1'b0;
            wb_valid        <= 1'b0;
            upd_valid       <= 1'b0;
            level_pending   <= 1'b0;
            bucket          <= 18'd0;
            clear_count     <= {(BB + 1){1'b0}};
            widx            <= 3'd0;
            st_v            <= 16'd0;
            stash_full      <= 1'b0;
            unknown_ref     <= 1'b0;
            bad_reduce      <= 1'b0;
            shares_overflow <= 1'b0;
        end
    end

    // Look at the bucket (and the stash), choose what to write back and
    // which level update follows. Runs in the clock the last word arrives.
    task decide;
        begin
            wb_valid      <= 1'b1;
            wb_skip       <= 1'b1;
            level_pending <= 1'b0;
            widx          <= 3'd0;
            upd_side      <= side;
            upd_slot      <= q_slot;
            upd_add       <= insert;
            upd_shares    <= ins_shares;
            state         <= S_WB;

            if (insert) begin
                wb_entry      <= {key_tag, side, in_win, in_win ? q_slot : 12'd0, ins_shares};
                level_pending <= in_win;
                if (used != 4'hF) begin
                    wb_skip <= 1'b0;
                    wb_off  <= {free_way, 1'b0};
                    wb_len  <= 3'd1;
                end else if (st_has_free) begin
                    st_v[st_free]   <= 1'b1;
                    st_ref[st_free] <= key[50:0];
                    st_ent[st_free] <= {side, in_win, in_win ? q_slot : 12'd0, ins_shares};
                end else begin
                    stash_full    <= 1'b1;
                    level_pending <= 1'b0;
                end
            end else if (match != 0 || st_hit) begin
                if (too_many)
                    bad_reduce <= 1'b1;
                level_pending <= cur[12];
                upd_side      <= cur[13];
                upd_slot      <= cur[11:0];
                upd_shares    <= take;
                side          <= cur[13];          // U: the new order keeps this side
                if (match != 0) begin
                    wb_skip  <= 1'b0;
                    wb_off   <= {match_way, 1'b0};
                    wb_len   <= 3'd0;
                    wb_entry <= {32'd0, w_left[match_way]};
                end else begin
                    st_ent[st_idx][16:0] <= st_cur[16:0] - st_take;
                    if (st_cur[16:0] == st_take)
                        st_v[st_idx] <= 1'b0;
                end
            end else begin
                unknown_ref <= 1'b1;
                second_half <= 1'b0;               // U of an unknown order adds nothing
            end
        end
    endtask

    // The write-back is done: hand the level update to book_levels (it
    // finishes while the next message reads its bucket), then go on to the
    // second half of U or the next message. Wait in S_LEVEL while
    // book_levels is still busy.
    task finish;
        begin
            if (level_pending && (!upd_ready || upd_valid))
                state <= S_LEVEL;
            else begin
                if (level_pending) begin
                    upd_valid     <= 1'b1;
                    level_pending <= 1'b0;
                end
                if (second_half) begin
                    second_half <= 1'b0;
                    insert      <= 1'b1;
                    take_all    <= 1'b0;
                    key         <= new_ref;
                    state       <= S_ISSUE;
                end else
                    state <= S_IDLE;
            end
        end
    endtask

endmodule

`default_nettype wire
