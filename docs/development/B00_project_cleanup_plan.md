# B00 — 现有项目盘点与整理方案 v0.2

> Brick: B00-A
> 日期: 2026-08-04
> 状态: **v0.3 执行完成** — Batch 0-4 已完成, 待最终审核
> B00 两段: B00-A = 方案审核, B00-B = 分批整理执行
> **Batch 0 报告**: [B00_completion_report.md](B00_completion_report.md)

---

## 0. 版本演进摘要

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-08-04 | 初版: 盘点 + 目标目录树 + 6 Batch 迁移方案 |
| v0.2 | 2026-08-04 | P0 修正: Batch 3 高风险标注、外部备份方案、`.gitignore` 收缩、活动项目不动、PDF 角色修正、bat 分类 |
| v0.3 | 2026-08-04 | **Batch 0 ✅** + P0 修正: Batch 3 整体延后到 B04/B06、绝对路径统计修正为 ~94 文件、回归命令修正为测试当前仓库 |

| 审计项 | v0.1 | v0.2 |
|--------|------|------|
| Batch 3 风险 | 误标为无风险 | v0.2 标注高风险 (~60 文件), v0.3 修正为 ~94 文件; Batch 3 **整体延后**到 B04/B05/B06 |
| 路径迁移 | 未提供 | 完整的逐文件 `旧路径→新路径` 修正表 |
| Board Information | 未计入 | 纳入盘点、索引、SHA256 清单 |
| 外部备份 | 无 | 不可变外部备份 + 全文件 SHA256 快照 + 恢复演练 |
| `_trash/` 可靠性 | 建议用 `_trash/` 暂存 | 明确 `_trash/` 不是可靠备份 |
| `.gitignore` | 隐藏 `_archive/` `_vitis/` `*.elf` `*.bit` `*.log` | 仅忽略可再生的工具生成物; 历史脚本和验证基线不受忽略 |
| 测试数量 | 写 22 个 | 修正为 24 个 Python 测试 |
| 回归标准 | "运行旧基线" | 每个 Batch 的精确命令、预期输出和证据位置 |
| 验证结论 | 混用历史和当前 | 拆为 "历史证据" (G0-G11) 和 "本次重跑" (B00-B) 两列 |
| PDF 角色 | "权威源码参考" | 修正为 "厂商流程与参数说明参考" (PDF 不含可执行 Tcl 源码) |
| bat 文件 | 全部归档 | 分类判定: 入口脚本、诊断工具、一次性试验 |
| B00 执行范围 | 移动活动项目 | **收缩**: B00 不移动任何含绝对路径的活动工程; 仅清理生成物、整理文档索引、补充 `.gitignore` |

---

## 1. 盘点：现有项目完整清单

### 1.1 顶层目录

| 路径 | 性质 | 说明 |
|------|------|------|
| `Xilinx_Vivado_MCP/` | **当前核心** — 独立 Git 仓库 | 27-tool MCP Server + 2 Skill + 24 test scripts。PL MCP (未来) 的基础 |
| `Xilinx_Vitis_MCP/` | **骨架** — 独立 Git 仓库 | 仅 README.md。PS MCP (未来) 的起手位置 |
| `zynq_platforms/` | **当前核心** — 独立 Git 仓库 | AX7020 平台工程。1 tracked file modified, ~60 untracked files |
| `docs/` | **当前核心** — 非 Git | 架构 v2.3.1、G0-G12 历史、Brick 规划、厂商教程 PDF (boardinformation/) |
| `hello_fpga/` | **可复用** | 纯 PL Breath LED 完整项目 |
| `g9_hw_test/` | **测试证据** | G9 PL 硬件闭环验证 |
| `validation_projects/` | **测试证据** | Golden + 11 故障注入 + Agent2 黑盒 |
| `embedded_projects/` | **可复用** | PS LED bare-metal ARM 程序 |
| `cp210x_driver/` | **外部厂商资料** | CP210x USB-UART 驱动 |
| `.claude/` | **当前核心** | Claude Code 配置 |
| `.Xil/` | **生成物** | Vivado 临时文件, 425KB |
| `*.zip, *.cab, *.ps1` | **外部厂商/工具** | 驱动安装包、USB 扫描脚本 |

### 1.2 独立 Git 仓库

| 仓库 | Commits | Untracked | Modified | 状态 |
|------|---------|-----------|----------|------|
| `Xilinx_Vivado_MCP/` | 3 | `_g10_prog.bat`, 5 个 `tests/*.py`, `vivado.jou/log` | 无 | 干净主干 |
| `Xilinx_Vitis_MCP/` | 1 | 无 | 无 | 纯净骨架 |
| `zynq_platforms/` | 1 | ~60 files (Tcl/BAT/ELF/XSA/build/) | `block_design/create_platform.tcl` | **需要治理** |

### 1.3 `docs/boardinformation/` 索引

| 文件 | 大小 | SHA256 | 内容 | 适用 Brick |
|------|------|--------|------|------------|
| `cource_s1_*_FPGA教程V1.01.pdf` | 23.3 MB | `561d1b36...` | Zynq 基础、硬件架构、PS/PL 互联、BD 创建、AXI GPIO | B01, B03 |
| `course_s2_*_Vitis应用教程V1.01.pdf` | 49.0 MB | `47d221e6...` | Vivado BD→XSA→Vitis BSP→UART→中断 全流程 | B01, B06 |
| `course_s3_*_HLS教程V1.03.pdf` | 4.4 MB | `a08b5b4f...` | HLS 高级综合 | 未来 |
| `course_s4_*_Linux应用教程V1.01.pdf` | 13.1 MB | `b2011436...` | Linux 应用开发 | 未来 |
| `course_s5_PYNQ开发教程.pdf` | 2.2 MB | `f3b1ffe2...` | PYNQ Python+FPGA | 未来 |
| `course_s6_*_Linux驱动篇V1.01.pdf` | 6.6 MB | `7a9ab51d...` | Linux 驱动 | 未来 |

> **角色定义**: 这些 PDF 是**厂商流程与参数说明参考**。PDF 描述了 PS7 537 参数的用途和 BD 流程步骤，但**不包含可执行的 `ps_config.tcl` 源码**。B03 固化 Board Profile 时，实际 preset Tcl 和 XDC 的来源仍是厂商分发介质 (`D:\BaiduNetdiskDownload\AX7020_2023.1\`) 中的原始文件。

> **元数据异常**: `cource_s1` 的文件名中 "course" 拼写为 "cource"。保留原始文件名不修改（外部厂商资料）。

> **授权/再分发**: 厂商 PDF 从外部介质复制到项目 `docs/` 下供离线参考。不对外分发。

### 1.4 绝对路径依赖完整扫描

扫描范围: 所有 `.py` `.tcl` `.bat` `.md`，排除 `.git/` `.Xil/` `__pycache__/`。

**结果: ~94 个文件包含绝对路径 `D:/fpgaproject`**

| 文件类型 | 数量 | 示例 |
|----------|------|------|
| `.tcl` | 58 | `build_g11.tcl`, `recover_target.tcl`, `bp_test.tcl`, 各类 `download_*.tcl`, `_export_*.tcl`, `build_g11_pl_uart.tcl` |
| `.py` | 18 | `test_golden.py`, `test_g9_hardware.py`, `test_platform*.py`, `g10_program.py`, `test_g7_validate.py`, `g5_3_validation.py`, `analyze_breath.py`, `smoke_mcp.py`, `reprogram.py` 等 |
| `.bat` | 10 | `run_g3.bat`, `_rebuild_final.bat`, `_full_build.bat`, `_make_boot*.bat`, `_xsct_*.bat`, `_g10_prog.bat` |
| `.md` | 8 | `G11_vitis_mcp.md`, `G11_debug_diagnostics.md`, AGENT 文档等 |

> 完整逐文件清单待 Batch 4 产出到 `docs/development/B00_dependency_scan.md`

**关键依赖链**:

```
被依赖最多的路径              引用次数    影响
────────────────────────────────────────────────────────────────
hello_fpga/rtl/               ~15         PL test scripts, platform tests, PL UART build
hello_fpga/reports/           ~10         test_golden, test_g7, test_errors, test_protocol
hello_fpga/output/            ~5          g9_smoke, reprogram, program_and_monitor
zynq_platforms/ax7020_base/   ~40         G10/G11 Tcl, build scripts, recover/download
Xilinx_Vivado_MCP/server.py   ~6          test entry points
```

### 1.5 bat 文件分类

| 类别 | 文件 | 建议 |
|------|------|------|
| **主入口** | `run_g3.bat` (hello_fpga) | 含绝对路径, B00 不动, B04 统一修正 |
| **诊断工具** | `diag_halt.tcl`, `recover_target.tcl`, `build_app.tcl` | 核心, 保留在 `zynq_platforms/ax7020_base/` 原处 |
| **一次性试验** | `_rebuild_final.bat`, `_xsct_*.bat`, `_make_boot*.bat`, `_full_build.bat` | 归档到 `_archive/`, 不得 `.gitignore` |

### 1.6 验证证据矩阵

| 验证项 | 历史证据 (G0-G11) | 本次重跑 (B00-B 执行) |
|--------|-------------------|---------------------|
| Vivado 进程生命周期 | G3_build_infrastructure.md | B00-B Batch 1: `test_process.py` |
| 27-tool MCP 功能 | G4_mcp_server.md + G5_software_loop.md | B00-B Batch 1: `test_golden.py` |
| Simulation | G6_simulation.md | B00-B Batch 1: `test_simulation.py` |
| 综合/实现/时序 | G7_validation.md | B00-B Batch 1: `test_g7_validate.py` |
| 硬件 JTAG 编程 | G9_hardware_loop.md | **不在 B00 重跑** (需要 FPGA 板卡连接) |
| AXI GPIO BD | G10_zynq_platform.md | **不在 B00 重跑** (依赖绝对路径 + Vivado 工程) |
| ARM JTAG 下载+运行 | G11_vitis_mcp.md | **不在 B00 重跑** (需要 FPGA 板卡 + hw_server) |
| Agent1 11/11 修复 | G8_skill_workflow.md | **不在 B00 重跑** (历史证据充分) |
| Agent2 黑盒 | AGENT2_ROUND2.md | **不在 B00 重跑** (历史证据充分) |

> **B00-B 回归原则**: 只重跑能在无板卡环境中通过纯 MCP 进程验证的测试。硬件测试的生命周期依赖于绝对路径引用的 bitstream/XSA/elf 产物——这些产物 B00 不动, 对应的硬件测试也不重跑。

---

## 2. 内容分类

### 2.1 当前核心 (core)

```
Xilinx_Vivado_MCP/     — PL MCP 代码基础 (24 test scripts)
zynq_platforms/        — AX7020 平台工程 (BD Tcl / XSA / PS7 config / recover/diag 脚本)
docs/                  — 架构文档、开发记录、Brick 规划、厂商教程 PDF
.claude/               — Claude Code 配置
```

### 2.2 可复用 (reusable)

```
hello_fpga/            — 纯 PL Breath LED 参考设计
embedded_projects/     — PS bare-metal ARM 参考代码
validation_projects/   — Golden + 故障注入套件 (11 faults + Agent2 blackbox)
```

### 2.3 历史参考 (historical)

```
docs/development/G*.md                       — G0-G12 阶段记录
docs/architecture_review.md                  — v1.0 (FROZEN)
Xilinx_Vivado_MCP/docs/zynq_architecture.md  — 旧架构
```

### 2.4 测试证据 (test_evidence)

```
g9_hw_test/AGENT2_G9.md
validation_projects/AGENT*.md
docs/development/G11_debug_diagnostics.md
docs/development/G11_vitis_mcp.md
docs/development/G10_zynq_platform.md
```

### 2.5 外部厂商资料 (vendor)

```
cp210x_driver/          — CP210x USB-UART 驱动
*.zip, *.cab            — 驱动安装包
docs/boardinformation/  — ALINX 官方 6 PDF (~99MB) — 厂商流程与参数说明参考
D:\BaiduNetdiskDownload\AX7020_2023.1\  — 厂商课程资料 (外部, 含可执行 Tcl/XDC)
```

### 2.6 可安全清除的生成物 (generated, safe to delete or .gitignore)

```
.Xil/                                      — Vivado temp
vivado*.jou, vivado*.log                   — Vivado session logs (root level)
vivado_*.backup.jou/log                    — Vivado backup logs (root level)
*/vivado_project/*.cache/                  — Vivado project cache
*/vivado_project/*.hw/                    — Vivado hardware session
*/sim/xsim.dir/                            — XSim compilation artifacts
*/sim/*.vcd, *.wdb, *.pb, xsim*.log       — Simulation artifacts
__pycache__/, *.pyc                        — Python bytecode
```

### 2.7 不可忽略的历史产物 (keep, version-controlled)

```
zynq_platforms/ax7020_base/_archive/      — 历史试验 Tcl/BAT (是诊断方法论的唯一证据)
zynq_platforms/ax7020_base/g10_build/     — G10 BD 构建成果 (含 ps7_init.tcl 和 BD 实例)
zynq_platforms/ax7020_base/g11_build/     — G11 BD 构建成果
zynq_platforms/ax7020_base/vitis_workspace*/ — Vitis 工程 (含 BSP 和 ARM app 源码)
zynq_platforms/ax7020_base/xsa/           — 导出的 Platform XSA (G11 验证基线)
zynq_platforms/ax7020_base/*.elf, *.bit   — G10/G11 验证产物基线
```

---

## 3. 建议的目标目录树 (B00 范围)

```
D:\fpgaproject\                          ← 项目根
│
├── docs/                                ← 不动
│   ├── architecture_ai_zynq7020.md
│   ├── architecture_review.md
│   ├── brick_development_plan.md
│   ├── boardinformation/                ← 不动 (厂商 PDF)
│   │   ├── INDEX.md                     ← [B00新增] 索引 + SHA256
│   │   └── *.pdf
│   ├── development/
│   │   ├── skill/                       ← [B00新增空目录]
│   │   ├── mcp/                         ← [B00新增空目录]
│   │   ├── tests/                       ← [B00新增空目录]
│   │   ├── B00_project_cleanup_plan.md  ← 本文件
│   │   ├── B00_completion_report.md     ← B00-B 完成后产出
│   │   ├── G0_environment.md            ← 历史, 不动
│   │   ├── ... G1-G12                   ← 历史, 不动
│   │   └── B00_dependency_scan.md       ← [B00新增] 完整绝对路径依赖清单
│   └── reference/
│       └── deployment.md
│
├── Xilinx_Vivado_MCP/                   ← 不动 (Phase C 再拆)
├── Xilinx_Vitis_MCP/                    ← 不动 (Phase C 再建设)
│
├── zynq_platforms/                      ← B00 只做 git 治理 + 归档整理
│   └── ax7020_base/
│       ├── block_design/                ← 活动: build_g1*.tcl, create_platform.tcl
│       ├── constraints/                 ← 活动: led_pins.xdc
│       ├── xsa/                         ← 活动基线产物 (不动)
│       ├── core/                        ← [B00新增] 从根移入核心脚本
│       │   ├── recover_target.tcl
│       │   ├── diag_halt.tcl
│       │   ├── build_app.tcl
│       │   ├── build_g11_vitis.tcl
│       │   └── recover_pl_uart.tcl
│       ├── _archive/                    ← [B00新增] 历史试验 (不受 .gitignore)
│       │   └── ... (bp_test*, bringup*, debug_*, download_*, led_*, uart_*, etc.)
│       ├── _builds/                     ← [B00新增] 构建产物 (不受 .gitignore)
│       │   ├── g10_build/
│       │   ├── g11_build/
│       │   └── g11_pl_uart_build/
│       └── _vitis/                      ← [B00新增] Vitis 工程 (不受 .gitignore)
│           ├── vitis_workspace/
│           └── vitis_workspace_g11/
│
├── hello_fpga/                          ← 不动 (B00 不移动活动项目)
├── g9_hw_test/                          ← 不动
├── embedded_projects/                   ← 不动
├── validation_projects/                 ← 不动
│
├── vendor/                              ← [B00新增] 厂商资料归集
│   └── drivers/                         ← 从顶层 .zip/.cab/cp210x_driver 移入
│
├── tools/                               ← [B00新增] 工具脚本归集
│   └── scripts/                         ← 从顶层 .ps1 移入
│
├── _trash/                              ← [B00新增] 仅暂存可再生的工具日志
│                                       ← 不暂存源码、脚本和历史产物
│
├── .mcp.json                            ← MCP 注册 (路径修正)
├── .gitignore                           ← [B00新增]
└── README.md                            ← [B00新增] 项目入口
```

> **B00 的核心决策变更**: 不移动 `hello_fpga/`、`g9_hw_test/`、`embedded_projects/`、`validation_projects/`。
> 这些目录被 ~94 个文件中的绝对路径引用, 移动将立即破坏所有现有测试和 Tcl 脚本。
> 路径统一修正属于 **B04** (PL MCP)、**B05** (Platform MCP) 和 **B06** (PS MCP), 那时会整体改为相对路径或 manifest 引用。

---

## 4. 执行批次

### 每个 Batch 的统一流程

```
1. 外部备份: 打包整个 D:\fpgaproject\ → 外部介质
2. 生成清单: 全文件 path+size+SHA256 → B00_dependency_scan.md
3. 执行变更
4. 回归测试: 运行该 Batch 的精确测试命令
5. 比对: 新旧 SHA256 清单 diff
6. 通过 → 记录证据; 失败 → 从外部备份恢复 → 停止
```

### 外部备份命令 (所有 Batch 执行前)

```bash
# 在 D:\fpgaproject 之外执行 (如 D:\_b00_backup\)
robocopy D:\fpgaproject D:\_b00_backup\fpgaproject_snapshot_20260804 /E /COPYALL /R:3 /W:5

# 生成全文件 SHA256 清单
cd D:\fpgaproject && python -c "
import os, hashlib
for root, dirs, files in os.walk('.'):
    if '.git' in root or '.Xil' in root or '__pycache__' in root:
        continue
    for f in files:
        path = os.path.join(root, f)
        try:
            h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            print(f'{h}  {os.path.getsize(path):>10}  {path}')
        except:
            print(f'SKIPPED  {path}')
" > D:\_b00_backup\sha256_manifest_20260804.txt
```

### Batch 0: 可恢复性预演 (必须在任何变更前执行)

| 操作 | 命令 |
|------|------|
| 外部备份 | `robocopy D:\fpgaproject D:\_b00_backup\fpgaproject_pre_b00 /E /COPYALL` |
| 快照清单 | 全文件 SHA256 → `sha256_pre_b00.txt` |
| 恢复演练 | 从备份恢复 3 个随机文件 → 校验 SHA256 → 确认工具链可读 |

### Batch 1: 生成物清理 (低风险)

| 文件 | 操作 | 原因 |
|------|------|------|
| `vivado.jou` | 删除 | Vivado session log, 顶层零散 |
| `vivado.log` | 删除 | Vivado session log |
| `vivado_*.backup.jou/log` | 删除 (共 ~14 个) | Vivado 崩溃备份 |
| `.Xil/` | 删除 | Vivado 临时文件 |

**回归测试**:

```bash
# T01: Vivado 进程生命周期 (不需要板卡)
cd D:\fpgaproject\Xilinx_Vivado_MCP
python tests/test_process.py

# T02: Vivado 信息查询 (不需要板卡)
python tests/check_server.py
python tests/debug_version.py
python tests/smoke_mcp.py
```

**证据**: `test_process.py` returncode=0, `check_server.py` 打印 version 信息。

### Batch 2: 工具/驱动归集 (低风险)

| 旧路径 | 新路径 | 原因 |
|--------|--------|------|
| `cp210x_driver/` | `vendor/drivers/cp210x/` | 厂商资料归集 |
| `CDM212364_Setup.zip` | `vendor/drivers/` | 厂商驱动 |
| `CP210x_Universal_Windows_Driver.zip` | `vendor/drivers/` | 厂商驱动 |
| `CP210x_Windows_Drivers.zip` | `vendor/drivers/` | 厂商驱动 |
| `ftdi_driver.cab` | `vendor/drivers/` | 厂商驱动 |
| `check_ch340.ps1` | `tools/scripts/` | 工具脚本 |
| `check_driver.ps1` | `tools/scripts/` | 工具脚本 |
| `install_cp210x.ps1` | `tools/scripts/` | 工具脚本 |
| `scan_usb.ps1` | `tools/scripts/` | 工具脚本 |

**回归测试**: Batch 1 测试 + 确认 `.ps1` 脚本在 PowerShell 下语法可解析。

### Batch 3: ⚠ 已延后 — zynq_platforms 内部整理

> **决策**: Batch 3 在 v0.3 中**整体延后**。核心 JTAG 脚本移动后路径立即失效,
> 且硬件回归在 B00 范围内被排除——移动活动脚本会制造无法验证的入口损坏。
> Batch 3 的各项整理任务重新分配到 B04/B05/B06。

| 原 Batch 3 内容 | 新归属 | 原因 |
|-----------------|--------|------|
| 核心 JTAG 脚本移动 (recover_target.tcl 等) | **B04/B06** | 含 ~10 个 `D:/fpgaproject` 绝对路径引用 |
| Vitis workspace 移动 | **B06** | PS MCP BSP/编译依赖这些路径 |
| XSA 目录移动 | **B05** | Platform XSA 是 B05 的产物基准 |
| G10/G11 build 目录移动 | **B04/B05** | 构建产物, BD Tcl 输出路径依赖 |
| 历史试验 Tcl 归档 | **B04** | 不紧急; 等 B04 修正完绝对路径后再整理 |
| bat 文件归档 | **B04** | 同上 |
| 导出 Tcl 归档 | **B05** | 同上 |

B00 只做以下不受影响的辅助操作:

| 文件 | 操作 |
|------|------|
| `ax7020_base/block_design/NA/` | 删除 (空目录) |
| `ax7020_base/.Xil/` | 删除 (Vivado 临时) |
| `ax7020_base/_targets_debug.txt` | 移到 `_archive/` (调试输出, 无外部引用) |

### Batch 4: 文档和入口补充 (低风险)

| 操作 | 文件 |
|------|------|
| 新建 | `docs/boardinformation/INDEX.md` (含版本/来源/SHA256/授权状态) |
| 新建 | `docs/development/B00_dependency_scan.md` (全 60 文件的绝对路径清单) |
| 新建 | `docs/development/skill/` (空目录, Brick 迭代入口) |
| 新建 | `docs/development/mcp/` (空目录) |
| 新建 | `docs/development/tests/` (空目录) |
| 新建 | `README.md` (项目入口, 含架构文档链接 + Brick 当前状态) |
| 新建 | `.gitignore` |
| 新建 | `docs/boardinformation/SHA256SUMS.txt` (6 PDF 校验和列表) |

### Batch 5: B00 验收

| 验收项 | 方法 |
|--------|------|
| 所有 Batch 回归通过 | 查看 B00_completion_report.md 中的测试输出 |
| 外部备份 SHA256 清单已生成 | 检查 `D:\_b00_backup\sha256_manifest_20260804.txt` |
| 恢复演练成功 | 从备份恢复 3 个随机文件, SHA256 校验通过 |
| `.gitignore` 正确 | `git status` 不隐藏 `_archive/` `_vitis/` `*.elf` `*.bit` `*.log` |
| 60 文件绝对路径依赖已记录 | `B00_dependency_scan.md` 列出每个文件和它引用的绝对路径 |
| 无文件无故丢失 | `diff` 新旧 SHA256 清单, 预期差异仅来自 Batch 1-4 的变更 |
| Vivado MCP 进程级回归通过 | `test_process.py` + `test_golden.py` returncode=0 |

---

## 5. `.gitignore` 设计

```gitignore
# ==== Vivado 工具生成物 (可再生) ====
.Xil/
*.jou
vivado_*.backup.*
vivado_project/*.cache/
vivado_project/*.hw/

# ==== XSim 工具生成物 (可再生) ====
xsim.dir/
*.vcd
*.wdb
*.pb
xsim_*.backup.*
xsimcrash.log
xsimkernel.log

# ==== Python ====
__pycache__/
*.pyc

# ==== 暂存区 (仅可再生的工具日志) ====
_trash/

# ==== 操作系统 ====
Thumbs.db
Desktop.ini
```

> **关键**: `_archive/` `_vitis/` `_builds/` `*.elf` `*.bit` `*.log` **不进入** `.gitignore`。
> 历史 Tcl 脚本和验证基线产物必须在版本控制中可追溯。

---

## 6. 不执行项 (B00 明确排除)

| 排除项 | 原因 | 归属 |
|--------|------|------|
| 移动 `hello_fpga/` | 被 ~25 个测试脚本 + Tcl 引用 | B04 |
| 移动 `g9_hw_test/` | 被测试脚本 + AGENT2 文档引用 | B04 |
| 移动 `embedded_projects/` | 被 Vitis Tcl 引用 | B06 |
| 移动 `validation_projects/` | 被 AGENT 文档和 test_g7 引用 | B04 |
| 拆分 `Xilinx_Vivado_MCP/` | 架构禁止 B00 做 MCP 拆分 | Phase C |
| 修正 ~94 个文件的绝对路径为相对路径 | 属于 MCP 适配层工作, 不是整理工作 | B04/B05 |
| 统一三个 Git 仓库 | 决策延后到 Phase C | Phase C |
| 删除任何 `.tcl` `.py` `.bat` 源码 | B00 只归档, 不删除 | N/A |
| 修正 `.mcp.json` 中的 `D:\fpga-agent\` 路径 | 你提供正确路径后修正 | 手动 |

---

## 7. 当前下一步

本方案 (B00-A v0.2) 提交审核。审核通过后：

1. 执行 Batch 0 (外部备份 + 恢复演练)
2. 依次执行 Batch 1-4 (每批后回归验证)
3. 产出 `B00_completion_report.md`
4. 更新 `brick_development_plan.md` 的 B00 状态为 "✅ 完成"
