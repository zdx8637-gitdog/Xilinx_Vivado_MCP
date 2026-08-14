# G0 — Environment Verification

> 日期: 2026-07-31
> 状态: ✅ COMPLETE

## Objective

Verify that all required tools and versions are correctly installed.

## Environment

| Component | Version | Location |
|-----------|---------|----------|
| Vivado | 2023.1 | `D:\Xilinx\Vivado\2023.1` |
| Vitis | 2023.1 | `D:\Xilinx\Vitis\2023.1` |
| Python | 3.12.9 | System |
| Node.js | v24.13.1 | System |
| Claude Code | 2.1.181 (Fable 5) | System |

## Results

- Vivado 2023.1 confirmed via `version -short`
- Vitis 2023.1 confirmed
- `vivado -version` known issue: exit code 1, no stdout (use `version -short` in Tcl instead)
- Vitis exit code -1, but version output correct
- All paths confirmed: `D:\Xilinx\`

## Test Script

`fpga-agent/scripts/g0_standalone.bat` — full validation pipeline.
