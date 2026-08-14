"""O7 R1 remediation contracts."""
from mcps.zynq_mcp.control.capabilities import ALL_TOOLS


def test_all_ps_public_schemas_expose_runtime_required_session_id():
    ps_tools = [tool for tool in ALL_TOOLS if tool.name.startswith("ps_")]
    assert ps_tools
    for tool in ps_tools:
        schema = tool.inputSchema
        session_schema = schema["properties"]["session_id"]
        assert session_schema["type"] == "string", tool.name
        assert session_schema["minLength"] == 1, tool.name
        assert session_schema["description"].startswith(
            "Required active session id"), tool.name


def test_ps_disconnect_schema_matches_runtime_session_contract():
    tool = next(t for t in ALL_TOOLS
                if t.name == "ps_disconnect_hw_server")
    session_schema = tool.inputSchema["properties"]["session_id"]
    assert "SESSION_ID_REQUIRED" in session_schema["description"]
