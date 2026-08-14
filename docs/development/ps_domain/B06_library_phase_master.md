# B06 Library Phase — Master Brief

> 日期: 2026-08-08
> 状态: PLANNING — 库阶段可并行，集成阶段等 B05 FREEZE
> 依赖: 无（所有新文件，零共享文件修改）
> 架构依据: `docs/architecture_ai_zynq7020.md` v2.3.1 §4.2 PS MCP

---

## 0. 背景与策略

B06 目标是在统一 zynq_mcp 内实现 PS/ARM Domain（~41 APIs）。B05 正在并行开发，会修改 `capabilities.py`、`dispatcher.py`、`domain_runner.py`。因此 B06 分两段：

| 阶段 | 内容 | 时机 | 是否与 B05 冲突 |
|------|------|------|:---:|
| **库阶段**（当前） | `adapters/xsct/` + `adapters/uart/` + `domains/ps/` 内部实现 | 现在即可并行 | ✅ 不冲突 |
| **集成阶段**（后续） | `capabilities.py`/`dispatcher.py`/`domain_runner.py` 注册+wiring + MCP SDK tests | B05 FREEZE 后 | 需人工合并 |

本文件定义库阶段的共享契约，使四个 Agent 可以并行开发、独立测试、最后无痛并入。

---

## 1. 架构回顾：PS Domain 在统一 MCP 中的位置

```
mcps/zynq_mcp/
├── server.py              ← 入口，不改
├── dispatcher.py          ← 路由，库阶段不改
├── control/
│   ├── domain_runner.py   ← CommandRunner + DomainExecutionMutex，库阶段不改
│   ├── capabilities.py    ← ALL_TOOLS 列表，库阶段不改
│   └── ...
├── adapters/
│   ├── vivado/            ← 已有 PL Adapter
│   ├── vivado_adapter.py  ← 已有 VivadoBridge
│   ├── xsct/              ← [NEW] Agent A: XSDB/XSCT Bridge
│   └── uart/              ← [NEW] Agent B: Serial Adapter
└── domains/
    ├── pl/                ← 已有 system_top.py
    ├── platform/          ← B05 开发中
    └── ps/                ← [NEW] Agent C/D: PS domain 模块
        ├── jtag_target.py
        ├── target_control.py
        ├── memory_access.py
        ├── target_recovery.py
        ├── debug_session.py
        └── tests/
```

## 2. 并行 Agent 分工

| Agent | 模块 | 新文件数 | 依赖 |
|-------|------|---------|------|
| **A** | XSCT/XSDB Bridge | ~4 | 无（纯进程管理） |
| **B** | UART Serial Adapter | ~3 | 无（纯串口，需 pyserial） |
| **C** | ARM Target Operations | ~5 生产 + ~4 测试 | 依赖 Agent A 的 XsdbBridge **接口**（不依赖实现） |
| **D** | ARM Debug Session | ~2 生产 + ~1 测试 | 依赖 Agent A 的 XsdbBridge **接口**（不依赖实现） |

**关键**：Agent C/D 依赖的是 Master 文档里定义的接口签名，不是 A 的实现代码。三个 Agent 可以同时开发——A 写真正的 bridge，C/D 先对着接口写 + 用假 bridge 做单元测试。

---

## 3. 共享接口契约

### 3.1 XsdbBridge（Agent A 实现，Agent C/D 消费）

```python
# adapters/xsct/xsdb_bridge.py

class XsdbBridgeError(Exception):
    """Raised when the bridge itself fails (process died, timeout, parse error)."""
    pass

class XsdbBridge:
    """Manages a persistent xsdb subprocess for JTAG debug operations.

    xsdb runs in interactive mode (no -batch). Commands are sent via stdin.
    Output is delimited by sentinel markers so we can reliably detect
    command completion even when the Tcl output is multi-line.

    Lifecycle: start() → [eval() × N] → stop()
    """

    async def start(self, hw_server_url: str = "localhost:3121") -> None:
        """Launch xsdb subprocess. Must be called before eval()."""

    async def stop(self) -> None:
        """Terminate xsdb subprocess. Safe to call multiple times."""

    async def eval(self, tcl: str, timeout_s: float = 30.0) -> dict:
        """Send a Tcl command and return structured result.

        Returns:
            {"status": "success", "data": "<stdout text>"}  on success
            {"status": "error", "error": {"code": "XSDM_EVAL_ERROR",
             "message": "...", "details": {"reason_code": "..."}}}  on failure

        Raises:
            XsdbBridgeError: if the bridge process is dead or unresponsive.
        """

    @property
    def pid(self) -> int | None:
        """Subprocess PID, or None if not started."""

    @property
    def ready(self) -> bool:
        """True if xsdb process is alive and accepting commands."""

    @property
    def hw_connected(self) -> bool:
        """True if connect() has been called successfully on this session."""
```

**实现约束**：
- 使用 `asyncio.create_subprocess_exec`，stdin/stdout/stderr 全部 PIPE
- 命令完成检测：每条 Tcl 命令前后插入 marker echo（`puts "__XSDB_BEGIN__"` / `puts "__XSDB_END__"`），读取直到匹配 `__XSDB_END__`
- stderr 非空时合并到 error message
- `timeout_s` 超时时终止子进程并 raise `XsdbBridgeError`
- 进程意外退出时，所有后续 `eval()` 返回错误而非 hang

### 3.2 XsctBridge（Agent A 实现，集成阶段消费）

```python
# adapters/xsct/xsct_bridge.py

class XsctBridgeError(Exception):
    pass

class XsctBridge:
    """Manages xsct subprocess for software platform operations.

    xsct is used for: import_hw, platform create, bsp create, app create, build.
    Unlike xsdb, xsct operations are typically one-shot batch commands, not
    persistent interactive sessions. But for consistency, we still use the
    same interactive + sentinel pattern.
    """

    async def start(self, workspace: str | None = None) -> None:
        """Launch xsct subprocess. Optional workspace path."""

    async def stop(self) -> None:
        """Terminate xsct subprocess."""

    async def eval(self, tcl: str, timeout_s: float = 60.0) -> dict:
        """Same contract as XsdbBridge.eval()."""

    @property
    def pid(self) -> int | None: ...
    @property
    def ready(self) -> bool: ...
```

**注意**：库阶段 XsctBridge 的消费者是 BSP/Build 模块，属于 B06 集成阶段。库阶段只需实现 bridge 本身 + 基础测试。

### 3.3 SerialAdapter（Agent B 实现）

```python
# adapters/uart/serial_adapter.py

class SerialAdapterError(Exception):
    pass

class SerialAdapter:
    """Synchronous serial port adapter for PS UART observation.

    This is intentionally synchronous (not async) because:
    1. pyserial is synchronous
    2. UART read is blocking by nature — you read for a duration
    3. It runs inside asyncio.to_thread() when called from async context
    """

    @staticmethod
    def list_ports() -> list[dict]:
        """List available serial ports.

        Returns:
            [{"port": "COM3", "description": "USB Serial Port", "hwid": "..."}, ...]
        """

    def open(self, port: str, baudrate: int = 115200,
             timeout: float = 0.1) -> None:
        """Open a serial port. Raises SerialAdapterError on failure."""

    def close(self) -> None:
        """Close the serial port. Safe to call multiple times."""

    def read(self, duration_ms: int = 5000) -> bytes:
        """Read from serial port for the given duration, then return buffered data."""

    def read_line(self, timeout_s: float = 10.0) -> bytes:
        """Read until newline or timeout."""

    def write(self, data: bytes) -> int:
        """Write data to serial port. Returns bytes written."""

    @property
    def is_open(self) -> bool: ...
```

**依赖**：`pyserial`（`import serial`）。如果环境没有安装，测试应 skip。

### 3.4 PS Domain 模块通用契约（Agent C/D）

每个 domain 模块是一个**无状态函数集合**，接收 `XsdbBridge` 作为依赖注入：

```python
# 示例：domains/ps/jtag_target.py

from mcps.common.tool_response import success, error

async def connect_hw_server(bridge: XsdbBridge,
                             url: str = "localhost:3121") -> dict:
    """Connect to JTAG hw_server. Returns ToolResponse-compatible dict."""
    ...

async def list_targets(bridge: XsdbBridge) -> dict:
    """List all targets on JTAG chain. Returns ToolResponse-compatible dict."""
    ...
```

**所有 PS domain 函数遵循统一返回格式**：

```python
# 成功
{"status": "success", "data": {...}}

# 失败
{"status": "error", "error": {
    "code": "ERROR_CODE",       # 顶层 ErrorCode，mcps/common/error_codes.py
    "message": "human readable",
    "details": {"reason_code": "SPECIFIC_REASON"}  # 可选的细分
}}
```

使用 `mcps/common/tool_response.py` 中的 `success()` / `error()` 构建器，禁止手写 dict。

---

## 4. 禁止修改的文件清单（零冲突保证）

以下文件**库阶段绝对不碰**：

| 文件 | 原因 |
|------|------|
| `mcps/zynq_mcp/server.py` | B04 R1 FROZEN |
| `mcps/zynq_mcp/dispatcher.py` | B05 并行修改中 |
| `mcps/zynq_mcp/control/domain_runner.py` | B04 R3.0 FROZEN + B05 修改中 |
| `mcps/zynq_mcp/control/capabilities.py` | B05 修改 `DOMAIN_TOOLS` 中 |
| `mcps/zynq_mcp/adapters/vivado_adapter.py` | B04 R2 FROZEN |
| `mcps/zynq_mcp/domains/pl/system_top.py` | B04 R3.1-B FROZEN |
| `mcps/zynq_mcp/domains/platform/` | B05 开发中 |
| `mcps/common/` 任何文件 | B02 FROZEN |

**仅创建以下目录中的全新文件**：
- `mcps/zynq_mcp/adapters/xsct/`
- `mcps/zynq_mcp/adapters/uart/`
- `mcps/zynq_mcp/domains/ps/`

---

## 5. 测试约定

### 5.1 测试位置
- `mcps/zynq_mcp/adapters/xsct/tests/`
- `mcps/zynq_mcp/adapters/uart/tests/`
- `mcps/zynq_mcp/domains/ps/tests/`

### 5.2 测试模式
- **单元测试**（无 marker）：用 mock/fake bridge，验证逻辑正确性
- **host_live 测试**（`@pytest.mark.host_live`）：需要真实 XSDB/XSCT 进程在 PATH 上
- **device_live 测试**（`@pytest.mark.device_live`）：需要真实硬件（JTAG 线+板卡上电 或 USB-UART 连接）

### 5.3 伪测试禁止（来自 CLAUDE.md）
- 不允许 `pass` 占位、空测试
- 不允许 `except Exception: pass`
- mock 测试不能说成真实测试（使用正确的 evidence level）
- 每个声称 IMPLEMENTED_AND_TESTED 的模块必须有真实生产入口 + 对应有效测试

### 5.4 conftest.py
每个测试目录应包含 `conftest.py`，定义共享 fixtures（如 fake `XsdbBridge`）。

---

## 6. 各 Agent 子文档

| Agent | 文档 | 可并行 |
|-------|------|:---:|
| A | [B06_agent_A_xsct_adapter.md](B06_agent_A_xsct_adapter.md) | ✅ |
| B | [B06_agent_B_uart_adapter.md](B06_agent_B_uart_adapter.md) | ✅ |
| C | [B06_agent_C_arm_target.md](B06_agent_C_arm_target.md) | ✅ (依赖 A 接口) |
| D | [B06_agent_D_arm_debug.md](B06_agent_D_arm_debug.md) | ✅ (依赖 A 接口) |

---

## 7. 完成标准（每个 Agent）

1. 所有指定文件已创建，代码通过 flake8/ruff 风格检查
2. 测试文件存在，collected 数 ≥ 指定目标
3. 单元测试全部 PASS（不需要真实工具）
4. host_live 测试：如果本地有 Xilinx 工具则 PASS，否则 skip（需在报告中说明 skip 原因）
5. 没有空 pass、TODO 占位、裸 except
6. 报告 collected/passed/skipped/failed 的真实 pytest 统计数字
7. 明确声明未修改共享文件清单（第 4 节）
8. 标记每个能力的证据等级
