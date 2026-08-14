////////////////////////////////////////////////////////////////////////////////
// Module: uart_tx
// Simple UART transmitter — 8N1, 115200 baud @ 50 MHz
//
// Usage:
//   - Assert `send` for one clock cycle with `data` valid
//   - `busy` is high while transmitting
//   - Connect `tx` to output pin
////////////////////////////////////////////////////////////////////////////////
`timescale 1ns / 1ps

module uart_tx #(
    parameter CLK_FREQ  = 50_000_000,
    parameter BAUD_RATE = 115200
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data,
    input  wire       send,
    output reg        tx,
    output reg        busy
);

    localparam BIT_PERIOD = CLK_FREQ / BAUD_RATE;  // ~434

    reg [15:0] bit_timer;
    reg [3:0]  bit_index;
    reg [9:0]  shift_reg;  // start(0) + 8data + stop(1)
    reg        running;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx        <= 1'b1;   // idle high
            busy      <= 1'b0;
            running   <= 1'b0;
            bit_timer <= 16'd0;
            bit_index <= 4'd0;
            shift_reg <= 10'd0;
        end else begin
            // Latch data on send pulse
            if (send && !running) begin
                shift_reg <= {1'b1, data, 1'b0};  // stop=1, data[7:0], start=0
                running   <= 1'b1;
                busy      <= 1'b1;
                bit_timer <= 16'd0;
                bit_index <= 4'd0;
            end

            if (running) begin
                tx <= shift_reg[0];
                if (bit_timer >= BIT_PERIOD - 1) begin
                    bit_timer <= 16'd0;
                    shift_reg <= shift_reg >> 1;
                    bit_index <= bit_index + 4'd1;
                    if (bit_index == 4'd9) begin  // 10 bits sent (start+8data+stop)
                        running <= 1'b0;
                        busy    <= 1'b0;
                        tx      <= 1'b1;
                    end
                end else begin
                    bit_timer <= bit_timer + 16'd1;
                end
            end
        end
    end

endmodule
