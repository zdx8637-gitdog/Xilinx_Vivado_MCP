"""
timeout_config.py — Configurable timeouts with min/max bounds.

Priority: code defaults → ZYNQ_TIMEOUT_* env vars → session param (cannot exceed max).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        f = float(v)
    except (ValueError, TypeError):
        return default
    return max(lo, min(hi, f))


@dataclass
class TimeoutConfig:
    # wait timeout
    wait_default_s: float = 30.0
    wait_min_s: float = 5.0
    wait_max_s: float = 300.0

    # heartbeat
    heartbeat_interval_s: float = 30.0
    heartbeat_timeout_s: float = 60.0
    heartbeat_min_s: float = 10.0
    heartbeat_max_s: float = 120.0

    # operation deadlines
    deadline_synth_s: float = 1800.0
    deadline_pnr_s: float = 3600.0
    deadline_bitstream_s: float = 600.0
    deadline_program_s: float = 900.0
    deadline_default_s: float = 600.0
    deadline_min_s: float = 60.0
    deadline_max_s: float = 86400.0

    # cleanup
    cleanup_timeout_s: float = 30.0
    cleanup_min_s: float = 10.0
    cleanup_max_s: float = 120.0

    # Vivado tool call
    tool_call_default_s: float = 30.0
    tool_call_max_s: float = 3600.0

    _frozen: bool = field(default=False, repr=False)


def load_timeout_config(session_overrides: dict | None = None) -> TimeoutConfig:
    """
    Build a frozen config from defaults → env → session overrides.

    Session overrides cannot exceed env/safety max bounds.
    """
    c = TimeoutConfig(
        wait_default_s=_env_float("ZYNQ_TIMEOUT_WAIT_DEFAULT_S", 30.0, 5.0, 300.0),
        heartbeat_interval_s=_env_float("ZYNQ_TIMEOUT_HEARTBEAT_INTERVAL_S", 30.0, 10.0, 120.0),
        heartbeat_timeout_s=_env_float("ZYNQ_TIMEOUT_HEARTBEAT_TIMEOUT_S", 60.0, 10.0, 120.0),
        deadline_synth_s=_env_float("ZYNQ_TIMEOUT_DEADLINE_SYNTH_S", 1800.0, 60.0, 86400.0),
        deadline_pnr_s=_env_float("ZYNQ_TIMEOUT_DEADLINE_PNR_S", 3600.0, 60.0, 86400.0),
        deadline_bitstream_s=_env_float("ZYNQ_TIMEOUT_DEADLINE_BITSTREAM_S", 600.0, 60.0, 86400.0),
        deadline_program_s=_env_float("ZYNQ_TIMEOUT_DEADLINE_PROGRAM_S", 900.0, 60.0, 86400.0),
        deadline_default_s=_env_float("ZYNQ_TIMEOUT_DEADLINE_DEFAULT_S", 600.0, 60.0, 86400.0),
        cleanup_timeout_s=_env_float("ZYNQ_TIMEOUT_CLEANUP_S", 30.0, 10.0, 120.0),
        tool_call_default_s=_env_float("ZYNQ_TIMEOUT_TOOL_CALL_DEFAULT_S", 30.0, 1.0, 3600.0),
    )
    if session_overrides:
        for k, v in session_overrides.items():
            if hasattr(c, k) and isinstance(v, (int, float)):
                # Session cannot exceed class-level max
                hi = getattr(type(c), f"{k[0:-2]}_max_s", None) if k.endswith("_s") else None
                lo = getattr(type(c), f"{k[0:-2]}_min_s", None) if k.endswith("_s") else None
                if lo is not None and hi is not None:
                    setattr(c, k, max(lo, min(hi, float(v))))
                else:
                    setattr(c, k, float(v))
    c._frozen = True
    return c


def deadline_for_tool(tool_name: str, config: TimeoutConfig) -> float:
    """Return the operation deadline for a given tool."""
    tool = tool_name.lower()
    if "synthesize" in tool:
        return config.deadline_synth_s
    if "place" in tool or "route" in tool:
        return config.deadline_pnr_s
    if "bitstream" in tool:
        return config.deadline_bitstream_s
    if "program" in tool:
        return config.deadline_program_s
    return config.deadline_default_s
