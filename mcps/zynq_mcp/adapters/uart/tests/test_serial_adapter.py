"""test_serial_adapter.py — SerialAdapter unit + device_live tests.

Evidence levels:
- Unit tests (no marker): mock serial.Serial — IMPLEMENTED_AND_TESTED
  for SerialAdapter (open/close/read/read_line/write/flush/list_ports).
- device_live tests (@pytest.mark.device_live): require a real USB-UART
  device; skip when none is connected.
"""
import pytest

serial = pytest.importorskip("serial")

from mcps.zynq_mcp.adapters.uart.serial_adapter import (  # noqa: E402
    SerialAdapter,
    SerialAdapterError,
)

# ══════════════════════════════════════════════════════════════════
# -- list_ports (static) --

class _FakePortInfo:
    device = "COM9"
    description = "USB-SERIAL CH340"
    hwid = "USB VID:PID=1A86:7523"
    manufacturer = "wch.cn"
    product = "USB-SERIAL CH340"
    serial_number = "ABC123"
    vid = 0x1A86
    pid = 0x7523


def test_list_ports_returns_list_of_dicts(monkeypatch):
    monkeypatch.setattr(serial.tools.list_ports, "comports",
                        lambda: [_FakePortInfo()])
    ports = SerialAdapter.list_ports()
    assert isinstance(ports, list)
    assert ports[0]["port"] == "COM9"
    assert ports[0]["description"] == "USB-SERIAL CH340"
    assert ports[0]["hwid"] == "USB VID:PID=1A86:7523"
    assert ports[0]["manufacturer"] == "wch.cn"
    assert ports[0]["product"] == "USB-SERIAL CH340"
    assert ports[0]["serial_number"] == "ABC123"
    assert ports[0]["vid"] == "0x1A86"
    assert ports[0]["pid"] == "0x7523"


def test_list_ports_empty_is_not_an_error(monkeypatch):
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [])
    assert SerialAdapter.list_ports() == []


def test_list_ports_none_vid_pid(monkeypatch):
    class _Info:
        device = "COM5"
        description = "Unknown"
        hwid = ""
        vid = None
        pid = None
    monkeypatch.setattr(serial.tools.list_ports, "comports",
                        lambda: [_Info()])
    ports = SerialAdapter.list_ports()
    assert ports[0]["vid"] is None
    assert ports[0]["pid"] is None


# ══════════════════════════════════════════════════════════════════
# -- lifecycle --

def test_open_close(mock_serial):
    adapter = SerialAdapter()
    assert not adapter.is_open
    adapter.open("COM99", 115200)
    assert adapter.is_open
    assert adapter.port == "COM99"
    assert adapter.baudrate == 115200
    adapter.close()
    assert not adapter.is_open


def test_double_open_raises(mock_serial):
    adapter = SerialAdapter()
    adapter.open("COM99")
    with pytest.raises(SerialAdapterError):
        adapter.open("COM99")
    assert adapter.is_open


def test_close_twice_safe(mock_serial):
    adapter = SerialAdapter()
    adapter.close()  # not open — no error
    adapter.open("COM99")
    adapter.close()
    adapter.close()  # again — no error
    assert not adapter.is_open
    assert adapter.port == ""
    assert adapter.baudrate == 0


def test_open_error_raises(mock_serial):
    mock_serial.serial_class.side_effect = serial.SerialException("port not found")
    adapter = SerialAdapter()
    with pytest.raises(SerialAdapterError):
        adapter.open("COM99")
    assert not adapter.is_open
    assert adapter.port == ""


def test_close_best_effort_on_error(mock_serial):
    mock_serial.device.close.side_effect = serial.SerialException("port gone")
    adapter = SerialAdapter()
    adapter.open("COM99")
    adapter.close()  # must not raise
    assert not adapter.is_open
    assert adapter.port == ""


# ══════════════════════════════════════════════════════════════════
# -- read --

def test_read_when_closed_raises():
    adapter = SerialAdapter()
    with pytest.raises(SerialAdapterError):
        adapter.read()


def test_read_returns_accumulated_data(mock_serial):
    state = {"calls": 0}

    def _read(size):
        if state["calls"] == 0:
            state["calls"] += 1
            return b"hello"
        return b""

    mock_serial.device.read.side_effect = _read
    adapter = SerialAdapter()
    adapter.open("COM99")
    data = adapter.read(duration_ms=5)
    assert isinstance(data, bytes)
    assert b"hello" in data


def test_read_returns_empty_bytes_when_nothing_received(mock_serial):
    adapter = SerialAdapter()
    adapter.open("COM99")
    data = adapter.read(duration_ms=5)
    assert data == b""


def test_read_error_wrapped(mock_serial):
    mock_serial.device.read.side_effect = serial.SerialException("read failed")
    adapter = SerialAdapter()
    adapter.open("COM99")
    with pytest.raises(SerialAdapterError):
        adapter.read(duration_ms=1)


def test_read_line_returns_line(mock_serial):
    mock_serial.device.readline.return_value = b"Hello\r\n"
    adapter = SerialAdapter()
    adapter.open("COM99")
    assert adapter.read_line(timeout_s=1.0) == b"Hello\r\n"


def test_read_line_when_closed_raises():
    adapter = SerialAdapter()
    with pytest.raises(SerialAdapterError):
        adapter.read_line()


# ══════════════════════════════════════════════════════════════════
# -- write --

def test_write_returns_byte_count(mock_serial):
    adapter = SerialAdapter()
    adapter.open("COM99")
    n = adapter.write(b"test")
    assert n == 4
    mock_serial.device.write.assert_called_once_with(b"test")


def test_write_accepts_str_encoded_utf8(mock_serial):
    adapter = SerialAdapter()
    adapter.open("COM99")
    n = adapter.write("hello")
    assert n == 5
    mock_serial.device.write.assert_called_once_with(b"hello")


def test_write_when_closed_raises():
    adapter = SerialAdapter()
    with pytest.raises(SerialAdapterError):
        adapter.write(b"x")


def test_write_error_wrapped(mock_serial):
    mock_serial.device.write.side_effect = serial.SerialException("broken pipe")
    adapter = SerialAdapter()
    adapter.open("COM99")
    with pytest.raises(SerialAdapterError):
        adapter.write(b"x")


# ══════════════════════════════════════════════════════════════════
# -- flush --

def test_flush_calls_serial_flush(mock_serial):
    adapter = SerialAdapter()
    adapter.open("COM99")
    adapter.flush()
    mock_serial.device.flush.assert_called_once()


def test_flush_when_closed_raises():
    adapter = SerialAdapter()
    with pytest.raises(SerialAdapterError):
        adapter.flush()


# ══════════════════════════════════════════════════════════════════
# -- device_live (real USB-UART; skipped when no device) --

@pytest.mark.device_live
def test_list_ports_finds_devices():
    """device_live: a real USB-UART must be enumerated by list_ports()."""
    ports = SerialAdapter.list_ports()
    assert isinstance(ports, list)
    assert len(ports) > 0, "no serial ports found — is a USB-UART connected?"


@pytest.mark.device_live
def test_open_close_real_device():
    """device_live: open/close the first real serial port.

    Only verifies lifecycle — loopback write->read would require a
    physical loopback jumper, which is not guaranteed.
    """
    ports = SerialAdapter.list_ports()
    if not ports:
        pytest.skip("no serial ports found — no USB-UART connected")
    adapter = SerialAdapter()
    port = ports[0]["port"]
    try:
        adapter.open(port, 115200, timeout=0.1)
    except SerialAdapterError as e:
        pytest.skip(f"cannot open {port}: {e}")
    assert adapter.is_open
    assert adapter.port == port
    adapter.close()
    assert not adapter.is_open
