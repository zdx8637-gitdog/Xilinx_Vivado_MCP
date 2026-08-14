"""O1 — Ledger v2 compatibility, observation, and public status contract."""
import asyncio
import copy
import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    CURRENT_SCHEMA, LEGACY_SCHEMA_V1,
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    LedgerSchemaError, LedgerWriteError, ObservationValidationError,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    OP_RUNNING, OP_SUCCEEDED,
    STATUS_SOURCE_VENDOR_RUN, BACKEND_VIVADO,
    OBS_RUNNING, HEALTH_ALIVE,
    ARTIFACT_VERIFYING, ACTION_WAIT, ACTION_NEXT_STEP,
    _now_iso,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.operation_service import (
    op_transition, op_observe, request_signature, operation_public_view,
)


REAL_REV = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


@pytest.fixture
def runtime_guard():
    root = Path(tempfile.mkdtemp())
    guard = InstanceGuard(root, "ws-o1")
    guard.determine_role()
    yield root, guard
    guard.release_owner_lock()
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.fixture
def initialized(runtime_guard):
    root, guard = runtime_guard
    path = root / "execution_ledger.json"

    def _init(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.execution_lane = EXECUTION_LANE_IDLE
        ledger.context = {
            "session_id": "sid-o1",
            "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(root / "project"),
            "current_stage": "PL_BUILD",
            "board_package_revision": REAL_REV,
            "expected_board_revision": REAL_REV,
        }
        return ledger

    ledger = ledger_transaction(guard, path, _init)
    return ledger, guard, path


def _admit(initialized, *, arguments=None):
    ledger, guard, path = initialized
    arguments = arguments or {}
    operation_id = f"op-o1-{uuid.uuid4().hex[:8]}"
    signature = request_signature(
        "sid-o1", "PL_BUILD", "pl_synthesize", arguments, REAL_REV)
    ledger = ledger_transaction(
        guard, path,
        preflight_mutator(
            "pl_synthesize", arguments, "sid-o1", "ALINX_AX7020_v1.0",
            str(path.parent / "project"), operation_id, signature,
        ),
    )
    return ledger, guard, path, operation_id


def _vendor_observation(*, observed_at=None):
    observed_at = observed_at or _now_iso()
    return {
        "status_source": STATUS_SOURCE_VENDOR_RUN,
        "backend": BACKEND_VIVADO,
        "observed_state": OBS_RUNNING,
        "vendor_status": "Running",
        "current_step": "SYNTHESIS",
        "progress_pct": None,
        "worker_health": HEALTH_ALIVE,
        "pid": 4242,
        "process_start_time": 1234.5,
        "executable_path": "D:/Xilinx/Vivado/2023.1/bin/unwrapped/win64.o/vivado.exe",
        "worker_generation": 3,
        "instance_id": "instance-o1",
        "controller_heartbeat_at": observed_at,
        "observed_at": observed_at,
        "last_output_at": observed_at,
        "detail": {"run": "synth_1"},
    }


class TestLedgerV2Migration:
    def test_o101_fresh_ledger_is_v2(self, initialized):
        ledger, _, _ = initialized
        assert ledger.schema_version == CURRENT_SCHEMA == "2.0"

    def test_o102_v1_shared_read_migrates_in_memory_without_write(self, runtime_guard):
        root, guard = runtime_guard
        path = root / "legacy.json"
        raw = {
            "schema_version": LEGACY_SCHEMA_V1,
            "ledger_sequence": 7,
            "instance_id": "legacy-i",
            "workspace_id": guard.workspace_id,
            "updated_at": "2026-08-12T00:00:00.000000Z",
            "execution_lane": "BUSY",
            "context": {"session_id": "legacy-sid", "current_stage": "PL_BUILD"},
            "active_operation": {
                "operation_id": "op-legacy", "tool_name": "pl_synthesize",
                "status": "RUNNING", "accepted_at": "2026-08-12T00:00:00.000000Z",
                "heartbeat_at": "2026-08-12T00:01:00.000000Z",
            },
            "previous_operation": None,
            "worker": {"state": "ABSENT", "worker_generation": 0},
            "dedup_registry": {"legacy-sig": "op-legacy"},
            "recent_errors": [], "recovery_log": [], "takeover_count": 0,
        }
        before = json.dumps(raw, indent=2).encode("utf-8")
        path.write_bytes(before)

        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)

        assert ledger.schema_version == CURRENT_SCHEMA
        assert ledger.ledger_sequence == 7
        assert ledger.context["session_id"] == "legacy-sid"
        assert ledger.dedup_registry == {"legacy-sig": "op-legacy"}
        op = ledger.active_operation
        assert op["observation"]["status_source"] == "RECOVERY"
        assert op["observation"]["observed_state"] == "UNKNOWN"
        assert op["observation"]["controller_heartbeat_at"] == "2026-08-12T00:01:00.000000Z"
        assert op["observation"]["observed_at"] is None
        assert path.read_bytes() == before
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == LEGACY_SCHEMA_V1

    def test_o103_first_transaction_persists_v2_once_without_data_loss(self, runtime_guard):
        root, guard = runtime_guard
        path = root / "legacy.json"
        path.write_text(json.dumps({
            "schema_version": LEGACY_SCHEMA_V1, "ledger_sequence": 9,
            "workspace_id": guard.workspace_id, "execution_lane": "IDLE",
            "context": {"session_id": "keep-me"},
            "active_operation": None,
            "previous_operation": {
                "operation_id": "op-old", "tool_name": "pl_synthesize",
                "status": "SUCCEEDED", "accepted_at": "2026-08-12T00:00:00.000000Z",
            },
            "worker": {"state": "ABSENT", "worker_generation": 4},
            "dedup_registry": {"sig": "op-old"},
            "recent_errors": [{"reason": "kept"}], "recovery_log": [],
            "takeover_count": 2,
        }), encoding="utf-8")

        ledger = ledger_transaction(guard, path, lambda current: current)

        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert ledger.schema_version == CURRENT_SCHEMA
        assert ledger.ledger_sequence == 10
        assert persisted["schema_version"] == CURRENT_SCHEMA
        assert persisted["context"]["session_id"] == "keep-me"
        assert persisted["dedup_registry"] == {"sig": "op-old"}
        assert persisted["recent_errors"] == [{"reason": "kept"}]
        assert persisted["previous_operation"]["recommended_action"] == ACTION_NEXT_STEP

    def test_o104_unknown_schema_fails_closed(self, runtime_guard):
        root, guard = runtime_guard
        path = root / "future.json"
        path.write_text(json.dumps({
            "schema_version": "3.0", "ledger_sequence": 1,
            "workspace_id": guard.workspace_id, "execution_lane": "IDLE",
        }), encoding="utf-8")
        with pytest.raises(LedgerSchemaError):
            ledger_read_shared(guard, path, guard.workspace_id)

    def test_o105_v2_malformed_observation_fails_closed(self, initialized):
        _, guard, path = initialized
        ledger, _, _, operation_id = _admit(initialized)
        data = ledger.to_dict()
        data["active_operation"]["observation"]["progress_pct"] = 101
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ObservationValidationError):
            ledger_read_shared(guard, path, guard.workspace_id)
        assert operation_id in path.read_text(encoding="utf-8")

    def test_o105b_v2_missing_observation_fails_closed(self, initialized):
        ledger, guard, path, _ = _admit(initialized)
        data = ledger.to_dict()
        del data["active_operation"]["observation"]
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ObservationValidationError):
            ledger_read_shared(guard, path, guard.workspace_id)


class TestObservationLifecycle:
    def test_o106_admission_has_complete_contract_defaults(self, initialized):
        ledger, _, _, _ = _admit(initialized)
        operation = ledger.active_operation
        assert operation["deadline_at"]
        assert operation["artifact_state"] == "NOT_APPLICABLE"
        assert operation["recommended_action"] == ACTION_WAIT
        observation = operation["observation"]
        assert set(observation) >= {
            "status_source", "backend", "observed_state", "vendor_status",
            "current_step", "progress_pct", "worker_health", "pid",
            "process_start_time", "executable_path", "worker_generation",
            "instance_id", "controller_heartbeat_at", "observed_at",
            "last_output_at", "detail",
        }
        assert observation["status_source"] == "LOCAL"
        assert observation["current_step"] == "ADMISSION"
        assert observation["observed_at"] is None
        assert observation["worker_health"] == "NOT_STARTED"

    def test_o107_op_observe_is_atomic_and_does_not_move_status_or_lane(self, initialized):
        ledger, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        before, _ = ledger_read_shared(guard, path, guard.workspace_id)

        result = op_observe(
            guard, path, operation_id, _vendor_observation(),
            artifact_state=ARTIFACT_VERIFYING, recommended_action=ACTION_WAIT,
        )

        after = result["ledger"]
        assert after.ledger_sequence == before.ledger_sequence + 1
        assert after.execution_lane == EXECUTION_LANE_BUSY
        assert after.active_operation["status"] == OP_RUNNING
        assert after.active_operation["observation"]["current_step"] == "SYNTHESIS"
        assert after.active_operation["artifact_state"] == ARTIFACT_VERIFYING

    def test_o108_controller_heartbeat_never_refreshes_observed_at(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        before_real, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert before_real.active_operation["observation"]["observed_at"] is None
        assert before_real.active_operation["observation"]["worker_health"] == "NOT_STARTED"
        observed_at = "2026-08-12T01:00:00.000000Z"
        op_observe(guard, path, operation_id,
                   _vendor_observation(observed_at=observed_at))

        heartbeat_at = "2026-08-12T01:01:00.000000Z"
        op_observe(guard, path, operation_id,
                   {"controller_heartbeat_at": heartbeat_at})
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        observation = ledger.active_operation["observation"]
        assert observation["controller_heartbeat_at"] == heartbeat_at
        assert observation["observed_at"] == observed_at

    def test_o108b_local_heartbeat_and_terminal_never_create_real_observed_at(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        heartbeat_at = "2026-08-12T01:01:00.000000Z"
        op_transition(guard, path, operation_id, OP_RUNNING,
                      heartbeat_at=heartbeat_at)
        running, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert running.active_operation["observation"]["controller_heartbeat_at"] == heartbeat_at
        assert running.active_operation["observation"]["observed_at"] is None

        op_transition(guard, path, operation_id, OP_SUCCEEDED)
        terminal, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert terminal.previous_operation["observation"]["status_source"] == "LOCAL"
        assert terminal.previous_operation["observation"]["observed_state"] == "COMPLETE"
        assert terminal.previous_operation["observation"]["observed_at"] is None

    def test_o109_real_observation_without_observed_at_is_rejected_without_write(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        before = path.read_bytes()
        with pytest.raises(ObservationValidationError):
            op_observe(guard, path, operation_id,
                       {"current_step": "SYNTHESIS"})
        assert path.read_bytes() == before

    @pytest.mark.parametrize("bad_progress", [True, -1, 101, float("nan")])
    def test_o110_invalid_progress_is_rejected(self, initialized, bad_progress):
        _, guard, path, operation_id = _admit(initialized)
        update = _vendor_observation()
        update["progress_pct"] = bad_progress
        with pytest.raises(ObservationValidationError):
            op_observe(guard, path, operation_id, update)

    def test_o111_pid_requires_complete_identity(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        update = _vendor_observation()
        update["executable_path"] = None
        with pytest.raises(ObservationValidationError):
            op_observe(guard, path, operation_id, update)

    def test_o112_terminal_transition_retains_last_real_observation(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        observation = _vendor_observation()
        op_observe(guard, path, operation_id, observation)
        op_transition(guard, path, operation_id, OP_SUCCEEDED,
                      result={"artifact": "ok"})
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        previous = ledger.previous_operation
        assert previous["status"] == OP_SUCCEEDED
        assert previous["observation"] == observation
        assert previous["recommended_action"] == ACTION_NEXT_STEP

    def test_o113_observe_terminal_operation_is_rejected_without_sequence_bump(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        op_transition(guard, path, operation_id, OP_SUCCEEDED)
        before, _ = ledger_read_shared(guard, path, guard.workspace_id)
        with pytest.raises(Exception) as exc:
            op_observe(guard, path, operation_id,
                       {"controller_heartbeat_at": _now_iso()})
        assert "OPERATION_NOT_ACTIVE" in str(exc.value)
        after, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert after.ledger_sequence == before.ledger_sequence

    def test_o114_observation_write_failure_preserves_original_ledger(self, initialized, monkeypatch):
        _, guard, path, operation_id = _admit(initialized)
        before = path.read_bytes()
        import mcps.zynq_mcp.control.execution_ledger as ledger_module

        def _fail_write(*_args, **_kwargs):
            raise LedgerWriteError("injected O1 write failure")

        monkeypatch.setattr(ledger_module, "_atomic_write", _fail_write)
        with pytest.raises(LedgerWriteError):
            op_observe(guard, path, operation_id,
                       {"controller_heartbeat_at": _now_iso()})
        assert path.read_bytes() == before


class TestPublicStatusContract:
    def test_o115_public_view_has_all_frozen_fields(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        op_observe(guard, path, operation_id, _vendor_observation())
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)

        view = operation_public_view(ledger, ledger.active_operation)

        assert view["operation_id"] == operation_id
        assert view["tool_name"] == "pl_synthesize"
        assert view["status"] == OP_RUNNING
        assert view["execution_lane"] == EXECUTION_LANE_BUSY
        assert view["workflow_stage"] == "PL_BUILD"
        assert view["current_step"] == "SYNTHESIS"
        assert view["status_source"] == STATUS_SOURCE_VENDOR_RUN
        assert view["backend"] == BACKEND_VIVADO
        assert view["observed_state"] == OBS_RUNNING
        assert view["vendor_status"] == "Running"
        assert view["worker_health"] == HEALTH_ALIVE
        assert view["worker_pid"] == 4242
        assert view["observed_at"]
        assert isinstance(view["elapsed_s"], (int, float))
        assert view["elapsed_s"] >= 0
        assert view["deadline_at"]
        assert isinstance(view["deadline_remaining_s"], (int, float))
        assert view["deadline_remaining_s"] >= 0
        assert view["artifact_state"] == "NOT_APPLICABLE"
        assert view["recommended_action"] == ACTION_WAIT
        assert view["poll_after_s"] == 10

    def test_o116_get_operation_status_uses_ledger_not_stale_cache(self, initialized):
        ledger, guard, path = initialized
        registry = OperationRegistry()
        stale = registry.admit_cache("op-cache-only", "pl_synthesize", OP_RUNNING)
        assert stale is not None

        from mcps.zynq_mcp.dispatcher import _get_operation_status

        class _Dispatcher:
            _ledger = ledger
            _guard = guard
            _ledger_path = path
            _op_registry = registry

        response = _get_operation_status({"operation_id": "op-cache-only"}, _Dispatcher())
        assert response["status"] == "error"
        assert response["error"]["code"] == "OPERATION_NOT_FOUND"
        assert response["error"]["details"]["reason_code"] == "OPERATION_NOT_FOUND"

    def test_o117_get_operation_status_query_does_not_bump_sequence(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        before_sequence = ledger.ledger_sequence
        registry = OperationRegistry()

        from mcps.zynq_mcp.dispatcher import _get_operation_status

        class _Dispatcher:
            _ledger = ledger
            _guard = guard
            _ledger_path = path
            _op_registry = registry

        response = _get_operation_status({"operation_id": operation_id}, _Dispatcher())
        assert response["status"] == "success"
        after, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert after.ledger_sequence == before_sequence

    def test_o118_channel_busy_contains_active_operation_evidence(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        from mcps.zynq_mcp.control.domain_runner import (
            CommandRunner, DomainExecutionMutex,
        )

        runner = CommandRunner(
            guard, path, OperationRegistry(), DomainExecutionMutex(), worker=None)
        response = asyncio.run(runner.run_command(
            tool_name="pl_synthesize", arguments={"different": True},
            session_id="sid-o1", board_id="ALINX_AX7020_v1.0",
            project_path=str(path.parent / "project"), executor="local",
            local_fn=None,
        ))

        assert response["status"] == "error"
        assert response["error"]["code"] == "LOCK_BUSY"
        details = response["error"]["details"]
        assert details["reason_code"] == "CHANNEL_BUSY"
        assert details["active_operation_id"] == operation_id
        assert details["tool_name"] == "pl_synthesize"
        assert details["status"] == "ACCEPTED"
        assert details["current_step"] == "ADMISSION"
        assert details["status_source"] == "LOCAL"
        assert details["backend"] == "NONE"
        assert details["observed_state"] == "NOT_STARTED"
        assert details["worker_health"] == "NOT_STARTED"
        assert details["observed_at"] is None
        assert details["worker_pid"] is None
        assert details["deadline_at"]
        assert isinstance(details["deadline_remaining_s"], (int, float))
        assert details["artifact_state"] == "NOT_APPLICABLE"
        assert details["recommended_action"] == ACTION_WAIT
        assert details["poll_after_s"] == 10

    def test_o119_registry_restore_keeps_observation_contract(self, initialized):
        _, guard, path, operation_id = _admit(initialized)
        op_transition(guard, path, operation_id, OP_RUNNING)
        op_observe(guard, path, operation_id, _vendor_observation())
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        registry = OperationRegistry()
        registry.restore_from_ledger(ledger)
        restored = registry.get(operation_id)
        assert restored.observation["current_step"] == "SYNTHESIS"
        assert restored.deadline_at == ledger.active_operation["deadline_at"]
        assert restored.recommended_action == ACTION_WAIT
