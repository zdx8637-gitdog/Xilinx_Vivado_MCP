一、最终目标架构

我建议最终做成：

                         Claude Code
                              │
                    ┌─────────┴─────────┐
                    │                   │
              FPGA Skill             MCP
                    │                   │
                    │          ┌────────┴────────┐
                    │          │                 │
                    │      VivadoMCP        Documentation
                    │          │
                    ▼          ▼
              工程规则      Vivado 2023.x
                              │
                    ┌─────────┼──────────┐
                    │         │          │
                 Synthesis  Impl.     Timing
                    │         │          │
                    └─────────┼──────────┘
                              │
                         Bitstream
                              │
                              ▼
                         Vitis 2023.x
                              │
                         HLS / PS / AIE

这里：

Claude Code：Agent / 大脑
SKILL.md：FPGA 工程流程和约束
VivadoMCP：操作 Vivado 的手
Vitis CLI：操作 Vitis 的手
Vivado/Vitis 2023：实际 EDA 后端

Vitis 2023 Unified IDE 本身就支持 CLI 和脚本化操作，而且官方明确说明它与 Vivado 生成的硬件设计协同工作。

二、第一步：先确认你的准确版本

这一步不要跳。

你说“Vitis 2023”，但我们必须知道是：

2023.1
2023.2

因为：

Vitis 2023.1
Vivado 2023.1

和：

Vitis 2023.2
Vivado 2023.2

不能混着假设。

Check 1：Vitis

打开你现在使用 Vitis 的终端：

vitis -version

如果这个命令不可用，先找到安装目录。

Check 2：Vivado

非常重要：

vivado -version

应该得到类似：

Vivado v2023.2
我们要求：
Vitis 2023.2
Vivado 2023.2

或者：

Vitis 2023.1
Vivado 2023.1

优先要求同版本。

不要出现：

Vitis 2023.2
Vivado 2023.1

然后拿这个环境做 Agent。

三、如果 vivado / vitis 找不到

Windows 下不要急着改系统 PATH。

先找到 AMD 安装目录。

典型结构类似：

C:\Xilinx\Vitis\2023.2\

或者：

C:\Xilinx\Vivado\2023.2\

里面应该存在：

bin\

然后检查：

Test-Path "C:\Xilinx\Vivado\2023.2\bin\vivado.bat"
Test-Path "C:\Xilinx\Vitis\2023.2\bin\vitis.bat"

如果返回：

True
True

说明安装本体没问题。

四、建立一个“FPGA Agent 专用终端”

我建议不要直接把 Vivado 永久塞进 Windows 全局 PATH。

因为以后非常容易出现：

Vivado 2023.2
Vivado 2025.1
Vivado 2026.1

互相污染。

建议建立：

D:\fpga-agent\

例如：

D:\fpga-agent\
├── env\
├── mcp\
├── skills\
├── projects\
└── scripts\

然后建立一个：

D:\fpga-agent\scripts\enter_fpga_env.bat

里面专门加载你的 AMD 环境。

例如：

@echo off

call C:\Xilinx\Vitis\2023.2\settings64.bat

echo.
echo ===== FPGA Agent Environment =====
echo Vitis:
vitis -version
echo.
echo Vivado:
vivado -version
echo.
echo Python:
python --version
echo.
echo Node:
node --version
echo.

然后：

D:\fpga-agent\scripts\enter_fpga_env.bat
Check 3

最终应该一次性看到：

Vitis 2023.2
Vivado 2023.2
Python 3.x
Node xx.x

如果这里版本不对，后面全部停止。

五、第二层：先完全绕开 Claude Code，验证 Vivado

这是整个部署最重要的一步。

AMD 官方 2023.2 文档明确支持：

vivado -mode batch -source xxx.tcl

Windows Command Prompt 也支持这种方式。

所以先创建：

D:\fpga-agent\scripts\test_vivado.tcl

内容：

puts "======================================"
puts " FPGA AGENT VIVADO TEST"
puts "======================================"

puts "Vivado version:"
version

puts "Part:"
puts [get_property PART [current_project]]

puts "Vivado TCL test PASSED"
exit

不过这个脚本没有 project 时，current_project 会有问题。

所以第一轮只测试：

puts "======================================"
puts " FPGA AGENT VIVADO TEST"
puts "======================================"

puts "Vivado version:"
version

puts "Tcl test PASSED"

exit

运行：

vivado -mode batch -source D:\fpga-agent\scripts\test_vivado.tcl
Check 4

必须看到：

Vivado version:
Vivado v2023.2
...
Tcl test PASSED

到这里说明：

Claude Code ❌
MCP ❌
Python ❌

Vivado 2023 → Tcl

已经打通。

这是第一个里程碑。

六、第三层：验证真实工程

然后不要马上 MCP。

拿一个你已经能够正常编译的最小 FPGA 工程。

例如：

D:\fpga-agent\projects\hello_fpga\

里面：

hello_fpga/
├── rtl/
│   └── top.v
├── constraints/
│   └── top.xdc
└── scripts/
    └── build.tcl

然后：

vivado -mode batch -source scripts/build.tcl

要求：

RTL
 ↓
Synthesis
 ↓
Implementation
 ↓
Timing
 ↓
Bitstream

全部能够完成。

七、这里建立 Agent 的“黄金基线”

这一点非常重要。

在允许 Agent 修改任何东西之前，保存：

baseline/
├── utilization.rpt
├── timing_summary.rpt
├── power.rpt
├── vivado.log
├── vivado.jou
└── baseline.dcp

并记录：

Vivado version
Part
Clock frequency
WNS
TNS
LUT
FF
BRAM
DSP

例如：

Vivado: 2023.2
Part: xc7z020clg400-2

Clock: 100 MHz

WNS: +1.237 ns
TNS: 0
LUT: 1234
FF: 2456
BRAM: 12
DSP: 4

以后 Claude Code 每次改代码，都必须回答：

WNS 有没有变好？
资源有没有恶化？
功能有没有破坏？

否则 Agent 很容易出现：

timing 变好了，但功能坏了。

八、第四层：安装官方 VivadoMCP

现在才进入 MCP。

AMD/Xilinx 官方目前公开的：

Xilinx/fpl26_optimization_contest

里面确实包含：

VivadoMCP/
├── vivado_mcp_server.py
├── requirements.txt
└── test_vivado_mcp.py

官方实现的核心机制是：

Python MCP Server
        │
      pexpect
        │
        ▼
vivado -mode tcl

也就是 MCP Server 启动 Vivado Tcl，然后通过 stdin/stdout 与 Vivado 通信。

九、但是这里先不要安装整个 FPL26 Agent

这是我特别建议你的地方。

不要：

git clone
make setup
运行整个 dcp_optimizer

因为整个项目是一个比赛用的 Agent，里面还包含：

RapidWright
Java
DCP optimizer
LLM
OpenRouter

而你现在只需要：

Claude Code
    ↓
VivadoMCP
    ↓
Vivado 2023

所以我们先单独拿：

VivadoMCP/

出来。

十、版本兼容性检查

这里是整个方案最需要谨慎的地方。

AMD 官方仓库目前要求：

Vivado 2025.1
Python 3.8+
Java 11+

并明确把 Vivado 2025.1 写在 prerequisites 里。

因此我们不能说：

官方 VivadoMCP 支持 Vivado 2023。

目前不能这么说。

我们只能说：

官方 VivadoMCP 是一个很好的基础实现，而 Vivado 2023 具有 Tcl batch/Tcl shell 能力，因此可以尝试做 2023 兼容层。

十一、先做 VivadoMCP 的“版本探测”

不要直接运行 Agent。

先让 MCP Server 能够回答：

Vivado version
Vivado executable path
FPGA part
Tcl version

例如 MCP 增加一个：

get_environment_info

返回：

{
  "vivado_version": "2023.2",
  "vivado_path": "C:\\Xilinx\\Vivado\\2023.2\\bin\\vivado.bat",
  "part": "...",
  "tcl_version": "8.x"
}
Check 5

Claude Code 还没参与。

直接运行 MCP server：

python vivado_mcp_server.py

看：

Python启动
↓
Vivado启动
↓
vivado -mode tcl
↓
Tcl handshake
↓
server等待MCP

必须全部成功。

十二、Windows 是这里最大的技术风险

官方 VivadoMCP 使用：

pexpect

来控制：

vivado -mode tcl

但是 pexpect 更偏 Unix/POSIX 环境。

而你是：

Windows

所以这里我不建议强行照搬 Linux 实现。

更稳的 Windows 方案是：

Claude Code
      │
      │ stdio MCP
      ▼
Windows VivadoMCP
      │
      │ subprocess
      ▼
vivado.bat -mode tcl

也就是说：

Linux 原版
Python
 ↓
pexpect
 ↓
vivado
Windows 改造版
Python
 ↓
subprocess.Popen
 ↓
vivado.bat

然后自己实现：

stdin → Vivado
stdout ← Vivado
十三、所以我建议你不要在第一版追求“官方 MCP 原样运行”

而是：

AMD 官方 VivadoMCP
        │
        ▼
作为 API / Tool 设计参考
        │
        ▼
Windows VivadoMCP Adapter
        │
        ▼
Vivado 2023

这样反而更稳。

十四、第一批 MCP Tool 不要太多

第一版只做 8 个：

1. get_vivado_version
2. open_project
3. run_tcl
4. run_synthesis
5. run_implementation
6. report_timing
7. report_utilization
8. close_vivado

其中最关键的是：

run_tcl

因为 Vivado Tcl API 非常庞大。

例如 Agent 想查询：

get_cells
get_nets
get_pins
get_clocks
get_property

没必要全部重新包装 MCP。

让：

run_tcl

承担低层能力。

十五、第五层：Claude Code 接 MCP

Claude Code 官方支持 MCP，并可以通过：

claude mcp

管理 MCP Server。

最终：

Claude Code
     │
     │ MCP stdio
     ▼
vivado_mcp_server.py
     │
     ▼
Vivado 2023

配置完成后先不要让 Claude 改代码。

只问：

查询 Vivado 版本。

然后：

查询当前 FPGA part。

然后：

执行 report_utilization。

然后：

执行 report_timing_summary。

十六、Claude Code MCP 的验收顺序

严格按照：

Test 1
查询 Vivado 版本

必须：

2023.x
Test 2
查询 FPGA part

必须和真实工程一致。

Test 3
执行 get_clocks

检查 Tcl 正常。

Test 4
report_utilization

检查返回正常。

Test 5
report_timing_summary

检查：

WNS
TNS

能被 Agent 正确读取。

Test 6
restart Vivado

测试异常恢复。

Test 7
打开工程

确认 project state 正常。

Test 8
执行一次 synthesis

确认 MCP 能真正改变 Vivado 状态。

十七、然后才建立 FPGA Skill

我建议目录：

D:\fpga-agent\
│
├── .claude\
│   └── skills\
│       └── fpga-engineer\
│           └── SKILL.md
│
├── mcp\
│   └── vivado-mcp\
│
├── scripts\
│
└── projects\

Skill 不应该写成：

“你是一个 FPGA 专家。”

这种东西价值很低。

应该写工程流程约束。

例如：

# FPGA Engineer Skill

## Toolchain

Vivado: 2023.2
Vitis: 2023.2

Never assume another Vivado version.

## Before modifying RTL

1. Inspect project structure.
2. Identify top module.
3. Identify target FPGA part.
4. Identify clocks.
5. Read XDC constraints.
6. Establish baseline timing/resource reports.

## RTL modification

...

## Validation

After every significant RTL modification:

1. Run synthesis.
2. Check utilization.
3. Run implementation when required.
4. Check WNS/TNS.
5. Check timing violations.
6. Compare against baseline.

## Safety

Never:
- modify XDC without explicit reason
- change FPGA part
- change clock constraints silently
- delete generated files
- overwrite golden DCP
十八、这里要加入一个非常重要的版本规则

Skill 第一行就应该明确：

TOOLCHAIN_VERSION = 2023.2

然后规定：

If Vivado reports a version different from 2023.2:
STOP.
Do not execute synthesis/implementation.
Report version mismatch.

这非常重要。

因为 Agent 最危险的情况之一就是：

Claude:
我执行 Vivado。

实际上：
PATH → Vivado 2025.1

然后生成：

2025.1 DCP
2025.1 XSA

你的 2023 工程就污染了。

十九、甚至建议 MCP 自己做版本保险

不要只依赖 Claude 的 Skill。

在 MCP Server 启动时：

启动 Vivado
     ↓
version
     ↓
检查
     ↓
2023.2 ?
   /     \
 Yes      No
  │        │
继续      STOP

例如：

Expected:
Vivado 2023.2

Detected:
Vivado 2025.1

ERROR:
Vivado version mismatch.
Expected 2023.2.
Refusing to execute tools.

这样即使 Claude 犯错，也执行不了错误版本。

这比单纯写 Skill 安全得多。

二十、Vitis 也采用同样机制

Vitis 2023.2 官方支持 interactive Python：

vitis -i

并且可以通过 CLI / script 管理开发流程。

所以以后可以再做：

VitisMCP

但第一阶段我不建议。

先：

Claude
 ↓
VivadoMCP
 ↓
Vivado 2023

打通。

然后：

Claude
 ├── VivadoMCP
 └── VitisMCP
二十一、最终验收测试

全部完成之后，拿一个非常小的真实项目。

例如：

AXI GPIO
+
Zynq

或者最简单：

counter
+
ILA

然后直接对 Claude Code 说：

检查当前 FPGA 工程，不修改任何文件。报告 Vivado 版本、FPGA 型号、时钟、LUT/FF/BRAM/DSP 使用量，以及 WNS/TNS。

如果它能够：

Claude
 ↓
MCP
 ↓
Vivado
 ↓
report
 ↓
Claude分析

那么第一阶段成功。

然后第二次：

把 counter 改成 32 bit，并重新综合，比较修改前后的资源变化。

如果成功：

Claude
 ↓
修改 RTL
 ↓
MCP
 ↓
Vivado synthesis
 ↓
report_utilization
 ↓
Claude比较

第二阶段成功。

最后才测试：

把目标时钟提高到 200 MHz，在不改变功能的情况下优化 RTL，直到 timing pass。

这才是真正的：

Agentic FPGA Engineering
二十二、我给你划一个版本兼容矩阵
组件	你当前	第一阶段要求	备注
Windows	Windows 10/11	✅	Vitis 2023.2 官方支持
Vitis	2023.x	2023.x	保留
Vivado	2023.x	与 Vitis 同版优先	核心
Claude Code	当前版	最新稳定版	MCP host
Python	3.x	建议独立 venv	MCP
Node.js	18+	Claude Code 要求	官方要求
VivadoMCP	AMD 2026项目	需改造/验证	官方项目要求 2025.1
RapidWright	暂不装	❌	第二阶段
VitisMCP	暂不做	❌	第二阶段
AMD Doc MCP	暂不依赖	❌	可后加
FPGA Skill	自建	✅	核心

Claude Code 当前官方 Windows 支持 Windows 10+，可通过 WSL 或 Git for Windows 运行；官方也建议用 claude doctor 做安装检查。

二十三、整个部署我建议分成 7 个 Gate

最终我们就按这个表执行：

Gate	验证内容	通过标准
G0	版本	Vitis/Vivado 精确版本确认
G1	AMD 环境	vitis / vivado 正确指向目标版本
G2	Vivado Tcl	vivado -mode batch 成功
G3	真实工程	synthesis/implementation/timing 成功
G4	VivadoMCP	MCP → Vivado 2023 成功
G5	Claude Code	Claude → MCP → Vivado 成功
G6	FPGA Skill	Agent 按工程规范工作
G7	Autonomous loop	RTL → build → timing → 修改 → rebuild

任何一个 Gate 不通过，不进入下一层。

我特别建议你现在先不要动任何东西

我们先做 G0。

你在 PowerShell 里执行这几个：

vitis -version
vivado -version
where.exe vitis
where.exe vivado
python --version
node --version
claude --version

把完整输出直接贴给我。

我可以根据你的实际输出，先帮你把 G0/G1 做掉，尤其检查有没有 Vitis 2023.x 和 Vivado 版本不一致、PATH 指向了别的 Vivado、Claude Code 使用的是 Windows 还是 Git Bash/WSL。然后我们再进入 MCP，不会一上来把环境搞乱。