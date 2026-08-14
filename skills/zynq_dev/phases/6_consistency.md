# S6 — 一致性验证（Consistency Check）

> 输入: 三 Manifest + 产物文件 | 输出: `verify_consistency` 报告（all_passed/errors）

## 职责

部署前**必须**通过一致性校验。任何一个 check 失败 = 拒绝部署。本阶段是纯 query，
不产生新文件、不推进 stage。

## 执行序列

1. 主动列出 `<PROJECT_PATH>/manifests/` 下三个子目录（platform / pl / ps）的
   `sha256_*.json` 文件：找到的传全部，找不到的记为不存在（不假设、不照抄示例）。
2. 调用 `verify_consistency`：传三 Manifest 路径 + `board_profile_sha256`
   （来自板卡配置包，S1 记录）。**三类 Manifest 全部存在**是正式契约；任何目录
   0 个或多个当前候选都先停止并报告，不得用空字符串、省略参数或手工新建
   Manifest 来得到部分校验。
3. 只有 `all_passed == true`、`errors == []`、`summary.failed == 0` 且
   `summary.skipped == 0` 才能继续。

## 校验规则（通用契约，与具体外设无关）

| # | 规则 | 说明 |
|---|------|------|
| 1 | `pl_build.built_from_platform_revision == platform.platform_revision` | PL 产物必须与 Platform 版本一致 |
| 2 | `ps_build.built_from_platform_revision == platform.platform_revision` | PS 产物必须与 Platform 版本一致 |
| 3 | `ps_build.platform_xsa_sha256 == platform.xsa_sha256` | PS 用的 XSA 必须是 Platform 产出的那个 |
| 4 | `ps_build.xparameters_addrs == platform.address_map` | 地址映射逐字段一致 |
| 5 | `ps_build.board_profile_sha256 == board_profile_sha256` | PS 板卡配置一致 |
| 6 | `pl_build.board_profile_sha256 == board_profile_sha256` | PL 板卡配置一致 |
| 7 | 所有 artifact 文件存在 + SHA256 匹配 | 文件完整性和完整性 |

## 智能体自主决策范围

- 执行校验、定位不匹配域（判断哪个域需要重跑）。

## 用户必须提供的物理事实

- 无。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| `failed` 非空 | 列出所有失败规则；最常见是产物文件被移动/修改；重查文件路径 |
| Manifest 不存在 | 对应域未满足终态门禁；从该域的公开 MCP 操作重跑，禁止手工发布 |
| board_profile 不匹配 | 确认 `<BOARD_ID>` 与板卡配置包一致，profile 未被篡改 |

## 涉及的工具类别

- `verify_consistency`（纯 query）。
