# B05 Platform/AXI Round 3 Manager Review

Date: 2026-08-08

Status: REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED

## Decision

B05 is still not ready for Agent3. The report says the Vivado success chain is
"ready to execute" and that the two tests require an exclusive runtime; it
does not provide a passed host-live trace. More importantly, the current
implementation makes the claimed Platform-to-PL handoff incompatible with the
existing B04 consumer.

## Blocking Findings

### P0-1: Published manifest paths break the B04 handoff

`platform_domain.py` publishes `xsa_path` and `bd_wrapper_path` as absolute
paths. The existing B04 binder in `mcps/zynq_mcp/domains/pl/system_top.py`
passes manifest paths through `_validate_contained()`, which explicitly rejects
absolute manifest paths as `MANIFEST_PATH_ESCAPE`. Therefore a generated
manifest cannot be consumed by `pl_generate_system_top`, regardless of the
local `validate_manifest()` result.

The manifest must be published with project-relative paths (`platform.xsa` and
`hdl/platform_bd_wrapper.v`). Validation may use a project-root-resolved copy,
but the persisted contract must remain relative.

### P0-2: The public handoff test is not testing the generated wrapper directly

The B05 black-box runner falls back to the hardcoded
`hdl/platform_bd_wrapper.v` path and does not derive the path from the returned
artifact. The previous public test also copied/renamed the wrapper before
calling B04. This can make a broken artifact appear consumable. The test must
derive a relative path from the public `wrapper_path` and call
`pl_generate_system_top` with that path, without copying or renaming the file.

### P1-1: Artifact SHA relationships are incomplete in black-box evidence

The runner checks that XSA/wrapper/manifest files exist and that manifest SHA
fields have the right format, but does not independently compare disk hashes to
the operation result or compare manifest artifact hashes to those result
hashes. Add these direct public-result-to-disk and manifest-to-disk checks.

### P1-2: Host-live success remains unproven

The report gives no exit code, operation ID, Vivado version, worker PID,
artifact sizes/hashes, or preserved evidence for a passing real Vivado run.
The status cannot be `READY FOR MANAGER RE-REVIEW` until the actual host-live
test and the project runner pass. If Vivado is unavailable, report
`BLOCKED: REAL VIVADO NOT EXECUTED`.

This review is functional only; no additional generalized security audit is
requested.

## Gate

Fix the relative manifest and direct handoff test, then execute the real
host-live success chain and the B05 black-box runner. Agent3 remains
unauthorized until those results are preserved and independently reviewable.

