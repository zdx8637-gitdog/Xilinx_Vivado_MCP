#////////////////////////////////////////////////////////////////////////////////
# create_bd.tcl — Zynq Block Design for PS UART + AXI GPIO
#
# Creates a block design that:
#   - Instantiates Zynq7 Processing System (UART1 on MIO48/49)
#   - Adds AXI GPIO for PL→PS duty cycle readback
#   - Exports GPIO input to top-level wrapper
#////////////////////////////////////////////////////////////////////////////////

set bd_name   "zynq_bd"
set bd_dir    [file dirname [file normalize [info script]]]/..

# Create block design
create_bd_design $bd_name

# ================================================================
# Zynq7 Processing System
# ================================================================
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]

# Apply preset: AX7020 configuration
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {
    make_external "FIXED_IO, DDR"
    Master "Disable"
    Slave  "Disable"
} $ps

# Configure PS:
#   - UART1: MIO48/49, 115200 baud
#   - Enable M_AXI_GP0 for AXI GPIO access
#   - Enable FCLK_CLK0 at 50 MHz for AXI GPIO IP clock
set_property -dict [list \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1} \
    CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49} \
    CONFIG.PCW_UART1_BAUD_RATE {115200} \
    CONFIG.PCW_MIO_48_PULLUP {1} \
    CONFIG.PCW_MIO_49_PULLUP {1} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_USE_FABRIC_INTERRUPT {0} \
    CONFIG.PCW_EN_CLK0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {50} \
    CONFIG.PCW_QSPI_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_ENET0_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_SD0_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_USB0_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_I2C0_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_TTC0_PERIPHERAL_ENABLE {0} \
    CONFIG.PCW_GPIO_MIO_GPIO_ENABLE {0} \
] $ps

puts "PS configured: UART1 MIO48/49, 115200, M_AXI_GP0 enabled"

# ================================================================
# AXI Interconnect
# ================================================================
set axi_interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {1}] $axi_interconnect

# ================================================================
# AXI GPIO — 1 channel, 32-bit input
# ================================================================
set axi_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_0]
set_property -dict [list \
    CONFIG.C_IS_DUAL {0} \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_ALL_OUTPUTS {0} \
] $axi_gpio

# Make GPIO input port external
set gpio_in [create_bd_port -dir I -from 31 -to 0 gpio_duty_in]
connect_bd_net $gpio_in [get_bd_pins axi_gpio_0/gpio_io_i]

puts "AXI GPIO: 32-bit input, port gpio_duty_in[31:0]"

# ================================================================
# Processor System Reset
# ================================================================
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 proc_sys_reset_0]

# ================================================================
# Connections
# ================================================================

# Clocks
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins axi_interconnect_0/ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins axi_interconnect_0/S00_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins axi_interconnect_0/M00_ACLK]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins axi_gpio_0/s_axi_aclk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
               [get_bd_pins proc_sys_reset_0/slowest_sync_clk]

# Reset
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] \
               [get_bd_pins proc_sys_reset_0/ext_reset_in]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] \
               [get_bd_pins axi_interconnect_0/ARESETN]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] \
               [get_bd_pins axi_interconnect_0/S00_ARESETN]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] \
               [get_bd_pins axi_interconnect_0/M00_ARESETN]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] \
               [get_bd_pins axi_gpio_0/s_axi_aresetn]

# AXI bus PS → Interconnect → GPIO
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] \
                    [get_bd_intf_pins axi_interconnect_0/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_interconnect_0/M00_AXI] \
                    [get_bd_intf_pins axi_gpio_0/S_AXI]

puts "AXI bus connected: PS M_AXI_GP0 → axi_interconnect → axi_gpio"

# ================================================================
# Assign address
# ================================================================
assign_bd_address
set gpio_addr [get_bd_addr_segs -of_objects $axi_gpio]
puts "AXI GPIO address: $gpio_addr"

# ================================================================
# Validate and save
# ================================================================
validate_bd_design
save_bd_design
puts "Block design '$bd_name' created and validated."

# ================================================================
# Create HDL wrapper
# ================================================================
set wrapper_path [make_wrapper -files [get_files $bd_name.bd] -top]
add_files -norecurse $wrapper_path
puts "HDL wrapper: $wrapper_path"

# ================================================================
# Generate output products (DCP for IPs)
# ================================================================
generate_target all [get_files $bd_name.bd]
puts "Output products generated."

puts ""
puts "========================================================================="
puts " Block design creation COMPLETE"
puts "========================================================================="
puts "Next: update build_ps.tcl to use this BD as top"
