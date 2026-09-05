"""memory_access.py — memory and register access (4 APIs).

B06 Library Phase, Agent C. Stateless functions taking an XsdbBridge;
each returns a ToolResponse envelope dict (mcps/common/tool_response.py).

Tcl command strings come from adapters/xsct.templates (Agent A's shared
contract).
"""
from __future__ import annotations

import re

from mcps.common.tool_response import success
from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.zynq_mcp.domains.ps import (
    extract_bridge_error,
    ps_error,
    require_connected,
    require_target_selected,
    safe_eval,
)

__all__ = [
    "reg_read",
    "reg_write",
    "mem_read",
    "mem_write",
]

# ARM Cortex-A9 (ARMv7) registers: r0-r15 plus the standard aliases.
_VALID_REGISTERS = {f"r{i}" for i in range(16)} | {
    "sp", "lr", "pc", "cpsr"}
_HEX_STR_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
_HEX_TOKEN_RE = re.compile(r"0[xX][0-9a-fA-F]+")
_HEX_DIGITS = "0123456789abcdefABCDEF"
_WORD_MASK = 0xFFFFFFFF


async def reg_read(bridge: XsdbBridge, register: str) -> dict:
    """Read a CPU register (xsdb rrd).

    data.value is a hex string like '0x...'.

    Errors: INVALID_REGISTER, NOT_CONNECTED, NO_TARGET_SELECTED,
    REG_READ_FAILED.
    """
    norm = _normalize_register(register)
    if norm is None:
        return ps_error("INVALID_REGISTER",
                        f"invalid register: {register!r} "
                        f"(expected r0-r15/sp/lr/pc/cpsr)",
                        details={"register": register})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.rrd(norm))
    err = extract_bridge_error(result)
    if err:
        return ps_error("REG_READ_FAILED",
                        f"failed to read register {norm}: {err[2]}",
                        details={"register": norm})
    value = _extract_reg_value(result.get("data", ""), norm)
    if value is None:
        return ps_error("REG_READ_FAILED",
                        f"could not parse value for register {norm} from "
                        "xsdb output",
                        details={"register": norm,
                                 "output": result.get("data")})
    return success(data={"register": norm, "value": value,
                         "target_id": tid}).to_dict()


async def reg_write(
    bridge: XsdbBridge,
    register: str,
    value: int | str,
) -> dict:
    """Write a CPU register (xsdb rwr).

    value: int or hex string '0x...'.

    Errors: INVALID_REGISTER, INVALID_VALUE, NOT_CONNECTED,
    NO_TARGET_SELECTED, REG_WRITE_FAILED.
    """
    norm = _normalize_register(register)
    if norm is None:
        return ps_error("INVALID_REGISTER",
                        f"invalid register: {register!r} "
                        f"(expected r0-r15/sp/lr/pc/cpsr)",
                        details={"register": register})
    value_hex = _normalize_reg_value(value)
    if value_hex is None:
        return ps_error("INVALID_VALUE",
                        f"invalid register value: {value!r} "
                        "(expected int or hex string)",
                        details={"register": norm, "value": value})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.rwr(norm, value_hex))
    err = extract_bridge_error(result)
    if err:
        return ps_error("REG_WRITE_FAILED",
                        f"failed to write register {norm}: {err[2]}",
                        details={"register": norm})
    return success(data={"register": norm, "value": value_hex,
                         "target_id": tid}).to_dict()


async def mem_read(
    bridge: XsdbBridge,
    address: int | str,
    length: int = 4,
) -> dict:
    """Read memory (xsdb mrd).

    address: physical address (int or '0x...' string).
    length: number of words to read (1 word = 4 bytes).

    data.words is a list of hex strings; data.address the canonical hex.

    Errors: INVALID_ADDRESS, INVALID_LENGTH, NOT_CONNECTED,
    NO_TARGET_SELECTED, MEM_READ_FAILED, MEM_READ_NO_DATA (mrd 静默空输出
    时 fail-closed——地址可能未入内存映射，先 ps_load_hardware 或 xsdb 对账).
    """
    addr = _normalize_address(address)
    if addr is None:
        return ps_error("INVALID_ADDRESS",
                        f"invalid address: {address!r} "
                        "(expected int or '0x...' string)",
                        details={"address": address})
    if isinstance(length, bool) or not isinstance(length, int) or length < 1:
        return ps_error("INVALID_LENGTH",
                        f"length must be a positive integer (words), "
                        f"got {length!r}",
                        details={"length": length})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.mrd(addr, length))
    err = extract_bridge_error(result)
    if err:
        return ps_error("MEM_READ_FAILED",
                        f"memory read failed: {err[2]}",
                        details={"address": addr, "length": length})
    words = _parse_mrd_words(result.get("data", ""))
    # B13-F8 修复轮#8 (黑盒实证): mrd 对"未加入内存映射/被阻断"的地址会
    # 静默返回空输出——此前 mem_read 报 success+words=[] (fail-open)，
    # 黑盒被空结果误导多轮。空数据必须 fail-closed，并给出可操作提示
    # （先 ps_load_hardware / 与 xsdb 手动 mrd 对账）。
    if not words:
        return ps_error("MEM_READ_NO_DATA",
                        "mrd returned no data — the address may be outside "
                        "the memory map (run ps_load_hardware first) or the "
                        "access is blocked; cross-check with xsdb "
                        f"'mrd {addr} {length}'",
                        details={"address": addr, "length": length,
                                 "raw": (result.get("data") or "")[:200]})
    return success(data={"address": addr, "length": length, "words": words,
                         "target_id": tid}).to_dict()


async def mem_write(
    bridge: XsdbBridge,
    address: int | str,
    data: int | list[int] | bytes,
) -> dict:
    """Write memory (xsdb mwr).

    data: a single word, a list of words, or bytes (converted to
    little-endian words).

    Errors: INVALID_ADDRESS, INVALID_DATA, NOT_CONNECTED,
    NO_TARGET_SELECTED, MEM_WRITE_FAILED.
    """
    addr = _normalize_address(address)
    if addr is None:
        return ps_error("INVALID_ADDRESS",
                        f"invalid address: {address!r} "
                        "(expected int or '0x...' string)",
                        details={"address": address})
    words = _normalize_write_data(data)
    if words is None:
        return ps_error("INVALID_DATA",
                        f"invalid data: {data!r} "
                        "(expected int, list[int], or bytes)",
                        details={"address": addr})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    value_str = " ".join(f"0x{w:X}" for w in words)
    result = await safe_eval(bridge,templates.mwr(addr, f"{{{value_str}}}"))
    err = extract_bridge_error(result)
    if err:
        return ps_error("MEM_WRITE_FAILED",
                        f"memory write failed: {err[2]}",
                        details={"address": addr})
    return success(data={"address": addr,
                         "written": [f"0x{w:X}" for w in words],
                         "target_id": tid}).to_dict()


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_register(register: str) -> str | None:
    if not isinstance(register, str):
        return None
    norm = register.strip().lower()
    return norm if norm in _VALID_REGISTERS else None


def _normalize_reg_value(value: int | str) -> str | None:
    """Normalize a register value to a hex string, or None if invalid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"0x{value & _WORD_MASK:08X}"
    if isinstance(value, str):
        s = value.strip()
        if _HEX_STR_RE.match(s):
            return s
    return None


def _normalize_address(address: int | str) -> str | None:
    """Normalize an address to a canonical hex string, or None if invalid."""
    if isinstance(address, bool):
        return None
    if isinstance(address, int):
        if address < 0:
            return None
        return f"0x{address:X}"
    if isinstance(address, str):
        s = address.strip()
        if _HEX_STR_RE.match(s):
            return s
    return None


def _normalize_write_data(data) -> list[int] | None:
    """Normalize mem_write data to a list of word ints, or None if invalid."""
    if isinstance(data, bool):
        return None
    if isinstance(data, int):
        if data < 0:
            return None
        return [data]
    if isinstance(data, list):
        words = []
        for v in data:
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                return None
            words.append(v)
        return words
    if isinstance(data, bytes):
        words = []
        for i in range(0, len(data), 4):
            chunk = data[i:i + 4]
            words.append(int.from_bytes(chunk, "little"))
        return words
    return None


def _extract_reg_value(data: str, register: str) -> str | None:
    """Extract the hex value for `register` from an rrd output blob.

    Real XSDB prints register values as bare hex (``pc: ffffff28``), not
    just ``0x``-prefixed, so both forms are accepted and the returned value
    is normalized to the 0x-prefixed form.
    """
    low_reg = register.lower()
    for line in (data or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(rf"^{re.escape(low_reg)}\b(?:\s*[:=]\s*|\s+)(.+?)\s*$",
                     line, re.IGNORECASE)
        if not m:
            continue
        value = _normalize_hex_value(m.group(1))
        if value is not None:
            return value
    # Fallback: output that is exactly one hex value.
    for line in (data or "").splitlines():
        line = line.strip()
        value = _normalize_hex_value(line)
        if value is not None:
            return value
    return None


def _normalize_hex_value(token: str) -> str | None:
    """Normalize a register-value token to a 0x-prefixed hex string.

    Accepts ``0x...``, bare hex (``ffffff28``), or a token whose first
    whitespace-delimited word is bare hex. Returns None when no hex value
    is present.
    """
    token = (token or "").strip()
    if _HEX_STR_RE.fullmatch(token):
        return token
    first = token.split(None, 1)[0] if token else ""
    if first and all(c in _HEX_DIGITS for c in first):
        return "0x" + first
    m = _HEX_TOKEN_RE.search(token)
    if m:
        return m.group(0)
    return None


# B13-F8 修复轮#8 (真板实证): xsdb mrd 的地址/字输出**不带 0x 前缀**
# （如 "E000102C:   0000000A"）——原 _HEX_TOKEN_RE（要求 0x）永远匹配
# 不到真实字，words 恒空（黑盒 ps_mem_read 空 words 的根因，主代理真板
# 复现：raw 有数据、解析为空）。
_MRD_WORD_RE = re.compile(r"(?:0[xX])?[0-9a-fA-F]+")


def _parse_mrd_words(data: str) -> list[str]:
    """Parse `mrd` output into a list of canonical hex word strings.

    mrd prints '<address>: <word1> <word2> ...' per line; everything
    before the first ':' is the address, the rest are words. Words are
    accepted with or without the 0x prefix (real xsdb omits it) and
    canonicalized to ``0x%08X``.
    """
    words = []
    for line in (data or "").splitlines():
        if ":" not in line:
            continue
        _, _, after = line.partition(":")
        for m in _MRD_WORD_RE.finditer(after):
            try:
                words.append(f"0x{int(m.group(0), 16):08X}")
            except ValueError:
                continue
    return words
