# F074 执行记录

> 最新状态：2026-10-14 已完成新基线真实 E2B 文件分析/下载、双成员、补播、人工等待点重启恢复及撤权复测。152 项后端回归、5 项真实环境测试通过。以 [current-acceptance.md](./current-acceptance.md) 为准；下文早期环境限制保留为历史记录。

## 文档与实施状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 分支 | 已创建 | feat/v2.7.0/074-harness-subagents |
| Spec Discovery | 已完成 | 会话中已明确 1.3 归属、快速交付范围和启动授权 |
| spec.md | ✅ 已评审并经用户确认 | 2026-10-13 用户回复“确认规格，继续推进”；spec-review.md |
| design.md | ✅ 已评审并经用户确认 | 用户回复“确认”；design-review.md |
| tasks.md | ✅ 已拆解并完成自查 | review.md；实施以设计确认后的任务表为准 |
| 业务代码 | 核心实现完成 | 配置、装配、身份事件、页面、补播与记忆接口适配 |
| 测试 / E2E | 离线测试通过；真实 E2E 阻塞 | implementation-report.md / e2e-report.md |

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
- 独立管理员开关未新增；新模式按非空成员配置启用，历史任务保留原路径。
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
