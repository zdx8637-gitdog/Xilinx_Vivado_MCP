"""Vivado direct Tcl bridge adapter package.

Provides a persistent interactive `vivado -mode tcl` subprocess bridge
(VivadoTclBridge) that talks to vivado.exe directly through the shared
sentinel-marker pattern (no old-MCP stdio middle layer). Long synthesis /
implementation runs stay alive as long as vivado itself is alive.
"""

from mcps.zynq_mcp.adapters.vivado.vivado_bridge import (
    VivadoTclBridge,
    VivadoBridgeError,
    find_vivado,
)

__all__ = [
    "VivadoTclBridge",
    "VivadoBridgeError",
    "find_vivado",
]
