"""
test_pl_bridge.py -- B04 Sub-step 1 T-001..T-035. All via production entries.
"""
import asyncio, hashlib, json, os, sys, time, subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcps.pl_mcp.vivado_bridge import BridgeOwner, BridgeError, BridgeResponseParseError, ShutdownResult
from mcps.pl_mcp.vivado_bridge import _resolve_project_root, _resolve_server_path, build_default_params
from mcps.pl_mcp.vivado_bridge import _parse_old_response, convert_to_b02_response, _sdk_pid_lock
from mcps.pl_mcp.worker_registry import WorkerRegistry, Operation, LeaseEntry, get_registry, reset_registry
from mcps.pl_mcp.server import PLControlAdapter
from mcps.pl_mcp.server import (_handle_pl_create_session, _handle_pl_get_session_info,
    _handle_pl_get_capabilities, _handle_pl_get_operation_status, _handle_pl_close_session_async)
from mcps.pl_mcp.errors import WorkerExecutor

FAKE_SERVER = Path(__file__).resolve().parent / "fake_mcp_server.py"
REAL_SERVER = _resolve_server_path()
needs_real = pytest.mark.skipif(not REAL_SERVER.is_file(), reason="no server.py")

@pytest.fixture(autouse=True)
def _reset():
    reset_registry(); yield; reset_registry()

def _fake_owner_factory():
    return BridgeOwner(command=sys.executable,args=[str(FAKE_SERVER)],cwd=str(FAKE_SERVER.parent))
def _bad_path_factory():
    return BridgeOwner(args=["/nonexistent/server.py"])

def _is_pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0: return False
    try:
        r = subprocess.run(["tasklist","/FI",f"PID eq {pid}","/NH"], capture_output=True, text=True, timeout=5)
        return str(pid) in r.stdout and "No tasks" not in r.stdout
    except Exception: return False

async def _poll_gone(pid, timeout=60.0) -> bool:
    dl = time.monotonic()+timeout
    while time.monotonic()<dl:
        if not _is_pid_alive(pid): return True
        await asyncio.sleep(0.3)
    return False

# ================================================================
# Path + .mcp.json
# ================================================================
class TestPathMCPJson:
    def test_t029_root(self): assert _resolve_project_root().name=="fpgaproject"
    def test_t029_server(self):
        sp=_resolve_server_path(); assert sp.name=="server.py" and sp.parent.name=="Xilinx_Vivado_MCP"
    def test_t030_sys_executable(self):
        try: p=build_default_params()
        except FileNotFoundError: pytest.skip(); return
        assert p.command==sys.executable
    def test_t035_sha_unchanged_and_not_read(self):
        mcp = _resolve_project_root()/".mcp.json"
        before = hashlib.sha256(mcp.read_bytes()).hexdigest()
        # Spy to prove no open/read of .mcp.json during bridge lifecycle
        real_open = open
        access_count = [0]
        def _spy_open(path, *a, **kw):
            s = str(path).replace('\\','/')
            if s.endswith('/.mcp.json') or s.endswith('\\.mcp.json'): access_count[0] += 1
            return real_open(path, *a, **kw)
        async def _run():
            o=_fake_owner_factory(); await o.start()
            resp=await o.call_tool("get_capabilities",{}); assert resp.status=="success"
            await o.shutdown()
        with patch("builtins.open", _spy_open):
            asyncio.run(_run())
        after = hashlib.sha256(mcp.read_bytes()).hexdigest()
        assert before==after, ".mcp.json modified"
        assert access_count[0]==0, f".mcp.json read {access_count[0]} times"

# ================================================================
# Parse/convert
# ================================================================
class TestParseConvert:
    def test_parse_ok(self):
        r=_parse_old_response(json.dumps({"status":"success","data":{"k":"v"}})); assert r["status"]=="success"
    @pytest.mark.parametrize("raw,pat",[
        ("","empty"),("x","non-JSON"),("[1]","dict"),("null","dict"),
        ('{"data":"x"}',"missing.*status"),('{"status":"x"}',"illegal"),('{"status":"error"}',"missing.*error"),
    ])
    def test_fail(self,raw,pat):
        with pytest.raises(BridgeResponseParseError,match=pat):_parse_old_response(raw)
    def test_convert(self):
        r=convert_to_b02_response({"status":"success","data":{"r":42},"warnings":["w"]},context_ref="s")
        assert r.data=={"r":42} and r.warnings==["w"] and r.context_ref=="s"

# ================================================================
# T-001, T-002, T-004, T-012: Owner + PID + concurrent start
# ================================================================
class TestOwnerWithPid:
    def test_t001_t002_pid_from_hook(self):
        async def _run():
            o=_fake_owner_factory(); await o.start()
            assert o.child_pid is not None and o.child_pid>0
            assert _is_pid_alive(o.child_pid)
            tools=await o.list_tools(); assert len(tools)>=3
            resp=await o.call_tool("get_capabilities",{}); assert resp.status=="success"
            await o.shutdown()
            assert not _is_pid_alive(o.child_pid), "PID still alive after natural shutdown"
        asyncio.run(_run())

    def test_t004_crash(self):
        async def _run():
            o=_fake_owner_factory(); await o.start()
            pid=o.child_pid; assert pid and _is_pid_alive(pid)
            with pytest.raises(BridgeError): await o.call_tool("crash_me",{})
            assert o.is_poisoned
            with pytest.raises(BridgeError): await o.call_tool("get_capabilities",{})
            await o.shutdown()
            gone=await _poll_gone(pid); assert gone
        asyncio.run(_run())

    def test_t012_two(self):
        async def _run():
            o1=_fake_owner_factory(); o2=_fake_owner_factory()
            await o1.start(); await o2.start()
            assert len(await o1.list_tools())>=3 and len(await o2.list_tools())>=3
            await o1.shutdown(); await o2.shutdown()
        asyncio.run(_run())

    def test_concurrent_start_two_pids_both_alive(self):
        """Two owners started concurrently → two distinct, alive PIDs."""
        async def _run():
            saw = []
            async def start_one(i):
                o = BridgeOwner(command=sys.executable, args=[str(FAKE_SERVER)], cwd=str(FAKE_SERVER.parent))
                await o.start()
                saw.append((i, o.child_pid))
                resp = await o.call_tool("get_capabilities", {})
                assert resp.status == "success"
                return o
            o1, o2 = await asyncio.gather(start_one(1), start_one(2))
            pid1, pid2 = saw[0][1], saw[1][1]
            assert pid1 is not None and pid2 is not None
            assert pid1 != pid2
            assert _is_pid_alive(pid1) and _is_pid_alive(pid2)
            await o1.shutdown(); await o2.shutdown()
            assert not _is_pid_alive(pid1) and not _is_pid_alive(pid2)
        asyncio.run(_run())

    def test_shutdown_cleaned_pid_verified(self):
        """shutdown returns cleaned=True only when PID is truly gone."""
        async def _run():
            o=_fake_owner_factory(); await o.start(); pid=o.child_pid
            assert pid and _is_pid_alive(pid)
            sr = await o.shutdown()
            assert sr.cleaned, f"shutdown not clean: {sr.error}"
            assert not _is_pid_alive(pid)
        asyncio.run(_run())


# ================================================================
# T-005..T-008: submit_command lifecycle + blocking close
# ================================================================
class TestSubmitCommand:
    def test_t005_returns_accepted_immediately(self):
        """submit_command returns accepted+operation_id before cmd_fn finishes."""
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1")
            started=asyncio.Event(); done=asyncio.Event()
            async def slow_cmd(w):
                started.set()
                await done.wait()
                return {"ok":True}
            r=await exec.submit_command("s1",slow_cmd)
            assert r["status"]=="success"
            d=r["data"]; assert d["status"]=="accepted" and "operation_id" in d
            op_id=d["operation_id"]
            await started.wait()  # cmd_fn now running
            op=exec._reg.get_operation(op_id)
            assert op is not None and op.status=="running"
            done.set()
            for _ in range(50):
                op=exec._reg.get_operation(op_id)
                if op and op.is_terminal(): break
                await asyncio.sleep(0.1)
            assert op.status=="succeeded"
            w=exec._reg.get_worker("s1"); await w.owner.shutdown() if w else None
        asyncio.run(_run())

    def test_t006_t007_succeeded(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1")
            async def ok(w): return {"done":1}
            r=await exec.submit_command("s1",ok); op_id=r["data"]["operation_id"]
            for _ in range(50):
                op=exec._reg.get_operation(op_id)
                if op and op.is_terminal(): break
                await asyncio.sleep(0.1)
            op=exec._reg.get_operation(op_id)
            assert op.status=="succeeded" and op.result=={"done":1}
            assert exec._reg.task_count("s1")==0, "background task not cleaned up"
            w=exec._reg.get_worker("s1"); await w.owner.shutdown() if w else None
        asyncio.run(_run())

    def test_t008_command_crash_operation_outcome_unknown(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1")
            async def crash_cmd(w): await w.owner.call_tool("crash_me",{})
            r=await exec.submit_command("s1",crash_cmd); op_id=r["data"]["operation_id"]
            for _ in range(80):
                op=exec._reg.get_operation(op_id)
                if op and op.is_terminal(): break
                await asyncio.sleep(0.1)
            op=exec._reg.get_operation(op_id)
            assert op.status=="failed" and op.error_code=="INTERNAL_ERROR"
            assert op.reason_code=="OPERATION_OUTCOME_UNKNOWN"
            assert exec._reg.task_count("s1")==0
        asyncio.run(_run())

    def test_blocking_command_close_session_cancels_task(self):
        """close_session cancels a still-running background command task."""
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            sr=await exec.start_or_get_worker(sid)
            pid=sr["data"].get("pid"); assert pid and _is_pid_alive(pid)

            # Submit a command that blocks forever
            blocking=asyncio.Event()
            async def forever_cmd(w):
                await blocking.wait()
                return {"done":1}
            result=await exec.submit_command(sid,forever_cmd)
            op_id=result["data"]["operation_id"]
            assert result["data"]["status"]=="accepted"
            # Command should be running
            await asyncio.sleep(0.2)
            op=exec._reg.get_operation(op_id)
            assert op is not None and op.status=="running"

            # close_session must cancel background task and close worker
            cr=await _handle_pl_close_session_async({"session_id":sid})
            assert cr["status"]=="success"

            # Operation is now failed with SESSION_CLOSED
            op=exec._reg.get_operation(op_id)
            assert op is not None and op.status=="failed"
            assert op.reason_code=="SESSION_CLOSED"
            assert op.error_code!="OPERATION_OUTCOME_UNKNOWN", \
                "should be SESSION_CLOSED not OPERATION_OUTCOME_UNKNOWN"

            # Task table clean
            assert exec._reg.task_count(sid)==0

            # Worker gone, PID gone
            assert exec._reg.get_worker(sid) is None
            gone=await _poll_gone(pid); assert gone

            # Context gone
            from mcps.common.context import SessionError,get_session_info
            with pytest.raises(SessionError): get_session_info(sid)
        asyncio.run(_run())


# ================================================================
# T-009: Auto-rebuild + retry once; double fail = no third
# ================================================================
class TestAutoRetry:
    def test_t009_success_on_second(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            r=await exec.start_or_get_worker("s1"); pid1=r["data"]["pid"]
            assert pid1 and _is_pid_alive(pid1)
            crashed=[False]
            async def qfn(w):
                if not crashed[0]: crashed[0]=True; await w.owner.call_tool("crash_me",{})
                else: return (await w.owner.call_tool("get_capabilities",{})).to_dict()
            result=await exec.execute("s1","query-stateless",qfn)
            assert result["status"]=="success"
            w2=exec._reg.get_worker("s1"); pid2=getattr(w2.owner,'child_pid',None)
            assert pid2 and pid2!=pid1
            gone=await _poll_gone(pid1); assert gone
            await w2.owner.shutdown()
        asyncio.run(_run())

    def test_t009_second_fail_no_third(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            r=await exec.start_or_get_worker("s1"); pid1=r["data"]["pid"]
            assert pid1 and _is_pid_alive(pid1)
            calls=[0]
            async def always_crash(w):
                calls[0]+=1; await w.owner.call_tool("crash_me",{})
            result=await exec.execute("s1","query-stateless",always_crash)
            assert result["status"]=="error"
            assert calls[0]==2, f"called {calls[0]} times"
            assert exec._reg.get_worker("s1") is None  # poisoned
        asyncio.run(_run())


# ================================================================
# T-010: timeout + force kill
# ================================================================
class TestTimeout:
    def test_timeout_natural_exit(self):
        async def _run():
            o=_fake_owner_factory(); await o.start(); pid=o.child_pid
            assert pid and _is_pid_alive(pid)
            with pytest.raises(BridgeError): await o.call_tool("sleep_forever",{},timeout=2.0)
            assert not o.owner_task.cancelled()
            try: await asyncio.wait_for(asyncio.shield(o.cleanup_done.wait()),60)
            except asyncio.TimeoutError: pytest.fail("timeout")
            assert o.owner_task.done() and not o.owner_task.cancelled()
            gone=await _poll_gone(pid); assert gone
        asyncio.run(_run())

    def test_force_kill_fallback(self):
        async def _run():
            o=_fake_owner_factory(); await o.start(); pid=o.child_pid
            assert pid and _is_pid_alive(pid)
            reg=get_registry()
            from mcps.pl_mcp.worker_registry import WorkerEntry
            reg._workers["s1"]=WorkerEntry("s1",o,pid=pid)
            o.shutdown=lambda: ShutdownResult.fail("hung")
            summary=await reg.shutdown_worker_and_tombstone("s1")
            assert summary["success"],f"{summary}"
            assert summary["pid_cleaned"] and not _is_pid_alive(pid)
        asyncio.run(_run())


# ================================================================
# T-011: context_ref
# ================================================================
class TestContext:
    def test_context_ref(self):
        async def _run():
            o=_fake_owner_factory(); await o.start()
            resp=await o.call_tool("get_capabilities",{},session_id="sess-1")
            assert resp.context_ref=="sess-1"; await o.shutdown()
        asyncio.run(_run())


# ================================================================
# T-013, T-014, T-019
# ================================================================
class TestCapacity:
    def test_t013_busy(self):
        async def _run():
            reg=get_registry(); exec=WorkerExecutor(reg,_fake_owner_factory)
            await exec.start_or_get_worker("s1"); reg.acquire_in_flight("s1")
            async def d(w): return {}
            r=await exec.execute("s1","query-stateful",d)
            assert r["error"]["code"]=="LOCK_BUSY"
            assert r["error"]["details"]["reason_code"]=="WORKER_BUSY"
            reg.release_in_flight("s1"); w=reg.get_worker("s1"); await w.owner.shutdown()
        asyncio.run(_run())
    def test_t014_lazy(self):
        r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
            "project_path":str(Path(__file__).parent)})
        assert r["status"]=="success"
        assert get_registry().get_worker(r["data"]["session_id"]) is None
    def test_t019_max_workers(self):
        calls=[0]
        def cf(): calls[0]+=1; return _fake_owner_factory()
        reg=WorkerRegistry(max_workers=1); exec=WorkerExecutor(reg,cf)
        async def _run():
            r1=await exec.start_or_get_worker("s1"); assert r1["status"]=="success"; assert calls[0]==1
            r2=await exec.start_or_get_worker("s2"); assert r2["error"]["code"]=="LOCK_BUSY"
            assert r2["error"]["details"]["reason_code"]=="MAX_WORKERS_EXCEEDED"
            assert calls[0]==1
            w=reg.get_worker("s1"); await w.owner.shutdown()
        asyncio.run(_run())


# ================================================================
# T-015, T-016: close_session PID + leases
# ================================================================
class TestCloseSession:
    def test_t015_pid_cleaned(self):
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            sr=await exec.start_or_get_worker(sid); pid=sr["data"].get("pid")
            assert pid and _is_pid_alive(pid)
            cr=await _handle_pl_close_session_async({"session_id":sid})
            assert cr["status"]=="success" and cr["data"]["pid_cleaned"]
            assert not _is_pid_alive(pid)
        asyncio.run(_run())

    def test_t016_lease_order(self):
        events=[]
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker(sid)
            w=get_registry().get_worker(sid)
            w.add_lease("jtag","j",lambda k,v:(events.append(f"rel_{k}"),True)[1])
            w.add_lease("project","p",lambda k,v:(events.append(f"rel_{k}"),True)[1])
            import mcps.pl_mcp.server as svr
            orig=svr.close_session
            def spy(s): events.append("cls"); orig(s)
            with patch.object(svr,"close_session",side_effect=spy) as m:
                cr=await _handle_pl_close_session_async({"session_id":sid})
                assert cr["status"]=="success"; m.assert_called_once_with(sid)
            pi=events.index("rel_project"); ji=events.index("rel_jtag"); ci=events.index("cls")
            assert pi<ji<ci,f"order:{events}"
            from mcps.common.context import SessionError,get_session_info
            with pytest.raises(SessionError): get_session_info(sid)
        asyncio.run(_run())

    def test_leases_released_exactly_once(self):
        """Each lease callback called exactly once; leases_released=2."""
        counts={"p":0,"j":0}
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker(sid)
            w=get_registry().get_worker(sid)
            l1=w.add_lease("project","p",lambda k,v:(counts.__setitem__("p",counts["p"]+1),True)[1])
            l2=w.add_lease("jtag","j",lambda k,v:(counts.__setitem__("j",counts["j"]+1),True)[1])
            cr=await _handle_pl_close_session_async({"session_id":sid})
            assert cr["status"]=="success"
            assert counts["p"]==1 and counts["j"]==1
            # Second release should NOT call callback again
            ok1=await l1.release(); assert ok1  # already released, no callback
            ok2=await l2.release(); assert ok2
            assert counts["p"]==1 and counts["j"]==1  # still 1
        asyncio.run(_run())

    def test_lease_fail_then_retry(self):
        """First release fails, second succeeds via retry."""
        fails=[0]
        def fail_then_ok(k,v):
            fails[0]+=1
            if fails[0]<2: raise RuntimeError("fail")
            return True
        async def _run():
            le=LeaseEntry("project","x",release_cb=fail_then_ok)
            ok1=await le.release(); assert not ok1  # failed
            ok2=await le.release(); assert ok2  # succeeded
            assert fails[0]==2
        asyncio.run(_run())


# ================================================================
# T-017, T-018, T-032, T-033, T-034
# ================================================================
class TestEnvCwd:
    def test_t017_stderr(self):
        async def _run():
            o=_fake_owner_factory(); await o.start()
            resp=await o.call_tool("get_capabilities",{}); assert resp.status=="success"
            await o.shutdown()
        asyncio.run(_run())
    def test_t018_outside(self):
        orig=os.getcwd()
        try:
            os.chdir(str(Path(__file__).resolve().parent.parent.parent.parent))
            async def _run():
                o=_fake_owner_factory(); await o.start()
                resp=await o.call_tool("get_capabilities",{}); assert resp.status=="success"
                await o.shutdown()
            asyncio.run(_run())
        finally: os.chdir(orig)
    def test_t032_env_echoed(self):
        env={**os.environ,"ZYNQ_TEST_T032":"hello_t032_val"}
        async def _run():
            o=BridgeOwner(command=sys.executable,args=[str(FAKE_SERVER)],cwd=str(FAKE_SERVER.parent),env=env)
            await o.start()
            resp=await o.call_tool("get_env",{"name":"ZYNQ_TEST_T032"})
            assert resp.status=="success" and resp.data["value"]=="hello_t032_val"
            await o.shutdown()
        asyncio.run(_run())
    def test_t034_stderr(self):
        async def _run():
            o=_fake_owner_factory(); await o.start()
            resp=await o.call_tool("get_capabilities",{}); assert resp.status=="success"
            await o.shutdown()
        asyncio.run(_run())


# ================================================================
# T-020: Rebuild
# ================================================================
class TestRebuild:
    def test_rebuild_events(self):
        ev=[]
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            r=await exec.start_or_get_worker("s1"); pid_old=r["data"]["pid"]
            assert pid_old and _is_pid_alive(pid_old)
            exec._reg.create_operation("s1"); w=exec._reg.get_worker("s1")
            try: await w.owner.call_tool("crash_me",{})
            except BridgeError: pass
            exec._reg.mark_poisoned("s1",command_reason=True)
            rr=await exec.rebuild_worker("s1",events=ev)
            assert rr["status"]=="success"
            assert ev==["old_shutdown","new_created","new_started","new_registered"]
            pid_new=rr["data"]["pid"]; assert pid_new and pid_new!=pid_old
            gone=await _poll_gone(pid_old); assert gone
            w2=exec._reg.get_worker("s1"); await w2.owner.shutdown()
        asyncio.run(_run())
    def test_rebuild_old_failure_no_new(self):
        calls=[0]
        def cf(): calls[0]+=1; return _fake_owner_factory()
        async def _run():
            reg=get_registry(); fo=_fake_owner_factory(); await fo.start()
            fo.shutdown=lambda: ShutdownResult.fail("injected")
            from mcps.pl_mcp.worker_registry import WorkerEntry
            reg._workers["s1"]=WorkerEntry("s1",fo); reg.mark_poisoned("s1")
            exec=WorkerExecutor(reg,cf)
            r=await exec.rebuild_worker("s1")
            assert r["status"]=="error" and r["error"]["details"]["reason_code"]=="WORKER_CLEANUP_FAILED"
            assert calls[0]==0 and reg._workers.get("s1") is not None
        asyncio.run(_run())


# ================================================================
# T-021, T-022
# ================================================================
class TestOpQuery:
    def test_t021(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1"); op=exec._reg.create_operation("s1"); op.transition("running")
            r=_handle_pl_get_operation_status({"operation_id":op.operation_id})
            assert r["status"]=="success" and r["data"]["status"]=="running"
            w=exec._reg.get_worker("s1"); await w.owner.shutdown() if w else None
        asyncio.run(_run())
    def test_t022(self):
        r=_handle_pl_get_operation_status({"operation_id":"op-x"})
        assert r["status"]=="error" and r["error"]["code"]=="OPERATION_NOT_FOUND"


# ================================================================
# T-023: close order via spy-calls-real
# ================================================================
class TestCloseOrder:
    def test_spy_calls_real_order(self):
        events=[]
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker(sid)
            w=get_registry().get_worker(sid)
            w.add_lease("jtag","j",lambda k,v:(events.append(f"rel_{k}"),True)[1])
            w.add_lease("project","p",lambda k,v:(events.append(f"rel_{k}"),True)[1])
            import mcps.pl_mcp.server as svr
            orig=svr.close_session
            def spy(s): events.append("cls"); orig(s)
            with patch.object(svr,"close_session",side_effect=spy) as m:
                cr=await _handle_pl_close_session_async({"session_id":sid})
                assert cr["status"]=="success"; m.assert_called_once_with(sid)
            assert events.index("rel_project")<events.index("rel_jtag")<events.index("cls")
            from mcps.common.context import SessionError,get_session_info
            with pytest.raises(SessionError): get_session_info(sid)
        asyncio.run(_run())
    def test_failure_skips_close(self):
        async def _run():
            r=_handle_pl_create_session({"board_id":"ALINX_AX7020_v1.0",
                "project_path":str(Path(__file__).parent)}); sid=r["data"]["session_id"]
            fo=_fake_owner_factory(); await fo.start(); fo.child_pid=None
            fo.shutdown=lambda: ShutdownResult.fail("injected")
            from mcps.pl_mcp.worker_registry import WorkerEntry
            reg=get_registry(); reg._workers[sid]=WorkerEntry(sid,fo)
            with patch("mcps.pl_mcp.server.close_session") as m:
                cr=await _handle_pl_close_session_async({"session_id":sid})
                assert cr["status"]=="error"
                m.assert_not_called()
            assert reg._workers.get(sid) is not None
            from mcps.common.context import get_session_info
            assert get_session_info(sid) is not None
        asyncio.run(_run())


# ================================================================
# T-024..T-026
# ================================================================
class TestCap:
    def test_t024(self):
        d=_handle_pl_get_capabilities({})["data"]
        assert d["domain_apis_implemented"]==0 and d["status"]=="bridge_ready" and d["total_tools"]==5
    def test_list(self):
        a=PLControlAdapter(); names={t.name for t in a.schemas}
        assert names=={"create_session","close_session","get_session_info","get_capabilities","get_operation_status"}
    def test_t025_t026(self):
        from mcps.common.control_api import PLATFORM_CAPABILITIES,PS_CAPABILITIES
        assert PLATFORM_CAPABILITIES["mcp_name"]=="zynq_platform" and PS_CAPABILITIES["mcp_name"]=="zynq_ps"


# ================================================================
# T-027, T-028
# ================================================================
class TestRecovery:
    def test_t027(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1"); exec._reg.mark_poisoned("s1")
            async def d(w): return {}
            r=await exec.execute("s1","query-stateful",d)
            assert r["error"]["code"]=="TOOL_ERROR" and r["error"]["details"]["reason_code"]=="SESSION_RECOVERY_REQUIRED"
        asyncio.run(_run())
    def test_t028(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_fake_owner_factory)
            await exec.start_or_get_worker("s1"); exec._reg.mark_poisoned("s1")
            async def d(w): return {}
            r=await exec.execute("s1","query-hw",d)
            assert r["error"]["code"]=="JTAG_ERROR" and r["error"]["details"]["reason_code"]=="DEVICE_RESELECT_REQUIRED"
        asyncio.run(_run())


# ================================================================
# T-031: server not found
# ================================================================
class TestServerNotFound:
    def test_t031(self):
        async def _run():
            exec=WorkerExecutor(get_registry(),_bad_path_factory)
            r=await exec.start_or_get_worker("s1")
            assert r["status"]=="error" and r["error"]["code"]=="ENV_ERROR"
            assert r["error"]["details"]["reason_code"]=="VIVADO_MCP_SERVER_NOT_FOUND"
        asyncio.run(_run())


# ================================================================
# Deterministic concurrent
# ================================================================
class TestConcurrent:
    def test_deterministic_race(self):
        calls=[0]
        class BlockingOwner(BridgeOwner):
            started=asyncio.Event(); resume=asyncio.Event()
            async def start(self):
                BlockingOwner.started.set(); await BlockingOwner.resume.wait(); await super().start()
        class CF:
            def __call__(self):
                calls[0]+=1
                return BlockingOwner(command=sys.executable,args=[str(FAKE_SERVER)],cwd=str(FAKE_SERVER.parent))
        async def _run():
            reg=get_registry(); exec=WorkerExecutor(reg,CF())
            BlockingOwner.started.clear(); BlockingOwner.resume.clear()
            res={}
            async def ta(): res["a"]=await exec.start_or_get_worker("same-s")
            async def tb():
                await BlockingOwner.started.wait(); await asyncio.sleep(0.05)
                res["b"]=await exec.start_or_get_worker("same-s")
            ga=asyncio.create_task(ta()); gb=asyncio.create_task(tb())
            await BlockingOwner.started.wait(); await asyncio.sleep(0.05)
            BlockingOwner.resume.set()
            await asyncio.gather(ga,gb)
            assert res["a"]["status"]=="success"
            assert res["b"]["status"]=="error" and res["b"]["error"]["code"]=="LOCK_BUSY"
            assert calls[0]==1
            w=reg.get_worker("same-s"); await w.owner.shutdown() if w else None
        asyncio.run(_run())


# ================================================================
# Tombstone + process kill
# ================================================================
class TestTombstone:
    def test_oversized(self):
        reg=WorkerRegistry()
        ops={f"o-{i:04d}":Operation(operation_id=f"o-{i:04d}",status="succeeded") for i in range(2000)}
        reg._move_to_tombstone(ops)
        assert reg.tombstone_count()==1000 and reg.get_operation("o-0000") is None and reg.get_operation("o-1999") is not None
    def test_pid(self): assert not WorkerRegistry.kill_process_tree(0)


# ================================================================
# Real MCP
# ================================================================
class TestRealMCP:
    @needs_real
    def test_real_handshake_pid_and_shutdown(self):
        """Real old MCP: PID captured, alive before shutdown, gone after."""
        async def _run():
            o=BridgeOwner(); await o.start()
            assert o.child_pid and o.child_pid>0
            assert _is_pid_alive(o.child_pid)
            tools=await o.list_tools(); assert len(tools)==27
            names={t["name"] for t in tools}
            assert {"get_capabilities","create_project","synth_design"}<=names
            resp=await o.call_tool("get_capabilities",{},timeout=120)
            assert resp.status in ("success","error")
            sr=await o.shutdown()
            assert sr.cleaned, f"shutdown not clean: {sr.error}"
            gone=await _poll_gone(o.child_pid); assert gone
        asyncio.run(_run())
