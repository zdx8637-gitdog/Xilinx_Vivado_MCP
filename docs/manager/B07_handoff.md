# B07 收口交接文档

> 日期: 2026-08-10
> 从: Manager Reviewer (long-context session)
> 到: 下一个 Manager Reviewer / Agent1 (B08 白盒验收)

---

## 1. 工作进度总览

```
B00 ✅ 项目盘点
B01 ✅ 标准流程
B02 ✅ 公共契约  
B03 ✅ 板卡配置
B04 ✅ 统一入口 + 执行账本 + PL Bridge
B05 ✅ Platform Domain (15 tools, Agent3 42/42)
B06 ✅ PS Domain (47 tools, 黑盒 3/5 PASS + 2 SKIP)
B07 ✅ GPIO Workflow Skill + E2E 验证 ← 当前
B08 ⬜ Agent1 白盒验收
B09 ⬜ Agent2 黑盒复现
B10 ⬜ GPIO v1 冻结
```

## 2. MCP 最终状态

| 指标 | 值 |
|------|:--:|
| `list_tools` | **101** |
| `domain_apis_implemented` | 92 |
| 全量回归 | **1178 passed, 0 failed** |
| P4 验证 | 12/12 passed, 0 skipped |

### 工具分布

| 域 | tools |
|----|:--:|
| control | 9 |
| platform | 15 |
| pl | 26 |
| ps | 47 |
| verification | 4 |
| **合计** | **101** |

## 3. 关键交付物

### Skill 文档

`skills/zynq_gpio/` — 8 个文件，716 行：
- `SKILL.md` — 入口：需求模板、Phase 概览、工具前缀约定、长任务轮询规则、**对话丢失恢复机制**
- `phases/0_board_profile.md` → `phases/7_debug_recovery.md` — 每个 Phase 的完整 Skill 决策 + MCP 调用序列

### E2E Runner（workspace）

`workspaces/gpio_e2e_20260809/`：
- `run.py` — Step 1 (P1+P2a) + Step 3 (P3-P6)，MCP SDK 调用
- `run_p2.py` — Step 2，VivadoTclBridge 独立综合
- `README.md` — E2E 运行记录 + 8 个 Issue 发现

### 生产代码关键修复

| 修复 | 文件 | 说明 |
|------|------|------|
| VivadoTclBridge | `adapters/vivado/vivado_bridge.py` (新建) | 直接 `vivado.exe -mode tcl`，取代旧 MCP 两层 stdio |
| write_bitstream 静默 | `pl_bridge_tools.py` | 加 `puts BIT_DONE` |
| WNS 解析 | `pl_bridge_tools.py` | `pl_analyze_timing` 从 bridge eval 输出解析 WNS |
| ps7_init source | `target_control.py` | `ps_initialize_ps` 接受 `tcl_path` 参数 |
| ps_load_hardware | `target_control.py` (新增) + 注册 | XSDB `loadhw $xsa` |
| ps_ensure_arm_accessible | `target_control.py` (新增) + 注册 | DAP 上电恢复 `rst -system` |
| PL stage 链 | `domain_runner.py` | synth→PL_IMPLEMENT, route→PL_TIMING, timing→PL_BITSTREAM, bitstream→PS_BUILD |
| PS 路由修复 | `dispatcher.py` | 9 个第三批 tools 加入 `_PS_TOOL_NAMES` |
| Build Manifest 自动生成 | `verification/build_manifest.py` (新建) | P2/P3 成功后自动写 manifest |
| Manifest 缺失→failed | `consistency_check.py` | 原来 skipped，现在 failed |

## 4. 工作模式（操作手册）

### 开发流程

```
发现问题 → 调研（读代码/查 Zynq 规范/跑独立测试）
         → 设计修复方案
         → 如果有独立块 → 发多个 subagent 并行
         → 回归验证（1178 tests, 0 failed 才放行）
         → E2E 验证（全清环境重跑）
         → 将发现融入 Skill 文档
```

### 子代理使用

所有子代理用 **sonnet** 模型。`run_in_background: true` 推后台，等待通知。

**给子代理的 prompt 结构**：
1. 先读哪些文件（按顺序列）
2. 背景（问题是什么）
3. 具体改动要求（文件+内容）
4. 验证命令（必须跑回归）
5. 规则（不能碰哪些文件）

### 并行策略

修改不同文件的 Agent 可以并行发。修改同一文件（capabilities.py/dispatcher.py）时需要顺序——等前者完成后手动合并。

### P2 Build 进程分离

**关键约束**：P2（Vivado 综合/布局/布线/bitstream）不能和 MCP session 在同一个进程。VivadoTclBridge 的 `stop()` 会干扰 MCP stdio transport。所以 E2E 是三步：
1. MCP session → P1 + P2a → close
2. VivadoTclBridge standalone → P2 build
3. 新 MCP session → P3-P6

### Skill 维护纪律

每次 E2E 发现新问题，先记录到 `README.md` 的 Issues Found，然后分类：
- **Skill gap** → 融入 Phase 文档
- **已修复** → 不需要融入
- **runner 特有问题** → 不需要融入

## 5. 已知遗留

| 项 | 严重度 | 说明 |
|----|:--:|------|
| 4 个仿真 tools 仍走旧 MCP | P2 | 标记 DEFERRED |
| template 字符串漂移 | P2 | 7 个测试已修复 |
| `_debug_sessions` 在库模块中 | P2 | 集成阶段应迁入 Ledger |
| Runner 是手脚架，不是产品 | — | B07 Skill 才是产品 |

## 6. B08 进入条件

B08 是 Agent1（白盒）用 B07 Skill 自主完成 GPIO 项目。

**前提条件**：
- [x] Skill 文档完整（7 Phase + context recovery）
- [x] 所有 MCP tools 已验证（101 tools）
- [x] E2E 全链路通过（12/12 P4 + PASS verdict）
- [ ] `main.c` 需修改——当前只输出 LED pattern 到 UART，不读回 GPIO 值。需要加 `Xil_In32` readback 来证明 GPIO 通路。这个**应该是 B08 执行内容的一部分**（AI 读 Phase 3 UART 规范 → 发现不满足 → 自己改）
- [ ] Agent1 prompt 需准备——不要给 runner 脚本，只给 Skill 目录 + MCP 连接信息 + 需求描述

## 7. 下一位 Reviewer 快速入门

```bash
# 验证当前基线
cd D:/fpgaproject && python -m pytest mcps -q -m "not host_live and not device_live"
# 预期: 1178 passed, 0 failed

# 查看 Skill
cat skills/zynq_gpio/SKILL.md

# 查看 E2E 证据
cat workspaces/gpio_e2e_20260809/README.md
ls workspaces/gpio_e2e_20260809/evidence/

# 工具数量
python -c "from mcps.zynq_mcp.control.capabilities import ALL_TOOLS; print(len(ALL_TOOLS))"
# 预期: 101
```
