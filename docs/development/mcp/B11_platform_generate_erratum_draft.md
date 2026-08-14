# B11 勘误草案：B05 冻结资产 `platform_generate` 处置（DRAFT）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 17:19 +08:00）
> 状态：**DRAFT — 勘误草案，待用户审核。本文档只记录处置方案，不执行任何代码/测试/资产变更，不更新任何冻结清单。**
> 性质：按项目勘误纪律（「发现冻结内容确有缺陷时，标记为 Erratum，说明影响和最小修改范围，不得顺手重构」）记录 B05 冻结资产 `platform_generate` 的去特化处置。
> 配套：`docs/development/mcp/B11_plan.md` 阶段②（处置执行计划）、`docs/development/tests/B11_blackbox_requirement_draft.md`（6-LED 考题）。

## 1. 勘误对象与授权

- **对象**：`mcps/zynq_mcp/control/capabilities.py` 中注册的公开工具 `platform_generate`（B05 交付，无参数快捷路径，内部硬编码「PS7 + AXI GPIO」Block Design 生成 + XSA/Manifest 导出），及其实现 `mcps/zynq_mcp/domains/platform/platform_domain.py` 的 `generate_platform`。
- **冻结状态**：`platform_generate` 属 B05 冻结范围；`capabilities.py` 与 `platform_domain.py` 均在 B10 冻结资产清单内（`docs/development/mcp/B10_freeze_manifest.md` §3：`capabilities.py` SHA256=`d5a1732658d8536926ee812619540018d27d75b8918e3b91d85676f218d61e35`，`platform_domain.py` SHA256=`c7383dcdb307dc7c94dc508cfb2f431f224e3e5251b054bc0f535259bdabd96b`）。实现后两文件 SHA256 将变更，届时更新冻结记录（本草案不更新）。
- **授权**：用户已明确授权对 `platform_generate` 的处置（B11 方向重定：「Skill 和 MCP 都彻底去 GPIO 化（用户明确授权处置 B05 冻结资产 platform_generate，需走勘误记录）」）。

## 2. 勘误理由（去特化）

`platform_generate {}` 是 GPIO 纵向切片的**特化快捷路径**：无参数、固定生成「PS7 + SmartConnect + 4-bit AXI GPIO」、固定地址 `0x41200000`、固定 4-bit 外部端口（`platform_domain.py` L316–357、L377–388、L507–511）。这与 B11 目标（Skill + MCP 面向任意 Zynq 工程、工具不得绑定具体外设）直接冲突：它既是 MCP 层的 GPIO 残留，也诱导 Skill 继续走「一键配方」而非「通用阶段 + 原子组合」。B05-R2 已交付 14 个平台原子（其 Tcl 序列镜像 platform_generate 的已验证序列，`platform_atoms.py` L114 注释），因此该快捷工具可由原子序列等价替代。

## 3. 影响范围

### 3.1 生产代码（移除/修改点，全部在阶段②执行）

| 文件 | 变更 |
|---|---|
| `control/capabilities.py` | 删除 `DOMAIN_TOOLS` 中的 `platform_generate`（L35）；`platform` 域 implemented 15→14；`DOMAIN_APIS_IMPLEMENTED` 改机械派生或修正注释（关闭 B10 已知限制①漂移）；ps implemented 47→48；A2/A3/A4/A5 去 GPIO 措辞 |
| `dispatcher.py` | 删除 `_DOMAIN_TOOLS` 项（L111）、`_make_platform_generate_fn`（L531–533）、dispatch 分支（L701–704）、相关注释（L1042） |
| `control/domain_runner.py` | 删除 input revision 字段（L425）、success-stage 项（L445）、终态 Manifest 校验特判（L1098–1114）；按阶段机决策点转移推进语义 |
| `control/execution_gate.py` | 删除 `platform_generate` stage 特判（L120–121）；按阶段机决策点调整 |
| `domains/platform/platform_domain.py` | 移除 `generate_platform` 及其 GPIO 硬编码（L112/L214/L219/L316–357/L377–388/L507–511）；公共错误类型/板包解析等按需迁往 atoms 层（最小修改，不重构） |
| `domains/verification/observation.py` | 默认 marker `GPIO_E2E_PASS/FAIL`（L73–74）改必填参数；docstring（L8–12）去 GPIO 字样 |
| `domains/ps/ps_bsp.py` | L278 注释措辞更新（语义保留） |
| `domains/platform/platform_atoms.py` / `domains/ps/target_control.py` / `domains/pl/pl_bridge_tools.py` | docstring/注释示例通用化（`B11_plan.md` §4 B10–B12） |
| `adapters/xsct/templates.py` | **不改**（`platform_generate()` 为 XSCT Vitis platform generate 模板，与 Vivado BD 工具同名无关，属 ps_bsp 依赖；在注释中注明区别即可，B15） |

### 3.2 测试（处置映射见 `B11_plan.md` §3，数量统计见 §5 下方）

直接相关 26 测试（`test_b05_platform_public.py` 7 host_live + `test_b05_platform_component.py` 19）；计数断言 5 处（101→100）；O6 契约 10 测试；marker/默认值 30 测试；fixture 审视 29 测试（预计零删除）。

## 4. 替代映射 1→N（工具语义等价）

`platform_generate {}` → 原子序列（等价性论证见 `B11_plan.md` §5.1）：

```
platform_create_design → platform_add_ps7 → platform_configure_ps7
→ platform_add_ip（外设 IP，按需求选型；不再是固定 axi_gpio）
→ platform_connect_interface / platform_connect_clock / platform_connect_reset
→ platform_set_address → platform_validate → platform_generate_wrapper
→ platform_export_hardware → platform_export_manifest
→ [阶段机推进：决策点 (a)/(b)/(c)，见 §6]
```

产物契约不变：XSA + BD wrapper + Platform Manifest（revision/SHA256/address_map 语义一致）；差异为「配置由需求驱动」+「每原子独立可观测、可恢复」。

## 5. 回归计划

1. 全量回归基线：1369 collected / 1331 passed / 1 skipped / 37 deselected（B10 清单 §2）；阶段②后**不净减**（含替代映射后的新增/等价测试）。
2. 5 处 `==101` 断言 → `==100` 并机械核对 `list_tools`/`get_capabilities` 输出。
3. `platform_domain.py`/`capabilities.py` 变更后 SHA256 重算并更新冻结清单（由实施轮次在正式勘误关闭时完成）。
4. 新框架 Skill 的 6-LED 流程经公开 MCP 全原子跑通（阶段③）作为行为等价证据。
5. 冻结纪律核对：仅按本勘误清单的最小范围修改，不顺手重构；其他冻结资产（`.mcp.json`、`skills/zynq_gpio/SKILL.md` 等）零变化。

## 6. 阶段机（stage machine）影响与决策点

- 现状：`PLATFORM_DESIGN --platform_generate--> PL_GENERATE --pl_generate_system_top--> PL_BUILD`（`_PL_SUCCESS_STAGE` L445，冻结于 `B04_single_channel_audit.md` §4.3）。
- 移除后推进权转移选项（**本草案不代为实现决策，实现轮次出最小设计经审核后定案**）：
  - (a) 推荐：`platform_export_manifest`（原子序列终点）承担推进，原子语义从「绝不推进 stage」放宽为该终点原子；
  - (b) `pl_generate_system_top` 门禁放宽为接受 `PLATFORM_DESIGN`；
  - (c) 新增通用 stage-advance 原子。
- stage 链属冻结契约，其变更随本勘误一并记录、一并审批。

## 7. 勘误流程（待用户审核后执行）

1. 用户审核本勘误草案与 `B11_plan.md`；
2. 阶段②实现轮次按 §3 清单执行最小修改，附测试映射表与全量回归机械统计；
3. 阶段③白盒 / 阶段④黑盒验证等价行为；
4. 用户确认后关闭勘误：更新冻结记录（SHA256）、发布勘误关闭说明（对齐 B09 契约勘误流程）。

## 8. DRAFT 声明

- 本文档为 **DRAFT 勘误草案**，待用户审核；不写 FROZEN/COMPLETE，不声称已批准/已执行。
- 未修改任何代码、测试、skill、boards、冻结文档；未运行 pytest、未启动 EDA、未碰硬件。
- 行号/数量来自本会话 read/grep 机械统计；与 `B11_plan.md` §2/§3/§4/§5 保持一致。
