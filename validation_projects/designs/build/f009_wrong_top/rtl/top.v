`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Module: top — PWM breathing LED + UART debug
// Target: XC7Z020CLG400-2 (ALINX AX7020, 50 MHz)
//
// LED[3:1] = breathing LEDs (J16, K16, M15)
// LED[0]   = UART TX monitor (M14, visible flicker when UART active)
// uart_tx   = J11 Pin3 (F17), 115200 8N1
//////////////////////////////////////////////////////////////////////////////////

module top(
    input  wire       sys_clk,
    input  wire       rst_n,
    output wire [3:0] led,
    output wire       uart_tx
);

    // ----------------------------------------------------------------
    // Breath LED
    // ----------------------------------------------------------------
    wire [31:0] duty_val;
    wire [3:0]  pwm_led;

    breath_led u_breath (
        .clk     (sys_clk),
        .rst_n   (rst_n),
        .led     (pwm_led),
        .duty_out(duty_val)
    );

    // ----------------------------------------------------------------
    // UART TX (115200 8N1)
    // ----------------------------------------------------------------
    wire       uart_busy;
    reg  [7:0] uart_data;
    reg        uart_send;
    wire       uart_tx_out;

    uart_tx #(
        .CLK_FREQ (50_000_000),
        .BAUD_RATE(115200)
    ) u_uart (
        .clk  (sys_clk),
        .rst_n(rst_n),
        .data (uart_data),
        .send (uart_send),
        .tx   (uart_tx_out),
        .busy (uart_busy)
    );

    assign uart_tx = uart_tx_out;
    assign led[0]  = uart_tx_out;   // flickers = UART active
    assign led[1]  = pwm_led[1];
    assign led[2]  = pwm_led[2];
    assign led[3]  = pwm_led[3];

    // ----------------------------------------------------------------
    // UART message sender — sends "DUTY=XXXXXXXX\r\n" every 500ms
    // ----------------------------------------------------------------
    localparam SEND_INTERVAL = 32'd25_000_000;  // 500ms @ 50MHz

    // Hex nibble to ASCII
    function [7:0] nibble_to_ascii;
        input [3:0] n;
        case (n)
            4'h0: nibble_to_ascii = "0";
            4'h1: nibble_to_ascii = "1";
            4'h2: nibble_to_ascii = "2";
            4'h3: nibble_to_ascii = "3";
            4'h4: nibble_to_ascii = "4";
            4'h5: nibble_to_ascii = "5";
            4'h6: nibble_to_ascii = "6";
            4'h7: nibble_to_ascii = "7";
            4'h8: nibble_to_ascii = "8";
            4'h9: nibble_to_ascii = "9";
            4'hA: nibble_to_ascii = "A";
            4'hB: nibble_to_ascii = "B";
            4'hC: nibble_to_ascii = "C";
            4'hD: nibble_to_ascii = "D";
            4'hE: nibble_to_ascii = "E";
            4'hF: nibble_to_ascii = "F";
        endcase
    endfunction

    reg [31:0] send_timer;
    reg [4:0]  byte_idx;       // 0-15
    reg [31:0] snap_duty;
    reg        sending;

    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            uart_send  <= 1'b0;
            uart_data  <= 8'd0;
            send_timer <= 32'd0;
            byte_idx   <= 5'd0;
            snap_duty  <= 32'd0;
            sending    <= 1'b0;
        end else begin
            uart_send <= 1'b0;  // default: pulse low

            if (!sending) begin
                if (send_timer >= SEND_INTERVAL) begin
                    send_timer <= 32'd0;
                    snap_duty  <= duty_val;
                    byte_idx   <= 5'd0;
                    sending    <= 1'b1;
                end else begin
                    send_timer <= send_timer + 32'd1;
                end
            end else begin
                if (!uart_busy && !uart_send) begin  // extra safety: one cycle gap
                    // "DUTY=XXXXXXXX\r\n" — 16 bytes
                    case (byte_idx)
                        0:  uart_data <= 8'h44;  // 'D'
                        1:  uart_data <= 8'h55;  // 'U'
                        2:  uart_data <= 8'h54;  // 'T'
                        3:  uart_data <= 8'h59;  // 'Y'
                        4:  uart_data <= 8'h3D;  // '='
                        5:  uart_data <= nibble_to_ascii(snap_duty[31:28]);
                        6:  uart_data <= nibble_to_ascii(snap_duty[27:24]);
                        7:  uart_data <= nibble_to_ascii(snap_duty[23:20]);
                        8:  uart_data <= nibble_to_ascii(snap_duty[19:16]);
                        9:  uart_data <= nibble_to_ascii(snap_duty[15:12]);
                        10: uart_data <= nibble_to_ascii(snap_duty[11:8]);
                        11: uart_data <= nibble_to_ascii(snap_duty[7:4]);
                        12: uart_data <= nibble_to_ascii(snap_duty[3:0]);
                        13: uart_data <= 8'h0D;  // '\r'
                        14: uart_data <= 8'h0A;  // '\n'
                        default: uart_data <= 8'h00;
                    endcase
                    uart_send <= 1'b1;
                    if (byte_idx >= 5'd14) begin
                        sending <= 1'b0;  // done
                    end else begin
                        byte_idx <= byte_idx + 5'd1;
                    end
                end
            end
        end
    end

endmodule
