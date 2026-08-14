# B10 冻结发布清单（O8）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 15:57 +08:00）
> 状态：**B10 COMPLETE / O8 冻结包交付**；GPIO v1 稳定基线已由用户确认
> 性质：发布清单（O8 主体），只做冻结归档与事实记录，不修改任何生产代码 / 测试 / skills / boards

## 1. 版本标签与提交

| 项 | 值 |
|---|---|
| 基线 tag | `o7r3-baseline-20260813`（注解 tag，tag 对象 SHA256 形式 hash `727f823992f6c0de3c7e48699e336df2d3a08c34`） |
| tag → 基线 commit | `4e0d1482477e9afc3a000837298c0f63dcf60c34`（`git rev-list -n 1 o7r3-baseline-20260813` 实测） |
| 基线 commit 说明 | `Initial commit: unified zynq_mcp core baseline (B09 O7 R3 verified 2026-08-13)` |
| 当前 HEAD | `fcbdf08e01217d96164a64d6cca6241aa9c7a0b6`（`chore: add .gitattributes - LF enforcement...`） |
| HEAD 与基线差集 | 仅预存在的 docs/chore 提交：`6da9046`（CLAUDE.md 记录基线/远端）、`fcbdf08`（.gitattributes + 5 个日志文件重提）。12 个冻结资产在基线 → HEAD 差集中**零变化**（`git diff 4e0d148 HEAD -- <12 files>` 为空） |
| 工作树 | 本轮开始时 `git status --short` 为空（干净） |

## 2. 回归入口（本轮实测）

- **收集统计（从项目根目录）**：
  ```bash
  python -m pytest mcps --collect-only -q
  ```
  → `1369 tests collected in 1.78s`
- **完整非硬件回归（从项目根目录，跳过需 EDA 工具或硬件的测试）**：
  ```bash
  python -m pytest mcps -m "not host_live and not device_live" -q
  ```
  → **`1331 passed, 1 skipped, 37 deselected in 215.51s (0:03:35)`**（0 failed）
- **硬件类测试规模（机械收集）**：`host_live` = 33（需 Vivado/XSim）、`device_live` = 4（需 USB-UART）；`1331 passed + 1 skipped + 37 deselected = 1369`（37 deselected 即 33 host_live + 4 device_live），与 collected 数闭合。
- ⚠️ 测试必须从项目根目录运行：`cd mcps` 后约 20 个 MCP SDK contract 测试会因派生子进程无法 import `mcps` 而失败（见已知限制 ④）。

## 3. 冻结资产 SHA256 清单

以下 12 个文件为本轮 B10 冻结资产，SHA256 用 .NET `[System.Security.Cryptography.SHA256]` + 文件字节（与 `UTF8.GetBytes(ReadAllText)` 结果一致；文件均无 BOM）计算：

| 文件 | SHA256 |
|---|---|
| `mcps/zynq_mcp/server.py` | `b57942a319d25bd26594940485cb8ddef71bad8d75808262ce8bb6d3ec882847` |
| `mcps/zynq_mcp/control/capabilities.py` | `d5a1732658d8536926ee812619540018d27d75b8918e3b91d85676f218d61e35` |
| `mcps/zynq_mcp/control/execution_ledger.py` | `dd5679bb9afac06d1d8fc4d109316b5b9f29819e55463047ef5b3de688147d5d` |
| `mcps/zynq_mcp/control/domain_runner.py` | `bb56355e6e7950f3480d1e91d3dbaa3b3f6bb9aefabd8112fff685cc0e4c3850` |
| `mcps/zynq_mcp/dispatcher.py` | `7843781fc7697898e16f64059029a515573c368f5625d08badcb28c7a8c96fa2` |
| `mcps/zynq_mcp/domains/platform/platform_domain.py` | `c7383dcdb307dc7c94dc508cfb2f431f224e3e5251b054bc0f535259bdabd96b` |
| `mcps/zynq_mcp/domains/pl/pl_bridge_tools.py` | `aa00bacf8ebd8dd38841dcedb042fcdb425ef9a1e2bdc1ce00e949ad234fdef9` |
| `mcps/zynq_mcp/adapters/vivado_adapter.py` | `2f3e3df9f7d39f0c6e198286df33d953fb04108449b3b7033c481466b6ffd2f1` |
| `mcps/common/artifact_schema.py` | `381dac32c76b65febcd2aecffb4e2ccede0d32a4f7c7b4ca4a84f48b7cda4418` |
| `mcps/common/tool_response.py` | `35a48a6a82a6e92c36e5e80ff8687d01da659914a62e2ee715be2cf817cf133a` |
| `.mcp.json` | `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02` |
| `skills/zynq_gpio/SKILL.md` | `9645d0cb817bd98106b3df95e70501dfe98d1d913ded8047a3b5b5af95c900df` |

**与历史冻结记录核对**：

- `platform_domain.py` = `c7383dcdb307dc7c94dc508cfb2f431f224e3e5251b054bc0f535259bdabd96b` → **与 O6 冻结记录一致（MATCH）**
- `.mcp.json` = `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02` → **与 O1–O6 冻结记录一致（MATCH）**

## 4. 能力矩阵（机械统计，来自 `mcps/zynq_mcp/control/capabilities.py`）

统计方法：`Select-String -Pattern 'Tool\(name='` 计数 + 按工具名前缀分组；domains 声明计数取自 `build_capabilities()` 的 `domains` 字典。

| Domain | capabilities.py 声明（implemented / planned） | 实测工具数（按前缀） | 说明 |
|---|---|---|---|
| control | 9 / 9（`len(CONTROL_TOOLS)`） | 9 | create_session … recover_execution |
| platform | 15 / 14 | 15 | platform_generate(1) + B05-R2 atoms(14) |
| pl | 27 / 12 | 27 | B07 PL bridge(26) + pl_program_fpga(1) |
| ps | 47 / 19 | **48** | 声明 47 与实际 48 差 1（已知限制 ①） |
| observation | 1 / 4 | 2（verify_consistency + evaluate_observation） | 声明口径与机械前缀口径不同（见已知限制 ①） |
| recovery | 2 / 2 | —（recover_execution 计入 control、ps_recover_target 计入 ps） | 声明为交叉计数 |
| **合计（工具注册）** | — | **101**（9 control + 92 domain） | `Tool(name=` 行数 = 101，与 `ALL_TOOLS = CONTROL_TOOLS + DOMAIN_TOOLS` 一致 |

**证据等级标注**（按项目证据等级规则，不越级）：

- **硬件验证**：GPIO 纵向链路（Platform P1 → PL P2 → PS P3 → 三 Manifest 一致性 P4 → JTAG 部署 P5 → UART/GPIO 回读 P6 → Observation 判定）由 B09 O7 R3 全新 Agent2 公开 MCP 纯黑盒验收 PASS 覆盖（2026-08-13，见 `docs/development/mcp/B09_O7_R3_pass_report.md`：Consistency 12/12、UART 8/8、`GPIO_E2E_PASS`、清理审计通过）。该链路涉及工具：`platform_generate`、`platform_*` 原子、`pl_create_project`/`pl_synthesize`/`pl_place`/`pl_route`/`pl_generate_bitstream`、`ps_initialize_ps`/`ps_load_hardware`/`ps_download_elf`/`ps_run_target`/`ps_read_uart`/`ps_start_uart_capture`/`ps_wait_uart_capture`/`ps_stop_uart_capture`、`verify_consistency`、`evaluate_observation`、`get_operation_status`/`wait_operation`、`diagnose_execution`/`recover_execution` 等。
- **测试证据（IMPLEMENTED_AND_TESTED）**：本轮完整非硬件回归 1331 passed / 1 skipped / 37 deselected（0 failed），对已注册生产工具提供契约/单元/集成测试覆盖；未在本轮重复执行 host_live / device_live 硬件项（33 + 4），硬件证据沿用 O7 R3 记录。
- **不越级声明**：本清单不对未覆盖工具声称硬件验证；Adapter 层证据等级受 Adapter 真实工具 Gate 约束（见已知限制 ③）。

## 5. 已知限制（技术债，必须写入发布清单）

1. **能力计数漂移**：`capabilities.py` 的 `DOMAIN_APIS_IMPLEMENTED=91` 与实际 92（101 − 9 control）差 1；ps `implemented=47` 与实际前缀统计 48 差 1。仅声明数字漂移，不影响注册行为。
2. **`.mcp.json` 空注册**：当前为 `{"mcpServers": {}}`（SHA256=`d8e397af...`，与 O1–O6 冻结记录一致）；最终「仅 `zynq` 一个入口」的注册形态未交付，待后续决策。
3. **uart adapter 标记不一致**：`adapters/uart/` 用 `device_live` 标记做真实工具验证，而 Adapter 真实工具 Gate 文字要求 `host_live`；语义相同（真实 USB-UART 枚举），标记不同。属文案/标记口径问题，非功能缺陷。
4. **测试套件 CWD 依赖**：必须从项目根目录运行 `python -m pytest mcps`；未配置 `pytest.ini` 的 `pythonpath`，`cd mcps` 会导致约 20 个 MCP SDK contract 测试因子进程 import 失败。
5. **根目录卫生**：`workspaces/`、`.zynq_runtime/`、`.o6_runtime*/`、`NA/`、`tmp/`、Vivado 日志等为不入库的漂移物（证据与运行态目录）。
6. **大二进制文件策略待定**：仓库内被跟踪 >2MB 文件共 **12** 个（本轮 `git ls-files` + 文件大小机械统计），已入库未排除——6 个 ALINX 官方 PDF（`docs/boardinformation/`，约 49/23/13/6.6/4.4/2.2 MB）、5 个 `.bit`（约 3.86 MB 各）、1 个 `vendor/drivers/CP210x_Windows_Drivers.zip`（6.84 MB）。是否改用 LFS / 排除策略待决策。

## 6. 下一切片决策记录

- **状态：方向已提出，规划待确认**（用户本轮授权记录，不夸大）。
- **用户提出的方向**：数据采集切片 —— PL AD 采集（pin 配置由用户提供）→ DMA → DDR3 → PS 读 DDR3 → UART 上行到上位机实时成像/分析（上位机由另一个智能体负责）。
- **尚未完成（不得视为已选定）**：正式切片规划（Skill / MCP / Tests 三目录逐 Brick 记录）、能力缺口清单、门禁定义均未开始。下一切片仅在用户确认规划后按现有流程立项。
- 本冻结包**不**隐含下一切片已明确选定。

## 7. 附：本轮机械证据

- collect-only：`1369 tests collected in 1.78s`
- 非硬件回归：`1331 passed, 1 skipped, 37 deselected in 215.51s (0:03:35)`（0 failed）
- host_live = 33、device_live = 4（`--collect-only -m` 实测）
- 工具注册：`Tool(name=` = 101；前缀统计 platform 15 / pl 27 / ps 48 / control 9 / observation-query 2
- 冻结资产 12 项 SHA256 见 §3；`platform_domain.py`、`.mcp.json` 与历史冻结记录一致
- CLAUDE.md 规则章节（`# AI Agent 驱动 Zynq-7020 项目规则` 起）改动前后 SHA256 均 = `66666d2037afa7c178657c8c25c58a8addbfdabbf36731b1a1f6be232eb3e3ea`（冻结纪律字节不变）
- 提交：见 git log（本清单随 `docs: B10/O8 freeze package...` 提交）
