"""
test_observation.py — B01 §5 Phase 6 Observation & Pass/Fail adjudication.

Covers evaluate_observation verdict rules (FROZEN; B11 phase 2: markers are
caller-supplied — the GPIO_E2E_* defaults were removed):
  · pass_marker present             → PASS
  · fail_marker present             → FAIL
  · no marker, empty/blank text     → TIMEOUT
  · no marker, non-blank text       → INCOMPLETE
  · both markers present            → PASS (pass takes precedence)
  · custom markers                  → custom verdict
  · truncated marker frame          → INCOMPLETE + partial_markers diagnostic
  · missing / empty / non-string markers → INVALID_ARGUMENT (fail-closed)

Also verifies registration as an MCP query tool (schema: pass_marker and
fail_marker are REQUIRED) and dispatch routing through the production query
path. Pure text analysis — no hardware, no board.
"""
import asyncio, json, os, shutil, sys, tempfile, uuid
from pathlib import Path
import pytest

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, EXECUTION_LANE_IDLE, WORKER_STATE_ABSENT,
)
from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id
from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
from mcps.zynq_mcp.dispatcher import ZynqDispatcher, _QUERY_TOOLS
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.domains.verification.observation import evaluate_observation

PASS = "TEST_E2E_PASS"
FAIL = "TEST_E2E_FAIL"
BOARD = "ALINX_AX7020_v1.0"
SH_PKG = "sha256:" + "01" * 32
SH_BP = "sha256:" + "02" * 32
REAL_WSID = compute_workspace_id(resolve_workspace_root())


# ── Pure verdict function ────────────────────────────────────────────────

class TestObservationVerdicts:

    @pytest.mark.asyncio
    async def test_pass_marker_verdict_pass(self):
        result = await evaluate_observation(
            uart_text=f"boot ok\n{PASS}\n", pass_marker=PASS, fail_marker=FAIL)
        assert result["status"] == "success"
        d = result["data"]
        assert d["verdict"] == "PASS"
        assert d["pass_marker_found"] is True
        assert d["fail_marker_found"] is False

    @pytest.mark.asyncio
    async def test_fail_marker_verdict_fail(self):
        result = await evaluate_observation(
            uart_text=f"something broke\n{FAIL}\n", pass_marker=PASS, fail_marker=FAIL)
        assert result["status"] == "success"
        d = result["data"]
        assert d["verdict"] == "FAIL"
        assert d["pass_marker_found"] is False
        assert d["fail_marker_found"] is True

    @pytest.mark.asyncio
    async def test_empty_text_timeout(self):
        result = await evaluate_observation(
            uart_text="", pass_marker=PASS, fail_marker=FAIL)
        assert result["status"] == "success"
        d = result["data"]
        assert d["verdict"] == "TIMEOUT"
        assert d["pass_marker_found"] is False
        assert d["fail_marker_found"] is False
        assert d["text_length"] == 0
        assert d["text_preview"] == ""

    @pytest.mark.asyncio
    async def test_whitespace_text_timeout(self):
        result = await evaluate_observation(
            uart_text="   \r\n\t  ", pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["verdict"] == "TIMEOUT"
        assert d["pass_marker_found"] is False
        assert d["fail_marker_found"] is False

    @pytest.mark.asyncio
    async def test_content_no_marker_incomplete(self):
        result = await evaluate_observation(
            uart_text="console ready, awaiting test frame...",
            pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["verdict"] == "INCOMPLETE"
        assert d["pass_marker_found"] is False
        assert d["fail_marker_found"] is False

    @pytest.mark.asyncio
    async def test_both_markers_pass_takes_precedence(self):
        result = await evaluate_observation(
            uart_text=f"step1 {FAIL}\nstep2 {PASS}\n",
            pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["verdict"] == "PASS"
        assert d["pass_marker_found"] is True
        assert d["fail_marker_found"] is True

    @pytest.mark.asyncio
    async def test_custom_markers(self):
        result = await evaluate_observation(
            uart_text="MY_PASS_TOKEN",
            pass_marker="MY_PASS_TOKEN", fail_marker="MY_FAIL_TOKEN")
        assert result["status"] == "success"
        assert result["data"]["verdict"] == "PASS"
        result2 = await evaluate_observation(
            uart_text="MY_FAIL_TOKEN",
            pass_marker="MY_PASS_TOKEN", fail_marker="MY_FAIL_TOKEN")
        assert result2["data"]["verdict"] == "FAIL"
        result3 = await evaluate_observation(
            uart_text="MY_PASS_TOKEN",
            pass_marker="CUSTOM_A", fail_marker="CUSTOM_B")
        assert result3["data"]["verdict"] == "INCOMPLETE"

    @pytest.mark.asyncio
    async def test_truncated_pass_frame_is_incomplete(self):
        # A cut-short PASS frame is an incomplete marker, not a pass.
        result = await evaluate_observation(
            uart_text="...TEST_E2E_PAS", pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["verdict"] == "INCOMPLETE"
        assert "TEST_E2E_PAS" in d["partial_markers"]
        assert d["pass_marker_found"] is False

    @pytest.mark.asyncio
    async def test_truncated_fail_frame_reported(self):
        result = await evaluate_observation(
            uart_text="...TEST_E2E_FAI", pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["verdict"] == "INCOMPLETE"
        assert "TEST_E2E_FAI" in d["partial_markers"]

    @pytest.mark.asyncio
    async def test_preview_capped_at_200(self):
        long_text = "A" * 500 + PASS
        result = await evaluate_observation(
            uart_text=long_text, pass_marker=PASS, fail_marker=FAIL)
        d = result["data"]
        assert d["text_length"] == len(long_text)
        assert len(d["text_preview"]) == 200
        assert d["verdict"] == "PASS"

    @pytest.mark.asyncio
    async def test_non_string_text_errors(self):
        result = await evaluate_observation(
            uart_text=12345, pass_marker=PASS, fail_marker=FAIL)
        assert result["status"] == "error"
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        assert result["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_missing_markers_error(self):
        """B11 phase 2: markers are REQUIRED — a missing marker is a
        fail-closed INVALID_ARGUMENT, never a default verdict."""
        result = await evaluate_observation(uart_text="x")
        assert result["status"] == "error"
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        assert result["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert "pass_marker" in str(result["error"]["message"])
        result2 = await evaluate_observation(
            uart_text="x", pass_marker=PASS)
        assert result2["status"] == "error"
        assert result2["error"]["code"] == "INVALID_ARGUMENT"
        assert "fail_marker" in str(result2["error"]["message"])

    @pytest.mark.asyncio
    async def test_empty_marker_errors(self):
        result = await evaluate_observation(uart_text="x", pass_marker="")
        assert result["status"] == "error"
        assert result["error"]["code"] == "INVALID_ARGUMENT"
        result2 = await evaluate_observation(uart_text="x", fail_marker=None)
        assert result2["status"] == "error"
        assert result2["error"]["code"] == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_bridge_accepted_but_unused(self):
        # ps_* calling convention: bridge may be passed first, never used.
        result = await evaluate_observation(
            object(), uart_text=f"ok {PASS}", pass_marker=PASS, fail_marker=FAIL)
        assert result["status"] == "success"
        assert result["data"]["verdict"] == "PASS"

    @pytest.mark.asyncio
    async def test_idempotent(self):
        # Envelopes carry a fresh request_id per call; the adjudicated data
        # must be byte-identical across identical inputs.
        text = f"boot\n{PASS}\n"
        r1 = await evaluate_observation(
            uart_text=text, pass_marker=PASS, fail_marker=FAIL)
        r2 = await evaluate_observation(
            uart_text=text, pass_marker=PASS, fail_marker=FAIL)
        assert r1["status"] == r2["status"] == "success"
        assert r1["data"] == r2["data"]


# ── Query tool registration ─────────────────────────────────────────────

class TestQueryToolRegistration:

    def test_schema_registered(self):
        tools = {t.name: t for t in ALL_TOOLS}
        assert "evaluate_observation" in tools
        t = tools["evaluate_observation"]
        props = t.inputSchema.get("properties", {})
        assert set(props) >= {"uart_text", "pass_marker", "fail_marker"}
        # B11 phase 2: markers are REQUIRED (no GPIO defaults in the domain)
        assert t.inputSchema["required"] == ["uart_text", "pass_marker", "fail_marker"]
        assert props["uart_text"]["type"] == "string"
        assert props["pass_marker"]["type"] == "string"
        assert props["fail_marker"]["type"] == "string"
        # empty text must be representable (it is a TIMEOUT verdict)
        assert "minLength" not in props["uart_text"]

    def test_routed_as_query(self):
        assert "evaluate_observation" in _QUERY_TOOLS


# ── Dispatch through the production query path ──────────────────────────

@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, REAL_WSID); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _prep_ledger(rt, g, project_path=""):
    lp = rt / "execution_ledger.json"
    def _i(l):
        l.instance_id = g.instance_id
        l.workspace_id = REAL_WSID
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        l.worker["state"] = WORKER_STATE_ABSENT
        l.worker["pid"] = None
        l.context = {
            "session_id": f"session-{uuid.uuid4().hex[:8]}",
            "board_id": BOARD, "project_path": project_path,
            "board_package_revision": SH_PKG,
            "expected_board_revision": SH_PKG,
            "board_profile_sha256": SH_BP,
            "current_stage": "DEPLOY",
            "platform_revision": "",
            "pl_revision": None, "ps_revision": None,
        }
        return l
    ledger_transaction(g, lp, _i)
    return lp


class TestQueryToolDispatch:

    @pytest.mark.asyncio
    async def test_dispatcher_routes_query(self, rtg):
        rt, g = rtg
        lp = _prep_ledger(rt, g)
        dispatcher = ZynqDispatcher(None, OperationRegistry(), g, lp, None)
        result = await dispatcher.dispatch("evaluate_observation", {
            "uart_text": f"boot {PASS}",
            "pass_marker": PASS, "fail_marker": FAIL,
        }, True)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "success"
        assert parsed["data"]["verdict"] == "PASS"
        assert parsed["data"]["pass_marker_found"] is True

    @pytest.mark.asyncio
    async def test_dispatcher_custom_markers(self, rtg):
        rt, g = rtg
        lp = _prep_ledger(rt, g)
        dispatcher = ZynqDispatcher(None, OperationRegistry(), g, lp, None)
        result = await dispatcher.dispatch("evaluate_observation", {
            "uart_text": "BAD",
            "pass_marker": "GOOD", "fail_marker": "BAD",
        }, True)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "success"
        assert parsed["data"]["verdict"] == "FAIL"

    @pytest.mark.asyncio
    async def test_dispatcher_fail_closed_on_missing_text(self, rtg):
        rt, g = rtg
        lp = _prep_ledger(rt, g)
        dispatcher = ZynqDispatcher(None, OperationRegistry(), g, lp, None)
        result = await dispatcher.dispatch("evaluate_observation", {}, True)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "error"
        assert parsed["error"]["code"] == "INVALID_ARGUMENT"
        assert parsed["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_dispatcher_fail_closed_on_missing_markers(self, rtg):
        """B11 phase 2: markers are required at the transport too — a call
        without them never falls back to a default verdict."""
        rt, g = rtg
        lp = _prep_ledger(rt, g)
        dispatcher = ZynqDispatcher(None, OperationRegistry(), g, lp, None)
        result = await dispatcher.dispatch("evaluate_observation", {
            "uart_text": "boot",
        }, True)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "error"
        assert parsed["error"]["code"] == "INVALID_ARGUMENT"
        assert parsed["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"

    def test_sdk_query_tool(self, rtg):
        """Real MCP server call over stdio — full production surface.

        The subprocess needs the package root (the parent of the ``mcps``
        package dir) on PYTHONPATH, because ``python -m`` does not inherit
        pytest's sys.path injection. PYTHONPATH is scoped to the subprocess
        env only.
        """
        rt, g = rtg
        _prep_ledger(rt, g)
        g.release_owner_lock()
        import mcps as _mcps_pkg
        pkg_root = str(Path(_mcps_pkg.__file__).resolve().parent.parent)
        sub_env = dict(os.environ)
        old_pp = sub_env.get("PYTHONPATH")
        sub_env["PYTHONPATH"] = pkg_root + (os.pathsep + old_pp if old_pp else "")
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        sub_env["ZYNQ_RUNTIME_ROOT"] = str(rt)
        try:
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "mcps.zynq_mcp.server"], env=sub_env)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        res = await s.call_tool("evaluate_observation", {
                            "uart_text": f"running test {PASS}",
                            "pass_marker": PASS, "fail_marker": FAIL,
                        })
                        assert not res.isError
                        d = json.loads(res.content[0].text)
                        assert d["status"] == "success"
                        assert d["data"]["verdict"] == "PASS"
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
