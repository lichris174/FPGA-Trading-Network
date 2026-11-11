// uart_rx.v - 8N1 serial receiver. Each bit is sampled once, in its middle.
//
// The line is synchronised through two flip-flops, so a falling edge is
// seen two clocks late. The wait to the middle of the start bit is half a
// bit less those two clocks, so every bit is sampled at its middle; then
// each data bit and the stop bit one bit apart. CLKS_PER_BIT must be at
// least 8. valid pulses for one clock with the
// byte; a byte whose stop bit is low is dropped and sets frame_error.

`default_nettype none

module uart_rx #(
    parameter CLKS_PER_BIT = 32          // 64.8 MHz / 2 Mbaud = 32.4
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg  [7:0] data,
    output reg        valid,
    output reg        frame_error        // sticky
);

    reg  [1:0]  sync;
    reg         busy;
    reg  [3:0]  bits;                    // bits sampled so far: start, 8 data, stop
    reg  [15:0] count;
    reg  [7:0]  shift;
    wire        line = sync[1];
    localparam [15:0] HALF = CLKS_PER_BIT / 2 - 2;
    localparam [15:0] FULL = CLKS_PER_BIT - 1;

    always @(posedge clk) begin
        sync  <= {sync[0], rx};
        valid <= 1'b0;
        if (rst) begin
            sync        <= 2'b11;
            busy        <= 1'b0;
            frame_error <= 1'b0;
        end else if (!busy) begin
            if (!line) begin                         // start bit: wait to its middle
                busy  <= 1'b1;
                bits  <= 4'd0;
                count <= HALF;
            end
        end else if (count != 0)
            count <= count - 1'b1;
        else begin
            count <= FULL;
            bits  <= bits + 1'b1;
            if (bits == 0) begin
                if (line)                            // a glitch, not a start bit
                    busy <= 1'b0;
            end else if (bits < 9)
                shift <= {line, shift[7:1]};
            else begin                               // stop bit
                busy <= 1'b0;
                if (line) begin
                    data  <= shift;
                    valid <= 1'b1;
                end else
                    frame_error <= 1'b1;
            end
        end
    end

endmodule

`default_nettype wire
