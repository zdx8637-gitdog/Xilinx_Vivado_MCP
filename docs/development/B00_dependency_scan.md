# B00 — Absolute Path Dependency Scan

> Scan date: 2026-08-04 08:00:50
> Scan method: Binary search for "fpgaproject" in .py/.tcl/.bat/.md source files
> Excluded dirs: .Xil, .git, __pycache__, _trash
> Excluded files: B00_project_cleanup_plan.md, B00_completion_report.md, B00_dependency_scan.md

## Summary

| Extension | Count |
|-----------|-------|
| .bat | 10 |
| .md | 8 |
| .py | 18 |
| .tcl | 58 |
| **Total** | **94** |

## File List

### .bat (10 files)

- `Xilinx_Vivado_MCP\_g10_prog.bat`
- `hello_fpga\scripts\run_g3.bat`
- `zynq_platforms\ax7020_base\_full_build.bat`
- `zynq_platforms\ax7020_base\_make_boot.bat`
- `zynq_platforms\ax7020_base\_make_boot2.bat`
- `zynq_platforms\ax7020_base\_rebuild_final.bat`
- `zynq_platforms\ax7020_base\_xsct_build.bat`
- `zynq_platforms\ax7020_base\_xsct_debug.bat`
- `zynq_platforms\ax7020_base\_xsct_dl.bat`
- `zynq_platforms\ax7020_base\_xsct_led.bat`

### .md (8 files)

- `README.md`
- `docs\development\G11_debug_diagnostics.md`
- `docs\development\G11_vitis_mcp.md`
- `g9_hw_test\AGENT2_G9.md`
- `validation_projects\AGENT1_PROMPT.md`
- `validation_projects\AGENT2_PROMPT.md`
- `validation_projects\AGENT2_ROUND2.md`
- `validation_projects\AGENT2_SKILL.md`

### .py (18 files)

- `Xilinx_Vivado_MCP\tests\analyze_breath.py`
- `Xilinx_Vivado_MCP\tests\debug_cells.py`
- `Xilinx_Vivado_MCP\tests\g10_program.py`
- `Xilinx_Vivado_MCP\tests\g5_3_validation.py`
- `Xilinx_Vivado_MCP\tests\g9_smoke.py`
- `Xilinx_Vivado_MCP\tests\program_and_monitor.py`
- `Xilinx_Vivado_MCP\tests\reprogram.py`
- `Xilinx_Vivado_MCP\tests\test_crash_recovery.py`
- `Xilinx_Vivado_MCP\tests\test_errors.py`
- `Xilinx_Vivado_MCP\tests\test_g10_full.py`
- `Xilinx_Vivado_MCP\tests\test_g7_validate.py`
- `Xilinx_Vivado_MCP\tests\test_g9_hardware.py`
- `Xilinx_Vivado_MCP\tests\test_golden.py`
- `Xilinx_Vivado_MCP\tests\test_platform.py`
- `Xilinx_Vivado_MCP\tests\test_platform2.py`
- `Xilinx_Vivado_MCP\tests\test_platform_v2.py`
- `Xilinx_Vivado_MCP\tests\test_protocol.py`
- `Xilinx_Vivado_MCP\tests\test_simulation.py`

### .tcl (58 files)

- `zynq_platforms\ax7020_base\_export_g11_pl_uart_xsa.tcl`
- `zynq_platforms\ax7020_base\_export_g11_xsa.tcl`
- `zynq_platforms\ax7020_base\_export_xsa.tcl`
- `zynq_platforms\ax7020_base\asm_test.tcl`
- `zynq_platforms\ax7020_base\block_design\build_g11_pl_uart.tcl`
- `zynq_platforms\ax7020_base\bp_test.tcl`
- `zynq_platforms\ax7020_base\bp_test_v2.tcl`
- `zynq_platforms\ax7020_base\bp_test_v3.tcl`
- `zynq_platforms\ax7020_base\bringup_diag.tcl`
- `zynq_platforms\ax7020_base\bringup_v2.tcl`
- `zynq_platforms\ax7020_base\bringup_v3.tcl`
- `zynq_platforms\ax7020_base\build_app.tcl`
- `zynq_platforms\ax7020_base\build_g11_vitis.tcl`
- `zynq_platforms\ax7020_base\create_bootbin.tcl`
- `zynq_platforms\ax7020_base\create_bootbin_xsct.tcl`
- `zynq_platforms\ax7020_base\debug_targets.tcl`
- `zynq_platforms\ax7020_base\debug_targets2.tcl`
- `zynq_platforms\ax7020_base\diag_halt.tcl`
- `zynq_platforms\ax7020_base\direct_main.tcl`
- `zynq_platforms\ax7020_base\download_final.tcl`
- `zynq_platforms\ax7020_base\download_fsbl.tcl`
- `zynq_platforms\ax7020_base\download_g11.tcl`
- `zynq_platforms\ax7020_base\download_robust.tcl`
- `zynq_platforms\ax7020_base\download_test.tcl`
- `zynq_platforms\ax7020_base\download_v2.tcl`
- `zynq_platforms\ax7020_base\download_v3.tcl`
- `zynq_platforms\ax7020_base\full_boot.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\ax7020_base_auto_pc_0_synth_1\ax7020_base_auto_pc_0.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\ax7020_base_axi_gpio_led_0_synth_1\ax7020_base_axi_gpio_led_0.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\ax7020_base_proc_sys_reset_0_0_synth_1\ax7020_base_proc_sys_reset_0_0.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\ax7020_base_processing_system7_0_0_synth_1\ax7020_base_processing_system7_0_0.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\impl_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\g10_build\g10_ps_led.runs\synth_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\ax7020_base_auto_pc_0_synth_1\ax7020_base_auto_pc_0.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\ax7020_base_axi_gpio_led_0_synth_1\ax7020_base_axi_gpio_led_0.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\ax7020_base_proc_sys_reset_0_0_synth_1\ax7020_base_proc_sys_reset_0_0.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\ax7020_base_processing_system7_0_0_synth_1\ax7020_base_processing_system7_0_0.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\impl_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\g11_build\ax7020_g11.runs\synth_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\ax7020_base_auto_pc_0_synth_1\ax7020_base_auto_pc_0.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\ax7020_base_axi_gpio_led_0_synth_1\ax7020_base_axi_gpio_led_0.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\ax7020_base_proc_sys_reset_0_0_synth_1\ax7020_base_proc_sys_reset_0_0.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\ax7020_base_processing_system7_0_0_synth_1\ax7020_base_processing_system7_0_0.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\impl_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\g11_pl_uart_build\ax7020_g11_pl_uart.runs\synth_1\ax7020_base_wrapper.tcl`
- `zynq_platforms\ax7020_base\led_loop.tcl`
- `zynq_platforms\ax7020_base\led_test.tcl`
- `zynq_platforms\ax7020_base\led_test2.tcl`
- `zynq_platforms\ax7020_base\main_jump.tcl`
- `zynq_platforms\ax7020_base\minimal_test.tcl`
- `zynq_platforms\ax7020_base\ps_led_test.tcl`
- `zynq_platforms\ax7020_base\raw_arm.tcl`
- `zynq_platforms\ax7020_base\rebuild_app.tcl`
- `zynq_platforms\ax7020_base\recover_pl_uart.tcl`
- `zynq_platforms\ax7020_base\recover_target.tcl`
- `zynq_platforms\ax7020_base\test_loadhw.tcl`
- `zynq_platforms\ax7020_base\uart_test.tcl`
- `zynq_platforms\ax7020_base\uart_test2.tcl`

## Key Dependency Chains

| Referenced Path | Approx. References | Affected Area |
|----------------|---------------------|---------------|
| `D:/fpgaproject/hello_fpga/` | ~25 | PL test scripts, platform tests, PL UART build |
| `D:/fpgaproject/zynq_platforms/` | ~40 | G10/G11 Tcl, build, recover, download scripts |
| `D:/fpgaproject/Xilinx_Vivado_MCP/` | ~6 | Test entry points |

> B00 decision: No absolute paths corrected. Path normalization belongs to B04 (PL MCP) / B05 (Platform MCP) / B06 (PS MCP).
