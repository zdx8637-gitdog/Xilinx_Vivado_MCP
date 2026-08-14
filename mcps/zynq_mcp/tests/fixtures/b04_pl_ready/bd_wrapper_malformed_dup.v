module bad_dup_wrapper
   (clk,
    clk);
  input clk;
  output clk;

  wire clk;
  bad_dup bad_dup_i (.clk(clk));
endmodule
