"""Fake stdio MCP server for R2 tests. Deterministic, no Vivado dependency."""
import asyncio, json, sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

TOOLS = [
    Tool(name="ping", description="Return pong", inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_capabilities", description="Return caps", inputSchema={"type": "object", "properties": {}}),
    Tool(name="hang_forever", description="Sleep (timeout test)",
         inputSchema={"type": "object", "properties": {"seconds": {"type": "number"}}}),
]


async def main():
    server = Server("fake_mcp")

    @server.list_tools()
    async def list_tools():
        return list(TOOLS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "ping":
            return [TextContent(type="text", text=json.dumps(
                {"status": "success", "data": {"pong": True}}))]
        if name == "get_capabilities":
            return [TextContent(type="text", text=json.dumps(
                {"status": "success", "data": {"mcp_name": "fake_mcp", "version": "1.0"}}))]
        if name == "hang_forever":
            seconds = float(arguments.get("seconds", 300))
            await asyncio.sleep(seconds)
            return [TextContent(type="text", text=json.dumps(
                {"status": "success", "data": {}}))]
        return [TextContent(type="text", text=json.dumps(
            {"status": "error", "error": "unknown_tool"}))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
