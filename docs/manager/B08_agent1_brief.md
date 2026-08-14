# B08 Agent1 白盒验收 — 操作手册

> 日期: 2026-08-10
> 角色: Agent1（白盒实现 Agent）
> 输入: 仅 Skill 文档 + MCP 连接 + 需求描述（不给 runner 脚本）

---

## B08 运行结果

> 日期: 2026-08-10 | Agent1 完成 | 最终判定: **PASS**
> 完整报告: `workspaces/gpio_b08_20260810/evidence/B08_AGENT1_REPORT.md`

Agent1 自主完成 GPIO 全流程，UART readback 证明 AXI GPIO 通路真实。
发现 **11 个问题**（2 P0, 3 P1, 6 P2），其中 6 个已在 B08→B09 过渡中修复。

---

## 1. 任务

用 `skills/zynq_gpio/` Skill 文档**自主**完成 GPIO 项目，验证 Skill 文档是否完备到足以让一个没有历史记忆的 Agent 从零走到 PASS。

### 工作目录

**固定**：`D:/fpgaproject/workspaces/gpio_b08_20260810/`（已创建，完全空目录）。

Agent1 的 `create_session` 必须使用此路径作为 `project_path`。所有产物由 MCP tools 写入此目录的子目录。

### 禁止事项

- **禁止**查看 `workspaces/gpio_e2e_20260809/` 或 `workspaces/gpio_b08_smoke_20260810/`（B07 runner 脚本）
- **禁止**参考 `embedded_projects/ps_led_test/src/main.c`（B07 旧固件，无 readback）
- **禁止**复用任何已有 `run.py` / `run_p2.py`
- 只能读 `skills/zynq_gpio/` 和本 brief 中的 MCP 启动模板

### 功能需求

| 字段 | 值 |
|------|-----|
| board_id | `ALINX_AX7020_v1.0` |
| 功能 | ARM 通过 AXI GPIO 控制 4 个 PL LED，**并读回 GPIO 值证明通路真实** |
| PS 软件 | **自行编写** `main.c`（Phase 3 Skill 规范） |
| UART 输出 | Banner + `WROTE:0x%X READ:0x%X` 每轮 + 最终 `GPIO_E2E_PASS` |
| pass_condition | UART 输出 `GPIO_E2E_PASS` |

### 与 B07 E2E 的关键差异

B07 复用已有 `embedded_projects/ps_led_test/src/main.c`（只 `Xil_Out32` 写 LED，**无 readback**）。B08 Phase 3 Skill 要求 Agent1 **自行编写** `main.c`，必须在每轮写入后 `Xil_In32` 读回并比较 wrote vs readback，8 轮全部匹配后输出 `GPIO_E2E_PASS`。**不再引用旧的 `main.c` 路径。**

---

## 2. MCP 加载方式（外部启动）

**不在 Claude Code 的 `.mcp.json` 中加载 MCP。** B08 全部 MCP 调用通过 MCP SDK 外部启动：

```python
import os, sys, tempfile
sys.path.insert(0, "D:/fpgaproject")

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

def spawn_server():
    env = os.environ.copy()
    env["PYTHONPATH"] = "D:/fpgaproject"
    # 独立 runtime root — 不与 Claude Code 的 .zynq_runtime/ 冲突
    env["ZYNQ_RUNTIME_ROOT"] = tempfile.mkdtemp(prefix="gpio_b08_")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcps.zynq_mcp.server"],
        env=env,
    )

async def main():
    params = spawn_server()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # ... 调用 101 个 tool
```

**关键参数**：
- Server 入口: `python -m mcps.zynq_mcp.server`
- PYTHONPATH: `D:/fpgaproject`
- `ZYNQ_RUNTIME_ROOT`: 使用独立临时目录，避免 InstanceGuard 冲突
- Server 自动发现 workspace root（通过 `resolve_workspace_root()` 向上查找 `mcps/` + `docs/brick_development_plan.md`）

---

## 3. Skill 文档位置

```
skills/zynq_gpio/
├── SKILL.md                          ← 入口：需求模板、Phase 概览、轮询规则、恢复机制
└── phases/
    ├── 0_board_profile.md             ← create_session + get_execution_state
    ├── 1_platform_design.md           ← platform_generate
    ├── 2_pl_build.md                  ← system_top → synth → place → route → timing → bitstream
    ├── 3_ps_software.md               ← import XSA → platform → BSP → app → add_sources → compile
    ├── 4_consistency.md               ← verify_consistency
    ├── 5_deployment.md                ← JTAG 烧录 + UART capture
    ├── 6_observation.md               ← evaluate_observation → PASS/FAIL
    └── 7_debug_recovery.md            ← 错误诊断 + 恢复
```

**B08 必须先通读 SKILL.md，再按 Phase 顺序执行。** 遇到不清晰的地方，记录为 Skill gap。

---

## 4. 工作目录

在 `D:/fpgaproject/workspaces/` 下创建新目录（如 `gpio_b08_20260810/`），作为 `create_session` 的 `project_path`。

```
workspaces/gpio_b08_20260810/
├── rtl/               ← pl_generate_system_top 输出
├── vivado/            ← P2 综合工程
├── xdc/               ← 专用约束文件（不是 board.xdc！）
├── bitstream/         ← .bit 文件
├── platform.xsa       ← P1 产物
├── ps/                ← PS 软件 + ELF
├── ps7_init.tcl       ← P1 产物
├── manifests/         ← Platform/PL/PS Build Manifest
│   ├── platform/
│   ├── pl/
│   └── ps/
└── evidence/          ← MCP 调用记录 + verdict
```

---

## 5. 流程分离约束（B07 已验证）

```
Step 1: MCP session → Phase 0 → Phase 1 (platform_generate) → Phase 2a (pl_generate_system_top) → close
Step 2: VivadoTclBridge standalone → synth → place → route → timing → bitstream → publish PL Manifest
Step 3: 新 MCP session → Phase 3 → Phase 4 → Phase 5 → Phase 6 → close
```

**P2 必须独立进程。** VivadoTclBridge 的 stop() 会干扰 MCP stdio transport。不能在 MCP session 内跑综合/布局/布线。

VivadoTclBridge 用法：
```python
from mcps.zynq_mcp.adapters.vivado.vivado_bridge import VivadoTclBridge
bridge = VivadoTclBridge()
bridge.start()
result = bridge.eval("open_project {...}\nadd_files {...}\nsynth_design\n...")
# 确认输出中有 BIT_DONE
bridge.stop()
```

P2 结束后必须手动生成 PL Build Manifest（因为独立进程不走 MCP 的 manifest hook）：
```python
from mcps.zynq_mcp.domains.verification.build_manifest import publish_pl_build_manifest
publish_pl_build_manifest(
    board_id="ALINX_AX7020_v1.0",
    project_path="workspaces/gpio_b08_20260810",
    board_profile_sha256="sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18",
    tool_args={"path": "workspaces/gpio_b08_20260810/bitstream/gpio_b08.bit"},
)
```

---

## 6. 关键约束（B07 Issues 已融入 Skill）

| # | 约束 | 相关 Phase |
|---|------|-----------|
| 1 | `ps_list_serial_ports` 返回 list of dicts，取 `p["port"]` 而非裸字符串 | P5 |
| 2 | P2 必须独立进程（见上节） | P2 |
| 3 | **禁止**使用 `board.xdc`——PS 设计需专用 XDC，端口名来自 BD wrapper（`gpio_led[3:0]`） | P2 |
| 4 | `write_bitstream` 成功 = `"BIT_DONE" in output` **且**文件存在且非空 | P2 |
| 5 | 部署用 `pl_program_fpga`（XSDB），**不是** `pl_program_device`（Vivado hw_manager 找不到 ARM-first JTAG） | P5 |
| 6 | UART marker 用 `["GPIO_E2E_PASS", "GPIO_E2E_FAIL", "WROTE:0x"]`，不用旧的 `=== AX7020`/`1010` | P5/P6 |
| 7 | 专用 XDC 必须放在 `vivado/` **之外**（如 `xdc/`），否则 manifest 扫描不到 | P2/P4 |
| 8 | P5 部署序列: `ps_initialize_ps` → **`ps_load_hardware`** → `ps_halt_target` → `ps_download_elf` → `ps_run_target`（`loadhw` 不可省略） | P5 |
| 9 | P2 用 VivadoTclBridge `async def` 方法——`start()`/`eval()`/`stop()` 都需要 `await` | P2 |
| 10 | PS domain tools (`ps_*`) 需要显式传 `session_id`；control/verify tools 不需要 | P3/P5 |
| 11 | UART null 字节——`xil_printf` 32-bit 写导致每字符间 `\x00\x00\x00`，MCP `ps_stop_uart_capture` 自动清理，但脚本自行读取时需 `.replace('\x00', '')` | P5/P6 |
| 12 | `board.xdc` 编码——文件含非 ASCII 注释，Python `open()` 需指定 `encoding='utf-8', errors='replace'` | P0/P2 |

---

## 7. 硬件环境

| 项目 | 值 |
|------|-----|
| hw_server | `localhost:3121`（需预先启动） |
| UART | COM4, 115200 8N1（Silicon Labs CP210x, VID:PID 10C4:EA60） |
| 板卡 | ALINX AX7020, xc7z020clg400-2 |
| LED | PL LED × 4 (active-low, pin J16/K16/M15/M14, LVCMOS33) |

---

## 8. 验收标准

### 通过条件

- [ ] 全流程 Phase 0→6 串行通过（不跳步，不手工构造中间产物）
- [ ] Phase 6 `evaluate_observation` verdict = PASS
- [ ] UART capture 包含 readback 值（证明 `Xil_In32` 通路真实）
- [ ] 所有 MCP 调用记录到 `evidence/` 目录
- [ ] 产出 Build Manifest（Platform + PL + PS）
- [ ] Skill gap 记录（如果发现 Skill 文档有歧义或缺失）

### 阻塞条件

- Phase 间产物不匹配（Manifest 校验失败）
- UART 有 banner 但 readback 值与写入值不一致
- 需要手工干预才能继续

---

## 9. B08→B09 已修复项

以下问题在 B08 运行中发现，已在 B09 开始前修复：

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | `ps_compile` 不产 ELF | 添加 make 步骤（`ps_bsp.py`） |
| P0-2 | UART null 字节破坏 marker | `ps_stop_uart_capture` 自动 `.replace("\x00", "")` |
| P1-3 | `platform_generate` 无幂等 | 添加 XSA/Manifest 缓存检查 |
| P2-4 | synth ERROR 检查误报 | 改为检查 `!*Complete!*` 而非 `*ERROR*` |
| P2-5 | 时序解析无约束设计失败 | 无约束设计返回 `timing_met: true` + note |
| — | Skill Phase 2 旧 MCP 路径 | 重写为 VivadoTclBridge + async + 端口名 |
| — | Skill Phase 5 重复块 | 删除重复 §5d/§5e + 添加 null-byte 清理 |
| — | Skill SKILL.md 轮询规则 | 区分 MCP 内 op vs VivadoTclBridge 独立进程 |
| — | Skill 缺少 session_id 表 | SKILL.md 添加 session_id 传递规则表 |
| — | Skill 缺少裸机 UART 代码 | 新 appendix_uart_baremetal.md |

---

## 10. 快速验证命令

```bash
# 运行前先 source Vivado 环境（所有需要 Vivado/XSCT 的命令都需要）
source "D:/Xilinx/Vivado/2023.1/settings64.sh"

# 确认统一 server 可启动（不创建 session，只验证 import 正常）
cd D:/fpgaproject && python -c "from mcps.zynq_mcp.control.capabilities import ALL_TOOLS; print(len(ALL_TOOLS))"
# 预期: 101

# 确认 boards 目录可达
ls D:/fpgaproject/boards/ALINX_AX7020_v1.0/

# 确认 Vivado/XSCT 可用
which vivado
which xsct

# 确认 hw_server 在运行
netstat -ano | grep 3121

# 确认工作目录是空的（B08 开始前）
ls D:/fpgaproject/workspaces/gpio_b08_20260810/

# 确认 B08 产物完整（如果 B08 已经跑过）
ls D:/fpgaproject/workspaces/gpio_b08_20260810/bitstream/
ls D:/fpgaproject/workspaces/gpio_b08_20260810/ps/gpio_app/Debug/gpio_app.elf
```
