"""
control_api.py — Shared handler implementations + schemas + dispatch.

All three MCP skeletons import and use these. No code duplication.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from mcp.types import Tool, TextContent

from mcps.common.tool_response import success, error
from mcps.common.context import (
    create_session, close_session, get_session_info,
    SessionError, BoardProfileError,
)

logger = logging.getLogger("control_api")

# In-memory operation registry (B04+ will extend)
_operations: dict[str, dict] = {}


# ================================================================
# Tool schemas (shared by all three MCP Servers)
# ================================================================

CONTROL_TOOL_SCHEMAS = [
    Tool(
        name="create_session",
        description="Create a new MCP session",
        inputSchema={
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "minLength": 1},
                "project_path": {"type": "string", "minLength": 1},
            },
            "required": ["board_id", "project_path"],
        },
    ),
    Tool(
        name="close_session",
        description="Close an MCP session and release all resources",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="get_session_info",
        description="Get metadata for an active session",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="get_capabilities",
        description="Get MCP capability declaration (ToolResponse envelope)",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_operation_status",
        description="Get status of an async long-running operation",
        inputSchema={
            "type": "object",
            "properties": {
                "operation_id": {"type": "string", "minLength": 1},
            },
            "required": ["operation_id"],
        },
    ),
]

# ================================================================
# Parameter validation helpers (fail-closed)
# ================================================================

def _check_str_arg(args: Any, name: str) -> str | dict:
    """Extract a required string from args. Returns (value, None) or (None, error_dict)."""
    if not isinstance(args, dict):
        return error(f"arguments must be a JSON object, got {type(args).__name__}",
                     code="INVALID_ARGUMENT").to_dict()
    value = args.get(name)
    if not isinstance(value, str):
        return error(
            f"{name} must be a string, got {type(value).__name__}",
            code="INVALID_ARGUMENT").to_dict()
    if not value.strip():
        return error(
            f"{name} must be a non-empty string",
            code="INVALID_ARGUMENT").to_dict()
    return value


# ================================================================
# Handler implementations
# ================================================================

def handle_create_session(args: Any) -> dict:
    board_id = _check_str_arg(args, "board_id")
    if isinstance(board_id, dict):
        return board_id
    project_path = _check_str_arg(args, "project_path")
    if isinstance(project_path, dict):
        return project_path
    try:
        ctx = create_session(board_id, project_path)
        return success(
            {"session_id": ctx.session_id,
             "board_id": ctx.board_id,
             "project_path": ctx.project_path},
            context_ref=ctx.session_id).to_dict()
    except BoardProfileError as e:
        return error(str(e), code="CONTEXT_INVALID").to_dict()


def handle_close_session(args: Any) -> dict:
    sid = _check_str_arg(args, "session_id")
    if isinstance(sid, dict):
        return sid
    try:
        close_session(sid)
        return success({"closed": sid}).to_dict()
    except SessionError as e:
        return error(str(e), code="CONTEXT_INVALID").to_dict()


def handle_get_session_info(args: Any) -> dict:
    sid = _check_str_arg(args, "session_id")
    if isinstance(sid, dict):
        return sid
    try:
        info = get_session_info(sid)
        return success(info).to_dict()
    except SessionError as e:
        return error(str(e), code="CONTEXT_INVALID").to_dict()


def handle_get_operation_status(args: Any) -> dict:
    op_id = _check_str_arg(args, "operation_id")
    if isinstance(op_id, dict):
        return op_id
    if op_id not in _operations:
        return error(f"Operation not found: {op_id}",
                     code="OPERATION_NOT_FOUND").to_dict()
    return success(_operations[op_id]).to_dict()


# ================================================================
# Capability declarations
# ================================================================

PLATFORM_CAPABILITIES = {
    "mcp_name": "zynq_platform", "version": "0.1.0", "status": "skeleton",
    "domain_apis_implemented": 0, "domain_apis_planned": 12,
    "planned_domain_apis": [
        "platform.create_design", "platform.add_ps7",
        "platform.configure_ps7", "platform.add_axi_gpio",
        "platform.connect_interface", "platform.connect_clock",
        "platform.connect_reset", "platform.set_address",
        "platform.validate", "platform.generate_wrapper",
        "platform.export_hardware", "platform.export_manifest",
    ],
}

PL_CAPABILITIES = {
    "mcp_name": "zynq_pl", "version": "0.1.0", "status": "skeleton",
    "domain_apis_implemented": 0, "domain_apis_planned": 12,
    "planned_domain_apis": [
        "pl.generate_system_top", "pl.create_project", "pl.set_top",
        "pl.synthesize", "pl.place_and_route", "pl.analyze_timing",
        "pl.generate_bitstream", "pl.connect_hw_server",
        "pl.open_hw_target", "pl.select_device",
        "pl.program", "pl.get_device_status",
    ],
}

PS_CAPABILITIES = {
    "mcp_name": "zynq_ps", "version": "0.1.0", "status": "skeleton",
    "domain_apis_implemented": 0, "domain_apis_planned": 19,
    "planned_domain_apis": [
        "ps.import_hardware", "ps.create_platform", "ps.create_bsp",
        "ps.create_app", "ps.add_sources", "ps.compile",
        "ps.connect_hw_server", "ps.select_target", "ps.reset",
        "ps.initialize", "ps.download", "ps.run", "ps.halt",
        "ps.get_target_status", "ps.read_register",
        "ps.start_uart_capture", "ps.wait_uart_capture",
        "ps.stop_uart_capture", "ps.recover_target",
    ],
}


def handle_get_capabilities(capabilities: dict):
    return success(capabilities)


# ================================================================
# Unified dispatch
# ================================================================

class ToolDispatcher:
    """Centralized dispatch for common control + optional domain tools."""

    def __init__(self, capabilities: dict) -> None:
        self._handlers: dict[str, Callable[[Any], dict]] = {
            "create_session": handle_create_session,
            "close_session": handle_close_session,
            "get_session_info": handle_get_session_info,
            "get_capabilities": (
                lambda args: handle_get_capabilities(capabilities).to_dict()),
            "get_operation_status": handle_get_operation_status,
        }

    @property
    def schemas(self) -> list[Tool]:
        return CONTROL_TOOL_SCHEMAS

    def dispatch(self, tool_name: str, arguments: Any) -> list[TextContent]:
        if not isinstance(arguments, dict):
            return _text_result(
                error("arguments must be a JSON object (dict)",
                      code="INVALID_ARGUMENT").to_dict())
        handler = self._handlers.get(tool_name)
        if handler is None:
            return _text_result(
                error(f"Unknown tool: {tool_name}",
                      code="INVALID_ARGUMENT").to_dict())
        try:
            result = handler(arguments)
            return _text_result(result)
        except Exception as exc:
            logger.exception("Tool error: %s", tool_name)
            return _text_result(
                error("Internal server error", code="INTERNAL_ERROR").to_dict())


def _text_result(d: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(d))]
