# Board XDC — ALINX AX7020 v1.0
# PL constraints for GPIO vertical slice:
#   PL 50MHz oscillator on U18
#   4 PL LEDs on J16/K16/M15/M14 (active-low)
# Source: ALINX AX7020 schematic v2.0; cross-checked against
#   hello_fpga/constraints/top.xdc, g9_hw_test/constraints/top.xdc,
#   zynq_platforms/ax7020_base/constraints/led_pins.xdc

# PL 50MHz clock (on-board oscillator, pin U18)
set_property PACKAGE_PIN U18 [get_ports sys_clk]
set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]
create_clock -period 20.000 -name pl_clk [get_ports sys_clk]

# PL LEDs (active-low via VCCIO_35 sink through resistor/LED to FPGA pin)
set_property PACKAGE_PIN J16 [get_ports {led_pins[3]}]
set_property PACKAGE_PIN K16 [get_ports {led_pins[2]}]
set_property PACKAGE_PIN M15 [get_ports {led_pins[1]}]
set_property PACKAGE_PIN M14 [get_ports {led_pins[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led_pins[*]}]
