"""
context.py — Unified ZynqContext via composition of B02 MCPContext.

Does NOT modify mcps/common/context.py (B02 frozen).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from mcps.common.context import MCPContext

# Workflow stages per B01 §5
STAGE_IDLE = "IDLE"
STAGE_BOARD_VALIDATION = "BOARD_VALIDATION"
STAGE_PLATFORM_DESIGN = "PLATFORM_DESIGN"
STAGE_PL_GENERATE = "PL_GENERATE"
STAGE_PL_BUILD = "PL_BUILD"
STAGE_PL_IMPLEMENT = "PL_IMPLEMENT"
STAGE_PL_TIMING = "PL_TIMING"
STAGE_PL_BITSTREAM = "PL_BITSTREAM"
STAGE_PS_BUILD = "PS_BUILD"
STAGE_CONSISTENCY_CHECK = "CONSISTENCY_CHECK"
STAGE_DEPLOYMENT = "DEPLOYMENT"
STAGE_OBSERVATION = "OBSERVATION"

SERIAL_STAGES = [
    STAGE_IDLE, STAGE_BOARD_VALIDATION, STAGE_PLATFORM_DESIGN,
    STAGE_PL_GENERATE, STAGE_PL_BUILD, STAGE_PL_IMPLEMENT,
    STAGE_PL_TIMING, STAGE_PL_BITSTREAM, STAGE_PS_BUILD,
    STAGE_CONSISTENCY_CHECK, STAGE_DEPLOYMENT, STAGE_OBSERVATION,
]

# Which stages can a given stage ROLLBACK_FIX to.
# B13-M1: PS_BUILD→PL_BUILD 是 P2 真板开发实证的缺口（PS 编译后发现 PL 缺陷，
# 必须合法回 PL 重建）；PL_IMPLEMENT→PL_BUILD 补自然缺口（实现失败重综合）。
ROLLBACK_TARGETS = {
    STAGE_PL_TIMING: [STAGE_PL_BUILD],
    STAGE_PL_BITSTREAM: [STAGE_PL_BUILD],
    STAGE_PL_IMPLEMENT: [STAGE_PL_BUILD],
    STAGE_PS_BUILD: [STAGE_PL_BUILD, STAGE_PS_BUILD],  # retry same + 回 PL 重建
    STAGE_CONSISTENCY_CHECK: [STAGE_PL_BUILD, STAGE_PS_BUILD],
    STAGE_DEPLOYMENT: [STAGE_DEPLOYMENT],
    STAGE_OBSERVATION: [STAGE_DEPLOYMENT],
}


@dataclass
class ZynqContext:
    """Composes B02 MCPContext with Zynq-specific extensions. B02 fields via .base."""
    base: MCPContext

    board_package_revision: str = ""
    board_profile_sha256: str = ""  # E005
    current_stage: str = STAGE_IDLE

    platform_revision: Optional[str] = None
    pl_revision: Optional[str] = None
    ps_revision: Optional[str] = None

    worker_generation: int = 0
    session_closed: bool = False

    @property
    def session_id(self) -> str:
        return self.base.session_id

    @property
    def board_id(self) -> str:
        return self.base.board_id

    @property
    def project_path(self) -> str:
        return self.base.project_path

    def to_dict(self) -> dict:
        return {
            "session_id": self.base.session_id,
            "board_id": self.base.board_id,
            "project_path": self.base.project_path,
            "board_package_revision": self.board_package_revision,
            "board_profile_sha256": self.board_profile_sha256,  # E005
            "current_stage": self.current_stage,
            "platform_revision": self.platform_revision,
            "pl_revision": self.pl_revision,
            "ps_revision": self.ps_revision,
            "worker_generation": self.worker_generation,
        }


def is_valid_forward(current: str, target: str) -> bool:
    """Check if `target` is the natural next stage from `current`."""
    try:
        idx = SERIAL_STAGES.index(current)
        return idx + 1 < len(SERIAL_STAGES) and SERIAL_STAGES[idx + 1] == target
    except ValueError:
        return False


def is_valid_rollback(current: str, target: str) -> bool:
    """Check if `target` is a legal ROLLBACK_FIX target from `current`."""
    allowed = ROLLBACK_TARGETS.get(current, [])
    return target in allowed


def is_valid_retry(current: str, target: str) -> bool:
    """Check if retrying the same stage."""
    return current == target
