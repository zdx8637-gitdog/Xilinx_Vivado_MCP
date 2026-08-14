###############################################################################
# Constraints: hello_fpga — G3 golden baseline
# Board:  ALINX AX7020
# Part:   XC7Z020CLG400-2
# Clock:  50 MHz (sys_clk, pin Z99, period 20 ns)
###############################################################################

# ---------- Pin assignments (AX7020 board) ----------
set_property PACKAGE_PIN Z99 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]

set_property PACKAGE_PIN N15 [get_ports rst_n]
set_property IOSTANDARD LVCMOS33 [get_ports rst_n]

set_property PACKAGE_PIN J16 [get_ports {led[3]}]
set_property PACKAGE_PIN K16 [get_ports {led[2]}]
set_property PACKAGE_PIN M15 [get_ports {led[1]}]
set_property PACKAGE_PIN M14 [get_ports {led[0]}]

set_property IOSTANDARD LVCMOS33 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[0]}]

# ---------- UART TX (debug output, 115200 8N1) ----------
# J11 expansion port Pin3, PL Bank35, 3.3V
set_property PACKAGE_PIN F17 [get_ports uart_tx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]

# ---------- Clock constraint: 50 MHz ----------
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]
