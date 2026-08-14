# Zynq GPIO Development Skill

> Brick: B07 | 基于 B01 v1.2.2 标准流程 | 仅 GPIO 切片

## 技能声明

我是一个 Zynq-7020 GPIO 开发技能。我**只**能做通过 AXI GPIO 控制 PL LED 的项目。
我不支持：中断、DMA、自定义 RTL、DDR 共享、FreeRTOS/Linux、QSPI/SD 启动、ILA 调试。

## 接收的需求格式

当用户描述一个 GPIO 项目需求时，将其转换为以下格式：

| 字段 | GPIO 示例值 | 必填 |
|------|-----------|:--:|
| `board_id` | `ALINX_AX7020_v1.0` | 是 |
| `part` | `xc7z020clg400-2` | 是 |
| `functional_requirement` | ARM 通过 AXI GPIO 控制 4 个 PL LED | 是 |
| `ps_software` | Bare-metal C, UART 115200 8N1, 输出 PASS/FAIL 标记 | 是 |
| `pl_logic` | 无（仅 BD 集成） | 是 |
| `ps_pl_communication` | AXI GPIO via M_AXI_GP0, 4-bit, output | 是 |
| `clocks` | PS FCLK0 = 50 MHz | 是 |
| `resets` | FCLK_RESET0_N → Processor System Reset | 是 |
| `addresses` | AXI GPIO @ 0x41200000 | 是 |
| `interrupts` | 无 | 否 |
| `dma` | 无 | 否 |
| `deployment` | JTAG only | 是 |
| `observable_output` | UART markers + LED visual (auxiliary) | 是 |
| `pass_condition` | UART 输出 `GPIO_E2E_PASS` | 是 |
| `fail_condition` | UART 无输出/超时/标记不完整 | 是 |

## 执行流程概览

```
Phase 0: 板卡验证    → 确认 board_id 和 profile SHA256
Phase 1: Platform    → platform_generate {} 生成 BD + XSA
Phase 2: PL Build    → system_top → synth → place → route → bitstream
Phase 3: PS Software → import XSA → BSP → app → compile → ELF
Phase 4: Consistency → verify_consistency 跨域校验
Phase 5: Deployment  → JTAG 烧录 bitstream + 下载 ELF + UART capture
Phase 6: Observation → evaluate_observation → PASS/FAIL
Phase 7: Recovery    → 错误分类 + 诊断工具（出问题时用）
```

**严格串行。** 每个 Phase 成功后才进入下一个。Phase 之间通过 Manifest 传递产物信息。

## Phase 对应文档

| Phase | 文档 | 关键 MCP Tools |
|-------|------|---------------|
| 0 | [phases/0_board_profile.md](phases/0_board_profile.md) | `create_session`, `get_execution_state` |
| 1 | [phases/1_platform_design.md](phases/1_platform_design.md) | `platform_generate`, `wait_operation` |
| 2 | [phases/2_pl_build.md](phases/2_pl_build.md) | `pl_generate_system_top`, `pl_create_project`, `pl_generate_target`, `pl_synthesize`, `pl_place`, `pl_route`, `pl_analyze_timing`, `pl_generate_bitstream` |
| 3 | [phases/3_ps_software.md](phases/3_ps_software.md) | `ps_import_hardware`, `ps_create_platform`, `ps_create_bsp`, `ps_create_app`, `ps_add_sources`, `ps_compile` |
| 4 | [phases/4_consistency.md](phases/4_consistency.md) | `verify_consistency` |
| 5 | [phases/5_deployment.md](phases/5_deployment.md) | `pl_program_fpga`, `ps_start_uart_capture`, `ps_download_elf`, `ps_run_target`, `ps_wait_uart_capture`, `ps_stop_uart_capture` |
| 6 | [phases/6_observation.md](phases/6_observation.md) | `evaluate_observation` |
| 7 | [phases/7_debug_recovery.md](phases/7_debug_recovery.md) | `get_operation_status`, `get_execution_state`, `diagnose_execution`, `recover_execution`, `ps_diagnose_uart_clock` |

## 公开边界（硬门禁）

本 Skill 的正式执行面只有当前统一 `zynq_mcp` 的公开 tools。智能体可以在
`project_path` 内创建需求输入文件（例如 `main.c`、XDC）并读取公开产物，但不得：

- 导入任何 MCP 内部 Python 包或实例化内部 bridge/controller；
- 自行启动、停止或调用 Vivado、Vitis、XSCT、XSDB、Tcl shell 或旧 MCP；
- 用 shell 编译/链接应用，或绕过 `ps_compile`；
- 手工生成、发布或修改 Platform/PL/PS Manifest；
- 直接读取、编辑或删除 Execution Ledger、runtime 状态、锁文件；
- 按进程名杀进程，或在 MCP 之外重试不确定结果。

公开能力缺失或返回不确定状态时必须停止并报告产品缺口，不能临时写脚本绕过。

## 长任务与真实状态规则

所有 command tool 都先返回 `operation_id`。随后只使用 `wait_operation` 或
`get_operation_status`，并保存每次公开响应形成状态时间线。至少读取：

`status`, `status_source`, `backend`, `observed_state`, `vendor_status`,
`current_step`, `observation_quality`, `last_progress_at`, `artifact_state`,
`deadline_at`, `recommended_action`。`progress_pct` 是可选字段；缺失不影响判断。

```
command → operation_id
  → wait_operation(operation_id, bounded_timeout)
  → RUNNING + recommended_action=WAIT       → 继续有界等待
  → RUNNING + recommended_action=DIAGNOSE   → diagnose_execution，再按返回建议处理
  → RECOVERY_REQUIRED / recommended_action=RECOVER
                                                → 先 diagnose_execution；仅在公开诊断确认
                                                   无活动受控进程/资源后调用 recover_execution
  → SUCCEEDED + artifact_state=PUBLISHED（Manifest 产物型操作） → 下一步
  → FAILED/TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → 停止正常流程，进入 Phase 7
```

`wait_operation` 的等待超时不等于 Operation 超时：若返回 `wait_timed_out=true`
且 Operation 仍为 `RUNNING`，不得把它当作失败或重新提交同一命令。所有判断以
Ledger 返回的真实 backend observation 和 `recommended_action` 为准。

## 对话丢失恢复（Context Recovery）

当你接手一个已有工作目录时，**不要盲目重跑所有 Phase**。先检查：

### 1. 通过公开状态和 Manifest 文件恢复上下文

```
workspaces/<project>/manifests/
├── platform/sha256_<rev>.json    ← Phase 1 完成的证据
├── pl/sha256_<rev>.json          ← Phase 2 完成的证据
└── ps/sha256_<rev>.json          ← Phase 3 完成的证据
```

先调用 `get_execution_state` 和 `diagnose_execution`。只读列出项目目录中的
Manifest 是允许的；不得读取或修改 runtime/Ledger 文件。随后用
`verify_consistency` 判断状态：

```
verify_consistency(
    platform_manifest_path = "manifests/platform/sha256_<rev>.json",
    pl_build_manifest_path  = "manifests/pl/sha256_<rev>.json",  # 如果有
    ps_build_manifest_path  = "manifests/ps/sha256_<rev>.json",  # 如果有
    board_profile_sha256    = "sha256:a7cb97..."
)
```

### 3. 根据结果决定下一步

| 结果 | 含义 | 动作 |
|------|------|------|
| `all_passed = true` | P1/P2/P3 全部完成且一致 | 进入 Phase 5 部署 |
| `failed` 非空 | 某个 manifest 数据不一致 | 找到不匹配的 Phase，从那里重跑 |
| `skipped` 非空 | 当前公开证据不完整 | 停止部署；从缺失 Manifest 对应的 Phase 重新执行公开 MCP 流程 |
| Platform Manifest 存在但 PL/PS manifest 不存在 | P2/P3 未完成 | 从缺失 Phase 开始；禁止根据散落产物手工补 Manifest |
| 所有 manifest 都不存在 | 全新项目 | 从 Phase 0 开始 |

### 4. 检查 session 状态

```
get_execution_state() → lane, stage, worker_state
  - lane = RECOVERY_REQUIRED → 调用 recover_execution
  - stage = PL_GENERATE → Phase 1 已完成，直接进入 Phase 2
  - stage = PL_BUILD → system_top 已就位
```

**原则**：公开 Ledger 状态 + 自动发布的 Manifest 是证据。磁盘上存在孤立的
bitstream/ELF 不能代替终态和 Manifest。

⚠️ **当前限制**：MCP `platform_generate` 不实现幂等检查——即使 XSA 和 Manifest
已存在也会重新执行（~5 分钟）。如果 Manifest 存在且 `verify_consistency` 通过，
可以直接进入 Phase 2，跳过 Phase 1 重新生成。

## 工具前缀约定

| 前缀 | 域 | 用途 |
|------|----|------|
| `create_*` / `close_*` / `get_*` / `recover_*` | Control | Session 管理 |
| `platform_*` | Platform | BD 设计 + XSA 导出 |
| `pl_*` | PL | FPGA 综合/布局/布线/bitstream |
| `ps_*` | PS | ARM 软件 + JTAG 部署 + UART |
| `verify_*` / `evaluate_*` | Verification | 跨域校验 + 判定 |

## Session ID 传递规则

| 工具前缀 | 是否需要 session_id | 说明 |
|---------|:--:|------|
| `create_session` / `close_session` | 否 | Control 层自动注入 |
| `get_*` / `recover_*` / `wait_operation` | 否 | Control 层自动注入 |
| `platform_*` | 否 | Transport 自动注入 |
| `pl_*` | 否 | Transport 自动注入 |
| `ps_*` | **是** | PS domain schema 要求显式传入 |
| `verify_*` / `evaluate_*` | 否 | Query tool |

**规则**：所有 `ps_*` 前缀的 domain tool 调用时都必须显式传入 `session_id` 参数。
其他 tool 不需要。不加 `session_id` 会返回 `INVALID_ARGUMENT / SESSION_ID_REQUIRED`。

## 工作目录约定

所有产物写入 session 创建时指定的 `project_path` 目录。**禁止**写入 `mcps/`、`zynq_platforms/`、`embedded_projects/` 源码目录。

## 当前限制（B07 范围）

| 限制 | 说明 |
|------|------|
| 无自定义 RTL | system_top 仅实例化 BD wrapper |
| 无中断 | 需要 AXI INTC + GIC，非当前切片 |
| 无 DMA | 需要 AXI DMA + HP port，非当前切片 |
| 无非 JTAG 部署 | BOOT.BIN 打包不在范围内 |
| UART 仅 115200 | 波特率固定，变更需修改 C 源码 |
| 编译器选项 | `ps_set_compiler_options` 仅支持 `-D` 宏定义（Vitis 2023.1 XSCT 限制），GPIO 项目不需要 |
| `ps_write_uart` | UART 写入工具已注册，但 GPIO 项目流程中不需要 |
