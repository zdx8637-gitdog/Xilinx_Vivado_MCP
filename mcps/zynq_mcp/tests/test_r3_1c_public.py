"""
test_r3_1c_public.py — R3.1-C tests.
Public MCP SDK tests: launch real zynq_mcp server via mcp.client.stdio.
Contract tests: CommandRunner with deterministic sync.
"""
import asyncio, json, os, shutil, sys, tempfile, time, uuid
from pathlib import Path
from types import MappingProxyType
import pytest
import hashlib

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT,
    OP_SUCCEEDED, OP_FAILED, OP_OUTCOME_UNKNOWN,
    ChannelBusyError,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.operation_service import request_signature
from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
    _PL_SUCCESS_STAGE,
)

SH_PKG = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
SH_BP = "sha256:3c95da56a6a9264ef42b6902f184d7d01c7229eafa70d1061cfd24cc0af0c90a"
BOARD = "ALINX_AX7020_v1.0"
FIXTURE_REV = "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "b04_pl_ready"
BD_REAL_SRC = str(FIXTURES / "bd_wrapper_realistic.v")
BD_NO_END = str(FIXTURES / "bd_wrapper_malformed_no_end.v")
MANIFEST_SRC = str(FIXTURES / "platform_manifest.json")

REAL_WSID = compute_workspace_id(resolve_workspace_root())


def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(65536), b""): h.update(c)
    return "sha256:" + h.hexdigest()

def _new_sid():
    return f"session-{uuid.uuid4().hex[:8]}"

async def _wait_terminal(guard, ledger_path, op_id, timeout_s=10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        l, _ = ledger_read_shared(guard, ledger_path)
        if l.active_operation is None:
            return l
        if l.active_operation.get("operation_id") != op_id:
            return l
        await asyncio.sleep(0.01)
    return None

async def _sdk_call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return json.loads(r.content[0].text)


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, REAL_WSID); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _setup_project(tmp_path, wrapper_src=BD_REAL_SRC):
    proj = str(tmp_path)
    for d in ["manifests/platform", "hdl", "rtl"]:
        os.makedirs(os.path.join(proj, d), exist_ok=True)
    hdl_target = os.path.join(proj, "hdl", "bd_wrapper_realistic.v")
    shutil.copy(wrapper_src, hdl_target)
    wh = _sha256_file(hdl_target)
    with open(MANIFEST_SRC, "r") as f:
        m = json.load(f)
    m = dict(m); m["bd_wrapper_path"] = "hdl/bd_wrapper_realistic.v"
    m["bd_wrapper_sha256"] = wh
    xsa_path = os.path.join(proj, "platform.xsa")
    Path(xsa_path).write_text("dummy xsa")
    m["xsa_path"] = "platform.xsa"; m["xsa_sha256"] = _sha256_file(xsa_path)
    from mcps.common.artifact_schema import _revision_to_filename
    fn = _revision_to_filename(m["platform_revision"])
    with open(os.path.join(proj, "manifests", "platform", fn), "w") as f:
        json.dump(m, f)
    return proj


def _prep_ledger(rtg, stage="PL_GENERATE", platform_revision=FIXTURE_REV,
                 project_path="", session_id=None):
    rt, g = rtg; lp = rt / "execution_ledger.json"
    sid = session_id or _new_sid()
    def _i(l):
        l.instance_id = g.instance_id; l.workspace_id = REAL_WSID
        l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
        l.worker["state"] = WORKER_STATE_ABSENT; l.worker["pid"] = None
        l.context = {"session_id": sid, "board_id": BOARD,
            "project_path": project_path, "board_package_revision": SH_PKG,
            "expected_board_revision": SH_PKG,
            "board_profile_sha256": SH_BP,
            "current_stage": stage, "platform_revision": platform_revision,
            "pl_revision": None, "ps_revision": None}
        return l
    ledger_transaction(g, lp, _i)
    return g, lp, sid


# ═══════════════════════════════════════════════════════════════════
# Public MCP SDK Tests — real server via stdio_client
# ═══════════════════════════════════════════════════════════════════

class TestPublicMCP:

    def test_r313_full_sdk_success(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "r313sdk")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        g.release_owner_lock()

        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        r1 = await _sdk_call(s, "pl_generate_system_top",
                            {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
                        assert r1["status"] == "success"
                        oid = r1["data"]["operation_id"]
                        assert r1["data"]["status"] == "accepted"
                        r2 = await _sdk_call(s, "wait_operation",
                            {"operation_id": oid, "timeout_s": 30})
                        assert r2["data"]["status"] == "SUCCEEDED"
                        ev = r2["data"].get("completion_evidence") or {}
                        assert ev.get("stage_advanced_from") == "PL_GENERATE"
                        assert ev.get("stage_advanced_to") == "PL_BUILD"
                        r3 = await _sdk_call(s, "get_execution_state", {})
                        assert r3["data"]["execution_lane"] == "IDLE"
                        assert r3["data"]["current_stage"] == "PL_BUILD"
                        assert r3["data"]["worker_state"] == "ABSENT"
                        assert r3["data"]["worker_pid"] is None
                        r4 = await _sdk_call(s, "get_operation_status",
                            {"operation_id": oid})
                        rd = r4["data"].get("result", {}).get("data", {})
                        assert os.path.isfile(rd["output_path"])
                        assert _sha256_file(rd["output_path"]) == rd["system_top_sha256"]
                        assert "output" not in rd
                        assert "ports" not in rd
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)

    def test_r321_sdk_missing_revision(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "r321sdk")
        g, lp, sid = _prep_ledger(rtg, platform_revision="", project_path=proj)
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        r1 = await _sdk_call(s, "pl_generate_system_top",
                            {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
                        oid = r1["data"]["operation_id"]
                        r2 = await _sdk_call(s, "wait_operation",
                            {"operation_id": oid, "timeout_s": 30})
                        assert r2["data"]["status"] == "FAILED"
                        assert r2["data"]["reason_code"] == "PLATFORM_MANIFEST_NOT_FOUND"
                        r3 = await _sdk_call(s, "get_execution_state", {})
                        assert r3["data"]["current_stage"] == "PL_GENERATE"
                        assert r3["data"]["execution_lane"] == "IDLE"
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)

    def test_r3s13_sdk_wrong_stage_rejected(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "r3s13sdk")
        g, lp, sid = _prep_ledger(rtg, stage="PLATFORM_DESIGN", project_path=proj)
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        r1 = await _sdk_call(s, "pl_generate_system_top",
                            {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
                        assert r1["status"] == "error"
                        assert r1["error"]["details"]["reason_code"] == "STAGE_PREREQUISITE_UNMET"
                        assert "operation_id" not in r1.get("data", {})
                        r2 = await _sdk_call(s, "get_execution_state", {})
                        assert r2["data"]["execution_lane"] == "IDLE"
                        assert r2["data"]["active_operation"] is None
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)

    def test_r3s14_sdk_list_tools(self, rtg):
        rtg[1].release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.list_tools()
                        tl = result.tools if hasattr(result, 'tools') else list(result)
                        names = sorted(t.name for t in tl)
                        # R3.1-C + B05 + B06 (33 PS/BSP) + B07 PL bridge (26):
                        # 9 control + 61 domain. + B06 third batch (9) +
                        # B01 UART capture (3) + B01 UART diagnostics (1) +
                        # B01 Phase 4 verify_consistency (1) +
                        # B01 Phase 6 observation (1) → 85 total.
                        # B05-R2 platform atoms add 14 → 99 total.
                        # ps_ensure_arm_accessible adds 1 → 100 total.
                        # B11 phase 2: platform_generate removed → 100 total
                        # (9 control + 91 domain).
                        # B11 ③.1: assign_addresses/make_external/synthesize
                        # added → 103 total (9 control + 94 domain).
                        # B12-N3: ps_start_hw_server → 104 total (9 control + 95 domain).
                        # B12 fix round #2: pl_reset_run → 105 total (9 control + 96 domain).
                        # B13-M1: workflow_rollback/workflow_resume_from → 107 total (11 control + 96 domain).
                        assert len(names) == 107
                        pl_in = [n for n in names if n.startswith("pl_")]
                        # B07 registered 26 PL bridge tools on top of
                        # pl_generate_system_top. Every registered pl_* tool
                        # must have a bridge function (PL_TOOL_MAP) and vice
                        # versa — catches registration drift.
                        from mcps.zynq_mcp.domains.pl.pl_bridge_tools import PL_TOOL_MAP
                        expected_pl = sorted(["pl_generate_system_top", *PL_TOOL_MAP.keys()])
                        assert pl_in == expected_pl
                        # B01-frozen PL API names still not registered (no
                        # bridge/implementation yet): set_top, the combined
                        # place_and_route, open_hw_target, select_device, program.
                        pl_not_registered = {"pl_set_top", "pl_place_and_route",
                            "pl_open_hw_target", "pl_select_device", "pl_program"}
                        for a in pl_not_registered:
                            assert a not in names
                        pt = [t for t in tl if t.name == "pl_generate_system_top"][0]
                        assert set(pt.inputSchema.get("properties",{}).keys()) == {"wrapper_path"}
                        assert pt.inputSchema["required"] == ["wrapper_path"]
                        assert pt.inputSchema.get("additionalProperties") is False
                        for fb in ("next_stage","project_path","platform_revision",
                                   "board_profile_sha256","session_id","executor"):
                            assert fb not in pt.inputSchema.get("properties",{})
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)


# ═══════════════════════════════════════════════════════════════════
# Contract A: Immutable Snapshot
# ═══════════════════════════════════════════════════════════════════

class TestContractASnapshot:

    def test_r3ca1_snapshot_is_immutable(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "ca1")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        captured = {}
        async def _spy(args, snap):
            assert isinstance(snap, MappingProxyType)
            try:
                snap["project_path"] = "/hack"
                pytest.fail("snapshot mutation must raise TypeError")
            except TypeError:
                pass
            captured["snap"] = dict(snap)
            return {"status": "success", "data": {}}
        _spy._contextual = True
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, "/stale/path", executor="local", local_fn=_spy,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert captured["snap"]["project_path"] == proj
        assert captured["snap"]["project_path"] != "/stale/path"

    def test_r3ca2_input_revision_is_platform_revision(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "ca2")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(args, snap):
            return {"status": "success", "data": {}}
        _fn._contextual = True
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_fn, timeout_s=5,
            next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid))
        assert l2 is not None
        assert l2.previous_operation["input_artifact_revision"] == FIXTURE_REV
        assert l2.previous_operation["input_artifact_revision"] != SH_PKG


# ═══════════════════════════════════════════════════════════════════
# Contract B: Same runtime/Ledger — only revision differs => no false dedup
# ═══════════════════════════════════════════════════════════════════

class TestContractBDedup:

    def test_r3cb1_same_ledger_different_revision_no_false_dedup(self, rtg, tmp_path):
        """Same runtime, same ledger, same session_id, same tool_name,
        same wrapper_path, same board_package_revision.
        Only platform_revision differs => two distinct successful operations
        from real CommandRunner admission — no false dedup."""
        proj = _setup_project(tmp_path / "cb1")
        shared_sid = _new_sid()
        g, lp, sid = _prep_ledger(rtg, project_path=proj, session_id=shared_sid)

        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(args, snap):
            return {"status": "success", "data": {}}
        _fn._contextual = True

        # First admission: stage=PL_GENERATE, revision=FIXTURE_REV
        r1 = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            shared_sid, BOARD, proj, executor="local", local_fn=_fn, timeout_s=5,
            next_stage="PL_BUILD"))
        assert r1["status"] == "success"
        oid1 = r1["data"]["operation_id"]
        assert r1["data"].get("deduplicated") is not True

        lt1 = asyncio.run(_wait_terminal(g, lp, oid1, timeout_s=10.0))
        assert lt1 is not None
        assert lt1.previous_operation["status"] == OP_SUCCEEDED

        # Stage advanced to PL_BUILD. Reset to PL_GENERATE + change revision.
        rev2 = "sha256:" + "ab" * 32
        def _reset(l):
            l.context["current_stage"] = "PL_GENERATE"
            l.context["platform_revision"] = rev2
            return l
        ledger_transaction(g, lp, _reset)

        # Second admission on same ledger with different revision
        r2 = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            shared_sid, BOARD, proj, executor="local", local_fn=_fn, timeout_s=5,
            next_stage="PL_BUILD"))
        assert r2["status"] == "success"
        oid2 = r2["data"]["operation_id"]
        assert r2["data"].get("deduplicated") is not True, \
            "Second admission must NOT be deduplicated — different revision"

        lt2 = asyncio.run(_wait_terminal(g, lp, oid2, timeout_s=10.0))
        assert lt2 is not None
        assert lt2.previous_operation["status"] == OP_SUCCEEDED

        assert oid1 != oid2
        assert lt1.previous_operation["input_artifact_revision"] == FIXTURE_REV
        assert lt2.previous_operation["input_artifact_revision"] == rev2

        # Compare actual persisted request_signature from both terminal operations
        persisted_sig1 = lt1.previous_operation["request_signature"]
        persisted_sig2 = lt2.previous_operation["request_signature"]
        assert persisted_sig1 != persisted_sig2, \
            f"Persisted signatures must differ: {persisted_sig1}"


# ═══════════════════════════════════════════════════════════════════
# Contract C: Precise reason_code from structured field
# ═══════════════════════════════════════════════════════════════════

class TestContractCReasonCode:

    def test_r3cc1_missing_revision_empty(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cc1")
        g, lp, sid = _prep_ledger(rtg, platform_revision="", project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "PLATFORM_MANIFEST_NOT_FOUND"
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    def test_r3cc2_invalid_revision(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cc2")
        g, lp, sid = _prep_ledger(rtg, platform_revision="not-a-sha256-rev", project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "INVALID_PLATFORM_REVISION"
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    def test_r3cc3_none_revision(self, rtg, tmp_path):
        """platform_revision=None => PLATFORM_MANIFEST_NOT_FOUND, not INVALID."""
        proj = _setup_project(tmp_path / "cc3")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        def _none(l):
            l.context["platform_revision"] = None; return l
        ledger_transaction(g, lp, _none)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "PLATFORM_MANIFEST_NOT_FOUND", \
            f"None must be PLATFORM_MANIFEST_NOT_FOUND, got {l2.previous_operation['reason_code']}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    def test_r3cc4_key_absent_revision(self, rtg, tmp_path):
        """platform_revision key absent => PLATFORM_MANIFEST_NOT_FOUND."""
        proj = _setup_project(tmp_path / "cc4")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        def _del(l):
            l.context.pop("platform_revision", None); return l
        ledger_transaction(g, lp, _del)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "PLATFORM_MANIFEST_NOT_FOUND", \
            f"Key absent must be PLATFORM_MANIFEST_NOT_FOUND, got {l2.previous_operation['reason_code']}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    def test_r3cc5_int_revision(self, rtg, tmp_path):
        """platform_revision=12345 (int, not str) => INVALID_PLATFORM_REVISION, not missing."""
        proj = _setup_project(tmp_path / "cc5")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        def _set(l):
            l.context["platform_revision"] = 12345; return l
        ledger_transaction(g, lp, _set)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "INVALID_PLATFORM_REVISION", \
            f"int 12345 must be INVALID_PLATFORM_REVISION, got {l2.previous_operation['reason_code']}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.context["current_stage"] == "PL_GENERATE"


# ═══════════════════════════════════════════════════════════════════
# Contract D: Real exception injection through _pl_generate_local_fn
# ═══════════════════════════════════════════════════════════════════

class TestContractDErrorMapping:

    def test_r3cd1_manifest_binding_error_real(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cd1")
        g, lp, sid = _prep_ledger(rtg, platform_revision="", project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.previous_operation["reason_code"] == "PLATFORM_MANIFEST_NOT_FOUND"

    def test_r3cd2_wrapper_parse_error_real(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cd2", wrapper_src=BD_NO_END)
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.previous_operation["reason_code"] == "UNCLOSED_MODULE"

    def test_r3cd3_path_safety_error_real(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cd3")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "../etc/passwd"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.previous_operation["reason_code"] == "PATH_ESCAPE"

    def test_r3cd4_atomic_write_error_real(self, rtg, tmp_path, monkeypatch):
        proj = _setup_project(tmp_path / "cd4")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        import mcps.zynq_mcp.domains.pl.system_top as st_mod
        def _fail(path, content):
            raise st_mod.AtomicWriteError("fake atomic failure")
        monkeypatch.setattr(st_mod, "_atomic_write_text", _fail)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.previous_operation["reason_code"] == "ATOMIC_WRITE_FAILED"

    def test_r3cd5_unknown_exception(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "cd5")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _crash(args, snap):
            raise RuntimeError("unexpected crash")
        _crash._contextual = True
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
            sid, BOARD, proj, executor="local", local_fn=_crash, timeout_s=5,
            next_stage="PL_BUILD"))
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_OUTCOME_UNKNOWN, \
            f"Expected OUTCOME_UNKNOWN, got {l2.previous_operation['status']}"
        assert l2.previous_operation["reason_code"] == "OP_OUTCOME_UNKNOWN", \
            f"Expected OP_OUTCOME_UNKNOWN, got {l2.previous_operation.get('reason_code')}"
        assert l2.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED


# ═══════════════════════════════════════════════════════════════════
# R3S01: Caller argument validation — non-string wrapper_path
# ═══════════════════════════════════════════════════════════════════

class TestR3S01ArgValidation:

    def test_r3s01_int_wrapper_path(self, rtg, tmp_path):
        """R3S01: wrapper_path=int(123) => FAILED + INVALID_ARGUMENT via real _pl_generate_local_fn."""
        proj = _setup_project(tmp_path / "s01int")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": 123},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "INVALID_ARGUMENT", \
            f"Expected INVALID_ARGUMENT, got {l2.previous_operation.get('reason_code')}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.context["current_stage"] == "PL_GENERATE"
        assert l2.active_operation is None
        assert oreg.task_count() == 0

    def test_r3s01_none_wrapper_path(self, rtg, tmp_path):
        """R3S01: wrapper_path=None => FAILED + INVALID_ARGUMENT via real _pl_generate_local_fn."""
        proj = _setup_project(tmp_path / "s01none")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": None},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "INVALID_ARGUMENT", \
            f"Expected INVALID_ARGUMENT, got {l2.previous_operation.get('reason_code')}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.context["current_stage"] == "PL_GENERATE"
        assert l2.active_operation is None
        assert oreg.task_count() == 0

    def test_r3s01_dict_wrapper_path(self, rtg, tmp_path):
        """R3S01: wrapper_path=dict => FAILED + INVALID_ARGUMENT via real _pl_generate_local_fn."""
        proj = _setup_project(tmp_path / "s01dict")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        from mcps.zynq_mcp.dispatcher import _pl_generate_local_fn
        r = asyncio.run(runner.run_command(
            "pl_generate_system_top", {"wrapper_path": {"x": 1}},
            sid, BOARD, proj, executor="local", local_fn=_pl_generate_local_fn,
            timeout_s=5, next_stage="PL_BUILD"))
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]
        l2 = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
        assert l2 is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.previous_operation["reason_code"] == "INVALID_ARGUMENT", \
            f"Expected INVALID_ARGUMENT, got {l2.previous_operation.get('reason_code')}"
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.context["current_stage"] == "PL_GENERATE"
        assert l2.active_operation is None
        assert oreg.task_count() == 0

    def test_r3s01_sdk_int_rejected_by_schema(self, rtg, tmp_path):
        """MCP SDK: wrapper_path=int => MCP schema rejects, no operation,
        execution state unchanged, no previous_operation."""
        proj = _setup_project(tmp_path / "s01sdki")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.call_tool("pl_generate_system_top",
                            {"wrapper_path": 123})
                        assert result.isError, "int args must be rejected by MCP schema"
                        r2 = await _sdk_call(s, "get_execution_state", {})
                        assert r2["data"]["execution_lane"] == "IDLE"
                        assert r2["data"]["active_operation"] is None
                        assert r2["data"]["current_stage"] == "PL_GENERATE"
                        assert r2["data"]["previous_operation"] is None
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)

    def test_r3s01_sdk_none_rejected_by_schema(self, rtg, tmp_path):
        """MCP SDK: wrapper_path=None => MCP schema rejects, no operation,
        execution state unchanged, no previous_operation."""
        proj = _setup_project(tmp_path / "s01sdkn")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.call_tool("pl_generate_system_top",
                            {"wrapper_path": None})
                        assert result.isError, "None args must be rejected by MCP schema"
                        r2 = await _sdk_call(s, "get_execution_state", {})
                        assert r2["data"]["execution_lane"] == "IDLE"
                        assert r2["data"]["active_operation"] is None
                        assert r2["data"]["current_stage"] == "PL_GENERATE"
                        assert r2["data"]["previous_operation"] is None
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)

    def test_r3s01_sdk_dict_rejected_by_schema(self, rtg, tmp_path):
        """MCP SDK: wrapper_path=dict => MCP schema rejects, no operation,
        execution state unchanged, no previous_operation."""
        proj = _setup_project(tmp_path / "s01sdkd")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rtg[0])
        try:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"],
                env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.call_tool("pl_generate_system_top",
                            {"wrapper_path": {"x": 1}})
                        assert result.isError, "dict args must be rejected by MCP schema"
                        r2 = await _sdk_call(s, "get_execution_state", {})
                        assert r2["data"]["execution_lane"] == "IDLE"
                        assert r2["data"]["active_operation"] is None
                        assert r2["data"]["current_stage"] == "PL_GENERATE"
                        assert r2["data"]["previous_operation"] is None
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)


# ═══════════════════════════════════════════════════════════════════
# R3C08: Single-channel concurrency — barrier-based
# ═══════════════════════════════════════════════════════════════════

class TestR3C08Concurrency:

    @pytest.mark.asyncio
    async def test_r3c08_barrier_channel_busy(self, rtg, tmp_path):
        proj = _setup_project(tmp_path / "c08")
        g, lp, sid = _prep_ledger(rtg, project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        enter = asyncio.Event()
        release = asyncio.Event()
        async def _slow(args, snap):
            enter.set(); await release.wait()
            return {"status": "success", "data": {}}
        _slow._contextual = True
        results = []
        async def _racer():
            rr = await runner.run_command(
                "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"},
                sid, BOARD, proj, executor="local", local_fn=_slow,
                timeout_s=10, next_stage="PL_BUILD")
            results.append(rr)
        t1 = asyncio.ensure_future(_racer())
        await asyncio.wait_for(enter.wait(), timeout=5.0)
        r2 = await runner.run_command(
            "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_other.v"},
            sid, BOARD, proj, executor="local", local_fn=_slow,
            timeout_s=5, next_stage="PL_BUILD")
        assert r2["status"] == "error"
        assert r2["error"]["details"]["reason_code"] == "CHANNEL_BUSY"
        release.set()
        await asyncio.wait_for(t1, timeout=10.0)
        assert len(results) == 1
        assert results[0]["status"] == "success"
        oid = results[0]["data"]["operation_id"]
        l2 = await _wait_terminal(g, lp, oid, timeout_s=10.0)
        assert l2 is not None
        assert l2.active_operation is None
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert oreg.task_count() == 0


# ═══════════════════════════════════════════════════════════════════
# Success-stage single source
# ═══════════════════════════════════════════════════════════════════

class TestR3S14Static:

    def test_r3s14_success_stage_immutable_single_source(self):
        assert isinstance(_PL_SUCCESS_STAGE, MappingProxyType)
        assert _PL_SUCCESS_STAGE["pl_generate_system_top"] == "PL_BUILD"
        # R3.1-C + B05 (2) + B07 PL bridge stage chain (4) = 6.
        # B07 mapping fixes the P0: pl_synthesize/pl_route/pl_analyze_timing/
        # pl_generate_bitstream now advance the frozen B01 §5 serial chain
        # (docs/development/mcp/B04_single_channel_audit.md §4.3), so PL_TIMING
        # and PL_BITSTREAM are reachable end-to-end.
        assert len(_PL_SUCCESS_STAGE) == 6
        assert _PL_SUCCESS_STAGE["pl_synthesize"] == "PL_IMPLEMENT"
        assert _PL_SUCCESS_STAGE["pl_route"] == "PL_TIMING"
        assert _PL_SUCCESS_STAGE["pl_analyze_timing"] == "PL_BITSTREAM"
        assert _PL_SUCCESS_STAGE["pl_generate_bitstream"] == "PS_BUILD"
        # pl_place is the placement half of implementation — no stage advance.
        assert "pl_place" not in _PL_SUCCESS_STAGE

    def test_r3s15_pl_bridge_stage_chain_legal(self):
        """B07: every PL bridge success-stage advance must be a legal strict
        serial transition (is_valid_forward) AND the execution gate must admit
        each tool at its source stage with the required evidence. This is the
        static proof that PL_TIMING / PL_BITSTREAM are reachable end-to-end
        (the P0 this fix closes)."""
        from mcps.zynq_mcp.control.context import is_valid_forward
        from mcps.zynq_mcp.control.execution_gate import _check_stage
        chain = [
            ("pl_generate_system_top", "PL_GENERATE", "PL_BUILD"),
            ("pl_synthesize", "PL_BUILD", "PL_IMPLEMENT"),
            ("pl_route", "PL_IMPLEMENT", "PL_TIMING"),
            ("pl_analyze_timing", "PL_TIMING", "PL_BITSTREAM"),
            ("pl_generate_bitstream", "PL_BITSTREAM", "PS_BUILD"),
        ]
        for tool, src, nxt in chain:
            assert _PL_SUCCESS_STAGE[tool] == nxt, tool
            assert is_valid_forward(src, nxt), f"{src} -> {nxt} is not a legal serial advance"
        # Gate admits each step at its source stage (with the required evidence)
        assert _check_stage("pl_synthesize", "PL_BUILD", None) is False
        assert _check_stage("pl_place", "PL_IMPLEMENT", None) is False
        assert _check_stage("pl_route", "PL_IMPLEMENT", None) is False
        assert _check_stage("pl_analyze_timing", "PL_TIMING",
                            {"tool_name": "pl_route", "status": "SUCCEEDED"}) is False
        assert _check_stage("pl_generate_bitstream", "PL_BITSTREAM",
                            {"tool_name": "pl_analyze_timing", "status": "SUCCEEDED",
                             "completion_evidence": {"timing_met": True}}) is False
        # Invalid jumps / missing evidence are rejected (fail-closed)
        assert _check_stage("pl_route", "PL_BUILD", None) is True
        assert _check_stage("pl_analyze_timing", "PL_TIMING", None) is True  # no place/route evidence
        assert _check_stage("pl_analyze_timing", "PL_IMPLEMENT",
                            {"tool_name": "pl_route", "status": "SUCCEEDED"}) is True  # wrong stage
        assert _check_stage("pl_analyze_timing", "PL_BITSTREAM",
                            {"tool_name": "pl_route", "status": "SUCCEEDED"}) is True  # re-run at PL_BITSTREAM would break the strict advance
        assert _check_stage("pl_generate_bitstream", "PL_BITSTREAM",
                            {"tool_name": "pl_analyze_timing", "status": "SUCCEEDED",
                             "completion_evidence": {"timing_met": False}}) is True

    def test_r3s16_pl_bridge_chain_end_to_end(self, rtg, tmp_path):
        """B07: full PL bridge stage chain through the real CommandRunner.
        Each step is a local executor returning success; the stage advances
        exactly per _PL_SUCCESS_STAGE, and pl_analyze_timing's timing_met
        evidence unblocks pl_generate_bitstream. Proves the P0 fix: PL_TIMING
        and PL_BITSTREAM are reachable (and the chain proceeds to PS_BUILD)."""
        proj = _setup_project(tmp_path / "r3s16")
        g, lp, sid = _prep_ledger(rtg, stage="PL_BUILD", project_path=proj)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)

        def _ok(data=None):
            async def _fn(args):
                return {"status": "success", "data": data or {}}
            return _fn

        def _advance(tool, fn, expect_stage, args=None):
            r = asyncio.run(runner.run_command(
                tool, args or {}, sid, BOARD, proj, executor="local", local_fn=fn,
                timeout_s=5, next_stage=_PL_SUCCESS_STAGE.get(tool)))
            assert r["status"] == "success", tool
            oid = r["data"]["operation_id"]
            l = asyncio.run(_wait_terminal(g, lp, oid, timeout_s=10.0))
            assert l is not None, f"{tool} did not reach terminal"
            assert l.previous_operation["status"] == OP_SUCCEEDED, tool
            assert l.context["current_stage"] == expect_stage, \
                f"{tool}: expected {expect_stage}, got {l.context['current_stage']}"
            assert l.execution_lane == EXECUTION_LANE_IDLE
            return l

        # PL_BUILD -> PL_IMPLEMENT
        l = _advance("pl_synthesize", _ok(), "PL_IMPLEMENT")
        assert l.previous_operation["completion_evidence"]["stage_advanced_to"] == "PL_IMPLEMENT"
        # pl_place stays in PL_IMPLEMENT (placement half of implementation)
        l = _advance("pl_place", _ok(), "PL_IMPLEMENT")
        # PL_IMPLEMENT -> PL_TIMING
        l = _advance("pl_route", _ok(), "PL_TIMING")
        # pl_analyze_timing: timing_met evidence surfaces, PL_TIMING -> PL_BITSTREAM
        l = _advance("pl_analyze_timing",
                     _ok({"wns_ns": 0.05, "tns_ns": 0.0, "timing_met": True}),
                     "PL_BITSTREAM")
        ev = l.previous_operation["completion_evidence"]
        assert ev.get("timing_met") is True
        assert ev["stage_advanced_from"] == "PL_TIMING"
        assert ev["stage_advanced_to"] == "PL_BITSTREAM"
        # PL_BITSTREAM -> PS_BUILD requires real artifact evidence and a
        # locked PL Build Manifest.  A generic success response is no longer
        # sufficient after O3 terminal-integrity hardening.
        constraints = Path(proj) / "constraints"
        constraints.mkdir(parents=True, exist_ok=True)
        (constraints / "board.xdc").write_text(
            "set_property PACKAGE_PIN Y9 [get_ports clk]\n",
            encoding="utf-8")
        bitstream = Path(proj) / "build" / "system_top.bit"
        bitstream.parent.mkdir(parents=True, exist_ok=True)
        bitstream.write_bytes(b"R3S16 deterministic bitstream evidence\n")
        l = _advance(
            "pl_generate_bitstream",
            _ok({"bitstream_path": str(bitstream)}),
            "PS_BUILD",
            args={"path": str(bitstream)},
        )
        assert l.previous_operation["artifact_state"] == "PUBLISHED"
        ev = l.previous_operation["completion_evidence"]
        assert Path(ev["manifest_path"]).is_file()
        assert ev["manifest_revision"].startswith("sha256:")
