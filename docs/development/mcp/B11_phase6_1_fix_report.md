# B11 阶段⑥.1 整改轮报告（crash-recover 残留 → 门禁永久拒绝）

> 日期：2026-08-15（`Get-Date` 实测）
> 状态：本轮为阶段⑥ Agent2 终验 BLOCKED 的 P1 服务端缺陷整改轮；修复后须由
> **全新无记忆 Agent2** 重新验收（B11_plan.md 阶段⑥门禁 6，另行安排）。
> 基线：`1417 collected / 1376 passed / 1 skipped / 40 deselected / 0 failed`
> （CLAUDE.md 记录，与阶段③.2 报告一致）。

## 0. 触发背景：Agent2 终验 BLOCKED（事件链）

Agent2（全新上下文黑盒验收）在隔离工作区 `D:\_b11_p4_external\agent2_20260815\`
判定 **BLOCKED**，证据目录 `evidence/`（REPORT.md + mcp_calls.jsonl 共 134 条）：

1. 实现期自身错误：`platform_make_external` 两次调用传入臆造的引脚名
   （`gpio_rtl_tri_o` / `gpio2_rtl_tri_i`，实际为 `gpio_io_o` / `gpio2_io_i`）→
   BD 出现 3 个悬空端口（REPORT.md §6.1 步骤 13）。
2. 恢复期遭遇服务器端状态门禁：重建 BD 的 `close_session` 后端清理失败
   （`BACKEND_SHUTDOWN_FAILED`，mcp_calls.jsonl `ts=1786815254.38`；
   REPORT.md §9.2 步骤 2）→ `recover_execution` 后**旧 Vivado worker 记录残留**；
3. 此后**所有 command 工具**（platform/pl 域，含 `platform_create_design`×7、
   `pl_get_vivado_info`×1）被 `UNOWNED_WORKER_PRESENT`
   （"Ledger contains a worker not owned by this controller"）预检门禁永久拒绝；
   `recover_execution`（7 次，含双连发）、服务器进程重启（3 次）、新会话 + 新
   project_path（5 个）**均无法解除**（mcp_calls.jsonl 6 次 wait_operation
   FAILED 证据：`ts=1786816872…1786819118`；REPORT.md §9.2 步骤 3–7）。

Agent2 遵守 skill「公开边界（硬门禁）」未编辑 Ledger/runtime/锁文件，按
「公开能力缺失必须停止并报告产品缺口」停止（REPORT.md §9.3）。

---

## 1. 现象

`recover_execution` 返回 worker ABSENT、generation 递增，但随后任意 command
（经 `CommandRunner._ensure_controlled_vivado` → `ToolProcessController.
ensure_backend`）仍抛 `UNOWNED_WORKER_PRESENT`；recover/服务器重启/新会话
均无法解除。表现为对全部 command 域（platform/pl/ps）的永久拒绝。

## 2. 根因（先读代码确认，含字段清单与写入位置）

### 2.1 门禁判定（fail-closed，语义正确，不在本轮改动）

`mcps/zynq_mcp/control/tool_process_controller.py`：

- `_ensure_backend` L255–262：`recorded_backend = worker.get("backend", BACKEND_NONE)`；
  `recorded_state = worker.get("state", WORKER_STATE_ABSENT)`；当
  `recorded_backend not in (None, "", BACKEND_NONE)` 或
  `recorded_state not in (ABSENT, DEAD)` → 抛 `UNOWNED_WORKER_PRESENT`。
- `_commit_started` L324–329：同一门禁（`ChannelBusyError("UNOWNED_WORKER_PRESENT")`）。

即：**Ledger 中 worker 的 `backend` 字段只要残留非 NONE 值，门禁即永久拒绝**，
无论 state 是否已被置 ABSENT。

### 2.2 worker 记录字段全集（`_worker_record`，tool_process_controller.py L342–363）

`backend / state / pid / process_start_time / executable_path / executable_args /
worker_generation / instance_id / supervisor_pid / supervisor_process_start_time /
supervisor_executable_path / last_heartbeat_at / project_lease_held /
jtag_lease_held / jtag_lease / serial_owner / uart_capture`。

Ledger 默认 worker 记录（execution_ledger.py L330–335）无 `backend` 键、无任何
identity/supervisor/instance 字段（`instance_id=None`）——即「从未有过 worker」
的缺省语义：`worker.get("backend", BACKEND_NONE)` 取 NONE，门禁放行。

### 2.3 残留字段的产生路径（grep 全部写 `worker["backend"]` 位置）

| 路径 | 行为 | 是否清 backend |
|---|---|---|
| `_commit_started` → `_worker_record` | 正常启动：backend=VIVADO/XSCT/XSDB | — |
| `_commit_absent`（正常 shutdown，L623–649） | backend=NONE + 全 identity 清空 | ✅ 清 |
| `_persist_failure`（L500–558） | 崩溃/身份失配：只置 state=DEAD/POISONED + lane=RECOVERY_REQUIRED，**其余字段保留** | ❌ **漏清** |
| `_set_crash` / `start_reconcile`（single_worker.py / server.py） | 崩溃路径 | 部分（`_succeeded_auto_recover` L557–581 清 backend，但 OUTCOME_UNKNOWN/FAILED/ORPHANED 路径保留） |
| `_shutdown_locked` 清理失败分支（L589–609） | close_session 触发：success=False → 走 `_persist_failure` | ❌ **漏清** |

### 2.4 缺陷根因（唯一修复点）

`mcps/zynq_mcp/control/recovery.py` `recovery_mutator`（修复前 L63–66）：
P1（无活进程）通过后只清 `state=ABSENT`、`pid=None`、`last_heartbeat_at=None`、
`worker_generation+1`、leases、serial_owner——**未清 `backend`、process identity
（process_start_time/executable_path/executable_args）、`instance_id`、supervisor
三字段**。崩溃 + shutdown 失败后 Ledger 残留 `backend="VIVADO"`（及
`instance_id`/supervisor 等），`recover_execution` 后门禁仍见 backend≠NONE →
`UNOWNED_WORKER_PRESENT` 永久拒绝。

**死锁的第二个层面**：修复前 recover 会把 lane 置 IDLE，此后再次
`recover_execution` 走 IDLE no-op 分支（ALREADY_IDLE），**不再触碰任何字段**——
这正是 Agent2 连调 7 次 recover 无果、服务器重启/新会话也无效的原因
（`create_session` 只写 context/dedup_registry，`close_session` 只清 context，
均不触碰 worker 记录）。

### 2.5 close_session shutdown 失败路径确认（需求 3）

`dispatcher._close_session_atomic` Step 2b（L290–315）调用
`controller.shutdown_backend(force=True)`（即 `_shutdown_locked(persist_absent=
True)`）。失败分支（L589–609）：PIDs 未清或 ABSENT 写失败 → `success=False` →
`_persist_failure`（保留 backend）→ dispatcher 报 `BACKEND_SHUTDOWN_FAILED` 并
`_close_failed` 写 RECOVERY_REQUIRED。此后 **recover_execution 是唯一公开出路**
（不存在其它公开工具改写 worker 记录；skill 硬门禁禁止直接编辑 Ledger）——
因此修复点收敛在 `recovery_mutator` 一处，`_shutdown_locked`/dispatcher 不改。

---

## 3. 修复

### 3.1 `mcps/zynq_mcp/control/recovery.py`（唯一生产改动）

新增两个纯函数并改 `recovery_mutator`：

- `_owner_residue_present(w)`：按门禁同源条件检测残留——`backend` 非
  (None,"",BACKEND_NONE)、或 `state` 非 (ABSENT,DEAD)、或 pid / process identity /
  instance_id / supervisor_pid 任一存在。
- `_clear_owner_residue(w)`：把 owner/instance 字段重置为 Ledger 缺省
  （backend=BACKEND_NONE、state=ABSENT、pid=None、process_start_time/
  executable_path/executable_args=None、instance_id=None、supervisor 三字段=None、
  last_heartbeat_at=None）——与 `_worker_record`/`_commit_absent` 的缺省语义
  一致，恢复后的记录对下一控制器**等价于「从未有过 worker」**。
  jtag_lease/uart_capture/serial_owner 资源证据不在本函数内处理（由调用方决定，
  原逻辑保留）。
- `recovery_mutator`：
  - **RECOVERY_REQUIRED 主路径**（P1 活进程、P2–P4 资源、P5 活动 Operation 检查
    全部保持原样）：P6–P7 commit 时以 `_clear_owner_residue(w)` 替换原来的
    `w["state"]=ABSENT; w["pid"]=None; w["last_heartbeat_at"]=None`。
  - **IDLE 分支**：保持「活 PID 不触碰」（正常稳态 no-op，历史语义不变）；
    无活进程时若检测到 owner/instance 残留（修复前 recover 遗留的
    `{state:ABSENT, backend:VIVADO, pid:None}` 死锁态）→ 清除残留并记录
    `RESIDUE_CLEARED`，否则记录 `ALREADY_IDLE`（原 no-op 语义）。

### 3.2 门禁不削弱论证（需求 2）

- `_ensure_backend` L255–262 / `_commit_started` L324–329 **零改动**：对
  「state 非 ABSENT/DEAD、或活 PID、或 lease 持有」的 worker 仍照旧拒绝。
- 只有「已确认真实死亡/无资源」的残留（P1 无活进程 + P2–P4 无资源）才被
  recover 清除——门禁语义 fail-closed 不变。
- 既有门禁测试保持通过（见 §4 门禁保持清单）：
  - `test_o222_legacy_worker_record_blocks_direct_backend_start`
    （test_o2_tool_process_controller.py L520–541，fixture=READY+backend NONE+
    pid 500 → `UNOWNED_WORKER_PRESENT`）：fixture 是「活 worker（READY 状态）/
    他实例」，非「ABSENT+backend 残留」，语义与本次修复目标**不冲突**，
    **原样保留、零改动**；
  - `test_worker_alive_blocks`（test_r1_recovery.py L46–51）与
    `test_e2_recover_still_refused_with_active_operation`
    （test_b11_heartbeat_remediation.py L352–379）：活 PID →
    `RECOVERY_BLOCKED_WORKER_ALIVE` 保持。

### 3.3 Skill 增强（P2，一并做）

`skills/zynq_dev/appendix_mechanics.md` §3 platform 原子序列模板补**决策规则
（连接/外部化前命名）**：「`platform_connect_interface` /
`platform_connect_clock` / `platform_connect_reset` / `platform_make_external`
中的引脚/接口名必须来自真实对象查询——工程内 IP 边界描述、BD 单元/引脚清单等
实际查询结果，不得臆造命名（臆造的引脚名会让 create_bd_port 成功但连线失败，
留下悬空端口，validate 报 critical warning）；查询不可得时停并报告」。零字样
门禁（gpio 大小写不敏感 / 0x41200000 / LED 整词 / breath|blink 大小写不敏感）
保持 0 命中（§5.4 实测）。

---

## 4. 测试

### 4.1 新增测试清单

| 测试 | 文件 | 级别 | 说明 |
|---|---|---|---|
| `test_recover_from_recovery_required_residue_clears_owner_fields` | `test_b11_phase6_1_recovery_residual.py`（新建） | 组件 | RECOVERY_REQUIRED + {backend:VIVADO, state:DEAD, pid:None, identity/supervisor/instance 残留} → recover → 全部字段清 + generation 递增 + 新控制器 `ensure_backend` 准入成功 |
| `test_recover_heals_idle_lane_residue_left_by_pre_fix_recover` | 同上 | 组件 | **Agent2 死锁态**：lane=IDLE + {state:ABSENT, backend:VIVADO, pid:None} → recover → `RESIDUE_CLEARED` + backend 清 + 新控制器真实启动 |
| `test_recover_from_dead_record_then_ensure_backend_admits` | 同上 | 组件 | 需求回归规格原文场景：{state:ABSENT, backend:"VIVADO", pid:None} → recover → `_ensure_backend` 不再抛 UNOWNED_WORKER_PRESENT |
| `test_recover_idle_with_live_worker_is_noop` | 同上 | 组件+真实进程 | IDLE + READY + 活 PID（真实子进程）→ recover 完全不触碰（ALREADY_IDLE） |
| `test_gate_still_refuses_live_worker_owned_by_other_instance` | 同上 | 组件 | READY + 活 PID + 他实例 owner → recover no-op；`_ensure_backend` 仍 UNOWNED_WORKER_PRESENT |
| `test_recovery_still_blocks_alive_pid` | 同上 | 组件 | RECOVERY_REQUIRED + 活 PID → 仍 RECOVERY_BLOCKED_WORKER_ALIVE |
| `test_crash_cleanup_failure_recover_then_new_session_command` | 同上 | **真实进程级** | 完整复现 Agent2 链：真实后端子进程启动 → `kill_process_tree_exact` 异常终止（真崩溃、不主动释放）→ shutdown 身份无法核验 fail-closed（BACKEND_CLEANUP_FAILED / BACKEND_IDENTITY_LOST_DURING_CLEANUP）→ 残留 {backend:VIVADO, state:POISONED, lane:RECOVERY_REQUIRED} → recover 清残留 → close_session → 新会话（create_session_mutator）→ 新 command 准入（preflight ACCEPTED）→ 新控制器 `ensure_backend(operation_id)` 真实启动成功（新 PID、generation 3） |
| `test_recover_residue_then_gate_accepts_without_cleanup_failure` | 同上 | 真实进程级 | 服务器重启场景：IDLE lane + {backend:VIVADO, state:DEAD, pid:None} 残留 → recover → 新控制器真实启动 |
| `test_skill_connect_external_names_must_come_from_real_queries` | `test_o6_skill_contract.py`（新增） | Skill 契约 | 扫描 appendix_mechanics.md 存在「决策规则（连接/外部化前命名）/ 引脚/接口名 / 必须来自真实对象查询 / 不得臆造命名 / 查询不可得时停并报告」 |

新增 **9** 个测试；**0 删除、0 替换、0 重命名**。

### 4.2 门禁保持（既有测试，全部继续通过）

- `test_o222_legacy_worker_record_blocks_direct_backend_start`
  （test_o2_tool_process_controller.py L520–541）→ `UNOWNED_WORKER_PRESENT` ✓
- `test_worker_alive_blocks`（test_r1_recovery.py）→ `RECOVERY_BLOCKED_WORKER_ALIVE` ✓
- `test_e2_recover_still_refused_with_active_operation`
  （test_b11_heartbeat_remediation.py）→ `RECOVERY_BLOCKED_WORKER_ALIVE` ✓
- `test_o206_cleanup_failure_blocks_new_backend`、
  `test_o207_external_crash_does_not_auto_restart` 等 O2 全套 ✓
- `test_idle_recovery_is_noop`（test_r1_recovery.py）→ 无残留时 IDLE recover 仍
  ALREADY_IDLE no-op ✓

### 4.3 既有测试受影响面

`recovery_mutator` 在无残留时行为与修复前逐字段一致（多写的字段本就是
ABSENT/None/False 的幂等值）；IDLE 分支在无残留/有活 PID 时仍是 no-op。
受影响套件（recovery / O2 / heartbeat-remediation / server-startup-reconcile /
r2-adapter / r1-session / o6-skill-contract）**全部通过**（§5.2）。

---

## 5. 回归与门禁

### 5.1 测试统计（机械实测，项目根目录）

- 基线：`1417 collected / 1376 passed / 1 skipped / 40 deselected / 0 failed`。
- 本轮新增测试 **9** 个（`test_b11_phase6_1_recovery_residual.py` +8、
  `test_o6_skill_contract.py` +1）；**0 删除、0 替换**。
- 收集：`1426 collected`（`--collect-only -q`，机械实测）。

### 5.2 完整非硬件回归结果（机械实测）

```
python -m pytest mcps -m "not host_live and not device_live" -q
1426 collected / 1385 passed / 1 skipped / 40 deselected / 0 failed   (218.10s)
（1386 运行 = 1426 − 40 deselected；40 = 36 host_live + 4 device_live）
```

- 对照基线：collected 1417 → **1426**（+9）；passed 1376 → **1385**（+9）；
  skipped 1 → 1；deselected 40 → 40；failed 0 → **0**。**无下降**。

### 5.3 受影响套件专项（机械实测）

```
python -m pytest mcps/zynq_mcp/tests/test_b11_phase6_1_recovery_residual.py -v
8 passed
python -m pytest mcps/zynq_mcp/tests/test_r1_recovery.py mcps/zynq_mcp/tests/test_o2_tool_process_controller.py mcps/zynq_mcp/tests/test_b11_heartbeat_remediation.py mcps/zynq_mcp/tests/test_server_startup_reconcile.py mcps/zynq_mcp/tests/test_r2_adapter.py mcps/zynq_mcp/tests/test_r1_session.py mcps/zynq_mcp/tests/test_o6_skill_contract.py -q
首轮 103 passed + 1 failed（test_r1_recovery 的 WORKER_STATE_DEAD 导入缺失，
修复即补 import）→ 修复后 recovery + 新文件重跑 15 passed → 完整回归 §5.2
1385 passed 全绿
```

### 5.4 机械门禁

- 新/改代码空 pass 扫描：**0 命中**（recovery.py 中 `except Exception: return
  False/0.0` 为既有 `_hb_current`/`_parse_iso`，非本轮新增；测试 fixture teardown
  改为收集清理错误并断言，无静默吞异常）。
- **Skill 零字样门禁**（`skills/zynq_dev/` 全 md 机械扫描，`test_o6_skill_contract.py::
  test_skill_mechanical_gate_zero_project_terms` 实测）：
  `gpio`（大小写不敏感）/ `0x41200000` / `LED`（整词）/ `breath|blink`
  （大小写不敏感）**0 命中**（新增决策规则段未引入任何禁词）。
- `.mcp.json` SHA256 = `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`
  **不变**（机械实测，与 O1–O6 冻结记录一致；内容 `{"mcpServers": {}}` 未改）。

---

## 6. 变更文件清单与 SHA256

### 生产代码（1）
| 文件 | SHA256 |
|---|---|
| `mcps/zynq_mcp/control/recovery.py` | `996b78c0732b6241281a61b45fb9dd635da2b9bb45f5c1504ef2dff951189b64` |

### 测试（2）
| 文件 | SHA256 |
|---|---|
| `mcps/zynq_mcp/tests/test_b11_phase6_1_recovery_residual.py`（新建） | `c2da21fed241a84a70a6c497ac56bcf745e92aea606d8cdba1214c208831b84e` |
| `mcps/zynq_mcp/tests/test_o6_skill_contract.py` | `a5f9ac64ec29146c53697ed1e7c26ad8dc685a02e9f3293fbd61f27392ad13e1` |

### Skill / 文档（3）
| 文件 | SHA256 |
|---|---|
| `skills/zynq_dev/appendix_mechanics.md` | `1573e5ee5e761c54ea265b7af9f41b1bfa533e50cd16090e35a2cb8cff3c0fa9` |
| `docs/development/mcp/B11_plan.md`（追加「阶段⑥.1 整改轮记录」） | 提交后校验值见交付汇报 |
| `docs/development/mcp/B11_phase6_1_fix_report.md`（本文档，新建，自引用） | 见 Git 段注（先例：`B11_phase3_2_fix_report.md` 同此处理） |

### Git
- 提交信息：`B11 phase 6.1: fix UNOWNED_WORKER_PRESENT residual after crash-recover (recovery clears backend/owner fields), skill add pin-name discovery rule, tests reproduce Agent2 chain`
- 提交 hash 与推送结果：**见交付汇报**（先例同此处理，报告内不记录自身提交 hash）。

> 注：本文档为自引用文件，自身 SHA256 无法写入自身内容（先例同此处理）；提交后
> 校验值见交付汇报。

---

## 7. Agent2 事件链复现证据引用

| # | 证据 | 位置 |
|---|---|---|
| 1 | `BACKEND_SHUTDOWN_FAILED`（close_session 后端清理失败） | `D:\_b11_p4_external\agent2_20260815\evidence\mcp_calls.jsonl` ts=1786815254.38 |
| 2 | `recover_execution` 后门禁仍拒（platform_create_design / pl_get_vivado_info 6 次 FAILED） | 同文件 ts=1786816872…1786819118（6 条 wait_operation） |
| 3 | 判定结论与恢复尝试全表（7 次 recover、3 次重启、5 个新会话） | `…\evidence\REPORT.md` §0、§6.2、§9.2 |
| 4 | 臆造引脚名 → 悬空端口（PLATFORM 类自身错误，修复方案在 §6.2） | `…\evidence\REPORT.md` §6.1 步骤 13、§11（`workspace/project/` BD + .xci） |

---

## 8. 禁区零触碰声明

- `boards/`、架构文档（`docs/architecture_ai_zynq7020.md`）、
  `docs/brick_development_plan.md`、README、CLAUDE.md、三个 legacy 目录
  （`Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/`）、
  `validation_projects/`：**零改动**。
- `workspaces/`：**零改动**（未新建任何证据文件）。
- 生产代码改动仅 `mcps/zynq_mcp/control/recovery.py` 一处；门禁
  （`tool_process_controller.py`）与 close_session/dispatcher **零改动**。
- 未自行冻结任何 Brick、未越级进入 Agent2 重验（另行安排）。
