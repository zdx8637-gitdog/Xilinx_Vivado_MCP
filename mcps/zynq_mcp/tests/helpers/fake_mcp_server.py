"""Fake stdio MCP server for R2 timeout/crash tests. Deterministic, no Vivado dependency."""
import asyncio, json, sys, time
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

TOOLS = [
    Tool(name="get_capabilities", description="Return caps", inputSchema={"type": "object", "properties": {}}),
    Tool(name="hang_forever", description="Sleep indefinitely", inputSchema={"type": "object", "properties": {"seconds": {"type": "number"}}}),
    Tool(name="ping", description="Return pong", inputSchema={"type": "object", "properties": {}}),
    Tool(name="list_tools_delayed", description="List tools after delay", inputSchema={"type": "object", "properties": {"delay": {"type": "number"}}}),
]


async def main():
    server = Server("fake_mcp")

    @server.list_tools()
    async def list_tools(): return list(TOOLS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "get_capabilities":
            return [TextContent(type="text", text=json.dumps(
                {"status": "success", "data": {"mcp_name": "fake", "version": "1.0", "total_tools": 4}}))]
        if name == "hang_forever":
            seconds = float(arguments.get("seconds", 60))
            await asyncio.sleep(seconds)
            return [TextContent(type="text", text=json.dumps({"status": "success", "data": {}}))]
        if name == "ping":
            return [TextContent(type="text", text=json.dumps({"status": "success", "data": {"pong": True}}))]
        if name == "list_tools_delayed":
            delay = float(arguments.get("delay", 0))
            await asyncio.sleep(delay)
            return [TextContent(type="text", text=json.dumps(
                {"status": "success", "data": {"tools": [{"name": "fake_tool"}]}}))]
        return [TextContent(type="text", text=json.dumps({"status": "error", "error": "unknown tool"}))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
