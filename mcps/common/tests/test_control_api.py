"""T-B02-C: Control API dispatch — unit tests without MCP server."""

import json
from mcps.common.control_api import ToolDispatcher, PLATFORM_CAPABILITIES

_dispatcher = ToolDispatcher(PLATFORM_CAPABILITIES)


def _call(name, args):
    result = _dispatcher.dispatch(name, args)
    return json.loads(result[0].text)


def test_dispatch_non_dict_arguments():
    for bad in [None, 123, "string", ["a", "b"]]:
        d = _call("create_session", bad)
        assert d["status"] == "error"
        assert d["error"]["code"] == "INVALID_ARGUMENT"
        assert "request_id" in d, f"Missing request_id for {type(bad).__name__}"


def test_create_session_board_id_int():
    d = _call("create_session", {"board_id": 123, "project_path": "p"})
    assert d["status"] == "error"
    assert d["error"]["code"] == "INVALID_ARGUMENT"


def test_create_session_project_path_bool():
    d = _call("create_session", {"board_id": "b", "project_path": True})
    assert d["status"] == "error"
    assert d["error"]["code"] == "INVALID_ARGUMENT"


def test_get_session_info_list():
    d = _call("get_session_info", {"session_id": ["x"]})
    assert d["status"] == "error"
    assert d["error"]["code"] == "INVALID_ARGUMENT"


def test_get_operation_status_none():
    d = _call("get_operation_status", {"operation_id": None})
    assert d["status"] == "error"
    assert d["error"]["code"] == "INVALID_ARGUMENT"


def test_get_capabilities_non_dict():
    d = _call("get_capabilities", None)
    assert d["status"] == "error"
    assert d["error"]["code"] == "INVALID_ARGUMENT"


def test_get_capabilities_valid():
    d = _call("get_capabilities", {})
    assert d["status"] == "success"
    assert d["data"]["mcp_name"] == "zynq_platform"
