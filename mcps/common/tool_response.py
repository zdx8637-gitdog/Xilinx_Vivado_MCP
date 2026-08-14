"""
tool_response.py — Unified ToolResponse v2 for all Zynq MCPs.

Fail-closed: to_dict() calls validate() and raises on invalid responses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

_VALID_TR_STATUS = {"success", "error"}
_VALID_OP_STATUS = {"accepted", "running", "succeeded", "failed"}


def _is_valid_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


class ToolResponseError(Exception):
    """Raised when an invalid ToolResponse is serialized."""
    pass


@dataclass
class ErrorDetail:
    code: str                  # Must be a valid ErrorCode value
    message: str
    recoverable: bool = False
    details: dict | None = None

    def validate(self) -> list[str]:
        issues = []
        from mcps.common.error_codes import ErrorCode
        valid = {e.value for e in ErrorCode}
        if self.code not in valid:
            issues.append(f"Invalid ErrorDetail.code: '{self.code}'. "
                          f"Must be one of {sorted(valid)}")
        if not self.message:
            issues.append("ErrorDetail.message must not be empty")
        return issues


@dataclass
class ToolResponse:
    """Every tool call returns this envelope.

    Fail-closed: to_dict() calls validate() and raises ToolResponseError on violations.
    """
    status: str                          # "success" | "error"
    request_id: str                      # full UUID
    data: Any = None
    error: ErrorDetail | None = None
    warnings: list[str] = field(default_factory=list)
    context_ref: str | None = None       # session_id

    def validate(self) -> list[str]:
        """Validate this ToolResponse. Returns list of violations (empty = valid)."""
        issues = []

        if not _is_valid_uuid(self.request_id):
            issues.append(f"request_id is not a valid UUID: '{self.request_id}'")

        if self.status not in _VALID_TR_STATUS:
            issues.append(
                f"Invalid status: '{self.status}' (must be success|error)")

        if self.status == "success":
            if self.error is not None:
                issues.append("success response must not have an error")
        elif self.status == "error":
            if self.error is None:
                issues.append("error response must have an error detail")
            else:
                issues.extend(self.error.validate())
            if self.data is not None:
                issues.append("error response must not have data")

        if self.context_ref is not None and not _is_valid_uuid(self.context_ref):
            # Allow session- prefix format
            if not self.context_ref.startswith("session-"):
                issues.append(
                    f"context_ref must be a UUID or session-*: '{self.context_ref}'")

        return issues

    def to_dict(self) -> dict:
        """Serialize to dict. Raises ToolResponseError on validation failure."""
        violations = self.validate()
        if violations:
            raise ToolResponseError(
                f"ToolResponse validation failed: {'; '.join(violations)}")
        d: dict = {"status": self.status, "request_id": self.request_id}
        if self.data is not None:
            d["data"] = self.data
        if self.error is not None:
            d["error"] = asdict(self.error)
        if self.warnings:
            d["warnings"] = self.warnings
        if self.context_ref is not None:
            d["context_ref"] = self.context_ref
        return d


@dataclass
class OperationStatus:
    """Long-running command lifecycle. Separate from ToolResponse."""
    operation_id: str
    status: str   # "accepted" | "running" | "succeeded" | "failed"
    result: dict | None = None
    progress_pct: int | None = None    # 0..100 or None
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> list[str]:
        issues = []
        if self.status not in _VALID_OP_STATUS:
            issues.append(f"Invalid status: '{self.status}' "
                          f"(must be accepted|running|succeeded|failed)")
        if self.progress_pct is not None:
            if not (0 <= self.progress_pct <= 100):
                issues.append(
                    f"progress_pct must be 0..100, got {self.progress_pct}")
        if self.status == "succeeded" and self.result is None:
            issues.append("succeeded OperationStatus must have result")
        if self.status == "failed" and self.result is None:
            issues.append("failed OperationStatus should have result")
        return issues


# ---- Helper constructors ----

def success(data: Any = None, context_ref: str | None = None) -> ToolResponse:
    return ToolResponse(
        status="success",
        request_id=str(uuid.uuid4()),
        data=data,
        context_ref=context_ref,
    )


def error(message: str, code: str, *,
          recoverable: bool = False, details: dict | None = None,
          context_ref: str | None = None) -> ToolResponse:
    from mcps.common.error_codes import ErrorCode
    valid = {e.value for e in ErrorCode}
    if code not in valid:
        raise ValueError(
            f"Unknown error code: {code}. Valid: {sorted(valid)}")
    return ToolResponse(
        status="error",
        request_id=str(uuid.uuid4()),
        error=ErrorDetail(code=code, message=message,
                          recoverable=recoverable, details=details),
        context_ref=context_ref,
    )


def command_accepted(operation_id: str,
                     context_ref: str | None = None) -> ToolResponse:
    return ToolResponse(
        status="success",
        request_id=str(uuid.uuid4()),
        data={"operation_id": operation_id, "status": "accepted"},
        context_ref=context_ref,
    )
