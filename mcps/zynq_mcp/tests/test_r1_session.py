"""test_r1_session.py — Session lifecycle tests."""
import shutil, tempfile, uuid
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_RUNNING, ChannelBusyError,
)
from mcps.zynq_mcp.control.session import (
    create_session_mutator, close_session_mutator, handle_get_session_info,
)
from mcps.zynq_mcp.control.operation_service import request_signature, TerminalDuplicateError
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.common.revision import is_sha256


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g; g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

@pytest.fixture
def pled(rtg):
    rt, g = rtg; lp = rt / "ledger.json"
    def _i(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
    return ledger_transaction(g, lp, _i), g, lp


class TestSession:
    def test_create_returns_real_session_id(self, pled):
        l, g, lp = pled; proj = tempfile.mkdtemp()
        try:
            sig = request_signature("","IDLE","create_session",{"board_id":"ALINX_AX7020_v1.0","project_path":proj},"")
            commit = create_session_mutator({"board_id":"ALINX_AX7020_v1.0","project_path":proj}, g.instance_id, f"op-{uuid.uuid4().hex}", sig)
            l = commit(g, lp)
            assert l.context["session_id"] != ""
            assert l.context["board_package_revision"] != ""
            # E005: board_profile_sha256 present, valid SHA, distinct from board_package_revision
            bpr = l.context["board_package_revision"]
            bps = l.context.get("board_profile_sha256", "")
            assert is_sha256(bpr), f"board_package_revision not valid SHA256: {bpr!r}"
            assert is_sha256(bps), f"board_profile_sha256 not valid SHA256: {bps!r}"
            expected_bps = "sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18"
            assert bps == expected_bps
            assert bps != bpr, \
                "board_profile_sha256 must not equal board_package_revision"
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_create_session_with_extra_file_succeeds(self, pled, monkeypatch):
        """B12-B03 direct regression: an extra file in the board package
        directory must NOT block create_session; the session must SUCCEED and
        record the correct board_profile_sha256 (evidence)."""
        import shutil as _shutil
        import mcps.zynq_mcp.control.session as session_mod
        import mcps.common.board_profile as bp_mod
        from mcps.common.revision import sha256_file

        l, g, lp = pled
        proj = tempfile.mkdtemp()
        try:
            # Copy the real locked board package to a temp dir, then add an
            # extra file — the exact accident from commit 12cec8f (ADC assets
            # not listed in the frozen manifest).
            src = Path(bp_mod.__file__).resolve().parents[2] / "boards" / "ALINX_AX7020_v1.0"
            pkg = Path(tempfile.mkdtemp()) / "ALINX_AX7020_v1.0"
            _shutil.copytree(str(src), str(pkg))
            (pkg / "adc_regression_extra.txt").write_text(
                "extra file placed by B12-B03 regression test", encoding="utf-8")

            # Point the authoritative loader (session.py) at the temp copy.
            real_bpl = bp_mod.board_profile_load
            monkeypatch.setattr(
                session_mod, "board_profile_load",
                lambda bid, *a, **kw: real_bpl(bid, search_dirs=[str(pkg)], *a, **kw))

            sig = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            commit = create_session_mutator(
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                g.instance_id, f"op-{uuid.uuid4().hex}", sig)
            l = commit(g, lp)

            assert l.context["session_id"] != ""
            bps = l.context.get("board_profile_sha256", "")
            assert is_sha256(bps), f"board_profile_sha256 not valid SHA: {bps!r}"
            # Correct sha = sha256 of the (unmodified) profile file in the copy.
            expected = sha256_file(str(pkg / "board_profile_ALINX_AX7020_v1.0.json"))
            assert bps == expected
            assert bps == "sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18"
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_new_session_clears_previous_dedup_registry(self, pled):
        """P1-B regression: a new session must NOT inherit the previous
        session's dedup_registry.

        Before the fix, an entry recorded in the previous session persisted
        across create_session (close_session only cleared context). The P10
        dedup gate matches a request signature against previous_operation
        (which also persists), so a same-tool call in the new session was
        wrongly rejected with CONFIRM_RETRY_REQUIRED — requiring a manual
        .zynq_runtime/ wipe as a workaround.
        """
        l, g, lp = pled
        proj = tempfile.mkdtemp()
        try:
            # Simulate the previous session: an old tool signature pointing at
            # a terminal previous_operation — the exact pre-fix collision.
            SH = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
            old_tool_sig = request_signature(
                "session-old", "PL_BUILD", "pl_synthesize", {"top": "a"}, SH)
            def _seed(lx):
                lx.dedup_registry[old_tool_sig] = "op-old"
                lx.previous_operation = {"operation_id": "op-old",
                    "tool_name": "pl_synthesize", "status": "SUCCEEDED"}
                return lx
            ledger_transaction(g, lp, _seed)

            # A new session is created over the same project.
            op_id = f"op-{uuid.uuid4().hex}"
            sig = request_signature("", "IDLE", "create_session",
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj}, "")
            commit = create_session_mutator(
                {"board_id": "ALINX_AX7020_v1.0", "project_path": proj},
                g.instance_id, op_id, sig)
            l = commit(g, lp)

            dr = l.dedup_registry or {}
            assert old_tool_sig not in dr, \
                "new session must not inherit the previous session's dedup entry"
            assert dr.get(sig) == op_id, \
                "create_session must seed its own dedup entry"

            # End-to-end: the old tool signature must no longer trip the P10
            # dedup gate (pre-fix it raised TerminalDuplicateError →
            # CONFIRM_RETRY_REQUIRED). The gate may still reject on a
            # non-dedup preflight (stage P7), but never on the stale entry.
            mut = preflight_mutator(
                "pl_synthesize", {"top": "a"}, "session-old",
                "ALINX_AX7020_v1.0", "p", f"op-{uuid.uuid4().hex}", old_tool_sig)
            try:
                ledger_transaction(g, lp, mut)
            except TerminalDuplicateError:
                pytest.fail("new session inherited the previous session's "
                            "dedup entry (false CONFIRM_RETRY_REQUIRED)")
            except ChannelBusyError:
                pass  # non-dedup preflight gate — expected and acceptable
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_create_busy_blocks(self, pled):
        l, g, lp = pled
        def _set(l): l.execution_lane = EXECUTION_LANE_BUSY; return l
        l = ledger_transaction(g, lp, _set)
        proj = tempfile.mkdtemp()
        try:
            with pytest.raises(ChannelBusyError):
                create_session_mutator({"board_id":"ALINX_AX7020_v1.0","project_path":proj}, g.instance_id, "op-x", "sig")(g, lp)
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_close_wrong_session_id_blocks(self, pled):
        l, g, lp = pled
        def _set(l): l.context["session_id"] = "real-sid"; return l
        l = ledger_transaction(g, lp, _set)
        with pytest.raises(ChannelBusyError):
            close_session_mutator({"session_id": "wrong-sid"})(g, lp)

    def test_close_active_op_blocks(self, pled):
        l, g, lp = pled
        def _set(l): l.context["session_id"]="sid"; l.execution_lane=EXECUTION_LANE_BUSY; l.active_operation={"operation_id":"op-1","status":OP_RUNNING}; return l
        l = ledger_transaction(g, lp, _set)
        with pytest.raises(ChannelBusyError):
            close_session_mutator({"session_id": "sid"})(g, lp)

    def test_get_session_info_from_ledger(self, pled):
        l, g, lp = pled
        def _set(l): l.context["session_id"] = "ledger-sid"; l.context["current_stage"] = "IDLE"; return l
        l = ledger_transaction(g, lp, _set)
        result = handle_get_session_info({"session_id": "ledger-sid"}, g, lp, g.workspace_id)
        assert result["status"] == "success"
        assert result["data"]["session_id"] == "ledger-sid"


class TestExistingProjectHint:
    """B12 fix round #3 (item #3): create_session must give a clear advisory
    when the project already holds prior artifacts, instead of silently
    restarting at PLATFORM_DESIGN (the white-box R5 "stuck after close+recreate"
    case). The hint is purely informative — it changes no gate or stage."""

    def test_detects_platform_manifest_xsa_bitstream(self, tmp_path):
        from mcps.zynq_mcp.dispatcher import _existing_project_artifacts_hint
        (tmp_path / "manifests" / "platform").mkdir(parents=True)
        (tmp_path / "manifests" / "platform" / "sha256_abc.json").write_text("{}")
        (tmp_path / "platform.xsa").write_bytes(b"xsa")
        (tmp_path / "bitstream").mkdir()
        (tmp_path / "bitstream" / "system_top.bit").write_bytes(b"bit")
        hint = _existing_project_artifacts_hint(str(tmp_path))
        assert hint.get("platform_manifest") is True
        assert hint.get("platform_xsa") is True
        assert hint.get("bitstream") is True

    def test_empty_project_yields_no_hint(self, tmp_path):
        from mcps.zynq_mcp.dispatcher import _existing_project_artifacts_hint
        assert _existing_project_artifacts_hint(str(tmp_path)) == {}

    def test_nonexistent_project_yields_no_hint(self, tmp_path):
        from mcps.zynq_mcp.dispatcher import _existing_project_artifacts_hint
        assert _existing_project_artifacts_hint(
            str(tmp_path / "does-not-exist")) == {}
