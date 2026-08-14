"""
ps_mcp/server.py — Zynq PS MCP skeleton (0 domain APIs).
"""
from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcps.common.control_api import ToolDispatcher, PS_CAPABILITIES

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [ps_mcp] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ps_mcp")

_dispatcher = ToolDispatcher(PS_CAPABILITIES)
server = Server("zynq_ps")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _dispatcher.schemas


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return _dispatcher.dispatch(name, arguments)


def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    main()
