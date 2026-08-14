"""jtag_target.py — JTAG connection and target management (6 APIs).

B06 Library Phase, Agent C. Stateless functions taking an XsdbBridge as
dependency injection; each returns a ToolResponse envelope dict built
with mcps/common/tool_response.py (never a hand-written dict).

Tcl command strings come from adapters/xsct.templates (Agent A's shared
contract) so the two agents stay consistent. Agent D's debug_session.py
imports select_target() from this module.

State (connection / selected target / download) is owned by the bridge
and the xsdb shell it wraps; this module keeps no mutable state.
"""
from __future__ import annotations

import re

from mcps.common.tool_response import success
from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.zynq_mcp.domains.ps import (
    extract_bridge_error,
    parse_hex_token,
    parse_state,
    parse_target_properties,
    parse_targets,
    ps_error,
    require_connected,
    require_target_selected,
    safe_eval,
)

__all__ = [
    "connect_hw_server",
    "disconnect_hw_server",
    "list_targets",
    "select_target",
    "get_target_status",
    "get_device_info",
]


async def connect_hw_server(
    bridge: XsdbBridge,
    url: str = "localhost:3121",
) -> dict:
    """Connect to the JTAG hw_server.

    Idempotent: returns already_connected=True when the bridge already
    reports a connection. On success the xsdb shell is connected to
    ``tcp:url``.

    Errors: INVALID_URL, BRIDGE_NOT_READY, HW_SERVER_UNREACHABLE.
    """
    if not isinstance(url, str) or not url.strip():
        return ps_error("INVALID_URL",
                        f"url must be a non-empty string, got {url!r}")
    if not getattr(bridge, "ready", False):
        return ps_error("BRIDGE_NOT_READY",
                        "bridge is not started; start the xsdb process first")
    if bridge.hw_connected:
        return success(data={"status": "connected", "already_connected": True,
                             "url": url}).to_dict()
    result = await safe_eval(bridge,templates.connect(url))
    err = extract_bridge_error(result)
    if err:
        # From the domain's perspective any connect failure is an
        # unreachable hw_server.
        return ps_error(
            "HW_SERVER_UNREACHABLE",
            f"failed to connect to hw_server at tcp:{url}: {err[2]}",
            details={"url": url})
    return success(data={"status": "connected", "already_connected": False,
                         "url": url}).to_dict()


async def disconnect_hw_server(bridge: XsdbBridge) -> dict:
    """Disconnect from the JTAG hw_server.

    Idempotent: returns already_disconnected=True when the bridge already
    reports no connection.

    Errors: BRIDGE_NOT_READY, DISCONNECT_FAILED.
    """
    if not getattr(bridge, "ready", False):
        return ps_error("BRIDGE_NOT_READY", "bridge is not started")
    if not bridge.hw_connected:
        return success(data={"status": "disconnected",
                             "already_disconnected": True}).to_dict()
    result = await safe_eval(bridge,templates.disconnect())
    err = extract_bridge_error(result)
    if err:
        return ps_error("DISCONNECT_FAILED",
                        f"disconnect failed: {err[2]}")
    return success(data={"status": "disconnected",
                         "already_disconnected": False}).to_dict()


async def list_targets(bridge: XsdbBridge) -> dict:
    """List all targets on the JTAG chain.

    data.targets is a list of {"id", "name", "type", "selected"} dicts and
    data.count is the number of targets.

    Errors: NOT_CONNECTED, JTAG_LIST_FAILED, JTAG_EMPTY_CHAIN.
    """
    pre = require_connected(bridge)
    if pre:
        return pre
    result = await safe_eval(bridge,templates.targets())
    err = extract_bridge_error(result)
    if err:
        return ps_error("JTAG_LIST_FAILED",
                        f"failed to list targets: {err[2]}")
    targets = parse_targets(result.get("data", ""))
    if not targets:
        return ps_error("JTAG_EMPTY_CHAIN",
                        "no targets found on the JTAG chain")
    return success(data={"targets": targets, "count": len(targets)}).to_dict()


async def select_target(bridge: XsdbBridge, target_id: int) -> dict:
    """Select a target on the JTAG chain by id (from list_targets).

    Errors: INVALID_TARGET_ID, NOT_CONNECTED, TARGET_NOT_FOUND.
    """
    if isinstance(target_id, bool) or not isinstance(target_id, int):
        return ps_error("INVALID_TARGET_ID",
                        f"target_id must be an integer, got {target_id!r}")
    if target_id < 1:
        return ps_error("INVALID_TARGET_ID",
                        f"target_id must be >= 1, got {target_id}")
    pre = require_connected(bridge)
    if pre:
        return pre
    listing = await list_targets(bridge)
    if listing["status"] != "success":
        return listing
    target = next((t for t in listing["data"]["targets"]
                   if t["id"] == target_id), None)
    if target is None:
        return ps_error(
            "TARGET_NOT_FOUND",
            f"target id {target_id} not found on the JTAG chain",
            details={"target_id": target_id,
                     "available": [t["id"]
                                   for t in listing["data"]["targets"]]})
    result = await safe_eval(bridge,templates.target_select(target_id))
    err = extract_bridge_error(result)
    if err:
        return ps_error("TARGET_NOT_FOUND",
                        f"failed to select target {target_id}: {err[2]}",
                        details={"target_id": target_id})
    return success(data={"selected": {
        "id": target["id"], "name": target["name"], "type": target["type"],
    }}).to_dict()


async def get_target_status(bridge: XsdbBridge) -> dict:
    """Query the current selected target's state.

    data.state: 'running' | 'halted' | 'reset' | 'unknown'.
    data.pc: current PC (hex) when the target is halted and readable.

    Errors: NOT_CONNECTED, NO_TARGET_SELECTED, TARGET_UNRESPONSIVE.
    """
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.get_target_properties(tid))
    err = extract_bridge_error(result)
    if err:
        return ps_error("TARGET_UNRESPONSIVE",
                        f"target did not respond to state query: {err[2]}",
                        details={"target_id": tid})
    state_raw, pc = parse_target_properties(result.get("data", ""))
    state = parse_state(state_raw) if state_raw is not None else "unknown"
    data = {"state": state, "target_id": tid}
    if state == "halted":
        if pc is not None:
            data["pc"] = pc
        else:
            pc_result = await safe_eval(bridge, templates.rrd("pc"))
            pc_err = extract_bridge_error(pc_result)
            if not pc_err:
                parsed_pc = parse_hex_token(pc_result.get("data", ""))
                if parsed_pc:
                    data["pc"] = parsed_pc
    return success(data=data).to_dict()


async def get_device_info(bridge: XsdbBridge) -> dict:
    """Query ARM DAP device info (idcode, irmask, ...).

    Returns the parsed `device properties` key/value pairs.

    Errors: NOT_CONNECTED, DEVICE_INFO_FAILED.
    """
    pre = require_connected(bridge)
    if pre:
        return pre
    result = await safe_eval(bridge,templates.device_info())
    err = extract_bridge_error(result)
    if err:
        return ps_error("DEVICE_INFO_FAILED",
                        f"failed to read device properties: {err[2]}")
    info = _parse_device_properties(result.get("data", ""))
    return success(data=info).to_dict()


def _parse_device_properties(text: str) -> dict:
    """Parse ``targets -target-properties`` output into normalized dict.

    XSDB 2023.1 outputs space-separated key-value pairs.
    Also handles the legacy ``device properties`` key:value format.
    """
    info = {}
    text = (text or "").strip()
    if not text:
        return info
    # XSDB 2023.1 format: space-separated key-value, quoted multi-word values
    import shlex
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    i = 0
    while i + 1 < len(parts):
        key = parts[i]
        val = parts[i + 1]
        # Skip keys that look like standalone values (hex, numeric)
        while i + 1 < len(parts) and (
                parts[i].startswith("0x") or parts[i].isdigit() or
                (parts[i].startswith("{") and not parts[i].startswith('"'))):
            i += 1
        if i + 1 >= len(parts):
            break
        key = parts[i].lower()
        val = parts[i + 1]
        info[key] = val
        i += 2
    # Fallback: classic key:value format
    if not info:
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-\s]+?)\s*[:=]\s*(.+?)\s*$", line)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_").replace("-", "_")
                info.setdefault(key, m.group(2).strip())
    return info
