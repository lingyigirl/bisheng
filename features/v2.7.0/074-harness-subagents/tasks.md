# F074 执行记录

> 最新状态：2026-10-18 已完成云端中间件功能、Skill 对象存储、多模型、副作用恢复与流式积压补测；代码基线 6db8e2657 已推送开发分支。本地应用服务与测试容器已停止。以 [当前验收摘要](./current-acceptance.md) 为准，文档入口为 [README](./README.md)；下文逐轮记录保留历史口径，T36 仍未全部验收。

## 文档与实施状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 分支 | 已整合并推送当前开发分支 | feat/2.7.0/beijing-ns-llm-guardrail；早期分支见历史基线记录 |
| Spec Discovery | 已完成 | 会话中已明确 1.3 归属、快速交付范围和启动授权 |
| spec.md | ✅ 已评审并经用户确认 | 2026-10-13 用户回复“确认规格，继续推进”；spec-review.md |
| design.md | ✅ 已评审并经用户确认 | 用户回复“确认”；design-review.md |
| tasks.md | ✅ 已拆解并完成自查 | review.md；实施以设计确认后的任务表为准 |
| 业务代码 | 核心实现完成 | 配置、装配、身份事件、页面、补播与记忆接口适配 |
| 测试 / E2E | 核心真实流程与定向回归通过；完整验收仍有遗留项 | current-acceptance.md / prepush-validation-2026-10-18.md |

## 已完成

- [x] 确认原工作区干净，从 main 创建独立分支。
- [x] 阅读版本契约、架构宪法、SDD Guide、后端与客户端规则。
- [x] 记录需求来源、责任边界和原始需求仍未覆盖的部分。
- [x] 编写书面验收规格，区分事件补播、人工中断恢复与崩溃恢复。
- [x] 按 sdd-review 的 spec 清单完成自查并记录结果。

## 实施任务（设计确认后执行）

路径缩写：`B=src/backend/bisheng`，`BT=src/backend/test/linsight`，`C=src/frontend/client/src`。下表路径均相对这些根。勾选表示该工作包的代码/离线验证完成，不代表真实服务 E2E 通过；真实联调单独在 T36 记录。测试任务按实际复用的测试文件更新。

### Wave 0：协议与存储基础

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T01 | [x] | 基础设施 | `B/linsight/domain/schemas/agent_config.py`、`linsight_schema.py`：版本化配置，唯一 ID、长度、最多 5 成员；null/空列表保持旧行为 | 设计确认 |
| T02 | [x] | 基础设施 | `B/linsight/domain/models/linsight_session_version.py` 与一条 Alembic revision：nullable JsonType 字段；用 alembic heads 查父节点，不猜文件序号；应用回滚保留字段，DDL downgrade 有数据丢失说明 | T01 |
| T03 | [x] | 基础设施 | `B/common/errcode/linsight.py` 与对应共享 api_errors 源：登记未占用 110 段错误码、三语错误信息，生成而非手改产物 | T01 |

### Wave 1：服务端成员配置与运行

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T04 | [x] | 后端测试 | `BT/test_agent_config.py`：非法/重复配置、授权资源集合差异、共享模型、禁用技能、连接器动作、null 兼容；覆盖 AC: AC-01, AC-03, AC-04, AC-06, AC-19 | T01,T03 |
| T05 | [x] | 后端 Domain | `B/linsight/domain/repositories/agent_config_repository.py`、`services/agent_config_service.py`：快照读写与资源解析，统一权限校验，请求集合必须全部有效，不新增 DAO 方法 | T02,T04 |
| T06 | [x] | 后端 Domain | `B/linsight/domain/services/workbench_impl.py`：首次提交保存解析后快照；已有会话/新会话同语义，不改变 F073 | T05 |
| T07 | [x] | 后端测试 | `BT/test_configured_subagents.py`、`BT/test_harness_runtime.py`：不同模型与工具集合、Skills 枚举、默认 GP 抑制、并发上限、失败降级和无子图 HITL；覆盖 AC: AC-02, AC-03, AC-06, AC-09, AC-10 | T04 |
| T08 | [x] | 后端 Domain | `B/linsight/domain/services/agent_factory.py`：按快照分别装配成员，保留防循环/二进制 guards；委派限流，UI ID 与内部名称映射 | T05,T07 |
| T09 | [x] | 后端 Domain | `B/linsight/domain/services/skill_provisioning.py`：每成员显式枚举与摘要校验，新模式装载失败报错；legacy best-effort 不变 | T07,T08 |
| T10 | [x] | 后端测试 | `BT/test_harness_runtime.py`：快照保存与恢复一致、成员配置只读、撤权后恢复拒绝、多轮上下文；覆盖 AC: AC-04, AC-05, AC-14, AC-17 | T06,T08 |
| T11 | [x] | Worker | `B/linsight/domain/task_exec.py`：新建/人工恢复/继续共用成员配置；沿用既有任务记录恢复 tenant_id ContextVar，finally reset；成员工具单独实例，避免共享可变客户端 | T09,T10 |
| T12 | [x] | 后端 API | `B/linsight/api/endpoints/linsight.py` 与 workstation 现有提交协议入口：只透传统一配置，调用 Service 校验；检查受影响共享入口的旧请求兼容 | T06,T11 |

### Wave 2：调用关联与补播

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T13 | [x] | 后端测试 | `BT/test_harness_runtime.py`：锁定 deepagents 版本，验证真实并行 tool_call_id/子图 namespace 桥接、重复成员、乱序；覆盖 AC: AC-07, AC-08 | T08 |
| T14 | [x] | 后端 Domain | `B/linsight/domain/services/subagent_context.py`（新增）与 `agent_factory.py`：每次委派捕获 invocation_id，子图 custom 帧显式发布 namespace 映射；原生不支持时受控包装，禁止猜顺序 | T13 |
| T15 | [x] | 后端 Domain | `B/linsight/domain/services/stream_event_mapper.py`：映射持久化到事件，未关联帧有界缓存，失败有明确诊断 | T14 |
| T16 | [x] | 后端测试 | `BT/test_event_replay.py`：实际 Lua + fakeredis 协议测试覆盖（真实 Redis 待 T36）幂等重试、多读者、快照/游标一致、裁剪、终态、未来游标；纯逻辑测试覆盖投影；覆盖 AC: AC-11, AC-12, AC-13 | T03 |
| T17 | [x] | 后端 Domain | `B/linsight/domain/services/event_replay.py`：原子幂等 append 与原子事件序列快照游标（客户端投影）、read_after、保留策略；Redis 丢失返回不完整历史状态 | T16 |
| T18 | [x] | 后端 Domain | `B/linsight/domain/services/state_message_manager.py`、`message_stream_handle.py`：按任务协议分新旧链路，先写再推，终态持久化完成后结束推送 | T15,T17 |
| T19 | [x] | 后端测试 | `BT/test_harness_api.py`、`BT/test_harness_access_memory.py`：跨租户、同租户非所有者、共享查看者、未登录、恶意 Redis key/游标；覆盖 AC: AC-19 | T05,T17 |
| T20 | [x] | 后端 API | `B/linsight/api/endpoints/linsight.py` 与 `domain/services/agent_config_service.py`：增加快照读取与 WS 授权，配置响应脱敏；新代码不在 Endpoint 直接查询 ORM | T18,T19 |

### Wave 3：Client 工作空间

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T21 | [x] | 前端 Client 测试 | `C/components/Linsight/Input/SubagentPicker.test.tsx`：默认成员、编辑、资源独立选择、运行只读、任务切换；覆盖 AC: AC-01, AC-03, AC-05 | T01 |
| T22 | [x] | 前端 Client | `C/components/Linsight/Input/SubagentPicker.tsx`、`SubagentEditor.tsx`：现有 UI 抽屉与选择器，useState/props，两个可编辑预设，无新增 Recoil | T21 |
| T23 | [x] | 前端 Client | `C/components/Chat/AiChatInput.tsx`、`Linsight/Input/TaskModeChatInput.tsx`：插槽、成员标签、配置提交；默认日常聊天不变，清理触及文件中的冻结中文 | T22 |
| T24 | [x] | 前端 Client | `C/components/Linsight/Input/TaskModeInput.tsx`、`C/hooks/useLinsightManager.tsx`：执行页只读快照、提交透传；新配置用组件状态，现有状态只扩展任务响应数据 | T12,T23 |
| T25 | [x] | 前端 Client 测试 | `C/components/Linsight/Execution/subagentIdentity.test.ts`：重复成员/乱序/失败/legacy；覆盖 AC: AC-06, AC-07, AC-08, AC-09, AC-10 | T15 |
| T26 | [x] | 前端 Client | `C/components/Linsight/Execution/execTypes.ts`、`stepTree.ts`：新版 identity 分组，旧历史保留 fallback | T25 |
| T27 | [x] | 前端 Client | `C/components/Linsight/Execution/SubagentRunCard.tsx`、`ExecutionTimeline.tsx`：名称/任务/状态/耗时/结果，工具活动折叠 | T26 |
| T28 | [x] | 前端 Client 测试 | `C/hooks/Websocket/replay.test.ts`：先快照后订阅、断连补发、重复帧、过期 reset、停止后迟到事件；覆盖 AC: AC-10, AC-11, AC-12, AC-13 | T20 |
| T29 | [x] | 前端 Client | `C/api/linsight.ts`、`C/hooks/Websocket/index.tsx`：恢复快照请求、事件去重、成功应用后推进游标；不手改 suppressions | T24,T28 |
| T30 | [x] | 前端 Client | `C/locales/{zh-Hans,en,ja}/translation.json`：成员配置和恢复提示三语键，生成 api_errors，执行 parity 检查 | T23,T24,T27,T29 |

### Wave 4：依赖适配与验收

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T31 | [x] | 后端测试 | `BT/test_harness_access_memory.py`：可信身份、unavailable 状态、提交来源、子 Agent 无直接持久写；覆盖 AC: AC-18 | T11 |
| T32 | [x] | 后端 Domain | `B/linsight/domain/services/memory_adapter.py`、`domain/task_exec.py`：定义并注入可选提供者，未接入不伪造成功，不建设记忆存储 | T31 |
| T33 | [x] | 后端测试 | `BT/test_harness_runtime.py`、`BT/test_worker_stranded_session.py`：人工中断后换 Worker、异常退出、未知副作用禁止自动重放；覆盖 AC: AC-14, AC-15, AC-17 | T11,T18 |
| T34 | [x] | Worker | `B/linsight/worker.py`、`domain/task_exec.py`：验证现有恢复/失败状态，并增加成员沙箱资源清理；未改写 Worker 接管机制；受控崩溃恢复若无安全证据保持关闭，tenant ContextVar 沿用记录恢复 | T33 |
| T35 | [x] | 集成测试 | `BT/test_e2b_copy_in_out.py`、`BT/test_configured_subagents.py`：任务沙箱文件 copy-in/out、产物下载、失败状态；使用真实 E2B 结果与 mock 结果分列；覆盖 AC: AC-09, AC-16 | T11 |
| T36 | [ ] | E2E | 特性目录 `e2e-report.md`：真实模型、两成员、刷新/断网、多端、澄清恢复、越权、沙箱文件、记忆依赖状态；覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18, AC-19 | T30,T32,T34,T35 |
| T37 | [x] | 交付 | `review.md`、`tasks.md`：记录后端 pytest/ruff 增量/arch-guard、前端 lint/typecheck/i18n、单 Alembic head、双库验证状态、代码评审和部署/回滚结论；不可用环境明确阻塞，不标已通过 | T36 |

## 日程目标

- 第一天下午优先 Wave 0–3 核心闭环，工具链和环境就绪为前提。
- 第二天故障路径、真实资源、恢复与适配联调。
- 第三天回归、缺陷与交付；更早满足所有门禁可提前交付。
- 不使用任务数量推导工时；Codex 加速编码，不代替真实模型、沙箱或数据库验证。

## 实际偏差记录

- 2026-10-13：客户端规则已冻结新增 Recoil，后续设计采用组件状态和服务端快照回显；不新增 atom/selector。
- 2026-10-13：设计细化补充“事件投影快照与游标必须同源”，用于实现已确认的补播不丢不重要求；见 design.md §3.5。

## 本轮验证结果与例外

- 后端前轮专项回归：137 passed；2026-10-14 最终受影响 16 文件专项 132 passed（选择范围不同，不相加）（含原生 deepagents 并行、重建图后人工恢复、HTTP 快照授权、Lua 补播与隔离、技能目录、现有 Worker/E2B/分享回归）。
- 前端：98 passed / 6 suites；全工作区 lint、typecheck、check-i18n 通过。页面与故障联调补齐终态快照、历史配置、人工答案、持续退避重试及 Worker 失败通知；本轮后端相关回归 82 passed。
- Ruff：新增 Python 文件与新增诊断清零；修改的 legacy 文件仍有既有历史规则诊断，未声称全量 ruff clean。
- T36 部分完成：Docker/MySQL、真实 DeepSeek、WebSocket 补播、人工等待点换进程、工作空间核心流程、API 实际断连自动重连、Worker 崩溃失败通知和主动终止通过。用户确认暂无 E2B；真实 Skill/连接器、工具撤权、Skill 禁用、跨租户 API、分享只读及浏览器裁剪提示已通过；E2B、DM8 与依赖场景仍未完成，见 e2e-report.md。
- T35 复用的 E2B 测试为离线文件协议回归，不是云端执行成功证明。
- T32 仅完成 MemoryAdapter 接口与 unavailable 行为；真实记忆服务属于其他负责人。
- 独立管理员开关未新增；当时新模式按非空成员配置启用。2026-10-18 按用户新要求调整为所有新任务启用，历史任务仍保留原协议，见 recovery-policy.md。
- T37 完成交付记录，不代表可以跳过 T36 合并上线。

## 2026-10-14 已确认扩展：执行中崩溃自动接续

本轮由用户确认 crash-recovery-proposal.md 后实施；覆盖原计划之外的 CR-01–CR-12，范围与边界以 automatic-recovery-validation.md 为准。

- [x] R01：recovery_epoch / recovery_state 单链迁移，MySQL 实际升级与 DM8 字段专项。
- [x] R02：Redis 租约、DB 代次隔离、运行期扫描、次数上限与退避、终止优先。
- [x] R03：主子工具回执、结果复用、只读重试白名单、未知副作用与缺失元数据阻断。
- [x] R04：同 thread_id 的空输入恢复、人工 interrupt 保留、工作区完整代次复制。
- [x] R05：三语恢复/阻断状态、未完成投影清理、恢复成员身份、完成帧丢失后快照校正。
- [x] R06：真实主/子模型强杀、双 Worker 重复消息、旧进程暂停后复活、用户终止、HTTP 未知结果和 MinIO 文件恢复。
- [x] R07：相关自动化回归、页面实际核对、增量 lint/类型/i18n、文档与运行环境整理。

R06 的真实服务证据与离线/模拟覆盖分列在自动恢复验收报告；不等于逐一穷举所有工具、存储灾难和操作系统崩溃点。全量前端 lint 的 59 项基线错误、长期记忆外部依赖及全平台 DM8 验收仍保留。

### 2026-10-18 网页验收缺陷修复

- [x] API 恢复后自动解除错误遮罩，保持当前页面与任务事件接续。
- [x] 后台子 Agent 按真实结算更新原始调用卡片，展示实际耗时和最终报告。
- [x] 成员编辑/只读快照补齐已授权连接器，缺失动作保留不可用标识。
- [x] job_output 默认有界异步等待，保留查询与取消语义，减少模型轮询。
- [x] CSV 任务上传、沙箱计算、二次核验、下载闭环。
- [x] 单测、Linsight 回归、真实浏览器/API 中断及回放验证；见 `browser-fix-validation-2026-10-18.md`。

## 2026-10-18 第二轮网页复测修复

- [x] 使用持久化终止时间收口执行投影，保留完成证据，实时与刷新计时一致，结束等待工具状态。
- [x] 从实际成员 registry 校验委派名称，前台/后台无效调用明确失败，不创建成功 job。
- [x] 在 API 行号与表格转换器边界统一转换，修复 CSV/XLSX 默认表头，保留自定义多行语义。
- [x] 自动化回归及真实网页停止、回放、上传、独立只读核验；见 `browser-retest-fixes-2026-10-18.md`。

## 2026-10-18 无成员 Harness 与恢复策略

- [x] 新任务无成员或省略配置时仍保存 Harness 事件/恢复协议和主模型快照，不升级历史任务。
- [x] 显式空列表清空成员，省略配置的追问继续重验并继承；继承失败后保留新任务 Harness 协议。
- [x] 前端按事件协议补播，不再按成员数量判断。
- [x] 明确内置只读、持久结果复用、外部未知副作用、沙箱进程状态、持久文件和后台 job 的恢复边界，并补回归锁定。
- [x] 网页无成员工具闭环与刷新、真实 Worker 强杀后无成员任务接续、历史 legacy 保持、全量回归；实现和证据见 recovery-policy.md。


## 2026-10-18 四项性能修复

- [x] P01：客户端索引合并思考帧，快照一次提交，保留代次替换与原始耗时。
- [x] P02：固定游标的分批快照、兼容可选无损压缩，覆盖并发追加、裁剪、跨页合并和末尾游标。
- [x] P03：恢复候选最小字段分页、批量 Redis 租约检查、metadata/增量索引和本地实际升级。
- [x] P04：运行期选定资源查询、工具类型联表、按工具组批量授权，覆盖每步撤权/禁用阻断。
- [x] P05：前后性能对比、真实 WebSocket 断线、新任务 Worker 强杀恢复、成员 Skills/连接器/E2B 闭环及浏览器回放；原始证据与限制见性能修复验收报告。


## 2026-10-18 沙箱传输专项

- [x] E01：定位 54 文件的串行及重复上传/下载，按路径去重并批量传输。
- [x] E02：合并变更产物的持久/本地回传，确认失败重试与禁止执行后重跑语义。
- [x] E03：分段计时、71 项定向与 989 项 Linsight 回归、真实两次代码执行、MinIO 内容与 MD5、浏览器文件预览。

## 2026-10-18 完整本地回归与缺陷修复

- [x] 网页配置双成员并执行 CSV/E2B、Skill、计算器、时间与只读连接器闭环；实际预览、下载、MinIO MD5 校验。
- [x] 真实人工等待/继续、追问保留上下文、主动停止、WebSocket 游标重连、安全崩溃接续和沙箱进程状态阻断。
- [x] Skill 云端包导入/更新/旧版本/缓存恢复与迁移回退，OpenFGA 允许/拒绝及失败补偿状态复核。
- [x] 修复主 Agent 失效 Skill 被静默忽略；修复恢复阻断后仍显示接续成功提示；后台本地文件代理改为云端 MinIO 地址。
- [x] 修复后后端相关 1113 passed/8 skipped；Harness UI 与补播 233 passed；客户端 lint、类型检查、国际化通过。
- [ ] 完整验收仍受 API 一次原因未明退出、5 个旧 Skill 源文件缺失、客户端 5 个失败测试套件、后台 lint 54 项问题以及既有外部验证边界限制；不据此勾选 T36。具体证据见 `full-local-acceptance-2026-10-18.md`。

- [x] 2026-10-18 补测两个真实模型配置主/子互换（1→18、18→1），均真实委派、调用计算器并完成整合；五个外部 HTTP 副作用/进程故障窗口验证已持久回执不重复发送、结果不明安全阻断。范围与证据见 `multimodel-external-recovery-2026-10-18.md`；不代表跨厂商或所有连接器完整端到端验收，不勾选 T36。

- [x] 2026-10-18 修复并行子代理思考碎片逐帧持久化积压：有界预取、按段合并、事件屏障、取消与租约丢失清理；1020 项 Linsight 回归通过，三路 6000 段模拟写入减少 99.8%，真实双模型并行任务 31.53 秒完成，见 `stream-buffer-performance-2026-10-18.md`。
- [ ] 本轮更新 Worker 时原长任务自动接续返回 Task produced no result；已保留研究摘要并走原会话 continue 接口救回交付流程。自动接续收尾问题尚未定位修复，不把人工维护接续算作自动恢复通过。

- [x] 2026-10-18 依用户要求完成当前分支推送前复测：后端 1121 项、Harness UI 233 项、真实副作用与恢复 37 项通过；新增双子代理完成后检查点重建/整合前中断回归，定向 44 项通过；类型、客户端 lint、i18n 与服务健康通过。已知边界保留，见 `prepush-validation-2026-10-18.md`。

- [x] 2026-10-18 收尾：停止本地 API、Worker、双前端、测试 HTTP 服务和 7 个本地中间件容器，保留数据与忽略目录证据；新增文档索引与环境交接，归档旧当前验收并更新摘要。见 `README.md`、`local-environment-handoff.md`。

## 2026-10-21 E2B 产物分类与流式回收

路径缩写：`B=src/backend/bisheng`，`L=src/backend/bisheng_langchain/gpts/tools/code_interpreter`，`BT=src/backend/test/linsight`。

### Wave 5：测试先行

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T38 | [x] | 后端测试 | `BT/test_code_interpreter_artifact_policy.py`（新增）：路径优先级、`output/` 保护、scratch 明确临时产物、未知大型 scratch、旧路径命名空间；覆盖 AC: AC-20, AC-21, AC-22 | 设计确认 |
| T39 | [x] | 后端测试 | `BT/test_workspace_backend.py`：`write_stream` 完整成功、流中失败、声明大小不一致、上传失败、本地缓存与 MinIO 状态；覆盖 AC: AC-20, AC-23 | T38 |
| T40 | [x] | 后端测试 | `BT/test_e2b_copy_in_out.py`：目录级剪枝、已知垃圾不读取、未知 scratch 必持久化、大 output 走 stream、单文件失败继续；覆盖 AC: AC-20, AC-21, AC-22, AC-23 | T39 |
| T41 | [x] | 后端测试 | `BT/test_init_config_tools.py`、`BT/test_final_result_selection.py`：任务 copy-in 与交付物扫描使用相同策略，scratch 临时产物不会进入 E2B copy-in 或工作区；覆盖 AC: AC-21, AC-22 | T40 |
| T42 | [x] | 后端测试 | `BT/test_crash_recovery.py`：有状态代码工具在执行/回收未知结果后仍阻断恢复，逐文件持久化不得改变重放规则；覆盖 AC: AC-24 | T40 |

### Wave 6：实现

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T43 | [x] | 后端 Domain | `L/artifact_policy.py`（新增）：集中路径规范化、`ArtifactDecision`、`output/` 优先、全局工具目录剪枝、scratch 临时产物规则 | T38 |
| T44 | [x] | 后端 Domain | `B/linsight/domain/services/workspace_backend.py`：新增 `write_stream`，临时文件承接 E2B 流，校验大小后上传 MinIO 并写穿本地缓存 | T39 |
| T45 | [x] | 后端 Domain | `L/e2b_executor.py`、`L/tool.py`、`L/base_executor.py`、`L/local_executor.py`：元数据扫描接入决策器，已知垃圾不读取，选中文件使用 `format="stream"` + `request_timeout`，逐文件错误与兼容字段；模型 schema 暴露 `required_files`，keep-alive 的 sandbox 404 先触发重建再 copy-in；可从本地/后端补传超过 5MB 的 scratch | T40,T43,T44 |
| T46 | [x] | 后端 Domain | `L/local_executor.py`：使用同一目录与临时产物策略，仅持久化真实任务状态 | T41,T43 |
| T47 | [x] | 后端 Domain | `B/linsight/domain/services/workbench_impl.py`、`B/linsight/domain/utils.py`：copy-in、基线、交付扫描统一使用产物决策器，保持 output/scratch 语义 | T41,T43 |
| T48 | [x] | 后端 Domain | `L/base_executor.py`、`L/e2b_executor.py`：移除无生产用途的阈值歧义，工具描述说明未知 scratch 持久化与临时产物边界；保持旧返回字段 | T45 |

### Wave 7：端到端与验收

| ID | 状态 | 类型 | 工作与文件 | 依赖 |
|---|---|---|---|---|
| T49 | [x] | 集成测试 | `BT/test_e2b_copy_in_out.py`、`BT/test_workspace_backend.py`：汇总定向回归，验证流式链路、MinIO 写穿、旧协议和恢复安全；覆盖 AC: AC-20, AC-21, AC-22, AC-23, AC-24 | T42,T46,T47,T48 |
| T50 | [x] | E2E | 真实 E2B 聚焦验证：字体缓存跳过、正常图表交付、6MiB scratch 在 sandbox 自然过期并重建后通过 `required_files` 恢复、大 output 落 MinIO；证据见 `e2b-artifact-policy-validation-2026-10-21.md` | T49 |
| T51 | [x] | 交付 | `/task-review`、后端定向与邻域回归、Ruff 增量、arch-guard、`git diff --check`、文档同步；证据追加到 `tasks.md` 和验证文档 | T50 |

### 离线验证记录

- 2026-10-21：产物策略、E2B copy-in/out、WorkspaceBackend、copy-in、交付扫描、超时、路径重定位、恢复安全等 11 个测试文件定向回归 **232 passed**。
- 新增 `write_stream` 用例覆盖完整流、源流中断、大小不一致、MinIO 上传失败与本地缓存状态；全部通过。
- 新增 `required_files` 工具 schema/透传与 keep-alive 404 用例；大型 scratch 可从本地 `file_dir` 和 WorkspaceBackend 补传到重建后的沙箱。
- `ruff format`、增量 `E4/E7/E9/I`、`arch-guard.sh`、`git diff --check` 通过。
- 2026-10-21 真实 E2B：普通回收任务 `persisted=3 failed=0`，字体/build 临时文件未读取；6MiB scratch 在 sandbox 自然过期后从 `i9rd...` 重建为 `ixzfty...`，`required_files` 恢复后的 SHA-256 一致并输出 `RECOVERY_OK`。
