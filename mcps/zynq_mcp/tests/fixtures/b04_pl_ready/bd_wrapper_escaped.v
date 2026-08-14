// Fixture with escaped identifiers that cannot be plain Verilog identifiers
// \foo.bar  uses a dot which is illegal in plain identifiers
// \bus[0]  uses brackets which are illegal in plain identifiers
`timescale 1 ps / 1 ps

module design_esc_wrapper
   (clk_in,
    \foo.bar ,
    \bus[0] ,
    data_out);
  input clk_in;
  input \foo.bar ;
  output [7:0]\bus[0] ;
  output [3:0]data_out;

  wire clk_in;
  wire \foo.bar ;
  wire [7:0]\bus[0] ;
  wire [3:0]data_out;

  design_esc design_esc_i
       (.clk_in(clk_in),
        .\foo.bar (\foo.bar ),
        .\bus[0] (\bus[0] ),
        .data_out(data_out));
endmodule
