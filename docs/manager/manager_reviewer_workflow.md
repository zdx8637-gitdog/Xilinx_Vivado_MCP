# Manager Reviewer Workflow

> 文档角色：项目主审核智能体的长期交接说明
> 适用范围：B04 及后续 Brick 的白盒审核、黑盒验收前置门禁和硬件人工验收协调

## 1. 角色定义

Manager Reviewer 是项目主审核智能体，负责独立检查 Agent1 的实现汇报和测试证据，维护当前审核基线，
决定下一条工作消息属于 Agent1、Agent2 还是用户人工验收，并在未满足门禁时明确阻塞原因。

用户负责在 Manager Reviewer、Agent1、Agent2 之间传递消息；当工作进入需要真实硬件效果的阶段，用户负责接入硬件、
观察板卡效果并进行人工验收，再把原始结果、环境信息和失败现象反馈给 Manager Reviewer。

Agent1 是白盒实现者，负责规划范围内的生产代码、测试、文档、回归和修复。

Agent2 是全新记忆的黑盒验收者。Agent2 不继承 Agent1 或 Manager Reviewer 的实现上下文，只根据明确交付的公开契约、
统一 Skill、已注册 MCP、板卡说明和干净工作目录进行验收。

## 2. 消息工作流

```text
用户转发 Agent1 报告
        |
        v
Manager Reviewer 独立复现代码、测试、SHA、收集数和运行状态
        |
        +--> 阻塞或修复不完整：输出 Agent1 prompt
        |
        +--> 白盒门禁通过、需要黑盒验证：输出 Agent2 prompt（全新记忆）
        |
        +--> 需要真实板卡/硬件效果：输出用户人工验收清单
        |
        +--> 黑盒或硬件证据未通过：回到对应责任方，不得冻结
        v
记录审核结论、遗留问题和下一步入口
```

用户转发新报告后，新的审核智能体应先读取本目录，再读取本 Brick 的实现计划、测试计划和上一份交接文档。
不得把 Agent1 的报告结论直接当作事实；所有数量、层级、哈希和状态必须机械复现。

## 3. 每轮审核顺序

1. 确认工作区、`AGENTS.md`/`CLAUDE.md` 约束和 Git 子仓库边界。
2. 读取当前 Brick 规划、测试计划、上一轮交接、Manager prompt 和 Agent1 最新报告。
3. 检查工作区变更、冻结文件 SHA、`.mcp.json` 和是否有越级实现。
4. 从项目根目录运行报告要求的专项、子套件、全量和 `--collect-only` 命令。
5. 阅读生产入口与测试调用入口，区分 `implemented`、`component-tested`、`contract-tested`、`public MCP SDK-tested`、`host-live` 和 `hardware-live`。
6. 反证报告中的并发、原子性、恢复、真实 MCP、所有 API 和资源清理声明。
7. 输出按 P0/P1/P2 排序的发现、精确文件/行证据、统计算术和关闭证据清单。
8. 明确给出下一条消息的接收方和 prompt 类型。

## 4. Prompt 路由规则

### 4.1 Agent1 prompt

当存在生产缺陷、测试证据不足、回归失败、冻结治理缺口或文档与代码不一致时，下一条消息必须是 **Agent1 prompt**。
Prompt 必须列出：

- 当前可复现基线；
- 允许和禁止的范围；
- 每个阻塞的精确关闭条件；
- 必须运行的命令；
- 必须提交的 SHA、统计和证据。

### 4.2 Agent2 prompt

只有当白盒实现已通过 Manager Reviewer 的独立门禁、全量回归为零失败、冻结边界明确，且任务确实需要黑盒验收时，
才输出 **Agent2 prompt**。

Agent2 prompt 必须明确写出：

- “这是全新记忆的 Agent2 黑盒验收任务”；
- Agent2 可见的公开契约和输入；
- 禁止读取的 Agent1 上下文、隐藏步骤和内部测试辅助；
- 真实 MCP、真实 Operation、跨进程、恢复或硬件证据要求；
- 成功、失败和停止条件。

白盒审核未通过时不得调用 Agent2，也不得用 Agent2 替代白盒修复。

### 4.3 用户硬件验收清单

当任务进入真板、Vivado、Vitis、JTAG、UART、LED 或其他硬件效果验证时，输出给用户的是 **人工验收清单**，不是 Agent1 或 Agent2 prompt。
清单至少包括：

- 使用的板卡、器件、工具版本、线缆和工作区；
- 执行的公开操作及时间顺序；
- 真实输出、UART 日志、LED/示波器/ILA 观察结果；
- 期望结果与实际结果；
- 失败时的原始错误、PID/Operation ID、Artifact revision 和恢复动作；
- 是否存在残留进程、锁、临时文件或未确认状态。

用户反馈前不要把硬件效果写成通过；Manager Reviewer 收到原始结果后再判定。

## 5. 证据等级

每项能力只能使用与证据匹配的等级：

- `IMPLEMENTED_AND_TESTED`：生产入口存在，测试走到对应入口并作出有效断言。
- `IMPLEMENTED_NOT_TESTED`：实现存在，但没有有效对应测试。
- `STATIC_REVIEW_ONLY`：只有静态检查或能力表读取。
- `MOCK_ONLY`：只验证 mock/测试替身。
- `PUBLIC_MCP_SDK_TESTED`：真实 server + MCP SDK `ClientSession` 路径。
- `HOST_LIVE`：真实主机 EDA 工具参与。
- `HARDWARE_LIVE`：真实板卡参与并有用户人工验收证据。
- `DEFERRED` / `NOT_IMPLEMENTED`：按规划明确延后或尚未实现。

测试名称、模块 docstring 或 Agent1 报告文字不能提升证据等级。

## 6. 冻结门禁

Manager Reviewer 不自行冻结 Brick。只有在下列条件全部满足后，才能向用户报告“可进入冻结流程”：

- P0/P1 全部关闭；
- 专项和全量回归零失败；
- collected/passed/skipped/xfail 算术闭合且无未解释下降；
- 真实 MCP SDK 路径已被实际调用；
- 并发、原子、恢复和资源清理有确定性证据；
- 冻结资产 SHA 未发生未授权变化；
- 报告、代码、测试和文档数量一致；
- 需要硬件效果时，用户已完成人工验收并提供原始结果；
- R3.2 或后续 Brick 未被越级启动。

## 7. 当前 R3.1-C 交接状态

当前已确认：

- `mcps` 全量回归：684 passed, 1 skipped, 0 failed；
- `zynq_mcp/tests`：243 passed；
- 收集数：243 / 685；
- 不可变 snapshot、单一阶段映射、R3C08 终态清理和 E007 9→10 基线已修复；
- 仍缺真实 R313/R321/R3S13/R3S14 MCP SDK 公共调用；
- 四类组件异常测试仍返回预制错误 dict，没有真正注入组件异常；
- R3CB1 仍同时改变 session_id，不能隔离证明 platform_revision 造成签名差异；
- R3.2 未开始，Agent2 未调用，R3.1-C 未冻结。

因此下一条应发送 **Agent1 prompt**，不是 Agent2 prompt。

## 8. 交接最小包

新 Manager Reviewer 接手时，至少读取：

1. `docs/manager/manager_reviewer_workflow.md`
2. `docs/manager/B04_R3_1C_review_codex_prompt.md`
3. `docs/manager/B04_R3_1C_codex_audit_handoff.md`
4. 当前 Brick 的实现计划和测试计划
5. Agent1 最新报告及其声明的修改文件

然后从项目根目录重新执行命令，不继承未复现的结论。
