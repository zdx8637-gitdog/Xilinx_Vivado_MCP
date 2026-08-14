"""uart_diagnostics.py — UART diagnostics (B01 §5 Phase 7 diagnosis cascade).

When the UART produces no output, the root cause is often a baud-rate
mismatch. This module runs the Phase 7 diagnosis sequence against the
currently selected target:

    1. rrd PC                         — is the CPU stuck in an abort handler?
    2. rrd CPSR                       — verify the CPU mode
    3. mrd 0xF8000154                 — SLCR UART_CLK_CTRL (ref-clock divisor)
    4. mrd 0xE0001000 + 0x18          — UART1 BAUDGEN (CD)
    5. mrd 0xE0001000 + 0x34          — UART1 BAUDDIV (BDIV)

It then computes the actual baud rate and compares it to the expected
value. Operational state (halt) management and target status queries are
left to the caller — the caller must halt the target before calling
(precondition, not enforced here; a running CPU returns stale values).

Baud-rate math (Zynq-7000 TRM UG585):

    UART_REF_CLK = IO_PLL / (UART_CLK_CTRL[DIVISOR0] + 1)
        SLCR UART_CLK_CTRL @ 0xF8000154
        IO_PLL = 1000 MHz (typical; the default Zynq-7020 UART clock comes
        from IO_PLL — SRCSEL bits [4:2] select the source, and only IO_PLL
        is modeled here)
        DIVISOR0 = bits [20:8] of UART_CLK_CTRL (default 10 -> /11)

    Actual Baud = UART_REF_CLK / (CD * BDIV)
        CD   = UART1 BAUDGEN @ 0xE0001018, bits [15:0]
        BDIV = UART1 BAUDDIV @ 0xE0001034, bits [7:0]

    Example: DIVISOR0=10, CD=49, BDIV=16 -> 90.9 MHz / (49*16) ≈ 115,956
    baud (~0.66 % above 115200).

Fail-closed: when any read fails the diagnosis cannot be completed, so a
status=error envelope is returned with the underlying reason_code
preserved (e.g. REG_READ_FAILED / MEM_READ_FAILED -> JTAG_ERROR) and
``details.diagnosis = "reg_read_failed"``.
"""
from __future__ import annotations

from mcps.common.tool_response import error, success
from mcps.zynq_mcp.domains.ps import (
    memory_access,
    ps_error,
    reason_of,
)

__all__ = ["diagnose_uart_clock"]

# Zynq-7020 SLCR / UART register addresses (UG585).
_IO_PLL_HZ = 1_000_000_000          # IO_PLL default 1000 MHz
_SLCR_UART_CLK_CTRL = 0xF8000154    # SLCR UART clock control
_UART1_BASE = 0xE0001000            # UART1 register base
_UART1_BAUDGEN = _UART1_BASE + 0x18  # UART1 BAUDGEN (CD), bits [15:0]
_UART1_BAUDDIV = _UART1_BASE + 0x34  # UART1 BAUDDIV (BDIV), bits [7:0]

# UART receivers tolerate a few percent of clock error; 3 % is the
# conventional bound for an 8N1 frame.
_BAUD_TOLERANCE_PCT = 3.0


async def diagnose_uart_clock(bridge, expected_baud: int = 115200) -> dict:
    """Read SLCR + UART baud registers from the currently selected target.

    Precondition: the target is halted (the caller halts before calling).

    Reads PC/CPSR via reg_read and the SLCR UART_CLK_CTRL + UART1
    BAUDGEN/BAUDDIV registers via mem_read, then computes the actual UART
    baud rate and compares it to ``expected_baud``.

    Returns (success)::

        {"status": "success", "data": {
            "pc": "0x...", "cpsr": "0x...",
            "slcr_uart_clk_ctrl": "0x...",
            "uart1_baudgen": N, "uart1_bauddiv": N,
            "computed_baud": N, "expected_baud": 115200,
            "baud_match": bool, "baud_error_pct": float,
            "diagnosis": "baud_ok" | "baud_mismatch"}}

    Fail-closed: a failed read returns status=error with the underlying
    reason_code preserved and ``details.diagnosis = "reg_read_failed"``;
    a zero baud divider (BAUDGEN*BAUDDIV == 0) returns UART_ERROR with
    ``details.diagnosis = "baud_unconfigured"``; an invalid expected_baud
    returns INVALID_ARGUMENT.
    """
    if isinstance(expected_baud, bool) or not isinstance(expected_baud, int) \
            or expected_baud <= 0:
        return error(message="expected_baud must be a positive integer",
                     code="INVALID_ARGUMENT",
                     details={"reason_code": "INVALID_ARGUMENT",
                              "expected_baud": expected_baud}).to_dict()

    pc, err = await _read_reg_hex(bridge, "pc")
    if err:
        return _read_failed(err, "pc")
    cpsr, err = await _read_reg_hex(bridge, "cpsr")
    if err:
        return _read_failed(err, "cpsr")
    slcr_clk_ctrl, err = await _read_mem_word(bridge, _SLCR_UART_CLK_CTRL,
                                              "slcr_uart_clk_ctrl")
    if err:
        return _read_failed(err, "slcr_uart_clk_ctrl")
    cd, err = await _read_mem_word(bridge, _UART1_BAUDGEN, "uart1_baudgen")
    if err:
        return _read_failed(err, "uart1_baudgen")
    bdiv, err = await _read_mem_word(bridge, _UART1_BAUDDIV, "uart1_bauddiv")
    if err:
        return _read_failed(err, "uart1_bauddiv")

    divisor0 = (slcr_clk_ctrl >> 8) & 0x1FFF
    uart_ref = _IO_PLL_HZ // (divisor0 + 1)
    divider = cd * bdiv
    if divider <= 0:
        return error(message="UART baud divider is zero "
                             "(BAUDGEN * BAUDDIV = 0); the baud generator "
                             "is not configured",
                     code="UART_ERROR",
                     details={"reason_code": "UART_BAUD_UNCONFIGURED",
                              "diagnosis": "baud_unconfigured",
                              "uart1_baudgen": cd,
                              "uart1_bauddiv": bdiv}).to_dict()

    computed_baud = uart_ref // divider
    baud_error_pct = round(
        abs(computed_baud - expected_baud) / expected_baud * 100.0, 3)
    baud_match = baud_error_pct <= _BAUD_TOLERANCE_PCT
    return success(data={
        "pc": pc,
        "cpsr": cpsr,
        "slcr_uart_clk_ctrl": f"0x{slcr_clk_ctrl:X}",
        "uart1_baudgen": cd,
        "uart1_bauddiv": bdiv,
        "computed_baud": computed_baud,
        "expected_baud": expected_baud,
        "baud_match": baud_match,
        "baud_error_pct": baud_error_pct,
        "diagnosis": "baud_ok" if baud_match else "baud_mismatch",
    }).to_dict()


# ── helpers ───────────────────────────────────────────────────────────────────

async def _read_reg_hex(bridge, register: str):
    """Read a CPU register; return (hex_value, None) or (None, error env)."""
    resp = await memory_access.reg_read(bridge, register)
    if resp.get("status") != "success":
        return None, resp
    value = resp.get("data", {}).get("value")
    if value is None:
        return None, ps_error("REG_READ_FAILED",
                              f"no value returned for register {register}",
                              details={"register": register})
    return value, None


async def _read_mem_word(bridge, address: int, name: str):
    """Read a single 32-bit memory word; return (int, None) or (None, env)."""
    resp = await memory_access.mem_read(bridge, address, length=1)
    if resp.get("status") != "success":
        return None, resp
    words = resp.get("data", {}).get("words") or []
    if not words:
        return None, ps_error("MEM_READ_FAILED",
                              f"no word returned for {name}",
                              details={"address": f"0x{address:X}"})
    return int(words[0], 16), None


def _read_failed(underlying, step: str) -> dict:
    """Wrap a failed sub-read into a fail-closed diagnosis error.

    The underlying envelope's reason_code is preserved so the top-level
    code keeps its canonical mapping (e.g. MEM_READ_FAILED -> JTAG_ERROR).
    """
    reason = reason_of(underlying) or "REG_READ_FAILED"
    return ps_error(reason,
                    f"UART clock diagnosis incomplete: could not read {step} "
                    f"({reason})",
                    details={"diagnosis": "reg_read_failed",
                             "failed_read": step})
