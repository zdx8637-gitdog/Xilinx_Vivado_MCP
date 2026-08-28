# 附录：通用机制（Mechanics）

> 本附录是 S0–S8 全部阶段共用的**通用执行机制**。所有模板均使用占位符
> （`<...>`），具体值一律来自需求文档、板卡物理事实或 S3 架构决策——
> 本 Skill 不预设任何具体外设、地址、marker。

## 1. 会话与 Operation 纪律

### 1.1 Session

- `create_session`：`{"board_id": "<BOARD_ID>", "project_path": "<PROJECT_PATH>"}`
  → 记录 `<SESSION_ID>` 与 `<PROJECT_PATH>`（后续所有 `ps_*` 调用显式携带
  `<SESSION_ID>`）。
- 所有产物写入 `<PROJECT_PATH>`；禁止写入仓库源码目录。
- 收尾 `close_session`（存在活动 Operation 时会被拒绝，先处理活动状态）。

### 1.2 Operation 纪律（command → operation_id → wait）

所有 command tool 调用后先返回 `operation_id`；随后**只**使用 `wait_operation`
或 `get_operation_status`。每次公开响应都保存，形成状态时间线；至少读取：
`status`, `status_source`, `backend`, `observed_state`, `vendor_status`,
`current_step`, `observation_quality`, `last_progress_at`, `artifact_state`,
`deadline_at`, `recommended_action`。

```
command → operation_id
  → wait_operation(operation_id, bounded_timeout)
  → RUNNING + recommended_action=WAIT     → 继续有界等待
  → RUNNING + recommended_action=DIAGNOSE → diagnose_execution，再按返回建议处理
  → RECOVERY_REQUIRED / recommended_action=RECOVER
                                              → 先 diagnose_execution；仅在公开诊断确认
                                                 无活动受控进程/资源后调用 recover_execution
  → SUCCEEDED + artifact_state=PUBLISHED（Manifest 产物型操作） → 下一步
  → FAILED/TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → 停止正常流程，进入 S8
```

- **超时预算**：外层 `wait_operation` 的超时必须**显著大于**内层 op 的时限
  （每轮内部轮询有 0.5–1s 间隔开销）。规则：外层 ≥ 内层 + 30s。
- `wait_timed_out=true` 且 Operation 仍 `RUNNING` → 不是失败，不重新提交同一
  command；按 `recommended_action` 继续 WAIT 或 DIAGNOSE。
- `ps_*` 前缀 tool 一律显式传 `session_id`；遗漏返回
  `INVALID_ARGUMENT / SESSION_ID_REQUIRED`。

### 1.3 fail-closed 判定

无法确认真实状态时返回明确错误并停止，**不推断成功、不推断运行中、不推断已
释放**。终态判定只认 Ledger 真实 backend observation + Manifest/Artifact 的
SHA256 证据。

## 2. Manifest 链与 verify_consistency 规则

### 2.1 三类 Manifest

```
<PROJECT_PATH>/manifests/
├── platform/sha256_<REVISION>.json    ← S5 Platform 完成证据
├── pl/sha256_<REVISION>.json          ← S5 PL 完成证据
└── ps/sha256_<REVISION>.json          ← S5 PS 完成证据（ps_compile 成功终态自动发布）
```

Manifest 全部由 MCP **自动发布**，智能体不得手工生成/修改。

### 2.2 verify_consistency（纯 query）

```json
{
  "platform_manifest_path": "manifests/platform/sha256_<REVISION>.json",
  "pl_build_manifest_path":  "manifests/pl/sha256_<REVISION>.json",
  "ps_build_manifest_path":  "manifests/ps/sha256_<REVISION>.json",
  "board_profile_sha256":    "sha256:<BOARD_PROFILE_SHA256>"
}
```

正式契约要求**三类 Manifest 全部存在**；任何目录 0 个或多个当前候选都先停止
并报告，不得用空字符串、省略参数或手工新建 Manifest 来得到部分校验。

**路径规则（D11 修复后）**：三条 Manifest 路径必须传**绝对路径**；或传
**相对路径**并显式给 `resolve_root`（实现按 `resolve_root` 解析相对 Manifest
路径）。两者皆缺时实现返回明确的 `INVALID_ARGUMENT` 错误并全部规则 skipped
（fail-closed），**绝不静默**。注意 `resolve_root` 同时用于解析 Manifest 内部
的产物相对路径（artifact 文件）。

### 2.3 7 条校验规则（通用契约）

| # | 规则 | 说明 |
|---|------|------|
| 1 | `pl_build.built_from_platform_revision == platform.platform_revision` | PL 与 Platform 版本一致 |
| 2 | `ps_build.built_from_platform_revision == platform.platform_revision` | PS 与 Platform 版本一致 |
| 3 | `ps_build.platform_xsa_sha256 == platform.xsa_sha256` | PS 用 XSA 即 Platform 产出 |
| 4 | `ps_build.xparameters_addrs == platform.address_map` | 地址映射逐字段一致 |
| 5 | `ps_build.board_profile_sha256 == board_profile_sha256` | PS 板卡配置一致 |
| 6 | `pl_build.board_profile_sha256 == board_profile_sha256` | PL 板卡配置一致 |
| 7 | 所有 artifact 文件存在 + SHA256 匹配 | 文件完整性 |

只有 `all_passed == true`、`errors == []`、`summary.failed == 0` 且
`summary.skipped == 0` 才能继续。

## 3. platform 原子序列模板

**注意**：本框架不使用任何「一键快捷路径」工具——Platform 一律由以下原子序列
构建（每个原子独立可观测、可恢复、不推进 stage）。IP 选型、配置、连线、地址
全部来自 S3 决策与需求文档（占位符）。

**BD 内步骤顺序**：create → add → connect → **assign（地址分配）** →
**make_external（端口外部化）** → validate → wrapper → **synthesize（合成）** →
export（XSA → Manifest）。**S3/S5 决策说明：地址分配与端口外部化必须在导出
（export_hardware / export_manifest）前完成**——未分配的 slave 段会使
validate 报真实告警、PS 域无法寻址；未外部化的端口不会出现在 wrapper/XSA
中；不合成则 XSA 缺 HDF，PS 域 `ps_create_platform` 必然失败。

**决策规则（连接/外部化前命名）**：`platform_connect_interface` /
`platform_connect_clock` / `platform_connect_reset` / `platform_make_external`
中的**引脚/接口名**（`<IF_SOURCE>`、`<IF_DEST>`、`<CLK_SOURCE>`、
`<CLK_TARGET>`、`<RST_SOURCE>`、`<RST_TARGET>`、`<IP>/<PIN>`、`<PORT>`）
**必须来自真实对象查询**——工程内 IP 边界描述、BD 单元/引脚清单等实际查询
结果，**不得臆造命名**（臆造的引脚名会让 create_bd_port 成功但连线失败，
留下悬空端口，validate 报 critical warning）；**查询不可得时停并报告**，
禁止用猜测的名字继续连线或外部化，也禁止为绕过查询而假设名称。

| # | 工具 | 参数（占位符） | 成功条件 |
|---|------|----------------|----------|
| 1 | `platform_create_design` | `{"name": "<DESIGN_NAME>", "part": "<PART>"}` | Operation SUCCEEDED |
| 2 | `platform_add_ps7` | `{"preset_name": "<PRESET_NAME>"}`（板卡 ps7 preset） | SUCCEEDED |
| 3 | `platform_configure_ps7` | `{"config": <CONFIG_按需求>}` | SUCCEEDED |
| 4 | `platform_add_ip` | `{"vlnv": "<IP_VLNV>", "instance_name": "<IP_INSTANCE>", "properties": <按需求>}` | SUCCEEDED；幂等 |
| 5 | `platform_connect_interface` | `{"source": "<IF_SOURCE>", "destination": "<IF_DEST>"}` | SUCCEEDED |
| 6 | `platform_connect_clock` | `{"source": "<CLK_SOURCE>", "targets": ["<CLK_TARGET>", ...]}` | SUCCEEDED |
| 7 | `platform_connect_reset` | `{"source": "<RST_SOURCE>", "targets": ["<RST_TARGET>", ...]}` | SUCCEEDED（极性不自检，由决策者选对引脚） |
| 8 | `platform_set_address` | `{"segment": "<SEGMENT>", "base": "<BASE_ADDRESS>"}` | SUCCEEDED；`<SEGMENT>` 支持 `<ip>/<INTF>` 短名（自动解析到真实段名 `<ip>/<INTF>/Reg`）；地址最终落实以 assign 为准 |
| 9 | `platform_assign_addresses` | `{"segments": [<SEGMENT_LIST>]}`（可选；缺省=全部未分配段） | 返回**非空** `address_map`（每 master OFFSET/RANGE）；幂等 |
| 10 | `platform_make_external` | `{"port_name": "<PORT>", "source_pin": "<IP>/<PIN>", "direction": "in|out|inout", "width": <W>}`；接口类传 `"interface": true` | 端口已创建并连接；wrapper 中将出现该端口 |
| 11 | `platform_validate` | `{}` | 无 error / critical warning（`-force` 强制重验，防止缓存假阳性） |
| 12 | `platform_generate_wrapper` | `{}` | 记录 `wrapper_rel`（S5 PL 用）；wrapper 含已外部化的端口 |
| 13 | `platform_synthesize` | `{"jobs": <N>}`（可选） | 运行 STATUS 含 complete；**XSA 才含 HDF** |
| 14 | `platform_export_hardware` | `{"path": "<XSA_PATH>"}` | XSA 存在且 SHA256 校验；**必须已在 synthesize 之后** |
| 15 | `platform_export_manifest` | `{}` | Manifest 自动发布；记 `platform_revision` 与 `address_map` |

内层时限按公开能力上限；外层 `wait_operation` 超时 = 内层 + 30s（合成步给足
预算：`platform_synthesize` 内层可达 1800s）。

## 4. PL 构建链

| 顺序 | 工具 | 参数（占位符） | 成功后的证据 |
|------|------|----------------|--------------|
| 1 | `pl_generate_system_top` | `{"wrapper_path": "<wrapper_rel>"}` | `rtl/system_top.v` 存在；wrapper 与 Manifest 交叉引用一致 |
| 2 | （工作区写） | 需求约束文件写入 `<PROJECT_PATH>/xdc/<CONSTRAINT_FILE>` | 文件存在（允许的工作区操作） |
| 3 | `pl_create_project` | `{"name": "<PL_NAME>", "part": "<PART>", "sources": [BD, wrapper, top], "constraints": [<CONSTRAINT_FILE>], "project_dir": "<PL_PROJECT_DIR>", "top": "<TOP>"}` | SUCCEEDED；路径缺失 fail-closed |
| 4 | `pl_generate_target` | `{"target_type": "synthesis"}` | BD output products 生成 |
| 5 | `pl_synthesize` | `{"top": "<TOP>"}` | 综合完成 |
| 6 | `pl_place` | `{}` | 布局完成 |
| 7 | `pl_route` | `{}` | 布线完成 |
| 8 | `pl_analyze_timing` | `{}` | `timing_met == true` |
| 9 | `pl_generate_bitstream` | `{"path": "<BITSTREAM_PATH>", "force": true}` | bitstream SHA256 有效；**PL Manifest 自动发布**，`artifact_state == "PUBLISHED"` |

**PL Manifest 终态门禁**：`pl_generate_bitstream` 只有在 bitstream 存在且
SHA256 已验证、timing 前置证据通过、约束被发现并交叉引用、Manifest 自动发布
且合法时才 `SUCCEEDED`。禁止把「bit 文件存在」当作成功，禁止手工补 Manifest。

> **接口时序仿真工具要点**：对外设接口时序验证按公开 MCP 顺序
> `pl_compile_sim`（xvlog 编译 RTL/testbench）→ `pl_elaborate_sim`（xelab 细化）
> → `pl_run_simulation`（xsim 运行）→ `pl_parse_sim_log`（解析日志取
> PASS/FAIL 机读结论）；同 phase5 强制步骤，仿真 PASS 前不得 `pl_generate_bitstream`。

## 5. PS 软件链

所有 `ps_*` 调用显式传 `session_id`。

| 顺序 | 工具 | 参数（占位符） | 说明 |
|------|------|----------------|------|
| 1 | `ps_import_hardware` | `{"xsa_path": "<XSA_PATH>", "project_path": "<PROJECT_PATH>"}` | **XSA staging**：若 `xsa_path` 与内部拷贝目标同文件会 `IMPORT_HW_FAILED: same file`；先复制 XSA 到 `<PROJECT_PATH>/inputs/platform.xsa` 再传入（输入文件 staging，非构建逃生通道） |
| 2 | `ps_create_platform` | `{"name": "<PLATFORM_NAME>", "project_path": "<PROJECT_PATH>"}` | XSCT platform |
| 3 | `ps_create_bsp` | `{"platform_name": "<PLATFORM_NAME>", "project_path": "<PROJECT_PATH>"}` | standalone BSP |
| 4 | `ps_create_app` | `{"name": "<APP_NAME>", "project_path": "<PROJECT_PATH>"}` | app |
| 5 | （工作区写） | **自写** `<PROGRAM_SOURCE>`（`<PROJECT_PATH>/src/<PROGRAM_SOURCE>`） | 按需求文档判定规范编写，不复用历史文件 |
| 6 | `ps_add_sources` | `{"app_name": "<APP_NAME>", "files": ["<PROGRAM_SOURCE>"]}` | 源码拷入 app src |
| 7 | `ps_compile` | `{"app_name": "<APP_NAME>"}` | **唯一正式编译入口**；成功终态 finalizer 校验 ELF 并自动发布 PS Manifest（**PS Manifest 已自动发布**是成功条件） |
| 8 | `ps_get_build_status` | `{"session_id": "<SESSION_ID>"}` | 取 `<ELF_PATH>` |
| 9 | `ps_read_elf_info` | `{"elf_path": "<ELF_PATH>"}` | ELFCLASS32 / little-endian / EM_ARM |

**编译门禁**：`ps_compile` 是唯一正式编译入口（shell 编译器/链接器禁止）；
ELF 存在但 Manifest 门禁失败仍是失败（`artifact_state == "PUBLISHED"` 才过关）。
`ps_set_compiler_options` 仅支持宏定义（defines），按需求需要才调用——宏经
`ps_compile` 的构建配置注入（`app config … define-compiler-symbols`，每符号
一条，`app build` 本身无 defines 选项）真实参与编译（D10 修复后），编译产物应能
核验宏生效（如 `#ifdef` 分支输出的差异字符串/指令）。

### 5.1 PS 端并行输出引脚驱动要点（通用知识，不含具体外设名）

当需求要求 PS 端引脚驱动输出型外设（如点亮某个低电平有效指示灯）时，按以下
要点编写裸机初始化与读写，**顺序不可省**：

1. **方向寄存器必须显式配置为输出**：方向位 1 = 输出方向、0 = 输入方向；
   复位后默认输入，不配置则引脚永远不驱动。
2. **输出使能寄存器必须显式配置为使能**：该寄存器位 1 = 输出驱动使能、
   0 = 输出驱动禁用（高阻/三态）。注意它与 PL 侧三态寄存器（1 = 三态）语义
   **相反**——按本机驱动源码或寄存器手册逐位核对极性，禁止照搬另一侧写法。
3. **数据写入用数据寄存器（RW）**：按位写 0/1 驱动引脚电平；只写需要控制的
   位所在寄存器，其他位按驱动库惯例（掩码写或整字写）处理。
4. **读回验证必须读引脚状态读回寄存器（DATA_RO 类，只读）**：它反映引脚
   真实电平；**禁止读写入镜像/写侧寄存器**——写镜像回读恒等于刚写入的值，
   会产生「自洽假象」：UART 全对但引脚实际没动（真板观察即此症状）。
5. **极性（写 0=亮 / 写 1=亮）由需求文档与板卡物理事实定义**，固件按需求
   直写，不臆造。
6. **验证闭环**：配置方向+输出使能 → 写数据 → 读回（真实状态寄存器）逐位
   核对 → 结合物理观测（用户/录像）确认亮灭模式。

典型初始化序列（寄存器名以实际平台为准）：

```
方向寄存器   |= 目标引脚掩码        // 1 = 输出方向
输出使能     |= 目标引脚掩码        // 1 = 输出驱动使能（极性以手册为准）
数据寄存器    = 模式值              // 驱动电平（极性按需求）
读回         = 引脚状态读回寄存器    // 真实电平，非写入镜像
```

## 6. UART 捕获（start → wait → stop）

**顺序强制**：capture 必须先于部署序列中的 `ps_run_target` 打开——先开窗户再
放跑。UART tools 都是 command tool，调用后 `wait_operation` 取结果。

| 步骤 | 工具 | 参数（占位符） | 验证 |
|------|------|----------------|------|
| 1 | `ps_start_uart_capture` | `{"session_id": "<SESSION_ID>", "port": "<UART_PORT>", "baudrate": <BAUDRATE>}` | 记录 `operation_id` → `wait_operation` → 取 `capture_id`（`wait["data"]["result"]["data"]["capture_id"]`） |
| 2 | `ps_wait_uart_capture` | `{"session_id": "<SESSION_ID>", "capture_id": "<CAPTURE_ID>", "markers": ["<PASS_MARKER>"], "timeout_s": <内层预算>}` | `matched` |
| 3 | `ps_stop_uart_capture` | `{"session_id": "<SESSION_ID>", "capture_id": "<CAPTURE_ID>"}` | 取完整 `text` |

**marker 纪律**：

- **marker 全部来自需求文档**（`<PASS_MARKER>` / `<FAIL_MARKER>`），不臆造；
- `ps_wait_uart_capture` 要求列表中**全部** marker 出现才返回 `matched`；
- 不得把互斥的 `<FAIL_MARKER>` 加入同一必需列表；停止 capture 后仍必须检查
  完整文本中不存在 `<FAIL_MARKER>`；
- **超时预算**：外层 `wait_operation` ≥ 内层 `timeout_s` + 30s。

**\x00 清理**：裸机程序经 32-bit 写操作 8-bit TX FIFO 会在每字符间插入
`\x00\x00\x00`。`evaluate_observation` 前必须
`uart_text = uart_text.replace("\x00", "")`，否则 marker 匹配失败。

**波特率**：以 S1 物理事实与需求文档为准；实际波特率可能偏离标称值，
`ps_diagnose_uart_clock` 读取寄存器计算真实波特率，`baud_match == false` 时用
`computed_baud` 重启 capture。

## 7. 观测判定（evaluate_observation）

```json
{
  "uart_text": "<清理后的捕获文本>",
  "pass_marker": "<PASS_MARKER>",
  "fail_marker": "<FAIL_MARKER>"
}
```

- **marker 显式传需求文档给定的值**，不得省略或用默认值；
- `verdict`：`PASS` / `FAIL` / `TIMEOUT` / `INCOMPLETE`；**PASS marker 优先于
  FAIL marker**；
- 产物：`<PROJECT_PATH>/evidence/uart_result.json`（verdict + 完整文本）。

## 8. 恢复阶梯（Recovery Ladder）

任何阶段失败时，按以下顺序（不跳级、不盲目重试）：

1. **分类**：对照 S8 的 8 类错误（ENV / TOOL / PLATFORM / PL_BUILD / PS_BUILD /
   JTAG / UART / ARTIFACT_STALE）确认类别；
2. **收集证据**：`get_operation_status`（op 详情）、`get_execution_state`
   （lane/stage/backend/资源）、`diagnose_execution`（Server 自我诊断）；
3. **服从 `recommended_action`**：WAIT 只等；DIAGNOSE 只诊断；RECOVER 先确认
   无活动受控进程/资源后调用 `recover_execution`；STOP 停止并报告；
4. **不得使用逃生通道**：所有 EDA/build/Manifest/JTAG/UART/恢复动作通过公开
   MCP tools 完成；不导入内部模块、不启动工具进程、不编辑 Ledger、不按名杀
   进程、不在 MCP 之外重试不确定结果。

## 9. Session / JTAG 清理

- 收尾：`close_session`（存在活动 Operation 时被拒绝；先处理活动状态）；
- JTAG 残留：`ps_clear_debug_session`（清断点/调试器残留）、
  `ps_reconnect_target`（断开重连）、`ps_recover_target`（自动 cascade：
  halt → reset → ps7_init → verify）；
- 重跑：重新 `create_session` 必须使用**新的** `<PROJECT_PATH>` 目录。
