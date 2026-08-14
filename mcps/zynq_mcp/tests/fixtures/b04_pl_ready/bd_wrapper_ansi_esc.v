module design_ansi_esc_wrapper
   (input \foo.bar ,
    output [7:0] \bus[0] ,
    input clk);
  wire \foo.bar ;
  wire [7:0] \bus[0] ;
  wire clk;
  design_ansi_esc design_ansi_esc_i
       (.\foo.bar (\foo.bar ),
        .\bus[0] (\bus[0] ),
        .clk(clk));
endmodule
