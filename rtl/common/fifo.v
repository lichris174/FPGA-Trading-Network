// fifo.v
//
// First-word-fall-through FIFO on a RAM with a registered read, so it can
// map to block RAM. dout holds the oldest entry while out_valid is high;
// pop takes it. A push when the FIFO is full is dropped and sets the
// sticky flag overflow.
//
// One read is in flight at a time, so after a pop the next entry shows
// two clocks later. That is plenty for a consumer that takes 20 clocks
// per entry. `level` counts every entry held, including the one on dout,
// and `peak` is the largest level since reset.

`default_nettype none

module fifo #(
    parameter WIDTH = 8,
    parameter ADDR_BITS = 5           // 2^ADDR_BITS entries in the RAM, plus one on dout
) (
    input  wire             clk,
    input  wire             rst,      // synchronous, active high

    input  wire             push,
    input  wire [WIDTH-1:0] din,

    output reg              out_valid,
    output reg  [WIDTH-1:0] dout,
    input  wire             pop,      // only while out_valid is high

    output reg              overflow,
    output wire [ADDR_BITS:0]   ram_level,
    output wire [ADDR_BITS+1:0] level,
    output reg  [ADDR_BITS+1:0] peak
);

    reg  [WIDTH-1:0]   mem [0:(1 << ADDR_BITS) - 1];
    reg  [WIDTH-1:0]   rdata;
    reg  [ADDR_BITS:0] wptr, rptr;     // one extra bit tells full from empty
    reg                fetch;          // a read was issued last clock

    assign ram_level = wptr - rptr;
    wire   full      = ram_level == (1 << ADDR_BITS);
    wire   take      = !fetch && ram_level != 0 && (!out_valid || pop);
    wire   write     = push && !full;
    assign level     = {1'b0, ram_level} + {{(ADDR_BITS + 1){1'b0}}, out_valid | fetch};

    always @(posedge clk) begin
        if (write)
            mem[wptr[ADDR_BITS-1:0]] <= din;
        if (take)
            rdata <= mem[rptr[ADDR_BITS-1:0]];
    end

    always @(posedge clk) begin
        if (rst) begin
            wptr      <= 0;
            rptr      <= 0;
            fetch     <= 1'b0;
            out_valid <= 1'b0;
            overflow  <= 1'b0;
            peak      <= 0;
        end else begin
            if (write)
                wptr <= wptr + 1'b1;
            if (push && full)
                overflow <= 1'b1;
            if (take)
                rptr <= rptr + 1'b1;
            fetch <= take;
            if (fetch) begin
                dout      <= rdata;
                out_valid <= 1'b1;
            end else if (pop)
                out_valid <= 1'b0;
            if (level > peak)
                peak <= level;
        end
    end

endmodule

`default_nettype wire
