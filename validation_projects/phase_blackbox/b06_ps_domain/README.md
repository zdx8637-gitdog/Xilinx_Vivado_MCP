# B06 PS Domain — Agent3 Black-Box Acceptance

> **Status**: READY FOR AGENT3
> **Phase**: B06 PS Domain | **Type**: FRESH_SESSION
> **Agent**: Agent3 (fresh context only)
> **Reference**: `docs/development/ps_domain/B06_gap_analysis.md`

## Purpose

Verify the B06 PS Domain public MCP boundary: the 42 `ps_*` tools through a
real `zynq_mcp` server, using only the MCP SDK `ClientSession` and public
tools. Covers tool discovery/schemas, the real XSCT BSP/Build pipeline, the
JTAG chain (hardware-gated), and the fail-closed error model.

This is a FRESH_SESSION project. The runner creates its own
`create_session(board_id, project_path)` per scenario and closes it
afterwards. No Manager provisioning or preconditioned runtimes.

## Scenarios

| # | Scenario | Gate | Flow | Assertions |
|---|----------|------|------|-----------|
| 1 | discovery | — | `list_tools` + `get_capabilities`; schemas of key `ps_*` tools; `ps_download_elf` registered | 16 |
| 2 | bsp_build | xsct | create_session → import XSA → platform → BSP → app → add sources → compiler opts → compile → ELF valid | 25 |
| 3 | jtag_connect | xsdb + hw_server | connect → targets → ARM DAP → select → status → device info → disconnect | 15 |
| 4 | jtag_deploy | xsdb + hw_server | connect → select ARM DAP → ps7_init → halt → reg_read pc → run → disconnect; download/run/UART sub-flow live (`ps_download_elf`) | 18 |
| 5 | error_paths | lane IDLE | NO_ACTIVE_SESSION, SESSION_ID_REQUIRED, SESSION_ID_MISMATCH, schema rejection, STAGE_PREREQUISITE_UNMET, XSA_NOT_FOUND, channel clean | 20 |

## Quick Start (Agent3)

```bash
cd D:\fpgaproject
python validation_projects\phase_blackbox\b06_ps_domain\runner.py --run-id agent3_b06_<unique_id>
```

That's it. Read `CLAUDE.md` then `AGENT3_EXECUTION_PROMPT.md` for full
instructions. Optionally run one scenario:

```bash
python validation_projects\phase_blackbox\b06_ps_domain\runner.py --run-id agent3_b06_x --scenario discovery
```

`--fail-on-skip` turns any SKIP into an overall failure (for strict gates).

## Architecture

```
expected_outputs/*.json     ← CHECKED-IN assertion contracts
         │
         ▼  runner.py loads them
         │
         ▼  runner.py starts MCP SDK server → calls public tools
evidence/<run_id>/          ← RUNNER OUTPUT
├── summary.json
├── environment.json        (one-shot capability probe)
├── *_result.json
└── <scenario>/
```

- `runner.py` imports ONLY stdlib + `mcp` SDK. No `mcps.zynq_mcp` imports.
- `runner.py` probes the environment once (xsct/xsdb/hw_server/serial); a
  hardware-gated scenario SKIPs with a recorded gate + reason when its
  prerequisites are absent and FAILs if it runs and an assertion is unmet.
- The server starts its own XSDB/XSCT bridge lazily on the first `ps_*` call.
- Runner uses a unique `ZYNQ_RUNTIME_ROOT` temp dir — no cross-run state
  leakage.

## Hardware notes

Board: ALINX AX7020 (xc7z020clg400-2). UART: COM4 (CP2102, 115200).
XSDB/XSCT/hw_server: `D:\Xilinx\Vitis\2023.1\bin`. XSA:
`D:\fpgaproject\zynq_platforms\xsa\ax7020_base.xsa`.

On a machine without hw_server running, `jtag_connect` and `jtag_deploy`
will SKIP (recorded) — that is the expected result. `bsp_build` runs whenever
XSCT is present.

## Known status (2026-08-09, first black-box run)

The runner is the gate: a scenario PASSES only when the real tools satisfy
the contract. On this machine (`xsct` present, `hw_server` NOT running) the
first full run produced:

| Scenario | Status | Detail |
|----------|--------|--------|
| discovery | PASS 16/16 | 42 `ps_*` tools registered; schemas correct |
| bsp_build | FAIL 25/25 | `ps_add_sources` and `ps_set_compiler_options` FAILED with `APP_CONFIG_FAILED` |
| jtag_connect | SKIP | hw_server not reachable at tcp:localhost:3121 |
| jtag_deploy | SKIP | hw_server not reachable at tcp:localhost:3121 |
| error_paths | PASS 20/20 | all reason codes verified |

**Production finding (P1, for Agent1, not fixed here):** the BSP/Build
second batch's `ps_add_sources` / `ps_set_compiler_options` generate XSCT
`app config` commands that Vitis 2023.1 rejects (`Unknown or ambiguous
parameter ... must be assembler-flags, build-config, ...`). The `-add <file>`
source-add and the `-flags`/`-append-args`/`-linker-flags` option map in
`mcps/zynq_mcp/domains/ps/ps_bsp.py` do not match the installed XSCT command
schema. Until fixed, `bsp_build` correctly fails. The rest of the pipeline
(import XSA → platform → BSP → app → compile → valid ELFCLASS32/EM_ARM ELF)
passes; note that because source add fails, the built ELF is the template
app, not the ps_led_test program.

## Public Contract

See `public_contract.md` for tool schemas, the `session_id` calling
convention, async op lifecycle, error codes, and SKIP semantics.

## Constraints

- Agent3 MUST NOT import `mcps.zynq_mcp` or read `execution_ledger.json`.
- Agent3 MUST NOT configure `zynq_mcp` as a Claude Code MCP server.
- Agent3 MUST NOT call `run_tcl`.
- Agent3 MUST NOT claim a hardware PASS for a SKIPPED scenario.
- Agent3 MUST NOT start B06, call Agent1/Agent2, or claim B06 frozen.
