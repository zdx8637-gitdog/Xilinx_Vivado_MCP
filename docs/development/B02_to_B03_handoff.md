# B02 → B03 Context Handoff

> 日期: 2026-08-04
> 用途: 会话压缩后恢复工作上下文的最小入口
> 原则: 不复制，只索引。不超过 300 行。

---

## 快速恢复区（< 30 行）

**项目目标**：让 AI Agent 通过统一 Skill 和 MCP 完成 Zynq-7020（XC7Z020CLG400-2, ALINX AX7020）标准开发流程。最终目标：一个新 Agent 从需求描述和板卡资料出发，黑盒复现 GPIO LED 全链路。

**用户角色**：不要求 Zynq 经验；要求流程标准化、砖块化、Agent2 可复现。
**Agent1**：负责实现和测试。
**Codex**：负责审核，不写代码。

**当前进度**：
- B00 ✅ 项目整理
- B01 ✅ 标准流程 + GPIO 验收规范
- B02 ✅ MCP 公共契约 + 三个空框架（234 passed, 1 skipped）
- B03 ⬜ 只允许规划

**三个 MCP 现状**：各有 5 公共控制 API，0 领域 API。Platform: 12 planned, PL: 12 planned, PS: 19 planned。总计 43 领域 API 排队在 B04/B05/B06。

**下一动作**：提交 B03 规划文档审核。禁止直接实现。

**禁止**：推翻 B01/B02 冻结结论，除非发现可验证的缺陷。

---

## 冻结决定索引

| 决定 | 链接 |
|------|------|
| PS / Interconnect / PL 三责任域 | [architecture_ai_zynq7020.md](../architecture_ai_zynq7020.md) §1.3 |
| Skill = 工作流+知识, MCP = 原子工具 | [同上](../architecture_ai_zynq7020.md) §2 |
| Platform Skill/PL Skill/PS Skill + Workflow 跨域编排 | [同上](../architecture_ai_zynq7020.md) §3-4 |
| Board Profile + JTAG Lock = 共享库, 不是第四 MCP | [同上](../architecture_ai_zynq7020.md) §4 |
| PL MCP 在 B04 适配旧 Vivado MCP, 不重写 Vivado/XSim | [B02_common_contract_plan.md](mcp/B02_common_contract_plan.md) §2 |
| Artifact Contract: revision, immutable manifest, stale 拒绝 | [同上](mcp/B02_common_contract_plan.md) §5 |
| Project Lock + JTAG Lock (Windows LockFileEx, posix fcntl.flock) | [同上](mcp/B02_common_contract_plan.md) §6 |
| JTAG-only 是当前配置, 不是架构不可变原则 | [architecture_ai_zynq7020.md](../architecture_ai_zynq7020.md) P7 |

---

## B02 可恢复基线

| 项 | 值 |
|------|------|
| 最终结果 | 235 collected / 234 passed / 1 skipped ([详细](mcp/B02_completion_report.md)) |
| Python | `C:\Users\zdx86\AppData\Local\Programs\Python\Python312\python.EXE` |
| MCP SDK | `mcp==1.28.1` |
| 测试入口 | `cd D:\fpgaproject && python -m pytest mcps/ -q` |
| 跳过的测试 | `test_posix_link_no_overwrite` — `os.link` 在 Windows 不可用 (POSIX-only) |
| 测试文件 | 14 文件, 234 test cases (详见完成报告) |

### .mcp.json 注册

```json
{
  "mcpServers": {
    "vivado":      { "command": "D:\\...\\python.exe", "args": ["...server.py", "--log-level", "WARNING"] },
    "zynq_platform": { "command": "python", "args": ["-m", "mcps.platform_mcp.server"] },
    "zynq_pl":       { "command": "python", "args": ["-m", "mcps.pl_mcp.server"] },
    "zynq_ps":       { "command": "python", "args": ["-m", "mcps.ps_mcp.server"] }
  }
}
```

### B01 冻结 SHA256

```
65080485...  docs/development/skill/B01_standard_zynq_flow.md
8cefa1e7...  docs/development/tests/B01_gpio_acceptance_spec.md
```

### 旧 Vivado MCP 冻结 SHA256

```
9fa66a0c...  Xilinx_Vivado_MCP/server.py
c7583ce7...  Xilinx_Vivado_MCP/models.py
59f9f112...  Xilinx_Vivado_MCP/requirements.txt
```

---

## 关键文件导航

恢复时读以下文件即可重建上下文，不需要从头搜索。

| 文件 | 为什么读 |
|------|---------|
| [docs/architecture_ai_zynq7020.md](../architecture_ai_zynq7020.md) | 顶层架构 P1–P8，三域四层模型 |
| [docs/brick_development_plan.md](../brick_development_plan.md) | 当前 Brick 状态和门禁规则 |
| [docs/development/skill/B01_standard_zynq_flow.md](skill/B01_standard_zynq_flow.md) | 标准 7 阶段 Zynq 流程，43 最小 API 定义 |
| [docs/development/tests/B01_gpio_acceptance_spec.md](tests/B01_gpio_acceptance_spec.md) | T00/T01/T02 验收标准 + 6 故障注入 |
| [docs/development/mcp/B02_common_contract_plan.md](mcp/B02_common_contract_plan.md) | B02 完整设计：契约、Schema、锁、35 文件清单 |
| [docs/development/tests/B02_contract_test_plan.md](tests/B02_contract_test_plan.md) | 14 测试文件计划和 MCP SDK 验证 |
| [docs/development/mcp/B02_completion_report.md](mcp/B02_completion_report.md) | B02 最终结果、限制、B03 进入条件 |
| `mcps/common/` | B02 冻结产物：ToolResponse、Context、Lock、Artifact 等 |
| `mcps/*/server.py` | 三个 MCP skeleton（0 领域 API） |
| `.mcp.json` | MCP 注册配置 |
| `Xilinx_Vivado_MCP/` | PL MCP 的进程层基础（B04 适配） |
| `docs/boardinformation/` | ALINX 官方 6 份 PDF 教程（~99MB） |

---

## 板卡和资料现状

### 已知基线（B03 需重新校验）

| 参数 | 旧文档记录值 | B03 动作 |
|------|------------|---------|
| 开发板 | ALINX AX7020 / Zynq-7020 XC7Z020CLG400-2 | 确认 |
| 厂商资料 | `D:\BaiduNetdiskDownload\AX7020_2023.1\` | 提取关键 Tcl/XDC + SHA256 |
| 项目内教程 | `docs/boardinformation/` (6 PDF, ~99MB) | 离线参考 |
| DDR 芯片 | `MT41J256M16 RE-125` (厂商 Tcl `PCW_UIPARAM_DDR_PARTNO`) | 校验 |
| DDR physical | 1 GB (2×4Gbit) | 校验 |
| DDR configured | 512 MB (`HIGHADDR=0x1FFFFFFF`) | 校验 |
| QSPI physical | 256 Mbit (W25Q256) | 校验 |
| QSPI 线性窗口 | 16 MB (`0xFC000000-0xFCFFFFFF`) | 校验 |
| PL LED | 4 (active-low) | 校验 |
| PS LED | 2 | 校验 |
| PL oscillator | 50 MHz | 校验 |
| PS clock | 33.333 MHz | 校验 |

> **警告**: 以上参数当前来源于多份旧文档和厂商 Tcl。B03 必须在 `board_profile.json` 中对每个参数标注来源文件和 SHA256，不能只靠文档描述。

---

## B03 严格范围

**目标**：让板卡参数只有一个受校验的数据源，并建立开发环境预检基线。

**交付**：AX7020 Board Configuration Package (board_profile.json, PS7 preset Tcl, XDC + SHA256 + 来源), Vivado/Vitis/XSCT 环境探测, 器件型号/JTAG cable/UART 识别, DDR/QSPI/LED/时钟参数校验, 配置漂移和错误 profile 的拒绝测试。

**门禁**：新会话仅凭 Board Configuration Package 完成环境预检；配置被修改后检测漂移并拒绝；不依赖手工记忆；不包含不必要绝对路径。

**B03 第一动作**：编写并提交规划文档审核，建议文件：
- `docs/development/mcp/B03_board_environment_plan.md`
- `docs/development/tests/B03_board_environment_test_plan.md`

未经审核不得实现。

---

## 遗留限制

- 43 领域 API 未实现 (B04/B05/B06)
- 三个 MCP 为 skeleton，0 domain APIs
- B02 未调用 Vivado、Vitis、JTAG 或板卡
- 真实 Board Profile 未建立 (B03)
- PL 适配未做 (B04)
- Platform/AXI MCP 未做 (B05)
- PS/ARM/UART MCP 未做 (B06)
- 统一 Skill + GPIO Workflow 未做 (B07)
- Agent2 黑盒验收未做 (B09)
- `.mcp.json` 使用 `python`，依赖 PATH
- 旧项目 ~94 文件含绝对路径，按域分派到 B04/B05/B06 处理

---

## 工作区状态（只读记录，未修改）

| 仓库 | HEAD | tracked modified | untracked |
|------|------|:-:|:-:|
| 根 `D:\fpgaproject` | **非 Git 仓库** | — | — |
| `Xilinx_Vivado_MCP` | `59f2abb` | 0 | 8 |
| `Xilinx_Vitis_MCP` | `c334866` | 0 | 0 |
| `zynq_platforms` | `2f24976` | 1 (`create_platform.tcl` CRLF) | 3610 |

---

## 建议压缩后首先读取的 3 个文件

1. `docs/development/B02_to_B03_handoff.md` — 本文件（恢复上下文入口）
2. `docs/brick_development_plan.md` — 当前 Brick 状态 + 下一步
3. `docs/development/mcp/B02_completion_report.md` — B02 可恢复基线证据
