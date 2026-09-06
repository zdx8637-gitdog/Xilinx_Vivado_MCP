# 附录：通用机制（Mechanics）

> 本附录是 S0–S8 全部阶段共用的**通用执行机制**。所有模板均使用占位符
> （`<...>`），具体值一律来自需求文档、板卡物理事实或 S3 架构决策——
> 本 Skill 不预设任何具体外设、地址、marker。

## 1. 会话与 Operation 纪律

### 1.0 即时落盘（上下文抗损红线）

**上下文随时可能损坏/压缩，文件是唯一可靠记忆**——因此：

1. **发现即落盘**：每个工具问题/偏离，当场写入
   `<PROJECT_PATH>/evidence/FINDINGS.md`（模板：Tool/现象/复现/影响/证据/
   建议分类），**不攒在上下文里等汇报**——对话损坏时不丢任何发现。
2. **阶段成果即文档**：S0–S4 文档随阶段推进即时成稿；每步 MCP 响应即时存为
   evidence 日志（带时间线）；修复动作写清"现象→根因→对策→证据"。
3. **交接保险**：重大进展节点更新 HANDOFF.md（当前交付物 SHA + 下一步操作
   事实）——即使本会话死亡，接续者只读文件即可续跑。
4. 参照形态：本框架白盒/黑盒工作区的 `evidence/` + `FINDINGS.md` + `HANDOFF.md`
   即为标准落盘形态。

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

## 10. 自验证配方（Test Stimulus / Event Counter / Pattern Checker / POST）

### 10.1 TPG 图案配方（数据通路 Test Stimulus）

| 模式 | 内容 | 能定位的错误 |
|------|------|-------------|
| 常数轮转 | N 个互异常量按位置轮转（`模式值 = <常量表>[位置 mod N]`） | 位置错位、整段重复/缺失 |
| 地址/序号标记 | `模式值 = 自身位置标识` | 地址错位、跨段串扰、读错位置 |
| 全 0 / 全 1 | 固定常值 | stuck-at、固定位错误 |
| walking-1 / walking-0 | 单比特行走（`0001→0010→0100→1000→…` 及反相） | 位道错接、位交换、宽度不匹配 |
| PRBS | LFSR + 种子，收端同种子重生成比对 | 随机位错误、间歇错误 |

选择指引：先常数轮转定位「段级」，再地址/序号标记定位「位置级」；位级用
全 0/全 1 与 walking 系列；随机性错误才上 PRBS。**禁止只用全 0 / 全 1**
（对位交换与位置错位无鉴别力）。常量互异且与真实数据可区分。

### 10.2 Event Counter 测量窗口

```
寄存器组： <事件>_CNT + <事件>_SNAP（影子锁存）+ OVERFLOW_STICKY
流程：     CLEAR → ARM → RUN → SNAPSHOT → READ
中止：     STOP / DISARM（可选）
```

- READ 只读影子寄存器；OVERFLOW_STICKY 置位即本轮 FAIL（或加宽位宽重跑）；
- 跨时钟域计数一律以 SNAPSHOT 为准；
- **端到端计数比对**（含排空）不属于本窗口，见 10.3。

预期关系模板（由 PS 侧 POST 按配置计算，RTL 不写死）：

| 关系类型 | 示例形态（占位符） | 判定 |
|----------|-------------------|------|
| 1:1 型 | `<发出事件>_CNT == <收到事件>_CNT` | 相等 |
| 周期型 | 每 `<周期>` 恰 `<K>` 次 | 整除余数校验 |
| 沿型 | 每次 `<发出事件>` 恰 1 个 `<事件沿>` | 逐事件核对 |

### 10.3 Pattern Checker + Observation Point 配方

```
测试模式流量： 逻辑记录 { SEQ, PATTERN, CHECK }
              物理编码不得改变被测通路位宽/握手/流控
最小扰动推荐： TPG = f(seq)（只传 PATTERN）；收端 expected = f(local_seq)
可选：         sideband 通道携带元数据（不改 payload）
正常模式流量： <REAL_DATA> 原样通过
```

```
Pattern Checker 结果：
  恒有效： { total, mismatch_count, first_bad_index }
  条件字段： { last_seq, seq_gap_count, first_bad_seq }
            仅当 SEQ 显式传输 / PATTERN 可无歧义反推 SEQ /
            checker 带明确 resynchronization 时有效
```

```
Observation Point：
  基础字段： { count, unit, overflow_sticky }
             + 按需 { state, status, error_flags, timestamp,
                      latency, period, occupancy }
  测试字段： { mismatch_count, first_bad_index }
             + 条件字段 { last_seq, seq_gap_count, first_bad_seq }
```

```
Data Accounting（守恒比对）： 相邻 OP 按 unit + 通路契约的守恒关系比较
                              （如 1 SAMPLE = 2 BYTE）；仅 1:1 段要求 count 相等

排空后比对（Drain-before-check）： STOP_SOURCE → QUIESCE/DRAIN → SNAPSHOT
  相邻 OP 计数差必须满足其一，否则不得判 FAIL：
    1. 通路已 quiescent / drained（守恒关系下差为 0）；
    2. 差值由可观测 occupancy / in-flight 完全解释。

逐段隔离： 故障段 = 守恒关系下相邻 OP 差值非零（或 first_bad_index 首次
           出现的 OP 对）
```

- Pattern Checker 判定：`mismatch_count == 0`；校验算法任选（checksum/CRC）；
- `<SEQ>` 位宽满足通路最大在途数据量（防回绕歧义）；允许有界回绕并在判定
  时校验。

### 10.4 POST 判定块格式（机读，KV 文本，支持 N/A）

```
SELFTEST BEGIN RUN=<N> BUILD=<SYSTEM_BUILD_ID> PL=<BITSTREAM_ID> PS=<ELF_ID>
SELFTEST L1 RUN=<N> STEP=<步骤名> RESULT=PASS|FAIL|N/A <数字字段>
SELFTEST L2 RUN=<N> STEP=<步骤名> RESULT=PASS|FAIL|N/A <数字字段>
SELFTEST DONE RUN=<N> L1=PASS|FAIL|N/A L2=PASS|FAIL|N/A
```

- `<RUN>` 每次自检递增；`<SYSTEM_BUILD_ID>` 由 Platform（XSA）+ bitstream +
  ELF 的 artifact manifest 共同生成；`<BITSTREAM_ID>`/`<ELF_ID>` 为该次构建
  产物标识（Manifest revision / SHA256 短码）；
- `N/A` 仅用于「工程不存在该层级适用对象」（如无外部握手接口的 L2），
  不得以 N/A 掩盖适用但未执行的测试；
- `<数字字段>` = 该步判定数字（`MISMATCH=0 TOTAL=<N>`、`EVENT=<名>
  EXPECT=<M> GOT=<N>`、`OP0_COUNT=<N> OP1_COUNT=<N> UNIT=<单位>`）；
- 判定以 `SELFTEST DONE` 行为准；单步 FAIL 必须体现在汇总行；
- 保持 KV 文本格式（不引入 JSON），便于 UART 逐行解析。

### 10.5 自检证据归档

POST 判定块文本随 7c 捕获一并保存；`evaluate_observation` 的 PASS 判据
（需求 marker）与 L1/L2 判定块互为独立证据，缺一不可。

## 11. 行业依据（测试理念出处）

本框架测试纪律是以下思想的**借鉴与工程化合并**，不是任何标准的符合性实现：

| 理念 | 行业出处 |
|------|----------|
| 测试能力是设计交付物 | DFT / BIST 思想：[DFT & BIST 课程](https://smtnet.com/training/index.cfm?fuseaction=view_event&event_id=461&company_id=50816) |
| 内部确定性测试源 + 汇端校验 | PRBS 图案生成/检查：[Xilinx 7 系列 GT 收发器手册](https://manualzz.com/doc/o/kus2n/xilinx-7-series-user-manual-83-h0_0011_07fe#14)；[LiteX Memory Testing and BIST](https://deepwiki.com/enjoy-digital/litex/7.2-uart-and-serial-communication) |

## 12. AXI 握手缺陷模式库（开发自查清单）

手写 AXI-Lite / AXI-Stream 从设备时，逐项自查以下同族缺陷（均真板实证）：

| # | 缺陷模式 | 症状 | 修法 |
|---|---------|------|------|
| 1 | BVALID/RVALID 零宽脉冲（同拍置位+清除） | 主机永远等不到响应，总线挂死 | 响应置位后保持至 READY 握手完成 |
| 2 | 同 always 块 last-write-wins 覆盖（`X<=1; if(RREADY) X<=0`） | 响应瞬时消失 | 用互斥分支，勿依赖赋值顺序 |
| 3 | READY 早置位握手（对端 ARVALID 单拍即撤/早置 READY） | 握手错过、总线挂死 | 按「注册使能+保持」握手实现；仿真用对端行为模型验证 |
| 4 | 多驱动信号（两个 always 块写同一 reg） | 仿真 last-write-wins 掩盖、综合 CRITICAL、真板 X/常量 | 单驱动块；synth CRITICAL=0 门禁 |
| 5 | 无驱动输出（RRESP/BRESP 等） | 综合告警、真板 X | 响应总线恒 OKAY 显式驱动 |
| 6 | X 传播（复位/未初始化状态进入握手逻辑） | 仿真 X、真板不定态 | 复位全清零 + 仿真 X 断言 |

自查要求：上板前 synth 日志 CRITICAL WARNING=0；仿真用「对端行为模型」
（含 ARVALID 单拍即撤、READY 早置位等真实对端时序）而非理想模型。
| 事件计数监视（不看数据内容） | [AMBA AXI Performance Monitor IP](https://semiiphub.com/ip/datasheet/amba-axi-performance-monitor-7051)；[RMON 计数器](https://manual.yamaha.com/network/switches/swx2310p/td/en/Rev.2.02.31/oam_oam_rmon.html) |
| 内嵌仪器标准化（借鉴思想） | [IEEE 1687-2014（IJTAG）](https://standards.ieee.org/ieee/1687/10896/)；[系统级 DFT 指南](https://www.jtag.com/system-dft-guidelines-boundary-scan-at-system-level/) |
| 片上自检工程范例 | [OpenTitan DV 方法论](https://opensecura.googlesource.com/3p/lowrisc/opentitan/+show/9cae6d97d933f648fc7545dec65c7a25dc1f1a03/doc/ug/dv_methodology/index.md) |
| 测试分层与上电自检 | [嵌入式测试指南](https://theembeddedkit.io/wp-content/uploads/2024/11/Embedded-testing-essential-guide-by-The-Embedded-Kit.pdf)；[FPGA 板级 bring-up 实例](https://github.com/heisaman/PLFM_RADAR/blob/main/docs/bring-up.html) |

## 13. 已知问题与处理建议（真板实证回流，仅通用件）

> 白盒/黑盒真板开发中沉淀的**框架/工具/平台级**通用坑。项目特化问题
> （某项目的上位机口径、版本串约定、具体 BD 细节）不在此表——它们属于
> 项目自身文档。状态：✅ 已修复 / ⚠️ 框架已告警未根治 / 📌 通用设计待办。

| # | 现象 | 成因/状态 | 处理建议 |
|---|------|----------|---------|
| 13.1 | `ps_mem_read` 返回空/失败 | ✅ 已修复：解析曾要求 `0x` 前缀而真实 xsdb mrd 输出不带（`E000102C: 0000000A`）。现返回 `0x%08X` 规范化字；空结果 fail-closed 报 `MEM_READ_NO_DATA` | 报 MEM_READ_NO_DATA = 地址可能未入映射/被阻断：先 `ps_load_hardware`，或手动 xsdb `mrd <addr> <len>` 对账；PL 寄存器读不到属正常（见 13.2） |
| 13.2 | dow 间歇报 `Blocked address ... Reserved address range`；不 loadhw 时读 PL 寄存器报 "PL AXI slave ports access is not allowed" | ✅ 已修复：`platform_export_hardware` 现自动把 address_map 注入 hwh 的 ADDRESSING 段（真板验证 loadhw rc=0 + DAP 直读 PL 寄存器）。旧 XSA（无段）loadhw 仍返回 `addressing_section=MISSING`+告警 | 用新导出的 XSA 部署即可；旧 XSA 遇 dow 受阻 → 重新 export（会注入段），或跳过 loadhw（DAP 默认身份映射对 ELF 运行足够） |
| 13.3 | 改打包 IP 内容重打包后重建，PL 报 `manifest already exists with different semantic content` | ✅ 已修复：PL 摘要已含 `.xci`+`ipshared/**`+`ip_repo` 根下 `component.xml`/`xgui/**`（.pkg_proj/.pkg_log 一次性打包目录排除）；PS 摘要已含 `.cproject`（编译 `-D` 宏）。改任一侧必换摘要 | 改 IP 后确认 **ip_repo 与 .gen/ipshared 两份拷贝都刷新**（只刷一份会消费陈旧产品）；摘要冲突已不应再出现，若仍出现即报框架缺陷 |
| 13.4 | 异常恢复后，下一次采集头部残留 ≤2 拍垃圾 | 📌 流路径上的 `axis_register_slice` 复位脚必须接**受控复位源**（接恒定电平/未受控复位会在 DMA 异常卡死后残留数据） | BD 设计：slice 复位接受控复位输出；停机流程：**排空 FIFO（level==0）后再停 DMA**，避免残留拍污染下次采集 |
| 13.5 | 固件/协议中嵌入的构建版本标识与板上产物不一致 | 📌 版本标识手动维护必漂移（改一侧忘同步另一侧） | 版本标识**从构建 manifest 自动生成**（编译时注入 version 头文件），禁止手写常量 |
| 13.6 | 短时/单行采样的速率判据报 FAIL | 📌 采样时长过短时速率无法测量（分母无效）——判据本身的口径问题，不是链路失败 | 验收判据按样本量区分：速率判据只在足量样本（如整图）上生效，短采样置 N/A 不参与判定 |
| 13.7 | 仿真工具（pl_compile_sim 等）在 PL 构建会话内被拒 `ADAPTER_NOT_READY` | ⚠️ 仿真后端与 PL 构建（direct Vivado）后端互斥 | 仿真**独立会话**执行；位流紧跟 `pl_analyze_timing`（P7 相邻性，见 5_domain_implementation §5.2） |

## 14. 工程层正确姿势库（裸机驱动 / 网络栈 / RTL / 判据推导，真板实证通用件）

> 与 §12/§13 同类：真板开发中「这么写 → 测试暴露 → 回改」沉淀的**通用防错规则**。
> 项目特化的具体数值/协议口径不在此表——它们属于项目自身文档。每条按
> 错误姿势（症状）→ 规范 三列，写代码前逐条自查。

| # | 主题 | 错误姿势（症状） | 规范 |
|---|------|----------------|------|
| 14.1 | UART 驱动非阻塞发送 | 一次发送调用发长帧、不查 FIFO 与计数（帧从未上总线，「调用成功」不代表发出） | 发前排空 TX FIFO（有界等待）→ 发送 → 核对实际计数；长帧分段；全部出帧走单一收口函数，禁止裸调 |
| 14.2 | 网络栈 NO_SYS 轮询 | 主循环只调接收输入函数；发送循环 break 后无人再触发输出（收不到任何包 / 窗口重开后 0 字节排出） | 主循环四件套：轮询接收 ISR 体（清中断位 → 调接收处理函数）+ 接收输入函数 + 定时器函数定周期 + 发送循环退出后主动触发输出，缺一不可 |
| 14.3 | 网络栈窗口/缓冲宏钳制 | 缓冲宏配超大值（窗口类型 16 位时截断为 0，连接后 0 字节发出） | 改 TCP 缓冲/窗口宏前先确认窗口类型宽度；不换宽类型则取值留头；或开启窗口扩展换宽类型 |
| 14.4 | 发送缓冲生命周期 | 栈/堆缓冲不带 COPY 标志发送（DMA 异步读死栈，帧头/校验成垃圾）；控制帧一次性发送不查返回值（拥塞下永久丢失） | 非静态缓冲一律 COPY 标志；必达控制帧先查发送缓冲余量、失败重试、仅对端断开才放弃；可丢数据走丢最旧兜底 |
| 14.5 | 校验热路径 | 逐位查表且表在普通未缓存内存（吞吐差一个量级） | slice-by-N 查表 + 表搬片上高速内存 + 数据按字宽装载；「关软件校验和、靠硬件卸载」的前提是先从驱动源码证实硬件已启用 |
| 14.6 | MMU/D-cache 使能 | 只开 cache 不配套一致性维护（DMA/网络数据面全断）；实验后普通重下（残留态毒化后续会话） | 使能必须配套 DMA 缓冲与描述符的 invalidate/flush；cache/MMU 实验后必须全量部署（复位 + 初始化 + 重灌位流） |
| 14.7 | 链接脚本栈/堆 | 沿用生成器默认栈大小（重负载自检栈溢出崩溃） | 按任务负载显式配置栈/堆；生成器重生成会覆盖链接脚本，改后必须复核 |
| 14.8 | strobe 型控制位 | 只写运行位、指望计数器「默认在数」（实测计数恒 0；此前「正常」是别处恰好武装过） | strobe 类控制位成对显式（清零 → 武装）；状态不得跨调用依赖；快照类回读等握手完成 |
| 14.9 | PL 数据通路复位假设 | 把「重下 ELF」当复位（随后采集头部固定丢样点） | 样点计数/FIFO/流水级残态在可编程逻辑内，ELF 重启不清除；跨会话续跑以全量部署为起点，验收链内禁止中途重下 |
| 14.10 | RTL 多位跨域同步 | 多位信号用位拼接移位同步（高位截断、档位错乱） | 位拼接移位只适用 1 位；多位信号整宽两级/三级赋值同步；评审见到多位拼移位即标红 |
| 14.11 | RTL case default 伪装 | default 兜底值恰好等于某合法档（档位错配被静默掩盖成「碰巧正常」） | default 显式非法处理（告警/挂起）或与合法值不同；验收覆盖全部枚举档位，不得只测 default 命中的档 |
| 14.12 | 判据阈值 | 判据写死标称值（合格实例被容差余量误杀） | 阈值 = 标称 × (1 − 容差)，容差来自契约；原始实测值照实落盘与判据同报 |
| 14.13 | 判据表达式 | 用「全集到达式」断言（存在合法丢弃/覆盖路径时必假） | 用账目守恒式（到达 + 合法丢弃 + 溢出 = 总数）+ 尾连续校验；写「应收到全部」前先问有无合法丢弃/覆盖路径 |
| 14.14 | 拥塞注入 | 叠病态参数（极小接收缓冲制造零窗口楔死、排空停滞） | 拥塞注入用停读时长表达；判定 deadline 按 停读 + 采集 + 受限排空 三段估算留余量 |
| 14.15 | 跨端常量单位 | 两端常量单位不对齐（点数 vs 字节数，目标被误判减半） | 两端共用常量表并互相校验；报告中数值必须标注单位 |
| 14.16 | 串口独占 | 同端口双开句柄（直接异常崩溃） | 单进程单句柄短开短关；需持续监听时所有收发走同一句柄 |

## 15. 写前查询：真值源与官方文档索引

### 15.0 写前查询纪律（防「凭记忆写 API」）

写任何驱动/库相关代码前，先查真值源，**禁止凭记忆写 API**——按序三查：

1. **BSP 真值源**（15.1）——本工程实际编译的库版本：函数签名 / 宏 / 实现语义；
2. **官方文档**（15.2 索引）——寄存器 / 协议 / 集成细节；
3. **§12 / §13 / §14**——已知缺陷模式与正确姿势自查。

三者都没有答案时：停止并报告产品缺口，不得编造 API 语义硬写。

### 15.1 BSP 真值源（版本绑定的库引用）

- BSP 由 PS 软件链的 BSP 创建步生成，位于 `<PROJECT_PATH>` 内 BSP 目录下：
  - `include/` —— 生成头（外设参数头：全部 IP 基址 / 设备 ID / 参数）；
  - `libsrc/` —— 各驱动 `<驱动>_v*/src/` 的头文件与**实现源码**（含自带注释）。
- 查询方式：框架若提供 BSP 查询工具（按模式检索返回声明/宏/注释）则用之；
  无该工具时直接用工作区读文件能力读取上述路径。
- **版本纪律**：只认本工程 BSP 里的那一份——外部静态知识文件与记忆一律不作数。

### 15.2 官方文档索引（Zynq-7000 / 7 系列常用子集，路标不替代检索）

> 文档编号以 docs.amd.com 实际检索结果为准（文档随版本演进）；驱动级 API 看
> 各驱动的 Doxygen 类参考页（docs.amd.com 检索驱动名），驱动源码可对照
> Xilinx embeddedsw 仓库（https://github.com/Xilinx/embeddedsw）。

| 编号 | 主题 | 查什么 |
|------|------|--------|
| UG585 | Zynq-7000 SoC 技术参考手册（TRM） | PS 寄存器、系统级、互联、外设控制器 |
| UG643 | OS 与库文档集合 | standalone BSP、驱动集合入口 |
| UG821 | Zynq-7000 软件开发指南 | 软件流程、BSP/应用开发 |
| DS187 | Zynq-7000 SoC 数据手册（概览） | 器件资源/特性总览 |
| DS188 | Zynq-7000 SoC 电气特性（DC/AC Switching） | 电平/时序参数 |
| UG480 | 7 系列 XADC 用户指南 | XADC 寄存器与操作 |
| UG471 | 7 系列 SelectIO 用户指南 | IO 电气/逻辑资源 |
| UG472 | 7 系列时钟资源用户指南 | 时钟架构 |
| UG473 | 7 系列存储资源用户指南 | Block RAM 等 |
| UG583 | 7 系列 CLB 用户指南 | 逻辑资源 |
| UG901 | Vivado 综合用户指南 | HDL 语法/编码风格 |
| UG903 | 使用约束（Using Constraints） | XDC 语法 |
| UG949 | UltraFast 设计方法指南 | 时序收敛方法 |

