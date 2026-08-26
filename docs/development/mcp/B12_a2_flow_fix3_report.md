# B12-A2 开发流程修复轮 #3（Agent1 白盒）报告

> 日期：2026-08-26（`Get-Date` 实测；UTC+8）｜角色：Agent1（白盒实现）
> 范围：修复剩余框架 P2 项（治本为主）：①操作 deadline 无 watchdog（D1 僵尸准入与 D-D 等待不超时的共同根）；②工程命名/路径契约（E/G）；③stage/会话续跑（评估+最小咨询）；④Skill 开发流程纪律（XDC 注释独占行 + 多驱动/未约束端口警告检查）。
> 明确不修：UART 捕获丢字节加固（用户口径=测试流程，由需求 v2.1 承接；框架侧留 P2 记录）。
> 纪律：改动前对每个生产/测试/文档文件建 `.bak`，收尾已删除（全仓 `.bak` = 0）。只跑非硬件回归。未执行任何 git 写操作。未修改 CLAUDE.md、docs 冻结文档、boards/、legacy 目录、workspaces/、.mcp.json。未运行任何 EDA/host_live/device_live。
> 注：`docs/development/tests/B12_a2_whitebox_v2_report.md` 与顶层 `bitstream/` 目录的改动/出现**均非本子代理所为**（应为白盒硬件代理/主代理的并行产物），未触碰、未清理。

---

## 0. 修复总览

| # | 级别 | 生产入口 | 修复性质 | 证据等级 |
|---|---|---|---|---|
| 1 | P2（治本） | `control/domain_runner.py` | 操作层 deadline 执行机制：域调用 wait 按 `min(调用方值, 剩余deadline)` 硬界；RUNNING op 超 deadline → 走既有 `except asyncio.TimeoutError` 强制 TIMED_OUT + 释放通道。 | `IMPLEMENTED_AND_TESTED`（组件级，+2 测：强制超时释放+恢复后准入、min() 单元+集成） |
| 2 | P2 | `domains/pl/system_top.py` | 工程命名/路径契约：`system_top.v` 保留名不再静默覆盖（不同内容→`SYSTEM_TOP_OVERWRITE_CONFLICT`）；路径逃逸错误明确说明输入必须位于 project 下。 | `IMPLEMENTED_AND_TESTED`（+2 测） |
| 3 | P2 | `dispatcher.py` | stage/会话续跑：`create_session` 新增 `resume_hint` 咨询字段（检测既有 platform manifest/xsa/bitstream），给调用方明确提示，不改任何 gate/stage 语义。 | `IMPLEMENTED_AND_TESTED`（+3 测） |
| 4 | P2 | `skills/zynq_dev/phases/5_domain_implementation.md` | Skill 开发流程纪律：XDC 注释必须独占行 + 综合/实现后必须检查多驱动与未约束端口警告。 | `STATIC_REVIEW_ONLY`（文档纪律；无对应测试，属 Skill 内容，非测试纪律） |

---

## 1. 项 #1（P2→治本）：操作 deadline 无 watchdog

### 根因
`deadline_at` 只在 admit 时写入（`operation_contract_fields` → `operation_deadline_at`），在 `operation_public_view` 上报 `deadline_remaining_s`——但**从未被执行**。D1（僵尸准入：ACCEPTED 永远不启动）与 D-D（`ps_wait_uart_capture` 在 deadline 后仍 RUNNING 不超时）的共同根就是此。域调用的 `asyncio.wait_for(local_fn(...), timeout_s or 300)` 用的是**固定值**，与 op 的 deadline 无关——两层超时不同源。

### 修复（`control/domain_runner.py`）
- 新增 `operation_remaining_deadline(guard, ledger_path, op_id, *, now_s=None)`：从 Ledger 读 active op 的 `deadline_at`（ISO，`operation_service._parse_record_time` 可解析），返回 `max(0, deadline_ts - now)`；op 缺失/deadline 不可解析 → `None`（fail-closed：读失败不解释为"已过期"，回退到调用方/base bound）。
- 新增 `_deadline_capped_timeout(timeout_s, remaining)`：`asyncio.wait_for` bound = `min(调用方 timeout_s, 剩余deadline)`；两者均 None → `None`（保留每工具默认）；仅一方有值 → 该值。
- 在 `_execute` 的 RUNNING 过渡后计算 `remaining`，用 `wait_bound = _deadline_capped_timeout(timeout_s, remaining)`，并**重指向 `timeout_s = wait_bound`**（`wait_bound 非 None 时`）——这样 `_execute` 内所有下游 `asyncio.wait_for(..., timeout_s or N)` 与 worker 路径 `execute_tool(timeout_s=timeout_s)` 都统一被 op 剩余 deadline 硬界。

**为何安全（B04 §4.3 / 冻结 gate）**：当 deadline 到期，`asyncio.wait_for` 抛 `asyncio.TimeoutError` → 落到**既有** `except asyncio.TimeoutError`（domain_runner ~1278），它 (a) 做 O4 强制后端清理（`shutdown_backend`，`test_o4_timeout_cleans_owned_xsct_and_records_timed_out` 依赖此）、(b) `op_transition(..., OP_TIMED_OUT)` → lane = RECOVERY_REQUIRED。`op_transition` 仅在 `SUCCEEDED && next_stage` 时推进 stage（`operation_service.py:135`）；TIMED_OUT 走 `OP_TERMINAL` 分支（`:156-165`），置 lane=RECOVERY_REQUIRED，**不动** `current.context["current_stage"]`。所以不会破坏 B04 §4.3 串行链，也不会推进 stage。

### 风险 / 已知局限
- **本地/plain/contextual 分支全覆盖**；长跑 bridge 分支（`_pl_bridge` long-run synth/place/route，line 1032）与 `_pl_bridge`+process_controller（line 998）**本身不包 `wait_for`**，其内部由 `run_vivado_run` / bridge eval 自带 bound 约束。未强行包装这些冻结的长跑/O4-observer 路径（避免双重过渡/清理竞态），报告为此局限。核心需求（复现：RUNNING op 超 deadline → 强制超时 + 通道释放）经本地路径满足。
- worker executor 分支（`execute_tool(timeout_s=timeout_s)`）同样被 `timeout_s` 重指向覆盖（该分支不包 `wait_for`，但 worker 自己用 `timeout_s` 作为 `asyncio.wait_for` 界）。
- 既有 `test_R305_local_timeout`（timeout_s=0.5）与 `test_o4_timeout_cleans_owned_xsct...`（timeout_s=0.05）本就期望 TIMED_OUT，现由 deadline 触发，行为一致。

### 测试（`test_r3_runner.py`，+2）
- `test_R305b_operation_deadline_watchdog_releases_channel` — 短 deadline(0.3s) + 永不返回的 local_fn（`asyncio.Event().wait()`）→ `previous_operation.status == OP_TIMED_OUT`、`execution_lane == RECOVERY_REQUIRED`、`active_operation is None`（通道释放）；随后 `recovery_mutator` → lane IDLE，再来一条 `ps_get_bsp_status` 准入 → `OP_ACCEPTED`（通道可复用）。
- `test_R305c_wait_bound_uses_remaining_deadline` — `_deadline_capped_timeout` 单元断言（min 语义：5/0.3→0.3、0.2/5→0.2、None+7→7、5+None→5、None+None→None）+ 集成（timeout_s=0.3 → TIMED_OUT）。

---

## 2. 项 #2（P2）：工程命名/路径契约（E/G）

### 根因
①`pl_generate_system_top` 固定写 `{project}/rtl/system_top.v`（保留名），**无覆盖保护**——白盒同 project 重跑时被静默覆盖，顶层退化。②`_validate_contained`（system_top 内路径校验）已 fail-closed 拒绝逃逸，但错误消息只回 `field=path` 片段，未明确"必须位于 project 下"（白盒难以定位。

### 修复（`domains/pl/system_top.py`）
- ①在生成前检查：若 `{project}/rtl/system_top.v` 已存在且**内容与本次生成不同** → `raise PathSafetyError("SYSTEM_TOP_OVERWRITE_CONFLICT", ...)`，附 prior_sha/new_sha，拒绝静默覆盖；内容相同则幂等允许（保持 `test_r314_deterministic_output` 的确定性/双次调用不变）。
- ②改进 `_validate_contained` 的逃逸错误消息：`PATH_ABSOLUTE`/`PATH_DRIVE_RELATIVE`/`PATH_ESCAPE` 均明确指出"must be a project-relative path under the project directory"（或 "must be contained under the project directory"）。reason_code 不变（`PATH_ABSOLUTE`/`PATH_ESCAPE` 等）。

### 风险
低。仅加覆盖保护（同内容幂等不受影响）+ 错误消息澄清，不改变任何 gate/接收路径。`PathSafetyError` 经 dispatcher `_pl_generate_local_fn` 映射为 `TOOL_ERROR`/`reason_code` 传播。

### 测试（`test_r3_1b_pl.py`，+2）
- `test_r315_reserved_output_not_silently_overwritten` — 同 input 重跑幂等（sha 相同）；写一个**不同**的既有 `system_top.v` 后再生成 → `PathSafetyError`/`SYSTEM_TOP_OVERWRITE_CONFLICT`。
- `test_r3s03b_escape_message_states_project_boundary` — `../etc/outside.v` → `PathSafetyError`/`PATH_ESCAPE`，消息含 "must be" 与 "project"。

---

## 3. 项 #3（P2）：stage/会话续跑

### 根因
close_session + create_session 后，`create_session` 无条件把 `current_stage` 置 `PLATFORM_DESIGN`、context 全新——即使 project 已有产物（platform manifest/XSA/bitstream）。白盒 R5 因此"项目已有产物但 stage 回到起点"，串行链要重跑。但自动从产物推导 stage 复杂且有误判风险；且契约测试将 create_session 的 `current_stage == PLATFORM_DESIGN` 钉死。

### 修复（`dispatcher.py`）
- 新增 `_existing_project_artifacts_hint(project_path)`：检测 `{project}/manifests/platform/sha256_*.json`、`{project}/platform.xsa`、`{project}/bitstream/*.bit`，返回咨询 dict。
- `_create_session` 成功响应在存在产物时追加 `resume_hint` 字段（否则不添加），**纯咨询**——不改 stage/gate/契约，调用方据此明确知道"项目已有产物，stage 已回到起点，需决定是否续跑/重建"。

### 风险
低。附加响应字段（非契约破坏——create_session 响应未做严格键数断言）；不改变任何 gate/stage 语义；fail-closed（无产物/路径不存在则无 hint，不臆造恢复）。

### 测试（`test_r1_session.py`，+3）
- `TestExistingProjectHint::test_detects_platform_manifest_xsa_bitstream`、`test_empty_project_yields_no_hint`、`test_nonexistent_project_yields_no_hint`。

---

## 4. 项 #4（P2）：Skill 开发流程纪律（仅开发流程部分）

### 修复（`skills/zynq_dev/phases/5_domain_implementation.md`，§5.2 PL 构建链）
在 PL 构建链说明后追加两条硬性检查纪律：
1. **XDC 注释必须独占行**：行内 `#` 会被 Vivado 误解析为 option 值（`Common 17-161`，进而 impl `UCIO-1` 未约束端口、write_bitstream 失败——已两次踩坑）；禁止 `set_property ... # 注释` 行内注释。
2. **综合/实现后必须检查多驱动与未约束端口警告**：`pl_synthesize`/`pl_place`/`pl_route` 后核对 `[Synth 8-XXXX] multiple drivers` 与 `[DRC UCIO-1]`/`[Common 17-XXXX]` 警告；RTL 多驱动曾**静默成活板 bug**（不报错、时序通过但行为错误）；任一警告必须定位到具体端口/信号并确认无冲突才可继续，否则视为失败。

> 只加**开发流程/实现**纪律；**未加**测试环节纪律（用户明确排除）。`test_skill_contains_no_direct_process_or_build_recipe` 禁 `make`/`vivado`/`run_tcl` 等——本新增文案不含这些 token（用 "set_property"、"综合/实现" 等），Skill 契约测试 12/12 通过。

### 风险
低。仅文档纪律；未触发 Skill 契约的禁词/结构检查（已跑 `test_o6_skill_contract.py` 12 passed）。

---

## 5. 回归机械统计（前后对照，数字来自命令输出）

```bash
python -m pytest mcps -m "not host_live and not device_live"   （仓库根）
```
| 指标 | 基线（fix #2 后） | 修复后 | 变化 |
|---|---|---|---|
| collected | 1460 | **1467** | +7（新增测试） |
| passed | 1418 | **1425** | +7 |
| skipped | 1 | 1 | 0 |
| deselected | 41 | 41 | 0 |
| failed | 0 | **0** | 0 |

- 修复后 passed（1425）≥ 基线（1418），failed = 0，无测试净减。
- collected 基线（1460）为 fix #2 后 `--collect-only` 输出；修复后 **1467 tests collected**。

### 新增测试映射

新增 7 个测试函数（全部非硬件）：

| 文件 | 函数 | 归属项 |
|---|---|---|
| `test_r3_runner.py` | `test_R305b_operation_deadline_watchdog_releases_channel` | #1 |
| `test_r3_runner.py` | `test_R305c_wait_bound_uses_remaining_deadline` | #1 |
| `test_r3_1b_pl.py` | `test_r315_reserved_output_not_silently_overwritten` | #2 |
| `test_r3_1b_pl.py` | `test_r3s03b_escape_message_states_project_boundary` | #2 |
| `test_r1_session.py` | `test_detects_platform_manifest_xsa_bitstream` | #3 |
| `test_r1_session.py` | `test_empty_project_yields_no_hint` | #3 |
| `test_r1_session.py` | `test_nonexistent_project_yields_no_hint` | #3 |

删除/重命名测试：**无**。未修改既有测试函数（仅新增）。

---

## 6. 修改文件清单

生产代码（2）：
- `mcps/zynq_mcp/control/domain_runner.py` — deadline 执行机制（helper + `_execute` wait_bound 重指向）
- `mcps/zynq_mcp/dispatcher.py` — `resume_hint` + `_existing_project_artifacts_hint`

领域（1）：
- `mcps/zynq_mcp/domains/pl/system_top.py` — 保留名覆盖保护 + 路径逃逸消息澄清

测试（3）：
- `mcps/zynq_mcp/tests/test_r3_runner.py`、`test_r3_1b_pl.py`、`test_r1_session.py`

Skill 文档（1）：
- `skills/zynq_dev/phases/5_domain_implementation.md` — XDC 注释独占行 + 多驱动/未约束端口检查纪律

（曾为每个文件建 `.bak`，收尾删除；全仓 `.bak` = 0。）

---

## 7. 未改动/未实现声明

- **未修改**：CLAUDE.md（含概览区与规则节）、docs 冻结文档（含白盒 v2 报告）、boards/、legacy 目录、workspaces/、.mcp.json。
- **未执行**：任何 git 写操作；任何 EDA/host_live/device_live 工具。
- **明确不修**：UART 捕获丢字节加固（留 P2 记录，由需求 v2.1 承接）。
- **未自行冻结 Brick、未越级进入下一步骤；未调用 Agent2。**

## 8. 需主代理注意 / 局限（如实）

1. **项 #1 长跑分支包装未做**：`_pl_bridge` long-run（synth/place/route）与 `_pl_bridge`+process_controller 分支不包 `_execute` 的 `asyncio.wait_for`，其内部由 `run_vivado_run`/bridge eval 自带 bound。若这些长跑工具超过其 **op deadline**（默认 synth=1800s/pnr=3600s/bitstream=600s 由 `timeout_config.deadline_for_tool` 决定）也需强制 TIMED_OUT 释放通道，需在相关长跑分支另行接入 deadline 界——本轮为规避对 O4-observer/B04 串行链的竞态风险，未强行包装，报告为局限。建议下一轮针对长跑分支单独评估。
2. **项 #3 为咨询提示而非自动续跑**：`resume_hint` 只提示"项目已有产物"，不自动推导/恢复 stage（复杂且有误判风险）。真正的"续跑"若要落地，需一个显式恢复工具（如 `resume_stage`）或 `create_session` 可选参数（如 `resume_from_artifacts`），属更大变更，本轮评估后选择最小 fail-closed 咨询方案。
3. **项 #4 为文档纪律**：Skill 的纪律项是开发流程检查，无自动化测试（Skill 契约测试仅校验无禁词/结构）。这些纪律的执行依赖智能体遵守，无机械门禁兜底。
4. **并行产物**：`docs/development/tests/B12_a2_whitebox_v2_report.md` 与顶层 `bitstream/` 目录的变动未触碰，请勿将其计入本子代理改动。
