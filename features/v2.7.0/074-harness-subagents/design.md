# Design: 灵思 Harness 可配置子 Agent

> 当前增量：2026-10-14 用户已确认并实施 [crash-recovery-proposal.md](./crash-recovery-proposal.md)。恢复租约、数据库代次、工具回执、工作区代次及页面投影详见 [automatic-recovery-validation.md](./automatic-recovery-validation.md)。下文最初版本“未实现执行中接管”与“尚无 E2B/DM8”的描述属于实施前历史，不覆盖该已确认扩展。

> 最新状态：2026-10-14 已完成新基线真实 E2B 文件分析/下载、双成员、补播、人工等待点重启恢复及撤权复测。152 项后端回归、5 项真实环境测试通过。以 [current-acceptance.md](./current-acceptance.md) 为准；下文早期环境限制保留为历史记录。

关联：[spec.md](./spec.md) · [tasks.md](./tasks.md)。版本：v2.7.0。更新：2026-10-14。

**状态：用户已确认设计，核心代码及本地真实模型联调已完成；E2B、DM8 与真实记忆提供者仍待环境/依赖，T36 保持部分完成。**

## 1. 目标与非目标

在现有任务模式中增加成员配置、原生自主委派、准确执行展示和可回放事件。长期记忆通过适配接口消费，不承担 WBS 8.2/8.3 的实现。

范围以 spec 为准：不替换运行时，不改写 F073，不建设模板中心，不承诺任意崩溃自动无损续跑。

## 2. 关键约束

遵循 [constitution.md](../../../docs/constitution.md) C1–C7 和 [release-contract.md](../release-contract.md)。客户端遵循其 AGENTS.md：不新增 Recoil atom/selector/import，不引入新状态或 UI 库。

- 默认最多 5 个成员，最多同时委派 2 个；唯一 ID 最长 48 字符（字母、数字、下划线、短横线），名称 64、职责 1000、提示词 12000 字符。数量和长度限制在服务端定义，前端同约束。
- 首次提交解析实际主模型及成员模型 ID。配置只引用资源 ID、技能名称/内容摘要与连接器动作 ID，不含密钥。
- 新任务保存 schema_version 和事件协议版本；legacy 分支保持旧行为。空成员列表沿用 legacy researcher，不解释为关闭全部委派。
- 新成员保留必要的 Harness 内置文件和计划工具；可选业务工具、连接器、Skills 必须显式选择。不宣称同任务内成员共享文件是彼此安全隔离的文件系统。
- 运行图重建采用保存的配置，执行前重新验证资源可访问性；资源被撤销/内容不兼容时显式失败。
- 已通过 `uv sync --frozen --extra test --python 3.11` 建立后端环境。独立 Docker 中间件、API 7860、Worker 与真实 DeepSeek 模型联调通过；测试配置已开启多租户。E2B 未配置、DM8 未部署，不计入已通过项。

## 3. 关键决策

### 3.1 原生子 Agent 与现有 Worker

选定扩展 `create_linsight_agent`，不引入 Hermes sidecar 或独立成员调度器。项目锁定 deepagents 0.6.8，已支持成员 model/tools/middleware/skills，现有 factory 只装配固定 researcher。

原生 `task` 工具承担委派与结果回传。成员调用前通过 MemberResourceGuard 重新验证模型在线状态、技能启用状态与所选工具权限，防止只在提交时校验而放过后续撤权。每个成员独立装配可选工具和模型，不从主工具列表按名称猜归属。工具名冲突在装配时拒绝。默认 general-purpose 自动注入需要显式抑制：自定义列表中保留一个受控的同名覆盖项，并映射到用户选择的第一个成员；UI 使用稳定成员 ID 与显示名称，不暴露该内部名称。空配置维持旧路径。

每个子图保留现有 resilience、loop breaker、binary guards，不挂 ask_user 或递归委派工具。并发额度在委派工具执行 middleware 中限制，作用域为当前任务的装配实例。

独立 Worker 在调度进程建立事件循环后初始化 OpenFGA；开启权限系统时初始化失败即阻止启动，不能悄悄将已授权工具判为无权。成员 UserPayload 使用任务持久化 tenant_id，资源校验沿用同一可信租户上下文。

替代方案为全部编译自定义子图，可控制性强但接入面更大；只有锁定版本的原生机制无法可靠表达配置或事件时才重新考虑，不升级依赖来绕过问题。

### 3.2 配置快照，而非配置中心

在 `linsight_session_version` 增加 nullable `agent_config`（JsonType）。旧记录无需回填。新 Repository 提供配置相关数据访问，不新增 DAO 入口。

主工具与子工具分别装配，复用 ToolExecutor 的资源解析；在初始化前比较请求资源与授权资源，任何差集报错，不能沿用“过滤后剩下多少就运行多少”。模型使用 LLMService 现有租户/共享可见性规则；资源授权统一经 PermissionService 或其现有封装。

连接器以已注册到现有工具系统的动作 ID 绑定；F074 不接受任意 URL、脚本或凭据。如果某连接器没有注册为可执行动作，就不在选择列表伪装成可用能力。

### 3.3 成员技能显式装配

复用技能治理与 provisioning，扩展为按成员选择名单枚举，记录技能内容摘要。每个成员各自拥有 SkillsMiddleware，不能把所有成员技能并集直接传给主 Agent。

维持目录感知的 FilesystemBackend 供技能发现，WorkspaceBackend 供实际文件读取。仅向该成员自动装配选择的技能；工作区属于同一个用户任务，文件共享不被描述为角色间安全隔离。新模式需要的技能缺失或装载失败时显式报错，legacy best-effort 行为保持原样。

### 3.4 每次委派显式建立身份

目标使用委派 middleware 捕获真实 tool_call_id，把 agent_id、invocation_id 传入该次调用上下文。子图启动时发出结构化 custom 事件，把实际子图 namespace 与 invocation_id 建立映射；常规模型/工具事件据此附加身份。

实现第一项验证是锁定版本下并行调用、嵌套 async 与恢复时上下文是否可靠传递。不得根据 namespace 字符串猜 tool_call_id，不得沿用按数组位置配对；若原生上下文不足，则使用受控 runnable 包装器显式传递身份，范围限于 factory 与事件适配层。

映射未到达时，mapper 有界缓存相关帧；调用结束仍无法关联则显示“未关联活动”并报告错误，不把事件塞给其他成员。映射写入事件与持久化历史，刷新不能依赖进程内映射仍存在。

### 3.5 先写事件，再实时推送

新版任务用独立 Redis Stream 存储事件，WebSocket 只读取，不消费删除。旧任务继续旧消息键。事件 TTL 默认 7 天，滚动上限 100000 条；长任务续写刷新 TTL，任何截断均可检测。

每个新版事件具有稳定 event_id（Stream ID），首次写入后该 ID 随持久化步骤保存。重试写入须复用 producer_event_id 并原子去重，不能让应用重试制造两个业务相同的事件。客户端成功应用帧后才推进游标。

恢复快照采用最小实现：通过 Redis Lua 原子 XRANGE 返回保留的事件序列及其最后一个 event_id，客户端重置执行视图后从该序列重建投影，再从同一游标订阅。保留事件和去重键同步裁剪，避免只裁剪事件却让去重索引无限增长。重放历史 final_result 时不触发重复的查询/导航副作用。

首个 replay_init 标识完整事件历史。裁剪后标识不在流中则 complete_history=false；流整体丢失时创建 replay_gap 游标，避免 0-0 导致重置死循环。此时返回当前可用数据库历史，并明确告知过程不完整；超出保留期不承诺逐条事件无损还原。完整补播保证只适用于 complete_history=true 的保留范围。真实 Redis 故障/数据库投影窗口仍列在集成验收清单。

订阅按任务持久化 event_protocol 选择新旧路径，客户端无需另传 protocol=2。事件读前/读后均验证游标，捕获校验与读取之间的裁剪。浏览器重新 online 时重建连接；共享页面只读取已授权的脱敏历史，不发起私有任务 WS 订阅。

完成/失败任务也加载一次完整快照，但不建立终态 WebSocket。运行任务持续按 1/2/4/8/16/30 秒退避重试，成功后重置；结束或卸载时取消连接与定时器。人工回答补播读取确认步骤的 user_input。Worker 孤儿任务清扫在启动时执行，旧心跳过期后标失败并发布幂等终态事件；不是持续故障探测或自动续跑。

选择补播而不是每次重连重新执行图，因为 UI 连接不拥有任务生命周期。未来若要求进程/Redis 双故障下事件零丢失，应另行设计持久化 outbox，不在此宣称已支持。

## 4. 现状与目标数据流

### 4.1 改动前的代码基线（用于理解兼容路径）

- `workbench_impl.submit_user_question` 构造任务版本，只保存主模型、主工具与主 Skills。
- `task_exec._create_agent` 创建 WorkspaceBackend 并 materialize_session_skills，再调用 agent_factory。
- `agent_factory._build_researcher_subagent` 固定 general-purpose，无独立模型配置；主图 `store=None`。
- `task_exec.async_resume` 使用同 thread_id 和 `Command(resume=...)`；async_continue 是新增用户轮次，不等于崩溃恢复。
- `MessageStreamHandle.connect` 调用 pop_message；当前列表读取会消耗消息。
- 当前 task-message-stream 入口校验登录，尚未看到读取前任务所有权校验；F074 将通过 Service 补充授权再读取。
- 前端 `stepTree.explodeSubagentGroup` 目前按顺序匹配目标；TaskModeChatInput 为首页入口，TaskModeInput 为执行页输入。

### 4.2 目标流程

成员草稿 → 现有 submit（新增 agent_config）→ Service 验证并解析资源 → 保存配置快照 → 原队列/Worker 恢复租户上下文 → 分别装配主/子能力 → deepagents 委派 → 身份事件与工具事件 → 新版事件投影/Stream → WebSocket 与前端执行卡片。

### 4.3 模块职责与修改范围

| 路径（相对仓库根） | 目标改动 | 边界 |
|---|---|---|
| src/backend/bisheng/linsight/domain/schemas/agent_config.py（新增） | 独立定义严格成员协议与长度限制 | 不加载资源 |
| src/backend/bisheng/linsight/domain/schemas/linsight_schema.py | 引用可选 agent_config | 不改变旧请求 |
| src/backend/bisheng/linsight/domain/models/linsight_session_version.py + Alembic revision | 添加 JsonType 快照字段 | 不回填旧记录 |
| src/backend/bisheng/linsight/domain/repositories/agent_config_repository.py（新增） | 配置持久化/查询 | 不新增 legacy DAO API |
| src/backend/bisheng/linsight/domain/services/agent_config_service.py（新增） | 授权、快照解析、资源可用性 | 不直接执行 ORM |
| src/backend/bisheng/linsight/domain/services/workbench_impl.py + domain/task_exec.py | 配置提交、恢复与工具装配 | 不重写队列调度 |
| src/backend/bisheng/linsight/domain/services/agent_factory.py + skill_provisioning.py | 成员装配与独立技能发现 | 不替换模型适配器 |
| src/backend/bisheng/linsight/domain/services/stream_event_mapper.py | 显式身份关联 | 不按顺序猜成员 |
| src/backend/bisheng/linsight/domain/services/event_replay.py（新增） | Stream、投影、幂等写入与快照 | 不承担图执行恢复 |
| src/backend/bisheng/linsight/domain/services/state_message_manager.py + message_stream_handle.py | 新旧协议分支与读取 | 不双写到旧队列导致泄漏 |
| src/backend/bisheng/linsight/api/endpoints/linsight.py | 新参数、恢复快照 API、Service 授权 | 不在 Endpoint 新增 ORM |
| src/frontend/client/src/components/Linsight/Input/SubagentPicker.tsx + SubagentEditor.tsx（新增） | 抽屉与表单，复用选择器 | 不新增状态库 |
| src/frontend/client/src/components/Linsight/Input/TaskModeChatInput.tsx + TaskModeInput.tsx | 配置入口与只读快照 | 运行中不改配置 |
| src/frontend/client/src/components/Chat/AiChatInput.tsx | 可选成员控件插槽 | 日常聊天默认不显示 |
| src/frontend/client/src/components/Linsight/Execution/SubagentRunCard.tsx（新增） | 单次调用状态、结果和工具活动 | 不取代最终产物面板 |
| src/frontend/client/src/components/Linsight/Execution/execTypes.ts + stepTree.ts + ExecutionTimeline.tsx | 精确身份分组和 legacy fallback | 旧历史仍可读 |
| src/frontend/client/src/hooks/Websocket/index.tsx + API/提交 hooks | 快照加载、游标推进、去重 | 不新增 Recoil 状态 |

## 5. 已知坑

| 事实 | 风险 | 处理位置 |
|---|---|---|
| deepagents 会自动增加 general-purpose | 未声明成员仍获得默认工具 | factory 显式覆盖并保持 UI ID 映射 |
| SkillsMiddleware 需要目录条目，MinIO backend 返回文件 | 技能“选了但没生效” | provisioning + 目录感知技能 backend |
| 新成员若继承父图工具可能得到 ask_user | 子图中断无法正确冒泡，任务卡死 | factory 禁止子图直接 HITL |
| 工具初始化会过滤无权限 ID | 配置与实际执行不一致 | Service 全量校验请求集合 |
| 相同成员可同时出现多次调用 | 名称做 key 导致串线 | invocation_id 唯一分组 |
| 旧 history 写入存在合并/延迟 | 任意游标与旧快照组合造成漏帧 | event_replay 的同源快照和游标 |
| 原 Worker 启动清理会将孤儿任务标失败 | 有 checkpoint 仍不会自动接续 | 恢复测试和明确状态，不假定已有容灾 |
| 客户端 useLocalize 函数引用不稳定 | useEffect 重跑覆盖正在编辑的配置 | 草稿只按任务身份首次初始化 |
| 同任务成员可能共享工具客户端/沙箱 | 并行调用共享可变状态 | 成员工具单独实例化，沙箱调用串行化或独立执行器，验证后启用并行 |
| 独立 Worker 没有 API 的 OpenFGA 生命周期 | 普通用户提交成功但运行时授权失败 | 在 Worker 自身事件循环初始化 FGA；成员身份带任务 tenant_id |
| 分享父组件未传 readOnly | 共享页出现配置入口并请求私有快照 | TaskTurnPanel 同时检查分享路由 token，禁用私有订阅、队列操作及成员配置入口 |
| 终态仅依赖截短的旧历史 | 刷新后缺失过程 | 终态加载一次完整事件快照，裁剪时显示不完整历史提示 |
| API 停机可能超过固定重试次数 | 页面永久停留断线状态 | 运行期间持续有界退避；结束/卸载取消 |

## 6. 对外契约与依赖

### 6.1 请求与事件

- `POST /api/v1/linsight/workbench/submit` 新增可选 `agent_config={schema_version:1,subagents:[...]}`。每个成员包含 `id,name,description,system_prompt,model_id,skill_names,tool_refs,connector_refs`。model_id 为空时首次提交解析为主模型实际 ID；资源引用只存 ID。
- 同一会话走 workstation 入口时也须透传同一协议，不能只改 legacy /linsight 页面。
- 所有者可读取冻结配置；共享版本的成员配置只保留 id/name/description。分享页只显示已授权历史，不提供成员配置、暂停回答、队列取消或反馈操作。共享授权不授予私有快照、WebSocket 或执行权限。
- WS 新增 `after=<event_id>`，协议由任务配置决定；事件 envelope 在旧 event_type/data 之外增加 event_id，步骤 extra_info 增加 agent_id/invocation_id/parent_call_id。
- 新增 `GET /api/v1/linsight/workbench/replay-snapshot?session_version_id=...`，返回 `snapshot={events:[...]},last_event_id,complete_history,terminal,version`；历史不完整时另带 execute_tasks。访问在 Service 授权后执行。
- 过期/非法游标回复 `replay_reset` 控制帧及原因；游标在当前流未来范围也按非法处理，不能静默漏消息。
- 新业务错误类在 linsight 的 110 段登记，实施时按当前仓库空号分配，错误文案只写共享 locales SSOT；不在此臆定已发布编号。

### 6.2 内部边界

- AgentConfigService：验证并生成快照，执行前重新验证；不拥有模型/技能/工具对象写入权。
- EventReplayService：append（幂等）、snapshot、read_after；任务访问先经授权。Redis key 由已授权租户与任务 ID 派生，不接受客户端自定义 key。
- MemoryAdapter：`load_context(trusted_scope,goal)` / `submit_candidates(trusted_scope,source,candidates)`。无提供者时返回明确 unavailable 状态，不伪造记忆。不新增存储或画像提取实现。

### 6.3 外部依赖

| 依赖 | 风险与处理 |
|---|---|
| deepagents 0.6.8 / LangGraph | 原生自定义事件与上下文传播必须验证；失败则受控 runnable 显式关联 |
| ToolExecutor、LLMService、PermissionService | 过滤语义和共享模型可见性不能当作完整配置验证；执行期重验 |
| Skills 治理、SkillStore | 名称不等于内容版本；快照记录摘要，恢复时验证 |
| Redis 持久化与保留策略 | TTL/裁剪/数据丢失都可能使回放不完整；明确回退语义 |
| E2B、MinIO | 文件需 copy-in/out；沙箱进程状态不等于持久文件，不承诺进程恢复 |
| WBS 8.2/8.3 | 接口未就绪时仅跑适配测试，长期记忆验收保持待依赖 |

## 7. 验证、上线与回滚

使用有授权资源的租户测试账号，访问 `/workspace/linsight/new`，配置两个成员完成资料检索和分析任务；在另一浏览器/刷新后观察事件，断网再连，等待澄清时重启 Worker 再回答。另用第二租户和同租户无权账号验证拒绝访问。禁止使用生产凭据写入测试文档。

单元测试覆盖协议/集合校验、成员工具隔离、调用乱序和去重；集成测试覆盖 Redis 原子写入/截断、快照一致性和恢复；E2E 验证真实模型委派、权限、沙箱文件、人工中断与刷新。测试结果分别记录，不把 stub 测试标成真实环境验收。

质量命令：backend 相关 pytest、ruff、alembic 单 head 检查、arch-guard；frontend 根目录 pnpm lint、pnpm typecheck、pnpm check-i18n；受影响文件修复历史冻结违规，避免新增 suppressions。

上线先加列，再部署兼容新旧协议的后端/Worker，最后部署客户端。新模式由非空成员配置显式启用，任务快照保存执行协议版本；空配置走 legacy 分支。尚未建设独立管理员开关，回滚前必须先停止新模式任务提交并排空运行任务。回滚应用时保留 nullable 列与任务数据；drop 列仅在无新模式任务且业务确认丢弃快照后执行，不能把破坏性 downgrade 当常规回滚。

日志记录 tenant/task/agent/invocation/event ID 与拒绝原因，避免输出凭据与完整私有提示词。进程异常验证不通过时保留显式失败状态，不上线自动恢复开关。

## 8. 后续边界

受控崩溃恢复需要独立故障注入证据：原 Worker 确认退出、单实例接管、checkpoint 有效、配置与文件可恢复，且工具重放安全。未知外部写入一律不自动恢复；完整容灾不以本期消息补播代替。

长期记忆与全平台连接器管理依赖其 Owner；不为满足演示创建旁路存储。只有明确要求跨任务成员模板或成员独立 OS 隔离时再扩展。

## 修订记录

| 日期 | 改动 | 原因 |
|---|---|---|
| 2026-10-13 | 首版设计草稿；实现尚未开始 | 用户确认规格，进入设计阶段 |
| 2026-10-14 | 同步终态快照、重连退避、启动清扫失败事件、Worker FGA 生命周期与分享路由只读约束 | 真实模型、故障与多租户联调发现的缺陷修复；不扩大自动崩溃恢复或长期记忆范围 |

## 9. 实施补充与验证边界

- 新配置错误码为 11071，已在共享 locales 三语源登记并生成产物。
- AgentConfigRepository 复用已有 tenant-aware DAO 读取资源；任务整行创建沿用已有写入链，不新增 DAO 方法。
- 私有任务归属校验集中到 PermissionService.check_private_session_owner；不创建新共享 FGA 资源，不赋予管理员跨用户执行权限。工具授权仍走既有 ToolPermissionService / PermissionService 链。
- 新模式发现代码工具使用本机执行器时明确拒绝；E2B 执行器绑定当前 WorkspaceBackend。成员技能先复制再创建沙箱工具，使技能资产进入 copy-in 候选。子 Agent 可关闭资源登记到 task_exec 清理路径。
- 长期记忆通过 `LinsightWorkflowTask(memory_adapter=provider)` 注入 MemoryAdapter；默认 UnavailableMemoryAdapter 不伪造保存成功，未提供具体记忆服务。主 Agent 可提交候选，子 Agent 无该写入工具。
- 架构检查发现 linsight 旧入口跨导入 share_link.api.dependencies，已将相同依赖移到 share_link/dependencies.py，旧 API 文件保留兼容重导出；分享语义不变。
- 所有新增组件和 helper 独立成小文件；既有 ChatView/useAiChat 等大型文件仅透传配置，不进行无关的全量重构。
- MySQL、Redis、OpenFGA、真实模型委派、Skill/HTTP 连接器、撤权阻断、跨租户 API、分享只读页面与历史裁剪提示已有真实证据，见 e2e-report.md。DM8 与 E2B 待验；原始 WBS 的任意崩溃续跑仍不满足，不开启自动重放。

## 新基线适配（2026-10-14）

开发基线改为 `origin/feat/2.7.0/beijing-ns-llm-guardrail`（`f1b49c51f`）。以旧基线、备份和目标分支三方合并，不覆盖团队新增能力。迁移父节点更新为 Alembic 实测的 `f064_field_comment_override`，维持单 head。目标分支新增 intent 时间线节点，已回答澄清应为终态，不能按普通 step 访问。测试文件显式导入 jest-dom matcher 类型，避免依赖旧基线的隐式类型引入。当前验证以 rebase-validation.md 为准，旧环境联调证据不能替代新基线真实服务复测。
