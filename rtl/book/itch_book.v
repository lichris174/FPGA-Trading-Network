// itch_book.v
//
// ITCH bytes in, order book out: itch_parser -> FIFO -> order_book.
//
// The parser takes a byte every clock and can't be held off, so the book
// messages it decodes (A, F, E, C, X, D, U) wait in a FIFO while the book
// works through them at about 20 clocks each. Other messages are not
// queued. With one byte per clock both AAPL days peak at 21 waiting
// messages (scripts/hw_book_check.py). The FIFO counts messages, not
// work, so a run of slow replaces followed by fast deletes can queue
// more than that; block RAM is 512 entries deep at this width anyway, so
// the FIFO holds 513 at no extra cost. If it is ever full the message is
// dropped and fifo_overflow is set, and fifo_peak shows the deepest it got.
//
// idle is high when the FIFO is empty and the book has applied every
// message, so the top 2 outputs are up to date. The parser may still be
// in the middle of a message.

`default_nettype none

module itch_book #(
    parameter BUCKET_BITS  = 18,
    parameter INIT_WAIT    = 20000,
    parameter REF_INTERVAL = 500
) (
    input  wire        clk,
    input  wire        rst,             // synchronous, active high

    input  wire [7:0]  in_data,
    input  wire        in_valid,
    output wire        in_ready,
    input  wire        filter_en,
    input  wire [15:0] cfg_locate,

    input  wire [3:0]  rd_lat,          // SDRAM read latency, 3 on the Tang Nano 20K
    input  wire [31:0] cfg_base,        // price of level slot 0

    output wire [31:0] bid1_price, bid2_price, ask1_price, ask2_price,
    output wire [19:0] bid1_size,  bid2_size,  ask1_size,  ask2_size,
    output wire        ready,           // SDRAM up and the table cleared
    output wire        idle,
    output reg         parse_error,     // sticky: a message with the wrong length
    output wire        fifo_overflow,
    output wire [10:0] fifo_peak,
    output wire        stash_full, unknown_ref, bad_reduce, shares_overflow, level_overflow,

    output wire        sd_cke, sd_cs_n, sd_ras_n, sd_cas_n, sd_we_n,
    output wire [1:0]  sd_ba,
    output wire [10:0] sd_addr,
    output wire [3:0]  sd_dqm,
    output wire [31:0] sd_dq_out,
    output wire        sd_dq_oe,
    input  wire [31:0] sd_dq_in
);

    // --- parser ----------------------------------------------------------------

    wire        msg_valid, msg_error, side_buy;
    wire [7:0]  msg_type;
    wire [31:0] shares, price;
    /* verilator lint_off UNUSEDSIGNAL */    // the book doesn't need these, nor reference bits above 50
    wire [63:0] order_ref, new_order_ref;
    wire [15:0] stock_locate;
    wire [47:0] timestamp;
    wire [7:0]  event_code, trading_state;
    /* verilator lint_on UNUSEDSIGNAL */

    itch_parser parser (
        .clk(clk), .rst(rst),
        .in_data(in_data), .in_valid(in_valid), .in_ready(in_ready),
        .filter_en(filter_en), .cfg_locate(cfg_locate),
        .msg_valid(msg_valid), .msg_error(msg_error), .msg_type(msg_type),
        .stock_locate(stock_locate), .timestamp(timestamp),
        .order_ref(order_ref), .new_order_ref(new_order_ref), .side_buy(side_buy),
        .shares(shares), .price(price), .event_code(event_code), .trading_state(trading_state)
    );

    always @(posedge clk)
        if (rst)            parse_error <= 1'b0;
        else if (msg_error) parse_error <= 1'b1;

    // --- FIFO: book messages only ----------------------------------------------

    // References above bit 50 are never used by the book (see order_book.v).
    localparam W = 8 + 51 + 51 + 1 + 32 + 32;

    wire is_book = msg_type == "A" || msg_type == "F" || msg_type == "E" || msg_type == "C" ||
                   msg_type == "X" || msg_type == "D" || msg_type == "U";

    wire         q_valid, q_pop;
    wire [W-1:0] q_data;
    /* verilator lint_off UNUSEDSIGNAL */
    wire [9:0]   ram_level;
    wire [10:0]  level;
    /* verilator lint_on UNUSEDSIGNAL */

    fifo #(.WIDTH(W), .ADDR_BITS(9)) queue (
        .clk(clk), .rst(rst),
        .push(msg_valid && is_book),
        .din({msg_type, order_ref[50:0], new_order_ref[50:0], side_buy, shares, price}),
        .out_valid(q_valid), .dout(q_data), .pop(q_pop),
        .overflow(fifo_overflow), .ram_level(ram_level), .level(level), .peak(fifo_peak)
    );

    // --- order book ------------------------------------------------------------

    wire book_ready, book_idle;
    assign q_pop = q_valid && book_ready;
    assign idle  = !q_valid && level == 0 && book_idle;

    order_book #(.BUCKET_BITS(BUCKET_BITS), .INIT_WAIT(INIT_WAIT), .REF_INTERVAL(REF_INTERVAL)) book (
        .clk(clk), .rst(rst), .rd_lat(rd_lat), .cfg_base(cfg_base),
        .msg_valid(q_valid), .msg_ready(book_ready),
        .msg_type(q_data[W-1 -: 8]),
        .msg_ref({13'd0, q_data[W-9 -: 51]}),
        .msg_new_ref({13'd0, q_data[W-60 -: 51]}),
        .msg_side_buy(q_data[64]), .msg_shares(q_data[63:32]), .msg_price(q_data[31:0]),
        .bid1_price(bid1_price), .bid2_price(bid2_price), .ask1_price(ask1_price), .ask2_price(ask2_price),
        .bid1_size(bid1_size), .bid2_size(bid2_size), .ask1_size(ask1_size), .ask2_size(ask2_size),
        .idle(book_idle), .stash_full(stash_full), .unknown_ref(unknown_ref), .bad_reduce(bad_reduce),
        .shares_overflow(shares_overflow), .level_overflow(level_overflow),
        .sd_cke(sd_cke), .sd_cs_n(sd_cs_n), .sd_ras_n(sd_ras_n), .sd_cas_n(sd_cas_n), .sd_we_n(sd_we_n),
        .sd_ba(sd_ba), .sd_addr(sd_addr), .sd_dqm(sd_dqm),
        .sd_dq_out(sd_dq_out), .sd_dq_oe(sd_dq_oe), .sd_dq_in(sd_dq_in)
    );

    // The book takes messages once the SDRAM is up and the table is cleared.
    reg seen_ready;
    always @(posedge clk)
        if (rst)             seen_ready <= 1'b0;
        else if (book_ready) seen_ready <= 1'b1;
    assign ready = seen_ready;

endmodule

`default_nettype wire
