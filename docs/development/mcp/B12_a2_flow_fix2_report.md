# B12-A2 开发流程修复轮 #2（Agent1 白盒）报告

> 日期：2026-08-25（`Get-Date` 实测；UTC+8）｜角色：Agent1（白盒实现）
> 范围：修复白盒 v2 第三轮暴露的 5 项框架级问题（P1 优先级：BSP 库完整性、D1 残留身份不一致；P2：D-B 扩展、PL 重试健壮性、add_ip 回读假阴性）。
> 纪律：改动前对每个生产/测试文件在同目录建 `.bak`，收尾已全部删除（全仓 `.bak` = 0）。只跑非硬件回归。未执行任何 git 写操作。未修改 CLAUDE.md、docs 冻结文档、skills/、boards/、legacy 目录、workspaces/、.mcp.json。未运行任何 EDA/host_live/device_live。
> 注：白盒 v2 报告 `docs/development/tests/B12_a2_whitebox_v2_report.md` 由主代理生成，不在本轮修改范围，未触碰。

---

## 0. 修复总览

| # | 级别 | 生产入口 | 修复性质 | 证据等级 |
|---|---|---|---|---|
| 1 | P1 | `domains/ps/ps_bsp.py`（`compile_app`） | **独立复现（nm/ar）否定"库不完整"假设**：libxil.a 实为完整；真因是应用调用 Vitis2023.1 驱动不存在（旧版）的 `XUartPs_Initialize`。真正的框架缺陷是 `ps_compile` 的 `app build failed` / `no ELF produced` 失败路径吞掉链接/构建细节——已修：回传完整输出（截断标注 + 总长）。 | `MOCK_ONLY`（nm/ar 对真实产物，逻辑修复 mock 测试） |
| 2 | P1 | `control/recovery.py`（D-E 分支） | **D1 残留根因**：recover 保留活 worker 时 bump 了 ledger `worker_generation`，与 process_controller 内存 `self._generation` 脱钩 → 重试 `BACKEND_IDENTITY_MISMATCH`。已修：保留活 worker 时**不 bump** generation。 | `IMPLEMENTED_AND_TESTED`（真实子进程 + 真实 process_controller） |
| 3 | P2 | `dispatcher.py` + `control/capabilities.py` | D-B 扩展：从 schema 派生 `_PS_ALLOWED_ARGS` 全扫 49 个 ps_* 工具参数契约，未知参数（如 `ps_get_bsp_status` 的 `platform_name`）在准入前稳定返回 INVALID_ARGUMENT/UNSUPPORTED_ARGUMENT，绝不 TypeError/OUTCOME_UNKNOWN。 | `IMPLEMENTED_AND_TESTED`（真实 MCP SDK contract） |
| 4 | P2 | `domains/pl/pl_bridge_tools.py`（新增 `pl_reset_run`）+ `platform_atoms.py`（export_manifest 版本化） | PL 重试健壮性：A=新增 `pl_reset_run`（重置失败 run，配合既有 stage SUCCEEDED-only 推进）；C=`platform_export_manifest` 把 `xsa_sha256` 纳入 revision_inputs，XSA 变化时正确版本化。B=确认 FAILED 不推进 stage，重试由既有语义 + `pl_reset_run` 满足，不改冻结 stage 逻辑。 | `MOCK_ONLY`（mock 测试；未跑真 Vivado） |
| 5 | P2 | `platform_atoms.py`（`_verify_ip_props`） | add_ip 回读假阴性：区分"未知属性名（Vivado 静默忽略，读回 ''）"与"真值未应用"，新增 `IP_PROPERTY_NOT_RECOGNIZED` 理由码。证据：白盒对 `axi_bram_ctrl` 传了非规范名 `C_DATA_WIDTH`（真名 `C_S_AXI_DATA_WIDTH`），round-#1 已正确报 mismatch；本轮使该情形可归因。 | `MOCK_ONLY`（mock 测试） |

---

## 1. 项 #1（P1）：BSP 库完整性

### 独立复现（nm/ar，对真实 `libxil.a`）
对 `workspaces/b12_a2_agent1c_20260825/project_f/` 下三份 `libxil.a`（export/bsplib、bsp、fsbl）用 Vitis 2023.1 `arm-none-eabi-nm`/`ar`/`objdump` 检查：

- `xuartps.o` 定义的符号：`XUartPs_CfgInitialize`、`XUartPs_ReceiveBuffer`、`XUartPs_Recv`、`XUartPs_Send`、`XUartPs_SendBuffer`、`XUartPs_SetBaudRate`、`XUartPs_StubHandler`——**不含 `XUartPs_Initialize`**。
- 三份 `libxil.a` 均搜不到 `XUartPs_Initialize`；且各自含全部 uartps driver 对象（xuartps.o/_g/_hw/_intr/_options/_selftest/_sinit 全在，共 121 个对象）。

### 真实根因（非"库不完整"）
`XUartPs_Initialize` 是**旧版驱动 API**。Vitis 2023.1 的 `uartps_v3_12` 驱动源码 `xuartps.c` 只实现 `XUartPs_CfgInitialize`（`xuartps.h` 仅声明 `XUartPs_CfgInitialize/XUartPs_Send/XUartPs_Recv/XUartPs_SetBaudRate`），**根本没有 `XUartPs_Initialize`**。白盒应用 `main.c:160` 调用了不存在的旧版函数 → 链接失败 `undefined reference to 'XUartPs_Initialize'`。

所以"standalone libxil.a 不完整——xuartps.o 缺 XUartPs_Initialize"的诊断**是把驱动版本差异误判为库构建缺陷**。库是完整的、正确的；问题在应用代码用了不存在的 API。同时白盒称 `_exit.o` 为空对象也不准：`nm` 显示其有 `W _exit`，`objdump -d` 显示 `b 0 <_exit>`（有效死循环 `_exit`），`.text` 4 字节，非空。

### 真正可修的框架缺陷（已修）
`ps_compile` 的失败路径吞掉构建/链接细节：
- `app build failed: {verr[2]}`（现在详情已由 round-#1 的 `_parse_tolerate_stderr` 修复保留）——但仍应像 MAKE_FALLBACK 一样 cap + 标注。
- **`no ELF produced after build for app {name}`** 路径（步骤 3 后）返回**一行结论**，把真实链接错误（undefined reference）吞掉——这是白盒实际命中的（mcp_calls.jsonl line 294）。

### 修复（`domains/ps/ps_bsp.py`）
1. `app build failed` 路径也经 `_cap_build_output` 截断，`details` 记录 `build_output_len`/`build_output_truncated`。
2. `no ELF produced` 路径：若构建返回 success 但无 ELF，把捕获到 stdout 里的构建/链接输出回传到 error message（cap + 标注），而非一行结论；无输出时保持干净基础行。

### 风险
低。仅放宽失败路径的信息量；不改变构建调用或成功路径；不影响任何 host_live XSCT 流程（本轮不跑）。

### 测试（`test_ps_bsp_domain.py`，+3）
- `test_compile_app_app_build_failure_includes_full_output` — `app build failed` 回传完整 undefined-reference 链接输出。
- `test_compile_app_no_elf_surfaces_build_output` — build success 但无 ELF 时，回传捕获到的链接错误（undefined reference / ld returned）——**复现白盒 v2 的 no-ELF 吞细节**。
- `test_compile_app_no_elf_no_output_keeps_bare_message` — 无可用输出时保持干净基础行（fail-closed，无编造）。

---

## 2. 项 #2（P1）：D1 残留——`BACKEND_IDENTITY_MISMATCH`

### 根因
白盒 v2：`ps_compile` 首次 FAILED → `ps_get_bsp_status` OUTCOME_UNKNOWN → `recover_execution` → `ps_compile` 重试报 `BACKEND_IDENTITY_MISMATCH`（pid/gen 不一致）→ lane RECOVERY_REQUIRED。

链：round-#1 的 D-E 分支（活 worker + 未决 previous op）在 `recovery.py` **第 160 行 `w["worker_generation"] = gen`（gen=old+1）**——保留活 worker 时却 bump 了 ledger generation。而 `tool_process_controller._verify_current_identity` 要求 `worker_generation == self._generation`（内存值，未变）→ 下次 `ensure_backend` 重入报 `BACKEND_IDENTITY_MISMATCH`。

### 修复（`control/recovery.py`）
D-E 分支改为**保留活 worker 的精确 generation**（`gen = w.get("worker_generation",0)`，不再 +1）。语义：同一个物理后端 + 同一控制器内存 generation 仍有效；只有清死的 worker（启动全新后端）才应推进 generation（P6-P7 提交分支保持不变）。

### 风险
低。仅改动"保留活 worker"的 generation 处理；清死 worker 的路径（P6-P7 commit 仍 +1）不受影响；既有"活 PID+无未决 previous"拒绝语义保留。

### 测试
- `test_r1_recovery.py::test_de_unresolved_previous_resolves_with_live_worker`（等价更新）：追加断言 recover 后 `worker_generation == 1`（未 bump）。
- `test_b11_phase6_1_recovery_residual.py::TestPhase61D1Residual::test_recover_keeps_live_worker_generation_then_controller_reenters`（新增，真实子进程 + 真实 process_controller）：起真实后端(gen=1) → 构造未决 OUTCOME_UNKNOWN previous + 活 worker → recover（保留活 worker，gen=1）→ **同一控制器** `ensure_backend` 重入成功（`snap2.worker_generation == 1`，同一 pid 复用），无 `BACKEND_IDENTITY_MISMATCH`。这是白盒 v2 的确切回归。

---

## 3. 项 #3（P2）：D-B 扩展

### 根因
round-#1 只对 `project_path` 做了固定守卫，但 `_dispatch_ps` 仍把剩余所有参数 `local_fn(bridge, **arguments)` 前传。任何域函数不接收的键（如 `ps_get_bsp_status(bridge)` 收到 `platform_name`）→ TypeError → OUTCOME_UNKNOWN → P6 gate。白盒 v2 实测证实（mcp_calls.jsonl line 297：`get_bsp_status() got an unexpected keyword argument 'platform_name'`）。

### 修复（`control/capabilities.py` + `dispatcher.py`）
- `capabilities.py`：从 `ALL_TOOLS` schema 派生 `_PS_ALLOWED_ARGS = {tool: frozenset(props - {'session_id'})}`（49 个 ps_* 工具；`session_id` 是传输键，dispatcher 会剥掉；`project_path` 仅在 4 个 workspace 工具 schema 声明，故正确包含）。
- `dispatcher.py`：`_dispatch_ps` 在准入/`run_command` 之前，对 `ps_args` 中不在 `allowed` 内的键 → 返回 `INVALID_ARGUMENT`/`UNSUPPORTED_ARGUMENT`（消息含 unsupported + supported 列表）。这**泛化了** round-#1 的 `project_path` 守卫，覆盖全部 ps_* 工具。未注册工具的 fallback 保留原 project_path 逻辑。

### 风险
低。只在"传入域函数不接受的键"时提前拒绝；合法调用（4 个 workspace 工具的 project_path、其余工具的 schema 参数）不受影响；0 新增域函数签名/0 破坏 schemas。不会让 SDK 产生 "Input validation error" 文本（那非项目信封）。

### 测试（`test_b06_ps_bsp_public.py`，+3）
- `test_ps_get_bsp_status_platform_name_never_outcome_unknown` — `ps_get_bsp_status` 传 `platform_name`+`project_path` → 精确 INVALID_ARGUMENT/UNSUPPORTED_ARGUMENT 信封，且 unsupported 含 `platform_name`（**复现白盒 v2 确切回归**）。
- `test_ps_compile_unsupported_arg_is_rejected` — `ps_compile` 传无关键 `elf_path` → 稳定拒绝。
- `test_ps_get_bsp_status_without_platform_name_accepted` — 仅 session_id 的合法 no-arg 调用被接受（拒收 guard）。

---

## 4. 项 #4（P2）：PL 重试健壮性

### A. `pl_reset_run`（新工具，+1）
白盒黑盒需要一个"重置失败 run"的手段。Vivado 失败后的 `launch_runs synth_1`/`impl_1` 处于非 Complete 态，直接重跑会被拒（run 已存在/未 reset）。新增 `pl_reset_run`：
- `domains/pl/pl_bridge_tools.py`：实现（`run_name` ∈ {synth_1,impl_1}，`force` 可选），先 `get_runs -quiet` 确认存在（fail-closed），再 `reset_run[-force]`，解析 `RESET_STATUS` 并校验（非 Complete/ERROR → `RESET_RUN_FAILED`）。
- `PL_TOOL_MAP` 注册（90s timeout）；`capabilities.py` 注册 schema。
- 结果：`{"run_name", "reset": True, "status"}`。

**阶段不推进**：受 `op_transition` SUCCEEDED-only 推进（已核实）与 `_check_stage` 约束——`pl_synthesize` 仅从 PL_BUILD 准入，FAILED 不推进 stage（仍 PL_BUILD）→ 可重试。因此 B（FAILED 后允许 stage 回退/重跑）由**既有语义 + `pl_reset_run`** 满足，无需改动冻结的 stage-advance 逻辑（B 不另做代码改动，否则会破坏 B04 §4.3 串行链）。

### C. `platform_export_manifest` 版本化
白盒需求：同 revision 但 generated_at/xsa_sha 变化时允许重发布（或正确版本化）。根因：原 `platform_export_manifest` 的 `revision_inputs` **未含 `xsa_sha256`**——wrapper/板卡/preset 不变但 XSA 变时，`compute_revision` 得到同一 revision → 写入同一 `sha256_<rev>.json` → `publish_manifest` 报 `ManifestConflictError`。
修复：把 `xsa_sha256` 纳入 `revision_inputs`；XSA 变化 → revision 推进 → 新 manifest 落到自己的 `sha256_<新rev>.json`，正确版本化。`validate_manifest` 的 platform `_REVISION_INPUTS_REQUIRED` (board_profile_sha256/tool_versions/source_files/config_files) 仍满足；额外键被 `compute_revision` 哈希。已核实二次 export 幂等（`already_exists_same`）不受影响。

### 风险
- A：新工具注册使总工具数 104→105，已同步更新所有相关计数断言（见 §6 测试映射）。
- C：platform manifest revision 计算含 xsa_sha——同一 BD 同一 XSA 重导出仍幂等；只有 XSA 变才推进；不破坏 `verify_consistency`（其校验 xsa_sha 与 platform manifest 一致）。

### 测试
- `test_pl_bridge.py`（+5）：`test_reset_run_success` / `test_reset_run_force_flag` / `test_reset_run_rejects_unknown_run` / `test_reset_run_fail_closed_on_bridge_error` / `test_reset_run_missing_run_reports_error`（+ `_TCL_EXPECT`/`_REPR_ARGS` 条目 + 计数断言 26→27）。
- `test_platform_atoms.py::test_re_export_versions_on_changed_xsa`（+1）：change XSA → 新 revision/新路径，记录新 xsa_sha256。

---

## 5. 项 #5（P2）：add_ip 回读假阴性

### 根因（基于白盒 v2 证据）
白盒对 `axi_bram_ctrl_0` 传 `{"C_DATA_WIDTH":32,"C_ADDR_WIDTH":14}` 和 `{"C_S_AXI_PROTOCOL":"AXI3",...}`，round-#1 修复后报 `IP_CONFIG_MISMATCH / actual=''`。**真因**：这些**不是** axi_bram_ctrl 的规范参数名（真名 `C_S_AXI_DATA_WIDTH`/`C_S_AXI_ADDR_WIDTH`；且 axi_bram_ctrl 是 AXI4-only，无 AXI3 protocol 选项）。Vivado 对不存在的 `CONFIG.C_DATA_WIDTH` **静默忽略**（set_property 不报错），round-#1 回读返回 '' 并正确报 mismatch——这是**正确的 fail-closed**，不是漏校验。白盒把"未知属性名被静默忽略"误读为"配置未被正确校验"。

### 修复（`platform_atoms.py` `_verify_ip_props` + `platform_add_ip`）
- `_verify_ip_props` 对每个 mismatch 项标注 `recognized: got 是否为真值`（读回空 → 属性不存在 → `recognized: False`）。
- `platform_add_ip` fresh-add 路径：若存在 `recognized: False` 项 → 抛 `IP_PROPERTY_NOT_RECOGNIZED`（明确告知"属性名不对/不被目录识别"）；否则真值 mismatch → 抛 `IP_CONFIG_MISMATCH`。
- `PlatformError` 直接携带 reason_code，经 dispatcher 映射 to `TOOL_ERROR`，无需改错误码注册表。

### 风险
低。仅使错误**可归因**（区分 wrong-name 与 value-mismatch）；对名称正确、值正确/错误的 IP 行为不变；不会把正确配置误判为 mismatch。

### 测试（`test_platform_atoms.py`，+2，并等价修订 +1）
- `test_fresh_add_unknown_property_raises_not_recognized` — 传不存在属性（`C_DATA_WIDTH` on axi_bram_ctrl）→ `IP_PROPERTY_NOT_RECOGNIZED`（**复现白盒 v2**）。
- `test_fresh_add_recognized_value_mismatch_raises_ip_config` — 规范名但值未应用（读回 64≠32）→ `IP_CONFIG_MISMATCH`。
- 等价修订：既有 `test_fresh_add_silent_drop_raises_not_success`（读回 '' 现归为 NOT_RECOGNIZED）。

---

## 6. 回归机械统计（前后对照，数字来自命令输出）

```bash
python -m pytest mcps -m "not host_live and not device_live"   （仓库根）
```
| 指标 | 基线（fix #1 后） | 修复后 | 变化 |
|---|---|---|---|
| collected | 1445 | **1460** | +15（新增测试） |
| passed | 1403 | **1418** | +15 |
| skipped | 1 | 1 | 0 |
| deselected | 41 | 41 | 0 |
| failed | 0 | **0** | 0 |

- 修复后 passed（1418）≥ 基线（1403），failed = 0，无测试净减。
- collected 基线（1445）为 fix #1 后 `--collect-only` 输出；修复后 **1460 tests collected**。

### 新增/修改测试映射

新增 15 个测试函数（全部非硬件）：

| 文件 | 函数 | 归属项 |
|---|---|---|
| `test_ps_bsp_domain.py` | `test_compile_app_app_build_failure_includes_full_output` | #1 |
| `test_ps_bsp_domain.py` | `test_compile_app_no_elf_surfaces_build_output` | #1 |
| `test_ps_bsp_domain.py` | `test_compile_app_no_elf_no_output_keeps_bare_message` | #1 |
| `test_b11_phase6_1_recovery_residual.py` | `test_recover_keeps_live_worker_generation_then_controller_reenters` | #2 |
| `test_b06_ps_bsp_public.py` | `test_ps_get_bsp_status_platform_name_never_outcome_unknown` | #3 |
| `test_b06_ps_bsp_public.py` | `test_ps_compile_unsupported_arg_is_rejected` | #3 |
| `test_b06_ps_bsp_public.py` | `test_ps_get_bsp_status_without_platform_name_accepted` | #3 |
| `test_pl_bridge.py` | `test_reset_run_success` | #4A |
| `test_pl_bridge.py` | `test_reset_run_force_flag` | #4A |
| `test_pl_bridge.py` | `test_reset_run_rejects_unknown_run` | #4A |
| `test_pl_bridge.py` | `test_reset_run_fail_closed_on_bridge_error` | #4A |
| `test_pl_bridge.py` | `test_reset_run_missing_run_reports_error` | #4A |
| `test_platform_atoms.py` | `test_re_export_versions_on_changed_xsa` | #4C |
| `test_platform_atoms.py` | `test_fresh_add_unknown_property_raises_not_recognized` | #5 |
| `test_platform_atoms.py` | `test_fresh_add_recognized_value_mismatch_raises_ip_config` | #5 |

等价修订（非删除）既有测试，保持语义/计数一致：

| 文件 | 函数 | 说明 |
|---|---|---|
| `test_ps_bsp_domain.py` | `test_compile_app_make_fallback_includes_full_output` | 增加 `build_output_len`/`truncated` 断言（round-#1 已改动）；本轮未再改 |
| `test_r1_recovery.py` | `test_de_unresolved_previous_resolves_with_live_worker` | 追加 `worker_generation == 1`（D1 残留守卫） |
| `test_platform_atoms.py` | `test_fresh_add_silent_drop_raises_not_success` | 读回 '' 现归为 `IP_PROPERTY_NOT_RECOGNIZED` |
| `test_pl_bridge.py` | `test_pl_bridge_tools_match_capabilities` | 26→27 |
| `test_pl_bridge.py` / `test_r3_runner.py` / `test_r1_mcp_sdk.py` / `test_r2_adapter.py` / `test_r3_1c_public.py` / `test_o6_skill_contract.py` / `test_b05_platform_public.py` | 工具计数/总数 | 104→105、44/27→45/28（新增 pl_reset_run 的机械计数，断言等价计数不变） |

删除/重命名测试：**无**。

---

## 7. 修改文件清单（非 git 提交，由主代理统一提交）

生产代码（5）：
- `mcps/zynq_mcp/domains/ps/ps_bsp.py` — app build failed / no-ELF 失败路径回传完整构建输出
- `mcps/zynq_mcp/control/recovery.py` — D-E 分支保留活 worker 不 bump generation
- `mcps/zynq_mcp/control/capabilities.py` — `_PS_ALLOWED_ARGS` + `pl_reset_run` schema（104→105）
- `mcps/zynq_mcp/dispatcher.py` — `_dispatch_ps` 用 `_PS_ALLOWED_ARGS` 全扫参数契约
- `mcps/zynq_mcp/domains/pl/pl_bridge_tools.py` — `pl_reset_run` + PL_TOOL_MAP 注册
- `mcps/zynq_mcp/domains/platform/platform_atoms.py` — `_verify_ip_props` recognized + `IP_PROPERTY_NOT_RECOGNIZED` + export_manifest 版本化（xsa_sha 入 revision_inputs）

测试（8）：
- `mcps/zynq_mcp/tests/test_ps_bsp_domain.py`、`test_b11_phase6_1_recovery_residual.py`、`test_b06_ps_bsp_public.py`、`test_pl_bridge.py`、`test_platform_atoms.py`、`test_r1_recovery.py`、`test_b05_platform_public.py`、`test_o6_skill_contract.py`、`test_r1_mcp_sdk.py`、`test_r2_adapter.py`、`test_r3_1c_public.py`、`test_r3_runner.py`

（曾为每个生产/测试文件建 `.bak`，收尾已全部删除；全仓 `.bak` = 0。）

---

## 8. 未改动/未实现声明

- **未修改**：CLAUDE.md、docs 冻结文档（含白盒 v2 报告）、skills/、boards/、legacy 目录、workspaces/、.mcp.json。
- **未执行**：任何 git 写操作；任何 EDA/host_live/device_live 工具。
- **#4B（stage 回退）不做独立代码**：核实 FAILED 不推进 stage（`op_transition` 仅 SUCCEEDED 推进），重试由既有 stage 语义 + `pl_reset_run`（重置失败 run）满足。改动冻结的 `_check_stage`/`SERIAL_STAGES` 会破坏 B04 §4.3 串行链，故不引入——报告此处为"分析确认，不另改代码"。
- **未自行冻结 Brick、未越级进入下一步骤；未调用 Agent2。**

## 9. 需主代理注意 / 局限（如实）

1. **项 #1**：我对"库不完整"假设做了独立 nm/ar 复核，**证据否定该假设**——库完整，真因是应用用了 Vitis2023.1 不存在的旧版 `XUartPs_Initialize`（应改应用代码用 `XUartPs_CfgInitialize`）。我在纪律内无法改应用源码（不在 mcps/ 生产范围），故白盒 v2 的应用链接失败需在固件侧改 API。我修的是框架侧 `ps_compile` 把真实链接错误暴露出来。
2. **证据等级**：项 #1/#4/#5 为 `MOCK_ONLY`（逻辑修复用 mock 测；未跑真 Vivado/XSCT host_live，属本轮非硬件纪律）。真实工具门禁需另行一轮 host_live。
3. **项 #4**：`pl_reset_run` 把总工具数从 104 提到 105，同步更新了多处机械计数断言；若主代理/后续有依赖"104"的脚本需知悉。`platform_export_manifest` 版本化把 `xsa_sha256` 纳入 revision——同一 XSA 重导出仍幂等，仅 XSA 变化推进版本。
4. **项 #5**：`axi_bram_ctrl` 的 `actual=''` 是"属性名不被目录识别"（白盒用了非规范名），非真值 mismatch。本轮修复让该情形报 `IP_PROPERTY_NOT_RECOGNIZED` 以可归因；但白盒需用规范参数名（`C_S_AXI_DATA_WIDTH`/`C_S_AXI_ADDR_WIDTH`）才能正确配置该 IP。
