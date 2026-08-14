"""serial_adapter.py — SerialAdapter + SerialAdapterError.

Synchronous serial port adapter for PS UART observation (reading ARM
printf output, writing commands). B06 Library Phase, Agent B.

This module intentionally performs ONLY physical serial I/O. Baud-rate
matching, line framing and data parsing are handled by domains/ps/.

Design notes:
- Synchronous on purpose: pyserial is synchronous, UART read is blocking
  by nature (you read for a duration), and this runs inside
  asyncio.to_thread() when called from async context.
- Thread-safety: open()/close()/read()/write() are sequential by design.
  The caller (CommandRunner) ensures single-thread access via the
  DomainExecutionMutex.
"""
import logging
import time

import serial
import serial.tools.list_ports

logger = logging.getLogger("zynq_mcp.adapters.uart")


class SerialAdapterError(Exception):
    """Raised on serial port errors (port not found, access denied, etc.)."""


class SerialAdapter:
    """Synchronous serial port adapter for PS UART.

    Lifecycle: open() -> read()/read_line()/write() -> close().
    close() is idempotent; all I/O raises SerialAdapterError when the
    port is not open.
    """

    def __init__(self):
        self._ser = None
        self._port = ""
        self._baudrate = 0

    # ---- Static ----
    @staticmethod
    def list_ports() -> list:
        """Enumerate all available serial ports.

        Returns:
            list of dicts with keys: port, description, hwid, manufacturer,
            product, serial_number, vid, pid.

            Empty list if no ports found (not an error).
        """
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "port": p.device,
                "description": p.description,
                "hwid": p.hwid,
                "manufacturer": getattr(p, "manufacturer", None),
                "product": getattr(p, "product", None),
                "serial_number": getattr(p, "serial_number", None),
                "vid": f"0x{p.vid:04X}" if p.vid else None,
                "pid": f"0x{p.pid:04X}" if p.pid else None,
            })
        return ports

    # ---- Lifecycle ----
    def open(self, port: str, baudrate: int = 115200,
             timeout: float = 0.1) -> None:
        """Open serial port.

        Args:
            port: COM port name (e.g. 'COM5' on Windows,
                  '/dev/ttyUSB0' on Linux)
            baudrate: 9600, 115200, 921600, etc.
            timeout: read timeout in seconds (0.1 = 100ms granularity)

        Raises:
            SerialAdapterError: port doesn't exist, access denied, already
                open, etc.
        """
        if self._ser and self._ser.is_open:
            raise SerialAdapterError(f"Port {self._port} already open, close first")

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
            )
            self._port = port
            self._baudrate = baudrate
        except serial.SerialException as e:
            raise SerialAdapterError(str(e)) from e

    def close(self) -> None:
        """Close serial port. Safe to call when not open."""
        ser = self._ser
        if ser is not None and ser.is_open:
            try:
                ser.close()
            except (serial.SerialException, OSError) as e:
                # Best-effort close: device may already be gone. We still
                # reset our internal state so the adapter can be reused.
                logger.debug("best-effort close failed for %s: %s",
                             self._port, e)
        self._ser = None
        self._port = ""
        self._baudrate = 0

    # ---- I/O ----
    def read(self, duration_ms: int = 5000) -> bytes:
        """Read from serial port for specified duration.

        Accumulates ALL data received during the duration window.
        Returns empty bytes if nothing received.

        Args:
            duration_ms: read window in milliseconds (default 5s)

        Raises:
            SerialAdapterError: port not open, or read failed
        """
        self._ensure_open()
        deadline = time.time() + duration_ms / 1000.0
        buf = bytearray()
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            # Set a short timeout for this read attempt
            self._ser.timeout = max(0.01, min(remaining, 0.5))
            try:
                chunk = self._ser.read(self._ser.in_waiting or 1)
                if chunk:
                    buf.extend(chunk)
            except serial.SerialException as e:
                raise SerialAdapterError(f"Read error: {e}") from e
        return bytes(buf)

    def read_line(self, timeout_s: float = 10.0) -> bytes:
        """Read until newline (\\n) or timeout.

        Args:
            timeout_s: max wait time in seconds

        Returns:
            Line including newline, or empty bytes on timeout.

        Raises:
            SerialAdapterError: port not open, or read failed
        """
        self._ensure_open()
        self._ser.timeout = timeout_s
        try:
            return self._ser.readline()
        except serial.SerialException as e:
            raise SerialAdapterError(f"Read error: {e}") from e

    def write(self, data: bytes | str) -> int:
        """Write data to serial port.

        Args:
            data: bytes or str (str will be UTF-8 encoded)

        Returns:
            Number of bytes written.

        Raises:
            SerialAdapterError: port not open, or write failed
        """
        self._ensure_open()
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            return self._ser.write(data)
        except serial.SerialException as e:
            raise SerialAdapterError(f"Write error: {e}") from e

    def flush(self) -> None:
        """Flush output buffer.

        Raises:
            SerialAdapterError: port not open, or flush failed
        """
        self._ensure_open()
        try:
            self._ser.flush()
        except serial.SerialException as e:
            raise SerialAdapterError(f"Flush error: {e}") from e

    # ---- Properties ----
    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    def _ensure_open(self) -> None:
        if not self.is_open:
            raise SerialAdapterError("Serial port not open")
