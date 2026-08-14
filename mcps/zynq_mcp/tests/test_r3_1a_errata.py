"""
test_r3_1a_errata.py — E003/E004/E005 Errata tests. @pytest.mark.asyncio.
Run: -W error::RuntimeWarning. 0 warnings required.
No asyncio.sleep guessing — all async waits are deterministic (Event + poll ledger).
"""
import asyncio, json, os, shutil, tempfile, time, uuid
from pathlib import Path
import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED,
    OP_OUTCOME_UNKNOWN,
    OP_NON_TERMINAL, OP_TERMINAL,
    ChannelBusyError,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.operation_service import (
    op_transition, request_signature,
)
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
    ResourceRequirement,
)
from mcps.zynq_mcp.control.context import STAGE_PLATFORM_DESIGN, STAGE_PL_GENERATE, STAGE_PL_BUILD
from mcps.common.revision import is_sha256

SH = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
BOARD = "ALINX_AX7020_v1.0"


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-r3_1a"); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _new_sid(): return f"session-{uuid.uuid4().hex[:8]}"


def _setup_ledger(rtg, stage):
    rt, g = rtg; lp = rt / "l.json"; sid = _new_sid()
    def _i(l):
        l.instance_id = g.instance_id; l.workspace_id = "ws-r3_1a"
        l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
        l.context["session_id"] = sid; l.context["board_id"] = BOARD
        l.context["board_package_revision"] = SH
        l.context["expected_board_revision"] = SH
        l.context["current_stage"] = stage
        return l
    return ledger_transaction(g, lp, _i), g, lp, sid


def _admit_op(guard, ledger_path, sid, stage, tool_name="pl_test"):
    op_id = f"op-{uuid.uuid4().hex[:8]}"
    sig = request_signature(sid, stage, tool_name, {}, SH)
    def _m(l):
        l.active_operation = {
            "operation_id": op_id, "tool_name": tool_name,
            "status": OP_ACCEPTED, "api_category": "command",
            "session_id": sid, "board_id": BOARD,
            "project_path": "/tmp/p", "workflow_stage": stage,
            "request_signature": sig,
            "worker_generation": 0,
            "input_artifact_revision": SH,
            "accepted_at": _now_iso(), "started_at": None,
            "heartbeat_at": None, "finished_at": None, "deadline_at": None,
            "output_artifact_revision": None, "completion_evidence": None,
            "error": None, "progress_pct": None,
        }
        l.execution_lane = EXECUTION_LANE_BUSY
        if not isinstance(l.dedup_registry, dict): l.dedup_registry = {}
        l.dedup_registry[sig] = op_id
        return l
    ledger_transaction(guard, ledger_path, _m)
    op_transition(guard, ledger_path, op_id, OP_RUNNING)
    return op_id


async def _wait_terminal(guard, ledger_path, op_id, timeout_s=5.0):
    """Deterministic: poll ledger until active_operation is None or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        l, _ = ledger_read_shared(guard, ledger_path)
        if l.active_operation is None:
            return l
        if l.active_operation.get("operation_id") != op_id:
            return l
        await asyncio.sleep(0.01)
    return None  # timeout


@pytest.fixture
def pled(rtg):
    rt, g = rtg; lp = rt / "l.json"
    def _i(l): l.instance_id=g.instance_id; l.workspace_id="ws-r3_1a"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
    return ledger_transaction(g, lp, _i), g, lp


# ============================================================
# E003: execution_gate restricts pl_generate_system_top to PL_GENERATE only
# ============================================================

class TestE003:

    def test_e003_pl_generate_at_pl_generate_allowed(self):
        from mcps.zynq_mcp.control.execution_gate import _check_stage
        blocked = _check_stage("pl_generate_system_top", "PL_GENERATE", None)
        assert blocked is False

    def test_e003_pl_generate_at_platform_design_rejected(self):
        from mcps.zynq_mcp.control.execution_gate import _check_stage
        blocked = _check_stage("pl_generate_system_top", "PLATFORM_DESIGN", None)
        assert blocked is True

    def test_e003_pl_generate_at_idle_rejected(self):
        from mcps.zynq_mcp.control.execution_gate import _check_stage
        blocked = _check_stage("pl_generate_system_top", "IDLE", None)
        assert blocked is True

    def test_e003_other_pl_apis_unchanged(self):
        from mcps.zynq_mcp.control.execution_gate import _check_stage
        blocked = _check_stage("pl_create_project", "PL_GENERATE", None)
        assert blocked is False
        blocked2 = _check_stage("pl_synthesize", "PL_BUILD", None)
        assert blocked2 is False

    @pytest.mark.asyncio
    async def test_e003_preflight_at_platform_design_rejected(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PLATFORM_DESIGN")
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_generate_system_top", {},
            sid, BOARD, "/tmp/p", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "STAGE_PREREQUISITE_UNMET"

    @pytest.mark.asyncio
    async def test_e003_preflight_at_pl_generate_allowed(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        done = asyncio.Event()
        async def _fn(a): done.set(); return {"status": "success", "data": {}}
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        r = await runner.run_command("pl_generate_system_top", {},
            sid, BOARD, "/tmp/p", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "success"; oid = r["data"]["operation_id"]
        l2 = await _wait_terminal(g, lp, oid, timeout_s=5.0)
        assert l2 is not None, "ledger did not reach terminal state"
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["operation_id"] == oid


# ============================================================
# E004: atomic next_stage chain
# ============================================================

class TestE004:

    def test_e004_next_stage_none_no_advance(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        r = op_transition(g, lp, oid, OP_SUCCEEDED)
        assert r["status"] == "success"
        l2, _ = ledger_read_shared(g, lp)
        assert l2.context["current_stage"] == "PL_GENERATE"
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    def test_e004_pl_generate_to_pl_build_atomic_success(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r["status"] == "success"
        l2, _ = ledger_read_shared(g, lp)
        assert l2.context["current_stage"] == "PL_BUILD"
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["operation_id"] == oid
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.active_operation is None
        ev = l2.previous_operation.get("completion_evidence", {})
        assert ev.get("stage_advanced_from") == "PL_GENERATE"
        assert ev.get("stage_advanced_to") == "PL_BUILD"

    def test_e004_illegal_stage_transition_rejected(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        l_before, sha_before = ledger_read_shared(g, lp)
        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="OBSERVATION")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "ILLEGAL_STAGE_TRANSITION"
        l_after, sha_after = ledger_read_shared(g, lp)
        assert sha_before == sha_after, "Ledger bytes changed despite rejected transaction"
        assert l_after.context["current_stage"] == "PL_GENERATE"
        assert l_after.active_operation is not None
        assert l_after.active_operation["status"] == OP_RUNNING
        assert l_after.active_operation["operation_id"] == oid

    def test_e004_completion_evidence_none_becomes_dict(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        l0, _ = ledger_read_shared(g, lp)
        assert l0.active_operation["completion_evidence"] is None
        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r["status"] == "success"
        l2, _ = ledger_read_shared(g, lp)
        ev = l2.previous_operation["completion_evidence"]
        assert isinstance(ev, dict)
        assert ev["stage_advanced_from"] == "PL_GENERATE"
        assert ev["stage_advanced_to"] == "PL_BUILD"

    def test_e004_completion_evidence_dict_preserves_existing(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        def _set_ev(lx):
            lx.active_operation["completion_evidence"] = {"existing_key": "val42"}
            return lx
        ledger_transaction(g, lp, _set_ev)
        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r["status"] == "success"
        l2, _ = ledger_read_shared(g, lp)
        ev = l2.previous_operation["completion_evidence"]
        assert ev["existing_key"] == "val42", "Existing completion_evidence key was lost"
        assert ev["stage_advanced_from"] == "PL_GENERATE"
        assert ev["stage_advanced_to"] == "PL_BUILD"

    def test_e004_completion_evidence_corrupt_rejected(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        def _set_ev(lx):
            lx.active_operation["completion_evidence"] = "corrupt_string_value"
            return lx
        ledger_transaction(g, lp, _set_ev)
        l_before, sha_before = ledger_read_shared(g, lp)
        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "COMPLETION_EVIDENCE_CORRUPT"
        l_after, sha_after = ledger_read_shared(g, lp)
        assert sha_before == sha_after
        assert l_after.context["current_stage"] == "PL_GENERATE"

    @pytest.mark.asyncio
    async def test_e004_command_runner_passes_next_stage(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        done = asyncio.Event()
        async def _fn(a): done.set(); return {"status": "success", "data": {}}
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        r = await runner.run_command("pl_generate_system_top", {},
            sid, BOARD, "/tmp/p", executor="local", local_fn=_fn, timeout_s=5,
            next_stage="PL_BUILD")
        assert r["status"] == "success"; oid = r["data"]["operation_id"]
        l2 = await _wait_terminal(g, lp, oid, timeout_s=5.0)
        assert l2 is not None, "ledger did not reach terminal state"
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.context["current_stage"] == "PL_BUILD"
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        ev = l2.previous_operation.get("completion_evidence", {})
        assert ev.get("stage_advanced_from") == "PL_GENERATE"
        assert ev.get("stage_advanced_to") == "PL_BUILD"

    @pytest.mark.asyncio
    async def test_e004_command_runner_default_no_advance(self, rtg):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        done = asyncio.Event()
        async def _fn(a): done.set(); return {"status": "success", "data": {}}
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        r = await runner.run_command("pl_test", {},
            sid, BOARD, "/tmp/p", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "success"; oid = r["data"]["operation_id"]
        l2 = await _wait_terminal(g, lp, oid, timeout_s=5.0)
        assert l2 is not None, "ledger did not reach terminal state"
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.context["current_stage"] == "PL_GENERATE", \
            "Stage should NOT advance when next_stage is not set"

    def test_e004_atomic_write_failure_ledger_unchanged(self, rtg, monkeypatch):
        l, g, lp, sid = _setup_ledger(rtg, "PL_GENERATE")
        oid = _admit_op(g, lp, sid, "PL_GENERATE")
        l_before, sha_before = ledger_read_shared(g, lp)
        assert l_before.active_operation["operation_id"] == oid

        from mcps.zynq_mcp.control import execution_ledger as el_mod
        def _fail_write(path, data):
            tmp_path = type(Path())(str(path) + ".tmp")
            with open(tmp_path, "wb") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")[:50])
                f.flush()
            raise OSError("simulated write failure")
        monkeypatch.setattr(el_mod, "_atomic_write", _fail_write)

        r = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r["status"] == "error"

        # Original Ledger unchanged
        l_after, sha_after = ledger_read_shared(g, lp)
        assert sha_before == sha_after, "Ledger SHA changed despite write failure"
        assert l_after.context["current_stage"] == "PL_GENERATE"
        assert l_after.active_operation is not None
        assert l_after.active_operation["status"] != OP_SUCCEEDED
        assert l_after.active_operation["operation_id"] == oid

        # .tmp file may exist — must NOT be parsed as official Ledger
        tmp_path = type(Path())(str(lp) + ".tmp")
        # ledger_read_shared returns original valid file, not .tmp

        # Subsequent successful transaction works and cleans up .tmp
        monkeypatch.undo()
        r2 = op_transition(g, lp, oid, OP_SUCCEEDED, next_stage="PL_BUILD")
        assert r2["status"] == "success"
        l_final, _ = ledger_read_shared(g, lp)
        assert l_final.context["current_stage"] == "PL_BUILD"
        assert l_final.previous_operation["status"] == OP_SUCCEEDED
        assert not tmp_path.exists(), f".tmp file still exists: {tmp_path}"


# ============================================================
# E005: one authoritative board_profile_load + B02 compatibility load
# ============================================================

class TestE005:

    def test_e005_create_session_includes_profile_sha(self, pled):
        """E005: create_session ledger context includes board_profile_sha256."""
        l, g, lp = pled
        proj = tempfile.mkdtemp()
        try:
            from mcps.zynq_mcp.control.session import create_session_mutator
            sig = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            commit = create_session_mutator(
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                g.instance_id, f"op-{uuid.uuid4().hex}", sig)
            l = commit(g, lp)
            assert l.context["session_id"] != ""
            assert l.context["board_package_revision"] != ""
            bps = l.context.get("board_profile_sha256", "")
            assert is_sha256(bps), f"board_profile_sha256 not valid SHA: {bps!r}"
            expected_bps = "sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18"
            assert bps == expected_bps
            assert bps != l.context["board_package_revision"]
            assert is_sha256(l.context["board_package_revision"]), \
                "board_package_revision must be valid SHA256"
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_e005_sha_validation_rejects_non_sha(self, rtg, monkeypatch):
        """E005: non-SHA256 values for board_package_revision or board_profile_sha256 are rejected."""
        import mcps.zynq_mcp.control.session as session_mod
        rt, g = rtg; lp = rt / "l.json"
        def _i(l): l.instance_id=g.instance_id; l.workspace_id="ws-r3_1a"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
        ledger_transaction(g, lp, _i)
        proj = tempfile.mkdtemp()
        try:
            from mcps.zynq_mcp.control.session import create_session_mutator

            # Save real loader before monkeypatching
            _real_load = session_mod.board_profile_load

            # Case 1: board_package_revision not sha256
            def _bad_rev(board_id, *a, **kw):
                d = dict(_real_load(board_id, *a, **kw))
                d["package_revision"] = "not-a-sha"
                return d
            monkeypatch.setattr(session_mod, "board_profile_load", _bad_rev)
            sig = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            with pytest.raises(ChannelBusyError) as exc1:
                create_session_mutator(
                    {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                    g.instance_id, f"op-{uuid.uuid4().hex}", sig)(g, lp)
            assert "BOARD_PACKAGE_REVISION_INVALID" in str(exc1.value.args[0])
            monkeypatch.undo()

            # Case 2: board_profile_sha256 not sha256
            def _bad_sha(board_id, *a, **kw):
                d = dict(_real_load(board_id, *a, **kw))
                d["sha256"] = "short"
                return d
            monkeypatch.setattr(session_mod, "board_profile_load", _bad_sha)
            sig2 = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            with pytest.raises(ChannelBusyError) as exc2:
                create_session_mutator(
                    {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                    g.instance_id, f"op-{uuid.uuid4().hex}", sig2)(g, lp)
            assert "BOARD_PROFILE_SHA_INVALID" in str(exc2.value.args[0])
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_e005_authoritative_load_count(self, monkeypatch):
        """E005: authoritative load called once from create_session_mutator.
        B02 create_session makes its own independent compatibility load.
        Ledger values must come from the authoritative load's sentinel return.
        """
        import mcps.zynq_mcp.control.session as session_mod
        import mcps.common.board_profile as bp_mod

        authoritative_count = [0]
        compatibility_count = [0]

        # Sentinel values — prove both values come from same authoritative profile object
        SENTINEL_REV = "sha256:" + "aa" * 32
        SENTINEL_SHA = "sha256:" + "bb" * 32

        _orig_load = bp_mod.board_profile_load

        # Spy on mcps.common.board_profile.board_profile_load (B02 compatibility path)
        def _compat_spy(board_id, *args, **kwargs):
            compatibility_count[0] += 1
            # Only count as compatibility; never return sentinel values
            return _orig_load(board_id, *args, **kwargs)

        # Spy on session module's import (authoritative path)
        def _auth_spy(board_id, *args, **kwargs):
            authoritative_count[0] += 1
            # Return a profile with sentinel values — proves they come from THIS load
            real = _orig_load(board_id, *args, **kwargs)
            result = dict(real)
            result["package_revision"] = SENTINEL_REV
            result["sha256"] = SENTINEL_SHA
            return result

        monkeypatch.setattr(bp_mod, "board_profile_load", _compat_spy)
        monkeypatch.setattr(session_mod, "board_profile_load", _auth_spy)

        proj = tempfile.mkdtemp()
        rt = Path(tempfile.mkdtemp())
        try:
            g = InstanceGuard(rt, "ws-e005"); g.determine_role()
            sig = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            lp = rt / "l.json"
            # Init fresh ledger
            def _fresh(l):
                l.instance_id=g.instance_id; l.workspace_id="ws-e005"
                l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id
                return l
            ledger_transaction(g, lp, _fresh)
            from mcps.zynq_mcp.control.session import create_session_mutator
            commit = create_session_mutator(
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                g.instance_id, f"op-{uuid.uuid4().hex}", sig)
            ledger = commit(g, lp)

            # Assert counts
            assert authoritative_count[0] == 1, \
                f"Authoritative load called {authoritative_count[0]} times, expected 1"
            assert compatibility_count[0] == 1, \
                f"B02 compatibility load called {compatibility_count[0]} times, expected 1"

            # Assert Ledger values equal sentinel values from authoritative load
            assert ledger.context["board_package_revision"] == SENTINEL_REV, \
                f"board_package_revision is {ledger.context['board_package_revision']}, expected {SENTINEL_REV}"
            assert ledger.context["board_profile_sha256"] == SENTINEL_SHA, \
                f"board_profile_sha256 is {ledger.context['board_profile_sha256']}, expected {SENTINEL_SHA}"

            # Prove sentinel values differ from real values (sentinels are test artifacts)
            real = _orig_load("ALINX_AX7020_v1.0")
            assert SENTINEL_REV != real["package_revision"], \
                "Sentinel must differ from real package_revision"
            assert SENTINEL_SHA != real["sha256"], \
                "Sentinel must differ from real profile sha256"
        finally:
            g.release_owner_lock()
            shutil.rmtree(proj, ignore_errors=True)
            shutil.rmtree(str(rt), ignore_errors=True)
