"""test_uart_capture.py — unit tests for the B01 §5 Phase 5 UART capture
lifecycle (domains/ps/uart_capture.py).

No hardware required: SerialAdapter is monkeypatched (in the
adapters.uart package namespace, which the production functions import
lazily) with a deterministic fake. The fake's read() runs in a worker
thread (asyncio.to_thread), mimicking the blocking pyserial read, and the
test feeds bytes through it before/while wait_uart_capture polls — so the
marker-matched / timeout / partial outcomes are deterministic without fixed
sleeps.

Coverage (production entries, evidence level IMPLEMENTED_AND_TESTED at the
unit level):
  - start_uart_capture: capture_id format, distinct ids, arg validation,
    serial open failure path
  - wait_uart_capture: matched / timeout / partial, arg validation, invalid
    capture_id, reader-failure fail-closed
  - stop_uart_capture: full-text return, port close, invalid capture_id
  - registration: the 3 tools are wired into capabilities.py (ALL_TOOLS)
    and dispatcher.py (_PS_TOOL_MAP / _ALL_KNOWN)
"""
from __future__ import annotations

import re
import threading
import time

import pytest
import pytest_asyncio

from mcps.zynq_mcp.adapters.uart import SerialAdapterError
from mcps.zynq_mcp.domains.ps import uart_capture

pytestmark = pytest.mark.asyncio(loop_scope="function")

_CAPTURE_ID_RE = re.compile(r"^uart-[0-9a-f]{8}$")


class _FakeSerialAdapter:
    """Deterministic SerialAdapter stand-in.

    feed() queues bytes; read() drains them under a lock (the read happens
    in a worker thread via asyncio.to_thread, so the lock keeps feed/read
    race-free). A short sleep mimics the blocking nature of pyserial and
    throttles the reader loop.
    """

    instances: list = []

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self.is_open_flag = False
        self.open_calls = 0
        self.close_calls = 0
        self.last_port = None
        self.last_baudrate = None
        _FakeSerialAdapter.instances.append(self)

    def open(self, port, baudrate=115200, timeout=0.1):
        if self.is_open_flag:
            raise SerialAdapterError("Port already open, close first")
        self.last_port = port
        self.last_baudrate = baudrate
        self.is_open_flag = True
        self.open_calls += 1

    def close(self):
        self.is_open_flag = False
        self.close_calls += 1

    @property
    def is_open(self):
        return self.is_open_flag

    def read(self, duration_ms=5000):
        if not self.is_open_flag:
            raise SerialAdapterError("Serial port not open")
        time.sleep(0.02)  # mimic the blocking read; throttles the reader loop
        with self._lock:
            data = b"".join(self._queue)
            self._queue.clear()
        return data

    def feed(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self._lock:
            self._queue.append(data)


class _FailingSerialAdapter(_FakeSerialAdapter):
    """read() always fails — exercises the reader fail-closed path."""

    def read(self, duration_ms=5000):
        raise SerialAdapterError("simulated read failure")


@pytest.fixture
def fake_serial(monkeypatch):
    """Monkeypatch adapters.uart.SerialAdapter with a controllable fake.

    Returns a callable: fake_serial() (default) or fake_serial(SomeClass).
    """
    import mcps.zynq_mcp.adapters.uart as uart_adapter_pkg

    def _patch(cls=_FakeSerialAdapter):
        monkeypatch.setattr(uart_adapter_pkg, "SerialAdapter", cls)
        return cls

    _patch()
    return _patch


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeSerialAdapter.instances.clear()
    yield
    _FakeSerialAdapter.instances.clear()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_captures():
    """Stop any capture left running so no background task outlives a test.

    pytest_asyncio.fixture is required: the repo runs pytest-asyncio in
    strict mode, where a plain async @pytest.fixture is rejected.
    """
    yield
    for cid in list(uart_capture._captures):
        try:
            await uart_capture.stop_uart_capture(None, capture_id=cid)
        except Exception as exc:  # best-effort cleanup; never mask the test result
            uart_capture.logger.warning("cleanup of capture %s failed: %s", cid, exc)


# ── start_uart_capture ─────────────────────────────────────────────────────────

class TestStart:

    async def test_start_returns_capture_id_format(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5", baudrate=115200)
        assert r["status"] == "success"
        cid = r["data"]["capture_id"]
        assert _CAPTURE_ID_RE.match(cid), f"unexpected capture_id: {cid!r}"
        assert r["data"]["port"] == "COM5"
        assert r["data"]["baudrate"] == 115200
        assert r["data"]["status"] == "started"
        assert cid in uart_capture._captures
        adapter = _FakeSerialAdapter.instances[-1]
        assert adapter.last_port == "COM5"
        assert adapter.last_baudrate == 115200
        assert adapter.is_open is True

    async def test_start_twice_distinct_ids(self, fake_serial):
        r1 = await uart_capture.start_uart_capture(None, port="COM5")
        r2 = await uart_capture.start_uart_capture(None, port="COM5")
        assert r1["status"] == "success"
        assert r2["status"] == "success"
        assert r1["data"]["capture_id"] != r2["data"]["capture_id"]
        assert len(uart_capture._captures) == 2

    async def test_start_invalid_port(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="   ")
        assert r["status"] == "error"
        assert r["error"]["code"] == "INVALID_ARGUMENT"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert uart_capture._captures == {}
        assert _FakeSerialAdapter.instances == []

    async def test_start_invalid_baudrate(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5", baudrate=0)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert uart_capture._captures == {}

    async def test_start_open_failure_is_fail_closed(self, fake_serial):
        class _PortBusyAdapter(_FakeSerialAdapter):
            def open(self, port, baudrate=115200, timeout=0.1):
                raise SerialAdapterError("access denied")

        fake_serial(_PortBusyAdapter)
        r = await uart_capture.start_uart_capture(None, port="COM5")
        assert r["status"] == "error"
        assert r["error"]["code"] == "UART_ERROR"
        assert r["error"]["details"]["reason_code"] == "SERIAL_OPEN_FAILED"
        assert uart_capture._captures == {}


# ── wait_uart_capture ─────────────────────────────────────────────────────────

class TestWait:

    async def test_wait_timeout(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["PS_UART_PASS"], timeout_s=0.3)
        assert w["status"] == "success"
        assert w["data"]["status"] == "timeout"
        assert w["data"]["matched"] == []
        assert w["data"]["missing"] == ["PS_UART_PASS"]
        assert w["data"]["capture_id"] == cid

    async def test_wait_matched(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        _FakeSerialAdapter.instances[-1].feed("hello PS_UART_PASS world\n")
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["PS_UART_PASS"], timeout_s=3.0)
        assert w["status"] == "success"
        assert w["data"]["status"] == "matched"
        assert w["data"]["matched"] == ["PS_UART_PASS"]
        assert "PS_UART_PASS" in w["data"]["partial_text"]

    async def test_wait_multiple_markers_all_required(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        _FakeSerialAdapter.instances[-1].feed("A_MARKER then B_MARKER\n")
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid,
            markers=["A_MARKER", "B_MARKER"], timeout_s=3.0)
        assert w["data"]["status"] == "matched"
        assert set(w["data"]["matched"]) == {"A_MARKER", "B_MARKER"}

    async def test_wait_partial(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        _FakeSerialAdapter.instances[-1].feed("only A_MARKER here\n")
        # First wait until A_MARKER is definitely in the buffer (deterministic),
        # then wait for both with a short deadline → partial, not timeout.
        w1 = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["A_MARKER"], timeout_s=3.0)
        assert w1["data"]["status"] == "matched"
        w2 = await uart_capture.wait_uart_capture(
            None, capture_id=cid,
            markers=["A_MARKER", "B_MARKER"], timeout_s=0.3)
        assert w2["data"]["status"] == "partial"
        assert w2["data"]["matched"] == ["A_MARKER"]
        assert w2["data"]["missing"] == ["B_MARKER"]

    async def test_wait_invalid_capture_id(self):
        r = await uart_capture.wait_uart_capture(
            None, capture_id="uart-nope", markers=["X"], timeout_s=1.0)
        assert r["status"] == "error"
        assert r["error"]["code"] == "UART_ERROR"
        assert r["error"]["details"]["reason_code"] == "INVALID_CAPTURE_ID"

    async def test_wait_invalid_markers(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers="notalist", timeout_s=1.0)
        assert w["status"] == "error"
        assert w["error"]["details"]["reason_code"] == "INVALID_MARKERS"

    async def test_wait_empty_markers(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=[], timeout_s=1.0)
        assert w["status"] == "error"
        assert w["error"]["details"]["reason_code"] == "INVALID_MARKERS"

    async def test_wait_invalid_timeout(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["X"], timeout_s=-1)
        assert w["status"] == "error"
        assert w["error"]["details"]["reason_code"] == "INVALID_TIMEOUT"

    async def test_wait_reader_error_is_fail_closed(self, fake_serial):
        fake_serial(_FailingSerialAdapter)
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["X"], timeout_s=3.0)
        assert w["status"] == "error"
        assert w["error"]["code"] == "UART_ERROR"
        assert w["error"]["details"]["reason_code"] == "SERIAL_READ_FAILED"


# ── stop_uart_capture ─────────────────────────────────────────────────────────

class TestStop:

    async def test_stop_returns_full_text(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        payload = "line1\nline2\nPS_UART_PASS\n"
        _FakeSerialAdapter.instances[-1].feed(payload)
        # Wait until matched: guarantees the payload reached the buffer before
        # we snapshot it via stop (deterministic, no fixed sleeps).
        w = await uart_capture.wait_uart_capture(
            None, capture_id=cid, markers=["PS_UART_PASS"], timeout_s=3.0)
        assert w["data"]["status"] == "matched"
        s = await uart_capture.stop_uart_capture(None, capture_id=cid)
        assert s["status"] == "success"
        assert s["data"]["text"] == payload
        assert s["data"]["char_count"] == len(payload)
        assert s["data"]["stopped"] is True
        assert cid not in uart_capture._captures

    async def test_stop_closes_port(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        adapter = _FakeSerialAdapter.instances[-1]
        assert adapter.is_open is True
        s = await uart_capture.stop_uart_capture(None, capture_id=cid)
        assert s["status"] == "success"
        assert adapter.is_open is False
        assert adapter.close_calls >= 1
        assert cid not in uart_capture._captures

    async def test_stop_invalid_capture_id(self):
        r = await uart_capture.stop_uart_capture(None, capture_id="uart-nonexistent")
        assert r["status"] == "error"
        assert r["error"]["code"] == "UART_ERROR"
        assert r["error"]["details"]["reason_code"] == "INVALID_CAPTURE_ID"
        assert "capture_id" in r["error"]["details"]

    async def test_stop_twice_is_fail_closed(self, fake_serial):
        r = await uart_capture.start_uart_capture(None, port="COM5")
        cid = r["data"]["capture_id"]
        s1 = await uart_capture.stop_uart_capture(None, capture_id=cid)
        assert s1["status"] == "success"
        s2 = await uart_capture.stop_uart_capture(None, capture_id=cid)
        assert s2["status"] == "error"
        assert s2["error"]["details"]["reason_code"] == "INVALID_CAPTURE_ID"


# ── registration (production sources) ─────────────────────────────────────────

class TestRegistration:

    async def test_uart_capture_tools_registered(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        from mcps.zynq_mcp.dispatcher import _ALL_KNOWN, _PS_TOOL_MAP

        names = {t.name for t in ALL_TOOLS}
        for n in ("ps_start_uart_capture", "ps_wait_uart_capture",
                  "ps_stop_uart_capture"):
            assert n in names, f"{n} missing from capabilities ALL_TOOLS"
            assert n in _PS_TOOL_MAP, f"{n} missing from dispatcher _PS_TOOL_MAP"
            assert n in _ALL_KNOWN, f"{n} missing from dispatcher _ALL_KNOWN"

        assert _PS_TOOL_MAP["ps_start_uart_capture"] == \
            (uart_capture, "start_uart_capture")
        assert _PS_TOOL_MAP["ps_wait_uart_capture"] == \
            (uart_capture, "wait_uart_capture")
        assert _PS_TOOL_MAP["ps_stop_uart_capture"] == \
            (uart_capture, "stop_uart_capture")
