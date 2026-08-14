# B11 立项规划草案：泛化框架黑盒验证（DRAFT）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 17:19 +08:00）
> 状态：**DRAFT — 待用户审核。本文档是立项规划草案，不代表 B11 已立项、不冻结任何资产、不修改任何生产代码 / 测试 / skills / boards / 冻结文档。** B11 正式立项须等用户批准本规划后，由后续轮次更新 `docs/brick_development_plan.md` 并按现有 Brick 流程（Skill / MCP / Tests 三目录逐子步骤记录）执行。
> 配套文档：`docs/development/mcp/B11_platform_generate_erratum_draft.md`（B05 冻结资产处置勘误草案）、`docs/development/tests/B11_blackbox_requirement_draft.md`（6-LED 黑盒考题需求草案）、`docs/development/skill/B11_generalized_skill_design.md`（阶段①设计基础，已更新定位）、`docs/development/mcp/B11_data_acquisition_proposal.md`（已改述为「验证实例候选：数据采集（非当前立项对象）」）。

## 1. 背景与用户最新决定（必须记录，含义不可改动）

### 1.1 方向重定：B11 = 泛化框架黑盒验证

- B11 不再是「数据采集切片」。新目标：**用 GPIO 项目当考题，但 Skill 里完全不提 GPIO；GPIO 项目只是一份递给黑盒智能体的项目需求**。目的：证明 Skill + MCP 是面向任意 Zynq 工程开发的**通用框架**（未来 ADC / 视频 / HDMI / TCP 都是同类验证实例）。
- 考核方式沿用黑盒验证路线（B09/O7 R3 风格）：全新上下文智能体只拿到「通用 Skill + 项目需求文档 + 板卡物理事实 + 公开 zynq_mcp」，完成从零到硬件闭环。

### 1.2 Skill 与 MCP 彻底去 GPIO 化（用户明确授权）

- Skill：新框架 Skill 全篇零 GPIO 字样（机械扫描门禁）。
- MCP：用户**明确授权处置 B05 冻结资产 `platform_generate`**（硬编码 GPIO Block Design 的一键快捷工具）——按项目勘误纪律走 Erratum 记录（草案见 `B11_platform_generate_erratum_draft.md`），推荐**移除注册**、以 B05-R2 的 14 个原子作为替代路径（1→N 映射见 §5）。
- 同步清理：`evaluate_observation` 默认 marker 的 GPIO 措辞、capability 常量修正（101→100）、关闭 B10 已知限制①的计数漂移。

### 1.3 黑盒考题：6-LED 一起控制（区别于原 B09 只做 4 个 PL LED）

- 板卡物理事实（已核实，引用 `boards/ALINX_AX7020_v1.0/`）：PL LED 4 个（J16/K16/M15/M14，**active-low，写 0=亮**），PS LED 2 个（MIO0/MIO13，active-low）；UART1 MIO48/49，默认 115200。
- **PL 侧实现路线不限**（AXI GPIO / EMIO 由智能体自主选型）；PS 侧自然走 MIO GPIO。需求文档只写「要什么」与板卡物理事实，不写实现路线（`B11_blackbox_requirement_draft.md`）。
- 判据沿用 B09 硬件门禁风格：LED 模式可观察 + UART 机读 PASS/FAIL + **每个 LED 读回证据**。

## 2. 阶段计划与门禁

### 阶段① 泛化 Skill 重写（零 GPIO 字样）

**交付**：
1. **新框架 Skill 落地**：把 `B11_generalized_skill_design.md` §3 的 S0–S8 通用阶段（需求解析 → 物理事实清单 → 带宽/资源预算 → 架构选型 → 方案提案 → 分域实现 → 一致性验证 → 部署观测 → 判定/恢复）写为新 Skill 主文档 + 阶段文档；每阶段含输入/输出/智能体自主决策范围/用户物理事实/失败恢复入口。**全篇不得出现 GPIO / AXI GPIO / gpio_led / 0x41200000 / GPIO_E2E / LED（考题相关外设名）等字样**（机械扫描 0 命中为门禁）。
2. **新 Skill 目录命名提案**：`skills/zynq_dev/`（推荐；备选 `skills/zynq_framework/`）。命名不含任何外设名，体现「通用 Zynq 工程开发框架」定位。
3. **旧 GPIO Skill 归档方案**（遵守「禁止静默搬迁」纪律——移动必须列入本规划并经用户审核）：
   - 提案 A（推荐）：移至 `docs/development/skill/archive/zynq_gpio_v1/`——`skills/` 下只保留活动 Skill；归档目录保留完整历史证据（SKILL.md + phases/ + appendix，SHA256 记录于归档说明）；与 `Xilinx_Vivado_MCP/skills/` 的 legacy 先例一致。
   - 提案 B：原位改 legacy 命名（如 `skills/legacy_zynq_gpio/`）——改动小，但 GPIO 字样留在活动 skills/ 命名空间，与「去 GPIO 化」目标冲突。
   - 理由与取舍：推荐 A（命名空间干净、证据完整、可追溯）。**二选一由用户拍板**；无论哪种，`test_o6_skill_contract.py` 的路径引用必须同步重映射（见 §3）。

**门禁**：
- 新 Skill 目录机械扫描：GPIO / AXI GPIO / gpio_led / 0x41200000 / GPIO_E2E 等模式 **0 命中**；
- 归档动作走 `git mv` 且归档后 SHA256 记录；
- 全量回归不净减（基线：1369 collected / 1331 passed / 1 skipped / 37 deselected，见 B10 清单 §2）。

**回归要求**：测试不得净减；`test_o6_skill_contract.py`（10 个测试）逐条映射到新框架 Skill 契约测试（同语义：无逃生通道、公开工具集、一致性 fail-closed、marker 非冲突），映射表见 §3。

### 阶段② MCP 去 GPIO 化

**交付 1：`platform_generate` 处置**（细节与勘误记录见 `B11_platform_generate_erratum_draft.md`）
- 推荐方案：**移除注册**——`capabilities.py` 的 `DOMAIN_TOOLS` 删除 `platform_generate`；`dispatcher.py` 删除本地执行器（L531–533、L701–704）与 `_DOMAIN_TOOLS` 项（L111）；`control/domain_runner.py` 删除 input revision 项（L425）、success-stage 项（L445）与终态 Manifest 校验特判（L1098–1114）；`control/execution_gate.py` 删除 stage 特判（L120–121）。
- **阶段机决策点（关键，实现轮次定案，本规划只记录选项）**：`platform_generate` 是当前 `PLATFORM_DESIGN → PL_GENERATE` 的唯一推进者（`_PL_SUCCESS_STAGE` L445）。移除后推进权必须转移，选项：
  - (a) 推荐：原子序列终点 `platform_export_manifest` 承担推进——Manifest 发布即「平台设计完成」，原子语义从「绝不推进 stage」放宽为该终点原子推进；
  - (b) `pl_generate_system_top` 门禁放宽为接受 `PLATFORM_DESIGN`（原子完成后直接生成 system_top）——会破坏 PL_GENERATE 中间态语义与现有 stage 断言；
  - (c) 新增通用 stage-advance 原子（如 `platform_finalize`）——与「最小移除」目标冲突。
  - 注：stage 链属冻结契约（`B04_single_channel_audit.md` §4.3 引用），变更同样走勘误记录。
- `generate_platform` 实现处置：其 Tcl 序列已被 14 原子镜像（`platform_atoms.py` L114「mirrors platform_generate's proven Tcl」），推荐与工具一并移除（避免死代码）；实现轮次确认无其他引用后定案。

**交付 2：`evaluate_observation` 默认 marker 措辞清理**
- `domains/verification/observation.py` L73–74 默认参数 `GPIO_E2E_PASS/FAIL` → 改为**必填参数**（或通用默认），docstring 决策规则（L8–12）去掉 GPIO 字样；`dispatcher.py` L1120 注释同步；`capabilities.py` L261 注释同步。

**交付 3：工具描述/注释去 GPIO 化**（按附录 §4 扫描清单逐条处置，见 §4）

**交付 4：capability 常量修正（关闭 B10 已知限制①）**
- 机械统计：101 → **100**（9 control + 91 domain；platform 15→14，pl 27，ps 48，verification 2）；
- `DOMAIN_APIS_IMPLEMENTED`：推荐改为**机械派生**（`len(DOMAIN_TOOLS)`）或按实际注册数修正注释（当前常量注释分项加总 91 与实际 92 差 1，为 B10 已知限制①的一半）；ps `implemented` 47→48（①的另一半）。

**门禁**：
- 全量回归不净减（1369 collected 基线不变或按测试处置说明一致调整）；
- 5 处 `==101` 计数断言（§3 清单）全部更新为 100 并通过；
- 新框架 Skill 的 6-LED 流程可经公开 MCP **全原子**跑通（阶段③实测）。

**回归要求**：受影响测试 → 替代测试一一映射（§3）。

### 阶段③ Agent1 白盒自测

**交付**：Agent1 仅用新框架 Skill + 6-LED 需求文档 + 公开 zynq_mcp 实现完整流程（Platform 原子序列 → PL 构建 → PS 软件 → 一致性 → JTAG 部署 → 观测判定），自证「Skill 零 GPIO 字样仍可完成 GPIO 考题」。
**门禁**：全部 EDA/构建/部署/观测经公开 MCP；UART 机读 PASS；LED 模式可观察（硬件或仿真证据）；**每个 LED 读回证据**；故障注入至少覆盖读回失败路径（FAIL marker）。
**回归**：全量回归 + 阶段①②变更的测试映射核对。

### 阶段④ Agent3 阶段黑盒

**交付**：全新上下文 Agent3 仅凭「新框架 Skill + 需求文档 + 板卡配置说明 + 公开 zynq_mcp」在隔离工作目录复现 6-LED 流程。
**门禁**：零 shell、全公开 MCP、Execution Ledger 全覆盖（沿用 B09 硬门禁）；阶段判定 PASS / AWAITING USER REVIEW。

### 阶段⑤ 用户硬件确认

**交付**：真板硬件现象确认（6 个 LED 模式交替可观察、UART 机读 PASS）。
**门禁**：用户确认 + 证据归档（UART 捕获、LED 照片/录像或用户书面确认、Manifest/产物 SHA256）。

### 阶段⑥ Agent2 终验黑盒（B11 验收）

**交付**：全新无记忆 Agent2，仅凭「新框架 Skill + 需求文档 + 公开 zynq_mcp」在隔离环境完成 P1–P6 全链路。
**门禁（沿用 B09/O7 R3 硬门禁，逐条列）**：
1. 黑盒智能体的 EDA、构建、Manifest、部署与观测操作全部通过公开 `zynq_mcp` tools；
2. 不导入 `mcps.zynq_mcp.*` 内部模块，不直接启动 Vivado/XSCT/Tcl，不手工执行 `make`，不直接调用内部 Manifest publisher（零 shell）；
3. 全部长任务均由 Execution Ledger 提供真实状态、PID/身份、心跳、期限和恢复证据（全覆盖）；
4. Consistency 通过（三 Manifest + board profile 一致）；
5. UART 机读判定：`LED_E2E_PASS`（无 `LED_E2E_FAIL`）+ 6 LED 逐位读回证据；
6. 修复后必须使用**全新无记忆 Agent2 会话**重新验收（任何阶段②/③修复都触发重验）；
7. 终态 `PASS / AWAITING USER REVIEW`，用户审核后按流程收尾（B11 不自行冻结）。

## 3. 测试不得净减：旧测试 → 替代测试映射（机械清单）

**原则（项目纪律）**：任何删除/重命名测试必须给出旧→替代一一映射与等价性说明；全量回归数量不得净减；机械统计（collected/passed/skipped/xfail）如实报告。

| 受影响测试文件 | 测试数 | 触发变更 | 替代处置 |
|---|---|---|---|
| `tests/test_b05_platform_public.py` | 7（host_live） | platform_generate 移除 | 1→N：list_tools 断言 → 14 原子注册断言；schema/拒绝 → 原子 schema 与门禁；full_success_chain → 原子序列 + 新阶段推进（决策点 (a)/(b)/(c)）；wrong-stage → 新推进工具门禁 |
| `tests/test_b05_platform_component.py` | 19 | generate_platform 移除 | 通用化保留：错误类型/顶层 BD 选择/板包解析等保留；`_parse_gpio_address`/`EXPECTED_GPIO_ADDRESS` 等 GPIO 专属断言删除或改为通用地址解析测试（等价映射声明） |
| `tests/test_o6_skill_contract.py` | 10 | Skill 归档 + 工具集变更 | SKILL_ROOT 指向新框架 Skill；required 工具集去掉 `platform_generate`；marker 断言（L125–127）改为新考题 marker（`LED_E2E_*`）；逃生通道扫描语义不变 |
| `tests/test_pl_bridge.py` | 65（其中 1 处） | 计数 101→100 | L951 `len(ALL_TOOLS)==101` → `==100`（注释同步） |
| `tests/test_r3_runner.py` | 45（其中 1 处） | 同上 | L757–760 断言与 docstring 计数序列更新为 100 |
| `tests/test_r2_adapter.py` | 35（其中 1 处） | 同上 | L756 `total_tools==101` → `==100` |
| `tests/test_r1_mcp_sdk.py` | 7（其中 1 处） | 同上 | L106–112 计数注释与断言 → 100 |
| `tests/test_r3_1c_public.py` | 27（其中 1 处） | 同上 | L247 `len(names)==101` → `==100` |
| `tests/test_observation.py` | 20 | 默认 marker 清理 | 默认参数移除后测试显式传 marker；GPIO_E2E_* → 通用/考题 marker |
| `tests/test_o5_resource_observation.py` | 8 | 考题 marker 变更 | fake serial 数据与 marker 断言改 `LED_E2E_*` |
| `tests/test_o5_public_resource_live.py` | 2（device_live） | 考题工程变更 | GPIO 工程路径与 `GPIO_E2E_PASS` → 6-LED 工程与 `LED_E2E_PASS` |
| `tests/test_build_manifest.py` | 10 | fixture 审视 | axi_gpio 地址映射作合法示例可保留；0x41200000 固定值在阶段②统一审视（通用化措辞） |
| `tests/test_consistency_check.py` | 19 | fixture 审视 | 同上（axi_gpio address map 示例） |

合计：受影响文件 13 个；其中 platform_generate 直接相关 2 文件（26 测试）+ O6 契约 1 文件（10 测试）+ 计数断言 5 文件（5 处断言）+ marker/默认值 3 文件（30 测试）+ fixture 审视 2 文件（29 测试，预计零删除）。任何一条替代映射若导致测试数减少，须先提交等价性证明并阻塞提交（项目纪律）。

## 4. GPIO 残留扫描清单（附录：`文件:行` + 处置建议）

扫描范围：`mcps/zynq_mcp/` 生产代码（含 `control/capabilities.py` 工具描述）。模式：`GPIO / AXI GPIO / GPIO_E2E / axi_gpio / gpio_led / 0x41200000 / GPIO_ONLY / LED`。结果分类：**A 工具描述与 schema 注释 / B 生产代码注释与常量 / C 测试文件残留（见 §3）**。

### A. 工具描述与 schema 注释（capabilities.py，5 处）

| # | 位置 | 残留内容 | 处置建议 |
|---|---|---|---|
| A1 | `capabilities.py:35` | `platform_generate` 描述「PS7 + AXI GPIO」 | 随工具移除（阶段②交付 1） |
| A2 | `capabilities.py:61` | `ps_load_hardware` 描述「AXI GPIO etc. are invisible」 | 通用化措辞：改「PL peripherals」或删示例 |
| A3 | `capabilities.py:261` | 注释「GPIO_E2E_* tokens in domains/verification/observation.py」 | 随默认 marker 清理更新（阶段②交付 2） |
| A4 | `capabilities.py:323` | `platform_connect_reset` 示例 `targets=['axi_gpio_led/s_axi_aresetn']` | 示例通用化（标注「示例」或换非外设名示例） |
| A5 | `capabilities.py:328` | `platform_set_address` 示例 `segment format 'axi_gpio_led/S_AXI'` | 同上 |

### B. 生产代码注释与常量（13 处 + 1 保留项）

| # | 位置 | 残留内容 | 处置建议 |
|---|---|---|---|
| B1 | `dispatcher.py:111` | `_DOMAIN_TOOLS` 含 `platform_generate` | 移除（阶段②交付 1） |
| B2 | `dispatcher.py:531–533` | `_make_platform_generate_fn` 本地执行器 | 移除 |
| B3 | `dispatcher.py:701–704` | `platform_generate` dispatch 分支 | 移除 |
| B4 | `dispatcher.py:1042` | 注释（900s 上限为 platform_generate BD 综合而设） | 移除/更新 |
| B5 | `control/domain_runner.py:425` | input revision 字段 `platform_generate` | 移除/转移（阶段机决策点） |
| B6 | `control/domain_runner.py:445` | success-stage `PL_GENERATE` | 移除/转移（阶段机决策点） |
| B7 | `control/domain_runner.py:1098–1114` | platform_generate 终态 Manifest 校验特判 | 移除/转移（决策点 (a) 时并入 export_manifest 原子语义） |
| B8 | `control/execution_gate.py:120–121` | `platform_generate` stage 特判 | 移除/转移（决策点 (a)/(b)/(c)） |
| B9 | `domains/platform/platform_domain.py`（整文件多处） | GPIO 硬编码：L112 `add_axi_gpio`、L214 `EXPECTED_GPIO_ADDRESS=0x41200000`、L219 `_parse_gpio_address`、L316–357 AXI GPIO BD 步骤、L377–388 GPIO 地址验证、L507–511 `address_map.axi_gpio_led` | generate_platform 与工具一并移除（其序列已被原子镜像）；保留的公共错误类型/板包解析等按需迁往 atoms 层 |
| B10 | `domains/platform/platform_atoms.py:333,359,384,508–510` | docstring 示例 `axi_gpio_led` / `0x41200000`（`EXPECTED_GPIO_ADDRESS` 引用） | 示例通用化（axi_gpio 作为合法 Catalog IP 示例可保留；固定地址示例改为非 GPIO 或标注示例） |
| B11 | `domains/ps/target_control.py:198` | 注释「GPIO at 0x41200000」 | 通用化措辞（可选，纯注释） |
| B12 | `domains/pl/pl_bridge_tools.py:868` | 注释「e.g. GPIO-only」 | 通用化措辞（可选） |
| B13 | `domains/verification/observation.py:9–10,73–74` | 默认 marker `GPIO_E2E_PASS/FAIL` + docstring | 必填参数化 + 去 GPIO 字样（阶段②交付 2） |
| B14 | `domains/ps/ps_bsp.py:278` | 注释引用 platform_generate 发布 XSA | 措辞更新（语义保留：XSA 同文件幂等导入） |
| B15 | `adapters/xsct/templates.py:173` | `platform_generate()` = XSCT Tcl「platform generate」 | **保留**——Vitis platform 构建模板，与 Vivado BD `platform_generate` 工具同名无关，供 ps_bsp 使用；仅需在注释中注明区别 |

### 统计

- A 类（工具描述/schema 注释）：**5 处**；B 类（生产代码注释与常量）：**14 处 + 1 保留项（B15）**；C 类（测试文件）：13 个文件（见 §3，其中 5 处计数断言 + 30 测试 marker/默认值 + 26 测试 platform_generate 直接相关 + 29 测试 fixture 审视）。
- 全部处置在阶段①/②执行，本规划只记录清单与建议，不代为实现。

## 5. platform_generate 移除影响面与 1→N 等价映射概要

### 5.1 工具语义 1→N

`platform_generate {}`（1 个无参数快捷工具，内部硬编码 GPIO BD）→ 替代为原子序列（B05-R2 14 原子中与平台生成相关的 10 步；B11 考题由智能体按需求选型配置，这正是「去特化」的本质）：

| # | 原子 | 对应 platform_generate 内部步骤（`platform_domain.py` `_PLATFORM_STEP_BY_LABEL`） |
|---|---|---|
| 1 | `platform_create_design` | create_project / create_design / create_bd |
| 2 | `platform_add_ps7` | create_ps7 / ps7_automation / source_ps7_preset / apply_preset |
| 3 | `platform_configure_ps7` | configure_ps7（固定 GPIO 配置 → 需求化配置） |
| 4 | `platform_add_ip` | add_axi_gpio / add_reset / add_smartconnect（IP 按需求） |
| 5 | `platform_connect_interface` / `platform_connect_clock` / `platform_connect_reset` | connect_axi / connect_clocks / connect_resets / gpio_external |
| 6 | `platform_set_address` | assign_address / get_addr |
| 7 | `platform_validate` | validate_bd / save_bd |
| 8 | `platform_generate_wrapper` | generate_target / make_wrapper / add_wrapper_to_project |
| 9 | `platform_export_hardware` | synthesize / synth_status / open_synth_run / export_xsa / vivado_version（XSA 导出） |
| 10 | `platform_export_manifest` | 生成/发布 Platform Manifest |
| 11 | （阶段机推进，决策点 (a)/(b)/(c)，见阶段②） | —（原由 platform_generate 完成后自动推进） |

等价性论证要点：原子序列逐 Tcl 步骤镜像 platform_generate 已验证序列（`platform_atoms.py` L114 注释），产物契约（XSA + wrapper + Manifest + address_map）不变；差异是**配置由需求驱动而非固定 GPIO**，且每个原子独立可观测、可恢复（符合 MCP 原子纪律 P3）。

### 5.2 测试影响面（数量）

- 直接相关：`test_b05_platform_public.py` 7 tests（host_live）+ `test_b05_platform_component.py` 19 tests；
- 计数断言：5 处 `==101` → `==100`（`test_pl_bridge` / `test_r3_runner` / `test_r2_adapter` / `test_r1_mcp_sdk` / `test_r3_1c_public`）；
- O6 契约：`test_o6_skill_contract.py` 10 tests（required 工具集 + SKILL_ROOT + marker）；
- marker/默认值：`test_observation.py` 20 + `test_o5_resource_observation.py` 8 + `test_o5_public_resource_live.py` 2；
- fixture 审视：`test_build_manifest.py` 10 + `test_consistency_check.py` 19。

### 5.3 能力影响

- 移除后公开工具数 101 → 100；platform 域 15 → 14；verification/ps/pl/control 计数不变；
- `ps_load_hardware`（注册 PL 内存映射）与 `pl_generate_system_top`（wrapper → system_top）不受影响；
- 6-LED 考题中 PL 侧若选 AXI GPIO，则 `platform_add_ip`（vlnv `xilinx.com:ip:axi_gpio:2.0`）+ 连线原子完全覆盖；若选 EMIO，则经 PS7 配置 + 顶层端口（PL 域）实现——两条路线均不依赖 platform_generate。

## 6. 与既有文档的关系

- `B11_generalized_skill_design.md`：**保留**，定位为阶段①设计基础（本文档 §2 阶段①引用其 §2/§3/§5）。
- `B11_data_acquisition_proposal.md`：标题与引言已改述为「验证实例候选：数据采集（非当前立项对象）」，其内容作为未来实例立项输入。
- `docs/brick_development_plan.md` / `docs/architecture_ai_zynq7020.md`：**不在本轮修改**；B11 立项后由后续轮次按流程更新。

## 7. DRAFT 声明

- 本文档为 **DRAFT**，待用户审核；不写 FROZEN/COMPLETE，不声称已立项。
- 阶段计划、门禁、映射表、决策点均为规划草案；阶段②的 stage 推进决策点与 generate_platform 处置在实现轮次出最小设计并经审核后定案。
- 未修改任何代码、测试、skill、boards、冻结架构文档；`mcps/`、`skills/`、`boards/`、`docs/architecture_ai_zynq7020.md`、`docs/brick_development_plan.md`、README、CLAUDE.md、三个 legacy 目录零改动。
- 未运行 pytest、未启动 EDA、未碰硬件。引用的行号/数量来自 read/grep 机械统计（本会话实测）。
