"""
fake_mcp_server.py -- Minimal MCP JSON-RPC stdio server for bridge testing.
Supports: initialize, list_tools, call_tool, get_pid (returns os.getpid/ppid),
crash_me (exit 1), sleep_forever (hang for timeout tests).
"""

import json
import os
import sys
import time


def read_message() -> dict | None:
    line = sys.stdin.readline()
    if not line: return None
    return json.loads(line.strip())

def send_message(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

def send_response(msg_id, result):
    send_message({"jsonrpc": "2.0", "id": msg_id, "result": result})

def ok_data(data):
    return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": data})}]}

def main():
    initialized = False
    while True:
        msg = read_message()
        if msg is None: break
        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            # T-034: write to stderr to verify isolation
            sys.stderr.write("FAKE_MCP_STDERR: starting up\n")
            sys.stderr.flush()
            initialized = True
            send_response(msg_id, {"protocolVersion": "2024-11-05",
                "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
                "capabilities": {}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send_response(msg_id, {"tools": [
                {"name": "get_capabilities", "description": "Fake capabilities",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_vivado_info", "description": "Fake vivado info",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "create_project", "description": "Create project",
                 "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string"}, "part": {"type": "string"},
                         "sources": {"type": "array", "items": {"type": "string"}},
                         "constraints": {"type": "array", "items": {"type": "string"}},
                         "project_dir": {"type": "string"}},
                     "required": ["name", "part", "sources", "constraints", "project_dir"]}},
                {"name": "get_pid", "description": "Return OS PID and PPID",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "get_env", "description": "Return env var value",
                 "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string"}},
                     "required": ["name"]}},
                {"name": "crash_me", "description": "Exit immediately",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "sleep_forever", "description": "Hang forever",
                 "inputSchema": {"type": "object", "properties": {}}},
            ]})
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            if tool_name == "crash_me": sys.exit(1)
            elif tool_name == "sleep_forever": time.sleep(99999)
            elif tool_name == "get_pid":
                send_response(msg_id, ok_data({"pid": os.getpid(), "ppid": os.getppid()}))
            elif tool_name == "get_env":
                var_name = params.get("arguments", {}).get("name", "")
                send_response(msg_id, ok_data({"name": var_name, "value": os.environ.get(var_name, "")}))
            elif tool_name == "get_capabilities":
                send_response(msg_id, ok_data({"vivado_version": "2023.1",
                    "part": "xc7z020clg400-2", "device_family": "zynq7000",
                    "design_open": False, "support": {}}))
            elif tool_name == "get_vivado_info":
                send_response(msg_id, ok_data({"version": "2023.1",
                    "build_id": "3865809", "build_date": "fake", "edition": "Vivado"}))
            elif tool_name == "create_project":
                send_response(msg_id, ok_data({"project_name": params.get("arguments", {}).get("name", "x"),
                    "part": params.get("arguments", {}).get("part", ""), "top": "top",
                    "project_dir": params.get("arguments", {}).get("project_dir", "")}))
            else:
                send_message({"jsonrpc": "2.0", "id": msg_id, "result":
                    {"content": [{"type": "text", "text": json.dumps(
                        {"status": "error", "error": f"Unknown tool: {tool_name}"})}]}})
        elif method == "shutdown": break
        else:
            send_message({"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}})

if __name__ == "__main__":
    main()
