"""T-B02-002: Error code completeness."""

from mcps.common.error_codes import ErrorCode


def test_all_error_codes_defined():
    required = {
        "ENV_ERROR", "TOOL_ERROR", "PLATFORM_ERROR", "PL_BUILD_ERROR",
        "PS_BUILD_ERROR", "JTAG_ERROR", "UART_ERROR", "ARTIFACT_STALE",
        "INTERNAL_ERROR",
        "CONTEXT_INVALID", "LOCK_BUSY",
        "OPERATION_NOT_FOUND", "INVALID_ARGUMENT",
    }
    defined = {e.value for e in ErrorCode}
    assert required.issubset(defined)


def test_error_codes_unique():
    values = [e.value for e in ErrorCode]
    assert len(values) == len(set(values))


def test_error_codes_count():
    assert len(list(ErrorCode)) == 13
