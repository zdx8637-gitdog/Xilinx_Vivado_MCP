# B05 Platform/AXI Round 2 Manager Review

Date: 2026-08-08

Status: REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED

## Decision

B05 is not ready for Agent3. The report's 2 Vivado success tests were
deselected, so the required public success path remains unexecuted. The
component tests passing and the black-box directory existing do not prove that
the worker can create a valid Platform artifact or that B04 can consume it.

This review is limited to functional correctness. It does not request another
general security audit.

## Blocking Findings

### P0-1: Real success is not demonstrated

`test_b05_platform_public.py` marks all tests `host_live`; the reported result
is `714 passed, 1 skipped, 2 deselected`. The two tests in
`TestRealVivadoSuccess` therefore did not run. No public trace proving
`create_session -> PLATFORM_DESIGN -> platform_generate -> SUCCEEDED ->
PL_GENERATE` exists.

### P0-2: Generated manifest is validated with relative paths

`platform_domain.py` builds the manifest with `xsa_path="platform.xsa"` and
`bd_wrapper_path="hdl/platform_bd_wrapper.v"`, then calls `validate_manifest`
before resolving those paths against `project_path`. The shared validator
checks file existence using the current process directory, so a project under a
temporary runtime produces `PATH_NOT_FOUND` and cannot pass its own validation.
The manifest must be validated using a project-root-resolved copy, while the
published manifest retains the required relative paths.

### P0-3: AXI GPIO flow is internally contradictory

The Tcl first configures `C_IS_DUAL {0}` and `C_ALL_OUTPUTS {1}`, then later
sets `C_IS_DUAL {1}` and `C_GPIO2_WIDTH {4}` but connects `gpio_io_o`. This is
not the requested one-channel four-bit GPIO contract and can make BD
validation fail. Configure one channel once and connect its corresponding
output port to the four-bit LED external port.

### P0-4: Tcl failures are collapsed to the wrong reason code

`_tcl()` converts every failed `run_tcl` response into `AdapterError`, including
`validate_bd_design` and `write_hw_platform` failures. Consequently
`BD_VALIDATION_FAILED` and `XSA_EXPORT_FAILED` cannot be emitted as specified.
Preserve exact mapping for the validation and export steps, and add focused
tests for those direct failures.

### P1-1: Output revision is not bound to the operation

`op_transition(..., result=result)` receives no `output_artifact_revision`.
The implementation only updates `context.platform_revision`. On success, the
operation record must contain `output_artifact_revision` equal to the generated
revision, atomically with stage and context updates.

### P1-2: Manifest publication is not atomic and the imported publisher is unused

The code imports `publish_manifest` but writes the final manifest with
`open(..., "w")`. Use the shared publisher after validation so the generated
manifest follows the existing no-replace contract.

### P1-3: Platform project does not use the Board Profile part

The Tcl hardcodes `xc7z020clg400-2`. Read the validated Board Profile part and
use that value when creating the project; reject an unavailable or inconsistent
part through the existing platform error mapping.

### P1-4: Black-box runner is not independent and expected contracts are empty

`b05_platform_axi/runner.py` imports `mcps.common.artifact_schema`, which is an
internal repository module rather than a public MCP API. It also hardcodes all
assertions while `expected_outputs/` is empty. The runner must independently
check the public response and artifact facts with stdlib, and its expected
assertions must be checked-in JSON consumed by the runner. Use the manifest's
reported wrapper path for the PL handoff; do not copy or rename the artifact to
make the test pass.

## Gate

Agent1 must fix the above functional issues, run the real host-live test (not
`--deselect` and not `-m 'not host_live'`), run focused regression, and preserve
the execution evidence. Only then may Manager issue an Agent3 execution prompt.

