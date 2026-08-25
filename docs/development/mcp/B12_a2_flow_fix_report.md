# B12-A2 开发流程修复轮（Agent1 白盒）报告

> 日期：2026-08-25（`Get-Date` 实测；UTC+8）｜角色：Agent1（白盒实现）
> 范围：修复 B12-A2 白盒实测暴露的 5 个开发流程缺陷（D1 / D-B / D-E / D-A / D-C）+ 记录 1 条 P2 技术债（P2 tech debt：`deadline_at` 到期无执行机制）。不改代码之项只记录。
> 纪律：所有改动前已对每个生产/测试文件在同目录复制 `.bak` 备份，收尾已全部删除（全仓 `.bak` = 0）。只跑非硬件回归。未执行任何 git 写操作（add/commit/push 一律未执行，工作树保留仅修复改动）。未修改 CLAUDE.md、docs 冻结文档、skills/、boards/、三个 legacy 目录、workspaces/、.mcp.json。

---

## 0. 修复总览

| 缺陷 | 级别 | 生产入口 | 修复性质 | 证据等级 |
|---|---|---|---|---|
| D1 | P1 | `recover_execution` / `recovery_mutator` | 准入死锁：识别"僵尸 ACCEPTED op"并在 recovery 中解析，live-but-stale worker 不再死锁 | `IMPLEMENTED_AND_TESTED`（组件级 + 真实活进程复现） |
| D-B | P2 | `_dispatch_ps`（ps_* 调度） | 非 `project_path` 意图的工具，传入 `project_path` 稳定返回 INVALID_ARGUMENT/UNSUPPORTED_ARGUMENT，绝不 TypeError/OUTCOME_UNKNOWN | `IMPLEMENTED_AND_TESTED`（真实 MCP SDK contract） |
| D-E | P2 | `recover_execution` / `recovery_mutator` | 未决 previous op + live worker 可被 recover_execution 在同 runtime 解析，P6 不再永久阻断 | `IMPLEMENTED_AND_TESTED`（组件级 + 真实活进程复现） |
| D-A | P2 | `platform_add_ip` | 属性应用后强制回读校验：要么真实生效，要么显式 `IP_CONFIG_MISMATCH`（actual 非空）；绝不再静默成功 | `IMPLEMENTED_AND_TESTED`（mock，`MOCK_ONLY` 上限） |
| D-C | P2 | `ps_compile` / `XsctBridge._parse_tolerate_stderr` | MAKE_FALLBACK 返回完整 make/编译器输出，超长截断并标注截断与总长 | `IMPLEMENTED_AND_TESTED`（mock，`MOCK_ONLY` 上限 + 解析器回归） |

P2 技术债（只记录，不实现）：操作层 `deadline_at` 到期无执行机制（无 watchdog）——见 §6。

---

## 1. D1（P1）：`ps_create_platform` 准入死锁（rdi_xsct 陈旧活 worker）

### 根因
- 一个 op 被 admit 进 `ACCEPTED`（`started_at=None`、lane `BUSY`），但**从未被推进到 `RUNNING`**：`CommandRunner.run_command` 只 `asyncio.ensure_future` 调度 `_execute`，真正写 `started_at` 的是 `_execute` 首行的 `op_transition(RUNNING)`。若该任务未触发或 RUNNING 写失败，op 永久停在 `ACCEPTED`。
- `deadline_at` 只在写入时**记录**，从未被执行（无 watchdog、无 sweeper）。`deadline_remaining_s=0` 是纯观测字段，不会自动超时一个 ACCEPTED op。
- 死锁闭环：`recover_execution` → `_alive_stale_revive_eligibility` 因 lane≠IDLE 且存在非终止 active op 返回 False → 落到 `recovery_mutator` → 因 worker pid 存活报 `RECOVERY_BLOCKED_WORKER_ALIVE`；`close_session` 因 `ACTIVE_OPERATION_PRESENT` 被拒；`wait_operation` 永挂。无公开处置。

### 修复（`control/recovery.py`）
- 新增谓词 `is_zombie_accepted(ao, now_s=None)`：`status==OP_ACCEPTED` **且** `started_at is None` **且** `deadline_at` 已过期。fail-closed——`RUNNING`/任何 terminal、任何 `started_at` 已设置、任何未过期或不可解析 deadline 均返回 False，绝不自动解析。
- 在 `recovery_mutator` 一开始（worker-alive 守卫之前）拦截：若 active op 是僵尸 → 置 `OP_TIMED_OUT` / `reason_code=ZOMBIE_ADMISSION_DEADLINE` / `resolved_by_recovery=True`，平移到 `previous_operation`，`active_operation=None`，lane=IDLE，追加 `ZOMBIE_ACCEPTED_RESOLVED` 到 recovery_log。**不清活 worker 记录**（僵尸 op 从未启动任何后端，进程不能归因于它；fail-closed，交给 D4 revive / close_session 接管）。

### 风险
- 只解析**可证明非运行**的僵尸（ACCEPTED + never started + 过期 deadline）。`test_e2_recover_still_refused_with_active_operation`（RUNNING）与 `test_recovery_still_blocks_alive_pid`（活 PID + 无 previous op）仍拒绝，门禁未削弱。
- 解析后 lane=IDLE 且 active op 清空，立即解除 `close_session` / `wait_operation` 阻塞；活 worker 由既有 D4 `_alive_stale_revive_eligibility` 在下次 revive，或由 close_session 关闭。

### 新增测试
- `test_r1_recovery.py::TestRecovery::test_d1_zombie_accepted_resolves_with_stale_live_worker` — 拉起**真实**子进程作 stale-live worker（`proc.pid` 存活，heartbeat 为陈旧 ts），构造 `ACCEPTED + started_at=None + 过期 deadline` 僵尸 op → `recovery_mutator` 返回 lane IDLE / previous 为 TIMED_OUT / resolved_by_recovery=True / worker pid 未被清；随后 `preflight_mutator` 准入下一命令成功（P6 不再挡）。
- `test_r1_recovery.py::TestRecovery::test_d1_zombie_accepted_still_refuses_running` — `is_zombie_accepted` 对 `RUNNING` 返回 False，且 recover 仍抛 `RECOVERY_BLOCKED_OPERATION_NON_TERMINAL`（fail-closed 保全）。

---

## 2. D-B（P2）：`ps_add_sources` 及 ps_* 工具 schema 不接受 `project_path`

### 根因
- 服务器 `server.py` 用 MCP lowlevel `call_tool`（`validate_input=True`），但 ps_* schema 均**未设 `additionalProperties:False`**，故 JSON Schema 默认放行多余 `project_path`——SDK 不拦截。
- 调度到 `_dispatch_ps`：`ps_add_sources` 等属于 `_PS_XSCT_TOOL_NAMES`，原逻辑**保留** `project_path` 前传。`domain_runner._execute` 以 `local_fn(bridge, **arguments)` 调用 `add_sources(bridge, app_name, files)` 等**不接收 `project_path`** 的函数签名 → `TypeError` → 被 `_execute` 泛型 `except Exception` 捕获 → `OP_OUTCOME_UNKNOWN`（过渡到 previous op）→ 下一命令准入命中 P6 `PREVIOUS_OPERATION_UNRESOLVED` 永久阻断。
- 因此"schema 不接受 project_path"是表象；真正的 TypeError 来自**域函数签名不接收该参数**。设 `additionalProperties:False` 只会让 SDK 返回 `"Input validation error"` 文本，**不是**项目要求的 `{status:error, error:{code:INVALID_ARGUMENT, details:{reason_code}}}` 稳定信封，故不作为修复手段。

### 修复（`control/domain_runner.py` + `dispatcher.py`）
- `domain_runner.py` 新增 `_PS_PROJECT_PATH_TOOLS = {ps_import_hardware, ps_create_platform, ps_create_bsp, ps_create_app}`——这 4 个工具的域函数**真实接收** `project_path`（它们自己 `setws`）。
- `dispatcher.py` 导入 `_PS_PROJECT_PATH_TOOLS`；`_dispatch_ps` 的 strip 逻辑改为：若工具 ∈ `_PS_PROJECT_PATH_TOOLS` 则保留（schema 已声明）；否则若 `project_path` 在参数里 → **返回稳定 `INVALID_ARGUMENT`/`UNSUPPORTED_ARGUMENT`**（在准入/run_command 之前，不创建 op → 不产生 OUTCOME_UNKNOWN → P6 永不挂）。若不在参数里则弹出（session 传输键）。

### 风险
- 只影响"不该接收 project_path 却传入"的情况，从 TypeError/OUTCOME_UNKNOWN 变为确定的 INVALID_ARGUMENT。4 个合法工具（schema 声明 project_path）行为不变。
- 不新增任何域函数签名，不改 schema JSON，不会让 SDK 产生 "Input validation error"。

### 新增测试（真实 MCP SDK contract，`test_b06_ps_bsp_public.py`）
- `TestBspErrorPaths::test_ps_add_sources_project_path_never_outcome_unknown` — 起真实 server，`ps_add_sources` 带 `project_path` → 断言精确信封 `status=="error"` + `code=="INVALID_ARGUMENT"` + `details.reason_code=="UNSUPPORTED_ARGUMENT"`；并断言响应**不是** `"Input validation error"` 前缀。
- `TestBspErrorPaths::test_ps_create_platform_project_path_accepted` — 反向：`ps_create_platform` 带 `project_path` → 断言 admission `status=="success"` / `data.status=="accepted"`（不误拒合法工具）。

---

## 3. D-E（P2）：任一未决操作 → P6 gate 永久阻断同 runtime 会话

### 根因
- P6 触发集 = `{INTERRUPTED, OUTCOME_UNKNOWN, TIMED_OUT}`（`execution_gate._gate` 与 `domain_runner._shared_preflight_check` 一致）。`FAILED` **不在** P6 集内且 `op_transition` 将 FAILED/CANCELLED 路由到 lane IDLE——白盒报告把 FAILED 计入 P6 trigger 是不准确的；真正阻断的是 OUTCOME_UNKNOWN/TIMED_OUT/INTERRUPTED。
- 当 OUTCOME_UNKNOWN op 留下**活 worker**：`recover_execution` → `recovery_mutator` → P1（`recovery.py:102-103`）因 `is_pid_alive(pid)` 抛 `RECOVERY_BLOCKED_WORKER_ALIVE`，**先于** `recovery.py:118-122`（解析 previous op 为 resolved）执行 → previous op 永远 unresolved → P6 永久挡。唯一逃生是运行时轮换。
- 另：`close_session` 只清 `context`，不清 `previous_operation`；`recovery_mutator` 的 IDLE 分支对"无活进程 + unresolved previous op"返回 `ALREADY_IDLE` 而未解析它——即使同 runtime close+recreate 会话，首条命令仍被 P6 挡。

### 修复（`control/recovery.py`）
- `recovery_mutator` 新增两个分支：
  1. **活 worker + unresolved previous op + 无 in-flight op**：解析 previous op（`resolved_by_recovery=True` 等），lane→IDLE，**不清活 worker 记录**（fail-closed）。这是让"单个未决操作"不需要推倒会话的主路径。
  2. **IDLE 分支的 companion**：lane=IDLE 且无活进程但 previous op 未决 → 也解析它（覆盖 close_session+create_session 同 runtime 场景）。
- P1 worker-alive 守卫语义收窄为：仅当"正在执行的非终止 op"或"活 worker 且无任何 terminal-unresolved 可回收"时才 `RECOVERY_BLOCKED_WORKER_ALIVE`（D-E 与 D1 分支已提前 return）。

### 风险
- 保留了"活 PID + 无未决 previous op"（`test_worker_alive_blocks`）与"活 PID 的 RECOVERY_REQUIRED"（`test_recovery_still_blocks_alive_pid`）的拒绝语义；只对确有 terminal-unresolved previous op 可回收时放开。
- 清 worker 记录只发生在确认真实死亡的场景（原路径）；对活 worker 从不清 owner 残留，避免泄漏不可证明的后端进程。

### 新增测试
- `test_r1_recovery.py::TestRecovery::test_de_unresolved_previous_resolves_with_live_worker` — 拉起**真实**子进程作活 worker，构造 `previous_operation={status:OUTCOME_UNKNOWN}`（无 `resolved_by_recovery`）+ lane RECOVERY_REQUIRED → `recovery_mutator` 返回 lane IDLE / previous resolved_by_recovery=True / worker pid 未清；随后 `preflight_mutator` 准入下一命令成功（P6 不再挡）。

---

## 4. D-A（P2）：`platform_add_ip` 对 AXI GPIO channel-2 配置不生效

### 根因（两个耦合缺陷，均在 `domains/platform/platform_atoms.py` `platform_add_ip`）
- **A（readback 盲）**：exists 路径用裸 `get_property CONFIG.X [get_bd_cells ...]`（无 `puts`）。Tcl bridge 只捕获 stdout（D8 契约），裸返回命令的回显为空 → `_tcl_output` 返回 `""` → `_norm_prop_val("") == ""` 恒不等于任何非空 `want` → 对**任何**属性（不只 channel-2）重跑即报 `IP_CONFIG_MISMATCH/actual=''`。
- **B（静默成功）**：fresh-add 路径 `create_bd_cell + set_property -dict` 后直接 return success，**不校验**属性是否真的写入。若 Vivado 静默丢弃/未提交某属性（AXI GPIO channel-2 参数在 C_IS_DUAL 未锁定的同 pass 内正是如此），原子仍报成功 → 重跑才暴露 mismatch 且 `gpio2_io_o` 引脚不存在。

结论：channel-2 属性**被发送**（`set_property` 包含所有 `props` 键），但从未被**证实写入**；而本应抓 mismatch 的 readback 本身是"瞎"的。

### 修复（`domains/platform/platform_atoms.py`）
- 新增 `_verify_ip_props(adapter, instance_name, props)`：对每个请求属性跑 `puts [get_property CONFIG.X [get_bd_cells ...]]`（D8 修复 readback），返回 mismatch map（实际值非空）。
- exists 路径取 `_verify_ip_props` 结果，非空则 `raise PlatformError(..., "IP_CONFIG_MISMATCH")`。
- fresh-add 路径在 `set_property` 后**也**跑 `_verify_ip_props`；非空则 `raise PlatformError("IP {} config not applied: {...}", "IP_CONFIG_MISMATCH")`——绝不再静默成功。

### 风险
- 复用既有 `IP_CONFIG_MISMATCH` reason_code（不新增契约码）；配置要么被证实应用（readback 等于请求 → 成功），要么显式报错。`MOCK_ONLY` 证据等级（未跑真 Vivado host_live，属非硬件纪律内）。
- 正确 Vivado 属性名：`C_IS_DUAL`、`C_GPIO_WIDTH`、`C_GPIO2_WIDTH`、`C_ALL_INPUTS`、`C_ALL_INPUTS_2`，均作为 BD-cell `CONFIG.*` 可设置；`C_GPIO2_WIDTH`/`C_ALL_INPUTS_2` 仅在 `C_IS_DUAL=1` 时生效，`set_property -dict [list ...]` 一次写入是有效的单命令形式。

### 测试
- `test_platform_atoms.py::TestAddIp::test_dual_channel_config_actually_written` — mock `_FakeAdapter` 提供 channel-2 readback 值；断言写入 tcl 含 `C_IS_DUAL {1}`/`C_GPIO2_WIDTH {10}`/`C_ALL_INPUTS_2 {1}`，且每个 readback 命令以 `puts [get_property CONFIG.` 开头、共 5 个 readback。
- `test_platform_atoms.py::TestAddIp::test_fresh_add_silent_drop_raises_not_success` — 模拟属性未生效（readback 为空/陈旧），断言 fresh-add 抛 `IP_CONFIG_MISMATCH`（而非静默成功）。
- 等价修订：既有 `test_creates_cell_when_absent` 适配新增 readback 调用（断言等价，行为不变）。

---

## 5. D-C（P2）：`ps_compile` MAKE_FALLBACK 失败只回传一行，吞掉编译器 stderr

### 根因
- make 经 XSCT Tcl `exec make` 运行，失败时 Tcl 把多行子进程 stdout+stderr+退出码赋给 `$__xsct_err`，`_catch_wrap` 以 `puts "__XSCT_TCLERR__$__xsct_err"` 打印（首行带 marker，其余为续行）。
- **决定性丢失点在 `XsctBridge._parse_tolerate_stderr`（`xsct_bridge.py:152-156`）**：原代码遇到**首个** `__XSCT_TCLERR__` 行即 return，只取该行余部 `s[len(marker):].strip()`，后续多行（真正的编译器错误详情、`make: *** Error 1` 等）被丢弃。所以 `verr[2]` 只剩 `'Building file: ../src/main.c'`。
- 次要丢失点：`_TclShellBridge.eval` 在 `tolerate_stderr=True` 下把 drain 到的 stderr 传给了 `_parse_tolerate_stderr`（该函数不接收 stderr_text）——任何落在 stderr 管道的输出也被丢弃。

### 修复（`adapters/xsct/xsct_bridge.py` + `domains/ps/ps_bsp.py`）
- `_parse_tolerate_stderr`：遇到 marker 行时改为**收集从该行到 data 末尾的全部续行**并 join 进错误信message（`_catch_wrap` 失败时不打印其它内容，故这些行都属于同一条错误，join 安全）。既有单行测试不受影响（单元素 join = 原消息）。
- `compile_app` MAKE_FALLBACK 错误构造改为：对 `verr[2]` 经 `_cap_build_output` 截断（超 `_MAX_BUILD_OUTPUT_LEN=8000` 时保留头部 + `\n...TRUNCATED: <kept>/<total>...`），并在 `details` 记录 `build_output_len`（原长）与 `build_output_truncated`（bool）。完整（或注记截断的）输出经 `_terminal_failed → str(result)` 进入 operation 错误。

### 风险
- 仅放宽解析——join 相同错误的全部行；既有 `test_parse_tolerate_stderr_*` 单行/ERROR 分支通过。
- 截断仅在超长时触发，且**显式标注截断与总长**，绝不静默丢输出；常量 8000 足以覆盖绝大多数编译器错误又不无限膨胀错误信封。

### 测试（`test_ps_bsp_domain.py`，非硬件）
- `TestTolerantStderrParse::test_parse_tolerate_stderr_preserves_full_make_error` — 喂多行 `__XSCT_TCLERR__` blob，断言完整编译器详情（`main.c:5: error`、`Error 1`）都保留。
- `TestCompileApp::test_compile_app_make_fallback_includes_full_output` — 注入完整 make 错误信封，断言 `BUILD_FAILED`、完整内容在 message、无截断标记（未超限）、`build_output_truncated is False`。
- `TestCompileApp::test_compile_app_make_fallback_truncates_long_output` — 注入超长输出，断言 `TRUNCATED:` 标记存在、`build_output_truncated is True`、`build_output_len` 如实记录、标记含 `<kept>/<total>`。

---

## 6. P2 技术债（只记录，本轮不实现）

| 技术债 | 说明 | 状态 |
|---|---|---|
| 操作层 `deadline_at` 到期无执行机制（无 watchdog） | `deadline_at` / `deadline_remaining_s` 只在 admit 时写入、在 `operation_public_view` 上报，**从未被执行**。没有任何 sweeper / periodic reconcile / admission-timeout 会在 ACCEPTED（或 RUNNING）op 的 deadline 过期后自动将其标记为 TIMED_OUT。本轮 D1 的修复是通过 `recover_execution`（显式公开调用）在 `recovery_mutator` 的原子事务内解析**已过期**的僵尸 ACCEPTED op，而非引入后台 watchdog。 | DEFERRED（记录，不实现）。建议后续在 `domain_runner`/服务层加一个有界 watchdog：对 `deadline_at` 已过期且 op 仍非终止的惰性 op，在下一个事件循环 tick 或周期任务中转 TIMED_OUT；需注意与 `op_observe`/运行中 RUNNING op 的竞态，仍走原子事务 + fail-closed。 |

---

## 7. 回归机械统计（前后对照）

非硬件回归（从仓库根运行，跳过硬/华需 EDA 工具或硬件的测试）：

```bash
python -m pytest mcps -m "not host_live and not device_live"
```

| 指标 | 基线 | 修复后 | 变化 |
|---|---|---|---|
| collected | 1435 | 1445 | +10（新增测试） |
| passed | 1393 | **1403** | +10 |
| skipped | 1 | 1 | 0 |
| deselected | 41 | 41 | 0 |
| failed | 0 | **0** | 0 |

- 修复后 passed（1403）≥ 基线（1393），failed = 0，满足"测试不得净减"门禁。
- collected 基线（1435）为命令输出；修复后 `python -m pytest mcps --collect-only -q` 给出 **1445 tests collected**（+10 新增测试）。
- 最终全量非硬件回归（含 import 清理后的复核）：`1403 passed, 1 skipped, 41 deselected in 216.34s`，exit code 0。

### 新增/修改测试数量与映射

新增 10 个测试函数（全部非硬件）：

| 文件 | 函数 | 归属缺陷 |
|---|---|---|
| `test_r1_recovery.py` | `test_d1_zombie_accepted_resolves_with_stale_live_worker` | D1 |
| `test_r1_recovery.py` | `test_d1_zombie_accepted_still_refuses_running` | D1 |
| `test_r1_recovery.py` | `test_de_unresolved_previous_resolves_with_live_worker` | D-E |
| `test_b06_ps_bsp_public.py` | `test_ps_add_sources_project_path_never_outcome_unknown` | D-B |
| `test_b06_ps_bsp_public.py` | `test_ps_create_platform_project_path_accepted` | D-B |
| `test_platform_atoms.py` | `test_dual_channel_config_actually_written` | D-A |
| `test_platform_atoms.py` | `test_fresh_add_silent_drop_raises_not_success` | D-A |
| `test_ps_bsp_domain.py` | `test_parse_tolerate_stderr_preserves_full_make_error` | D-C |
| `test_ps_bsp_domain.py` | `test_compile_app_make_fallback_includes_full_output` | D-C |
| `test_ps_bsp_domain.py` | `test_compile_app_make_fallback_truncates_long_output` | D-C |

修改（非删除，等价性保留）1 个既有测试：

| 文件 | 函数 | 说明 |
|---|---|---|
| `test_platform_atoms.py` | `TestAddIp::test_creates_cell_when_absent` | 适配 D-A 新增的回读调用：mock 输出序列增加两个 readback 值；断言从 `_last_tcl`（现指向末次 readback）改为显式取第 2 条 write 命令；断言语义与旧等价（创建 cell、写 `set_property -dict`、`already_exists=False`），并新增"readback 必须以 `puts [` 开头"。 |

删除/重命名测试：**无**。所有被修改清单仅含上述 1 项等价适配。

---

## 8. 修改文件清单

生产代码（6）：
- `mcps/zynq_mcp/control/recovery.py` — is_zombie_accepted + recovery_mutator D1/D-E
- `mcps/zynq_mcp/control/domain_runner.py` — `_PS_PROJECT_PATH_TOOLS`
- `mcps/zynq_mcp/dispatcher.py` — `_dispatch_ps` project_path 拒绝
- `mcps/zynq_mcp/domains/platform/platform_atoms.py` — `_verify_ip_props` + platform_add_ip 校验
- `mcps/zynq_mcp/domains/ps/ps_bsp.py` — `_cap_build_output` + `_MAX_BUILD_OUTPUT_LEN` + compile_app 错误
- `mcps/zynq_mcp/adapters/xsct/xsct_bridge.py` — `_parse_tolerate_stderr` 多行保留

测试（4）：
- `mcps/zynq_mcp/tests/test_r1_recovery.py`
- `mcps/zynq_mcp/tests/test_b06_ps_bsp_public.py`
- `mcps/zynq_mcp/tests/test_platform_atoms.py`
- `mcps/zynq_mcp/tests/test_ps_bsp_domain.py`

（另曾为上述每个文件建立 `.bak`，收尾已全部删除；全仓 `.bak` 计数 = 0。）

---

## 9. 未改动项声明

- 未修改：`CLAUDE.md`（尤其规则节）、`docs/` 下任何冻结文档（含 B12_a2_whitebox_report / rerun_report / requirement draft）、`skills/`、`boards/`、三个 legacy 目录（`Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/`）、`workspaces/`、`.mcp.json`。
- 未执行任何 git 写操作（add/commit/push 一律未执行）。
- 全程只运行**非硬件**回归（`-m "not host_live and not device_live"`）；**绝对未**运行 host_live/device_live/任何 EDA 工具/Vivado/XSCT/hw_server。
- 未自行冻结 Brick、未越级进入下一步骤；未调用 Agent2。

---

## 10. 剩余阻塞 / 需要主代理注意的点

1. **证据等级边界**：D-A 与 D-C 的修复用 mock（`MOCK_ONLY` 上限）；按纪律"Adapter 未被真实工具 host_live 通过前不能向 Domain 层声称 IMPLEMENTED_AND_TESTED"。D-A 的真实 Vivado host_live（真正添加双通道 AXI GPIO → 重跑 add_ip → 断言 unchanged / gpio2_io_o 存在）与 D-C 的真实 XSCT make 失败输出，需另一轮 host_live（本轮因纯非硬件纪律未跑，未与另一白盒硬件代理冲突）。
2. **D-D（`ps_wait_uart_capture` 不超时）不在本轮 5 项内**——任务清单只列 D1/D-B/D-E/D-A/D-C。D-D 是 P1 且与白盒 v2 串行推进直接相关（wait_uart_capture 在 deadline 后仍 RUNNING → CHANNEL_BUSY）。本轮未触碰；应在下一轮单独处理（wait_uart_capture 强制 deadline 超时 + 关闭/接管死锁通道），否则 A2 白盒 v2 仍可能在收尾命中同类死锁。请主代理确认是否将 D-D 纳入下轮修复范围。
3. **D-E 的"同 runtime 继续"上限**：本修复让"单个未决操作"在同一 runtime 通过 `recover_execution` 解析、P6 不再挡。但若该未决 op 留下的是**控制器未持有 / 身份不可核验**的活后端，`recover_execution` 解析后如需真实复用该后端，仍需 process_controller 层"接管/释放"该存量 handle（属独立的后端所有权关切）。本轮聚焦 P6/gate 解锁；后端接管复用作为后续硬化。
