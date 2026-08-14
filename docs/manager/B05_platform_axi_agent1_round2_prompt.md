# Agent1 Prompt: B05 Platform/AXI Round 2 Functional Closure

## Target And Status

Target: Agent1, external Claude Code, continue current B05 context.

Read first:

1. `D:/fpgaproject/docs/manager/B05_platform_axi_round1_review.md`
2. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_prompt.md`
3. The current B05 implementation and shared artifact/session contracts.

Current status is `REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED`. Do not call
Agent3 or Agent2, do not start B06, and do not claim B05 complete until the real
public success flow has passed.

## Objective

Close the B05 functional vertical slice in one pass:

```text
fresh public create_session
  -> PLATFORM_DESIGN
  -> public platform_generate {}
  -> real existing Vivado worker/adapter
  -> valid PS7 + SmartConnect + AXI GPIO BD
  -> wrapper + XSA + valid Platform Manifest
  -> atomic context.platform_revision publication
  -> PL_GENERATE
  -> generated artifact is consumable by pl_generate_system_top
```

Do not add generalized security hardening or broad negative-test matrices. Test
the functional chain and its direct failure modes.

## Required Remediation

### 1. Make B05 reachable from a fresh public session

B05 is a `FRESH_SESSION` phase, not a preconditioned-ledger phase.
`create_session` already loads and validates the locked Board Package. After
that successful validation, initialize the public session at
`PLATFORM_DESIGN`, with lane IDLE and no worker/operation. Update the affected
session contract tests and documentation. Do not use a Manager-provisioned
stage and do not add a hidden setter.

Keep `platform_generate` admission restricted to `PLATFORM_DESIGN` and success
advance restricted to `PL_GENERATE`.

### 2. Use the existing worker lifecycle correctly

- Capture/pass the `SingleWorkerController`, not its private `_adapter` value.
- After command admission, call `ensure_worker()` through the controller.
- Execute fixed internal Tcl through the existing controller/adapter path so
  worker state and failures remain owned by the unified worker lifecycle.
- Map unavailable/startup failure honestly (`ADAPTER_NOT_READY` or the precise
  existing Vivado environment reason).
- Give the full Platform operation a realistic Vivado timeout (not 30 seconds).
- Do not create another process launcher, worker, server, or ledger.

### 3. Correct and prove the Vivado Platform Tcl flow

At minimum, the real flow must:

1. create the project for the Board Profile part;
2. create the BD;
3. create `processing_system7_0`;
4. source the board preset and call `set_ps_config processing_system7_0`;
5. make PS7 `DDR` and `FIXED_IO` external;
6. create a one-channel four-bit output AXI GPIO;
7. create SmartConnect and `proc_sys_reset`;
8. connect PS7 `M_AXI_GP0 -> SmartConnect -> AXI GPIO`;
9. connect `FCLK_CLK0` to PS7 `M_AXI_GP0_ACLK`, SmartConnect, GPIO, and reset;
10. connect resets;
11. expose the GPIO output as a four-bit external LED port;
12. assign and verify the deterministic GPIO address `0x41200000` with 64 KiB
    range, failing on collision or mismatch;
13. run and pass `validate_bd_design`, save/generate the BD target;
14. create/add the wrapper and obtain its exact returned path;
15. export a Platform XSA without requesting a nonexistent bitstream;
16. verify wrapper and XSA exist, are non-empty, and match reported SHA256.

Parse the actual legacy response shape (`data.output`). Make address-query Tcl
print an explicit machine-readable mapping; do not infer success from an empty
map. Missing wrapper must be `XSA_EXPORT_FAILED`/the selected exact wrapper
error, never an all-zero SHA success.

Use the real Vivado tool version in evidence. Map BD validation and XSA export
failures to their required exact reason codes rather than collapsing everything
to `VIVADO_ERROR`.

### 4. Produce the existing Platform Manifest contract

Do not invent a B05-only manifest schema. Reuse
`mcps.common.artifact_schema` and `mcps.common.revision` rules:

- `schema_version = "1.0"`;
- `manifest_type = "platform"`;
- `status = "locked"`;
- relative `xsa_path` and `bd_wrapper_path`;
- valid `xsa_sha256` and `bd_wrapper_sha256`;
- `address_map` and `clock_tree`;
- deterministic `revision_inputs` containing the required board profile, tool
  version, source files, and config files plus any deterministic topology input;
- `manifest_revision == platform_revision == compute_revision(revision_inputs)`;
- canonical filename from the shared `_revision_to_filename` rule.

Call the shared validator before publication and publish atomically/no-replace.
The checked-in component tests must call `validate_manifest()` and assert zero
issues. Also prove that the generated manifest can be consumed by the existing
B04 `generate_system_top`/public `pl_generate_system_top` path.

### 5. Publish the output revision atomically with success

On `platform_generate` terminal success, the same ledger transaction must:

- set operation status `SUCCEEDED`;
- set `output_artifact_revision` to the generated platform revision;
- set `context.platform_revision` to that exact revision;
- advance `PLATFORM_DESIGN -> PL_GENERATE`;
- preserve compact result fields for XSA, wrapper, manifest, address map, and
  their hashes.

On any failure, none of those revision/stage fields may advance. Test both
success and failure.

### 6. Replace registration-only evidence with real public success evidence

Keep discovery/schema tests, but add a real MCP SDK test using
`stdio_client + ClientSession` against `python -m mcps.zynq_mcp.server`:

1. start with a fresh runtime;
2. call `create_session` and observe `PLATFORM_DESIGN`;
3. call `platform_generate {}` and receive an operation ID;
4. call `wait_operation` until `SUCCEEDED`;
5. observe lane IDLE, stage `PL_GENERATE`, worker state/PID, and matching
   `platform_revision` through public APIs;
6. independently hash and validate wrapper, XSA, and manifest;
7. verify PS7/GPIO/SmartConnect topology and GPIO address from generated
   evidence;
8. call public `pl_generate_system_top` with the generated wrapper and prove the
   Platform-to-PL handoff succeeds.

If Vivado is unavailable, report the exact blocker and leave status
`BLOCKED: REAL VIVADO NOT EXECUTED`; do not count mocks, discovery, or rejection
tests as host-live success.

Direct failure tests should cover: wrong stage, additional public argument,
board revision/profile mismatch, BD validation failure, wrapper/XSA export
failure, and no stage/revision advance. Avoid unrelated path-attack expansion.

### 7. Build the complete Agent3 project now

Create:

```text
D:/fpgaproject/validation_projects/phase_blackbox/b05_platform_axi/
  CLAUDE.md
  README.md
  AGENT3_EXECUTION_PROMPT.md
  public_contract.md
  runner.py
  expected_outputs/
  evidence/
  cleanup.py
```

This project is `FRESH_SESSION`. Its runner must create a clean project via
public `create_session` and use only MCP SDK public calls. It must not import
`mcps.zynq_mcp` internals, read/write the ledger, call `run_tcl`, or inject a
stage. All Agent3 instructions must live inside this project; Agent3 should not
need `docs/manager`.

Prepare success, wrong-stage/schema, and direct failure expectations, but do
not execute or impersonate Agent3.

## Verification And Report

Run:

```text
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_component.py -v
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -v -m host_live
python -m pytest mcps -q -W error::RuntimeWarning
```

Return:

- modified files with full SHA256;
- exact public schema and tool/capability counts;
- real public MCP command trace and terminal operation evidence;
- actual Vivado version and worker PID lifecycle;
- wrapper/XSA/manifest paths, sizes, and independent SHA256;
- shared manifest validator result (`0` issues);
- stage and `platform_revision` before/after proof;
- Platform-to-PL handoff result;
- focused negative results with exact reason codes;
- full regression arithmetic;
- completed black-box project inventory;
- confirmation that Agent3/Agent2 were not called and B06 was not started.

Use status `READY FOR MANAGER RE-REVIEW / AGENT3 NOT EXECUTED` only if the real
Vivado-backed public success chain passed and the phase project exists.

