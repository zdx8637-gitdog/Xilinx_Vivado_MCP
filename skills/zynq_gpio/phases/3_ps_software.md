# Phase 3 — PS Software

> 输入: Platform XSA + project_path | 输出: ELF (ARM executable) + 自动发布的 PS Build Manifest + 自行编写的 main.c

## Skill 决策

- **自行编写 `main.c`**，不依赖 `embedded_projects/ps_led_test/src/main.c`。见下方 GPIO 测试规范
- bare-metal standalone BSP，CPU = ps7_cortexa9_0，直接寄存器操作（`xil_io.h`）
- 编译器选项：不需要设置（GPIO 项目无特殊编译需求）
- XSCT workspace 放在 `<project_path>/` 下（session 根目录）
- `main.c` 写入 `<project_path>/src/main.c`，然后通过 `ps_add_sources` 添加

## 前提

Phase 1 的 `platform_generate` 必须已成功完成（产出 Platform XSA）。

## GPIO 测试规范（C 代码必须满足）

以下规范是**强制要求**。AI 必须自己编写 `main.c`，不能复用已有文件。

### 硬件参数

| 参数 | 值 |
|------|-----|
| AXI GPIO 基地址 | `0x41200000` |
| 数据寄存器偏移 | `0x00`（读/写 4-bit LED 值） |
| 方向寄存器偏移 | `0x04`（写 0 = 输出） |
| UART1 基地址 | `0xE0001000` |
| LED 有效位 | bit[3:0]，active-low（写 0 点亮） |

### 程序逻辑（必须实现）

```
1. 配置 UART1（115200 8N1，直接寄存器操作）
2. 设置 GPIO 方向为输出（Xil_Out32(LED_BASE+0x04, 0x0)）
3. 输出启动 banner: "=== AX7020 GPIO B08 ===\r\n"
4. 进入主循环（~1s 周期）:
   a. 写入 pattern 到 GPIO: Xil_Out32(LED_BASE, ~pattern & 0xF)
   b. 读回 GPIO:  readback = Xil_In32(LED_BASE)
   c. UART 输出: "WROTE:0x%X READ:0x%X\r\n" (大写 hex)
   d. 比较 wrote 和 readback & 0xF
   e. 如果不相等 → uart_send("GPIO_E2E_FAIL\r\n") 然后死循环
   f. 翻转 pattern (0xA ↔ 0x5)
   g. 延迟 ~1 秒
5. 循环 8 次后（8 轮全部通过），uart_send("GPIO_E2E_PASS\r\n")，死循环
```

### UART 输出格式要求

| 输出 | 格式 | 何时出现 |
|------|------|---------|
| Banner | `=== AX7020 GPIO B08 ===` | 启动时一次 |
| 每轮数据 | `WROTE:0xA READ:0xA` | 每轮循环 |
| 失败标记 | `GPIO_E2E_FAIL` | readback 不匹配时（立即） |
| 通过标记 | `GPIO_E2E_PASS` | 8 轮全部通过后 |

**完整预期输出示例**：
```
=== AX7020 GPIO B08 ===
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
GPIO_E2E_PASS
```

**注意**：UART 是 115200 8N1，使用 `\r\n` 换行。如果 readback 值与写入值不一致（低 4 位），程序输出 `GPIO_E2E_FAIL` 并停止。

### 为什么必须 readback

`Xil_Out32(0x41200000, ...)` 走的是 **PL AXI 总线**。如果 P5 部署缺少 `ps_load_hardware`（注册 PL 内存映射），写操作不会生效但程序不会 crash——UART 仍然正常输出。**没有 readback，UART 有 banner 也不能证明 GPIO 通路工作。** readback 是唯一可自动验证的证据。

## 执行序列

**所有 PS domain tools（`ps_*` 前缀）调用时必须显式传入 `session_id` 参数。**
Control tools（`wait_operation`、`get_execution_state`）不需要。
遗漏会导致 `INVALID_ARGUMENT / SESSION_ID_REQUIRED`。

### 3a. 导入硬件

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `ps_import_hardware` | `{"xsa_path": "<Phase 1 产出的 xsa_path>", "project_path": "<project_path>"}` | `status == "SUCCEEDED"` |

`wait_operation` timeout=60。

**⚠️ XSA 路径冲突**：`platform_generate` 将 `platform.xsa` 产出在 workspace 根目录（`<project>/platform.xsa`）。
`ps_import_hardware` 的 `xsa_path` 如果指向 workspace 内的文件，可能与内部拷贝目标相同导致 `IMPORT_HW_FAILED: same file`。

**解法**：只做输入文件 staging（不执行任何 EDA/build）：先将 XSA 复制到
`<project_path>/inputs/platform.xsa`，再把该路径传给公开 tool：
```python
import shutil
staged = "<project_path>/inputs/platform.xsa"
shutil.copy2("<project_path>/platform.xsa", staged)
ps_import_hardware(xsa_path=staged, project_path="<project>")
```
这是路径冲突规避，不是构建逃生通道。导入本身仍必须由 `ps_import_hardware` 完成。

### 3b. 创建 Platform

| 步骤 | MCP Tool | 参数 |
|------|----------|------|
| 2 | `ps_create_platform` | `{"name": "gpio_platform", "project_path": "<project_path>"}` |

`wait_operation` timeout=300。XSCT 创建 platform 可能需要几分钟。

### 3c. 生成 BSP

| 步骤 | MCP Tool | 参数 |
|------|----------|------|
| 3 | `ps_create_bsp` | `{"platform_name": "gpio_platform", "project_path": "<project_path>"}` |

`wait_operation` timeout=300。

### 3d. 创建 App

| 步骤 | MCP Tool | 参数 |
|------|----------|------|
| 4 | `ps_create_app` | `{"name": "gpio_app", "project_path": "<project_path>"}` |

`wait_operation` timeout=60。

### 3e. 编写并添加源码

| 步骤 | 动作 | 说明 |
|------|------|------|
| 5a | **编写** `main.c` | 按上方「GPIO 测试规范」自行编写，写入 `<project_path>/src/main.c` |
| 5b | `ps_add_sources` | `{"app_name": "gpio_app", "files": ["<project_path>/src/main.c"]}` |

`wait_operation` timeout=60。`ps_add_sources` 将源文件拷贝到 `<project_path>/gpio_app/src/`——Makefile 从此目录编译。

### 3f. 编译

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 6 | `ps_compile` | `{"app_name": "gpio_app"}` | Operation `SUCCEEDED`; ELF 已验证；PS Manifest 已自动发布 |
| 7 | `wait_operation` | `{"operation_id": "...", "timeout_s": 900}` | `status == "SUCCEEDED"`, `artifact_state == "PUBLISHED"` |

`ps_compile` 是唯一正式编译入口。产品内部先执行 Vitis `app build`，如果该版本
没有产出 ELF，会在同一受控 XSCT Operation 内执行产品拥有的 fallback；智能体
不得调用 shell 编译器或链接器。返回的 `result.data.build_method` 只用于审计，
不能改变成功标准。

终态 finalizer 会校验 ELF（ELFCLASS32 / little-endian / EM_ARM）并自动发布
`manifests/ps/sha256_*.json`。ELF 存在但 Manifest 门禁失败仍是 Phase 3 失败。

### 3g. 确认 ELF

| 步骤 | MCP Tool | 参数 |
|------|----------|------|
| 8 | `ps_get_build_status` | `{"session_id": "..."}`，随后等待 Operation | 

从返回中找到 `gpio_app` 的 `elf` 路径。**记录 elf_path，Phase 5 要用。**

**`wait_operation` 返回结构**：
返回 `{status: "success", data: {operation_id, status, result: {status, data: {...}}}}}`.
tool 的实际数据在 `data.result.data` 中（两层嵌套）。例如 `ps_import_hardware`
的 `xsa_path` 在 `wait_result["data"]["result"]["data"]["xsa_path"]`。
技能文档各步骤只写 "从返回数据中取 xsa_path"——实际路径是嵌套的。

**PS workspace 位置**：所有 PS build tools 的 `project_path` 参数使用 session 根目录
（即 `create_session` 传入的 `project_path`），不是 `<project_path>/ps/` 子目录。
XSCT 的 `setws` 在该目录下创建 `gpio_platform/`、`gpio_app/` 等子目录。

## 产物

| 产物 | 路径 | 用途 |
|------|------|------|
| ELF | `<project_path>/gpio_app/Debug/gpio_app.elf` | Phase 5 JTAG 下载 |
| main.c | `<project_path>/src/main.c` | 自行编写的测试程序（证据） |

用 `ps_read_elf_info` 可验证 ELF 为 ELFCLASS32 / EM_ARM。

## 失败恢复

| 症状 | reason_code | 动作 |
|------|------------|------|
| `IMPORT_HW_FAILED` | XSA 无效 | 检查 XSA 文件是否存在、是否包含 HDF（Phase 1 是否成功） |
| `IMPORT_HW_FAILED` | `same file` | `xsa_path` 与内部拷贝目标同一文件：先复制 XSA 到 workspace 外的临时路径（或 `_agent2/stage/` 子目录）再传入，见上方 §3a 说明 |
| `PLATFORM_CREATE_FAILED` | XSCT platform create 失败 | 检查 XSCT 版本（需要 2023.1），检查 workspace 路径无空格 |
| `APP_CREATE_FAILED` | app 创建失败 | 检查 platform 是否已成功创建 |
| `BUILD_FAILED` | 编译失败 | 检查 main.c 语法、是否引用了 xparameters.h（bare-metal direct register 不需要） |
| `TIMED_OUT` / `OUTCOME_UNKNOWN` | 受控 XSCT Operation 未得到可信终态 | 保存公开观测；调用 `diagnose_execution`，仅按 `recommended_action` 恢复，禁止重启 server 或直接操作进程 |
| `MANIFEST_PUBLISH_FAILED` / `ARTIFACT_FINALIZATION_FAILED` | ELF/Manifest 终态门禁失败 | 停止；不得手工补 Manifest |

## 已知限制

- `ps_set_compiler_options` 仅支持 `-D` 宏定义（Vitis 2023.1 XSCT 限制）。GPIO 项目不需要编译选项，不调用此 tool
- 裸机直接寄存器操作：只用 `xil_io.h`（`Xil_Out32`/`Xil_In32`），不依赖 BSP 头文件（`xparameters.h` 等）
- **`xil_printf` null 字节**：bare-metal `xil_printf` 在 Zynq PS UART 使用
  32-bit 写（`Xil_Out32`）操作 8-bit TX FIFO，每字符间插入 `\x00\x00\x00`。
  P5/P6 拿到 UART 文本后必须 `.replace("\x00", "")` 清理，否则 marker 匹配失败。
  更干净的方法是用 `Xil_Out8` 逐字节写 UART（见 Phase 7 UART 诊断参考代码）。
