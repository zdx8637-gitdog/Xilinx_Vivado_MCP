# Agent1 Prompt: B05 Platform/AXI Round 3 Functional Test Closure

Target: Agent1, external Claude Code, fresh context or continuation.

Read first:

1. `D:/fpgaproject/docs/manager/B05_platform_axi_round2_review.md`
2. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_round2_prompt.md`
3. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_prompt.md`

Status is `REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED`. Do not call Agent3 or
Agent2, do not start B06, and do not claim success from deselected tests.

## Objective

Close the real functional B05 vertical slice:

```text
fresh public create_session
  -> PLATFORM_DESIGN
  -> platform_generate {}
  -> existing Vivado worker
  -> valid PS7 + one-channel AXI GPIO + SmartConnect BD
  -> wrapper + XSA + contract-valid Platform Manifest
  -> atomic operation/context revision publication
  -> PL_GENERATE
  -> public pl_generate_system_top consumes the generated wrapper
```

Do not expand the security audit. Fix the functional chain and its direct
failure behavior.

## Required Changes

### 1. Make the actual public success path pass

- Keep `create_session -> PLATFORM_DESIGN` and the empty public
  `platform_generate {}` schema.
- Run the existing host-live test without deselecting it. It must start the
  normal `zynq_mcp` server, call public MCP tools, wait for terminal success,
  and preserve the complete trace.

### 2. Correct Platform generation

- Use the validated Board Profile's `part`, not a hardcoded part string.
- Keep the existing `SingleWorkerController.ensure_worker()` lifecycle; do not
  create a second worker or process launcher.
- Source `boards/ALINX_AX7020_v1.0/ps7_preset.tcl` after creating
  `processing_system7_0`, then call `set_ps_config processing_system7_0`.
- Configure AXI GPIO as exactly one channel, four-bit output. Remove the later
  dual-channel reconfiguration and connect the matching output pin to the
  four-bit LED external port.
- Connect PS7 GP0, SmartConnect, GPIO clocks, reset, and PS7 DDR/FIXED_IO.
- Query the real address map with explicit machine-readable Tcl output and
  verify `axi_gpio` is `0x41200000` with a 64 KiB range. Do not accept an
  empty/unparsed map.
- Generate the wrapper with `make_wrapper`, add it to the project, and use the
  exact returned/generated path. Fail with `WRAPPER_EXPORT_FAILED` if it is
  missing or empty.
- Export XSA with `write_hw_platform -fixed` and no bitstream option. Fail with
  `XSA_EXPORT_FAILED` if missing or empty.
- Preserve `BD_VALIDATION_FAILED` and `XSA_EXPORT_FAILED` when those specific
  Tcl operations fail; do not convert all Tcl errors to `ADAPTER_NOT_READY`.

### 3. Correct the shared Platform Manifest contract

- Use `schema_version="1.0"`, `manifest_type="platform"`, `status="locked"`.
- Publish relative `xsa_path` and `bd_wrapper_path`, but validate a resolved
  copy rooted at `project_path` so file existence and SHA checks use the actual
  files.
- Include `revision_inputs` required by `mcps.common.revision`, including the
  board profile SHA, actual Vivado version, preset/config SHA, wrapper/source
  SHA, and deterministic topology/address inputs.
- Set both `manifest_revision` and `platform_revision` to
  `compute_revision(revision_inputs)`.
- Use `publish_manifest()` for the final no-replace publication. Do not write
  the final file directly with `open(..., "w")`.
- Add a component test that loads the generated/representative manifest,
  resolves paths as production does, and asserts `validate_manifest()` returns
  zero issues.

### 4. Bind operation output revision atomically

On successful terminal transition, the operation record and context must both
show the exact same platform revision:

- `operation.output_artifact_revision == platform_revision`
- `context.platform_revision == platform_revision`
- `current_stage: PLATFORM_DESIGN -> PL_GENERATE`
- lane returns to `IDLE`

On any generation failure, stage, context revision, and output revision must not
advance. Add one focused success assertion and one focused failure assertion.

### 5. Complete and make the black-box project genuinely black-box

Keep this directory complete:

`D:/fpgaproject/validation_projects/phase_blackbox/b05_platform_axi/`

Required files:

```text
CLAUDE.md
README.md
AGENT3_EXECUTION_PROMPT.md
public_contract.md
runner.py
expected_outputs/*.json
evidence/
cleanup.py
```

The runner may use only stdlib and MCP SDK public calls. It must not import
`mcps.zynq_mcp` or `mcps.common`, read the ledger, call `run_tcl`, or inject a
stage. Replace internal `validate_manifest` imports with independent checks of
the public artifact paths, JSON fields, revision format, and SHA256. Load all
assertion expectations from checked-in JSON files; do not hardcode comparator
values in the runner. Use the generated wrapper path as returned by the public
operation for `pl_generate_system_top`; do not copy it to another path.

## Required Verification

Run all of these and report exact commands and exit codes:

```text
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_component.py -v
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -v -m host_live
python -m pytest mcps -q -W error::RuntimeWarning
python validation_projects/phase_blackbox/b05_platform_axi/runner.py --run-id <unique-id>
```

The host-live test and Agent3 runner must actually execute. If Vivado is not
available, report exactly `BLOCKED: REAL VIVADO NOT EXECUTED`; do not label the
phase ready based on discovery/rejection/component tests.

## Report Requirements

Return full SHA256 for modified files, actual Vivado version and worker PID,
public command trace, terminal operation status, operation/context revision
values, stage before/after, wrapper/XSA/manifest paths and independent hashes,
address/topology evidence, manifest validation result, Platform-to-PL handoff,
focused failure reason codes, regression arithmetic, black-box file inventory,
and confirmation that Agent2/Agent3/B06 were not started.

