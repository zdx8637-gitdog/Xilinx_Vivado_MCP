# B13-P4b 白盒 第三代接续 — 最终实现报告

日期：2026-09-06（接续会话）
状态：**完成主体交付，停止汇报，等主代理审计（bypass_audit + Phase R）与用户裁定。**

## 1. 本会话完成的工作

### 1.1 F-21 修复：PL 引擎 gear 同步链（100k 档物理不可达）
- 根因：`b13_engine.v` 齿轮跨域同步链丢 bit0
  （`gear_sync1 <= {gear_sync1[0], gear_sync0[1]}` → gear_run 只能是 00/11）。
- 修复：两级整宽同步 `gear_sync0 <= gear_reg; gear_sync1 <= gear_sync0;`
  （`src/b13_engine.v` 与 `project_p4b/ip_repo/user.org/user/b13_engine/1.0/src/b13_engine.v` 两处）。
- 重建路径：`workflow_resume_from` PLATFORM_DESIGN→PL_BUILD（平台 BD/XSA/包装器
  拓扑未变：xsa b34bbcb3、wrapper 07bc9182）；`pl_create_project` 增补
  `ip_repo_paths`（B13-F8 防陈旧 .gen 产物）+ 删除陈旧的
  `platform_bd_bd.gen` 输出产物强制重生成；全链 pl02..pl08 单进程跑完。
- 验证：新位流 sha **44523455**，新 PL 清单 **f0e1f536**，timing_met=true；
  三档 FSCAL 实测 **2001 / 100000 / 1000005 Hz**（±0.5% 内）——gear1 修复实锤。

### 1.2 F-22 修复：采集路径不武装事件计数器（STATUS 实测速率恒 0）
- `capture_begin` 在 `b13_eng_start` 前补 `ENG_CTRL_CLR` → `ENG_CTRL_ARM`
  （与 l2_counter_check 同款），速率测量不再依赖"先跑 SELFTEST"的隐式状态。

### 1.3 F-23 修复：拥塞下 END 帧一次性发送被静默丢弃
- END 帧移到行排空之后、`tcp_sndbuf >= 38` 才入队并重试到成功；
  删除 DONE 跳变处的一次性发送。

### 1.4 F-24 记录：脏 halt + 仅重下 ELF 毒化数据通路（~927 样点头部丢失）
- 已定位并绕行：最终验收链以全量部署（program_fpga）为起点；
  全量部署后 probe 证实 0 丢失。

### 1.5 F-25 记录（未完全解决）：lwIP NO_SYS 零窗口 persist 恢复损坏
- 溢出门禁排空在 35s 停读后卡死：DBG 证据 `sndwnd=65070`（窗口已开）、
  `cwnd=1446`（1 MSS）冻结 75s。尝试三剂：去 RCVBUF=4096、去 100ms
  RXEN 翻转工作区、断连重连排空（新连接满 cwnd）——尾部续传 255 行后仍停滞。
- 契约语义本身全部正确（丢最旧 + 溢出计数 40 + 尾连续 + 断连续传光标），
  残留缺陷在 lwIP 拥塞/持久计时器恢复层，裁定权交主代理。

## 2. 最终交付物

| 工件 | sha256 |
|---|---|
| 平台清单（未变） | c72e3858b1b4bb7698370d6b18efc69f2a50fd0a5fbfd187ab79e0ff8c42ccf6 |
| PL 清单（新） | f0e1f53690a0a0e46e51ab3082899b23fc0dc87c671c108b89c02162669534fe |
| PS 清单（新） | ed3e90eb77e7c008534eeff040065e7ec4447ad3c571584af1a60e46ac07a709 |
| 位流 bit/b13_p4b.bit | 445234552087acfc09003ea98b0c4e69803da224881ae0e6a642d48c81ec01f6 |
| ELF b13_p4b_app/Debug/b13_p4b_app.elf | 5294e86d8f3e2ba69a9131b90105fc89b444de6ddd3f813c0df097f109f9c711 |
| verify_consistency | **v10 = 12/12 PASS**（platform/pl/ps 三件套） |

## 3. 验收门禁结果（全量部署起点、顺序执行）

| # | 门禁 | 结果 | 关键值 |
|---|---|---|---|
| 1 | verify_consistency 12/12 | **PASS** | v10 日志 |
| 2 | L1 整图 TPG | **PASS** | rows=5000, bad_crc=0, tpg_mismatch=0, 2.0003 MB/s, 25M 点 |
| 3 | 三档 FSCAL | **PASS** | 2001 / 100000 / 1000005 Hz（±0.5%） |
| 4 | P3 真采 ADC | **PASS** | 25M 点, bad_crc=0, 2.0004 MB/s |
| 5 | STOP | **PASS** | 8/8 检查（含 idle 恢复） |
| 6 | 覆盖（ADC 后 TPG） | **PASS** | tpg_mismatch=0 |
| 7 | 溢出（丢最旧） | **FAIL** | 语义正确、排空卡死（F-25） |
| 8 | 重连续传 | **FAIL** | 被溢出残留排空态 NAK BUSY 连锁（F-25 下游） |

门禁 7/8 的残留缺陷与处置建议见 FINDINGS.md F-25（已附 DBG 证据链）。

## 4. FINDINGS 收尾

- 本会话新增：F-21（PL 缺陷，已修）、F-22（固件缺陷，已修）、
  F-23（固件缺陷，已修）、F-24（复位缺口，已记录绕行）、
  F-25（lwIP NO_SYS 排空恢复缺陷，已暴露未解决，裁定权交主代理）。
- 全部落盘于 `evidence/FINDINGS.md`，逐条含 Tool/现象/复现/影响/证据/建议分类。

## 5. 停止汇报

按任务书收尾要求：FINDINGS 收尾 + 实现报告已完成，现停止，等主代理
审计（bypass_audit + Phase R）与用户裁定 F-19/F-25 两个待裁定点。
