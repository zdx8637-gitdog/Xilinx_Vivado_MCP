"""observation.py — Observation & Pass/Fail adjudication (B01 §5 Phase 6).

Machine-decidable verdict from UART capture text. The capture lifecycle
(start → wait → stop in domains/ps/uart_capture.py) already produces the full
text; this module is a pure text analysis over that output — no hardware, no
JTAG, no side effects.

Decision rules (B01 §5 Phase 6, FROZEN — markers are now caller-supplied,
B11 phase 2: the B01 GPIO_E2E_* defaults were removed):
  · UART contains pass_marker        → PASS
  · UART contains fail_marker        → FAIL
  · UART timeout (no complete frame) → TIMEOUT
  · UART contains incomplete/partial markers → INCOMPLETE

Verdict precedence: PASS wins over FAIL (a mixed frame is still a pass).
Truncated marker fragments are reported in ``data.partial_markers`` for
diagnostics; the verdict itself is decided on the full markers only.

``bridge`` is accepted for the uniform ps_* calling convention (the
CommandRunner passes the XsdbBridge as the first positional argument) but is
never used: adjudication is pure string analysis over already-captured text.

Error model (fail-closed): invalid arguments return an INVALID_ARGUMENT
error envelope so a malformed call can never be misread as a verdict.
"""
from __future__ import annotations

from mcps.common.tool_response import success, error

__all__ = ["evaluate_observation"]

# Shortest truncated marker fragment that is still recognized as a partial
# frame (B01: "UART contains incomplete/partial markers → INCOMPLETE").
_MIN_PARTIAL_LEN = 8

_VERDICT_PASS = "PASS"
_VERDICT_FAIL = "FAIL"
_VERDICT_TIMEOUT = "TIMEOUT"
_VERDICT_INCOMPLETE = "INCOMPLETE"

_PREVIEW_LEN = 200


def _find_partial_markers(text: str, markers: list[str]) -> list[str]:
    """Return the furthest truncated frame for each marker not fully present.

    For every marker that is NOT fully present in the text, scan its proper
    prefixes (marker[:-1], marker[:-2], ... down to _MIN_PARTIAL_LEN) and
    report the longest one found — e.g. ``TEST_E2E_PAS`` when the PASS frame
    was cut short mid-transmission. Purely diagnostic: the verdict is decided
    on the full markers only.
    """
    partials: list[str] = []
    for marker in markers:
        if not marker or marker in text:
            continue
        longest = None
        for cut in range(1, len(marker)):
            fragment = marker[:-cut]
            if len(fragment) < _MIN_PARTIAL_LEN:
                break
            if fragment in text:
                longest = fragment
                break  # longest match for this marker
        if longest is not None:
            partials.append(longest)
    return partials


async def evaluate_observation(
    bridge=None,
    *,
    uart_text: str,
    pass_marker: str | None = None,
    fail_marker: str | None = None,
) -> dict:
    """Machine-decidable PASS/FAIL from UART capture text (B01 §5 Phase 6).

    Pure text analysis over the output already produced by
    stop_uart_capture / wait_uart_capture — no hardware is touched.

    Decision rules:
    - pass_marker present             → PASS (takes precedence over FAIL)
    - fail_marker present             → FAIL
    - neither present, text blank     → TIMEOUT
    - neither present, text non-blank → INCOMPLETE

    Args:
        bridge: accepted for the uniform ps_* calling convention; unused.
        uart_text: full captured UART output (may be empty — that is TIMEOUT).
        pass_marker: marker that declares a PASS. REQUIRED (no GPIO default;
            B11 phase 2). A missing/empty/non-string value is INVALID_ARGUMENT.
        fail_marker: marker that declares a FAIL. REQUIRED (same rule).

    Returns:
        {"status": "success", "data": {
            "verdict": "PASS" | "FAIL" | "TIMEOUT" | "INCOMPLETE",
            "pass_marker_found": bool,
            "fail_marker_found": bool,
            "partial_markers": [str, ...],
            "text_length": int,
            "text_preview": str (first 200 chars),
        }}

    Errors (stable top-level ErrorCode):
    - INVALID_ARGUMENT: uart_text is not a string, or a marker is not a
      non-empty string.
    """
    if not isinstance(uart_text, str):
        return error(
            f"uart_text must be a string, got {type(uart_text).__name__}",
            code="INVALID_ARGUMENT",
            details={"reason_code": "INVALID_ARGUMENT",
                     "expected": "str"}).to_dict()
    for label, marker in (("pass_marker", pass_marker),
                          ("fail_marker", fail_marker)):
        if not isinstance(marker, str) or not marker:
            return error(
                f"{label} must be a non-empty string, got {marker!r}",
                code="INVALID_ARGUMENT",
                details={"reason_code": "INVALID_ARGUMENT",
                         "field": label}).to_dict()

    pass_found = pass_marker in uart_text
    fail_found = fail_marker in uart_text

    if pass_found:
        verdict = _VERDICT_PASS
    elif fail_found:
        verdict = _VERDICT_FAIL
    elif not uart_text.strip():
        verdict = _VERDICT_TIMEOUT
    else:
        verdict = _VERDICT_INCOMPLETE

    return success(data={
        "verdict": verdict,
        "pass_marker_found": pass_found,
        "fail_marker_found": fail_found,
        "partial_markers": _find_partial_markers(
            uart_text, [pass_marker, fail_marker]),
        "text_length": len(uart_text),
        "text_preview": uart_text[:_PREVIEW_LEN],
    }).to_dict()
