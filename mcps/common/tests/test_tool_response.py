"""T-B02-001: ToolResponse v2 — fail-closed serialization and validation."""

import pytest
from mcps.common.tool_response import (
    success, error, command_accepted,
    ToolResponse, OperationStatus, ErrorDetail, ToolResponseError,
)


# ---- Positive tests ----

def test_success_query_response():
    r = success(data={"version": "2023.1"})
    d = r.to_dict()
    assert d["status"] == "success"
    assert "request_id" in d
    assert len(d["request_id"]) == 36
    assert d["data"] == {"version": "2023.1"}


def test_error_response():
    r = error("Vivado not found", code="ENV_ERROR")
    d = r.to_dict()
    assert d["status"] == "error"
    assert d["error"]["code"] == "ENV_ERROR"
    assert d["error"]["message"] == "Vivado not found"
    assert len(d["request_id"]) == 36


def test_command_response_has_operation_id():
    r = command_accepted(operation_id="op-00000000-0000-0000-0000-000000000001")
    d = r.to_dict()
    assert d["status"] == "success"
    assert d["data"]["operation_id"] == "op-00000000-0000-0000-0000-000000000001"
    assert d["data"]["status"] == "accepted"


def test_operation_status_lifecycle():
    op = OperationStatus(operation_id="op-001", status="accepted")
    assert op.validate() == []
    op.status = "running"
    op.status = "succeeded"
    op.result = success(data={"done": True}).to_dict()
    assert op.validate() == []


# ---- Fail-closed: to_dict() raises on invalid responses ----

def test_success_with_error_raises():
    r = ToolResponse(status="success", request_id=str(__import__('uuid').uuid4()),
                     error=ErrorDetail(code="ENV_ERROR", message="bad"))
    with pytest.raises(ToolResponseError, match="must not have an error"):
        r.to_dict()


def test_error_without_detail_raises():
    r = ToolResponse(status="error", request_id=str(__import__('uuid').uuid4()))
    with pytest.raises(ToolResponseError, match="must have an error"):
        r.to_dict()


def test_invalid_status_raises():
    r = ToolResponse(status="pending", request_id=str(__import__('uuid').uuid4()))
    with pytest.raises(ToolResponseError, match="Invalid status"):
        r.to_dict()


def test_invalid_request_id_raises():
    r = ToolResponse(status="success", request_id="not-a-uuid")
    with pytest.raises(ToolResponseError, match="not a valid UUID"):
        r.to_dict()


def test_empty_request_id_raises():
    r = ToolResponse(status="success", request_id="")
    with pytest.raises(ToolResponseError, match="not a valid UUID"):
        r.to_dict()


def test_error_with_invalid_code_raises():
    r = ToolResponse(status="error", request_id=str(__import__('uuid').uuid4()),
                     error=ErrorDetail(code="NOT_A_CODE", message="x"))
    with pytest.raises(ToolResponseError, match="Invalid ErrorDetail.code"):
        r.to_dict()


def test_error_with_data_raises():
    r = ToolResponse(status="error", request_id=str(__import__('uuid').uuid4()),
                     error=ErrorDetail(code="ENV_ERROR", message="x"),
                     data={"should": "not"})
    with pytest.raises(ToolResponseError, match="must not have data"):
        r.to_dict()


def test_invalid_error_code_rejected_by_helper():
    with pytest.raises(ValueError, match="Unknown error code"):
        error("bad", code="NOT_A_CODE")


# ---- OperationStatus validation ----

def test_progress_pct_out_of_range():
    op = OperationStatus(operation_id="op-1", status="running", progress_pct=150)
    assert len(op.validate()) == 1


def test_progress_pct_negative():
    op = OperationStatus(operation_id="op-1", status="running", progress_pct=-1)
    assert len(op.validate()) == 1


def test_progress_pct_none_is_valid():
    op = OperationStatus(operation_id="op-1", status="running", progress_pct=None)
    assert op.validate() == []


def test_succeeded_needs_result():
    op = OperationStatus(operation_id="op-1", status="succeeded", result=None)
    assert any("must have result" in i for i in op.validate())
