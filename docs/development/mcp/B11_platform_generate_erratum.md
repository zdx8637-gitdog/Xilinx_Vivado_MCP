# B11 勘误完成记录：B05 冻结资产 `platform_generate` 处置（COMPLETE）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 18:24 +08:00）
> 状态：**COMPLETE — 阶段②已执行并关闭**（MCP 去 GPIO 化的 `platform_generate` 处置勘误完成记录；原草案全文并入 §10「草案历史」后 `git rm` 删除）
> 性质：按项目勘误纪律（「发现冻结内容确有缺陷时，标记为 Erratum，说明影响和最小修改范围，不得顺手重构」）记录并执行 B05 冻结资产 `platform_generate` 的去特化处置。
> 配套：`docs/development/mcp/B11_plan.md` 阶段②（处置执行计划，含阶段②完成记录）、`docs/development/tests/B11_blackbox_requirement_draft.md`（6-LED 考题）。

## 1. 用户授权摘录

- 用户已明确授权对 `platform_generate` 的处置（B11 方向重定：「Skill 和 MCP 都彻底去 GPIO 化（用户明确授权处置 B05 冻结资产 platform_generate，需走勘误记录）」）。
- 阶段②执行前，用户已批准本勘误草案与 `B11_plan.md` 阶段②计划；**阶段机决策点 (a)**（`platform_export_manifest` 承担推进权）由用户默认批准定案。

## 2. 勘误理由（去特化）

`platform_generate {}` 是 GPIO 纵向切片的**特化快捷路径**：无参数、固定生成「PS7 + SmartConnect + 4-bit AXI GPIO」、固定地址 `0x41200000`、固定 4-bit 外部端口（原 `platform_domain.py` L316–357、L377–388、L507–511）。这与 B11 目标（Skill + MCP 面向任意 Zynq 工程、工具不得绑定具体外设）直接冲突。B05-R2 已交付 14 个平台原子（其 Tcl 序列镜像 platform_generate 的已验证序列），因此该快捷工具由原子序列等价替代，注册与实现一并移除（避免死代码）。

## 3. 变更文件清单（修改前后 SHA256 每文件）

SHA256 用 `Get-FileHash -Algorithm SHA256` 计算（文件无 BOM，与 B10 冻结清单口径一致）。生产代码 12 个文件 + 测试 11 个文件 + 文档（本记录）。

### 3.1 生产代码（12 文件）

| 文件 | 修改前 SHA256 | 修改后 SHA256 | 变更摘要 |
|---|---|---|---|
| `control/capabilities.py` | `d5a1732658d8536926ee812619540018d27d75b8918e3b91d85676f218d61e35` | `24a9957fdb858a1cd93e3a02bc825679bb74c05aa2d9e067eb426341771f4610` | 删除 `platform_generate` 注册（A1）；`DOMAIN_APIS_IMPLEMENTED` 改机械派生 `len(DOMAIN_TOOLS)`；platform implemented 15→14、ps implemented 47→48（关闭 B10 已知限制①）；evaluate_observation schema required 加 pass_marker/fail_marker（A3）；A2/A4/A5 去 GPIO 措辞；原子注释更新（决策 (a)） |
| `dispatcher.py` | `7843781fc7697898e16f64059029a515573c368f5625d08badcb28c7a8c96fa2` | `85a0a67d34afda168da69793ced1c146d09cf92d2becd3d0fd31aed5b1b7826c` | 删除 `_DOMAIN_TOOLS` 项（B1）、`_make_platform_generate_fn` 本地执行器（B2）、dispatch 分支（B3）、900s 上限注释（B4）；`_evaluate_observation_query` 改为 markers 全量透传（B13 侧） |
| `control/domain_runner.py` | `bb56355e6e7950f3480d1e91d3dbaa3b3f6bb9aefabd8112fff685cc0e4c3850` | `c3fd80e3fcf0db32ba1d2510f4043dab905b9879bb9102fcbef4abcad76a9c65` | 删除 input revision 项（B5）；success-stage 项移除并转移：`platform_export_manifest → PL_GENERATE`（B6，决策 (a)）；删除终态 Manifest 校验特判（B7，校验语义并入 export_manifest 原子） |
| `control/execution_gate.py` | `83d8f7271943faf25869a4b6ceb2b8ed436ca7f650e6a7171ae3ac1ae7b2233b` | `d29d592211922d911715ad2586c73dc97b5526a0cd9530306b152dc58e854ba9` | 删除 `platform_generate` stage 特判（B8）；新增 `platform_export_manifest` 门禁（仅 PLATFORM_DESIGN）与 `platform_generate_wrapper` 门禁（保留原 substring 匹配语义） |
| `domains/platform/platform_domain.py` | `c7383dcdb307dc7c94dc508cfb2f431f224e3e5251b054bc0f535259bdabd96b` | `8fcaf9eb27f9fe0f657c9151533f4c9e2943d006dfb964e2910286561be263ac` | 移除 `generate_platform`（B9）及其 GPIO 硬编码（`EXPECTED_GPIO_ADDRESS`/`_parse_gpio_address`/`add_axi_gpio`/`gpio_external` 步骤与 address_map）；清理因此产生的未用 import；保留公共错误类型/板包解析/`_run_tcl`/`_top_bd_command`/超时常量（atoms 依赖） |
| `domains/platform/platform_atoms.py` | `f1e11fd3f23f5583e1238a508b8d7e1d8a81ff1ddb71b118677e58b9daeb6d90` | `5e8371f8071dde0a948e2c3304fbc40c5831d685cd5e53ff0abaf3e35a8faa83` | docstring/示例通用化（B10）；错误提示去掉已移除工具名；`platform_export_manifest` 返回 `_context_updates={"platform_revision": ...}`（决策 (a) 落实：Manifest 发布即推进） |
| `domains/verification/observation.py` | `fe4e4cccff0ae98ecee4a89077f9634b81ed5861b63d6ba7762d6cf73dffbd56` | `9301621abea8900c3b4e8bc2e952298b21d7a9e7bbfd7a36819c5ce47561dc01` | 默认 marker `GPIO_E2E_PASS/FAIL` 移除（B13）：pass_marker/fail_marker 改必填（缺省即 INVALID_ARGUMENT）；docstring 去 GPIO 字样 |
| `domains/verification/build_manifest.py` | `314cb3a060f2fea0384cdce7accdddf196b109e96b880ee035310f0bd2faa76e` | `841fae08a04aa4cc681c3677a8337cf0a7c0ec779fe3df62fc713807002fd267` | 模块注释中 `platform_generate` 引用更新为 `platform_export_manifest`（B14 邻域） |
| `domains/ps/ps_bsp.py` | `0e25c92025359a2dd6e005fb03bd460f02778ae2c814835e4d0d24746d09e1eb` | `6e97817f53a1647c9fc5a1d368f8545e9d43da4d3436c6c06035636c7701c30e` | B14：L278 注释措辞更新（XSA 同文件幂等导入语义保留） |
| `domains/ps/target_control.py` | `31c0101fff0e0582aa25fe48094a30ae4f4b55b8013d5014f5a8ee632e07adfb` | `207d69113ecddda299130d6d6cda3e8769a812ec598e34db5a5ed1037cb13c50` | B11：`load_hardware` docstring 示例通用化（去 GPIO at 0x41200000） |
| `domains/pl/pl_bridge_tools.py` | `aa00bacf8ebd8dd38841dcedb042fcdb425ef9a1e2bdc1ce00e949ad234fdef9` | `1c3bab8fdc95a92db376907be82672580a111aa00ff8745e9bb101f43744abf8` | B12：L868 注释通用化；L1111 Windows 路径示例去 gpio 字样 |
| `adapters/xsct/templates.py` | `4ce6d88ba32b7577fa51c93948197dcdE1439eb06e86ca87e18fdef681abd721` | `891cfe1b2cdbed3dbaba648fe0d7cf42979b1595ba039f10c51d3400dbb7f4f7` | **B15 保留项**：`platform_generate()`（XSCT Vitis platform generate 模板，供 ps_bsp 使用）不动，仅加注释注明与已移除的 Vivado BD 工具同名无关 |

### 3.2 测试（11 文件）

| 文件 | 修改前 SHA256 | 修改后 SHA256 | 变更摘要 |
|---|---|---|---|
| `tests/test_b05_platform_public.py` | `07cbcbecf22afbdb9920e2b9604593138330adcb0ec2d8035eb11bc708c3bd95` | `97a95c907ebd120ae8f3beaa8fb57195c3526202ec5c5a1806ad20c3c3b54761` | 7→8（host_live）：platform_generate 相关 7 测试 1→N 重映射到原子/新推进工具；新增旧路径 UNKNOWN_TOOL 负路径 |
| `tests/test_b05_platform_component.py` | `b2b81a1c7ebe263f0b399c48b79845207e47c9057b7b57154a9fba6ddc7824a6` | `1fad6bfed4efd58d21e6263e0e1e5cbe60b4cd63832b9034ed23cdb855ba22d6` | 移除 4 个 generate_platform/GPIO 单测，新增 4 个等价原子层测试（净零） |
| `tests/test_o6_skill_contract.py` | `92bafd899f1c27f92bc5d39d023f29468c82d02a2eb9f58878c05c459bc24a80` | `76ccc95ddc69d439b7020ef0705c016f298a34fc51747489537ff97bf41cf374` | required 工具集断言 + `platform_generate not in public_names` + `list_tools == 100` |
| `tests/test_observation.py` | `33a3a4c890427a0413dedbcf396adfb2fcbbf0b4b6842f777a0e1df0cac1ad77` | `1a32f59472a6fa53c7767bb060eeb1c65decde19b3d918f13fd76101ba05b1b5` | 20→22：全部调用显式传 marker；schema required 断言更新；新增缺 marker 负路径 ×2 |
| `tests/test_o5_resource_observation.py` | `048587fe97210ccee5f362e11f1484c8b9880de06da74d4a9b7bbb19a04305c7` | `6fc2001ae3ecadddc52ab7bff0d6411ae0fce0614ac8f5b849d78f34f42162f2` | fake serial 数据与 marker 断言 `GPIO_E2E_PASS` → `LED_E2E_PASS`（8 测试数不变） |
| `tests/test_pl_bridge.py` | `35c07f7417ee0d9ca1df328cc3a5ae4d682aa34d5e2c4627f8b6c93ce52bf0d7` | `a5bfd7d5eb5a329d601f7bc2c1f7095b940d72a6b3e5dd2a8b4eed1fc1b972d1` | `==101` → `==100`（计数断言 ①） |
| `tests/test_r3_runner.py` | `a0c3d648b834c3b38ac2925948cca69cddaa4324a875db29a9f45d063a408b1c` | `5f2db04b1442d4f88237e675523bb18e4cf9bd0e31de31c120776556fdecef25` | `==101` → `==100`（计数断言 ②，docstring 链同步修正） |
| `tests/test_r2_adapter.py` | `d9b19b1c25a27f50e2c490af0be845e23a9ef2a747e4eb39d66b911baac36a6f` | `2cdf3a07f95a65ac3e03b3e4870b21709885dbb189e62a547c664582529616b1` | `==101` → `==100`（计数断言 ③） |
| `tests/test_r1_mcp_sdk.py` | `dbd930fcd72bdfcd2c852dc2f0bd66f336a5356b09e1eefd89a5f858e28a09f9` | `011aad0e18e2343fdb0aa34d979f17de4b1aa9a3be814981d2e3acefb958e25e` | `==101` → `==100`（计数断言 ④，docstring 链同步修正） |
| `tests/test_r3_1c_public.py` | `162d256b65c2dc6a14f80bbf94f21a7861aa25de3b5e9718c97ed29257e78079` | `4871ca1dfa6517b687db685d827e28cbdec6a4ec35b92d3eba2cd5d4cdd40ddd` | `==101` → `==100`（计数断言 ⑤） |
| `tests/test_platform_atoms.py` | `eac8b2c689718b0255f274dd0876430b45f6f3a97b1a2d6eba145de91f8a7139` | `b8825f5b522af9d86a0f5babac5ddcfe2e00ca3595bf37e71f7d1b9ee02e0c87` | 新增 platform_export_manifest 推进 stage 正/负路径测试 ×2 + 旧路径 UNKNOWN_TOOL ×1 |

> 注：`tests/test_o5_public_resource_live.py`（2 个 device_live）**未修改**——其 `GPIO_E2E_*` 是 B09 固件在真实板卡 UART 上打印的标记（`gpio_b09_r3_20260812` 工程产物），且该测试不调用 `evaluate_observation`；改为 `LED_E2E_*` 需待阶段③ 6-LED 工程（其固件打印新标记）落地后执行（见 §8 残留清单 R2）。

## 4. 替代映射 1→N（工具语义等价）

`platform_generate {}`（1 个无参数快捷工具，内部硬编码 GPIO BD）→ 原子序列（等价性论证见 `B11_plan.md` §5.1）：

| # | 原子 | 对应 platform_generate 内部步骤（原 `platform_domain.py` `_PLATFORM_STEP_BY_LABEL`） |
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
| 11 | （阶段机推进，决策 (a)） | —（原由 platform_generate 完成后自动推进） |

产物契约不变：XSA + BD wrapper + Platform Manifest（revision/SHA256/address_map 语义一致）；差异为「配置由需求驱动」+「每原子独立可观测、可恢复」。
**已知行为差异（如实记录）**：原快捷路径内部执行顶层综合（`launch_runs synth_1`）以保证 XSA 含 HDF；原子层无合成原子，`platform_export_hardware` 直接 `write_hw_platform`。此差异为 B05-R2 既有设计，阶段②按最小移除原则不改原子语义；6-LED 全链路是否需补合成步骤由阶段③白盒自测验证并记录。

## 5. 阶段机推进权决策 (a)（冻结契约变更记录）

- 现状（冻结于 `B04_single_channel_audit.md` §4.3）：`PLATFORM_DESIGN --platform_generate--> PL_GENERATE --pl_generate_system_top--> PL_BUILD`。
- **决策 (a)（用户已默认批准）**：移除后推进权转移给原子序列终点 `platform_export_manifest`——「Manifest 发布即平台设计完成」。
  - `control/domain_runner.py` `_PL_SUCCESS_STAGE`：`platform_export_manifest → PL_GENERATE`（删除 `platform_generate → PL_GENERATE`）。
  - `control/execution_gate.py` `_check_stage`：`platform_export_manifest` 仅从 `PLATFORM_DESIGN` 受理（否则 `STAGE_PREREQUISITE_UNMET`），杜绝后置 stage 非法推进；`platform_generate_wrapper` 保留原 PLATFORM_DESIGN 门禁（原为 substring 匹配的既有行为）。
  - `domains/platform/platform_atoms.py` `platform_export_manifest` 成功返回 `_context_updates={"platform_revision": ...}`——旧快捷路径曾负责把 `platform_revision` 写入 context，`pl_generate_system_top` 依赖它绑定 Manifest；该语义随推进权一并转移。
  - 其他原子保持「绝不推进 stage」（next_stage=None）。
- 该变更属冻结契约（stage 链），随本勘误一并记录、一并审批；阶段②后 `PLATFORM_DESIGN → PL_GENERATE` 的**唯一**推进者为 `platform_export_manifest`。

## 6. 测试映射表（旧测试名 → 新测试名/位置，数量对照）

| 旧测试 | 处置 | 新测试/位置 | 数量 |
|---|---|---|---|
| `test_b05_platform_component::TestAddressMap::test_parse_gpio_address` | 等价替换 | `test_parse_manifest_address_map_normalizes_offset`（同文件） | 1→1 |
| `test_b05_platform_component::TestAddressMap::test_parse_no_match` | 等价替换 | `test_parse_manifest_address_map_ignores_partial_lines` | 1→1 |
| `test_b05_platform_component::TestAddressMap::test_expected_address` | 等价替换 | `test_parse_manifest_address_map_keeps_canonical_base` | 1→1 |
| `test_b05_platform_component::TestAdapterRequired::test_no_adapter_raises` | 等价替换 | `test_atom_no_adapter_fails_closed`（atom 层 ADAPTER_NOT_READY） | 1→1 |
| `test_b05_platform_public::TestToolDiscovery::test_list_tools_includes_platform_generate` | 重映射 | `test_shortcut_removed_atoms_registered`（14 原子注册断言） | 1→1 |
| `test_b05_platform_public::TestToolDiscovery::test_schema_empty_object` | 重映射 | `test_export_manifest_schema_is_closed_object` | 1→1 |
| `test_b05_platform_public::TestToolDiscovery::test_public_tool_count` | 更新 | 同函数（42→41，去 platform_generate） | 1→1 |
| `test_b05_platform_public::TestStageRejection::test_rejected_when_stage_is_not_platform_design` | 1→2 | `test_removed_shortcut_is_unknown_tool`（旧路径拒绝）+ `test_export_manifest_rejected_without_session` | 1→2 |
| `test_b05_platform_public::TestStageRejection::test_extra_property_rejected` | 更新 | 同函数（目标工具换 platform_export_manifest） | 1→1 |
| `test_b05_platform_public::TestRealVivadoSuccess::test_full_success_chain` | 重映射 | `test_full_success_chain_atom_sequence`（原子序列 + 决策 (a) 推进断言） | 1→1 |
| `test_b05_platform_public::TestRealVivadoSuccess::test_wrong_stage_after_pl_generate` | 更新 | 同函数（重放目标换 platform_export_manifest） | 1→1 |
| `test_observation.py` 20 测试 | 签名更新 | 全部显式传 marker；新增 `test_missing_markers_error` + `test_dispatcher_fail_closed_on_missing_markers` | 20→22 |
| 5 处 `==101` 计数断言（test_pl_bridge / test_r3_runner / test_r2_adapter / test_r1_mcp_sdk / test_r3_1c_public） | 更新 | `==100` | 5→5 |
| `test_o6_skill_contract::test_skill_public_workflow_tools_are_registered` | 更新 | + `platform_generate not in public_names` + `list_tools == 100` | 1→1 |
| `test_o5_resource_observation.py` 8 测试 | marker 更新 | fake serial/断言 `GPIO_E2E_PASS`→`LED_E2E_PASS` | 8→8 |
| — | **新增** | `test_export_manifest_success_advances_stage`（正路径） | +1 |
| — | **新增** | `test_export_manifest_rejected_when_stage_not_platform_design`（负路径，STAGE_PREREQUISITE_UNMET） | +1 |
| — | **新增** | `test_removed_shortcut_rejected_as_unknown_tool`（旧路径拒绝） | +1 |
| `test_o5_public_resource_live.py` 2（device_live） | **保留**（无变更） | 见 §8 R2（B09 固件 marker 依赖） | 2→2 |

**数量对照**：collected 1370（基线，阶段①后）→ **1376**（阶段②后，+6 = public +1 / observation +2 / platform_atoms +3）；passed 1332 → **1337**；无任何测试净减。

## 7. 回归数字（全部机械实测）

| 项 | 基线（阶段①后） | 阶段②后 | 说明 |
|---|---|---|---|
| `--collect-only` | 1370 | **1376** | 新增 6 测试（+1 host_live 等） |
| 完整非硬件回归 | 1332 passed / 1 skipped / 37 deselected | **1337 passed / 1 skipped / 38 deselected / 0 failed**（202.70s） | passed 与 collected 均上升 |
| host_live 收集数 | 33 | **34** | +1（test_b05_platform_public 新增旧路径拒绝测试） |
| device_live 收集数 | 4 | **4** | 不变 |
| 受影响文件专项 | — | test_observation/component/atoms/o6/o5_resource 5 文件 **123 passed**；计数/门禁 9 文件 **242 passed** | 阶段②执行中两次专项 |

**闭合校验**：1337 + 1 + 38 = 1376 = collected ✓；38 deselected = 34 host_live + 4 device_live ✓。

## 8. 残留清单（生产代码扫描 `(?i)gpio|0x41200000|\bLED\b`，不得默默保留）

扫描范围：`mcps/zynq_mcp/` 生产代码（`*.py` 不含 tests，PowerShell `Select-String` 机械扫描）。**R1 类（历史注释，标注「历史记录」保留）**：

| 文件:行 | 残留内容 | 保留理由 |
|---|---|---|
| `control/capabilities.py:258` | 「B01 GPIO_E2E_* defaults…were removed」 | 移除记录注释（B11 phase 2） |
| `domains/platform/platform_domain.py:211-216` | 「generate_platform (hard-coded PS7 + AXI GPIO…0x41200000) was removed」 | 移除记录注释（勘误链接） |
| `domains/verification/observation.py:9,91` | 「B01 GPIO_E2E_* defaults were removed」「no GPIO default」 | 移除记录注释（docstring） |
| `dispatcher.py:1058` | 「no GPIO_E2E_* defaults any more」 | 移除记录注释（query handler docstring） |

**R2 类（保留项，理由各注）**：

| 文件:行 | 残留内容 | 保留理由 |
|---|---|---|
| `adapters/xsct/templates.py:173-179` | `platform_generate()`（XSCT「platform generate」） | **B15 保留**——Vitis platform 构建模板，供 `ps_bsp.create_bsp` 使用；与已移除的 Vivado BD 工具同名无关，已加注释注明 |
| `domains/platform/LEGACY_COMPARISON.md`（全文） | GPIO/axi_gpio/0x41200000 等 | 历史对比文档（非生产代码；B05 旧实现 vs legacy G10/G11 的对比记录，属证据文档） |
| `domains/verification/observation.py`（docstring 决策规则） | pass_marker/fail_marker 语义 | 已无 GPIO 字样（仅 §R1 移除记录） |
| `tests/` 若干文件（test_o5_public_resource_live、test_build_manifest、test_consistency_check、test_platform_atoms 等） | `GPIO_E2E_*` / `axi_gpio` / `0x41200000`（fixture/固件 marker） | C 类测试残留，按 §3/§6 处置清单：fixture 中 axi_gpio 为合法 Catalog IP 示例（`B11_plan.md` §4 B10 允许）；`test_o5_public_resource_live` 的 `GPIO_E2E_*` 为 B09 固件真实 marker，阶段③ 6-LED 工程落地后重映射为 `LED_E2E_*` |

**templates.py:173 之外的生产代码 `platform_generate` 字样**：仅 `platform_generate_wrapper` 原子（合法工具名）、`templates.platform_generate()` XSCT 调用（B15）、以及 R1 类移除记录注释——无任何仍注册的 `platform_generate` 工具（`list_tools` 100 项机械验证，见 §7）。

## 9. 勘误关闭声明

- 本勘误按 §3 清单执行**最小修改**：仅删除/转移 platform_generate 相关代码与 GPIO 措辞，未顺手重构无关模块；fail-closed 语义未削弱（evaluate_observation 缺 marker → INVALID_ARGUMENT；export_manifest 非 PLATFORM_DESIGN → STAGE_PREREQUISITE_UNMET；旧路径 → UNKNOWN_TOOL）。
- 产物契约（XSA/Manifest/地址一致性）既有规则不变；`verify_consistency`、`pl_generate_system_top`、`ps_import_hardware` 等下游契约零语义变更。
- 冻结资产：`.mcp.json` SHA256=`d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`（与 O1–O6/B10 冻结记录一致，未变）；`capabilities.py`/`platform_domain.py` 等 12 个 B10 冻结生产文件 SHA256 已按 §3.1 更新——冻结清单（`B10_freeze_manifest.md`）由后续轮次按流程更新，本记录只登记实测值。
- 已知行为差异（§4）：原子流无顶层合成步骤（XSA HDF 由阶段③验证）；此差异如实记录，不隐藏。
- 本勘误关闭不等于冻结 B11 阶段②之后的资产；阶段③起按 `B11_plan.md` 继续。

## 10. 草案历史（原 `B11_platform_generate_erratum_draft.md` 全文，原样并入）

原文件 SHA256（删除前）：`2836c040273da1bf526fbeb1c6f5bbfadf851c258688483ef98fd82637bccef1`。以下为草案原文（历史记录，其中的「待审核/将执行」表述均已在本记录 §1–§9 定案并执行）：

```markdown
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
```
