`timescale 1 ps / 1 ps

module design_ansi_wrapper
   (input clk,
    input reset_n,
    output [3:0]led_pins,
    input [7:0]data_in,
    output [7:0]data_out);

  wire clk;
  wire reset_n;
  wire [3:0]led_pins;
  wire [7:0]data_in;
  wire [7:0]data_out;

  design_ansi design_ansi_i
       (.clk(clk),
        .reset_n(reset_n),
        .led_pins(led_pins),
        .data_in(data_in),
        .data_out(data_out));
endmodule
