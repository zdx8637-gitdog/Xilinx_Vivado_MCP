module first_module
   (clk, rst);
  input clk; input rst;
  wire clk; wire rst;
  first first_i (.clk(clk), .rst(rst));
endmodule

module second_module
   (led);
  output [3:0]led;
  wire [3:0]led;
  second second_i (.led(led));
endmodule
