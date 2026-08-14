"""conftest.py — UART adapter test fixtures (mock serial.Serial)."""
import pytest

serial = pytest.importorskip("serial")

from unittest.mock import Mock  # noqa: E402


class MockSerialHandle:
    """Wraps the fake serial device plus the patched serial.Serial class.

    Tests read configuration through `.device` (the fake handle) and use
    `.serial_class` to inject constructor/error behaviour.
    """

    def __init__(self, device, serial_class):
        self.device = device
        self.serial_class = serial_class


@pytest.fixture
def mock_serial(monkeypatch):
    """Replace serial.Serial with a mock returning a fake device handle.

    The fake device behaves like an open idle port: is_open=True,
    in_waiting=0, read()/readline() return b"", write() returns the
    byte count of what it was given.
    """
    device = Mock()
    device.is_open = True
    device.in_waiting = 0
    device.read.return_value = b""
    device.readline.return_value = b""
    device.write.side_effect = lambda data: len(data)
    device.close.return_value = None
    serial_class = Mock(return_value=device)
    monkeypatch.setattr(serial, "Serial", serial_class)
    return MockSerialHandle(device=device, serial_class=serial_class)
