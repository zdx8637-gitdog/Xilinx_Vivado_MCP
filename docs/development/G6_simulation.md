# G6 — Simulation Infrastructure

> 日期: 2026-08-01
> 状态: ✅ COMPLETE

## Objective

Establish independent simulation domain (`XSimProcess`) parallel to `VivadoProcess`.

## Architecture

```
MCP → SimTools → XSimProcess → xvlog/xelab/xsim
```

`XSimProcess` is a **task process** (one-shot), not a **session process** (persistent). Each simulation step spawns and exits independently.

## Deliverables

| File | Purpose |
|------|---------|
| `xsim_process.py` | Generic `run(executable, args)` interface |
| `sim_tools.py` | 4 simulation tools |
| `tb_breath_led.v` | Testbench with 7 test cases |

## Tools

| Tool | Executable | Purpose |
|------|------------|---------|
| `compile_sim` | xvlog | Compile RTL + testbench |
| `elaborate_sim` | xelab | Elaborate top module |
| `run_simulation` | xsim | Run simulation, collect assertions |
| `parse_sim_log` | — | Extract PASS/FAIL from log |

## Test Results

4/4 simulation tool tests PASS. Compile (~1.1s) → Elaborate (~1.5s) → Simulate (~5.8s). Total simulation cycle < 10 seconds vs. build cycle ~5 minutes — **37x faster feedback loop**.

## Key Fixes

- Vivado executables on Windows are `.bat` wrappers, not `.exe`
- `subprocess.run()` encoding fixed to `utf-8` with `errors=replace`
- `IS_SEQUENTIAL` returns `0`/`1` in Vivado Tcl (not `true`/`false`)
- `get_cells` default changed to `hierarchical=True`
