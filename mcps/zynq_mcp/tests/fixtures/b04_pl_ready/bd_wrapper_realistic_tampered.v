// TAMPERED COPY — same file path, different content
// This has an extra port not in the manifest's bd_wrapper_sha256
module design_1_wrapper
   (clk_in,
    reset_n,
    led_pins,
    data_in,
    data_out,
    extra_port);
  input clk_in;
  input reset_n;
  output [3:0]led_pins;
  input [7:0]data_in;
  output [7:0]data_out;
  output extra_port;

  wire clk_in; wire reset_n;
  wire [3:0]led_pins; wire [7:0]data_in; wire [7:0]data_out;
  wire extra_port;

  design_1 design_1_i
       (.clk_in(clk_in), .reset_n(reset_n), .led_pins(led_pins),
        .data_in(data_in), .data_out(data_out), .extra_port(extra_port));
endmodule
