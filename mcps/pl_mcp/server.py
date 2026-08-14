"""
pl_mcp/server.py -- Zynq PL MCP (Sub-step 1).  5 control APIs, 0 domain APIs.
"""

from __future__ import annotations

import asyncio, json, logging, sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcps.common.tool_response import success, error
from mcps.common.context import (
    create_session, close_session, get_session_info,
    SessionError, BoardProfileError,
)
from mcps.common.control_api import CONTROL_TOOL_SCHEMAS, PL_CAPABILITIES
from mcps.pl_mcp.worker_registry import get_registry, reset_registry
from mcps.pl_mcp.errors import worker_cleanup_failed

logging.basicConfig(level=logging.WARNING,
    format="%(asctime)s [pl_mcp] %(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("pl_mcp")


def _check_str_arg(args: Any, name: str) -> str | dict:
    if not isinstance(args, dict):
        return error(f"arguments must be a JSON object, got {type(args).__name__}",
                     code="INVALID_ARGUMENT").to_dict()
    v = args.get(name)
    if not isinstance(v, str):
        return error(f"{name} must be a string", code="INVALID_ARGUMENT").to_dict()
    if not v.strip():
        return error(f"{name} must be a non-empty string", code="INVALID_ARGUMENT").to_dict()
    return v


def _handle_pl_create_session(args: Any) -> dict:
    a = _check_str_arg(args, "board_id")
    if isinstance(a, dict): return a
    b = _check_str_arg(args, "project_path")
    if isinstance(b, dict): return b
    try:
        ctx = create_session(a, b)
        return success({"session_id": ctx.session_id, "board_id": ctx.board_id,
                        "project_path": ctx.project_path},
                       context_ref=ctx.session_id).to_dict()
    except BoardProfileError as e:
        return error(str(e), code="CONTEXT_INVALID").to_dict()


def _handle_pl_get_session_info(args: Any) -> dict:
    sid = _check_str_arg(args, "session_id")
    if isinstance(sid, dict): return sid
    try: return success(get_session_info(sid)).to_dict()
    except SessionError as e: return error(str(e), code="CONTEXT_INVALID").to_dict()


def _handle_pl_get_capabilities(args: Any) -> dict:
    return success({
        "mcp_name": PL_CAPABILITIES["mcp_name"],
        "version": PL_CAPABILITIES["version"],
        "status": "bridge_ready", "domain_apis_implemented": 0,
        "domain_apis_planned": PL_CAPABILITIES["domain_apis_planned"],
        "planned_domain_apis": PL_CAPABILITIES["planned_domain_apis"],
        "control_apis": 5, "total_tools": 5,
    }).to_dict()


def _handle_pl_get_operation_status(args: Any) -> dict:
    op_id = _check_str_arg(args, "operation_id")
    if isinstance(op_id, dict): return op_id
    reg = get_registry(); op = reg.get_operation(op_id)
    if op is None:
        return error(f"Operation not found: {op_id}", code="OPERATION_NOT_FOUND").to_dict()
    return success(op.to_dict()).to_dict()


async def _handle_pl_close_session_async(args: Any) -> dict:
    sid = _check_str_arg(args, "session_id")
    if isinstance(sid, dict): return sid

    reg = get_registry()
    summary = await reg.shutdown_worker_and_tombstone(sid)

    if not summary["success"]:
        # Worker or leases failed → preserve state, return error
        return worker_cleanup_failed(summary.get("error", "unknown"))

    # Cleanup succeeded: delete context
    try: close_session(sid)
    except SessionError as e: return error(str(e), code="CONTEXT_INVALID").to_dict()

    return success({
        "closed": sid,
        "operations_cancelled": summary["operations_cancelled"],
        "worker_removed": summary["worker_removed"],
        "pid_cleaned": summary["pid_cleaned"],
        "leases_released": summary["leases_released"],
    }).to_dict()


class PLControlAdapter:
    def __init__(self):
        self._sync_handlers = {
            "create_session": _handle_pl_create_session,
            "get_session_info": _handle_pl_get_session_info,
            "get_capabilities": _handle_pl_get_capabilities,
            "get_operation_status": _handle_pl_get_operation_status,
        }
        self._async_handlers = {"close_session": _handle_pl_close_session_async}
        self._domain_handlers: dict[str, Any] = {}

    @property
    def schemas(self) -> list[Tool]: return list(CONTROL_TOOL_SCHEMAS)

    async def dispatch(self, tool_name: str, arguments: Any) -> list[TextContent]:
        if not isinstance(arguments, dict):
            return _text(error("arguments must be a JSON object (dict)",
                               code="INVALID_ARGUMENT").to_dict())
        h = self._sync_handlers.get(tool_name)
        if h is not None:
            try: return _text(h(arguments))
            except Exception as exc:
                logger.exception("Handler error: %s", tool_name)
                return _text(error(f"Internal: {exc}", code="INTERNAL_ERROR").to_dict())
        ah = self._async_handlers.get(tool_name)
        if ah is not None:
            try: return _text(await ah(arguments))
            except Exception as exc:
                logger.exception("Async handler error: %s", tool_name)
                return _text(error(f"Internal: {exc}", code="INTERNAL_ERROR").to_dict())
        dh = self._domain_handlers.get(tool_name)
        if dh is not None:
            try:
                r = dh(arguments)
                if asyncio.iscoroutine(r): r = await r
                return _text(r)
            except Exception as exc:
                logger.exception("Domain handler error: %s", tool_name)
                return _text(error(f"Internal: {exc}", code="INTERNAL_ERROR").to_dict())
        return _text(error(f"Unknown tool: {tool_name}", code="INVALID_ARGUMENT").to_dict())


def _text(d: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(d))]

_adapter = PLControlAdapter()
server = Server("zynq_pl")

@server.list_tools()
async def list_tools() -> list[Tool]: return _adapter.schemas

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await _adapter.dispatch(name, arguments)

def main() -> None: asyncio.run(_run())
async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
if __name__ == "__main__": main()
