# B12-A2 开发流程修复轮 #5（Agent1 白盒）报告——Skill 补丁：接口时序仿真验证步骤

> 日期：2026-08-26（`Get-Date` 实测；UTC+8）｜角色：Agent1（白盒实现）
> 范围（用户授权）：在 `skills/zynq_dev/` 开发流程中补入**接口时序仿真验证步骤**。背景：白盒/黑盒的 AD7606C 接口都因缺这一步而上板反复试错（黑盒 9 代排查）；`pl_analyze_timing`（STA）只验证 FPGA 内部时序，不验证对外设接口时序。
> 纪律：改动前建 `.bak`，收尾已删除（全仓 `.bak` = 0）。只跑非硬件回归。未执行任何 git 写操作。未修改 CLAUDE.md、docs 冻结文档、boards/、legacy 目录、workspaces/、.mcp.json。未运行任何 EDA/host_live/device_live。

---

## 0. 改动总览

| 文件 | 改动 | 类型 |
|---|---|---|
| `skills/zynq_dev/phases/5_domain_implementation.md` | 在 PL 构建步骤（`pl_analyze_timing`）之后、`pl_generate_bitstream` 之前，补一条**强制步骤**（对外接口时序仿真验证）+ 原因说明 | Skill 开发流程纪律 |
| `skills/zynq_dev/appendix_mechanics.md` | 补四个仿真工具（`pl_compile_sim`/`pl_elaborate_sim`/`pl_run_simulation`/`pl_parse_sim_log`）调用要点一句 | 附录工具要点 |
| `mcps/zynq_mcp/tests/test_o6_skill_contract.py` | 新增契约测试 `test_skill_mandatory_interface_timing_simulation_step`，钉住强制步骤与四个公开仿真工具 | 契约回归 |

---

## 1. 改动内容

### 1.1 `phases/5_domain_implementation.md`（强制步骤）
在既有「对外接口时序」约束纪律块之后、§5.3 PS 之前，新增一条：

> **对外接口时序仿真验证（强制步骤，放在 `pl_analyze_timing` 之后、`pl_generate_bitstream` 之前；缺失即不得进入位流生成与上板）：**
> 若设计含对外设接口的时序要求（如 ADC/DAC/存储器的控制/数据时序），必须：
> 1. 先编写**数据手册级行为模型**与**自检 testbench**（含接口时序断言——如 CONVST/BUSY/CS/RD 建立/保持、采样窗口、通道数据对照等）。
> 2. 经公开 MCP `pl_compile_sim → pl_elaborate_sim → pl_run_simulation → pl_parse_sim_log` 完成仿真，**PASS 后才允许继续位流生成与上板**。
> 原因：`pl_analyze_timing`（STA）只验证 FPGA **内部**时序，**不验证对外设接口时序**。接口时序错误（如控制信号建立/保持、通道数据错位）在时序报告中不可见，只能靠接口级仿真暴露；`pl_parse_sim_log` 的 PASS/FAIL 为机读证据。仿真失败/Fail 时必须定位并修复后重跑，不得跳过直接上板。

满足要求：补在 `pl_generate_bitstream` 之前、`pl_analyze_timing` 之后；含时序断言与通道数据对照；经四个公开仿真工具；附一行原因说明（STA 只证内部时序、不证对外接口；仿真日志为机读证据）。**只属开发流程，未引入测试环节纪律**（如 PASS 阈值/黑盒验收 etc.）。

### 1.2 `appendix_mechanics.md`（工具要点一句）
在 PL 构建链表与 Manifest 终态门禁之后、§5 PS 软件链之前，追加一句：
> **接口时序仿真工具要点**：对外设接口时序验证按公开 MCP 顺序 `pl_compile_sim`（xvlog 编译 RTL/testbench）→ `pl_elaborate_sim`（xelab 细化）→ `pl_run_simulation`（xsim 运行）→ `pl_parse_sim_log`（解析日志取 PASS/FAIL 机读结论）；同 phase5 强制步骤，仿真 PASS 前不得 `pl_generate_bitstream`。

### 1.3 契约测试（新增）
`test_skill_mandatory_interface_timing_simulation_step`：断言 phase5 含「对外接口时序仿真验证」「强制步骤」「pl_analyze_timing」「pl_generate_bitstream」与四个公开仿真工具序列、含「内部/对外」原因说明；四个仿真工具均为已注册公开 MCP 工具；appendix 也含同样序列。

---

## 2. 契约测试结果

```bash
python -m pytest mcps/zynq_mcp/tests/test_o6_skill_contract.py -q
```
- **13 passed**（原 12 + 新增 1）。禁词/结构约束（`test_skill_contains_no_direct_process_or_build_recipe` 等）全部通过——新文案不含 `make`/`vivado`/`run_tcl`/`subprocess` 等禁 token。

---

## 3. 回归机械统计（前后对照，数字来自命令输出）

```bash
python -m pytest mcps -m "not host_live and not device_live"   （仓库根）
```
| 指标 | 基线（fix #4 后） | 修复后 | 变化 |
|---|---|---|---|
| collected | 1469 | **1470** | +1（新增契约测试） |
| passed | 1427 | **1428** | +1 |
| skipped | 1 | 1 | 0 |
| deselected | 41 | 41 | 0 |
| failed | 0 | **0** | 0 |

- 修复后 passed（1428）≥ 基线（1427），failed = 0，无测试净减。
- collected 基线（1469）为 fix #4 后 `--collect-only` 输出；修复后 **1470 tests collected**。

### 测试映射
新增 1 个测试函数（非硬件）：
| 文件 | 函数 | 归属 |
|---|---|---|
| `test_o6_skill_contract.py` | `test_skill_mandatory_interface_timing_simulation_step` | 契约回归（钉住强制步骤与公开仿真工具） |

删除/重命名测试：**无**。

---

## 4. 修改文件清单

Skill 文档（2）：
- `skills/zynq_dev/phases/5_domain_implementation.md`
- `skills/zynq_dev/appendix_mechanics.md`

测试（1）：
- `mcps/zynq_mcp/tests/test_o6_skill_contract.py`

（曾为三个文件建 `.bak`，收尾删除；全仓 `.bak` = 0。）

---

## 5. 未改动/未实现声明

- **未修改**：CLAUDE.md、docs 冻结文档、boards/、legacy 目录、workspaces/、.mcp.json。
- **未执行**：任何 git 写操作；任何 EDA/host_live/device_live 工具。
- **未自行冻结 Brick、未越级进入下一步骤；未调用 Agent2。**
- **未引入测试环节纪律**：只补开发流程（接口时序仿真作为位流生成前置必检项），未把仿真 PASS 阈值/黑盒验收/过测判定等测试纪律并入。

## 6. 需主代理注意（如实）

1. **本步为文档/流程纪律**，无对应自动化"强制"机制（Skill 契约测试仅校验文本结构与公开工具存在性，不校验执行）。强制的落地依赖智能体在 phase5 实际执行该步骤。
2. **仿真工具为 MOCK/受限支持**：`pl_compile_sim`/`pl_elaborate_sim`/`pl_run_simulation`/`pl_parse_sim_log` 走旧 Vivado MCP adapter（deferred XSim 独立 adapter）；若后续在无 XSim 环境下它们不可用，契约测试仍宽松通过（仅验文本含工具名与注册性），真实仿真需 host_live 环境验证。
3. **接口时序断言/行为模型由智能体按数据手册编写**——本 Skill 只规定"必须做"与"用什么公开工具"，不预设具体外设（保持零项目外设字样，B11 阶段①约束）。
