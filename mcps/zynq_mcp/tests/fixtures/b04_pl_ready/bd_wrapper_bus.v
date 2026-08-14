`timescale 1 ps / 1 ps

module design_bus_wrapper
   (clk,
    addr,
    data);
  input clk;
  input [31:0]addr;
  inout [7:0]data;

  wire clk;
  wire [31:0]addr;
  wire [7:0]data;

  design_bus design_bus_i
       (.clk(clk),
        .addr(addr),
        .data(data));
endmodule
