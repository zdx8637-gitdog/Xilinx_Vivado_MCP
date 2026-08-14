"""uart_capture.py — UART capture lifecycle (B01 §5 Phase 5 model).

The capture window is opened *before* the CPU executes so no output is
lost. The caller starts a capture, deploys the program, waits for expected
markers, and then stops the capture to get the full text:

    capture_id = start_uart_capture(port, baud)          # open window
    ps.download(elf) → ps.run()                          # CPU executes
    result = wait_uart_capture(capture_id, markers, timeout=15s)
    full_text = stop_uart_capture(capture_id)            # close window

Implementation notes:
- Uses SerialAdapter internally (synchronous pyserial). The capture is
  persistent across MCP calls — the open serial port and accumulated
  buffer live in the module-level ``_captures`` dict keyed by capture_id.
- ``bridge`` is accepted for the uniform ps_* calling convention
  (the CommandRunner passes the XsdbBridge as the first positional
  argument) but is not used: UART observation is an independent serial
  port and never touches the JTAG shell.
- pyserial is imported lazily (inside the functions), so a missing
  pyserial never crashes the server import — same convention as the
  ps_read_uart / ps_write_uart wrappers.
- The background reader is an asyncio task on the server's event loop.
  SerialAdapter.read() is blocking, so it is driven with
  asyncio.to_thread(). The capture buffer is only mutated in the
  event-loop thread (the reader task's continuation) and only read from
  the event-loop thread (wait/stop), so the bytearray needs no extra
  locking; the module-level dict is single-thread accessed via the
  CommandRunner mutex above.

Error model: every error envelope carries a stable top-level ErrorCode
(default ``UART_ERROR`` from mcps/common/error_codes.py) with the
fine-grained reason in ``error.details.reason_code``, matching the
existing ps_read_uart / ps_write_uart wrappers.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from mcps.common.tool_response import error, success

logger = logging.getLogger("zynq_mcp.ps.uart_capture")

__all__ = ["start_uart_capture", "wait_uart_capture", "stop_uart_capture"]

# Compatibility-only capture registry. The formal server injects a
# UartResourceFacade and persists production truth in the Execution Ledger.
_captures: dict[str, dict] = {}

_READ_CHUNK_MS = 100      # background reader window per SerialAdapter.read() call
_WAIT_POLL_S = 0.05       # wait_uart_capture polling interval
_READER_STOP_TIMEOUT_S = 5.0  # bounded join for the reader in stop_uart_capture


def _uart_error(reason_code: str, message: str,
                details: dict | None = None, *,
                code: str = "UART_ERROR") -> dict:
    """Fail-closed UART error envelope with a stable top-level ErrorCode."""
    extra = dict(details or {})
    extra["reason_code"] = reason_code
    return error(message=message, code=code, details=extra).to_dict()


def _capture_text(capture: dict) -> str:
    """Decode the capture buffer as UTF-8 (lossy) for marker matching."""
    return bytes(capture["buffer"]).decode("utf-8", errors="replace")


async def _read_loop(capture: dict) -> None:
    """Background reader: continuously accumulate serial data.

    Runs until ``stop_event`` is set or the port read fails. A read failure
    is recorded on the capture (fail-closed) and ends the loop — the caller
    then sees an explicit error rather than a silent stall. SerialAdapter.read
    is time-bounded, so each iteration returns within _READ_CHUNK_MS.
    """
    from mcps.zynq_mcp.adapters.uart import SerialAdapterError
    adapter = capture["adapter"]
    try:
        while not capture["stop_event"].is_set():
            try:
                chunk = await asyncio.to_thread(adapter.read, _READ_CHUNK_MS)
            except SerialAdapterError as exc:
                capture["error"] = str(exc)
                logger.warning("uart capture %s read error: %s",
                               capture["capture_id"], exc)
                break
            if chunk:
                capture["buffer"].extend(chunk)
    except asyncio.CancelledError:
        raise


def _resolve_capture(capture_id):
    if not isinstance(capture_id, str) or not capture_id:
        return None
    return _captures.get(capture_id)


# ── public UART capture API ────────────────────────────────────────────────────

async def start_uart_capture(bridge, *, port=None, baudrate=115200) -> dict:
    """Open a UART capture window before the CPU executes.

    Opens the serial port and starts a background asyncio task that
    continuously reads into the capture buffer. Returns ``data.capture_id``
    which must be passed to wait_uart_capture() / stop_uart_capture().

    Errors:
    - INVALID_ARGUMENT: port empty / baudrate not a positive integer
    - UART_ERROR/SERIAL_OPEN_FAILED: serial port open failed
    - INTERNAL_ERROR: background reader could not be started
    """
    # O5 formal server path: ``bridge`` is a UartResourceFacade.  The legacy
    # in-module capture below remains only for historical component tests;
    # production resource truth is always persisted by the facade.
    if bridge is not None and hasattr(bridge, "start_uart_capture"):
        return await bridge.start_uart_capture(port=port, baudrate=baudrate)

    if not isinstance(port, str) or not port.strip():
        return _uart_error("INVALID_ARGUMENT", "port must be a non-empty string",
                           code="INVALID_ARGUMENT")
    if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
        return _uart_error("INVALID_ARGUMENT",
                           "baudrate must be a positive integer",
                           code="INVALID_ARGUMENT")

    from mcps.zynq_mcp.adapters.uart import SerialAdapter, SerialAdapterError
    adapter = SerialAdapter()
    try:
        adapter.open(port, baudrate)
    except SerialAdapterError as exc:
        return _uart_error("SERIAL_OPEN_FAILED", f"open {port}: {exc}")

    capture_id = f"uart-{uuid.uuid4().hex[:8]}"
    capture = {
        "capture_id": capture_id,
        "port": port,
        "baudrate": baudrate,
        "adapter": adapter,
        "buffer": bytearray(),
        "reader_task": None,
        "stop_event": asyncio.Event(),
        "error": None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
    }
    try:
        task = asyncio.get_running_loop().create_task(_read_loop(capture))
    except Exception as exc:  # pragma: no cover - loop teardown race
        try:
            adapter.close()
        except Exception as close_exc:
            logger.warning("start_uart_capture best-effort close failed: %s",
                           close_exc)
        return _uart_error("INTERNAL_ERROR", f"start reader: {exc}",
                           code="INTERNAL_ERROR")
    capture["reader_task"] = task
    _captures[capture_id] = capture
    return success(data={
        "capture_id": capture_id,
        "port": port,
        "baudrate": baudrate,
        "status": "started",
    }).to_dict()


async def wait_uart_capture(bridge, *, capture_id=None, markers=None,
                            timeout_s=15.0) -> dict:
    """Wait until all markers appear in the captured output, or timeout.

    Returns data.status: "matched" | "partial" | "timeout" plus
    data.matched (the markers seen so far) and data.partial_text (the
    current captured text). A reader failure is surfaced as an explicit
    error (fail-closed) instead of a false match.

    Errors:
    - UART_ERROR/INVALID_CAPTURE_ID: unknown capture_id
    - UART_ERROR/INVALID_MARKERS: markers not a non-empty list of strings
    - UART_ERROR/INVALID_TIMEOUT: timeout_s not a positive number
    - UART_ERROR/SERIAL_READ_FAILED: the capture reader has failed
    """
    if bridge is not None and hasattr(bridge, "wait_uart_capture"):
        return await bridge.wait_uart_capture(
            capture_id=capture_id, markers=markers, timeout_s=timeout_s)

    capture = _resolve_capture(capture_id)
    if capture is None:
        return _uart_error("INVALID_CAPTURE_ID",
                           f"Unknown capture_id: {capture_id!r}",
                           {"capture_id": capture_id})
    if (not isinstance(markers, list) or not markers
            or not all(isinstance(m, str) and m for m in markers)):
        return _uart_error(
            "INVALID_MARKERS",
            "markers must be a non-empty list of non-empty strings",
            {"capture_id": capture_id})
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
            or timeout_s <= 0:
        return _uart_error("INVALID_TIMEOUT",
                           "timeout_s must be a positive number",
                           {"capture_id": capture_id})

    deadline = time.monotonic() + timeout_s
    while True:
        if capture.get("error"):
            return _uart_error(
                "SERIAL_READ_FAILED",
                f"capture {capture_id} reader failed: {capture['error']}",
                {"capture_id": capture_id,
                 "partial_text": _capture_text(capture)})
        text = _capture_text(capture)
        matched = [m for m in markers if m in text]
        if len(matched) == len(markers):
            return success(data={
                "status": "matched",
                "matched": matched,
                "capture_id": capture_id,
                "partial_text": text,
            }).to_dict()
        if time.monotonic() >= deadline:
            return success(data={
                "status": "partial" if matched else "timeout",
                "matched": matched,
                "missing": [m for m in markers if m not in matched],
                "capture_id": capture_id,
                "partial_text": text,
            }).to_dict()
        await asyncio.sleep(_WAIT_POLL_S)


async def stop_uart_capture(bridge, *, capture_id=None) -> dict:
    """Close a capture and return the full accumulated text.

    Stops the background reader (bounded join), closes the serial port,
    removes the capture from the registry and returns all captured data
    as text.

    Errors:
    - UART_ERROR/INVALID_CAPTURE_ID: unknown capture_id (idempotent stop
      of an already-stopped capture reports the same error)
    """
    if bridge is not None and hasattr(bridge, "stop_uart_capture"):
        return await bridge.stop_uart_capture(capture_id=capture_id)

    capture = _resolve_capture(capture_id)
    if capture is None:
        return _uart_error("INVALID_CAPTURE_ID",
                           f"Unknown capture_id: {capture_id!r}",
                           {"capture_id": capture_id})

    capture["stop_event"].set()
    task = capture.get("reader_task")
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=_READER_STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("uart capture %s reader did not stop; cancelling",
                           capture_id)
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                # The reader is wedged; the port close below unblocks it
                # (a closed port makes the next read fail and the loop exit).
                pass
        except Exception as exc:
            # The reader crashed with an unexpected exception; record it and
            # continue cleanup (fail-closed: never leave the port open).
            capture["error"] = str(exc)
            logger.warning("uart capture %s reader exited with exception: %s",
                           capture_id, exc)

    adapter = capture["adapter"]
    try:
        adapter.close()
    except Exception as exc:
        logger.warning("stop_uart_capture best-effort close failed: %s", exc)

    text = _capture_text(capture)
    # xil_printf on Zynq PS UART uses 32-bit writes (Xil_Out32) to an 8-bit
    # TX FIFO, producing \x00 padding between each character.  Remove null
    # bytes so marker matching (evaluate_observation) works correctly.
    text = text.replace("\x00", "")
    _captures.pop(capture_id, None)
    return success(data={
        "capture_id": capture_id,
        "text": text,
        "char_count": len(text),
        "stopped": True,
    }).to_dict()
