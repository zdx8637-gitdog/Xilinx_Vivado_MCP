"""T-B02-011: PL MCP — MCP SDK ClientSession, timeout, env, no Vivado import."""

import asyncio, ast, json, os, sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert os.path.isdir(os.path.join(PROJECT_ROOT, "mcps"))

_ENV = {**os.environ,
        "PYTHONPATH": PROJECT_ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
TIMEOUT = 30


async def _with_session(coro_fn):
    async with asyncio.timeout(TIMEOUT):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcps.pl_mcp.server"],
            env=_ENV)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return await coro_fn(s)


@pytest.mark.asyncio
async def test_list_tools_exactly_five():
    async def check(s):
        tools = await s.list_tools()
        names = {t.name for t in tools.tools}
        for api in ["create_session", "close_session", "get_session_info",
                     "get_capabilities", "get_operation_status"]:
            assert api in names
        assert len(tools.tools) == 5
    await _with_session(check)


@pytest.mark.asyncio
async def test_get_capabilities():
    async def check(s):
        r = await s.call_tool("get_capabilities", {})
        caps = json.loads(r.content[0].text)
        assert caps["status"] == "success"
        cd = caps["data"]
        assert cd["mcp_name"] == "zynq_pl"
        assert cd["domain_apis_implemented"] == 0
        assert cd["domain_apis_planned"] == 12
    await _with_session(check)


@pytest.mark.asyncio
async def test_create_and_close_session(tmp_path):
    async def check(s):
        r = await s.call_tool("create_session", {
            "board_id": "TEST_AX7020_MINIMAL",
            "project_path": str(tmp_path),
        })
        d = json.loads(r.content[0].text)
        assert d["status"] == "success"
        sid = d["data"]["session_id"]
        r = await s.call_tool("get_session_info", {"session_id": sid})
        assert json.loads(r.content[0].text)["status"] == "success"
        r = await s.call_tool("close_session", {"session_id": sid})
        assert json.loads(r.content[0].text)["status"] == "success"
        r = await s.call_tool("get_session_info", {"session_id": sid})
        d2 = json.loads(r.content[0].text)
        assert d2["status"] == "error"
        assert d2["error"]["code"] == "CONTEXT_INVALID"
    await _with_session(check)


@pytest.mark.asyncio
async def test_operation_not_found():
    async def check(s):
        r = await s.call_tool("get_operation_status",
                              {"operation_id": "nonexistent-op"})
        d = json.loads(r.content[0].text)
        assert d["status"] == "error"
        assert d["error"]["code"] == "OPERATION_NOT_FOUND"
    await _with_session(check)


@pytest.mark.asyncio
async def test_unknown_tool_error_envelope():
    async def check(s):
        r = await s.call_tool("nonexistent_tool", {})
        d = json.loads(r.content[0].text)
        assert d["status"] == "error"
        assert d["error"]["code"] == "INVALID_ARGUMENT"
        assert "request_id" in d
    await _with_session(check)


@pytest.mark.asyncio
async def test_non_string_args_rejected():
    async def check(s):
        tested = 0
        for bad in [123, True, [], None]:
            try:
                r = await s.call_tool("create_session",
                                      {"board_id": bad, "project_path": "p"})
                d = json.loads(r.content[0].text)
                assert d["status"] == "error"
                assert d["error"]["code"] == "INVALID_ARGUMENT"
                tested += 1
            except json.JSONDecodeError:
                tested += 1
        assert tested >= 2
    await _with_session(check)


def test_pl_skeleton_no_vivado_import():
    path = os.path.join(PROJECT_ROOT, "mcps", "pl_mcp", "server.py")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "Xilinx_Vivado_MCP" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "Xilinx_Vivado_MCP" not in node.module
