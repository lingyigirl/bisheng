# Release Contract — v2.7.0

> 本文件是版本级领域归属与全局约束的权威来源。
> **所有 spec.md 在动笔前必须先阅读本文件。**
> 每次 spec 评审时，必须对照本文件检查一致性。

---

## 表 1：领域对象归属

每个领域对象只能有一个 Owner Feature，负责定义该对象的写入行为
（创建、更新、删除）。其他 Feature 只能"读取"或"引用"该对象。

| 领域对象 | Owner Feature | 说明 |
|---------|--------------|------|
| LLMServer / LLMModel（`minimax` 服务商类型的 TTS 客户端注册与参数处理） | F045-minimax-tts-adapter | 在既有 `llm_server` / `llm_model` 表与 CRUD 之上，为 `minimax` 服务商类型新增 TTS 客户端注册（`MiniMaxTTSClient`）；**不拥有** LLMServer / LLMModel 对象本身（沿用 v2.5.0 LLM 管理归属），不新增表 / 字段 / DAO 入口 |
| —（无新增领域对象） | F045-minimax-tts-adapter | 本特性仅新增一个 TTS 协议客户端 + 一处注册 + 前端类型白名单放行；错误码复用既有 TTS 错误码（`common/errcode/server.py` 的 Tts* 系列），不新增错误码 |
| application_process 遥测事件的 `status` 字段（运行成功/失败口径） | F046-agent-monitoring | 扩展 ApplicationProcessEventData schema 与各上报点（workflow Celery 任务 finally、assistant/workstation/linsight 聊天结束处）写入 status；历史事件不回填 |
| 监控中间表（智能体运行聚合、按智能体 × 时间桶） | F046-agent-monitoring | 新增 ES 中间表（沿用 BaseMidTable 范式）+ beat 增量同步任务 + dashboard_dataset 数据集声明；仅本 Feature 写入 |
| 调用日志查询端点（只读） | F046-agent-monitoring | 新增只读查询 API（查原始遥测事件），不拥有任何既有领域对象 |
| 应用收藏记录（user_app_favorite） | F047-agent-plaza | 新增收藏表（user_id + app_id + app_type + tenant_id，用户×应用唯一约束防重）；仅本 Feature 写入；广场「我的收藏」与收藏状态读取 |
| 广场统计只读查询端点（应用访问统计） | F047-agent-plaza | 只读聚合查询 F046 的 `mid_agent_run_dtl`（访问量/成功率/平均响应时间），不拥有任何既有领域对象 |
| 流程版本对比查询端点（只读） | F051-flow-version-compare | 新增只读查询 API（对 `flow_version` 两个版本做结构化差异：节点增/删/改 + 字段级新旧值）；不拥有 FlowVersion 对象本身，不新增表 / 字段 / 错误码（复用 10500） |
| 节点执行记录表（智能体运行中各节点的开始/结束、状态、失败原因、输入/输出数据） | F052-agent-monitor-transactions | 新增独立持久化表（不进入 chatmessage 聊天历史）；写入点在工作流型智能体节点执行回调；仅本 Feature 写入；既有聊天历史接口不受影响 |
| application_process 遥测事件的 `input_snapshot` 字段（运行输入快照，交易标识） | F052-agent-monitor-transactions | 扩展 ApplicationProcessEventData schema 与各上报点写入输入快照（≤200 字符截断）；与 F046 的 `status` 字段互不冲突（各自字段、各自归属） |
| 智能体监控查询端点（交易列表 / 节点明细，只读） | F052-agent-monitor-transactions | 新增只读查询 API：交易列表读遥测事件（含 input_snapshot），节点明细读节点执行记录表 + 同 trace 的模型调用事件；不拥有 F046 的任何领域对象 |
| evaluation 表的 `compare_group_id` 字段（版本对比评测分组标识） | F053-agent-version-eval-compare | 新增可空列：同一对比组的两条评测共享一个 uuid；仅本 Feature 写入 |
| 评估创建端点扩展（双版本参数 + 数据集复用参数） | F053-agent-version-eval-compare | 扩展现有 POST /api/v1/evaluation（既有 evaluation 模块无 owner；本 Feature 增加对比组创建与数据集复用能力，不改变单版本评测行为） |
| 版本对比查询端点（只读） | F053-agent-version-eval-compare | 新增只读查询 API（读同组两条评测的结果 CSV，计算总分差值 + 逐题对比）；不拥有 F051/F052 的任何领域对象 |
| 评测执行链的表单型提交分支（form_input 工作流按数据集表单值提交并继续执行） | F054-eval-form-workflows | 修改评测执行函数（`execute_workflow_get_answer` 的 form_input 分支）；不改变问答型分支行为；仅本 Feature 写入该分支逻辑 |
| 评测数据集 `form_values` 列的解析与校验 | F054-eval-form-workflows | 新增可选 CSV 列（JSON，按表单项 label 引用）；创建评测时校验；仅本 Feature 定义其语义 |
| 知识库/文档的时效性字段（生效日期 / 失效日期）与权重、优先级配置 | F055-risk-compliance-knowledge | 扩展既有 knowledge / knowledgefile 对象：新增时效与权重/优先级字段及其写入行为；检索期的时效过滤注入与加权排序由本 Feature 定义；不拥有 knowledge/knowledgefile 对象本身（沿用既有知识库模块归属） |
| 工作流知识检索节点（rag / knowledge_retriever / qa_retriever）的检索注入扩展（时效过滤、标签过滤、权重排序、top_k / 分数阈值参数） | F055-risk-compliance-knowledge | 扩展既有检索节点的参数与召回后处理（`workflow/common/knowledge.py` 检索构建链路）；不改变未配置新参数时的既有行为（AC-11/AC-13 兼容）；仅本 Feature 写入该扩展逻辑 |
| 敏感词策略的对话场景（business_type=`chat_input` / `chat_output` 的配置语义与检测行为） | F056-llm-guardrail | 扩展 v2.6.0 F036 既有的 `SensitiveWordPolicy` 对象到对话输入/输出场景（新增枚举值 + `extra_config` 中 action 语义 + 检测点接入）；**不拥有**策略表本身（F036 归属），不改变知识库文件解析 / 订阅文章两个既有场景的行为 |
| 敏感词命中日志表（sensitive_word_hit_log） | F056-llm-guardrail | 新增独立表（租户 / 方向 / 应用 / 命中词摘要 / 动作 / 时间）；仅本 Feature 写入；平台日志页只读展示 |
| 组织知识库的部门归属（`knowledge.department_id`） | F057-knowledge-department-ownership | 扩展既有 `knowledge` 对象：新增可空归属列及其写入行为（指派 / 取消 / 部门删除联动）；**不拥有** `knowledge` 对象本身（沿用既有知识库模块归属），不新增表、不新增错误码。归属可见性在**读取时**求值（`可见 = FGA 授权 OR 创建者本人 OR 部门归属`），不写入任何 FGA 元组 |
| 知识库列表的部门筛选参数与部门维度统计端点（只读） | F057-knowledge-department-ownership | 扩展 `GET /api/v1/knowledge` 的筛选查询参数 + 新增 `GET /api/v1/knowledge/department_stats` 只读聚合；筛选只收窄展示，不授予任何权限 |
| 平台级敏感词库词条（`platform_sensitive_word`） | F059-chat-compliance-and-answer-enhancements | 新增独立全局表（无租户列：词条内容 / 来源标记 / 创建人 / 时间）；仅本 Feature 写入；超管维护，所有租户只读引用；命中归属与词库内容不向租户暴露 |
| 平台级敏感词策略（`platform_sensitive_word_policy`） | F059-chat-compliance-and-answer-enhancements | 新增独立全局表（启用位 / 输出动作 / 固定话术）；**不并入** F036/F056 的 `sensitive_word_policy` 租户表（该表 `tenant_id` 非空且受租户自动过滤，混存全局行会破坏 C3 隔离口径）；仅本 Feature 写入；读取路径显式 `bypass_tenant_filter` |
| 敏感词命中日志的 `source` 字段（命中来源：`tenant` 租户策略 / `platform` 平台强制） | F059-chat-compliance-and-answer-enhancements | 扩展 F056 的 `sensitive_word_hit_log` 新增可空列；仅本 Feature 写入该字段；**不拥有**该表本身（F056 归属），不改变既有行的语义（历史行按 `tenant` 回填）。平台来源的命中词在租户侧查询响应中以占位返回 |
| 评价事件表（`message_feedback_event`） | F059-chat-compliance-and-answer-enhancements | 新增 append-only 事件表（租户 / 用户 / 消息 ID / 应用 / 变更前后评价 / 差评原因 / 时间）；每次状态变更写一行（含取消与改评）；**不拥有** `chatmessage.liked` / `chatmessage.remark`（既有归属），仅在写入时读取其变更前后值；租户读本租户、超管读全局 |
| 场景热词生成端点（即时生成，不落库） | F059-chat-compliance-and-answer-enhancements | 新增即时生成 API（当轮会话内展示，不持久化）；输入为当轮用户提问与 AI 回答文本，**不检索知识库**；不拥有任何既有领域对象，不新增表 |
| 关联问题生成端点（即时生成，不落库） | F059-chat-compliance-and-answer-enhancements | 新增即时生成 API（当轮会话内展示，不持久化）；只读调用 F055 的检索构建入口与既有 `recommend_question` 的 LLM 槽位 `qa_similar_model`；不拥有任何既有领域对象，不新增表 |
| 对话回答区的热词列表、关联问题列表与免责提示（前端展示态） | F059-chat-compliance-and-answer-enhancements | 扩展 `AiMessageBubble` 回答区（热词 → 关联问题 → 友情提醒，顺序固定）；纯展示，不新增领域对象；友情提醒文案为 i18n 词条（三语），无服务端配置项 |

**规则**：
- 非 Owner Feature 的 AC 中不得出现其他对象的"创建/修改/删除"行为，只能"读取"或"调用" Owner 的 Service
- 新增领域对象时必须先更新本表

---

## 表 2：跨 Feature 不变量（INV-N）

全局业务约束，任何 spec 的 AC **不得与之矛盾**。

| ID | 不变量描述 | 涉及领域对象 | 来源 spec |
|----|-----------|------------|---------|
| INV-8 | 系统默认模型配置（知识库 / 工作台 / 助手 / 评估 / 工作流）中的模型 ID 必须是本租户内可见（自有或 Root 共享）的 `llm_model` 行，且其 `model_type` 与配置槽位匹配（如工作台 `asr_model` 必须为 `asr` 类型、`tts_model` 必须为 `tts` 类型） | llm_model, tenant_system_model_config | F022（v2.6.0 遗留） |
| INV-9 | 服务商配置中的密钥类字段（`openai_api_key` 等）在读取响应中必须脱敏；写入路径沿用既有 CRUD 与审计 | llm_server | v2.6.0 LLM 管理 |
| INV-10 | 平台级强制词库对所有租户的对话入口无条件生效：其检测独立于租户策略的启停，不受租户管理员修改或关闭；同一输入同时命中平台词库与租户词表时，以平台词库的判定与话术为准；平台词库内容不得经由租户侧任何界面（含命中日志）被反向获取 | platform_sensitive_word_policy, platform_sensitive_word, sensitive_word_hit_log | F059 |
| INV-11 | 对话留痕数据的存储边界：命中日志与评价事件均只存标识与摘要（命中词及计数、评价类型与变更前后值、消息 ID），**不存对话原文**；该边界对后续所有扩展留痕的 Feature 生效 | sensitive_word_hit_log, message_feedback_event | F056（沿用）/ F059 |

> 注：INV-8 / INV-9 为 v2.6.0 既有约束的回填登记（原 v2.6.0 release-contract 未编号记录，此处补记，与 F022/F020 行为一致）。编号续接 v2.6.0 的 INV-1 ~ INV-7。F045 不改动这些不变量，仅在其框架内注册新的 TTS 客户端。
>
> INV-10 由 F059 引入，**放宽了 F056 的三条既有 AC**（AC-02「未启用不检测」、AC-04「租户策略隔离」、AC-17「未配置行为不变」的绝对措辞）——F056 spec 已同步加「除平台强制词库外」的范围限定。Impacted Specs 见变更历史。

**规则**：
- 新增不变量：先在此表追加，再写 AC
- 修改不变量：必须列出 Impacted Specs 清单，逐一回写并重新评审
- 冲突检测：若 AC 与不变量矛盾，spec 评审不通过

---

## 表 3：Feature 依赖图

| Feature | 依赖（必须先完成） | 说明 |
|---------|-----------------|------|
| F045-minimax-tts-adapter | v2.6.0 LLM 管理（F020/LLM CRUD 已上线） | 依赖既有 `/api/v1/llm` CRUD、`BishengTTS` 工厂与工作台 TTS 调用链（`/llm/workbench/tts`） |
| F051-flow-version-compare | 无（既有流程版本机制已上线） | 只读使用既有 `flow_version` 数据与版本查询接口，不依赖其他 feature 的新增能力 |
| F052-agent-monitor-transactions | F046-agent-monitoring（运行级遥测事件与租户过滤口径已上线） | 只读复用 `base_telemetry_events` 的 application_process/model_invoke 事件与 F046 的租户用户集合过滤口径；不修改 F046 的任何领域对象 |
| F053-agent-version-eval-compare | 无（既有 flow_version 版本列表 + 评估执行链已上线） | 复用版本列表 API 与「按版本执行评测」能力；不依赖 F051/F052 |
| F054-eval-form-workflows | F053-agent-version-eval-compare（对比组机制已上线） | 复用 F053 的对比组与对比端点；只改评测执行链的表单分支，不依赖 F051/F052 |
| F055-risk-compliance-knowledge | 无（既有知识库模块 / 工作流检索节点已上线） | 扩展既有知识库对象与检索链路；复用 metadata 过滤、双引擎（Milvus+ES）AND 组合机制；不依赖其他 feature 的新增能力 |
| F056-llm-guardrail | 无（v2.6.0 F036 sensitive_word 模块已上线） | 扩展既有策略基础设施到对话链路；复用 AC 自动机 / 策略表 / 管理 API；不依赖本版本其他 feature 的新增能力 |
| F057-knowledge-department-ownership | 无（既有 department 模块与知识库权限体系已上线） | 复用既有部门树（`department.path` 物化路径）、`PermissionService` 五级短路的隐式回退位、知识库列表的游标分页链路；不依赖本版本其他 feature 的新增能力 |
| F059-chat-compliance-and-answer-enhancements | F056-llm-guardrail（对话围栏链路与命中日志已上线）、F055-risk-compliance-knowledge（检索构建入口与标签体系已上线） | 复用 F056 的 `ChatGuardService` / 命中日志表 / 平台安全围栏页；复用 F055 的 `retrieve_knowledge_documents` 与标签过滤；复用既有 `recommend_question` 的 LLM 槽位 `qa_similar_model`（场景热词与关联问题共用该槽位）。本 Feature 引入 INV-10，据此**放宽** F056 的 AC-02 / AC-04 / AC-17（见 Impacted Specs） |

---

## 已分配模块编码（MMMEE）

> 新 Feature 分配错误码时，必须检查此表避免冲突。

| 模块编码 (MMM) | 模块 | Owner Feature |
|----------------|------|---------------|
| —（F045 不新增错误码，复用 `common/errcode/server.py` Tts* 系列） | — | — |
| —（F051 不新增错误码，复用 `common/errcode/flow.py` 10500 NotFoundVersionError） | — | — |
| —（F052 不新增错误码：窗口/参数校验沿用 F046 call-logs 的 HTTP 400 口径） | — | — |
| 112 | 敏感词 / 安全围栏（`common/errcode/sensitive_word.py` 新增：11201=输入命中拦截、11202=输出命中截断） | F056-llm-guardrail |
| —（F057 不新增错误码：归属写入的拒绝路径复用既有 HTTP 错误信封 403） | — | — |
| 113 | 平台敏感词库（`common/errcode/platform_sensitive_word.py` 新增：导入文件无法解析 / 无有效词条 / 词条不存在；命中拦截复用 F056 的 11201、11202 不改语义） | F059-chat-compliance-and-answer-enhancements |

---

## 变更历史

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-08-26 | 初始版本：登记 F045-minimax-tts-adapter | — |
| 2026-09-02 | 登记 F051-flow-version-compare（只读对比端点，不新增领域对象 / 错误码） | — |
| 2026-09-05 | 登记 F052-agent-monitor-transactions（节点执行记录表 + input_snapshot 事件字段 + 只读监控查询端点） | — |
| 2026-09-05 | 登记 F053-agent-version-eval-compare（evaluation.compare_group_id 字段 + 创建端点扩展 + 只读对比端点） | — |
| 2026-09-05 | 登记 F054-eval-form-workflows（评测执行链表单型分支 + 数据集 form_values 列语义） | — |
| 2026-09-06 | 登记 F055-risk-compliance-knowledge（知识库/文档时效与权重/优先级字段 + 检索注入） | — |
| 2026-09-07 | 登记 F056-llm-guardrail（敏感词策略 chat_input/chat_output 场景扩展 + 命中日志表 + 错误码模块 112） | — |
| 2026-09-16 | 登记 F057-knowledge-department-ownership（knowledge.department_id 归属列 + 读时可见性求值 + 列表部门筛选参数 + 只读部门统计端点；不新增错误码） | — |
| 2026-09-17 | 登记 F059-chat-compliance-and-answer-enhancements（平台级敏感词库词条表 + 平台级策略表 + 命中日志 source 字段 + 评价事件表 + **场景热词生成端点** + 关联问题生成端点 + 回答区展示态 + 错误码模块 113）。**新增不变量**：INV-10（平台强制词库效力与优先级）、INV-11（留痕存储边界）。**Impacted Specs**：F056-llm-guardrail — AC-02 / AC-04 / AC-17 需加「除平台强制词库外」范围限定，本次已回写；F036 的知识库文件解析 / 订阅文章两个场景**不受影响** | F056, F036 |
