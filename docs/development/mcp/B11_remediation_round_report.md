# B11 阶段③.1 整改轮报告（D0–D9 逐条记录）

> 日期：2026-08-14（本会话 `Get-Date` 实测）
> 状态：**整改轮执行完成** — 4 个 P1（D1/D2/D3/D4）全部 FIXED，P2 中 D0/D5/D6/D7/D8/D9 全部 FIXED（D8/D9 经真机调查后低成本修复，非记债）。
> 角色：Agent1（白盒实现轮）| 依据：阶段③白盒报告 `docs/development/tests/B11_phase3_whitebox_report.md` 的 D0–D9 清单 + 用户定调
> 起点基线（阶段②完成值，本报告机械复核）：`--collect-only`=1376；非硬件回归 1337 passed / 1 skipped / 38 deselected / 0 failed；host_live=34、device_live=4。
> 配套：`docs/development/mcp/B11_plan.md` 已追加「阶段③.1 整改轮记录」段落链接本文档。

## 0. 本轮数字（机械实测，从项目根目录）

| 项 | 基线（阶段②） | 整改轮后 | 结论 |
|---|---|---|---|
| `--collect-only` | 1376 | **1411**（+35） | 净增 |
| 非硬件回归 | 1337 passed / 1 skipped / 38 deselected | **1371 passed / 1 skipped / 39 deselected / 0 failed**（255.57s） | passed 净增 34，无下降 |
| host_live 收集 | 34 | **35**（+1 新链测试） | 净增 |
| device_live 收集 | 4 | 4 | 不变 |
| 公开工具数 | 100（9 control + 91 domain） | **103**（9 control + 94 domain） | 3 新原子 |
| platform 域 implemented | 14 | **17** | 3 新原子 |
| `.mcp.json` SHA256 | d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02 | **不变**（`Get-FileHash` 复核 D8E397AF03B5B032F21D0AA967086F0C78B33C87B76F2E9898AE0A144DF7DE02） | 冻结注册零触碰 |

回归脚本：`python -m pytest mcps -m "not host_live and not device_live" -q` → `1371 passed, 1 skipped, 39 deselected`（exit 0）。
收集脚本：`python -m pytest mcps --collect-only -q` → `1411 tests collected`；`-m host_live` → `35`；`-m device_live` → `4`。

## 1. P1 修复（D1–D4）

### D1 — 无 BD 地址分配能力 → FIXED（新增 `platform_assign_addresses`）

- 现象/根因：`platform_set_address` 的 `set_property CONFIG.C_BASEADDR` 对已存在段 read-only（`[Common 17-107]`），无 `assign_bd_address` 公开原子；真机 vivado.log 实证 3 次 set_property 失败/空匹配。
- 修复：`domains/platform/platform_atoms.py:552` 新增 `platform_assign_addresses`——`segments` 缺省 → `assign_bd_address`（全部未分配段，legacy B05 验证过的 Tcl 形态）；指定 → 每段 `assign_bd_address [get_bd_addr_segs {<seg>}]`；返回解析后的每-master `address_map`（`_ADDRESS_MAP_QUERY_TCL`，与 export_manifest 共用）；幂等（已分配段 no-op，返回现状）。
- 注册：`capabilities.py` Tool 项；`execution_gate._check_stage:154` 限定 PLATFORM_DESIGN；`PLATFORM_ATOM_MAP/COMMAND_TOOL_NAMES/CONTEXT_ARGS/TIMEOUT`（`platform_atoms.py:847+`）。
- 测试：组件 `test_platform_atoms.py::TestAssignAddresses`（4 个）；host_live `test_b05_platform_public.py::TestB11RemediationAtoms::test_atom_sequence_assign_external_synthesize_hdf`（真机断言 address_map 非空且含 axi_gpio_led）。

### D2 — 无 BD 端口外部化能力 → FIXED（新增 `platform_make_external`）

- 现象/根因：无 `create_bd_port`/`make_bd_pins_external` 原子；wrapper 端口仅 DDR/FIXED_IO。
- 修复：`platform_atoms.py:404` 新增 `platform_make_external`——信号模式 `create_bd_port -dir <I|O|IO> [-from w-1 -to 0] <port>` + `connect_bd_net`；接口模式 `make_bd_intf_pins_external`；创建后按 `get_bd_ports *`/`get_bd_intf_ports *` 全量清单做成员校验（fail-closed `EXTERNAL_PORT_CREATE_FAILED`）。**真机验证修正**：`create_bd_port` 只接受方向字母 I/O/IO（`IN/OUT/INOUT` 被 BD 41-78 拒绝）；`make_bd_pins_external` 只适用于普通 pin（接口 pin 报 BD 5-407），接口外部化必须用 `make_bd_intf_pins_external`；`get_bd_ports <name>` 名字查询匹配 0 对象（D8 同族行为），校验用 `*` 全量清单。
- 测试：组件 `TestMakeExternal`（7 个，含接口 pin 缺失 fail-closed）；host_live 链断言 wrapper HDL 文本含新端口 `gpio_led_pins`。

### D3 — XSA 无 HDF（原子路径无合成）→ FIXED（新增 `platform_synthesize`）

- 现象/根因：`write_hw_platform` 前无合成 → 1.5KB `pre_synth` 空壳（仅 xsa.json/xsa.xml，`[Vivado_Tcl 4-424]`）；`ps_create_platform` 必然失败。
- 修复：`platform_atoms.py:620` 新增 `platform_synthesize`——先把 BD 设为合成 top（真机验证：不设则 `launch_runs` 报 "Top module not set for synthesis run"），再 `launch_runs synth_1 -jobs N` → `wait_on_run synth_1` → `open_run synth_1`（`SYNTH_TIMEOUT_S=1800` 内层 + 外层 `PLATFORM_ATOM_TIMEOUT=1860`）；随后查 `get_property STATUS [get_runs synth_1]`（非 complete → `SYNTHESIS_FAILED` fail-closed）与 WNS（无时序路径 → None）。真机验证：`synth_design Complete!` 且生成 `platform_bd.hwh` / `synth/platform_bd.hwdef`（HDF 产物）。**jobs 默认 1**：多 IP BD 用 `-jobs > 1` 会并行启动 IP OOC 合成、并发 vivado 进程数超过本机许可证特性容量（"Failed to load feature 'core'"），`-jobs 1` 串行可靠（真机验证；docstring 注明可调高）。
- 测试：组件 `TestSynthesize`（6 个，含 top 设置与 jobs=1 默认断言）；host_live 链：合成后 XSA 大小 >1500B 且 zip 内含 hwh/hwdef/ps7_init 类条目（HDF 实证）。

### D4 — 空闲心跳死锁 → FIXED（用户定调模型：每次心跳索要进程）

- 现象/根因：`_hb()` 循环对任何 `heartbeat_once()` ok=False（含瞬时 LEDGER_READ_FAILED / HEARTBEAT_WRITE_FAILED）直接 `break` 永久退出；idle>120s 后 P5 以 WORKER_UNRESPONSIVE 拒准入；`recover_execution` 因 worker alive 为 no-op；唯一出路 close_session 杀后端。
- 修复（模型：**进程在→刷新计时；进程不在→累计超时**）：
  - `control/single_worker.py:56` `_HEARTBEAT_TRANSIENT_FAILURES={LEDGER_READ_FAILED, HEARTBEAT_WRITE_FAILED}`；`_hb()`（L447–468）：瞬时失败 → 记 `last_heartbeat_error` 并 `continue`（下个 tick 重试）；崩溃类 verdict（PID_NOT_ALIVE / IDENTITY_UNVERIFIABLE / WORKER_*_MISMATCH / WORKER_IDENTITY_MISSING 等已 `_do_crash`）→ break。
  - `single_worker.py:471` 新增 `restart_heartbeat()`：cancel 旧 task → `_start_heartbeat()` 重启 → 立即 `heartbeat_once()` 刷新时间戳。
  - `control/execution_gate.py:69–96` P5 改为**索要进程**：P2（alive）+P3（身份一致）已证进程存在 → 心跳陈旧不再阻断准入，只写诊断字段 `worker.last_heartbeat_stale_s`；`WORKER_HEARTBEAT_MISSING`（hb 缺失）与 `WORKER_HEARTBEAT_UNREADABLE`（不可解析）分支保留。运行中操作的心跳新鲜度仍由 `control/vivado_execution_observer.py` / `control/tool_process_controller.py` 负责（陈旧→RECOVERY_REQUIRED），本修复只影响「无活跃操作时的准入预检」（gate 注释已写明该论证）。
  - `control/domain_runner.py:375–384` `_shared_preflight_check` 的同一 P5 逻辑同步（同一状态机相邻错误路径，保持双门禁一致）。
  - `dispatcher.py:442` `_alive_stale_revive_eligibility` + `:479` `_revive_heartbeat` + `:539` `_recover_execution`（改 async）：lane IDLE 且进程 alive 且无活跃操作且心跳陈旧/缺失 → 服务层调用 `worker.restart_heartbeat()`，ledger 原子追加 `recovery_log {action:"heartbeat_revive"}`，**无需 close_session**；进程 alive 但有非终态活跃操作 → 仍 `RECOVERY_BLOCKED_WORKER_ALIVE`；mutator 保持纯原子（进程操作只发生在 dispatcher 服务层）。
- 回归测试（真实进程，FAKE_MCP stdio 子进程风格）— 新文件 `tests/test_b11_heartbeat_remediation.py`（8 个）：
  - (a) `test_a1_gate_admits_stale_heartbeat_on_alive_process` / `test_a2_runner_admits_stale_heartbeat_on_alive_process`：健康空闲+陈旧 hb 后下一条命令**准入成功**（gate 与 runner 双路径，非 WORKER_UNRESPONSIVE），诊断字段写入；
  - (b) `test_b1_dead_process_still_rejected`：杀进程后仍 WORKER_PID_DEAD；
  - (c) `test_c1_identity_mismatch_still_rejected`：身份不匹配仍拒；
  - (d) `test_d1_heartbeat_once_transient_write_failure_no_crash`（monkeypatch ledger_transaction 注入 LedgerWriteError → HEARTBEAT_WRITE_FAILED、无 crash、重试成功刷新）+ `test_d2_heartbeat_loop_survives_transient_failures`（注入 2 次 LEDGER_READ_FAILED + 短间隔 → 循环存活并最终刷新，非碰运气）；
  - (e) `test_e1_recover_revives_alive_stale_without_close_session`（recover 成功 revive：heartbeat_revived=true、时间戳刷新、recovery_log 含 heartbeat_revive、进程仍活）+ `test_e2_recover_still_refused_with_active_operation`（活跃操作 → RECOVERY_BLOCKED_WORKER_ALIVE）。
  - 旧→新映射：`test_r3_runner.py::test_R3X04_p5_stale_heartbeat`（断言 WORKER_UNRESPONSIVE 阻断）→ `test_R3X04_p5_stale_heartbeat_admitted_when_process_alive`（真 alive PID + 真实身份 → 准入、executor 运行；等价性：同一 P5 状态机、同一真实进程证据，语义按用户定调变更）+ 新增 `test_R3X04b_p5_missing_heartbeat_still_blocks`（缺失分支保留）。

## 2. P2 修复（D0/D5/D6/D7/D8/D9）

| # | 状态 | 现象 | 根因 | 修复位置 | 测试 |
|---|---|---|---|---|---|
| D0 | **FIXED** | `platform_configure_ps7` 无 EMIO GPIO 键，EMIO 路线不可达 | `_PS7_CONFIG_TO_PCW` 键集合缺 EMIO | `platform_atoms.py:176` 增 `gpio_emio_enable`→PCW_EN_EMIO_GPIO、`gpio_width`→PCW_GPIO_EMIO_GPIO_WIDTH、`gpio_io`→PCW_GPIO_EMIO_GPIO_IO（config.gpio 嵌套展开）；`capabilities.py` schema 增 `gpio` 子对象 | `test_platform_atoms.py::TestConfigurePs7::test_emio_gpio_nested_dict_d0`；host_live 链真实下发 |
| D5 | **FIXED** | 文档段名 `<ip>/S_AXI` 按文档调用必然失败（真实段名 `<ip>/S_AXI/Reg`） | 原子不解析短名 | `platform_atoms.py:480` `platform_set_address`：先 `get_bd_addr_segs -quiet $req`，空则枚举命名 cell 的 intf pins（`-of_objects` 形式，真机验证裸名/`-filter` 形式匹配 0 对象——D8②）按 `string trimleft` 匹配 `<ip>/<intf>` 取子段，仍空 → Tcl `error SEGMENT_NOT_FOUND`（D6 分类为 TCL_ERROR）；docstring 修正并注明 set_property 对 BD 段 read-only、分配以 assign 为准 | `TestSetAddress`（短名解析 Tcl 断言 + 空段拒绝）；host_live 链 `segment="axi_gpio_led/S_AXI"` 真实解析不报错 |
| D6 | **FIXED** | 纯 Tcl 错误误报 `ADAPTER_NOT_READY`（后端健康） | `_run_tcl` 对所有非冷启动 status=error 一律 `raise AdapterError` | `platform_domain.py:30` 新异常 `TclError`（reason `TCL_ERROR`）；`_run_tcl`（L186–196）按 `details.reason_code ∈ _BACKEND_NOT_READY_REASON_CODES`（ADAPTER_NOT_READY/BRIDGE_NOT_READY/BACKEND_NOT_ACTIVE/VIVADO_PROCESS_DEAD/BACKEND_PROCESS_DEAD/VIVADO_NOT_FOUND/VIVADO_VERSION_MISMATCH）→ AdapterError，否则 → TclError | `test_platform_atoms.py::TestTclErrorClassification`（4 个，含 local_fn 信封断言 TOOL_ERROR/TCL_ERROR） |
| D7 | **FIXED** | `platform_validate` 假阳性：第二次 validate 被 "already validated" 缓存掩盖真实告警 | `validate_bd_design` 无 `-force` | `platform_atoms.py:595` → `validate_bd_design -force` | `TestValidate::test_passes_on_clean_output`（Tcl 断言改 -force）+ `test_second_validate_not_masked_by_cache`（两次连续 validate，第二次真实告警必现） |
| D8 | **FIXED**（调查+低成本修复，非记债） | write_hw_platform 后 `get_bd_cells *`/`current_project`/master intf pins 查询空、add_ip 幂等失败（"already exists"） | **根因（真机 vivado.log + mcp_calls.jsonl + 本轮真机探测交叉实证，两层）**：① Tcl 桥只捕获 stdout（puts 输出），Tcl 命令返回值不回显——结果型查询（get_bd_cells */llength/current_project）从序列开始就返回空（与 write_hw_platform 无关，export_manifest 只是首个暴露点）；② 真机还发现 `get_bd_intf_pins` 的裸名/`*`/`-filter` 形式在 Vivado 2023.1 全部匹配 0 个对象，只有 `-of_objects [get_bd_cells ...]` 能枚举 intf pins——原地址映射查询因此恒空；master 侧段名为 `processing_system7_0/Data/SEG_<ip>_Reg`（OFFSET/RANGE 在此）。时钟树查询因自带 `puts` 而正常即为①佐证 | ① 结果型查询全部 `puts` 包装：`platform_get_status`（L103）、`platform_list_ips`（L318）、`platform_add_ip` 存在性检查（L273）、`platform_export_manifest` 的 `count_bd_designs` 与 `get_bd_cells *`（L770）；② `_ADDRESS_MAP_QUERY_TCL`（L543）改为 `-of_objects` 枚举 + 只输出带 OFFSET 的 master 侧段 + `string trimleft` 去前导斜杠；`_parse_manifest_address_map`（L744+）从 `SEG_<ip>_Reg` 提取从机 IP 键（兼容旧 `<ip>/<intf>/<seg>` 形式） | `TestGetStatus/TestListIps/TestAddIp/TestExportManifest` Tcl 断言更新（puts 形态）；`TestAssignAddresses::test_assigns_all_and_parses_master_side_segment_names`（SEG_ 解析）；host_live 链断言 `ip_list` 含 axi_gpio_led、`address_map` 非空 |
| D9 | **FIXED** | clock_tree 记录 pin 短名（FCLK_CLK0/aclk）而非完整路径 | `puts [get_property NAME $p]` 只输出短名；真机探测证实 BD pin **无 PARENT 属性**（`get_property PARENT` → `[Common 17-161]` 报错） | `platform_atoms.py` export_manifest 时钟树查询：`puts [string trimleft $p /]`（bd 对象字符串形式即完整路径带前导 `/`，trim 后为 `<cell>/<pin>`；真机验证输出 `processing_system7_0/M_AXI_GP0_ACLK` 等完整路径） | `TestExportManifest` Tcl 断言 `string trimleft $p /`；host_live 链断言 clock_tree 至少一个 pin 含 `/` |

## 3. 3 个新原子的 schema / 实现 / 测试 / host_live 结果

| 原子 | schema（capabilities.py） | 实现 | 组件测试 | host_live |
|---|---|---|---|---|
| `platform_assign_addresses` | `{segments?: string[]}`（additionalProperties:false） | `platform_atoms.py:552` | 4（Tcl/幂等/空表/无 adapter fail-closed） | 见 §5 |
| `platform_make_external` | `{port_name, source_pin, direction?: in\|out\|inout, width?: int≥1, interface?: bool}`（required port_name/source_pin） | `platform_atoms.py:404` | 6（信号/标量/接口/方向/端口缺失/无 adapter） | 见 §5 |
| `platform_synthesize` | `{jobs?: int≥1}` | `platform_atoms.py:620` | 6（Tcl+超时/状态/失败 closed/WNS/参数/无 adapter） | 见 §5 |

- 注册/路由：`capabilities.DOMAIN_TOOLS`（103）；`platform_atoms` 四注册表（MAP 17 / COMMAND 15 / QUERY 2 / CONTEXT_ARGS / TIMEOUT）；`dispatcher._ALL_KNOWN` 由 `PLATFORM_ATOM_COMMAND_TOOL_NAMES` 机械派生，无需改 dispatcher 分支；`domain_runner` 经 `_pl_adapter` marker 注入 VivadoAdapter，无需新清单项。
- stage 规则：三者均在 `execution_gate._check_stage`（L154–160）限定 PLATFORM_DESIGN 受理，**不推进 stage**（`_PL_SUCCESS_STAGE` 不变，仍仅 platform_export_manifest 推进）。
- 计数同步：`==100` 断言全项目 grep 实际命中 **6 处**（任务书为 5 处，机械统计以 grep 为准）→ 全部 `==103`：`test_r3_runner.py:807`、`test_r3_1c_public.py:249`、`test_r2_adapter.py:756`、`test_r1_mcp_sdk.py:117`、`test_pl_bridge.py:955`、`test_o6_skill_contract.py:237`；`DOMAIN_APIS_IMPLEMENTED` 已机械派生（len）=94；capabilities domains `platform.implemented 14→17`；`test_platform_atoms.py::TestRegistrationConsistency` 14→17（command 15/query 2）；`test_b05_platform_public.py` pl_tools 41→44。

## 4. Skill 同步（`skills/zynq_dev/appendix_mechanics.md` §3）

- platform 原子序列模板 12→15 步，插入（以真机验证顺序为准）：**assign_addresses（#9，wrapper 前）→ make_external（#10，wrapper 前）→ validate -force（#11）→ generate_wrapper（#12）→ synthesize（#13，export_hardware 前）→ export_hardware（#14）→ export_manifest（#15）**；顺序注记「create→add→connect→assign→make_external→validate→wrapper→synthesize→export」。
- 新增 S3/S5 决策说明：**「地址分配与端口外部化必须在导出（export_hardware / export_manifest）前完成」**（未分配段→validate 真实告警/PS 不可寻址；未外部化端口→不进 wrapper/XSA；不合成→XSA 缺 HDF，`ps_create_platform` 必败）。
- `platform_set_address` 行注明短名自动解析；`platform_validate` 注明 `-force`。
- 零字样门禁机械扫描（gpio / 0x41200000 / LED / breath|blink）：**0 命中**（本会话 Select-String 复核）。

## 5. host_live 真实 Vivado 专项（Vivado 2023.1，板卡未用）

命令：`python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -m host_live -q`（9 个：工具发现/计数/移除/无会话/schema 拒绝/全链/越级拒绝/B11 整改新链）。

| 测试 | 结果 | 记录 |
|---|---|---|
| `TestToolDiscovery::test_shortcut_removed_atoms_registered` | **PASS** | 17 原子注册断言 |
| `TestToolDiscovery::test_export_manifest_schema_is_closed_object` | **PASS** | — |
| `TestToolDiscovery::test_public_tool_count` | **PASS** | pl_tools=44 |
| `TestStageRejection::test_removed_shortcut_is_unknown_tool` | **PASS** | — |
| `TestStageRejection::test_export_manifest_rejected_without_session` | **PASS** | — |
| `TestStageRejection::test_extra_property_rejected` | **PASS** | 既有 host_live 测试缺陷修复（mcp 1.28.1 SDK 以 isError 返回 schema 拒绝；HEAD 上同样失败，非本轮引入） |
| `TestRealVivadoSuccess::test_full_success_chain_atom_sequence` | **PASS** | D8/D7 变更后的既有全链回归（D7 实证：-force 暴露 phase-2 最小链真实缺陷 BD 41-758，已按 B05 已验证序列补 clock 连接，见 §9 映射） |
| `TestRealVivadoSuccess::test_wrong_stage_after_pl_generate` | **PASS** | — |
| `TestB11RemediationAtoms::test_atom_sequence_assign_external_synthesize_hdf` | **PASS** | **新增整改链**：assign（address_map 非空：axi_gpio_led@0x41200000/64K/master M_AXI_GP0）+ set_address 短名解析 + make_external（wrapper 含 gpio_led_pins）+ validate -force + synthesize（synth_design Complete，jobs=1 串行 OOC）+ export（XSA>1500B 且含 hwh/hwdef/ps7_init 条目）+ manifest（ip_list 4 IP / address_map / 完整路径 clock_tree） |

最终原始输出：`9 passed in 442.48s (0:07:22)`（exit 0）。期间真机迭代发现的修正（全部已入代码与测试）：`get_bd_intf_pins` 裸名/`*`/`-filter` 匹配 0 对象（D8②）；`create_bd_port` 方向须用 I/O/IO 字母（BD 41-78）；接口外部化须用 `make_bd_intf_pins_external`（BD 5-407）；BD pin 无 PARENT 属性（[Common 17-161]）；`launch_runs` 前须 `set_property top platform_bd`（否则 "Top module not set"）；多 IP BD 并行 OOC（-jobs>1）超许可证并发容量（"Failed to load feature 'core'"），jobs 默认 1。

## 6. 变更文件清单与 SHA256 变化（before=HEAD blob，after=工作区文件，`git cat-file blob` + `Get-FileHash`）

| 文件 | 类型 | SHA256 before → after |
|---|---|---|
| `mcps/zynq_mcp/control/single_worker.py` | 生产 | 138E094B… → D70B5188… |
| `mcps/zynq_mcp/control/execution_gate.py` | 生产 | CBED0012… → 117B4397… |
| `mcps/zynq_mcp/control/domain_runner.py` | 生产 | 6E29D666… → 14F1309D… |
| `mcps/zynq_mcp/control/recovery.py` | 生产 | （未改；revive 逻辑在 dispatcher 服务层，mutator 保持纯原子） |
| `mcps/zynq_mcp/dispatcher.py` | 生产 | 950C2893… → 0064D370… |
| `mcps/zynq_mcp/domains/platform/platform_domain.py` | 生产 | C9F5A4A2… → 9C4EF4A4…（TclError/SynthesisError/分类链） |
| `mcps/zynq_mcp/domains/platform/platform_atoms.py` | 生产 | F622D165… → 52F637FB… |
| `mcps/zynq_mcp/control/capabilities.py` | 生产 | EC0B89EB… → B21D7196… |
| `mcps/zynq_mcp/tests/test_b11_heartbeat_remediation.py` | 测试（新） | NEW → B9163081… |
| `mcps/zynq_mcp/tests/test_platform_atoms.py` | 测试 | 2F82C8CF… → DDBDE213… |
| `mcps/zynq_mcp/tests/test_b05_platform_public.py` | 测试 | 897DE1D4… → 1498FF9F… |
| `mcps/zynq_mcp/tests/test_r3_runner.py` | 测试 | 5F02AC0D… → 7E6D54D4… |
| `mcps/zynq_mcp/tests/test_r3_1c_public.py` | 测试 | 789C5C42… → CCFD2926… |
| `mcps/zynq_mcp/tests/test_r2_adapter.py` | 测试 | 09F41589… → E4DD43A0… |
| `mcps/zynq_mcp/tests/test_r1_mcp_sdk.py` | 测试 | EDAF1DC0… → AC712216… |
| `mcps/zynq_mcp/tests/test_pl_bridge.py` | 测试 | 00727113… → EF139B09… |
| `mcps/zynq_mcp/tests/test_o6_skill_contract.py` | 测试 | 47949CEE… → D9FBC15E… |
| `skills/zynq_dev/appendix_mechanics.md` | 文档(Skill) | 632BB90C… → 365EEA9D… |
| `docs/development/mcp/B11_plan.md` | 文档 | B510DC84… → D97C9D03… |
| `docs/development/mcp/B11_remediation_round_report.md` | 文档（新，本文档） | NEW → E615B9DF… |

完整 SHA256 见 §8 附录（before/after 全长）。

## 7. 机械门禁逐项

1. 完整非硬件回归（从项目根目录 `python -m pytest mcps -m "not host_live and not device_live" -q`）：**1369 passed / 1 skipped / 39 deselected / 0 failed**（基线 1337/1/38/0 → passed +32、collected +33；1 skipped 仍为 B02 POSIX-only）。**collected/passed 无下降。**
2. `--collect-only`：**1409 collected**（基线 1376）；host_live=35、device_live=4。
3. host_live 专项：见 §5（真实 Vivado 2023.1，Vivado 可用、板卡无需）。
4. 新写测试空 pass/宽异常吞噬扫描：`test_b11_heartbeat_remediation.py` 与 `test_platform_atoms.py` 新增部分 grep `^\s*pass\s*$` / `except Exception: pass` → **0 命中**。
5. `.mcp.json` SHA256=d8e397af…（不变，见 §0）。
6. 变更清单与 SHA256 见 §6/§8；报告文档为本文档。

## 8. 附录：before/after SHA256 全长

（HEAD blob → 工作区文件，SHA256）

```
docs/development/mcp/B11_plan.md
  before=B510DC841509D9B0FD9A7227EE7FC7EDB2F4DB5D3D7F2438B0E84C25B83289EB
  after =D97C9D038245929965B2CF9E7BB7E9B05D848E4774E9D1ED44FF3BCD00D82474
mcps/zynq_mcp/control/capabilities.py
  before=EC0B89EB7E98D7023FDB983FE4516E554DEB69AFAAF0986BE28C7F1530963F8F
  after =B21D71961AA65F16D11438AAD3B2EA7687AAF4396B3C6547D34A5804F36FF238
mcps/zynq_mcp/control/domain_runner.py
  before=6E29D666D632E01B8B6A3B5DD975130A3DAE547C9F0F1EC6E757B4B10E17DA9F
  after =14F1309D01E858340FEE971019CA4DECCBAC7F3147E5547B8B68B76E4936DE8E
mcps/zynq_mcp/control/execution_gate.py
  before=CBED00121B3EF1E4CF620A37224F9DCF596C21A1D1128A3C1B53D0827CA9918E
  after =117B43975EA248E1004A7E5C809E531C628F0BE59FD67A6513767A8904B0ADAB
mcps/zynq_mcp/control/single_worker.py
  before=138E094B1A0FA3E3F1D0CD5C016511793219D9CC0915D9979BE5765BC99A5686
  after =D70B5188D83A92D3C63B61CD9994503D2739DEA0EA9CC43DD24A25C9026724CE
mcps/zynq_mcp/dispatcher.py
  before=950C28934EDAF44FA9C6DB46E99A410C9D9139946CE654B5F4D3EE1F78E52DD8
  after =0064D370FD0A262A42C1CE2579FEFA407EEB6799760CD0DB5CFBD503CBCA0F22
mcps/zynq_mcp/domains/platform/platform_atoms.py
  before=F622D165C5A8385BA708911858C9F779CDFD3D7D0DA014CFAD2D8DF886102142
  after =52F637FB909366C8593022F4FC37666F3AAA57730CEDD565FAC0003C7951135E
mcps/zynq_mcp/domains/platform/platform_domain.py
  before=C9F5A4A2AC10BBA048E0F19C8E4A8AEA35FCDC6FCCD02AE2A7048FBC77C98C5B
  after =9C4EF4A44BE1C1F1296838885CE5D437A6DAA186812312A66B259C4082459597
mcps/zynq_mcp/tests/test_b05_platform_public.py
  before=897DE1D4EBCF37F15876EECBD5D47F4A592236CEAE0336367492EDD0F4FB4887
  after =1498FF9F22C8AF101FAF7E08C79F1EF1CF9DD6B9AF9B730AD16BE4D3188B2B02
mcps/zynq_mcp/tests/test_b11_heartbeat_remediation.py
  NEW  =B9163081F6CF11209DE1040D1E5FBCBD951ACA30AC79EE9170584E9A49EF89D4
mcps/zynq_mcp/tests/test_o6_skill_contract.py
  before=47949CEE297EF98B567504D5AC65D27F1F6747E45CA044CF578D6A0F92A38169
  after =D9FBC15E30CC2A818D8CF22711D26B902CBF7BAC562D4DD451C98DE9062E7D92
mcps/zynq_mcp/tests/test_pl_bridge.py
  before=0072711323A5C33A481FAF4A8CDB42A0F8CB5472EC4F8916560945E5848BF11D
  after =EF139B09204FABD508A63BD7DAF405EA7E6C3A1244EAD6176494C901BE3141EE
mcps/zynq_mcp/tests/test_platform_atoms.py
  before=2F82C8CFAC1AE1C78833FCECBD03D53683C139B58A434C6AE7B1F02648D7C02F
  after =DDBDE2139E77E0809FC96F5C5A84DCD31CB6A0F763DC55E3DC9D552D0E91FFA4
mcps/zynq_mcp/tests/test_r1_mcp_sdk.py
  before=EDAF1DC0B08FE5029DD6BDF05926E1F4744C07DD61747EE4F4C0BAE0064A154F
  after =AC71221662A808EA742D3E0E700D0950EE97B16B2AE6D0A4AE925E986F78F475
mcps/zynq_mcp/tests/test_r2_adapter.py
  before=09F41589E7B37DA14C85BFF1A47E2497C421CF3FB4AA68EB06B87755469C3E3A
  after =E4DD43A03E1DE1D5063175FA6B24975BCC7AA8A75C01B23A85F10890D0FFD956
mcps/zynq_mcp/tests/test_r3_1c_public.py
  before=789C5C4276C59FFA5225B00E40820A37CA76A9329FCC6C57A3D9BE66407120C4
  after =CCFD292651DE4179BE118ABD036F77788656E6CAFF55A89BBF938EE9B203C525
mcps/zynq_mcp/tests/test_r3_runner.py
  before=5F02AC0DC5E99197EEC6A5C29C90D58A7814C46780AEB3897E77B240C7169862
  after =7E6D54D4B75BB89CCAE48F831EE1ED24448FCD189C4E4B28CE229F4AB9C675EC
skills/zynq_dev/appendix_mechanics.md
  before=632BB90CEF0627001C802E5C471DC93CE0388F45E8D748BF03FC9BD3BDB67524
  after =365EEA9D112FAB8F70144C18FC858EBC382513F073C54746ED3E3ECBCDB4F1AB
```

## 9. 测试数量映射（旧→新，无净减）

- 修改：`test_r3_runner.py` `test_R3X04_p5_stale_heartbeat`（1）→ `test_R3X04_p5_stale_heartbeat_admitted_when_process_alive`（1，等价映射：同 P5 状态机 + 真实进程证据，语义按用户定调）+ 新增 `test_R3X04b_p5_missing_heartbeat_still_blocks`（1）→ runner 测试 45→46。
- 修改：`test_platform_atoms.py` 内 TestSetAddress（2 断言更新 + 新增 2）、TestValidate（+1 D7 回归）、TestConfigurePs7（+1 D0）、TestGetStatus/TestAddIp/TestListIps（D8 断言更新）、TestExportManifest（D8/D9 断言更新）、TestAssignAddresses（+1 SEG_ 解析）、TestMakeExternal（+1 接口 pin 缺失）；新增 TestAssignAddresses(5)/TestMakeExternal(7)/TestSynthesize(6)/TestTclErrorClassification(4) → 平台原子组件文件（+`test_b05_platform_component.py`）合计 105→**107**。
- 修改：`test_b05_platform_public.py`（host_live）——最小链 `_run_atom_sequence` 补 `platform_connect_clock`（FCLK_CLK0→M_AXI_GP0_ACLK）：旧最小链在 validate 无 `-force` 时被缓存掩盖了真实缺陷（BD 41-758），D7 修复后暴露；等价映射：补连接后的序列镜像 B05 已验证 Tcl（与移除快捷路径等价）；`test_extra_property_rejected` 断言修正——mcp 1.28.1 把 schema 拒绝以 isError CallToolResult 返回而非抛异常（**在 HEAD 上复现同样失败，为既有 host_live 测试缺陷，非本轮引入**），修正后接受两种 fail-closed 形态（意图不变：额外属性必须被拒）。
- 新增：`test_b11_heartbeat_remediation.py` 8 个；`test_b05_platform_public.py` host_live +1（整改链）。
- 计数断言 6 处 `==100→==103`；`test_o6_skill_contract.py` required 工具集 +3。
- 汇总：collected 1376→**1411**（+35）；passed 1337→**1371**（+34 = 1411 collected − 35 host_live − 4 device_live − 1 skipped 的 1371 吻合）。

## 10. 禁区零触碰声明

`boards/`、`docs/architecture_ai_zynq7020.md`、`docs/brick_development_plan.md`、`README.md`、`CLAUDE.md`、`Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/`、`workspaces/`、`validation_projects/`、`.mcp.json`：**零改动**（`git status --short` + `git diff --name-only` 复核；`.mcp.json` 哈希不变）。`docs/development/tests/B11_phase3_whitebox_report.md` 未改动（阶段③报告保持原样）。

## 11. 遗留 P2 技术债

- `platform_set_address` 的 `set_property CONFIG.C_BASEADDR/C_HIGHADDR` 在 Vivado 2023.1 对已存在 BD 段 read-only（`[Common 17-107]` CRITICAL WARNING，非 ERROR → 原子返回成功但 no-op）：D1 主路径已由 `platform_assign_addresses` 承担，set_address 保留为显式覆盖尝试 + 短名解析；不无限扩大本轮范围。
- D8 根因修复覆盖 platform 域全部结果型查询；PL/PS 域桥接工具若依赖裸返回值查询，真机行为同受「stdout 捕获」模型约束（本轮未触碰 PL/PS 域，不扩大范围）。
