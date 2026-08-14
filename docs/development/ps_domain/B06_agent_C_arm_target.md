# Agent C: ARM Target Operations

> Brick: B06 Library Phase | Agent: C | 依赖: Agent A 的 XsdbBridge **接口**（不需要 A 的实现完成）
> 主文档: 先读 [B06_library_phase_master.md](B06_library_phase_master.md)

## 1. 任务

在 `mcps/zynq_mcp/domains/ps/` 下实现 ARM 目标操作模块：

1. **jtag_target.py** — JTAG 连接与目标管理（6 APIs）
2. **target_control.py** — 目标执行控制（7 APIs）
3. **memory_access.py** — 内存/寄存器访问（4 APIs）
4. **target_recovery.py** — 目标恢复与诊断（4 APIs）

共 21 个 API 函数。

## 2. 架构约束

### 2.1 依赖注入模式

所有函数接收 `XsdbBridge` 实例作为第一个参数（不创建自己的 bridge）：

```python
# domains/ps/jtag_target.py

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.common.tool_response import success, error

async def connect_hw_server(bridge: XsdbBridge,
                             url: str = "localhost:3121") -> dict:
    """Connect to JTAG hw_server.

    Precondition: bridge is started but not yet connected.
    Postcondition: bridge.hw_connected == True.
    """
    ...
```

### 2.2 返回格式

所有函数返回 `ToolResponse` 兼容 dict（使用 `mcps/common/tool_response.py`）：

```python
# 成功
return success(data={"targets": [...], "count": 3})

# 失败
return error(
    message="No ARM DAP found on JTAG chain",
    code="TARGET_NOT_FOUND",
    details={"reason_code": "NO_ARM_DAP", "targets_found": 2}
)
```

### 2.3 错误码

使用 `mcps/common/error_codes.py` 中的 ErrorCode，或在模块内定义新的 ErrorCode 常量。所有 error 必须有 `code` 字段（顶层），推荐 `details.reason_code`（细分）。

### 2.4 无状态

模块本身是**纯函数集合**。状态（连接状态、选中的 target、下载的 ELF）由 XsdbBridge 内部维护（xsdb 进程本身就是有状态的 Tcl shell）。

### 2.5 单通道兼容性

所有函数是 async 但内部是顺序逻辑（发送 Tcl、等待响应、解析）。不需要锁——锁在 CommandRunner 层。

## 3. 交付文件

```
mcps/zynq_mcp/domains/ps/
├── __init__.py
├── jtag_target.py         ← 6 APIs
├── target_control.py      ← 7 APIs
├── memory_access.py       ← 4 APIs
├── target_recovery.py     ← 4 APIs
└── tests/
    ├── __init__.py
    ├── conftest.py        ← fake XsdbBridge fixture
    ├── test_jtag_target.py
    ├── test_target_control.py
    ├── test_memory_access.py
    └── test_target_recovery.py
```

## 4. 详细规格

### 4.1 jtag_target.py — 硬件连接与目标管理（6 APIs）

参考架构 doc §4.2 "硬件连接与目标管理"。

```python
async def connect_hw_server(
    bridge: XsdbBridge,
    url: str = "localhost:3121"
) -> dict:
    """连接 JTAG hw_server。
    
    后置条件: bridge.hw_connected == True。
    幂等: 已连接时返回 success 并注明 already_connected=True。
    
    错误:
    - HW_SERVER_UNREACHABLE: 无法连接到 hw_server
    - BRIDGE_NOT_READY: bridge 未 start
    """

async def disconnect_hw_server(
    bridge: XsdbBridge
) -> dict:
    """断开 JTAG 连接。
    
    幂等: 未连接时返回 success 并注明 already_disconnected=True。
    后置条件: bridge.hw_connected == False。
    """

async def list_targets(
    bridge: XsdbBridge
) -> dict:
    """列出 JTAG 链上所有目标。
    
    返回 data.targets: [
        {"id": 1, "name": "ARM Cortex-A9 #0", "type": " Cortex-A9", ...},
        {"id": 2, "name": "xc7z020", "type": "FPGA", ...},
    ]
    
    错误:
    - NOT_CONNECTED: 未连接 hw_server
    - JTAG_EMPTY_CHAIN: JTAG 链上无设备
    """

async def select_target(
    bridge: XsdbBridge,
    target_id: int
) -> dict:
    """选择 JTAG 链上的目标（通常是 ARM DAP）。
    
    target_id 来自 list_targets() 返回的 id。
    后置条件: xsdb 的当前 target 被设为指定 target。
    
    错误:
    - TARGET_NOT_FOUND: target_id 不存在
    - NOT_CONNECTED
    """

async def get_target_status(
    bridge: XsdbBridge
) -> dict:
    """查询当前选中目标的状态。
    
    返回 data.state: "running" | "halted" | "reset" | "unknown"
    返回 data.pc: 当前 PC 值（如果 halted）
    
    错误:
    - NO_TARGET_SELECTED: 未选择目标
    - TARGET_UNRESPONSIVE: 目标不响应
    """

async def get_device_info(
    bridge: XsdbBridge
) -> dict:
    """查询 ARM DAP 设备信息。
    
    返回 data: {idcode, irmask, ...}
    
    注意: FPGA DONE pin 属于 PL MCP。本 API 只查 ARM DAP。
    """
```

**Tcl 实现参考**（内部使用，不暴露）：

```tcl
# connect_hw_server
connect -url tcp:localhost:3121

# list_targets (xsdb targets 输出格式需要解析)
targets
# 输出示例:
#   1  ARM Cortex-A9 #0  (DAP)
#   2  xc7z020  (FPGA)

# select_target
targets -set -filter {id == 1}

# get_target_status
targets -target-properties -filter {id == 1}
# 解析 STATE 字段

# get_device_info
device properties
```

### 4.2 target_control.py — JTAG 下载与执行控制（7 APIs）

参考架构 doc §4.2 "JTAG 下载与目标控制"。

```python
async def reset_target(
    bridge: XsdbBridge,
    scope: str = "processor"
) -> dict:
    """复位目标。
    
    scope: 'processor' (仅 CPU) 或 'system' (全系统)
    后置条件: 目标处于 halted 状态。
    
    错误:
    - INVALID_SCOPE: scope 不是 processor/system
    - NO_TARGET_SELECTED
    - RESET_FAILED
    """

async def initialize_ps(
    bridge: XsdbBridge
) -> dict:
    """执行 PS7 初始化序列。
    
    调用 xsdb 的 ps7_init 命令。必须已连接 + 已选择 ARM 目标。
    
    错误:
    - PS7_INIT_FAILED
    - NO_TARGET_SELECTED
    """

async def download_elf(
    bridge: XsdbBridge,
    elf_path: str
) -> dict:
    """JTAG 下载 ELF 到 DDR。
    
    参数:
        elf_path: 绝对路径或相对路径（相对 project_path）
    
    验证:
    - ELF 文件存在且可读
    - 路径不包含 .. 转义
    
    错误:
    - ELF_NOT_FOUND: 文件不存在
    - ELF_INVALID: 不是有效的 ELF
    - DOWNLOAD_FAILED: xsdb dow 失败
    - NO_TARGET_SELECTED
    """

async def run_target(
    bridge: XsdbBridge,
    core: int | None = None
) -> dict:
    """启动处理器执行（xsdb con）。
    
    后置条件: 目标 running（需 wait_for_state 确认）。
    
    错误:
    - NO_TARGET_SELECTED
    - RUN_FAILED
    """

async def halt_target(
    bridge: XsdbBridge,
    core: int | None = None
) -> dict:
    """暂停处理器（xsdb stop）。
    
    后置条件: 目标 halted。
    幂等: 已 halted 时返回 success + already_halted=True。
    
    错误:
    - NO_TARGET_SELECTED
    - HALT_FAILED
    """

async def step_target(
    bridge: XsdbBridge,
    core: int | None = None
) -> dict:
    """单步执行（xsdb stp）。
    
    后置条件: 执行一条指令后 halted。
    
    错误:
    - NO_TARGET_SELECTED
    - TARGET_NOT_HALTED: 单步前必须先 halt
    - STEP_FAILED
    """

async def wait_for_state(
    bridge: XsdbBridge,
    state: str,
    timeout_s: float = 30.0
) -> dict:
    """等待目标进入指定状态。
    
    state: 'halted' | 'running'
    
    内部轮询 get_target_status，每 0.5s 一次，直到状态匹配或超时。
    超时时返回错误但不 raise 异常。
    
    错误:
    - TIMEOUT: 在 timeout_s 内未达到目标状态
    - INVALID_STATE: state 不是 halted/running
    """
```

### 4.3 memory_access.py — 内存与寄存器（4 APIs）

```python
async def reg_read(
    bridge: XsdbBridge,
    register: str
) -> dict:
    """读取 CPU 寄存器。
    
    register: 'r0'-'r15', 'sp', 'lr', 'pc', 'cpsr'
    
    返回 data.value: 十六进制字符串 "0x..."
    
    错误:
    - INVALID_REGISTER: 寄存器名无效
    - REG_READ_FAILED
    """

async def reg_write(
    bridge: XsdbBridge,
    register: str,
    value: int | str
) -> dict:
    """写 CPU 寄存器。
    
    value: 整数或十六进制字符串
    
    错误:
    - INVALID_REGISTER
    - REG_WRITE_FAILED
    """

async def mem_read(
    bridge: XsdbBridge,
    address: int | str,
    length: int = 4
) -> dict:
    """读内存。
    
    address: 物理地址（整数或 "0x..." 字符串）
    length: 读取的 word 数（1 word = 4 bytes），默认 1
    
    返回 data.words: [0x..., ...]
    返回 data.address: 十六进制字符串
    
    错误:
    - INVALID_ADDRESS
    - MEM_READ_FAILED
    """

async def mem_write(
    bridge: XsdbBridge,
    address: int | str,
    data: int | list[int] | bytes
) -> dict:
    """写内存。
    
    data: 单个值、值列表或 bytes
    
    错误:
    - INVALID_ADDRESS
    - MEM_WRITE_FAILED
    """
```

### 4.4 target_recovery.py — 恢复与诊断（4 APIs）

参考架构 doc §4.2 "目标恢复"。

```python
async def recover_target(
    bridge: XsdbBridge,
    strategy: str = "auto"
) -> dict:
    """自动恢复目标连接。
    
    strategy='auto' 的 cascade:
    1. halt_target  (尝试暂停)
    2. reset_target(scope="processor")  (处理器复位)
    3. reset_target(scope="system")     (系统复位)
    4. initialize_ps()                  (PS7 init)
    5. 验证 state → halted
    
    每个步骤失败时：
    - 记录当前阶段
    - 返回 data.failed_at_step: N
    - 返回 data.completed_steps: [...]
    - 不继续执行后续步骤
    
    错误:
    - RECOVERY_CASCADE_FAILED: 全部步骤失败
    - RECOVERY_PARTIAL: 部分步骤完成但最终验证失败
    """

async def reconnect_target(
    bridge: XsdbBridge
) -> dict:
    """重新连接到已打开的 JTAG 目标。
    
    内部: disconnect_hw_server → connect_hw_server → list_targets → select_target(ARM DAP)
    
    错误:
    - RECONNECT_FAILED
    """

async def clear_debug_session(
    bridge: XsdbBridge
) -> dict:
    """清除残留调试器状态。
    
    内部: 尝试 halt → 清除 breakpoints → disconnect → reconnect
    
    此操作不假设任何当前状态，尽最大努力清理。
    """

async def diagnose_dap(
    bridge: XsdbBridge
) -> dict:
    """诊断 DAP 状态并报告可能的原因。
    
    返回 data.diagnosis: {
        "connected": bool,
        "target_selected": bool,
        "target_state": str,
        "dap_locked": bool | None,
        "likely_issues": ["Cable disconnected", "Target in reset", ...],
        "suggested_action": "Run recover_target('auto')",
    }
    """
```

## 5. 测试要求

### 5.1 单元测试（≥20 collected，无 marker）

使用 **FakeXsdbBridge**：一个实现 XsdbBridge 接口但不启动真实进程的假 bridge。

```python
# tests/conftest.py

import pytest

class FakeXsdbBridge:
    """Test double for XsdbBridge.

    Pre-programmed responses for Tcl commands.
    Supports: start/stop lifecycle, eval with canned responses.
    """

    def __init__(self):
        self._responses: dict[str, str] = {}
        self._started = False
        self._hw_connected = False
        self._eval_history: list[str] = []

    async def start(self, hw_server_url: str = "localhost:3121") -> None:
        self._started = True
        if hw_server_url:
            self._hw_connected = True

    async def stop(self) -> None:
        self._started = False
        self._hw_connected = False

    async def eval(self, tcl: str, timeout_s: float = 30.0) -> dict:
        self._eval_history.append(tcl)
        # Look up canned response
        for pattern, response in self._responses.items():
            if pattern in tcl:
                return {"status": "success", "data": response}
        # Default: echo the command
        return {"status": "success", "data": tcl}

    def set_response(self, pattern: str, output: str):
        """Program a canned response for commands matching pattern."""
        self._responses[pattern] = output

    def set_error(self, pattern: str, message: str, code: str = "ERROR"):
        self._responses[pattern] = f"__ERROR__:{code}:{message}"

    @property
    def pid(self) -> int | None:
        return 12345 if self._started else None

    @property
    def ready(self) -> bool:
        return self._started

    @property
    def hw_connected(self) -> bool:
        return self._hw_connected


@pytest.fixture
def fake_bridge():
    return FakeXsdbBridge()
```

**每个模块的测试用例**：

**test_jtag_target.py** (≥5 tests):
| # | 测试 | 验证 |
|---|------|------|
| 1 | connect → hw_connected=True | 后置条件 |
| 2 | connect 两次 → already_connected | 幂等 |
| 3 | list_targets 正常 → 解析 targets 列表 | 解析逻辑 |
| 4 | select_target 无效 id → TARGET_NOT_FOUND | 错误路径 |
| 5 | 未连接时任何操作 → NOT_CONNECTED | 前提检查 |

**test_target_control.py** (≥6 tests):
| # | 测试 | 验证 |
|---|------|------|
| 1 | reset scope=processor → 成功 | 正常路径 |
| 2 | reset scope=invalid → INVALID_SCOPE | 参数验证 |
| 3 | download_elf 文件不存在 → ELF_NOT_FOUND | 文件检查 |
| 4 | download_elf 路径包含 .. → PATH_ESCAPE | 安全 |
| 5 | run → 成功 | 正常路径 |
| 6 | halt 两次 → already_halted | 幂等 |
| 7 | step 未 halt → TARGET_NOT_HALTED | 前提检查 |

**test_memory_access.py** (≥4 tests):
| # | 测试 | 验证 |
|---|------|------|
| 1 | reg_read r0 → 返回 value | 正常路径 |
| 2 | reg_read 无效寄存器 → INVALID_REGISTER | 参数验证 |
| 3 | mem_read 正常 → 返回 words | 正常路径 |
| 4 | mem_write 正常 → 成功 | 正常路径 |

**test_target_recovery.py** (≥5 tests):
| # | 测试 | 验证 |
|---|------|------|
| 1 | recover auto 全部成功 → completed_steps 长度=5 | cascade |
| 2 | recover 第3步失败 → failed_at_step=3 | 中途失败 |
| 3 | reconnect → 调用序列正确 | 序列 |
| 4 | clear_debug 无论状态都成功 | 容错 |
| 5 | diagnose_dap → 返回结构化诊断 | 诊断格式 |

### 5.2 host_live 测试（≥3 collected，需 XSDB + hw_server）

需要真实 XSDB 在 PATH 上 + hw_server 运行。没有则全部 skip。

- `test_list_targets_real`：真实 JTAG 链扫描
- `test_connect_disconnect_real`：真实连接/断开
- `test_halt_run_real`：真实 halt/run（需要板卡上电 + JTAG 连接，没有则 skip 并注明）

### 5.3 注意

- Agent C 的测试**不**启动 XsdbBridge 真实进程（那是 Agent A 的 host_live 测试范围）
- Agent C 的单元测试只验证 domain 逻辑：Tcl 命令生成 + 输出解析 + 错误处理
- 使用 FakeXsdbBridge 注入假的 Tcl 响应来模拟各种场景

## 6. 禁止

- 不修改 `adapters/xsct/`（Agent A 的代码）—— 只 import 它
- 不修改 `adapters/uart/`（Agent B 的代码）
- 不修改 `capabilities.py`、`dispatcher.py`、`server.py`
- 不做 MCP tool 注册
- 不实现 BSP/Build 管线（那是集成阶段的工作）

## 7. 依赖管理

Agent C 依赖 `XsdbBridge` 接口。如果 Agent A 还没完成：
1. 创建 `mcps/zynq_mcp/adapters/xsct/__init__.py` 和 `xsdb_bridge.py` 的**最小骨架**（只有 class 定义和方法签名，方法体 raise NotImplementedError）
2. 测试中使用 FakeXsdbBridge（见 §5.1）
3. Agent A 完成后替换骨架为真实实现

Agent C 应在 conftest.py 中 import 并检查：
```python
try:
    from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
except ImportError:
    # Agent A not yet complete — tests use FakeXsdbBridge
    pass
```

## 8. 完成标准

1. 所有 4 个生产模块已创建，函数签名完整，可从 `mcps.zynq_mcp.domains.ps.jtag_target` 等路径导入
2. 单元测试 ≥20 collected，全部 PASS
3. host_live 测试 ≥3 collected，PASS 或 skip（需说明原因）
4. 所有 API 的参数验证 + 错误路径都有测试覆盖
5. 无空 pass、TODO 占位、裸 except
6. 未修改 Master §4 共享文件清单中的任何文件
7. 报告真实 pytest 数字
