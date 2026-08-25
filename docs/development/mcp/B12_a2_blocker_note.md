# B12-A2 白盒首轮 BLOCKED 处置记录（P1 框架死锁，宿主级处置）

> 日期：2026-08-25 ｜ 触发：`docs/development/tests/B12_a2_whitebox_report.md`（commit `d0281b5`）

## 1. 现象与根因（子代理已定位）

- Platform/PL 全部建好（bitstream 时序满足、WNS=0），`ps_import_hardware` 成功；
- `ps_create_platform` **卡在 ADMISSION（ACCEPTED/NOT_STARTED）** >15 分钟；`rdi_xsct.exe` 存活但心跳 STALE；
- `recover_execution` → `RECOVERY_BLOCKED_WORKER_ALIVE`；`close_session` → `ACTIVE_OPERATION_PRESENT`；admission 期限无自动超时；
- 属 B11-N2 类（BUSY 通道 + 活进程 + 心跳陈旧）的变体，公开契约无出路。

## 2. 缺陷登记（只记录，生产代码未改）

- **D1（P1）**：ADMISSION 卡死无自动超时 + 陈旧活 worker 无公开清除路径（recover 被 fail-closed 挡住）；
- **D2（P2）**：无公开 PL stage 回退（analyze_timing→PL_BITSTREAM 后 pl_create_project/synthesize 被硬门禁，只能重启会话）；
- **D3（P2，已修）**：Vivado XDC 把行中 `#` 当参数（Common 17-161）→ 约束失效 → DRC UCIO-1；注释换行后通过。

## 3. 宿主级处置（本次，未动生产代码）

- 现场核查：白盒报告后 rdi_xsct（pid 5620）**已自行退出**（CIM 无该进程），死锁只剩孤儿 ledger（lane=BUSY、ACCEPTED op、死 pid）；
- 处置：旧 runtime（`workspaces/b12_a2_agent1_20260825/runtime`）弃用留证；**全新 workspace+新 runtime 重跑白盒**（B11-N2 先例）；
- 判据：若重跑在同一位置复现卡死 → 判定系统性缺陷，停止并等用户，随后安排修复轮（admission 期限强制 + 陈旧活 worker 恢复路径）；
- 晚间待办追加：D1/D2 修复轮与 A1 外部可验证性补强合并评估。

## 4. 遗留

- 旧 workspace 的 bitstream/固件/分析脚本作为白盒参考保留（新代理可复用设计，但流程必须重走公开 MCP）。
