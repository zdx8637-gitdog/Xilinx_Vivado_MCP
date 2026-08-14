# Agent D: ARM Debug Session

> Brick: B06 Library Phase | Agent: D | 依赖: Agent A 的 XsdbBridge **接口**（不需要 A 的实现完成）
> 主文档: 先读 [B06_library_phase_master.md](B06_library_phase_master.md)

## 1. 任务

在 `mcps/zynq_mcp/domains/ps/` 下实现 ARM 调试会话模块：

- **debug_session.py** — 调试会话 + 断点 + 寄存器 + 调用栈（7 APIs）

## 2. 架构约束

与 Agent C 相同（见 [B06_agent_C_arm_target.md](B06_agent_C_arm_target.md) §2）：
- 依赖注入：`XsdbBridge` 作为第一个参数
- 返回 `ToolResponse` 兼容 dict
- 纯函数，无模块级状态
- 不操作 mutex/ledger（那是 CommandRunner 层的事）

## 3. 交付文件

```
mcps/zynq_mcp/domains/ps/
├── debug_session.py       ← [NEW] 7 APIs
└── tests/
    └── test_debug_session.py   ← [NEW]
```

**注意**：Agent C 也会创建 `domains/ps/tests/` 下的文件（conftest.py 等）。Agent D 和 Agent C 需要使用**相同的 FakeXsdbBridge**。

协调方案：
- Agent D 创建 `tests/conftest.py` 时检查是否已存在。如果已存在，追加 fixtures 而不覆盖。
- 或者 Agent D 使用自己的测试 conftest，但 import 相同的 FakeXsdbBridge。

**建议**：Agent D 的 `test_debug_session.py` 从 `tests/conftest.py` import `fake_bridge` fixture。如果 conftest 尚不存在，在自己的测试文件中定义一个简化版。

## 4. 详细规格

### 4.1 debug_session.py — 调试（7 APIs）

参考架构 doc §4.2 "调试"。

```python
"""debug_session.py — ARM JTAG debug session management.

A debug session wraps an XsdbBridge connection in a debug context:
- ELF is loaded (symbols available)
- Breakpoints can be set/removed
- Registers can be read/written
- Stack trace can be captured

The debug_session_id is an opaque token returned by debug_start().
It must be passed to all other debug_* functions.

Implementation note: xsdb does not have a native "debug session" concept.
We emulate it by tracking state:
- debug_start: download ELF, verify halted
- debug_close: clear breakpoints, halt if running
"""

import uuid
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.common.tool_response import success, error


async def debug_start(
    bridge: XsdbBridge,
    elf_path: str,
    target_id: int | None = None
) -> dict:
    """开始调试会话。

    1. 如果 target_id 指定，select_target(target_id)
    2. halt_target (确保目标暂停)
    3. download_elf(elf_path)  (加载符号)
    4. 生成 debug_session_id (UUID)
    5. 返回 session_id

    注意: 内部复用 Agent C 的函数（jtag_target.select_target,
    target_control.halt_target, target_control.download_elf）。
    不重新实现相同的 Tcl 逻辑。

    返回 data.debug_session_id: str

    错误:
    - DEBUG_START_FAILED
    - 以及所有子调用的错误（ELF_NOT_FOUND, DOWNLOAD_FAILED 等）
    """

async def breakpoint_add(
    bridge: XsdbBridge,
    debug_session_id: str,
    location: str  # 地址 "0x00100000" 或符号 "main"
) -> dict:
    """设置断点。

    返回 data.breakpoint_id: int  (xsdb bpadd 返回的 bp id)

    错误:
    - INVALID_DEBUG_SESSION: session_id 无效
    - BREAKPOINT_ADD_FAILED: bpadd 失败
    - INVALID_LOCATION: 地址格式无效
    """

async def breakpoint_remove(
    bridge: XsdbBridge,
    debug_session_id: str,
    bp_id: int
) -> dict:
    """移除断点。

    错误:
    - INVALID_DEBUG_SESSION
    - BREAKPOINT_NOT_FOUND: bp_id 不存在
    """

async def read_register(
    bridge: XsdbBridge,
    debug_session_id: str,
    register: str
) -> dict:
    """读取 CPU 寄存器（带调试上下文验证）。

    与 Agent C 的 reg_read 的区别：
    - 验证 debug_session_id 存在
    - 返回格式包含 session 关联信息

    返回 data: {register: str, value: str, debug_session_id: str}

    错误:
    - INVALID_DEBUG_SESSION
    - INVALID_REGISTER
    - TARGET_NOT_HALTED: 读寄存器前目标必须 halted
    """

async def write_register(
    bridge: XsdbBridge,
    debug_session_id: str,
    register: str,
    value: int | str
) -> dict:
    """写 CPU 寄存器。

    与 read_register 相同的前提条件。

    错误:
    - INVALID_DEBUG_SESSION
    - INVALID_REGISTER
    - TARGET_NOT_HALTED
    """

async def stack_trace(
    bridge: XsdbBridge,
    debug_session_id: str
) -> dict:
    """获取调用栈。

    内部: xsdb backtrace → 解析输出
    
    返回 data.frames: [
        {"level": 0, "pc": "0x...", "function": "main", "file": "main.c:42"},
        {"level": 1, "pc": "0x...", "function": "_start", "file": None},
    ]

    错误:
    - INVALID_DEBUG_SESSION
    - TARGET_NOT_HALTED
    - BACKTRACE_FAILED
    """

async def debug_close(
    bridge: XsdbBridge,
    debug_session_id: str
) -> dict:
    """关闭调试会话。

    1. 清除所有断点 (bpremove all)
    2. halt_target (如果 running)
    3. 不 disconnect（JTAG 连接保持，供其他操作使用）

    错误:
    - INVALID_DEBUG_SESSION
    """
```

### 4.2 内部调试会话追踪

因为 xsdb 没有原生 session 概念，我们需要在 Python 层追踪：

```python
# 模块级（线程安全：CommandRunner mutex 保证单线程访问）
_debug_sessions: dict[str, dict] = {}

def _create_session(elf_path: str) -> str:
    sid = f"debug-{uuid.uuid4().hex[:8]}"
    _debug_sessions[sid] = {
        "session_id": sid,
        "elf_path": elf_path,
        "breakpoints": set(),  # set of bp_ids
        "created_at": None,     # set by debug_start
    }
    return sid

def _get_session(session_id: str) -> dict:
    if session_id not in _debug_sessions:
        raise ValueError("INVALID_DEBUG_SESSION")
    return _debug_sessions[session_id]

def _remove_session(session_id: str):
    _debug_sessions.pop(session_id, None)
```

> 在集成阶段，这个追踪字典可以移到 Ledger 的 worker 上下文中持久化。库阶段先这样。

### 4.3 与 Agent C 模块的关系

debug_session.py **内部 import** Agent C 的模块：

```python
from mcps.zynq_mcp.domains.ps.jtag_target import select_target
from mcps.zynq_mcp.domains.ps.target_control import halt_target, download_elf
```

不要重新实现这些函数。如果 Agent C 还没完成，先写接口调用，测试时用 mock 替换。

## 5. 测试要求

### 5.1 单元测试（≥8 collected，无 marker）

使用与 Agent C 相同的 FakeXsdbBridge 模式。

**test_debug_session.py**:

| # | 测试 | 验证 |
|---|------|------|
| 1 | debug_start → 返回 session_id | 正常路径 |
| 2 | breakpoint_add → 返回 breakpoint_id | 正常路径 |
| 3 | breakpoint_remove → 成功 | 正常路径 |
| 4 | breakpoint_remove 不存在的 bp_id → BREAKPOINT_NOT_FOUND | 错误路径 |
| 5 | read_register → 返回 value | 正常路径 |
| 6 | read_register 无效 session → INVALID_DEBUG_SESSION | session 验证 |
| 7 | stack_trace → 返回 frames 列表 | 解析逻辑 |
| 8 | debug_close → 清除 session | 清理 |
| 9 | 重复 debug_close → INVALID_DEBUG_SESSION | 幂等保护 |
| 10 | 未 halt 时 read_register → TARGET_NOT_HALTED | 前提检查 |

### 5.2 FakeXsdbBridge 扩展

Agent D 需要 FakeXsdbBridge 支持更多 Tcl 命令模式。在 `conftest.py` 中扩展：

```python
# 预设的 JTAG 链响应
FAKE_TARGETS_OUTPUT = """
  1  ARM Cortex-A9 #0  (DAP)
  2  xc7z020  (FPGA)
"""

FAKE_BACKTRACE_OUTPUT = """
  #0  main () at main.c:42
  #1  _start () at crt0.S:15
"""

FAKE_REGISTER_OUTPUT = """
  r0: 0x00000000
  r1: 0x00100000
  pc: 0x00100040
"""
```

### 5.3 host_live 测试（≥2 collected）

需要真实 XSDB + hw_server + JTAG 连接。没有则 skip。

- `test_debug_start_halt_real`: 连接真板 → halt → debug_start → 验证 session_id
- `test_breakpoint_add_real`: 真板设置断点 → verify

**重要**：host_live 测试在真板上操作，绝不能破坏板卡状态。测试后必须清理（debug_close）。

## 6. 禁止

- 不修改 Agent C 的文件（`jtag_target.py` 等）—— 只 import 它们
- 不修改 `adapters/xsct/`（Agent A 的代码）
- 不修改 `adapters/uart/`（Agent B 的代码）
- 不修改 `capabilities.py`、`dispatcher.py`、`server.py`
- 不做 MCP tool 注册

## 7. 完成标准

1. `debug_session.py` 已创建，所有 7 个函数可导入
2. 单元测试 ≥8 collected，全部 PASS
3. host_live 测试 ≥2 collected，PASS 或 skip（需说明原因）
4. 内部正确 import Agent C 的模块（或 graceful fallback）
5. 无空 pass、TODO 占位、裸 except
6. 未修改 Master §4 共享文件清单中的任何文件
7. 报告真实 pytest 数字
