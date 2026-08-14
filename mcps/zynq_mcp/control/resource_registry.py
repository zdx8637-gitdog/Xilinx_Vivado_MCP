"""Persistent JTAG and UART resource truth for O5.

EDA process ownership remains in :mod:`tool_process_controller`.  This module
owns the connection/capture records that live longer than a single command.
Every public record is written through ``ledger_transaction``; the in-memory
objects below are only live handles and are never accepted as recovery truth.
"""
from __future__ import annotations

import asyncio
import copy
import time
import uuid
from typing import Callable

from mcps.common.tool_response import error, success
from mcps.zynq_mcp.control.execution_ledger import (
    ACTION_WAIT, BACKEND_UART, BACKEND_XSDB,
    HEALTH_ALIVE, HEALTH_NOT_APPLICABLE,
    OBS_COMPLETE, OBS_FAILED, OBS_RUNNING,
    OP_NON_TERMINAL, STATUS_SOURCE_RESOURCE,
    ChannelBusyError, _now_iso, ledger_read_shared, ledger_transaction,
    validate_observation,
)


JTAG_LEASE_TTL_S = 600.0
UART_READ_CHUNK_MS = 100
UART_READER_STOP_TIMEOUT_S = 5.0


def resource_public_view(worker: dict | None) -> dict:
    """Return the public, JSON-safe resource truth stored in the Ledger."""
    worker = worker or {}
    lease = copy.deepcopy(worker.get("jtag_lease"))
    capture = copy.deepcopy(worker.get("uart_capture"))
    owner = copy.deepcopy(worker.get("serial_owner"))
    return {
        "jtag": {
            "lease": lease,
            "held": bool(worker.get("jtag_lease_held")),
            "connected": bool(isinstance(lease, dict) and lease.get("connected")),
            "status": (lease.get("status") if isinstance(lease, dict) else "ABSENT"),
            "owner_session_id": (lease.get("owner_session_id")
                                 if isinstance(lease, dict) else None),
            "worker_pid": worker.get("pid"),
            "worker_generation": worker.get("worker_generation", 0),
            "worker_instance_id": worker.get("instance_id"),
        },
        "uart": {
            "serial_owner": owner,
            "capture": capture,
            "active": bool(isinstance(owner, dict) and owner.get("capture_id")),
        },
    }


def _resource_error(reason_code: str, message: str, *, code="UART_ERROR",
                    details=None) -> dict:
    payload = dict(details or {})
    payload["reason_code"] = reason_code
    return error(message=message, code=code, details=payload).to_dict()


def _resource_observation(current, *, backend: str, step: str,
                          state: str, detail: dict, output: bool = False) -> dict:
    worker = current.worker or {}
    now = _now_iso()
    observation = {
        "status_source": STATUS_SOURCE_RESOURCE,
        "backend": backend,
        "observed_state": state,
        "vendor_status": None,
        "current_step": step,
        "progress_pct": None,
        "worker_health": (HEALTH_ALIVE if backend == BACKEND_XSDB
                          else HEALTH_NOT_APPLICABLE),
        "pid": worker.get("pid") if backend == BACKEND_XSDB else None,
        "process_start_time": (worker.get("process_start_time")
                               if backend == BACKEND_XSDB else None),
        "executable_path": (worker.get("executable_path")
                            if backend == BACKEND_XSDB else None),
        "worker_generation": (worker.get("worker_generation", 0)
                              if backend == BACKEND_XSDB else 0),
        "instance_id": (worker.get("instance_id")
                        if backend == BACKEND_XSDB else None),
        "controller_heartbeat_at": now,
        "observed_at": now,
        "last_output_at": now if output else None,
        "detail": copy.deepcopy(detail),
    }
    validate_observation(observation)
    return observation


class JtagResourceRegistry:
    """Persist one owner-bound JTAG connection and target selection."""

    def __init__(self, guard, ledger_path):
        self._guard = guard
        self._ledger_path = ledger_path

    def record_result(self, operation_id: str, session_id: str,
                      tool_name: str, arguments: dict, result: dict,
                      step: str) -> dict:
        ok = isinstance(result, dict) and result.get("status") == "success"

        def _mutate(current):
            ao = current.active_operation
            if not isinstance(ao, dict) or ao.get("operation_id") != operation_id \
                    or ao.get("status") not in OP_NON_TERMINAL:
                raise ChannelBusyError("OPERATION_NOT_ACTIVE")
            worker = current.worker or {}
            now = _now_iso()
            lease = copy.deepcopy(worker.get("jtag_lease") or {})
            data = result.get("data", {}) if isinstance(result, dict) else {}
            if not isinstance(data, dict):
                data = {}

            if ok and tool_name == "ps_connect_hw_server":
                url = data.get("url") or arguments.get("url") or "localhost:3121"
                if lease.get("lease_id") and lease.get("owner_session_id") != session_id:
                    raise ChannelBusyError("JTAG_OWNER_MISMATCH")
                lease = {
                    "lease_id": (lease.get("lease_id")
                                 if lease.get("connected")
                                 else f"jtag-{uuid.uuid4().hex[:12]}"),
                    "owner_session_id": session_id,
                    "lock_key": str(url),
                    "hw_server_url": str(url),
                    "status": "CONNECTED",
                    "connected": True,
                    "target_id": lease.get("target_id"),
                    "target_name": lease.get("target_name"),
                    "acquired_at": lease.get("acquired_at") or now,
                    "last_observed_at": now,
                    "heartbeat_at": now,
                    "ttl_s": JTAG_LEASE_TTL_S,
                    "worker_generation": worker.get("worker_generation"),
                    "instance_id": worker.get("instance_id"),
                }
                worker["jtag_lease"] = lease
                worker["jtag_lease_held"] = True
            elif ok and tool_name == "ps_disconnect_hw_server":
                if lease:
                    lease.update({"status": "DISCONNECTED", "connected": False,
                                  "last_observed_at": now, "heartbeat_at": now})
                worker["jtag_lease"] = lease or None
                worker["jtag_lease_held"] = False
            elif lease:
                if lease.get("owner_session_id") != session_id:
                    raise ChannelBusyError("JTAG_OWNER_MISMATCH")
                if ok and tool_name == "ps_select_target":
                    selected = data.get("selected") or {}
                    lease["target_id"] = selected.get("id")
                    lease["target_name"] = selected.get("name")
                reason_code = ((result.get("error", {}).get("details", {})
                                .get("reason_code")) if not ok else None)
                connection_unknown = reason_code in {
                    "NOT_CONNECTED", "BRIDGE_NOT_READY",
                    "HW_SERVER_UNREACHABLE", "TARGET_UNRESPONSIVE",
                    "PROCESS_DEAD", "BACKEND_PROCESS_DEAD",
                }
                lease["status"] = (
                    "UNKNOWN" if connection_unknown else "CONNECTED")
                lease["connected"] = True
                lease["last_observed_at"] = now
                lease["heartbeat_at"] = now
                worker["jtag_lease"] = lease
                worker["jtag_lease_held"] = True

            detail = {
                "resource": "JTAG",
                "owner_session_id": session_id,
                "lease_id": lease.get("lease_id") if lease else None,
                "connection_status": lease.get("status") if lease else (
                    "DISCONNECTED" if tool_name == "ps_disconnect_hw_server" else "ABSENT"),
                "target_id": lease.get("target_id") if lease else None,
                "reason_code": ((result.get("error", {}).get("details", {})
                                 .get("reason_code")) if not ok else None),
            }
            ao["observation"] = _resource_observation(
                current, backend=BACKEND_XSDB, step=step,
                state=OBS_COMPLETE if ok else OBS_FAILED,
                detail=detail, output=True)
            ao["recommended_action"] = ACTION_WAIT
            ao["updated_at"] = now
            current.worker = worker
            current.active_operation = ao
            return current

        return ledger_transaction(self._guard, self._ledger_path, _mutate).worker


class UartResourceFacade:
    """Per-operation facade injected into the three UART capture tools."""

    def __init__(self, registry, session_id: str, operation_id: str):
        self._registry = registry
        self._session_id = session_id
        self._operation_id = operation_id

    async def start_uart_capture(self, *, port=None, baudrate=115200):
        return await self._registry.start(
            self._session_id, self._operation_id, port, baudrate)

    async def wait_uart_capture(self, *, capture_id=None, markers=None,
                                timeout_s=15.0):
        return await self._registry.wait(
            self._session_id, self._operation_id, capture_id, markers, timeout_s)

    async def stop_uart_capture(self, *, capture_id=None):
        return await self._registry.stop(
            self._session_id, self._operation_id, capture_id)


class UartResourceRegistry:
    """Live serial handles plus a Ledger-backed capture registry."""

    def __init__(self, guard, ledger_path, *, serial_factory: Callable | None = None):
        self._guard = guard
        self._ledger_path = ledger_path
        self._serial_factory = serial_factory
        self._captures: dict[str, dict] = {}

    def facade(self, session_id: str, operation_id: str) -> UartResourceFacade:
        return UartResourceFacade(self, session_id, operation_id)

    def record_ephemeral_result(self, session_id: str, operation_id: str,
                                tool_name: str, arguments: dict,
                                result: dict) -> None:
        """Persist real UART evidence for one-shot read/write/list commands."""
        ok = isinstance(result, dict) and result.get("status") == "success"
        data = result.get("data", {}) if isinstance(result, dict) else {}
        if not isinstance(data, dict):
            data = {}
        error_details = ((result.get("error") or {}).get("details") or {}) \
            if isinstance(result, dict) else {}
        step = {
            "ps_read_uart": "UART_READ",
            "ps_write_uart": "UART_WRITE",
            "ps_list_serial_ports": "UART_ENUMERATE",
        }.get(tool_name, "UART_ACCESS")
        detail = {
            "resource": "UART",
            "owner_session_id": session_id,
            "port": arguments.get("port"),
            "baudrate": arguments.get("baudrate"),
            "bytes_read": data.get("bytes_read", data.get("bytes_received")),
            "bytes_written": data.get("bytes_written"),
            "port_count": (len(data.get("ports"))
                           if isinstance(data.get("ports"), list) else None),
            "reason_code": error_details.get("reason_code") if not ok else None,
        }

        def _mutate(current):
            ao = current.active_operation
            if not isinstance(ao, dict) or ao.get("operation_id") != operation_id \
                    or ao.get("status") not in OP_NON_TERMINAL:
                raise ChannelBusyError("OPERATION_NOT_ACTIVE")
            ao["observation"] = _resource_observation(
                current, backend=BACKEND_UART, step=step,
                state=OBS_COMPLETE if ok else OBS_FAILED,
                detail=detail,
                output=bool(detail["bytes_read"] or detail["bytes_written"]
                            or detail["port_count"]))
            ao["recommended_action"] = ACTION_WAIT
            ao["updated_at"] = _now_iso()
            current.active_operation = ao
            return current

        ledger_transaction(self._guard, self._ledger_path, _mutate)

    def _new_adapter(self):
        if self._serial_factory is not None:
            return self._serial_factory()
        from mcps.zynq_mcp.adapters.uart import SerialAdapter
        return SerialAdapter()

    def _read_record(self) -> dict:
        ledger, _ = ledger_read_shared(self._guard, self._ledger_path)
        return copy.deepcopy((ledger.worker or {}).get("uart_capture") or {})

    def _persist(self, session_id: str, operation_id: str, record: dict,
                 *, step: str, state: str, clear_owner: bool = False,
                 output: bool = False) -> None:
        def _mutate(current):
            ao = current.active_operation
            if not isinstance(ao, dict) or ao.get("operation_id") != operation_id \
                    or ao.get("status") not in OP_NON_TERMINAL:
                raise ChannelBusyError("OPERATION_NOT_ACTIVE")
            worker = current.worker or {}
            owner = worker.get("serial_owner")
            if owner and isinstance(owner, dict) and \
                    owner.get("session_id") != session_id:
                raise ChannelBusyError("UART_OWNER_MISMATCH")
            worker["uart_capture"] = copy.deepcopy(record)
            worker["serial_owner"] = None if clear_owner else {
                "session_id": session_id,
                "capture_id": record["capture_id"],
                "port": record["port"],
            }
            detail = {
                "resource": "UART",
                "capture_id": record["capture_id"],
                "owner_session_id": session_id,
                "port": record["port"],
                "baudrate": record["baudrate"],
                "capture_status": record["status"],
                "last_rx_at": record.get("last_rx_at"),
                "bytes_received": record.get("bytes_received", 0),
                "markers_found": list(record.get("markers_found") or []),
                "deadline_at": record.get("deadline_at"),
            }
            ao["observation"] = _resource_observation(
                current, backend=BACKEND_UART, step=step, state=state,
                detail=detail, output=output)
            ao["recommended_action"] = ACTION_WAIT
            ao["updated_at"] = _now_iso()
            current.worker = worker
            current.active_operation = ao
            return current
        ledger_transaction(self._guard, self._ledger_path, _mutate)

    def _persist_background(self, record: dict, *, clear_owner=False) -> None:
        """Persist reader truth without requiring an active command."""
        def _mutate(current):
            worker = current.worker or {}
            owner = worker.get("serial_owner")
            if not isinstance(owner, dict) or \
                    owner.get("capture_id") != record.get("capture_id"):
                raise ChannelBusyError("UART_OWNER_MISMATCH")
            worker["uart_capture"] = copy.deepcopy(record)
            if clear_owner:
                worker["serial_owner"] = None
            current.worker = worker
            return current
        ledger_transaction(self._guard, self._ledger_path, _mutate)

    async def _reader(self, capture: dict) -> None:
        from mcps.zynq_mcp.adapters.uart import SerialAdapterError
        while not capture["stop_event"].is_set():
            try:
                chunk = await asyncio.to_thread(
                    capture["adapter"].read, UART_READ_CHUNK_MS)
            except SerialAdapterError as exc:
                capture["record"]["status"] = "DISCONNECTED"
                capture["record"]["reason_code"] = "UART_DISCONNECTED"
                capture["record"]["finished_at"] = _now_iso()
                try:
                    self._persist_background(capture["record"], clear_owner=True)
                finally:
                    await asyncio.to_thread(capture["adapter"].close)
                return
            if chunk:
                capture["buffer"].extend(chunk)
                record = capture["record"]
                record["bytes_received"] = len(capture["buffer"])
                record["last_rx_at"] = _now_iso()
                self._persist_background(record)

    async def start(self, session_id: str, operation_id: str,
                    port, baudrate=115200) -> dict:
        if not isinstance(port, str) or not port.strip():
            return _resource_error("INVALID_ARGUMENT", "port must be non-empty",
                                   code="INVALID_ARGUMENT")
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
            return _resource_error("INVALID_ARGUMENT", "baudrate must be positive",
                                   code="INVALID_ARGUMENT")
        adapter = self._new_adapter()
        try:
            await asyncio.to_thread(adapter.open, port, baudrate)
        except Exception as exc:
            return _resource_error("SERIAL_OPEN_FAILED", f"open {port}: {exc}")
        capture_id = f"uart-{uuid.uuid4().hex[:12]}"
        record = {
            "capture_id": capture_id, "session_id": session_id,
            "port": port, "baudrate": baudrate, "status": "RUNNING",
            "started_at": _now_iso(), "last_rx_at": None,
            "bytes_received": 0, "markers_found": [],
            "deadline_at": None, "finished_at": None,
            "instance_id": getattr(self._guard, "instance_id", None),
        }
        capture = {"record": record, "adapter": adapter,
                   "buffer": bytearray(), "stop_event": asyncio.Event(),
                   "reader_task": None}
        try:
            self._persist(session_id, operation_id, record,
                          step="UART_CAPTURE_START", state=OBS_RUNNING)
            capture["reader_task"] = asyncio.create_task(self._reader(capture))
            self._captures[capture_id] = capture
        except BaseException:
            await asyncio.to_thread(adapter.close)
            raise
        return success({"capture_id": capture_id, "port": port,
                        "baudrate": baudrate, "status": "started"}).to_dict()

    def _owned_capture(self, session_id: str, capture_id) -> tuple[dict | None, dict | None]:
        if not isinstance(capture_id, str) or not capture_id:
            return None, _resource_error("INVALID_CAPTURE_ID", "capture_id is invalid")
        record = self._read_record()
        if not record or record.get("capture_id") != capture_id:
            return None, _resource_error("INVALID_CAPTURE_ID", "capture is not in Ledger")
        if record.get("session_id") != session_id:
            return None, _resource_error("UART_OWNER_MISMATCH", "capture has another owner")
        capture = self._captures.get(capture_id)
        if capture is None:
            return None, _resource_error("UART_CAPTURE_NOT_LIVE",
                                         "Ledger capture has no live serial handle")
        return capture, None

    async def wait(self, session_id: str, operation_id: str, capture_id,
                   markers, timeout_s=15.0) -> dict:
        capture, problem = self._owned_capture(session_id, capture_id)
        if problem:
            return problem
        if not isinstance(markers, list) or not markers or not all(
                isinstance(item, str) and item for item in markers):
            return _resource_error("INVALID_MARKERS", "markers must be non-empty strings")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
                or timeout_s <= 0:
            return _resource_error("INVALID_TIMEOUT", "timeout_s must be positive")
        record = capture["record"]
        record["deadline_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() + timeout_s))
        deadline = time.monotonic() + float(timeout_s)
        while True:
            if record.get("status") == "DISCONNECTED":
                self._persist(session_id, operation_id, record,
                              step="UART_CAPTURE_DISCONNECTED", state=OBS_FAILED,
                              clear_owner=True)
                return _resource_error("UART_DISCONNECTED", "UART port disconnected",
                                       details={"capture_id": capture_id})
            text = bytes(capture["buffer"]).decode("utf-8", errors="replace").replace("\x00", "")
            found = [marker for marker in markers if marker in text]
            record["markers_found"] = found
            record["bytes_received"] = len(capture["buffer"])
            if len(found) == len(markers):
                record["status"] = "MATCHED"
                record["finished_at"] = _now_iso()
                self._persist(session_id, operation_id, record,
                              step="UART_MARKER_MATCH", state=OBS_COMPLETE,
                              output=True)
                return success({"status": "matched", "matched": found,
                                "capture_id": capture_id, "partial_text": text,
                                "bytes_received": record["bytes_received"],
                                "last_rx_at": record.get("last_rx_at")}).to_dict()
            if time.monotonic() >= deadline:
                record["status"] = "PARTIAL" if found else "TIMEOUT"
                record["finished_at"] = _now_iso()
                self._persist(session_id, operation_id, record,
                              step="UART_CAPTURE_TIMEOUT", state=OBS_FAILED,
                              output=bool(record["bytes_received"]))
                return success({"status": "partial" if found else "timeout",
                                "matched": found,
                                "missing": [m for m in markers if m not in found],
                                "capture_id": capture_id, "partial_text": text,
                                "bytes_received": record["bytes_received"],
                                "last_rx_at": record.get("last_rx_at")}).to_dict()
            self._persist(session_id, operation_id, record,
                          step="UART_CAPTURE_WAIT", state=OBS_RUNNING,
                          output=bool(record["bytes_received"]))
            await asyncio.sleep(0.05)

    async def stop(self, session_id: str, operation_id: str, capture_id) -> dict:
        capture, problem = self._owned_capture(session_id, capture_id)
        if problem:
            return problem
        capture["stop_event"].set()
        task = capture.get("reader_task")
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, UART_READER_STOP_TIMEOUT_S)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await asyncio.to_thread(capture["adapter"].close)
        text = bytes(capture["buffer"]).decode("utf-8", errors="replace").replace("\x00", "")
        record = capture["record"]
        record.update({"status": "STOPPED", "finished_at": _now_iso(),
                       "bytes_received": len(capture["buffer"])})
        self._persist(session_id, operation_id, record,
                      step="UART_CAPTURE_STOP", state=OBS_COMPLETE,
                      clear_owner=True, output=bool(record["bytes_received"]))
        self._captures.pop(capture_id, None)
        return success({"capture_id": capture_id, "text": text,
                        "char_count": len(text), "bytes_received": record["bytes_received"],
                        "last_rx_at": record.get("last_rx_at"),
                        "markers_found": list(record.get("markers_found") or []),
                        "stopped": True}).to_dict()

    async def shutdown_all(self, *, status="INTERRUPTED") -> bool:
        ok = True
        for capture_id, capture in list(self._captures.items()):
            capture["stop_event"].set()
            task = capture.get("reader_task")
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            try:
                await asyncio.to_thread(capture["adapter"].close)
            except Exception:
                ok = False
            record = capture["record"]
            record.update({"status": status, "finished_at": _now_iso(),
                           "reason_code": "MCP_SHUTDOWN"})
            try:
                self._persist_background(record, clear_owner=True)
            except Exception:
                ok = False
            self._captures.pop(capture_id, None)
        return ok
