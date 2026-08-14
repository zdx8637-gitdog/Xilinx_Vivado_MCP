`timescale 1 ps / 1 ps

module bad_no_end_wrapper
   (clk,
    rst);
  input clk;
  input rst;

  wire clk;
  wire rst;

  bad_no_end bad_no_end_i
       (.clk(clk),
        .rst(rst));
// endmodule intentionally omitted
