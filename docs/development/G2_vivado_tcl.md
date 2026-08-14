# G2 — Vivado Tcl Communication

> 日期: 2026-07-31
> 状态: ✅ COMPLETE

## Objective

Prove that Python subprocess can communicate with Vivado Tcl shell on Windows.

## Approach

- Tested batch mode: `vivado -mode batch -source test_vivado.tcl` → PASS
- Tested interactive mode: `echo puts {TCL_MODE_OK} | vivado -mode tcl` → `TCL_MODE_OK`
- Confirmed stdin/stdout PIPE works as alternative to Unix-only `pexpect`

## Key Finding

`subprocess.Popen` + stdin/stdout PIPE can replace `pexpect` on Windows. This enables the entire MCP architecture without Unix PTY dependency.

## Reference

- `fpga-agent/scripts/test_vivado.tcl` — minimal Tcl connectivity test
