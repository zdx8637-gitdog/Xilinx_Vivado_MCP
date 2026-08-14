#////////////////////////////////////////////////////////////////////////////////
# sim.tcl — Vivado xsim simulation script
# Usage: xvlog ... ; xelab ... ; xsim ...
#////////////////////////////////////////////////////////////////////////////////

set sim_dir  [file dirname [file normalize [info script]]]
set rtl_dir  $sim_dir/../rtl

# ---- Compile ----
puts ">> xvlog: compiling RTL and testbench..."
xvlog -sv -work work $rtl_dir/breath_led.v $rtl_dir/uart_tx.v $sim_dir/tb_breath_led.v

# ---- Elaborate ----
puts ">> xelab: elaborating tb_breath_led..."
xelab -L xil_defaultlib -debug typical -s tb_breath_led_snap tb_breath_led

# ---- Simulate ----
puts ">> xsim: running simulation..."
xsim tb_breath_led_snap -R
puts ""
puts "Simulation complete. Waveform: tb_breath_led.vcd"
