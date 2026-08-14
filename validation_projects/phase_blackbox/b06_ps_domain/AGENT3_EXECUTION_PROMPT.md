# Agent3 Execution Prompt — B06 PS Domain Black-Box Acceptance

## Target

You are **Agent3**, an external Claude Code agent using a fresh memory. Execute
the B06 PS Domain black-box acceptance and return an acceptance report. Agent1,
Agent2, and Agent3 are external agents operated by the user; they are not
Codex sub-agents.

## Read first

From this project directory, read these files in order:

1. `CLAUDE.md`
2. `README.md`
3. `public_contract.md`
4. `AGENT3_EXECUTION_PROMPT.md` (this file)

Do not search `docs/manager` for additional instructions. This project-local
package is the execution handoff.

## Fixed Environment

Repository: `D:\fpgaproject`
Project: `D:\fpgaproject\validation_projects\phase_blackbox\b06_ps_domain`
Board: `ALINX_AX7020_v1.0`
Session type: **FRESH_SESSION** — no Manager provisioning required.

Runner creates its own `create_session` per scenario. No preconditioned
runtimes. No `scenario_manifest.json`.

## MCP Usage

- Do NOT configure `zynq_mcp` as a Claude Code MCP server.
- Do NOT restart Claude Code.
- The runner starts its own `python -m mcps.zynq_mcp.server` subprocess
  through MCP SDK `stdio_client` and `ClientSession`.
- Do NOT import internal `mcps.zynq_mcp` or `mcps.common` modules.
- Do NOT read or modify `execution_ledger.json`.

Run:

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\b06_ps_domain\runner.py --run-id agent3_b06_<unique_id>
```

Replace `<unique_id>` with a timestamp like `20260809_080000`. Use a new
unique run id. Do NOT overwrite a previous run.

If the runner exits non-zero, preserve its evidence directory and report the
failure. Do NOT edit the runner, expected outputs, or any production code.

## Functional Scenarios

The runner must execute all five scenarios in order:

1. **`discovery`**: `list_tools` and `get_capabilities`; assert ≥33 `ps_*`
   tools registered (currently 42) and `ps.domains.ps.implemented ≥ 33`;
   assert the schemas of `ps_connect_hw_server`, `ps_list_targets`,
   `ps_import_hardware`, `ps_compile`, `ps_select_target`; assert
   `ps_download_elf` IS registered (the deploy download sub-flow is live).

2. **`bsp_build`**: `create_session` → `ps_import_hardware(xsa_path,
   project_path)` → `ps_create_platform` → `ps_create_bsp` →
   `ps_create_app` → `ps_add_sources(main.c)` →
   `ps_set_compiler_options` → `ps_compile` → verify `ps_get_build_status`
   reports a built ELF → `ps_read_elf_info` reports ELFCLASS32 / EM_ARM.
   **Requires XSCT**; skips with a recorded reason when xsct is absent.

3. **`jtag_connect`**: `create_session` → `ps_connect_hw_server` →
   `ps_list_targets` (ARM Cortex-A9 DAP present) → `ps_select_target` →
   `ps_get_target_status` → `ps_get_device_info` → `ps_disconnect_hw_server`.
   **Requires a reachable hw_server at tcp:localhost:3121 + XSDB**; skips
   otherwise.

4. **`jtag_deploy`**: `create_session` → `ps_connect_hw_server` →
   `ps_select_target` (ARM DAP) → `ps_initialize_ps` → `ps_halt_target` →
   `ps_reg_read pc` → `ps_run_target` (confirmed running) →
   `ps_disconnect_hw_server`. The ELF download + run + UART marker
   verification is **live**: `ps_download_elf` is registered; the sub-flow
   downloads the `bsp_build` ELF, runs it, and asserts the
   `AX7020 ARM Test G11` marker on COM4. `SKIPPED_NO_ELF` is accepted only
   when no ELF was produced (bsp_build skipped). Download/run/uart failures
   FAIL. **Requires board + hw_server + XSDB**; skips otherwise.

5. **`error_paths`**: with no active session, `ps_connect_hw_server` →
   `LOCK_BUSY` / `NO_ACTIVE_SESSION`; after `create_session`, empty /
   non-string / mismatched `session_id` → `SESSION_ID_REQUIRED` /
   `SESSION_ID_MISMATCH`; missing required `target_id` → MCP input-schema
   rejection; `pl_generate_system_top` at `PLATFORM_DESIGN` →
   `STAGE_PREREQUISITE_UNMET` (shared P7 gate — PS tools are
   stage-agnostic); `ps_import_hardware` with a nonexistent XSA →
   `XSA_NOT_FOUND` (or `BRIDGE_NOT_READY`); channel returns to `IDLE`.
   No hardware required.

The checked-in `expected_outputs/*.json` files are the assertion contract
(94 assertions total: discovery 16, bsp_build 25, jtag_connect 15,
jtag_deploy 18, error_paths 20). Every assertion must be evaluated and
recorded with `assertion_id`, expected value, actual value, and
`PASS`/`FAIL`. The runner enforces `expected_assertion_count ==
consumed_assertions` for scenarios that run.

## SKIP semantics

A hardware-gated scenario **SKIPs** with a recorded gate + reason when its
prerequisites are absent (e.g. hw_server not reachable). A SKIP is a valid
outcome and must be reported as such — never as a PASS and never as a FAIL.
`environment.json` in the evidence records which capabilities were present.
`bsp_build` and `error_paths` run on a machine with XSCT present;
`jtag_connect`/`jtag_deploy` will SKIP unless hw_server is running with the
board attached.

## Evidence

The runner writes to `evidence/<run_id>/`:
- `summary.json` — overall pass/fail + per-scenario status + skip reasons
- `environment.json` — the one-shot capability probe
- `discovery_result.json`, `bsp_build_result.json`,
  `jtag_connect_result.json`, `jtag_deploy_result.json`,
  `error_paths_result.json` — per-scenario assertions
- `discovery/`, `bsp_build/`, ... — per-scenario evidence subdirectories

Agent3 must review all evidence files and report:
- Exact command executed
- Run ID
- Exit code
- Per-scenario status (PASS / FAIL / SKIP + gate + reason) and assertion
  counts (expected/consumed, PASS/FAIL)
- Total assertions: expected == consumed for the scenarios that ran?
- The environment probe summary (xsct/xsdb/hw_server/serial/xsa/source)
- For a run bsp_build: the produced ELF path, class/machine
- Any exceptions or residual process issues

## Scope Boundaries

- This is **FRESH_SESSION** — the runner handles the full public lifecycle
  per scenario, including `close_session`.
- The debug-session tools (`ps_debug_*`, `ps_stack_trace`, `ps_write_uart`)
  are **NOT part of this acceptance** — they are registered but outside
  these scenarios. Do NOT claim them.
- `ps_download_elf` IS part of this acceptance (jtag_deploy sub-flow).
- The `jtag_*` scenarios require real hardware. If they SKIP, report the
  gate + reason and do NOT claim hardware acceptance.
- Do NOT modify production code, frozen tests/assets, root `.mcp.json`,
  Manager harness files, or fixtures.
- Do NOT call Agent1 or Agent2, start B07, or claim B06 frozen.

## Report Requirements

Use status `EXECUTED - READY FOR MANAGER REVIEW` only when no scenario
FAILs (PASS or SKIP each) and `--fail-on-skip` was not needed. Otherwise use
`EXECUTED - FAILED` and preserve all evidence.

Report format:

```markdown
## B06 Agent3 Black-Box Acceptance Report

**Status**: EXECUTED - READY FOR MANAGER REVIEW | EXECUTED - FAILED
**Run ID**: <run_id>
**Command**: <exact command>
**Exit code**: <code>

### Scenario Results

| Scenario | Status | Assertions | Pass | Fail | Consumed | Gate/Reason |
|----------|--------|-----------|------|------|----------|-------------|
| discovery | PASS | 16 | 16 | 0 | 16 | — |
| bsp_build | ... | 25 | ... | ... | ... | ... |
| jtag_connect | SKIP | 0 | 0 | 0 | 0 | hw_server: not reachable |
| jtag_deploy | SKIP | 0 | 0 | 0 | 0 | hw_server: not reachable |
| error_paths | ... | 20 | ... | ... | ... | ... |

### Evidence Path
evidence/<run_id>/

### Environment Probe
xsct=<path|missing>, xsdb=<path|missing>, hw_server_reachable=<bool>,
uart_ports=[...], xsa=<present|missing>, source_c=<present|missing>

### Artifacts (bsp_build, when run)
- ELF: <path> class=ELFCLASS32 machine=40

### Exceptions
<list or "none">
```
