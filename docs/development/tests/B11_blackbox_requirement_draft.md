# B11 黑盒考题需求草案：6-LED 交替控制项目（DRAFT）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 17:19 +08:00）
> 状态：**DRAFT — 待用户审核。本文档是递给黑盒智能体的项目需求（考题），只描述「要什么」与板卡物理事实，不写实现路线；不代表 B11 已立项。**
> 配套：`docs/development/mcp/B11_plan.md`（B11 规划，阶段③–⑥执行本考题）、`docs/development/mcp/B11_platform_generate_erratum_draft.md`。
> 板卡物理事实出处：`boards/ALINX_AX7020_v1.0/README.md`、`boards/ALINX_AX7020_v1.0/board_profile_ALINX_AX7020_v1.0.json`。

## 1. 项目目标（一句话）

构建一个 Zynq-7020 项目：**控制板卡全部 6 个 LED（4 个 PL LED + 2 个 PS LED）按定义模式交替亮灭，并经 UART 输出机器可判定的 PASS/FAIL 证据（含每个 LED 的读回）**。

## 2. 板卡物理事实（用户提供 / 板卡包确认，智能体不得臆造）

| 项 | 事实 | 出处 |
|---|---|---|
| PL LED × 4 | 引脚 J16（LED3）、K16（LED2）、M15（LED1）、M14（LED0）；**active-low：写 0 = 亮，写 1 = 灭** | `boards/ALINX_AX7020_v1.0/README.md` §LEDs；`board_profile_*.json` `pl_leds` |
| PS LED × 2 | MIO13（LED1）、MIO0（LED0）；**active-low：MIO 低 = 亮** | 同上 `ps_leds` |
| UART | UART1，MIO48（TX）/ MIO49（RX），默认波特率 115200 | 同上 §UART / `uart` |
| USB-UART 桥片 | CP2102-GM（VID/PID 0x10C4 / 0xEA60） | `board_profile_*.json` `usb_bridge` |
| 器件 | XC7Z020-2CLG400I（Vivado part `xc7z020clg400-2`），board_id `ALINX_AX7020_v1.0` | README §Board Identification |

## 3. 目标行为（要什么）

### 3.1 LED 模式定义（6-bit，位序固定）

- 6 个 LED 组织为 6-bit 状态，**位序（最高位 → 最低位）固定为**：`[PL3 PL2 PL1 PL0 PS1 PS0]`（PL 引脚序号见 §2，PS 以 MIO 序号为准）。
- **模式 A**：位串 `101010`（hex `0x2A`）→ 按 active-low 语义，点亮 **PL2、PL0、PS0**，其余熄灭。
- **模式 B**：位串 `010101`（hex `0x15`）→ 点亮 **PL3、PL1、PS1**，其余熄灭。
- 行为：模式 A 与模式 B **交替**，每个模式保持约 1 秒（可观察的闪烁节奏），循环不少于 **8 轮**（A→B 计 1 轮；即模式 A、B 各出现 ≥ 8 次）。

### 3.2 读回要求（每个 LED 的证据）

- **每个 LED 必须提供读回证据**：每轮输出当前模式的实际写入值与读回值。
- 读回语义：PL 侧 LED 经 PL 地址空间读回（如 GPIO 数据寄存器）；PS 侧 LED 经 MIO GPIO 数据寄存器读回。读回值必须与写入值**逐位一致**（含 active-low 语义——写入 0 的 LED 读回必须为 0，写入 1 的读回必须为 1）。
- UART 每轮输出格式（必须可机器解析）：`WROTE:0x%X READ:0x%X`（大写 hex，值含全部 6 位；例：`WROTE:0x2A READ:0x2A`）。此格式固定，实现不得更改字段名。

### 3.3 PASS / FAIL 判据（机读）

| 条件 | 输出 | 判定 |
|---|---|---|
| 8 轮全部通过（每轮 6 位写入 == 读回） | `LED_E2E_PASS` | PASS |
| 任一轮任一 LED 读回与写入不一致 | `LED_E2E_FAIL`（随后停止） | FAIL |
| UART 无输出 / 超时 / 无完整 marker | — | TIMEOUT / INCOMPLETE（观测端判定） |

- 输出 marker 为固定 token：`LED_E2E_PASS`、`LED_E2E_FAIL`；首行启动 banner 建议形如 `=== AX7020 LED B11 ===`（非强制）。
- 超时建议：程序启动后 60 秒内未收到 PASS/FAIL marker 判 TIMEOUT（观测端）。

## 4. 明确不写实现路线（智能体自主选型）

- **PL 侧实现路线不限**：AXI GPIO（含其地址分配）或 EMIO（经 PS7 配置 + PL 顶层端口）均可，由智能体按框架决策（S3 架构选型）自主选择；本需求不指定 IP、不指定地址、不指定引脚之外的任何工程细节。
- **PS 侧**：自然走 MIO GPIO（PS LED 为 MIO 外设），实现细节由智能体决定。
- 约束仅两条：① 必须满足 §2 物理事实（引脚、极性、波特率）；② 必须满足 §3 行为与判据（模式、读回、marker、轮数）。
- 智能体交付：Platform/PL/PS 三 Manifest + bitstream + ELF + UART 捕获证据（经公开 MCP 自动发布，不得手工制造）。

## 5. 需求自检（机器可判定性）

| 检查 | 方法 |
|---|---|
| LED 行为可观察 | 真板：LED 亮灭模式与 §3.1 一致（用户/录像确认）；无板：仿真波形证据 + UART 轨迹 |
| 读回证据 | UART 每轮 `WROTE/READ` 对逐位一致（脚本可核对） |
| PASS/FAIL 机读 | `LED_E2E_PASS` / `LED_E2E_FAIL` token 由观测端自动判定（`evaluate_observation` 显式传 marker） |
| 完整性 | 三 Manifest + board profile 一致性（`verify_consistency`） |

## 6. 与 B09 考题的区别（对照说明）

- B09/O7 考题：4 个 PL LED（AXI GPIO，4-bit），Skill 为 GPIO 配方。
- B11 考题：**6 个 LED 一起控制（4 PL + 2 PS）**，PL 路线不限定；Skill 为**零 GPIO 字样的通用框架**——本需求文档是黑盒智能体唯一拿到项目事实的入口。
- 判据风格沿用 B09 硬件门禁：LED 可观察 + UART 机读 + 读回证据 + 三 Manifest 一致性。

## 7. DRAFT 声明

- 本文档为 **DRAFT**，待用户审核；不写 FROZEN/COMPLETE，不声称已立项。
- 仅描述需求与板卡物理事实；不涉及实现路线、不引用任何 Skill/MCP 内部细节。
- 未修改任何代码、测试、skill、boards、冻结文档；板卡事实均引用 `boards/ALINX_AX7020_v1.0/`。
