"""test_r1_mcp_sdk.py — Real MCP SDK: 9 APIs, persisted ops, crash recovery, second instance."""
import asyncio, json, os, shutil, signal, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction,
    EXECUTION_LANE_IDLE,
    OP_RUNNING, OP_SUCCEEDED,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_service import op_transition, request_signature
from mcps.zynq_mcp.control.process_guard import is_pid_alive

WS = str(resolve_workspace_root())
REAL = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


async def _call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return json.loads(r.content[0].text)


@pytest.fixture
def zynq_env(tmp_path):
    rt = tmp_path / ".zynq_runtime"
    old = os.environ.get("ZYNQ_RUNTIME_ROOT")
    os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcps.zynq_mcp.server"], env=os.environ)
    yield params, rt
    if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
    else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
    shutil.rmtree(str(rt), ignore_errors=True)


# === Persisted Operation ===
class TestSDKWithPersistedOperation:
    @pytest.fixture
    def prepared_runtime(self, tmp_path):
        rt = tmp_path / ".zynq_prep"
        rt.mkdir(parents=True)
        wsid = compute_workspace_id(resolve_workspace_root())
        g = InstanceGuard(rt, wsid); g.determine_role()
        assert g.is_primary
        lp = rt / "execution_ledger.json"
        def _i(l):
            l.instance_id=g.instance_id; l.workspace_id=wsid; l.execution_lane=EXECUTION_LANE_IDLE
            l.primary_instance_id=g.instance_id
            l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD",
                       "board_package_revision":REAL,"expected_board_revision":REAL}
            return l
        l=ledger_transaction(g,lp,_i)
        sig=request_signature("sdk","PL_BUILD","pl_synthesize",{},REAL)
        oid=f"op-sdk-{uuid.uuid4().hex[:8]}"
        mut=preflight_mutator("pl_synthesize",{},"sdk","ALINX_AX7020_v1.0","p",oid,sig)
        l=ledger_transaction(g,lp,mut)
        op_transition(g,lp,oid,OP_RUNNING)
        op_transition(g,lp,oid,OP_SUCCEEDED,result={"ok":True})
        g.release_owner_lock()
        return rt, oid

    def test_get_operation_status_real_op(self, prepared_runtime):
        rt, oid = prepared_runtime
        old=os.environ.get("ZYNQ_RUNTIME_ROOT"); os.environ["ZYNQ_RUNTIME_ROOT"]=str(rt)
        params=StdioServerParameters(command=sys.executable,args=["-m","mcps.zynq_mcp.server"],env=os.environ)
        try:
            async def _run():
                async with stdio_client(params) as (r,w):
                    async with ClientSession(r,w) as s:
                        await s.initialize()
                        d=await _call(s,"get_operation_status",{"operation_id":oid})
                        assert d["status"]=="success", f"Failed: {d}"
                        assert d["data"]["operation_id"]==oid
                        assert d["data"]["status"]==OP_SUCCEEDED
            asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"]=old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT",None)

    def test_wait_operation_terminal(self, prepared_runtime):
        rt, oid = prepared_runtime
        old=os.environ.get("ZYNQ_RUNTIME_ROOT"); os.environ["ZYNQ_RUNTIME_ROOT"]=str(rt)
        params=StdioServerParameters(command=sys.executable,args=["-m","mcps.zynq_mcp.server"],env=os.environ)
        try:
            async def _run():
                async with stdio_client(params) as (r,w):
                    async with ClientSession(r,w) as s:
                        await s.initialize()
                        d=await _call(s,"wait_operation",{"operation_id":oid,"timeout_s":10})
                        assert d["status"]=="success"
                        assert d["data"]["operation_id"]==oid
                        assert d["data"]["status"] in ("SUCCEEDED","FAILED","CANCELLED","TIMED_OUT","INTERRUPTED","OUTCOME_UNKNOWN")
            asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"]=old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT",None)


# === All 33 tools (9 control + 2 domain + 22 PS) ===
class TestAllTools:
    def test_tool_count_matches_capabilities(self, zynq_env):
        """E007: total_tools=73 after R3.1-C + B05 + B06 (33 PS/BSP) + B07 PL bridge (26) + B06 third batch (9) + B01 UART capture (3) + B01 UART diagnostics (1) + B01 Phase 4 verify_consistency (1) + B01 Phase 6 observation (1) + B05-R2 platform atoms (14) → 99. ps_ensure_arm_accessible adds 1 → 100."""
        params,_=zynq_env
        async def _run():
            async with stdio_client(params) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize()
                    d=await _call(s,"get_capabilities"); assert d["data"]["total_tools"]==101
                    d=await _call(s,"get_execution_state"); assert d["data"]["instance_role"]=="primary"
                    proj=tempfile.mkdtemp()
                    d=await _call(s,"create_session",{"board_id":"ALINX_AX7020_v1.0","project_path":proj})
                    assert d["status"]=="success"
                    sid=d["data"]["session_id"]
                    assert isinstance(sid,str) and len(sid)>0
                    assert d["data"]["board_package_revision"]!=""
                    d=await _call(s,"get_session_info",{"session_id":sid}); assert d["status"]=="success"
                    d=await _call(s,"get_operation_status",{"operation_id":"nonexistent"})
                    assert d["status"]=="error"
                    d=await _call(s,"recover_execution"); assert d["status"]=="success"
                    assert d["data"]["execution_lane"]=="IDLE"
                    d=await _call(s,"wait_operation",{"operation_id":"nonexistent-op","timeout_s":5})
                    # Nonexistent op returns OPERATION_NOT_FOUND immediately
                    assert d["status"] == "error", f"Expected error for nonexistent-op, got: {d}"
                    assert d["error"]["code"] == "OPERATION_NOT_FOUND"
                    d=await _call(s,"diagnose_execution"); assert d["status"]=="success"
                    d=await _call(s,"close_session",{"session_id":sid}); assert d["status"]=="success"
                    shutil.rmtree(proj,ignore_errors=True)
        asyncio.run(_run())

    def test_query_no_seq_bump(self, zynq_env):
        params,_=zynq_env
        async def _run():
            async with stdio_client(params) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize()
                    d=await _call(s,"get_execution_state"); seq1=d["data"]["ledger_sequence"]
                    await _call(s,"get_capabilities"); await _call(s,"diagnose_execution")
                    d=await _call(s,"get_execution_state"); seq2=d["data"]["ledger_sequence"]
                    assert seq1==seq2
        asyncio.run(_run())

    def test_unknown_tool(self, zynq_env):
        params,_=zynq_env
        async def _run():
            async with stdio_client(params) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize()
                    d=await _call(s,"nonexistent_tool")
                    assert d["status"]=="error" and d["error"]["code"]=="INVALID_ARGUMENT"
        asyncio.run(_run())


# === Real crash recovery: subprocess admits op, prints READY, stays alive, gets killed ===
class TestCrashRecovery:
    def test_primary_crash_new_server_recovers(self, tmp_path):
        rt = tmp_path / ".zynq_cr"; rt.mkdir(parents=True)
        wsid = compute_workspace_id(resolve_workspace_root())

        # Build the crash-primary script
        crash_script = tmp_path / "crash_primary.py"
        crash_script.write_text(f'''
import sys, time, os, json, signal
from pathlib import Path
sys.path.insert(0, r'{WS}')
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, ExecutionLedger, EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_service import op_transition, request_signature
from mcps.zynq_mcp.control.workspace import compute_workspace_id, resolve_workspace_root

rt_p = Path(r'{rt}')
wsid = compute_workspace_id(resolve_workspace_root())
g = InstanceGuard(rt_p, wsid)
r = g.determine_role()
assert r.name == 'PRIMARY', f'Expected PRIMARY got {{r.name}}'
lp = rt_p / 'execution_ledger.json'
REAL = 'sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7'

def _init(l):
    l.instance_id = g.instance_id
    l.workspace_id = wsid
    l.execution_lane = 'IDLE'
    l.primary_instance_id = g.instance_id
    l.context = dict(board_id='ALINX_AX7020_v1.0', current_stage='PL_BUILD',
                     board_package_revision=REAL, expected_board_revision=REAL)
    return l

l = ledger_transaction(g, lp, _init)
sig = request_signature('crash', 'PL_BUILD', 'pl_synthesize', dict(), REAL)
oid = 'op-crash-sub'
mut = preflight_mutator('pl_synthesize', dict(), 'crash', 'ALINX_AX7020_v1.0', 'p', oid, sig)
l = ledger_transaction(g, lp, mut)
l2 = op_transition(g, lp, oid, 'RUNNING')['ledger']
ao = l2.active_operation or dict()
print(f'READY|pid={{os.getpid()}}|oid={{oid}}|lane={{l2.execution_lane}}|ao_status={{ao.get("status","None")}}', flush=True)

def _sig_handler(signum, frame):
    sys.exit(0)
signal.signal(signal.SIGTERM, _sig_handler)
while True:
    time.sleep(1)
''', encoding='utf-8')

        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        try:
            # Phase 1: Start primary subprocess
            proc = subprocess.Popen(
                [sys.executable, str(crash_script)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ)
            line = proc.stdout.readline()
            assert "READY|" in line, f"Primary not ready: {line}\nSTDERR: {proc.stderr.read()}"
            parts = dict(p.split("=") for p in line.strip().split("|")[1:])
            primary_pid = int(parts["pid"]); crash_oid = parts["oid"]
            assert parts["lane"] == "BUSY"
            assert parts["ao_status"] == "RUNNING"

            # Phase 2: Verify primary PID is alive
            assert is_pid_alive(primary_pid), f"Primary PID {primary_pid} not alive"

            # Phase 3: Kill primary (simulate crash)
            proc.kill()
            proc.wait(timeout=10)
            time.sleep(1.0)
            assert not is_pid_alive(primary_pid), f"Primary PID {primary_pid} still alive after kill"

            # Phase 4: Start new MCP server — should reconcile to RECOVERY_REQUIRED
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "mcps.zynq_mcp.server"], env=os.environ)
            async def _run():
                async with stdio_client(params) as (r,w):
                    async with ClientSession(r,w) as s:
                        await s.initialize()
                        d=await _call(s,"get_execution_state")
                        assert d["data"]["execution_lane"]=="RECOVERY_REQUIRED", \
                            f"Got lane={d['data']['execution_lane']}"
                        assert d["data"]["active_operation"] is None
                        assert d["data"]["previous_operation"]==crash_oid, \
                            f"Expected prev={crash_oid} got {d['data']['previous_operation']}"
                        d2=await _call(s,"get_operation_status",{"operation_id":crash_oid})
                        assert d2["status"]=="success"
                        prev_status = d2["data"]["status"]
                        assert prev_status in ("OUTCOME_UNKNOWN","INTERRUPTED"), \
                            f"Expected recovery status, got {prev_status}"
            asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"]=old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT",None)
            shutil.rmtree(str(rt),ignore_errors=True)


# === Second instance ===
class TestSecondInstance:
    def test_second_exits_with_diagnostic(self, tmp_path):
        rt=tmp_path/".zynq_2i"; rt.mkdir(parents=True)
        old=os.environ.get("ZYNQ_RUNTIME_ROOT"); os.environ["ZYNQ_RUNTIME_ROOT"]=str(rt)
        try:
            params=StdioServerParameters(command=sys.executable,args=["-m","mcps.zynq_mcp.server"],env=os.environ)
            async def _prime():
                async with stdio_client(params) as (r,w):
                    async with ClientSession(r,w) as prime:
                        await prime.initialize()
                        d=await _call(prime,"get_execution_state")
                        assert d["data"]["instance_role"]=="primary"
                        seq_before = d["data"]["ledger_sequence"]

                        r2=subprocess.run([sys.executable,"-m","mcps.zynq_mcp.server"],
                            capture_output=True,text=True,timeout=15,env=os.environ)
                        assert r2.returncode==0, f"Expected exit 0 got {r2.returncode}"
                        combined=(r2.stdout or "")+(r2.stderr or "")
                        assert "INSTANCE_ALREADY_RUNNING" in combined, f"Missing: {combined[:200]}"

                        d3=await _call(prime,"get_execution_state")
                        assert d3["data"]["ledger_sequence"] == seq_before, \
                            f"Second instance bumped sequence from {seq_before} to {d3['data']['ledger_sequence']}"

                        d4=await _call(prime,"diagnose_execution")
                        wpid = d4["data"].get("worker_pid")
                        assert wpid is None or wpid == "None" or wpid == 0, \
                            f"Second instance may have started a Worker: pid={wpid}"

                        d5=await _call(prime,"get_execution_state")
                        assert d5["data"]["instance_role"]=="primary"
            asyncio.run(_prime())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"]=old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT",None)
            shutil.rmtree(str(rt),ignore_errors=True)
