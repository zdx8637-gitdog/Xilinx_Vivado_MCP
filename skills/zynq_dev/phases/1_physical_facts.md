# S1 — 物理事实清单（Physical Facts）

> 输入: 需求 + 板卡配置包 | 输出: 物理事实表 + 已验证 session

## 职责

从板卡配置包（Board Configuration Package，含 board profile JSON / 板卡 XDC /
ps7 preset）提取板级事实，与需求文档交叉校验，形成物理事实表。**本 Skill 不
臆造物理事实**——查不到就写「未确认」，缺失的必需项阻塞并列出。

## 执行序列

| 步骤 | 工具类别 | 说明 |
|------|----------|------|
| 1 | control：`create_session` | `{"board_id": "<BOARD_ID>", "project_path": "<PROJECT_PATH>"}`；记录 `<SESSION_ID>` |
| 2 | control：`get_execution_state` | 确认 `execution_lane == "IDLE"`、`current_stage == "PLATFORM_DESIGN"` |
| 3 | 工作区读（允许） | 读取板卡配置包，记录 `board_profile_sha256`（S6 校验要用） |

## 物理事实表字段

| 字段 | 来源 | 说明 |
|------|------|------|
| 板卡型号 / 器件型号 | 板卡配置包 | `<BOARD_ID>` / `<PART>` |
| 外设型号与接口 | **用户**（现实层） | 需求声明的外设型号、数据接口类型、分辨率/速率、通道数、参考/量程 |
| 引脚分配 | **用户**（现实层） | 目标信号所在的物理引脚（写进约束文件的依据） |
| 电平方案 | **用户**（现实层） | 电平标准；非标准电平需电平转换方案 |
| 时钟 | 板卡配置包 + 用户 | 板上时钟、需求要求的时钟域 |
| 未确认项 | — | 一律标注「未确认」，不得推断 |

## 智能体自主决策范围

- 从板卡配置包提取板级事实并做交叉校验（例如 UART 桥片型号与 VID/PID 对应）；
- 决定哪些事实已满足 S2 预算输入。

## 用户必须提供的物理事实（现实层）

外设型号、数据接口类型、分辨率、最大速率、通道数、参考电压/量程、
**引脚分配**、板级电平方案。缺一项 → 阻塞并列出必需项。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| `create_session` 失败 | `<BOARD_ID>` 不存在或已有活动执行上下文：检查 `<BOARD_ID>`；用 `get_execution_state` + `diagnose_execution` 取公开证据，禁止删除 runtime 文件 |
| `execution_lane != "IDLE"` | 前次 Operation 或资源仍活动/待恢复：按 `recommended_action` 处理；只有诊断明确建议恢复时才 `recover_execution`，失败则停止并报告 |
| 事实缺失 | 阻塞并列出必需项；查不到写「未确认」 |

## 涉及的工具类别

- control query：`create_session`、`get_execution_state`、`diagnose_execution`、`recover_execution`（恢复时）。
- 工作区只读：板卡配置包、需求文档。
