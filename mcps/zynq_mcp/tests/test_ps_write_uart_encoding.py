"""F-11 (fix round #12): ps_write_uart binary downlink channel.

The contract requires binary UART downlink frames (A5 5A | CMD | LEN |
payload | CRC16-LE) whose payload bytes are not valid text. ps_write_uart
now accepts encoding="hex": the data string is hex-decoded (whitespace
tolerated) and written as raw bytes. The legacy text path (encoding omitted
or "utf-8") is unchanged. No hardware required: the SerialAdapter is
monkeypatched with a recording fake (same pattern as test_uart_capture.py).
"""
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="function")

from mcps.zynq_mcp.adapters.uart import SerialAdapterError
import mcps.zynq_mcp.adapters.uart as uart_adapter_pkg
from mcps.zynq_mcp import dispatcher


class _FakeSerialAdapter:
    instances = []

    def __init__(self):
        self.is_open = False
        self.last_port = None
        self.last_baudrate = None
        self.written = b""
        self.closed = False
        _FakeSerialAdapter.instances.append(self)

    def open(self, port, baudrate=115200, timeout=0.1):
        self.is_open = True
        self.last_port = port
        self.last_baudrate = baudrate

    def write(self, data):
        if not self.is_open:
            raise SerialAdapterError("not open")
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.written += data
        return len(data)

    def close(self):
        self.closed = True
        self.is_open = False


class _FailingOpenAdapter(_FakeSerialAdapter):
    def open(self, port, baudrate=115200, timeout=0.1):
        raise SerialAdapterError("access denied")


@pytest.fixture
def fake_serial(monkeypatch):
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


class TestHexChannel:

    async def test_hex_valid_writes_raw_bytes(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="a55a 01 85 000f4240a5d132", encoding="hex")
        assert r["status"] == "success"
        assert r["data"]["bytes_written"] == 11
        assert r["data"]["port"] == "COM5"
        adapter = _FakeSerialAdapter.instances[-1]
        assert adapter.written == bytes.fromhex("a55a0185000f4240a5d132")
        assert adapter.closed is True

    async def test_hex_with_newlines_tolerated(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="A5\n5A 05 00 B4\t09", encoding="hex")
        assert r["status"] == "success"
        assert _FakeSerialAdapter.instances[-1].written == bytes.fromhex("a55a0500b409")

    async def test_hex_odd_length_fail_closed(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="a55a1", encoding="hex")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert _FakeSerialAdapter.instances == []

    async def test_hex_non_hex_chars_fail_closed(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="a55z", encoding="hex")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert _FakeSerialAdapter.instances == []

    async def test_hex_requires_str(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data=b"\xa5\x5a", encoding="hex")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"

    async def test_unknown_encoding_fail_closed(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="hello", encoding="base64")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"


class TestLegacyTextPath:

    async def test_default_utf8_unchanged(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="B13 CMD START\r\n")
        assert r["status"] == "success"
        assert r["data"]["bytes_written"] == len("B13 CMD START\r\n".encode("utf-8"))
        assert _FakeSerialAdapter.instances[-1].written == b"B13 CMD START\r\n"

    async def test_explicit_utf8_same_as_default(self, fake_serial):
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="ping", encoding="utf-8")
        assert r["status"] == "success"
        assert _FakeSerialAdapter.instances[-1].written == b"ping"

    async def test_open_failure_still_fail_closed(self, fake_serial):
        fake_serial(_FailingOpenAdapter)
        r = await dispatcher._ps_write_uart_wrapper(
            None, port="COM5", data="a55a", encoding="hex")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "SERIAL_OPEN_FAILED"


class TestSchema:

    async def test_capability_schema_has_encoding_enum(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        tool = next(t for t in ALL_TOOLS if t.name == "ps_write_uart")
        enc = tool.inputSchema["properties"]["encoding"]
        assert enc == {"type": "string", "enum": ["utf-8", "hex"]}
