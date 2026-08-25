"""
test_r2_adapter.py — B04 R2 tests. Every function has a tier label.

Tiers: production / component / mock / static
"""
import asyncio, json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import pytest

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from mcps.zynq_mcp.control.workspace import (
    resolve_workspace_root, WorkspaceNotFoundError, WorkspaceAmbiguousError,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_CLOSING,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT, WORKER_STATE_READY,
    WORKER_STATE_POISONED, WORKER_STATE_DEAD, WORKER_STATE_BUSY,
    OP_RUNNING, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT,
)
from mcps.zynq_mcp.control.single_worker import (
    SingleWorkerController, _set_worker_state, _heartbeat_tick,
)
from mcps.zynq_mcp.control.process_guard import is_pid_alive
from mcps.zynq_mcp.adapters.vivado_adapter import (
    VivadoAdapter, VivadoBridge, BridgeError,
    ADAPTER_ABSENT, ADAPTER_READY, ADAPTER_POISONED,
    _resolve_server_path,
)

WP = resolve_workspace_root()
REAL_SERVER = _resolve_server_path()
needs_real = pytest.mark.skipif(not REAL_SERVER.is_file(), reason="no real Vivado MCP server")
FAKE_MCP = str(Path(__file__).resolve().parent / "helpers" / "fake_mcp.py")


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        if "cancel scope" not in str(e).lower() and "different task" not in str(e).lower():
            raise
    except BaseException as e:
        if "ExceptionGroup" in type(e).__name__ or "BaseExceptionGroup" in type(e).__name__:
            if "cancel scope" in str(e).lower() or "different task" in str(e).lower():
                return
        raise


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-r2")
    g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _fresh_ledger(rtg):
    rt, g = rtg
    lp = rt / "l.json"
    def _i(l):
        l.instance_id = g.instance_id; l.workspace_id = "ws-r2"
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        return l
    return ledger_transaction(g, lp, _i), g, lp


class TestR2Adapter:
    """R201-R228 with tier labels."""

    # ================================================================
    # R201-R206: component tests (keep, stable)
    # ================================================================
    @needs_real
    def test_r201_start_via_controller(self, rtg):
        """[component] ensure_worker() via real MCP."""
        l, g, lp = _fresh_ledger(rtg); sw = SingleWorkerController(l,g,lp)
        async def _r():
            a = await sw.ensure_worker()
            assert a.is_started and a.child_pid>0 and is_pid_alive(a.child_pid)
            assert a.status==ADAPTER_READY
            l2,_ = ledger_read_shared(g,lp)
            assert l2.worker["pid"]==a.child_pid
            assert l2.worker["state"]==WORKER_STATE_READY
            assert l2.worker["instance_id"]==g.instance_id
            await sw.shutdown()
        _run_async(_r())

    @needs_real
    def test_r202_pid_capture(self, rtg):
        """[component] PID captured via SDK hook."""
        l,g,lp = _fresh_ledger(rtg); sw = SingleWorkerController(l,g,lp)
        async def _r():
            a = await sw.ensure_worker(); pid = a.child_pid
            assert pid and is_pid_alive(pid)
            l2,_ = ledger_read_shared(g,lp)
            assert l2.worker["pid"]==pid
            await sw.shutdown()
        _run_async(_r())

    def test_r203_tool_call_fake(self, rtg):
        """[component] fake MCP ping → deterministic success."""
        rt,g = rtg
        async def _r():
            a = VivadoAdapter(); a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid; a._started=True; a.status=ADAPTER_READY; a._generation+=1
            resp = await a.call_tool("ping",{},session_id="session-r203")
            assert resp.status=="success" and resp.data is not None
            assert resp.data.get("pong") is True
            assert resp.context_ref=="session-r203"
            await a.shutdown()
        _run_async(_r())

    @needs_real
    def test_r204_crash_outcome_unknown(self, rtg):
        """[component] kill worker → execute_tool → OUTCOME_UNKNOWN."""
        l,g,lp = _fresh_ledger(rtg)
        SH="sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
        def _ctx(lx):
            lx.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD",
                        "board_package_revision":SH,"expected_board_revision":SH}; return lx
        l=ledger_transaction(g,lp,_ctx); sw=SingleWorkerController(l,g,lp)
        async def _r():
            a=await sw.ensure_worker(); pid=a.child_pid; assert is_pid_alive(pid)
            from mcps.zynq_mcp.control.execution_gate import preflight_mutator
            from mcps.zynq_mcp.control.operation_service import request_signature
            sig=request_signature("cr","PL_BUILD","pl_synthesize",{},SH)
            oid=f"op-crash-{uuid.uuid4().hex[:8]}"
            l2=ledger_transaction(g,lp,preflight_mutator("pl_synthesize",{},"cr","ALINX_AX7020_v1.0","p",oid,sig))
            def _run(lx):
                lx.active_operation["status"]=OP_RUNNING;lx.execution_lane="BUSY";return lx
            l2=ledger_transaction(g,lp,_run)
            from mcps.zynq_mcp.control.process_guard import kill_process_tree_exact
            kill_process_tree_exact(pid); await asyncio.sleep(1.0)
            assert not is_pid_alive(pid)
            result=await sw.execute_tool("get_capabilities",{},"session-crash")
            assert result["status"]=="error"
            assert result["error"]["details"]["reason_code"]=="OPERATION_OUTCOME_UNKNOWN"
            l2,_=ledger_read_shared(g,lp)
            assert l2.worker["state"] in (WORKER_STATE_POISONED,WORKER_STATE_DEAD)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            assert l2.active_operation is None
            assert l2.previous_operation["operation_id"]==oid
            assert l2.previous_operation["status"]==OP_OUTCOME_UNKNOWN
            await sw.shutdown()
        _run_async(_r())

    def test_r205_timeout_hang_forever(self, rtg):
        """[component] hang_forever → precise VIVADO_TIMEOUT, auto_retry=0."""
        rt,g=rtg; l,g2,lp=_fresh_ledger(rtg)
        SH="sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
        def _ctx(lx):
            lx.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD",
                        "board_package_revision":SH,"expected_board_revision":SH};return lx
        l=ledger_transaction(g,lp,_ctx)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            pid=a.child_pid;assert is_pid_alive(pid)
            from mcps.zynq_mcp.control.execution_gate import preflight_mutator
            from mcps.zynq_mcp.control.operation_service import request_signature
            sig=request_signature("to","PL_BUILD","pl_synthesize",{},SH)
            oid=f"op-to-{uuid.uuid4().hex[:8]}"
            l=ledger_transaction(g,lp,preflight_mutator("pl_synthesize",{},"to","ALINX_AX7020_v1.0","p",oid,sig))
            def _run(lx):
                lx.active_operation["status"]=OP_RUNNING;lx.execution_lane="BUSY";return lx
            l=ledger_transaction(g,lp,_run)
            sw=SingleWorkerController(l,g,lp);sw._adapter=a
            result=await sw.execute_tool("hang_forever",{"seconds":30},"session-to",timeout_s=2.0)
            assert result["status"]=="error"
            assert result["error"]["details"]["reason_code"]=="VIVADO_TIMEOUT"
            assert result["error"]["details"].get("auto_retry_count")==0
            assert not is_pid_alive(pid)
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            assert l2.previous_operation["status"]==OP_TIMED_OUT
        _run_async(_r())

    def test_r206_context_ref(self, rtg):
        """[component] adapter.call_tool forwards context_ref."""
        rt,g=rtg
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            resp=await a.call_tool("ping",{},session_id="ctx-r206")
            assert resp.context_ref=="ctx-r206"
            await a.shutdown()
        _run_async(_r())

    # ================================================================
    # R207-R211: static/mock (keep)
    # ================================================================
    def test_r207_server_path(self):
        """[static] _resolve_server_path() finds server.py."""
        sp=_resolve_server_path()
        assert sp.is_file() and "Xilinx_Vivado_MCP" in str(sp) and "server.py" in str(sp)

    def test_r208_workspace_root_from_temp(self, tmp_path):
        """[mock] resolve_workspace_root(start_path=...)."""
        (tmp_path/"mcps").mkdir();(tmp_path/"docs").mkdir()
        (tmp_path/"docs"/"brick_development_plan.md").write_text("# test")
        r=resolve_workspace_root(start_path=str(tmp_path/"docs"/"brick_development_plan.md"))
        assert r is not None and (r/"mcps").is_dir()

    def test_r209_zero_candidates(self, tmp_path):
        """[mock] zero candidates → WorkspaceNotFoundError."""
        (tmp_path/"empty").mkdir()
        with pytest.raises(WorkspaceNotFoundError):
            resolve_workspace_root(start_path=str(tmp_path/"empty"))

    def test_r210_ambiguous(self, tmp_path):
        """[mock] ambiguous → WorkspaceAmbiguousError."""
        for s in["a","b"]:
            (tmp_path/s/"mcps").mkdir(parents=True)
            (tmp_path/s/"docs").mkdir(parents=True)
            (tmp_path/s/"docs"/"brick_development_plan.md").write_text("# test")
        (tmp_path/"mcps").mkdir(exist_ok=True);(tmp_path/"docs").mkdir(exist_ok=True)
        (tmp_path/"docs"/"brick_development_plan.md").write_text("# test")
        with pytest.raises(WorkspaceAmbiguousError):
            resolve_workspace_root(start_path=str(tmp_path/"a"/"subdir"))

    def test_r211_mcp_json_not_read(self):
        """[static] server path without .mcp.json."""
        assert _resolve_server_path().name=="server.py"

    @needs_real
    def test_r212_real_handshake(self, rtg):
        """[component] Real Vivado MCP → 27+ tools → shutdown."""
        l,g,lp=_fresh_ledger(rtg);sw=SingleWorkerController(l,g,lp)
        async def _r():
            a=await sw.ensure_worker();pid=a.child_pid;assert is_pid_alive(pid)
            tools=await a.list_tools();assert len(tools)>=27
            assert {"get_capabilities","create_project","synth_design"}<={t["name"] for t in tools}
            await sw.shutdown();assert not is_pid_alive(pid)
        _run_async(_r())

    # ================================================================
    # R213-R215: production — close_session lease integration (keep)
    # ================================================================
    def test_r213_close_session_double_lease(self, rtg):
        """[production] close_session via Dispatcher, real Project+JTAG leases."""
        rt,g=rtg
        from mcps.common.project_lock import acquire as lac, jtag_acquire, list_leases_for_owner
        proj=str(rt/"test_proj");os.makedirs(proj,exist_ok=True)
        jr=jtag_acquire("localhost:3121","217700000000","sid-213",ttl_s=120);assert jr.status=="acquired"
        pr=lac(proj,"sid-213",scope="vivado_project",ttl_s=120);assert pr.status=="acquired"
        assert len(list_leases_for_owner("sid-213"))==2
        l,g2,lp=_fresh_ledger(rtg)
        def _sid(lx):lx.context["session_id"]="sid-213";lx.execution_lane=EXECUTION_LANE_IDLE;return lx
        l=ledger_transaction(g2,lp,_sid)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            pid=a.child_pid;assert is_pid_alive(pid)
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            from mcps.zynq_mcp.dispatcher import ZynqDispatcher
            from mcps.zynq_mcp.control.operation_registry import OperationRegistry
            d=ZynqDispatcher(l,OperationRegistry(),g2,lp,sw)
            rl=await d.dispatch("close_session",{"session_id":"sid-213"},True)
            data=json.loads(rl[0].text)
            assert data["status"]=="success",f"close failed: {data}"
            c=data["data"]["completed"];assert "context_deleted" in c
            le=[e for e in c if e.startswith("lease_released:")]
            assert len(le)==2
            # Order: Project before JTAG
            proj_ev=[e for e in c if pr.lease.lease_id in e][0]
            jtag_ev=[e for e in c if jr.lease.lease_id in e][0]
            assert c.index(proj_ev) < c.index(jtag_ev), "Project must release before JTAG"
            assert len(list_leases_for_owner("sid-213"))==0
            pr2=lac(proj,"sid-213-b",scope="vivado_project",ttl_s=60);assert pr2.status=="acquired"
            jr2=jtag_acquire("localhost:3121","217700000000","sid-213-b",ttl_s=60);assert jr2.status=="acquired"
            for lx in list_leases_for_owner("sid-213-b"):
                from mcps.common.project_lock import release_lease_safe;release_lease_safe(lx)
            l2,_=ledger_read_shared(g2,lp)
            assert l2.execution_lane==EXECUTION_LANE_IDLE
            assert l2.context.get("session_id") is None or l2.context.get("session_id")==""
            assert not is_pid_alive(pid)
        _run_async(_r())

    def test_r214_project_lease_release_failure(self, rtg):
        """[production] Project lease release fails → error, RECOVERY_REQUIRED."""
        rt,g=rtg
        from mcps.common.project_lock import acquire as lac, jtag_acquire, list_leases_for_owner
        proj=str(rt/"test_proj");os.makedirs(proj,exist_ok=True)
        pr=lac(proj,"sid-214",scope="vivado_project",ttl_s=120);assert pr.status=="acquired"
        jr=jtag_acquire("localhost:3121","217700000001","sid-214",ttl_s=120);assert jr.status=="acquired"
        l,g2,lp=_fresh_ledger(rtg)
        def _sid(lx):lx.context["session_id"]="sid-214";lx.execution_lane=EXECUTION_LANE_IDLE;return lx
        l=ledger_transaction(g2,lp,_sid)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            import mcps.zynq_mcp.dispatcher as dmod
            _orig=dmod.release_lease_safe
            def _evil(lease):
                if hasattr(lease,'scope') and lease.scope=='vivado_project':
                    return (False,"sim_project_fail")
                return _orig(lease)
            dmod.release_lease_safe=_evil
            try:
                from mcps.zynq_mcp.dispatcher import ZynqDispatcher
                from mcps.zynq_mcp.control.operation_registry import OperationRegistry
                d=ZynqDispatcher(l,OperationRegistry(),g2,lp,sw)
                rl=await d.dispatch("close_session",{"session_id":"sid-214"},True)
                data=json.loads(rl[0].text)
                assert data["status"]=="error"
                assert data["error"]["details"]["reason_code"]=="LEASE_RELEASE_FAILED"
                l2,_=ledger_read_shared(g2,lp)
                assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
                assert l2.context.get("session_id")=="sid-214"
                assert len(list_leases_for_owner("sid-214"))>=1
            finally:
                dmod.release_lease_safe=_orig
                for lx in list_leases_for_owner("sid-214"):_orig(lx)
        _run_async(_r())

    def test_r215_jtag_lease_release_failure(self, rtg):
        """[production] JTAG lease release fails → error, RECOVERY_REQUIRED."""
        rt,g=rtg
        from mcps.common.project_lock import acquire as lac, jtag_acquire, list_leases_for_owner
        proj=str(rt/"test_proj");os.makedirs(proj,exist_ok=True)
        pr=lac(proj,"sid-215",scope="vivado_project",ttl_s=120);assert pr.status=="acquired"
        jr=jtag_acquire("localhost:3121","217700000002","sid-215",ttl_s=120);assert jr.status=="acquired"
        l,g2,lp=_fresh_ledger(rtg)
        def _sid(lx):lx.context["session_id"]="sid-215";lx.execution_lane=EXECUTION_LANE_IDLE;return lx
        l=ledger_transaction(g2,lp,_sid)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            import mcps.zynq_mcp.dispatcher as dmod
            _orig=dmod.release_lease_safe
            def _evil(lease):
                if hasattr(lease,'scope') and lease.scope=='jtag':
                    return (False,"sim_jtag_fail")
                return _orig(lease)
            dmod.release_lease_safe=_evil
            try:
                from mcps.zynq_mcp.dispatcher import ZynqDispatcher
                from mcps.zynq_mcp.control.operation_registry import OperationRegistry
                d=ZynqDispatcher(l,OperationRegistry(),g2,lp,sw)
                rl=await d.dispatch("close_session",{"session_id":"sid-215"},True)
                data=json.loads(rl[0].text)
                assert data["status"]=="error"
                assert data["error"]["details"]["reason_code"]=="LEASE_RELEASE_FAILED"
                l2,_=ledger_read_shared(g2,lp)
                assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
                assert l2.context.get("session_id")=="sid-215"
                rem=list_leases_for_owner("sid-215")
                assert len([l for l in rem if l.scope=="jtag"])>=1
                assert len([l for l in rem if l.scope=="vivado_project"])==0
            finally:
                dmod.release_lease_safe=_orig
                for lx in list_leases_for_owner("sid-215"):_orig(lx)
        _run_async(_r())

    # ================================================================
    # R216: production — CLOSING deterministic concurrency
    # ================================================================
    def test_r216_closing_deterministic_concurrency(self, rtg):
        """[production] Blockable Worker.shutdown: close_session enters
        CLOSING → shutdown blocks → create_session + recover_execution
        must BOTH return CHANNEL_CLOSING precisely. Then unblock."""
        rt,g=rtg;l,g2,lp=_fresh_ledger(rtg)
        def _sid(lx):lx.context["session_id"]="sid-216";lx.execution_lane=EXECUTION_LANE_IDLE;return lx
        l=ledger_transaction(g2,lp,_sid)
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        from mcps.zynq_mcp.control.operation_registry import OperationRegistry

        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            pid=a.child_pid;assert is_pid_alive(pid)
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            # Inject blockable shutdown: signal when entered, wait for unblock
            entered=asyncio.Event();unblock=asyncio.Event()
            _orig_shutdown=sw.shutdown
            async def _blocking_shutdown():
                entered.set()
                await unblock.wait()
                return await _orig_shutdown()
            sw.shutdown=_blocking_shutdown

            d=ZynqDispatcher(l,OperationRegistry(),g2,lp,sw)

            results=[]
            async def closer():
                rl=await d.dispatch("close_session",{"session_id":"sid-216"},True)
                results.append(("close",json.loads(rl[0].text)))

            async def caller(name,args):
                await entered.wait()  # ensure CLOSING is written
                await asyncio.sleep(0.1)  # let ledger transaction commit
                rl=await d.dispatch(name,args,True)
                results.append((name,json.loads(rl[0].text)))

            async def _go():
                await asyncio.gather(
                    closer(),
                    caller("create_session",{"board_id":"ALINX_AX7020_v1.0","project_path":str(rt/"p1")}),
                    caller("recover_execution",{}),
                )
            # Run closer + callers, but closer blocks in shutdown → unblock manually
            tasks=[asyncio.ensure_future(closer()),
                   asyncio.ensure_future(caller("create_session",
                       {"board_id":"ALINX_AX7020_v1.0","project_path":str(rt/"p1")})),
                   asyncio.ensure_future(caller("recover_execution",{}))]
            # Wait for entered signal + a bit, then unblock
            await entered.wait();await asyncio.sleep(0.2)
            unblock.set()
            await asyncio.gather(*tasks)

            assert len(results)==3,f"Expected 3 results: {results}"
            # close must succeed
            close_r=[r for r in results if r[0]=="close"]
            assert len(close_r)==1 and close_r[0][1]["status"]=="success",f"close failed: {close_r}"
            # create_session AND recover_execution must BOTH return CHANNEL_CLOSING
            errors=[r for r in results if r[0]!="close"]
            assert len(errors)==2,f"Expected 2 errors: {errors}"
            for name,data in errors:
                assert data["status"]=="error",f"{name} should error: {data}"
                assert data["error"]["code"]=="LOCK_BUSY",f"{name} code: {data}"
                assert data["error"]["details"]["reason_code"]=="CHANNEL_CLOSING", \
                    f"{name} reason={data['error']['details'].get('reason_code')}"
        _run_async(_r())

    # ================================================================
    # R217-R228: heartbeat_once() 11-field proof + complete chains
    # ================================================================

    async def _setup_worker_for_heartbeat(self, rtg):
        """Helper: returns (sw, adapter, pid, g, lp, ident)."""
        rt,g=rtg;l,g2,lp=_fresh_ledger(rtg)
        a=VivadoAdapter();a._server_path=FAKE_MCP
        a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
        await a._bridge.start()
        a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
        pid=a.child_pid;assert is_pid_alive(pid)
        sw=SingleWorkerController(l,g2,lp);sw._adapter=a
        ident=a.worker_identity
        ledger_transaction(g2,lp,lambda lx:_set_worker_state(lx,ident,WORKER_STATE_READY))
        return sw,a,pid,g2,lp,ident

    # --- R217: all 5 fields match → ok=True, READY ---
    def test_r217_heartbeat_all_match(self, rtg):
        """[component] heartbeat_once: all 5 fields match → ok=True, READY."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            result=await sw.heartbeat_once()
            assert result.ok,f"heartbeat should pass: {result}"
            assert result.worker_state==WORKER_STATE_READY
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.worker["state"]==WORKER_STATE_READY
            await sw.shutdown()
        _run_async(_r())

    # --- R218: BUSY → heartbeat preserves BUSY ---
    def test_r218_heartbeat_preserves_busy(self, rtg):
        """[component] heartbeat_once: BUSY → ok=True, BUSY preserved."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident,WORKER_STATE_BUSY))
            l,_=ledger_read_shared(g,lp);assert l.worker["state"]==WORKER_STATE_BUSY
            result=await sw.heartbeat_once()
            assert result.ok,f"heartbeat should pass on BUSY: {result}"
            assert result.worker_state==WORKER_STATE_BUSY
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.worker["state"]==WORKER_STATE_BUSY
            assert l2.worker["last_heartbeat_at"] is not None
            await sw.shutdown()
        _run_async(_r())

    # --- R219-R223: 5 fields individually missing → WORKER_IDENTITY_MISSING ---
    def test_r219_missing_pid(self, rtg):
        """[component] pid missing → WORKER_IDENTITY_MISSING."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["pid"]=None
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            cur,_=ledger_read_shared(g,lp);assert cur.worker["pid"] is None
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_IDENTITY_MISSING"
            assert result.ledger_persisted is True
            assert "pid" in result.detail
            l2,_=ledger_read_shared(g,lp)
            assert l2.worker["state"] in (WORKER_STATE_POISONED,WORKER_STATE_DEAD)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    def test_r220_missing_start_time(self, rtg):
        """[component] process_start_time missing → WORKER_IDENTITY_MISSING."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["process_start_time"]=None
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_IDENTITY_MISSING"
            assert result.ledger_persisted is True
            assert "process_start_time" in result.detail
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            await sw.shutdown()
        _run_async(_r())

    def test_r221_missing_exe(self, rtg):
        """[component] executable_path missing → WORKER_IDENTITY_MISSING."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["executable_path"]=None
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_IDENTITY_MISSING"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            await sw.shutdown()
        _run_async(_r())

    def test_r222_missing_gen(self, rtg):
        """[component] worker_generation missing → WORKER_IDENTITY_MISSING."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["worker_generation"]=None
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_IDENTITY_MISSING"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            await sw.shutdown()
        _run_async(_r())

    def test_r223_missing_instance_id(self, rtg):
        """[component] instance_id missing → WORKER_IDENTITY_MISSING.
        Must write raw ledger dict — _set_worker_state fallback replaces None."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            # Write identity directly with instance_id=None to avoid fallback
            def _write(lx):
                w=dict(lx.worker)
                w.update({"pid":pid,"process_start_time":ident["process_start_time"],
                    "executable_path":ident["executable_path"],
                    "worker_generation":ident["worker_generation"],
                    "instance_id":None,"state":WORKER_STATE_READY})
                lx.worker=w;return lx
            ledger_transaction(g,lp,_write)
            cur,_=ledger_read_shared(g,lp)
            assert cur.worker["instance_id"] is None
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_IDENTITY_MISSING"
            assert result.ledger_persisted is True
            assert "instance_id" in result.detail
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            await sw.shutdown()
        _run_async(_r())

    # --- R224-R228: 5 fields individually mismatched → precise reason_code ---
    def test_r224_pid_mismatch(self, rtg):
        """[component] pid mismatch → WORKER_PID_MISMATCH."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["pid"]=99999
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_PID_MISMATCH"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.worker["state"] in (WORKER_STATE_POISONED,WORKER_STATE_DEAD)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    def test_r225_start_time_mismatch(self, rtg):
        """[component] process_start_time drift >5s → WORKER_START_TIME_MISMATCH."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            from mcps.zynq_mcp.control.process_guard import get_process_identity
            ident_os=get_process_identity(pid);assert ident_os is not None
            ident_bad=dict(ident);ident_bad["process_start_time"]=ident_os.process_start_time-100.0
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_START_TIME_MISMATCH"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    def test_r226_exe_mismatch(self, rtg):
        """[component] executable_path mismatch → WORKER_EXECUTABLE_MISMATCH."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["executable_path"]="/wrong/python"
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_EXECUTABLE_MISMATCH"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    def test_r227_gen_mismatch(self, rtg):
        """[component] worker_generation mismatch → WORKER_GENERATION_MISMATCH."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            assert ident.get("worker_generation")!=99,"adapter gen already 99"
            ident_bad=dict(ident);ident_bad["worker_generation"]=99
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_GENERATION_MISMATCH"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    def test_r228_instance_mismatch(self, rtg):
        """[component] instance_id mismatch → WORKER_INSTANCE_MISMATCH."""
        async def _r():
            sw,adapter,pid,g,lp,ident=await self._setup_worker_for_heartbeat(rtg)
            ident_bad=dict(ident);ident_bad["instance_id"]="wrong-iid"
            ledger_transaction(g,lp,lambda lx:_set_worker_state(lx,ident_bad,WORKER_STATE_READY))
            result=await sw.heartbeat_once()
            assert not result.ok;assert result.reason_code=="WORKER_INSTANCE_MISMATCH"
            assert result.ledger_persisted is True
            l2,_=ledger_read_shared(g,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED
            # No active_operation means no previous_operation from crash
            await sw.shutdown()
        _run_async(_r())

    # ================================================================
    # R229: shutdown → _server_finalizer complete chain
    # ================================================================
    def test_r229_shutdown_finalizer_complete_chain(self, rtg):
        """[component] Evil heartbeat → _server_finalizer calls shutdown
        internally → failure → persist RECOVERY_REQUIRED. Complete chain."""
        rt,g=rtg;l,g2,lp=_fresh_ledger(rtg)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            pid_before=a.child_pid;assert is_pid_alive(pid_before)
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            async def _evil_hb():
                try:
                    while True:await asyncio.sleep(60)
                except asyncio.CancelledError:
                    try:await asyncio.sleep(600)
                    except asyncio.CancelledError:pass
            sw._heartbeat_task=asyncio.ensure_future(_evil_hb())

            # Call _server_finalizer — it calls shutdown() internally
            from mcps.zynq_mcp.server import _server_finalizer
            diag=await _server_finalizer(g2,sw,lp,None)
            assert diag["shutdown_incomplete"] is True, \
                f"shutdown must be incomplete: {diag}"
            assert diag["recovery_persisted"] is True, \
                f"persist should succeed: {diag}"
            assert diag["owner_lock_released"] is True, \
                f"owner lock should release after persist: {diag}"
            l2,_=ledger_read_shared(g2,lp)
            assert l2.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED, \
                f"Expected RECOVERY_REQUIRED, got {l2.execution_lane}"
            assert not sw._heartbeat_task.done(),"task still running"
            # Cleanup
            sw._heartbeat_task.cancel()
            try:await asyncio.wait_for(sw._heartbeat_task,timeout=2.0)
            except:pass
            sw._heartbeat_task=None
            if is_pid_alive(pid_before):
                from mcps.zynq_mcp.control.process_guard import kill_process_tree_exact
                kill_process_tree_exact(pid_before)
            sw._adapter=None
        _run_async(_r())

    # ================================================================
    # R230: persist failure → no owner lock release
    # ================================================================
    def test_r230_persist_failure_no_lock_release(self, rtg):
        """[component] Shutdown fails AND persist fails → owner_lock_released=False.
        No release_owner_lock() calls on persist failure path."""
        rt,g=rtg;l,g2,lp=_fresh_ledger(rtg)
        async def _r():
            a=VivadoAdapter();a._server_path=FAKE_MCP
            a._bridge=VivadoBridge(command=sys.executable,args=[FAKE_MCP],cwd=str(rt))
            await a._bridge.start()
            a._child_pid=a._bridge.child_pid;a._started=True;a.status=ADAPTER_READY;a._generation+=1
            pid_before=a.child_pid;assert is_pid_alive(pid_before)
            sw=SingleWorkerController(l,g2,lp);sw._adapter=a
            # Force shutdown failure: remove guard → ABSENT write skipped → success=False
            sw._guard=None
            # Use nonexistent path → _persist_shutdown_failure returns False
            from mcps.zynq_mcp.server import _server_finalizer
            diag=await _server_finalizer(g2,sw,rt/"nonexistent"/"ledger.json",None)
            assert diag["shutdown_incomplete"] is True, f"shutdown must be incomplete: {diag}"
            assert diag["persist_failed"] is True, \
                f"persist must fail: {diag}"
            assert diag["owner_lock_released"] is False, \
                f"owner lock NOT released: {diag}"
            # PID killed (force-kill)
            assert not is_pid_alive(pid_before)
            g2.release_owner_lock()
        _run_async(_r())

    # ================================================================
    # R231: server exit no worker (production)
    # ================================================================
    def test_r231_server_exit_no_worker(self, tmp_path):
        """[production] Real server._main() subprocess, no Worker."""
        rt=tmp_path/".zynq_runtime"
        old=os.environ.get("ZYNQ_RUNTIME_ROOT");os.environ["ZYNQ_RUNTIME_ROOT"]=str(rt)
        env=os.environ.copy();pp=str(WP.parent) if str(WP.parent) else str(WP)
        existing=env.get("PYTHONPATH","");env["PYTHONPATH"]=pp+(";"+existing if existing else"")
        try:
            params=StdioServerParameters(command=sys.executable,
                args=["-m","mcps.zynq_mcp.server"],env=env)
            async def _r():
                async with stdio_client(params) as(r,w):
                    async with ClientSession(r,w) as s:
                        await s.initialize()
                        caps=json.loads((await s.call_tool("get_capabilities",{})).content[0].text)
                        assert caps["status"]=="success" and caps["data"]["total_tools"]==105  # B11 ③.1: 100 (phase 2, platform_generate removed) + assign_addresses/make_external/synthesize = 103 (9 control + 94 domain); B12-N3 + ps_start_hw_server = 104 (9 control + 95 domain); B12 fix #2 + pl_reset_run = 105 (9 control + 96 domain)
                await asyncio.sleep(1.5)
                g2=InstanceGuard(rt,"ws-r2-check")
                try:g2.determine_role();assert g2.is_primary
                finally:g2.release_owner_lock()
            _run_async(_r())
        finally:
            if old is not None:os.environ["ZYNQ_RUNTIME_ROOT"]=old
            else:os.environ.pop("ZYNQ_RUNTIME_ROOT",None)
            shutil.rmtree(str(rt),ignore_errors=True)

    # ================================================================
    # R232: heartbeat ledger write failure → ledger_persisted=False
    # ================================================================
    def test_r232_heartbeat_crash_persisted_helper(self):
        """[component] _crash_persisted extracts ledger_persisted correctly.
        Tests: True, False, missing key, empty dict — NOT hardcoded."""
        from mcps.zynq_mcp.control.single_worker import _crash_persisted
        # Full crash response with persisted=True
        assert _crash_persisted({"error":{"details":{"ledger_persisted":True}}}) is True
        # Full crash response with persisted=False
        assert _crash_persisted({"error":{"details":{"ledger_persisted":False}}}) is False
        # Missing details key
        assert _crash_persisted({"error":{}}) is False
        # Empty dict
        assert _crash_persisted({}) is False
        # Not a dict
        assert _crash_persisted(None) is False
        assert _crash_persisted("not_dict") is False

    # ================================================================
    # Component baseline
    # ================================================================
    def test_capability_no_worker_start(self, rtg):
        """[component] adapter_status=ABSENT when no worker."""
        l,g,lp=_fresh_ledger(rtg);sw=SingleWorkerController(l,g,lp)
        assert sw.adapter_status==ADAPTER_ABSENT and not sw.has_worker

    @needs_real
    def test_concurrent_ensure_one_worker(self, rtg):
        """[component] concurrent ensure_worker → one PID."""
        l,g,lp=_fresh_ledger(rtg);sw=SingleWorkerController(l,g,lp);results=[]
        async def _r():
            barrier=asyncio.Barrier(2)
            async def racer(idx):
                await barrier.wait()
                try:a=await sw.ensure_worker();results.append((idx,a.child_pid,"ok"))
                except Exception as e:results.append((idx,None,str(e)))
            await asyncio.gather(racer(1),racer(2))
        _run_async(_r())
        assert len(results)==2
        ok=[r for r in results if r[2]=="ok"];assert len(ok)==2
        assert len({r[1] for r in ok if r[1] is not None})==1
        assert sw._factory_call_count==1
        async def _s():await sw.shutdown()
        _run_async(_s())

    def test_server_controller_has_guard_and_path(self, rtg):
        """[component] controller has guard+ledger_path."""
        l,g,lp=_fresh_ledger(rtg);sw=SingleWorkerController(l,g,lp)
        assert sw._guard is not None and sw._ledger_path is not None
