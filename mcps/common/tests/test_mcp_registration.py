"""T-B02-R → B04 updated: MCP registration — .mcp.json raw config validation + SDK startup.

B04 unified the 4 legacy MCP servers (vivado, zynq_platform, zynq_pl, zynq_ps)
into a single mcps.zynq_mcp.server. The .mcp.json is deliberately empty — MCP
is loaded externally via MCP SDK by Agent scripts (B08+).

Legacy test → replacement mapping:
  test_vivado_entry_unchanged            → test_mcp_config_is_empty
  test_registration_entry_exists[zynq_platform] → test_legacy_entries_removed[zynq_platform]
  test_registration_entry_exists[zynq_pl]       → test_legacy_entries_removed[zynq_pl]
  test_registration_entry_exists[zynq_ps]       → test_legacy_entries_removed[zynq_ps]
  test_platform_starts_from_raw_config  → (covered by zynq_mcp 1171-test suite)
  test_pl_starts_from_raw_config        → (covered by zynq_mcp 1171-test suite)
  test_ps_starts_from_raw_config        → (covered by zynq_mcp 1171-test suite)
"""

import asyncio, json, os, shutil, sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert os.path.isdir(os.path.join(PROJECT_ROOT, "mcps"))

TIMEOUT = 30


def _load_mcp_config():
    path = os.path.join(PROJECT_ROOT, ".mcp.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["mcpServers"]


# ===========================================================================
# Static config checks (B04/B08 updated)
# ===========================================================================

# B04 merged the 4 legacy servers. B08 external-loads via MCP SDK.
# .mcp.json is deliberately empty so Claude Code does not auto-start them.
_LEGACY_MCP_NAMES = ["vivado", "zynq_platform", "zynq_pl", "zynq_ps"]


def test_mcp_config_is_empty():
    """B04/B08: .mcp.json mcpServers is deliberately empty."""
    cfg = _load_mcp_config()
    assert cfg == {}, f"Expected empty mcpServers, got: {list(cfg.keys())}"


@pytest.mark.parametrize("name", _LEGACY_MCP_NAMES)
def test_legacy_entries_removed(name):
    """B04: legacy MCP servers must not appear in .mcp.json."""
    cfg = _load_mcp_config()
    assert name not in cfg, (
        f"Legacy server '{name}' should not be in .mcp.json — "
        f"B04 merged all into mcps.zynq_mcp.server, loaded externally"
    )


def test_python_on_path():
    """Document what 'python' resolves to. Not a pass/fail gate — but if
    'python' is not on PATH, the SDK tests below will fail."""
    resolved = shutil.which("python")
    print(f"\n    'python' resolves to: {resolved}")
    assert resolved is not None, \
        "'python' not found on PATH — MCP registration will fail to start"


# ===========================================================================
# SDK ClientSession startup — unified zynq_mcp server (replaces platform/pl/ps)
# ===========================================================================

@pytest.mark.asyncio
async def test_unified_zynq_mcp_starts():
    """B04: unified zynq_mcp server starts from raw python -m mcps.zynq_mcp.server.

    This replaces the 3 legacy SDK tests:
      - test_platform_starts_from_raw_config (zynq_platform, 5 tools, 0 implemented)
      - test_pl_starts_from_raw_config     (zynq_pl, 5 tools, 0 implemented)
      - test_ps_starts_from_raw_config     (zynq_ps, 5 tools, 0 implemented)

    The unified server exposes 101 tools (92 implemented) across all domains.
    """
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcps.zynq_mcp.server"],
    )
    async with asyncio.timeout(TIMEOUT):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = await s.list_tools()
                # 101 tools — control 9 + platform 15 + pl 26 + ps 47 + verification 4
                assert len(tools.tools) >= 90, (
                    f"Expected >= 90 tools on unified server, got {len(tools.tools)}"
                )
