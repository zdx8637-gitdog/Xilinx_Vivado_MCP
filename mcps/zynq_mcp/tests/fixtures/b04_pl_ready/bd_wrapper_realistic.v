//Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
//Copyright 2022-2023 Advanced Micro Devices, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2023.1 (win64) Build 3865809 Sun May  7 15:05:29 MDT 2023
//Date        : Mon Aug  3 19:47:40 2026
//Host        : DESKTOP-AVUTG91 running 64-bit major release  (build 9200)
//Command     : generate_target design_1_wrapper.bd
//Design      : design_1_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module design_1_wrapper
   (clk_in,
    reset_n,
    led_pins,
    data_in,
    data_out);
  input clk_in;
  input reset_n;
  output [3:0]led_pins;
  input [7:0]data_in;
  output [7:0]data_out;

  wire clk_in;
  wire reset_n;
  wire [3:0]led_pins;
  wire [7:0]data_in;
  wire [7:0]data_out;

  design_1 design_1_i
       (.clk_in(clk_in),
        .reset_n(reset_n),
        .led_pins(led_pins),
        .data_in(data_in),
        .data_out(data_out));
endmodule
