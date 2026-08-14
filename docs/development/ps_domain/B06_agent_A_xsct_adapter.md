# Agent A: XSCT/XSDB Bridge

> Brick: B06 Library Phase | Agent: A | 依赖: 无
> 主文档: 先读 [B06_library_phase_master.md](B06_library_phase_master.md)

## 1. 任务

在 `mcps/zynq_mcp/adapters/xsct/` 下实现 XSDB Bridge 和 XSCT Bridge 两个进程管理器。

## 2. 背景

现有 Vivado Adapter（`adapters/vivado_adapter.py`）通过 MCP SDK stdio 管理 Vivado MCP 子进程。XSCT/XSDB 不同——它们是裸 Tcl shell，不需要 MCP 协议层。bridge 直接用 stdin/stdout 与子进程通信。

**XSDB** = Xilinx System Debugger，Tcl shell，用于 JTAG 操作（connect、targets、download、reset、memory read/write 等）。
**XSCT** = Xilinx Software Command-Line Tool，Tcl shell，用于软件平台操作（import hardware、create platform/BSP/app、build）。

两者都是 `xsdb` / `xsct` 可执行文件，位于 Xilinx Vitis 安装目录下（通常 `D:\Xilinx\Vitis\2023.1\bin`）。

## 3. 交付文件

### 3.1 生产代码（4 个文件）

```
mcps/zynq_mcp/adapters/xsct/
├── __init__.py
├── xsdb_bridge.py       ← XsdbBridge + XsdbBridgeError
├── xsct_bridge.py       ← XsctBridge + XsctBridgeError
└── templates.py         ← 常用 Tcl 命令模板
```

### 3.2 测试代码（2+ 文件）

```
mcps/zynq_mcp/adapters/xsct/tests/
├── __init__.py
├── conftest.py
├── test_xsdb_bridge.py
└── test_xsct_bridge.py
```

## 4. 详细规格

### 4.1 XsdbBridge

接口签名见 Master 文档 §3.1。以下是实现级补充：

**进程启动**：
```python
import asyncio, os, sys

# 查找 xsdb 可执行文件
def _find_xsdb() -> str:
    """查找 xsdb.exe。搜索顺序：
    1. 环境变量 XSDB_EXEC (如果设了完整路径)
    2. 环境变量 VITIS_ROOT/bin/xsdb
    3. D:/Xilinx/Vitis/2023.1/bin/xsdb
    4. PATH 中的 xsdb
    """
```

```python
async def start(self, hw_server_url: str = "localhost:3121") -> None:
    # 1. 启动子进程
    self._proc = await asyncio.create_subprocess_exec(
        self._xsdb_path,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # 2. 读初始 banner（直到第一个 prompt）
    await self._read_until_prompt(timeout=5.0)
    # 3. 如果 url 非空，发送 connect
    if hw_server_url:
        await self.eval(f"connect -url tcp:{hw_server_url}")
        self._hw_connected = True
```

**命令执行（核心）**：
```python
async def eval(self, tcl: str, timeout_s: float = 30.0) -> dict:
    # 1. 构造带 sentinel 的 Tcl
    marker_begin = f"puts __XSDB_BEGIN_{self._seq}__"
    marker_end = f"puts __XSDB_END_{self._seq}__"
    full_cmd = f"{marker_begin}\n{tcl}\n{marker_end}\n"
    
    # 2. 写入 stdin
    self._proc.stdin.write(full_cmd.encode())
    await self._proc.stdin.drain()
    
    # 3. 读取直到 __XSDB_END_{seq}__
    output = await asyncio.wait_for(
        self._read_until(f"__XSDB_END_{self._seq}__"),
        timeout=timeout_s
    )
    
    # 4. 解析输出：提取 BEGIN 和 END 之间的内容
    # 5. 检查 stderr（如果有错误输出）
    # 6. 如果 Tcl 返回错误（输出包含 "ERROR:"），返回 error dict
    # 7. 否则返回 success dict
    self._seq += 1
```

**超时处理**：
```python
# 超时时：kill 子进程，raise XsdbBridgeError("eval timeout after {timeout_s}s")
# 下次调用 eval() 时自动 restart（或返回 DEAD 状态）
```

**进程清理**：
```python
async def stop(self) -> None:
    # 1. 发送 exit 命令（尝试优雅退出）
    # 2. 等待 3s
    # 3. 如果还在运行，terminate() → 等 2s → kill()
    # 4. 清理所有引用
```

**状态属性**：
```python
@property
def pid(self) -> int | None:
    return self._proc.pid if self._proc and self._proc.returncode is None else None

@property
def ready(self) -> bool:
    return (self._proc is not None 
            and self._proc.returncode is None 
            and self._proc.stdin is not None)

@property
def hw_connected(self) -> bool:
    return self._hw_connected
```

### 4.2 XsctBridge

与 XsdbBridge 几乎相同的结构，差异：
- 可执行文件是 `xsct`（或 `xsct.bat` on Windows）
- 搜索路径使用 `XSCT_EXEC` / `VITIS_ROOT` 环境变量
- 默认 timeout 更长（60s，因为 build 操作可能很慢）
- 不需要 `hw_connected` 属性
- `start()` 可以接受 `workspace` 参数（`setws <workspace>`）

### 4.3 templates.py

```python
"""Tcl command templates for XSDB/XSCT operations.

NOT exhaustive — only the templates needed by library-phase PS domain modules.
Each template is a function that returns a Tcl string.
"""

def connect(url: str = "localhost:3121") -> str:
    return f"connect -url tcp:{url}"

def targets() -> str:
    return "targets"

def target_select(target_id: int) -> str:
    return f"targets -set -filter {{id == {target_id}}}"

def get_target_properties(target_id: int) -> str:
    return f"targets -target-properties -filter {{id == {target_id}}}"

def device_info() -> str:
    return "device properties"

def rst(scope: str = "processor") -> str:
    """scope: 'processor' or 'system'"""
    return f"rst -{scope}"

def ps7_init() -> str:
    return "ps7_init"

def dow(elf_path: str) -> str:
    return f"dow {elf_path}"

def con() -> str:
    return "con"

def stop() -> str:
    return "stop"

def stp() -> str:
    return "stp"

def mrd(address: str, length: int = 1) -> str:
    return f"mrd {address} {length}"

def mwr(address: str, value: str) -> str:
    return f"mwr {address} {value}"

def rrd(register: str) -> str:
    return f"rrd {register}"

def rwr(register: str, value: str) -> str:
    return f"rwr {register} {value}"

def bpadd(addr_or_symbol: str) -> str:
    return f"bpadd {addr_or_symbol}"

def bpremove(bp_id: int) -> str:
    return f"bpremove {bp_id}"

def bplist() -> str:
    return "bplist"

def backtrace() -> str:
    return "backtrace"

def disconnect() -> str:
    return "disconnect"

def after(delay_ms: int) -> str:
    return f"after {delay_ms}"

# build-related (for integration phase)
def setws(workspace: str) -> str:
    return f"setws {workspace}"

def import_hw(xsa_path: str) -> str:
    return f"importhw {xsa_path}"

def platform_create(name: str, hw: str, cpu: str = "ps7_cortexa9_0",
                    os: str = "standalone") -> str:
    return f"platform create -name {name} -hw {hw} -proc {cpu} -os {os}"

def bsp_create(platform: str, name: str = "bsp") -> str:
    return f"bsp create -platform {platform} -name {name}"

def app_create(name: str, platform: str, template: str = "empty_application") -> str:
    return f"app create -name {name} -platform {platform} -template {template}"

def app_build(name: str) -> str:
    return f"app build -name {name}"
```

> 这些模板是纯字符串构造函数。Agent C/D 可以直接使用它们，也可以自己写 Tcl。

## 5. 测试要求

### 5.1 单元测试（无 marker，必须有 ≥10 collected）

测试目标：验证 bridge 的逻辑正确性，**不需要真实 XSDB/XSCT**。

使用 Python 的 `asyncio.create_subprocess_shell` 或 mock：
- 启动一个假的 "Tcl shell"（如 `python -c "while True: print(input())"` 或简单的 echo 脚本）
- 验证 bridge 能正确发送命令、解析输出、处理超时
- 验证 start/stop 生命周期
- 验证多次 eval 后 seq 递增
- 验证进程意外退出后的错误处理
- 验证 stop 幂等性

**关键测试用例**：

| # | 测试 | 验证点 |
|---|------|--------|
| 1 | start 不存在的可执行文件 → raise | 环境探测 |
| 2 | start → pid 非空 → ready=True | 启动成功 |
| 3 | eval 简单 Tcl → 返回 success + 正确输出 | 命令执行 |
| 4 | eval 连续 3 条命令 → seq 递增，输出不混淆 | seq 机制 |
| 5 | eval 超时 → XsdbBridgeError | 超时保护 |
| 6 | stop → pid=None → ready=False | 停止 |
| 7 | stop 两次 → 不报错 | 幂等 |
| 8 | eval 在 stop 之后 → 明确错误 | 状态保护 |
| 9 | 子进程被外部 kill → eval 返回错误（不 hang） | 故障检测 |
| 10 | 并发 eval → 第二条被拒绝或排队 | 单通道（bridge 层面） |

### 5.2 host_live 测试（≥3 collected）

需要 XSDB 在 PATH 上。如果找不到，全部 skip 并在报告中说明。

- `test_xsdb_start_stop_real`：启动真实 xsdb，验证 banner，stop
- `test_xsdb_eval_simple_real`：`eval("puts hello")` → 返回 "hello"
- `test_xsdb_hw_connect_real`：`start(hw_server_url=...)` 后 `hw_connected=True`（需要 hw_server 运行，没有则 skip）

### 5.3 禁止

- 不做 MCP tool 注册
- 不做 MCP SDK 测试
- 不修改 `capabilities.py`、`dispatcher.py`、`server.py`
- 不创建 `domains/ps/` 下的文件（那是 Agent C/D 的工作）

## 6. 参考：现有 Vivado Adapter 模式

`adapters/vivado_adapter.py` 是相似的进程管理模式，可以参考其：
- `BridgeError` / `BridgeTimeoutError` 异常层次
- `ADAPTER_ABSENT/STARTING/READY/BUSY/POISONED/DEAD` 状态常量
- `INITIALIZE_TIMEOUT` / `CALL_TOOL_TIMEOUT` / `SHUTDOWN_TIMEOUT` 超时常量
- 使用 `asyncio.Lock` 保护状态转换

但 XSDB Bridge **不需要** MCP ClientSession（那是 Vivado MCP 专有的）。

## 7. 环境探测

Bridge 启动时需要探测 XSDB/XSCT 可执行文件。使用现有的 `mcps/common/env_probe.py` 模式（只读参考，不修改）。

搜索优先级：
1. `XSDM_EXEC` / `XSCT_EXEC` 环境变量（完整路径）
2. `VITIS_ROOT` 环境变量 + `/bin/xsdb` 或 `/bin/xsct`
3. 默认安装路径 `D:/Xilinx/Vitis/2023.1/bin/xsdb`
4. `shutil.which("xsdb")` / `shutil.which("xsct")`

## 8. 完成标准

1. 所有 4.1–4.3 中的文件已创建
2. pytest 单元测试 ≥10 collected，全部 PASS
3. host_live 测试 ≥3 collected，PASS 或 skip（需说明 skip 原因）
4. 无空 pass、TODO 占位、裸 except
5. 未修改共享文件清单（Master §4）中的任何文件
6. 报告真实数字：collected / passed / skipped / failed
7. 代码可被 `from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge` 导入
