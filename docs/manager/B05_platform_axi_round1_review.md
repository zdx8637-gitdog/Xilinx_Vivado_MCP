# B05 Platform/AXI Round 1 Manager Review

Date: 2026-08-08

Status: REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED

## Outcome

The public tool is registered and the focused tests pass, but the functional
vertical slice has not been demonstrated and the current production path cannot
complete it. B05 is not ready for Agent3.

This review is intentionally limited to functional closure: worker startup,
Vivado BD/XSA generation, artifact contract compatibility, workflow state, and
the missing phase project.

## Blocking Findings

### P0-1: The production path never starts the Vivado worker

`dispatcher.py` reads `disp._worker._adapter` before execution. A normal server
starts with `_adapter is None`, and no production call to `ensure_worker()`
exists. Therefore a correctly staged `platform_generate` reaches
`generate_platform(adapter=None)` and fails with `ADAPTER_NOT_READY`; it cannot
generate an XSA.

### P0-2: The generated Platform Manifest violates the existing contract

The implementation writes `schema_version=1.0.0`,
`manifest_revision=1.0.0`, `status=active`, `wrapper_path`, and
`wrapper_sha256`, while the shared contract requires `schema_version=1.0`, a
SHA revision derived from `revision_inputs`, `status=locked`,
`bd_wrapper_path`, and `bd_wrapper_sha256`.

An independent call to `validate_manifest(manifest, "platform")` returned seven
issues: unsupported schema, missing `bd_wrapper_path`, missing
`bd_wrapper_sha256`, missing `revision_inputs`, invalid manifest revision,
invalid status, and platform/manifest revision mismatch. The generated manifest
therefore cannot be accepted as the Platform artifact consumed by B04.

### P0-3: Success does not publish `platform_revision` to session context

The terminal transition only advances `current_stage`. It does not atomically
set `context.platform_revision`, nor bind the same value as the operation's
`output_artifact_revision`. A subsequent `pl_generate_system_top` snapshots an
empty platform revision and fails even if files were generated.

### P0-4: The Tcl flow is not executable as written

The board preset only defines `set_ps_config`; it does not create the PS7 cell
or call the procedure. The implementation sources the text but never creates
`processing_system7_0` and never calls `set_ps_config`, so later PS7 connections
have no valid source cell.

Additional success-path defects are present:

- PS7 `DDR` and `FIXED_IO` are not made external;
- the PS7 `M_AXI_GP0_ACLK` input is not connected;
- AXI GPIO is not connected to a four-bit external LED port;
- wrapper discovery uses a shallow glob, silently substitutes an all-zero SHA
  when the wrapper is absent, and continues;
- `write_hw_platform -include_bit` requests a bitstream even though B05 does not
  build one;
- response parsing reads `data.tcl_output`, but `run_tcl` returns `data.output`;
- address-map Tcl does not emit the `name -> address` format expected by the
  parser, so the declared map is empty.

### P1-1: The public SDK suite does not test public success

All six public tests cover discovery, schema/capabilities, or wrong-stage
rejection. None invokes a staged happy path, waits for `SUCCEEDED`, observes
`PL_GENERATE`, starts a worker, or verifies a real wrapper/XSA/manifest. Marking
these tests `host_live` does not make them Vivado-backed.

The second rejection test is also named as a `PL_GENERATE` test but creates a
fresh `IDLE` session and never changes its stage.

### P1-2: The required B05 phase black-box project does not exist

`validation_projects/phase_blackbox/b05_platform_axi/` is absent. An earmarked
path is not a prepared test project.

### P1-3: Manager provisioning would violate the B05 fresh-session gate

The phase workflow specifies `FRESH_SESSION` for B05. Agent3 must start a clean
server and call public `create_session`; a Manager-injected
`PLATFORM_DESIGN` ledger is not permitted. Since `create_session` already
validates the locked Board Package, B05 must make that successful validation
lead to the public `PLATFORM_DESIGN` starting stage, or introduce an explicitly
reviewed public board-validation transition. The selected one-tool slice favors
the former.

## Verification Performed

- `test_b05_platform_component.py` + `test_b05_platform_public.py`: 28 passed.
- Shared Platform Manifest validation: failed with seven issues listed above.
- B05 phase project existence check: absent.
- No hardware/JTAG/board operation was run.

## Gate

Agent1 must close all findings in one functional remediation pass, run a real
Vivado-backed public SDK success flow, and create the complete phase project.
Only then should Manager consider an Agent3 prompt.

