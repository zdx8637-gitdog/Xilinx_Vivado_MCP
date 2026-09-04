"""test_workflow_loop.py — B13-M1: workflow_rollback / workflow_resume_from.

Legal-iteration regression tests: the stage machine must support the real
build→test→fix→rebuild loop as a first-class auditable operation (no ledger
surgery, no close+create re-walk).
"""
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    OP_RUNNING, ChannelBusyError,
)
from mcps.zynq_mcp.control.session import create_session_mutator
from mcps.zynq_mcp.control.operation_service import request_signature
from mcps.zynq_mcp.control.workflow import (
    workflow_rollback_mutator, workflow_resume_mutator,
)
from mcps.zynq_mcp.control.context import is_valid_rollback


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-loop")
    g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


@pytest.fixture
def session(rtg):
    rt, g = rtg
    lp = rt / "ledger.json"

    def _i(l):
        l.instance_id = g.instance_id
        l.workspace_id = "ws-loop"
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        return l

    ledger_transaction(g, lp, _i)
    proj = tempfile.mkdtemp()
    sig = request_signature("", "IDLE", "create_session",
                            {"board_id": "ALINX_AX7020_v1.0",
                             "project_path": proj}, "")
    commit = create_session_mutator(
        {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
        g.instance_id, f"op-{uuid.uuid4().hex}", sig)
    ledger = commit(g, lp)
    yield ledger, g, lp, proj
    shutil.rmtree(proj, ignore_errors=True)


def _set_stage(g, lp, stage, *, lane=EXECUTION_LANE_IDLE, active_op=None,
               dedup=None):
    def _m(current):
        current.context["current_stage"] = stage
        current.context["platform_revision"] = "sha256:" + "a" * 64
        current.context["pl_revision"] = "sha256:" + "b" * 64
        current.context["ps_revision"] = "sha256:" + "c" * 64
        current.execution_lane = lane
        if active_op is not None:
            current.active_operation = active_op
        if dedup is not None:
            current.dedup_registry = dedup
        return current
    return ledger_transaction(g, lp, _m)


class TestRollback:
    def test_rollback_table_ps_build_to_pl_build(self):
        # B13-M1: the P2-proven gap — PS_BUILD must legally roll back to
        # PL_BUILD after a PL defect is found.
        assert is_valid_rollback("PS_BUILD", "PL_BUILD") is True
        assert is_valid_rollback("PS_BUILD", "PS_BUILD") is True  # retry same

    def test_rollback_table_pl_implement_to_pl_build(self):
        assert is_valid_rollback("PL_IMPLEMENT", "PL_BUILD") is True

    def test_rollback_table_pl_generate_to_platform_design(self):
        # B13-F3 修复轮#7: PL_GENERATE（export_manifest 后）必须能回
        # PLATFORM_DESIGN 改 BD——白盒实证缺口（被迫 close+create ×3）。
        assert is_valid_rollback("PL_GENERATE", "PLATFORM_DESIGN") is True
        assert is_valid_rollback("PL_GENERATE", "PL_BUILD") is False

    def test_rollback_table_platform_design_from_any_build_stage(self):
        # F3 同类缺口一并补齐: 所有 PL_GENERATE 之后的阶段都可回平台级。
        for stage in ("PL_BUILD", "PL_IMPLEMENT", "PL_TIMING",
                      "PL_BITSTREAM", "PS_BUILD", "CONSISTENCY_CHECK"):
            assert is_valid_rollback(stage, "PLATFORM_DESIGN") is True, stage

    def test_rollback_table_forward_target_rejected(self):
        assert is_valid_rollback("PS_BUILD", "OBSERVATION") is False
        # 平台阶段自身无回退目标（只能 retry 同阶段）
        assert is_valid_rollback("PLATFORM_DESIGN", "PL_GENERATE") is False

    def test_rollback_moves_stage_and_invalidates_revisions(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD")
        commit = workflow_rollback_mutator(
            {"session_id": sid, "target_stage": "PL_BUILD",
             "reason": "PL defect found on board"})
        l2 = commit(g, lp)
        assert l2.context["current_stage"] == "PL_BUILD"
        assert l2.context["pl_revision"] is None   # downstream invalidated
        assert l2.context["ps_revision"] is None
        assert l2.context["platform_revision"] == "sha256:" + "a" * 64  # kept
        hist = l2.context.get("workflow_history") or []
        assert hist and hist[-1]["from"] == "PS_BUILD" and hist[-1]["to"] == "PL_BUILD"

    def test_rollback_retry_same_drops_ps_revision(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD")
        l2 = workflow_rollback_mutator(
            {"session_id": sid, "target_stage": "PS_BUILD"})(g, lp)
        assert l2.context["current_stage"] == "PS_BUILD"
        assert l2.context["ps_revision"] is None  # re-run will re-derive it

    def test_rollback_pl_generate_to_platform_design(self, session):
        # B13-F3: 平台级迭代环——从 PL_GENERATE 回 PLATFORM_DESIGN 必须
        # 合法且把平台/PL/PS revision 全部失效（BD 重改后全链重建）。
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PL_GENERATE")
        l2 = workflow_rollback_mutator(
            {"session_id": sid, "target_stage": "PLATFORM_DESIGN",
             "reason": "BD needs a rewire (F5-class fix)"})(g, lp)
        assert l2.context["current_stage"] == "PLATFORM_DESIGN"
        assert l2.context["platform_revision"] is None
        assert l2.context["pl_revision"] is None
        assert l2.context["ps_revision"] is None
        hist = l2.context.get("workflow_history") or []
        assert hist and hist[-1]["from"] == "PL_GENERATE" \
            and hist[-1]["to"] == "PLATFORM_DESIGN"

    def test_rollback_invalid_target_refused(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD")
        with pytest.raises(ChannelBusyError) as ei:
            workflow_rollback_mutator(
                {"session_id": sid, "target_stage": "OBSERVATION"})(g, lp)
        assert "ROLLBACK_TARGET_INVALID" in str(ei.value)

    def test_rollback_busy_lane_refused(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD", lane=EXECUTION_LANE_BUSY)
        with pytest.raises(ChannelBusyError) as ei:
            workflow_rollback_mutator(
                {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        assert "CHANNEL_BUSY" in str(ei.value)

    def test_rollback_active_op_refused(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        ao = {"operation_id": "op-x", "tool_name": "ps_compile",
              "status": OP_RUNNING}
        _set_stage(g, lp, "PS_BUILD", lane=EXECUTION_LANE_BUSY, active_op=ao)
        with pytest.raises(ChannelBusyError) as ei:
            workflow_rollback_mutator(
                {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        # active op implies lane=BUSY: the first fail-closed gate fires.
        assert "CHANNEL_BUSY" in str(ei.value)

    def test_rollback_session_mismatch_refused(self, session):
        l, g, lp, proj = session
        _set_stage(g, lp, "PS_BUILD")
        with pytest.raises(ChannelBusyError) as ei:
            workflow_rollback_mutator(
                {"session_id": "other-session",
                 "target_stage": "PL_BUILD"})(g, lp)
        assert "SESSION_ID_MISMATCH" in str(ei.value)

    def test_rollback_resets_dedup_registry(self, session):
        # P1-B semantics: dedup entries must not survive a stage move, so a
        # re-run of the same build command is not falsely P10-rejected.
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD", dedup={"some-sig": "op-old"})
        l2 = workflow_rollback_mutator(
            {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        assert l2.dedup_registry == {}


class TestResume:
    def _make_artifacts(self, proj):
        mdir = Path(proj) / "manifests" / "platform"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "sha256_deadbeef.json").write_text("{}", encoding="utf-8")
        (Path(proj) / "platform.xsa").write_bytes(b"\x00" * 16)
        hdl = Path(proj) / "hdl"
        hdl.mkdir(exist_ok=True)
        (hdl / "platform_bd_wrapper.v").write_text("module w; endmodule",
                                                   encoding="utf-8")

    def test_resume_requires_artifacts(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        with pytest.raises(ChannelBusyError) as ei:
            workflow_resume_mutator(
                {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        assert "RESUME_ARTIFACTS_MISSING" in str(ei.value)

    def test_resume_forward_with_artifacts(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        self._make_artifacts(proj)
        l2 = workflow_resume_mutator(
            {"session_id": sid, "target_stage": "PL_BUILD",
             "reason": "platform unchanged, PL rebuild"})(g, lp)
        assert l2.context["current_stage"] == "PL_BUILD"
        hist = l2.context.get("workflow_history") or []
        assert hist and hist[-1]["via"] == "resume"

    def test_resume_backward_refused(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        _set_stage(g, lp, "PS_BUILD")
        with pytest.raises(ChannelBusyError) as ei:
            workflow_resume_mutator(
                {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        assert "RESUME_TARGET_NOT_FORWARD" in str(ei.value)

    def test_resume_active_op_refused(self, session):
        l, g, lp, proj = session
        sid = l.context["session_id"]
        self._make_artifacts(proj)
        ao = {"operation_id": "op-x", "tool_name": "ps_compile",
              "status": OP_RUNNING}
        _set_stage(g, lp, "PLATFORM_DESIGN", lane=EXECUTION_LANE_BUSY,
                   active_op=ao)
        with pytest.raises(ChannelBusyError) as ei:
            workflow_resume_mutator(
                {"session_id": sid, "target_stage": "PL_BUILD"})(g, lp)
        assert "CHANNEL_BUSY" in str(ei.value)
