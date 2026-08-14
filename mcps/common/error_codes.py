"""error_codes.py — Unified error classification for all Zynq MCPs."""

from enum import Enum


class ErrorCode(str, Enum):
    # Domain errors (from B01 error classification tree)
    ENV_ERROR = "ENV_ERROR"                # Vivado/XSCT not found, hw_server unreachable
    TOOL_ERROR = "TOOL_ERROR"              # Vivado/Tcl syntax error, XSCT build error
    PLATFORM_ERROR = "PLATFORM_ERROR"      # Address conflict, validate fails
    PL_BUILD_ERROR = "PL_BUILD_ERROR"      # Synthesis error, timing failure
    PS_BUILD_ERROR = "PS_BUILD_ERROR"      # Compile error, link error
    JTAG_ERROR = "JTAG_ERROR"              # DAP not responding, download fails
    UART_ERROR = "UART_ERROR"              # No output, garbled, timeout
    ARTIFACT_STALE = "ARTIFACT_STALE"      # Revision/board profile mismatch
    INTERNAL_ERROR = "INTERNAL_ERROR"      # Unexpected exception, assertion failure

    # Infrastructure errors
    CONTEXT_INVALID = "CONTEXT_INVALID"    # Bad session/board/project
    LOCK_BUSY = "LOCK_BUSY"               # Resource held by another session
    OPERATION_NOT_FOUND = "OPERATION_NOT_FOUND"  # Unknown operation_id
    INVALID_ARGUMENT = "INVALID_ARGUMENT"  # Bad API parameters
