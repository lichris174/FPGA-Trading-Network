// uart_tx.v - 8N1 serial transmitter, one byte per valid/ready handshake.

`default_nettype none

module uart_tx #(
    parameter CLKS_PER_BIT = 562         // 64.8 MHz / 115200
) (
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] data,
    input  wire       valid,
    output wire       ready,
    output reg        tx
);

    reg [9:0]  shift;                    // stop, data[7:0], start; sent LSB first
    reg [3:0]  bits;                     // bits left to send
    reg [15:0] count;

    assign ready = bits == 0;

    always @(posedge clk) begin
        if (rst) begin
            tx    <= 1'b1;
            bits  <= 4'd0;
            count <= 16'd0;
        end else if (bits == 0) begin
            if (valid) begin
                shift <= {1'b1, data, 1'b0};
                bits  <= 4'd10;
                count <= 16'd0;
            end
        end else if (count == 0) begin
            tx    <= shift[0];
            shift <= shift >> 1;
            count <= CLKS_PER_BIT - 1;
        end else begin
            count <= count - 1'b1;
            if (count == 1)
                bits <= bits - 1'b1;
        end
    end

endmodule

`default_nettype wire
