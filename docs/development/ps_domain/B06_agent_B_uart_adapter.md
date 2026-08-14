# Agent B: UART Serial Adapter

> Brick: B06 Library Phase | Agent: B | 依赖: 无（仅需 pyserial）
> 主文档: 先读 [B06_library_phase_master.md](B06_library_phase_master.md)

## 1. 任务

在 `mcps/zynq_mcp/adapters/uart/` 下实现串口适配器，用于 PS UART 观测（读取 ARM printf 输出、写入命令）。

## 2. 背景

Zynq-7020 的 ARM Cortex-A9 通过 UART1（MIO 48-49）输出调试信息。ALINX AX7020 板载 CH340 USB-UART 芯片，连接 PC 时显示为 COM 端口。B06 需要 `read_uart` / `write_uart` API，但底层是跨平台的串口通信。

现有项目已有：
- `tools/scripts/` 中的 PowerShell UART 扫描脚本（Windows 专用）
- 波特率 115200（Platform PS7 preset 默认值）
- UART baud 三层模型（见架构 doc §4.2）：Platform 设初始值、PS 软件可覆盖、观测端必须匹配

本模块只做物理串口 I/O。波特率匹配、数据解析等高层逻辑在 `domains/ps/` 中处理。

## 3. 交付文件

```
mcps/zynq_mcp/adapters/uart/
├── __init__.py
├── serial_adapter.py    ← SerialAdapter + SerialAdapterError
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_serial_adapter.py
```

## 4. 详细规格

### 4.1 SerialAdapter

接口签名见 Master §3.3。实现级补充：

```python
# adapters/uart/serial_adapter.py

import serial
import serial.tools.list_ports
import time

class SerialAdapterError(Exception):
    """Raised on serial port errors (port not found, access denied, etc.)."""
    pass


class SerialAdapter:
    """Synchronous serial port adapter for PS UART.

    Thread-safe: open()/close()/read()/write() are sequential by design.
    The caller (CommandRunner) ensures single-thread access via DomainExecutionMutex.

    All methods are synchronous. Async callers should wrap in asyncio.to_thread().
    """

    def __init__(self):
        self._ser: serial.Serial | None = None
        self._port: str = ""
        self._baudrate: int = 0

    # ---- Static ----
    @staticmethod
    def list_ports() -> list[dict]:
        """Enumerate all available serial ports.

        Returns list of dicts with keys: port, description, hwid, manufacturer,
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
            port: COM port name (e.g. 'COM5' on Windows, '/dev/ttyUSB0' on Linux)
            baudrate: 9600, 115200, 921600, etc.
            timeout: read timeout in seconds (0.1 = 100ms granularity)

        Raises:
            SerialAdapterError: port doesn't exist, access denied, already open, etc.
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
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass  # Best-effort close
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
            SerialAdapterError: port not open
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
            SerialAdapterError: port not open
        """
        self._ensure_open()
        self._ser.timeout = timeout_s
        try:
            line = self._ser.readline()
            return line
        except serial.SerialException as e:
            raise SerialAdapterError(f"Read error: {e}") from e

    def write(self, data: bytes | str) -> int:
        """Write data to serial port.

        Args:
            data: bytes or str (str will be UTF-8 encoded)

        Returns:
            Number of bytes written.

        Raises:
            SerialAdapterError: port not open, write failed
        """
        self._ensure_open()
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            return self._ser.write(data)
        except serial.SerialException as e:
            raise SerialAdapterError(f"Write error: {e}") from e

    def flush(self) -> None:
        """Flush output buffer."""
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

    def _ensure_open(self):
        if not self.is_open:
            raise SerialAdapterError("Serial port not open")
```

## 5. 测试要求

### 5.1 单元测试（≥8 collected，无 marker）

使用 **虚拟串口对**（`com0com` 或 socat）或 **mock**。

推荐方案：mock `serial.Serial`，验证：
- open/close 生命周期
- read 在 duration 后停止
- write 返回正确字节数
- close 幂等
- 未 open 时 read/write → SerialAdapterError
- list_ports 返回 list

```python
# 测试结构示例
class TestSerialAdapter:
    def test_list_ports_returns_list(self):
        ports = SerialAdapter.list_ports()
        assert isinstance(ports, list)

    def test_open_close(self, mock_serial):
        adapter = SerialAdapter()
        adapter.open("COM99", 115200)
        assert adapter.is_open
        adapter.close()
        assert not adapter.is_open

    def test_double_open_raises(self, mock_serial):
        adapter = SerialAdapter()
        adapter.open("COM99")
        with pytest.raises(SerialAdapterError):
            adapter.open("COM99")

    def test_close_twice_safe(self, mock_serial):
        adapter = SerialAdapter()
        adapter.close()  # no error
        adapter.open("COM99")
        adapter.close()
        adapter.close()  # no error

    def test_read_when_closed_raises(self):
        adapter = SerialAdapter()
        with pytest.raises(SerialAdapterError):
            adapter.read()

    def test_read_returns_data(self, mock_serial):
        # mock_serial.read returns b"hello" then b""
        adapter = SerialAdapter()
        adapter.open("COM99")
        data = adapter.read(duration_ms=100)
        assert isinstance(data, bytes)

    def test_write_returns_byte_count(self, mock_serial):
        adapter = SerialAdapter()
        adapter.open("COM99")
        n = adapter.write(b"test")
        assert n == 4

    def test_write_accepts_str(self, mock_serial):
        adapter = SerialAdapter()
        adapter.open("COM99")
        n = adapter.write("hello")
        assert n == 5
```

### 5.2 device_live 测试（≥2 collected）

需要真实 USB-UART 设备连接。没有则全部 skip。

- `test_list_ports_finds_devices`：list_ports() 返回非空列表
- `test_loopback_or_open`：如果有回环线，验证 write→read；否则只验证 open/close

### 5.3 依赖

需要在 `mcps/requirements.txt` 或测试环境中添加 `pyserial`。检查是否已有：

```bash
python -c "import serial; print(serial.__version__)"
```

如果没有，Agent 应在报告中注明需要 `pip install pyserial`。

## 6. 禁止

- 不碰 `adapters/xsct/`（Agent A 的领域）
- 不碰 `domains/ps/`（Agent C/D 的领域）
- 不修改 `capabilities.py`、`dispatcher.py`、`server.py`
- 不做 MCP tool 注册
- 不做与 Vivado/XSCT/JTAG 相关的任何事

## 7. 完成标准

1. `serial_adapter.py` 已创建，可通过 `from mcps.zynq_mcp.adapters.uart.serial_adapter import SerialAdapter` 导入
2. 测试文件 ≥8 collected，单元测试全部 PASS
3. device_live 测试 PASS 或 skip（需说明原因）
4. 无空 pass、TODO 占位、裸 except
5. 未修改 Master §4 共享文件清单中的任何文件
6. 报告真实 pytest 数字
