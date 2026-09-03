# B13 联调暴露的框架问题与加强方案（Skill + MCP 泛化层）

- 日期：2026-09-03（B13 P0–P3 联调复盘）
- 范围：只列**泛化框架**问题（Skill + MCP），排除子代理协作机制（DSH 层）、B13 契约文本（项目层）、具体 RTL/固件代码缺陷（实现层）。
- 依据：P0/P1/P2 三次真板开发 + P3 联调全过程的实证记录。

---

## 0. 核心设计判断：线性阶段机 vs 真实迭代开发

现状：MCP 工作流阶段机把 Zynq 开发建模为**一次性正向流水**（PLATFORM_DESIGN → … → PL_BITSTREAM → PS_BUILD），工具按当前 stage 门禁，**只进不退**（ROLLBACK_TARGETS 中 PS_BUILD 仅可 retry 自身）。

冲突：真实开发是**环**——构建 → 上板测 → 找缺陷 → 改 RTL → 重建 → 重测。P1/P2 各撞一次「PS_BUILD 后要重建 PL 无路可走」：历史上只能外科改 ledger，或 close_session+create_session 重走全流程（平台步骤全部白跑）。

结论：线性模型保住了 fail-closed 纪律，但**没有把「合法回退/迭代」建模为一等公民**。加强方案以「让环合法、让环可验证、让环可观测」为主线。

---

## 一、MCP 问题清单（5 条，均带真板实证）

### M1. 阶段机无合法回退
- 现象：stage=PS_BUILD 后，`pl_create_project` 报 STAGE_PREREQUISITE_UNMET；`capabilities/context.py` 的 ROLLBACK_TARGETS 中 PS_BUILD→仅 [PS_BUILD]。
- 证据：P2 读挂修复后需重建 PL，被硬拦；`.tmp_p2_rollback.py` 外科改 stage 的历史先例；合法路径 = close+create 全流程重走。
- 修法：新增受控回退原子（如 `workflow_rollback(to_stage)`）：lane=IDLE、无 active op 时允许；回退同时把下游 artifact revision 标记失效（验证一致性不失真）。

### M2. 平台域原子覆盖缺口
- 现象：① 无自定义 RTL 打包/用户 IP 仓库注册原子 → 自研引擎只能走「PL 域工程 + .bd 文件」旁支（B12 黑盒先例）；② 无 BD 端口属性（ASSOCIATED_BUSIF）原子 → BD 41-2559 validate 告警无法消；③ 无接口级 DATA_WIDTH 原子 → SmartConnect 32-bit 与 DMA 64-bit 不匹配被迫换 axi_interconnect；④ `make_bd_intf_pins_external` 派生名（M01_AXI→M01_AXI_0）与 make_external 校验名不一致 → EXTERNAL_PORT_CREATE_FAILED（fail-closed 正确但名称对不上）。
- 修法：补 `platform_package_user_ip` / `platform_set_bd_port_property` / 接口宽度属性原子；make_external 派生名校验与 Vivado 实际命名对齐。

### M3. XSA 导出非字节确定
- 现象：同一 BD 重导出 XSA 字节不同（含时间戳）→ manifest revision 漂移（P2：307130c4→6bf2e166）→ verify 失配，只能靠 HWH 逐项 + ps7_init 字节一致做结构等价证明。
- 修法：XSA 导出确定性归一（去时间戳/非确定性字段）；manifest revision = 纯内容哈希。目标：同输入同输出（可复现构建）。

### M4. PS manifest 轮次指针不同步
- 现象：`ps_import_hardware`/`ps_compile` 不更新 built_from_platform_revision（session 恢复时沿用旧 platform 上下文）；`platform_export_manifest` 要求 BD open，session 重置后不可用。P1 遗留 verify 2 项失败，P2 重建 PS manifest 才关闭。
- 修法：session 恢复时同步最新 platform revision；export_manifest 对已导出产物幂等（产物在即返回现 manifest）。

### M5. 原子幂等性 / 重试语义缺陷
- 现象：`ps_set_compiler_options` 重复添加已持久化 define → FAILED；同 request_signature 重试被 dedup_registry 拒绝（P10），代理被迫清 registry（外科）。
- 修法：set 类原子幂等化（同值重复 set = 成功）；同签名合法重试放行或给出明确重试通道（如显式 retry 参数），禁止「失败一次即永久锁死同一输入」。

---

## 二、Skill 问题清单（4 条）

### S1. 缺「修复必须配回归」强制步骤
- 现象：RST 断开缺陷修复被后续代理的源码编辑**回退两次**；BVALID 零宽的教训没传到 P2——同一缺陷反复复活，两次都靠主代理亲测才抓回。
- 修法：Skill 强制项——任何缺陷修复必须附带回归用例并进入机读门禁脚本（修复无回归 = 不允许上板）。

### S2. 缺「综合告警门禁 + 仿真 X 传播检查」
- 现象：P2 conv_cnt 多驱动在 XSim 下 last-write-wins 掩盖（SIM_PASS）→ 真板引擎窗口挂死；synth CRITICAL 202 条才暴露。
- 修法：强制步骤——synth **CRITICAL WARNING=0** 列为上板前置门禁；仿真加多驱动/X 传播断言（xvlog/xelab 全 warning 审视）。

### S3. 缺「AXI 握手缺陷模式库」
- 现象：同族缺陷跨 Brick 复现——BVALID 零宽（P1）、RVALID 零宽（P2 再现）、READY 早置位下同拍 last-write-wins、多驱动。
- 修法：Skill 附录加泛化缺陷模式清单（零宽脉冲 / 同拍覆盖 / 早置 READY / 多驱动 / X 传播），开发时对照自查。

### S4. 缺「双端字节级 KAT 对拍」步骤
- 现象：P3 联调全 bad——上位机与下位机各按合理理解实现 + 各自自测全过，真机才撞 CRC 口径差异；下位机自证接收器与固件同源，「独立」不彻底。
- 修法：Skill 增加联调前强制步骤——双端用同一组字节级 KAT 向量对拍（不只各测各的）。

---

## 三、加强方案（按优先级，主线 = 让迭代合法/可验证/可观测）

1. **环的合法化（M1，最高优先）**：新增 `workflow_rollback(to_stage)` 受控回退原子 + 下游 artifact 失效传播；配套「快进」：`workflow_resume_from(stage)` 对既有产物做完整性/一致性校验后跳转（平台未变时重建 PL 不必重走平台流）。
2. **环的可验证性（M3+M4，次高）**：确定性构建（XSA 归一、manifest 纯内容哈希）+ 元数据同步——保证每次迭代后 verify_consistency 都能干净通过，迭代历史可审计。
3. **环的顺畅性（M5）**：原子幂等 + 合法重试——迭代必然重复调用同批原子，不允许「重复即失败」。
4. **环的覆盖度（M2）**：补平台域原子（用户 IP 打包/端口属性/宽度属性/命名对齐）——自研 RTL 进设计不再走旁支。
5. **Skill 四补（S1–S4）**：修复-回归锁定、综合门禁+X 检查、缺陷模式库、双端 KAT 对拍——把「测了但测不到、修了又丢、双方理解偏差」三类盲区关掉。
6. **迭代可观测**：ledger 记录 loop 历史（stage 前进/回退/迭代计数），审计可见「环是合法发生的」而非外科修改。

---

## 四、实施路径

- 按项目**修复轮模式**执行：每条修法 = 一个修复轮（生产代码 + 测试 + 全量回归统计 + 宿主真实工具 gate），不挤占 Brick 进度，可与 B13-P3/P4 并行排期。
- 建议分批：第一批（环合法化 M1 + 确定性 M3 + 幂等 M5）——直接消除「外科改 ledger」类风险；第二批（M2 原子补齐 + M4 同步）；第三批（Skill 四补，纯文档/纪律层，成本最低可最先做）。
- 每条入 backlog 时登记：现象证据、受影响工具/章节、验收测试设计。
