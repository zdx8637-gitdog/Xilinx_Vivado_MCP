"""XSCT/XSDB adapter package (B06 Agent A).

Provides persistent interactive Tcl-shell bridges:
  - XsdbBridge / XsdbBridgeError — XSDB (JTAG) operations
  - XsctBridge / XsctBridgeError — XSCT (software platform) operations
  - templates                    — Tcl command-string templates
  - find_xsdb / find_xsct        — executable resolution helpers
"""

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import (
    XsdbBridge,
    XsdbBridgeError,
    find_xsdb,
)
from mcps.zynq_mcp.adapters.xsct.xsct_bridge import (
    XsctBridge,
    XsctBridgeError,
    find_xsct,
)

__all__ = [
    "XsdbBridge",
    "XsdbBridgeError",
    "XsctBridge",
    "XsctBridgeError",
    "find_xsdb",
    "find_xsct",
]
