"""B13-F-12 修复轮#12: ps_bsp_grep — BSP 真值源查询。

bsp_grep 纯文件系统检索会话 BSP（include/ 生成头 + libsrc/ 驱动头/源），
fail-closed：BSP 不存在报 BSP_NOT_FOUND（绝不静默空）。dispatcher 侧为
query 形态（不走 Operation 状态机），capabilities 注册计数同步（109→110）。
"""
import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="function")

from mcps.zynq_mcp.domains.ps import ps_bsp


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _fake_bsp(root, *, with_libsrc=True):
    """Minimal BSP tree: include/xparameters.h + libsrc driver headers."""
    _write(os.path.join(root, "include", "xparameters.h"),
           "#define XPAR_XEMACPS_0_BASEADDR 0xE000B000\n"
           "/* XUartPs not used here */\n")
    if with_libsrc:
        _write(os.path.join(root, "libsrc", "emacps_v3_18", "src", "xemacps.h"),
               "typedef struct XEmacPs XEmacPs;\n"
               "void XEmacPs_Send(XEmacPs *InstancePtr);\n"
               "/* non-blocking: sends 0 bytes when the TX FIFO is full */\n")
        _write(os.path.join(root, "libsrc", "emacps_v3_18", "src", "xemacps.c"),
               "void XEmacPs_Send(XEmacPs *p) { /* impl */ }\n")


class TestBspGrep:
    async def test_finds_declaration_with_context(self, tmp_path):
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))
        r = ps_bsp.bsp_grep(proj, "XEmacPs_Send", scope="sources")
        assert r["status"] == "success"
        d = r["data"]
        assert d["total_hits"] == 2
        assert d["truncated"] is False
        assert d["bsp_root"].replace("\\", "/").endswith("bsp/ps7")
        files = {h["file"] for h in d["hits"]}
        assert any(f.endswith("xemacps.h") for f in files)
        assert any(f.endswith("xemacps.c") for f in files)
        # context lines carry the surrounding text
        header_hit = next(h for h in d["hits"] if h["file"].endswith("xemacps.h"))
        assert "typedef struct XEmacPs" in header_hit["context"]

    async def test_scope_headers_only(self, tmp_path):
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))
        r = ps_bsp.bsp_grep(proj, "XPAR_XEMACPS_0_BASEADDR", scope="headers")
        assert r["status"] == "success"
        assert r["data"]["total_hits"] == 1
        assert all(h["file"].startswith("include/") for h in r["data"]["hits"])

    async def test_scope_sources_excludes_include(self, tmp_path):
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))
        r = ps_bsp.bsp_grep(proj, "XPAR_XEMACPS_0_BASEADDR", scope="sources")
        assert r["status"] == "success"
        assert r["data"]["total_hits"] == 0
        assert r["data"]["hits"] == []

    async def test_invalid_regex_falls_back_to_literal(self, tmp_path):
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))
        r = ps_bsp.bsp_grep(proj, "XEmacPs_Send(", scope="sources")
        assert r["status"] == "success"
        # literal fallback matches both the .h declaration and the .c
        # implementation (the "(XEmacPs" argument list appears in both)
        assert r["data"]["total_hits"] == 2

    async def test_no_bsp_is_bsp_not_found(self, tmp_path):
        proj = str(tmp_path / "empty")
        os.makedirs(proj, exist_ok=True)
        r = ps_bsp.bsp_grep(proj, "anything")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BSP_NOT_FOUND"

    async def test_invalid_scope_fails_closed(self, tmp_path):
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))
        r = ps_bsp.bsp_grep(proj, "x", scope="bogus")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"

    async def test_max_hits_truncates_with_total(self, tmp_path):
        proj = str(tmp_path / "proj")
        root = os.path.join(proj, "app_platform", "ps7", "bsp", "ps7")
        inc = os.path.join(root, "include")
        os.makedirs(inc, exist_ok=True)
        lines = "\n".join(f"#define XPAR_IP_{i}_BASEADDR 0x4000{i:04X}"
                          for i in range(10))
        _write(os.path.join(inc, "xparameters.h"), lines + "\n")
        os.makedirs(os.path.join(root, "libsrc"), exist_ok=True)
        r = ps_bsp.bsp_grep(proj, r"XPAR_IP_\d+_BASEADDR", max_hits=4)
        assert r["status"] == "success"
        assert len(r["data"]["hits"]) == 4
        assert r["data"]["total_hits"] == 10
        assert r["data"]["truncated"] is True


class TestDispatcherQuery:
    async def test_handler_requires_session_id(self):
        from mcps.zynq_mcp import dispatcher
        r = await dispatcher._ps_bsp_grep_query({}, None)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "SESSION_ID_REQUIRED"

    async def test_handler_uses_ledger_project_path(self, tmp_path):
        from mcps.zynq_mcp import dispatcher
        proj = str(tmp_path / "proj")
        _fake_bsp(os.path.join(proj, "app_platform", "ps7", "bsp", "ps7"))

        class _Ledger:
            context = {"session_id": "session-x", "project_path": proj}

        class _Disp:
            _ledger = _Ledger()

        r = await dispatcher._ps_bsp_grep_query(
            {"session_id": "session-x", "pattern": "XEmacPs_Send"},
            _Disp())
        assert r["status"] == "success"
        assert r["data"]["total_hits"] == 2

    async def test_handler_missing_project_path_fails_closed(self):
        from mcps.zynq_mcp import dispatcher

        class _Ledger:
            context = {}

        class _Disp:
            _ledger = _Ledger()

        r = await dispatcher._ps_bsp_grep_query(
            {"session_id": "session-x", "pattern": "x"}, _Disp())
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "PROJECT_PATH_UNKNOWN"


class TestRegistration:
    async def test_tool_registered_with_session_id_injected(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS, DOMAIN_TOOLS, \
            DOMAIN_APIS_IMPLEMENTED
        tool = next(t for t in ALL_TOOLS if t.name == "ps_bsp_grep")
        assert "session_id" in tool.inputSchema["properties"]
        assert "pattern" in tool.inputSchema["required"]
        assert DOMAIN_APIS_IMPLEMENTED == len(DOMAIN_TOOLS)
        assert len(ALL_TOOLS) == 111  # B13-F-01: +platform_reopen_project (110→111)

    async def test_query_tool_not_in_command_routes(self):
        from mcps.zynq_mcp import dispatcher
        assert "ps_bsp_grep" in dispatcher._QUERY_TOOLS
        assert "ps_bsp_grep" not in dispatcher._PS_TOOL_NAMES
