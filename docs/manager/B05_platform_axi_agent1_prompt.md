# Agent1 Prompt: B05 Platform/AXI Domain Minimum Vertical Slice

## Target And Memory

**Target: Agent1, external Claude Code, fresh memory.** The user manually forwards this prompt. Agent1, Agent2, and Agent3 are external Claude Code agents; they are not Codex sub-agents.

## Current Gate

B04 R3.1-C phase public MCP smoke has passed independent Agent3 execution and Manager evidence review:

- 5/5 scenarios passed;
- 107/107 assertions consumed and passed;
- `pl_generate_system_top` public MCP behavior and `PL_GENERATE -> PL_BUILD` were verified;
- evidence is preserved under `D:\tmp\r3_1c_agent3_execution\prov_8465c6b84fb3\evidence\agent3_r3_1c_20260808_1`.

Read `D:\fpgaproject\docs\manager\B04_R3_1C_agent3_review.md` for the accepted boundary. B05 may now begin. Agent2 remains prohibited until B08.

## Objective

Implement the **minimum B05 Platform/AXI Domain vertical slice** inside the unified `zynq_mcp` server. The functional result must create the Platform required by the later GPIO workflow:

```text
board profile + Platform design request
  -> Zynq-7000 PS7 block design
  -> AXI GPIO connected to PS GP AXI
  -> clocks/resets/interfaces connected
  -> deterministic address assignment
  -> design validation
  -> HDL wrapper
  -> Platform XSA
  -> revision-pinned Platform manifest
  -> workflow stage advances to PL_GENERATE
```

Functionality and testability are the priority. Do not turn this phase into another generalized security framework.

## Required First Reads

Before editing, read the relevant sections of:

1. `docs/brick_development_plan.md` — B05 scope and completion gate.
2. `docs/architecture_ai_zynq7020.md` — Platform Domain API and ownership boundary.
3. `docs/development/tests/brick_test_workflow_architecture.md` — B05 Agent1/Agent3 gates and artifact handoff.
4. `docs/manager/B04_R3_1C_agent3_review.md` — accepted B04 boundary.
5. Existing unified server patterns under `mcps/zynq_mcp/`, especially capabilities, dispatcher, domain runner, operation lifecycle, stage gate/advance, PL domain, Vivado adapter, and public MCP SDK tests.
6. Existing board package for `ALINX_AX7020_v1.0` and the historical Platform skeleton only as reference. Do not revive a separate Platform MCP server.

Report the exact B05 API names selected from the architecture before implementation. Implement only the minimal API set needed for the flow above; do not mechanically expose every planned Platform API if it is not required by this slice.

## Product Boundaries

- There remains exactly one public `zynq_mcp` server.
- Platform is an internal domain under `mcps/zynq_mcp/domains/platform/`, sharing the existing context, execution ledger, operation service, preflight gate, and single execution channel.
- Reuse the existing Vivado adapter/worker and `Xilinx_Vivado_MCP` process layer. Do not implement a second Vivado launcher, process pool, per-session worker, or independent ledger.
- Platform Domain owns BD/PS7/AXI topology and Platform XSA/manifest production.
- Platform Domain does not own final PL bitstream, JTAG, board programming, PS application code, UART, or GPIO pin assignment decisions.
- The board package is the source of device/PS7/clock/reset constraints. Do not hardcode a second board profile in the domain.
- Public commands return `operation_id`; use `wait_operation`/`get_operation_status` for terminal evidence. Detailed current workflow state remains observable through the existing public control APIs.

## Decisions That Must Not Drift

### Public schema

The selected public API for this slice is exactly one tool: `platform_generate`.
Its public input is an empty JSON object:

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

`session_id`, `board_id`, `project_path`, `board_profile_sha256`, and
`board_package_revision` are internal execution inputs obtained from the same
ledger snapshot used for admission. Do not expose them as caller-controlled
arguments, and do not accept hidden `next_stage`, Tcl, output, or force fields.
The component function may retain the internal signature described below.

### Stage reachability

`platform_generate` admits only from `PLATFORM_DESIGN` and advances to
`PL_GENERATE` after the artifacts are really generated and validated. The
current frozen `create_session` implementation starts at `IDLE`; this slice
does not silently change that B04 contract and does not invent a stage jump.
Therefore component/public integration tests may use an explicit Manager test
fixture whose ledger is already at `PLATFORM_DESIGN`. The B05 phase project must
label this as `PRECONDITIONED_PLATFORM_DESIGN` unless Agent1 adds a separately
reviewed, backward-compatible public board-validation transition. Do not claim
that `create_session -> platform_generate` was tested from a fresh `IDLE`
session when it was not.

### Real Vivado boundary

The production executor must use the existing `SingleWorkerController` /
`VivadoAdapter` and the existing `Xilinx_Vivado_MCP` process. A fixed internal
Tcl orchestration sent through that adapter is acceptable because the public
surface remains only `platform_generate`; arbitrary caller Tcl is forbidden.
Do not create a second launcher or worker, and do not write dummy XSA/BD
artifacts on a success path. When Vivado or the adapter is unavailable, return
`ADAPTER_NOT_READY` (or the mapped exact error) and leave the stage unchanged.
Mocked component evidence and real Vivado/XSA evidence must be reported in
separate sections. A mock must never be counted as a real XSA pass.

## Minimum Functional Contract

Implement the smallest coherent public contract that can complete the B05 flow. Whether this is one coarse command or a small ordered command set must follow the architecture and current server conventions, but it must satisfy all of the following:

- strict input schema with no hidden `next_stage`, project path, board revision, Tcl, or internal control parameters;
- stage admission from the correct pre-Platform state and deterministic advance to `PL_GENERATE` only after successful XSA and manifest validation;
- PS7 configured from the selected board package;
- one AXI GPIO suitable for the later four-LED GPIO slice;
- required AXI interconnect, clocks, resets, and interface connections;
- deterministic, machine-readable address assignment;
- `validate_bd_design` equivalent success before export;
- reproducible wrapper, XSA, and Platform manifest artifacts;
- Platform manifest includes board identity/revision, tool version, topology/address/clock data, XSA/wrapper paths and SHA256 values, and a deterministic `platform_revision` derived using the existing artifact rules;
- failed, interrupted, or stale operations do not advance the stage or publish a valid manifest;
- all operations obey deduplication, single-channel serialization, worker ownership, recovery, and compact persisted-result conventions already established in B04.

Do not invent a new response envelope, state store, runner, or artifact schema when an existing project contract applies.

## Implementation Sequence

1. Establish a B05 test plan from the public contract and stage/artifact expectations.
2. Implement pure Platform domain components and focused unit tests for deterministic topology/configuration, address map, manifest generation, and exact error mapping.
3. Integrate through dispatcher/domain runner/operation lifecycle and public tool registration. Add `platform_generate` to the existing domain dispatch path; do not add a second server or lifecycle.
4. Add real MCP SDK tests using `stdio_client` + `ClientSession` against `python -m mcps.zynq_mcp.server`.
5. Exercise a real Vivado-backed software flow if the local toolchain is available. No physical board is required for Platform XSA generation. Clearly separate mocked/component evidence from real Vivado/XSA evidence. If Vivado is unavailable, mark the host-live test `BLOCKED/SKIPPED` with the probe output; never convert it to PASS.
6. Prepare the B05 phase black-box project for later Agent3 execution at
   `validation_projects/phase_blackbox/b05_platform_axi/`. It must contain its
   own `CLAUDE.md`, `README.md`, `AGENT3_EXECUTION_PROMPT.md`, `runner.py`,
   expected-output files, and evidence instructions so Agent3 need not read
   `docs/manager`. Do not execute it as Agent3.
7. Run focused tests and the full regression.

## Required Tests

At minimum cover:

- public tool discovery and schema;
- happy path through public MCP: admission, wait, terminal success, stage advance, compact result;
- generated BD topology and AXI GPIO configuration;
- address map determinism and collision rejection;
- XSA and manifest existence plus SHA/revision consistency;
- board/revision mismatch and stale artifact rejection;
- wrong-stage rejection with no operation/state mutation;
- invalid public schema input rejection;
- Vivado validation/export failure with exact structured reason code and no stage advance;
- duplicate request behavior and cross-domain single-channel busy rejection;
- interrupted/unknown outcome behavior and recovery compatibility;
- repeated clean run produces equivalent functional artifacts after excluding explicitly nondeterministic metadata.

For the phase black-box project, the runner may only use MCP SDK
`ClientSession`/`stdio_client` and public tools. It must not import
`mcps.zynq_mcp` internals, read or modify the ledger, call `run_tcl`, or create
the `PLATFORM_DESIGN` precondition itself. The Manager harness supplies the
controlled precondition and the report must identify it as such.

Tests must assert exact structured fields and reason codes. Do not call internal modules from the Agent3 black-box runner.

## Scope Prohibitions

- Do not start B06 PS/ARM development, B07 workflow integration, B08 final Agent1 acceptance, or B09 Agent2 testing.
- Do not implement PL synthesis/place-route/bitstream/JTAG tools as part of B05.
- Do not program hardware or require UART/LED evidence.
- Do not modify root `.mcp.json` unless the unified server genuinely cannot start with the existing configuration; any such need must be reported before editing.
- Do not modify accepted B04 production/tests merely to simplify B05. If a shared contract must change, isolate it, explain compatibility, and provide regression evidence.
- Do not change `create_session` merely to make the B05 black-box convenient. If
  fresh-session reachability is required, stop and report the exact missing
  board-validation transition for Manager approval rather than hiding it in
  `platform_generate`.
- Do not call Agent3 or Agent2 and do not claim B05 frozen.
- Avoid broad security hardening unrelated to the functional B05 contract.

## Evidence And Report

Return:

- exact selected public API list and why each API is necessary;
- modified files with full SHA256;
- component, integration, public MCP SDK, and real Vivado/XSA test results clearly separated;
- exact tool counts/capability changes;
- exact stage transition and artifact chain;
- generated XSA/manifest/wrapper locations and SHA256 values;
- negative-case matrix with exact reason codes;
- full regression arithmetic and runtime;
- phase black-box project path and readiness status;
- any dependency/toolchain blocker or hardware boundary;
- confirmation that Agent3/Agent2 were not called and B06 was not started.

Use status `READY FOR MANAGER REVIEW / AGENT3 NOT EXECUTED`. Manager Reviewer will review functional correctness first and only then route a fresh-memory Agent3 phase test.
