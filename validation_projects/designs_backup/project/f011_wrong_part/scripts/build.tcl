#////////////////////////////////////////////////////////////////////////////////
# build.tcl — G3 golden baseline build script
# Project:  hello_fpga
# Part:     xc7k160tfbg676-1  
# Vivado:   2023.1
#////////////////////////////////////////////////////////////////////////////////

# ================================================================
# GATE: Version lock — refuse to run on wrong Vivado version
# ================================================================
set expected_version "2023.1"
set actual_version [version -short]

puts "========================================================================="
puts " HELLO_FPGA G3 BUILD — Golden Baseline"
puts "========================================================================="
puts "Expected Vivado: $expected_version"
puts "Actual Vivado:   $actual_version"

if {$actual_version ne $expected_version} {
    puts ""
    puts "ERROR: Vivado version mismatch!"
    puts "Expected: $expected_version"
    puts "Actual:   $actual_version"
    puts ""
    puts "Refusing to execute. Check your PATH and settings64.bat."
    exit 100
}

puts "Version check PASSED."
puts "========================================================================="

# ================================================================
# Project setup
# ================================================================
set proj_name  "hello_fpga"
set proj_dir   [file dirname [file normalize [info script]]]/..
set proj_path  [file normalize $proj_dir]
set rtl_dir    $proj_path/rtl
set constr_dir $proj_path/constraints
set report_dir $proj_path/reports
set output_dir $proj_path/output

file mkdir $report_dir
file mkdir $output_dir

puts "Project root : $proj_path"
puts "RTL          : $rtl_dir"
puts "Constraints  : $constr_dir"
puts "Reports      : $report_dir"
puts "Output       : $output_dir"
puts "========================================================================="

# ================================================================
# Create project
# ================================================================
create_project -force $proj_name $proj_path/vivado_project -part xc7k160tfbg676-1  

# ================================================================
# Add sources and constraints
# ================================================================
add_files -fileset sources_1 [glob $rtl_dir/*.v]
set_property top top [current_fileset]

add_files -fileset constrs_1 [glob $constr_dir/*.xdc]

puts "Files added. Top module: [get_property top [current_fileset]]"
puts "========================================================================="

# ================================================================
# Synthesis
# ================================================================
puts ">> Running synthesis..."
synth_design -top top -flatten_hierarchy rebuilt

puts ">> Writing post-synthesis utilization report..."
write_checkpoint -force $report_dir/post_synth.dcp
report_utilization -file $report_dir/synthesis_utilization.rpt

puts "========================================================================="

# ================================================================
# Implementation
# ================================================================
puts ">> Running opt_design..."
opt_design

puts ">> Running place_design..."
place_design

puts ">> Running phys_opt_design..."
phys_opt_design

puts ">> Running route_design..."
route_design

puts "========================================================================="

# ================================================================
# Reports — GOLDEN BASELINE
# ================================================================
puts ">> Generating reports..."

# Timing
puts "       report_timing_summary  ..."
report_timing_summary -file $report_dir/timing_summary.rpt

# Utilization
puts "       report_utilization     ..."
report_utilization -file $report_dir/utilization.rpt

# Clock
puts "       report_clocks          ..."
report_clocks -file $report_dir/clock_summary.rpt

# Power (optional but useful)
puts "       report_power           ..."
report_power -file $report_dir/power.rpt

puts "========================================================================="

# ================================================================
# Bitstream
# ================================================================
puts ">> Writing bitstream..."
write_bitstream -force $output_dir/hello_fpga.bit

puts "========================================================================="
puts " G3 BUILD COMPLETE"
puts "========================================================================="
puts ""
puts "Golden baseline files:"
puts "  $report_dir/synthesis_utilization.rpt"
puts "  $report_dir/utilization.rpt"
puts "  $report_dir/timing_summary.rpt"
puts "  $report_dir/clock_summary.rpt"
puts "  $report_dir/power.rpt"
puts "  $report_dir/post_synth.dcp"
puts "  $output_dir/hello_fpga.bit"
puts ""
puts "========================================================================="

