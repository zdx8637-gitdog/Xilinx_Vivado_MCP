"""uart — synchronous serial port adapter for PS UART observation.

Exposes SerialAdapter (physical serial I/O) and SerialAdapterError.
High-level baud matching / data parsing lives in domains/ps/.
"""
from mcps.zynq_mcp.adapters.uart.serial_adapter import (
    SerialAdapter,
    SerialAdapterError,
)

__all__ = ["SerialAdapter", "SerialAdapterError"]
