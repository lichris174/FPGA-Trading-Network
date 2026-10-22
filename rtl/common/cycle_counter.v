// cycle_counter.v
//
// Free-running cycle counter with synchronous reset and enable.
// Counts up by one on every rising clock edge while en is high,
// holds its value while en is low, and wraps to zero after the
// maximum value. Later this becomes the latency stopwatch that
// measures cycles from message in to decision out.

`default_nettype none

module cycle_counter #(
    parameter WIDTH = 32
) (
    input  wire             clk,
    input  wire             rst,   // synchronous, active high
    input  wire             en,
    output reg  [WIDTH-1:0] count
);

    always @(posedge clk) begin
        if (rst)
            count <= {WIDTH{1'b0}};
        else if (en)
            count <= count + 1'b1;
    end

endmodule

`default_nettype wire
