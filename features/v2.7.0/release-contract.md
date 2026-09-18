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
| 数据源（`db_datasource`） | F061-datasource-qa | 新增独立表（租户 / 名称 / 引擎 / 主机·端口·库名·账号 / 密码密文 / 启用位）；仅本 Feature 写入；密码 Fernet 加密存储、任何读取路径不回显（见 INV-19）。**不并入** `Knowledge` 表、**不进入** `KnowledgeTypeEnum`、不产生向量、不创建 Milvus 集合 / ES 索引 |
| 数据源表元数据（`db_datasource_table`） | F061-datasource-qa | 新增独立表（数据源引用 / 表名 / 结构快照（字段名·类型·库内注释）/ 人工业务描述 / 暴露开关）；仅本 Feature 写入；重新采集只刷新结构快照，**不覆盖人工业务描述**；**不采集表内真实数据行**（无样例值采样） |
| 数据源查询执行记录（`db_query_log`） | F061-datasource-qa | 新增独立表（租户 / 操作人 / 数据源 / 涉及的表 / 执行的语句 / 执行状态 / 降级标记 / 时间）；仅本 Feature 写入；**只存语句与状态，不存结果集数据**（见 INV-19） |
| 数据源查询失败留档（`db_query_failure`） | F061-datasource-qa | 新增独立表（租户 / 数据源 / 失败语句 / 错误类别 / 供应商错误码 / 时间）；供管理员定位需补全描述的表；**不存对话原文**——遵守 INV-11 的留痕存储边界，仅存标识与摘要 |
| AuthorizationModel 中**新增的数据源资源类型定义**（OpenFGA type definition） | F061-datasource-qa | 在 F004 的授权模型中新增一类受控资源（**与知识库同级**，非其子类型）；仅本 Feature 写入该类定义；**不拥有** AuthorizationModel 本身（F004 归属），**不修改**任何既有资源类型的定义与任何既有元组；须按 v2.5.0 INV-15 完成权限接入（创建时 `PermissionService.authorize()`、变更前 `check()`） |
| 工作流 Agent 节点的数据源配置（由「内联连接信息」改为「引用数据源」） | F061-datasource-qa | 扩展既有 Agent 节点参数：新增「选择数据源」形态；**保留对既有内联 `sql_agent` 配置的兼容读取**（既有流程对外行为不变，且不再允许经内联新建连接）；不拥有 `node_params` 本身（既有归属） |
| 数据源检索工具（可被模型调用的工具形态，运行时不落库） | F061-datasource-qa | 数据源以工具形态进入对话与工作流 Agent 节点，**与知识库检索工具同层**（同一轮内模型可自由选择其一或两者）；不拥有知识库任何对象，不改变知识库检索链路与授权形状 |
| 抽取模板（`doc_extract_template`） | F058-doc-extract-template | 新增独立表（租户 / 名称 / 文档类型标识 / 适用范围 / 字段定义 / 呈现形式 / 启用位）；仅本 Feature 写入。**2026-09-17 由 v2.8.0 移入本版本** |
| 抽取记录（`doc_extract_run`） | F058-doc-extract-template | 新增独立表（模板引用 / 分类置信度 / 预处理链路 / 抽取字段 / 渲染结果 / 状态）；仅本 Feature 写入；**仅在「已进入抽取后」落记录**——业务抽取的成功与失败均落，试抽取不落，前置校验被拒（未进入抽取）不落。**2026-09-17 由 v2.8.0 移入本版本** |

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
| INV-12 | 抽取模板按租户隔离：`doc_type` 仅在同租户内唯一；模板的读取、分类候选集构建、抽取执行均限定在当前租户内，不跨租户生效 | doc_extract_template | F058（**2026-09-17 由 v2.8.0 移入**） |
| INV-13 | 抽取模板的**呈现形式**中出现的每个字段变量，必须在该模板的**字段定义**中存在；二者在保存时即校验一致，不得存在运行期才暴露的悬空引用 | doc_extract_template | F058（**2026-09-17 由 v2.8.0 移入**） |
| INV-14 | 抽取执行**不修改**源文档、不写入知识库、不产生向量；本 Feature 对既有知识库数据只读 | doc_extract_run, knowledge | F058（**2026-09-17 由 v2.8.0 移入**） |
| INV-15 | 抽取模板的创建与变更**必须留痕**（操作者 / 时间 / 模板名）。模板是管理员可改的配置，改了会直接改变后续所有同类型文档的抽取结果，因此「谁改的」必须可事后查阅 | doc_extract_template | F058（**2026-09-17 由 v2.8.0 移入**） |
| INV-19 | 数据源查询的安全与留痕边界：①连接凭据加密存储、任何读取响应与日志不回显；②查询仅放行单条 SELECT，拒绝多语句 / 写操作 / 结构变更 / 加锁读取，并受行数上限与超时约束；③执行记录与失败留档**只存语句与状态摘要，不存结果集数据、不存对话原文**（延续 INV-11）；④平台**不施加行级数据过滤**——可见行范围等于所配置数据库账号的权限范围，数据源被显式授权给谁即对该主体全量开放；⑤查询生成的答案文本受既有对话合规约束保护（不绕过敏感词关卡） | db_datasource, db_datasource_table, db_query_log, db_query_failure | F061 |

> 注：INV-8 / INV-9 为 v2.6.0 既有约束的回填登记（原 v2.6.0 release-contract 未编号记录，此处补记，与 F022/F020 行为一致）。编号续接 v2.6.0 的 INV-1 ~ INV-7。F045 不改动这些不变量，仅在其框架内注册新的 TTS 客户端。
>
> INV-10 由 F059 引入，**放宽了 F056 的三条既有 AC**（AC-02「未启用不检测」、AC-04「租户策略隔离」、AC-17「未配置行为不变」的绝对措辞）——F056 spec 已同步加「除平台强制词库外」的范围限定。Impacted Specs 见变更历史。
>
> INV-19 由 F061 引入，**不放宽任何既有不变量**。其 ③ 为 INV-11 边界在本特性的具体化（本特性不新增例外，仅遵守）；其 ④ 为**显式声险**：平台不提供行级过滤，安全责任落在「所配置数据库账号的权限」与「数据源的授权治理」两处外部因素上，平台侧不作技术强制——该风险由本特性显式接受，后续任何 Feature 若引入行级过滤能力，应回写本行并重新评审。
>
> **跨版本依赖登记**：F061 消费 v2.5.0 **F004-rebac-core** 拥有的 `AuthorizationModel`——新增一类数据源资源类型的 type definition，并按 v2.5.0 **INV-15** 完成权限接入。这是本 Feature 唯一跨越版本边界的对象写入，特此登记（v2.5.0 契约的 INV-15 资源清单为**开放枚举**，新增类型即扩该清单，无需修改 v2.5.0 既有文本；仅需保证：创建调 `authorize()`、变更前调 `check()`）。

> INV-12 ~ INV-15 由 F058-doc-extract-template 引入，**2026-09-17 随该 Feature 的版本归属调整由 v2.8.0 移入本版本**。不变量内容与其来源 Feature 均未变化，仅登记位置改变；编号为全局编号，**不因移入而重新编排**。
>
> **本版本 INV 全貌**：INV-8 / INV-9（v2.6.0 回填）、INV-10 / INV-11（F059）、INV-12 ~ INV-15（F058）、INV-19（F061）。**注意编号不连续是正常的**：INV-16 ~ INV-18 由 v2.8.0 的 F060-term-library 持有，未移入本版本。**不要按编号是否连续推断是否有遗漏，也不要仅凭某个 INV 编号落在本版本的区间内就认为它属于本版本**——以本表实际登记为准。

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
| F061-datasource-qa | **跨版本**：v2.5.0 F004-rebac-core（OpenFGA 授权模型与 `PermissionService` 已上线）、v2.5.0 F005-role-menu-quota（独立权限点所需的菜单权限机制已上线）。**本版本**：F056-llm-guardrail（对话链路输入/输出缝合点已上线）、F059-chat-compliance-and-answer-enhancements（回答区附属信息的展示位已上线） | **复用（跨版本）**：F004 的 `AuthorizationModel`（新增一类数据源资源类型，按 v2.5.0 INV-15 接入 `PermissionService.authorize()` / `check()`；见 INV-19 后的跨版本依赖登记）、F005 的菜单权限机制（承载数据源的独立管理权限点）。**复用（本版本）**：F056 的对话合规关卡（数据库生成的答案文本同样受限，见 INV-19 ⑤）、F059 的回答区展示位（引用锚点与展开区需与其共存不冲突）。**复用（既有平台机制，非 feature 依赖）**：`tenant_system_model_config` 槽位机制与 Root 租户回退（承载 SQL 生成的独立模型槽位）、既有 LLM 工厂、既有知识库检索工具形态（数据源以同层工具形态呈现，**不修改知识库任何对象**）。**开发基线**：本特性所在的项目交付集成基线（特性间为线性叠加，非各自独立分支）——见 spec.md 依赖栏 |
| F058-doc-extract-template | 无（既有知识库解析链路 / LLM 管理 / 多租户体系已上线） | 复用既有 loader 栈与 `TempFilePipeline`（`knowledge/rag/`）、`settings.get_knowledge()` 的 provider 配置、`LLMService.get_bisheng_llm_sync` 与 `TenantSystemModelConfig` 槽位机制、`UserPayload.get_tenant_admin_user` 鉴权；不依赖本版本其他 feature 的新增能力。**2026-09-17 由 v2.8.0 移入本版本**（该特性**已实现**，此行为登记迁移，非新增设计） |

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
| 116 | 文档抽取（`common/errcode/doc_extract.py` 新增：11601 指定文档类型不可用（不存在或已停用）、11602 类型标识重复、11603 模板定义非法（字段定义非法 / 名称为空 / 字段定义为空）、11604 呈现形式悬空引用、11605 无可用识别服务、11606 类型未识别（含无可判定候选、置信度低于阈值）、11607 字段校验失败、11608 内容获取失败（模型未配置 / 模型调用失败或超时 / 识别服务调用失败）、11609 文档不可读或为空、11610 文档超出体量上限、11611 模板不存在或无权限访问） | F058-doc-extract-template（**2026-09-17 由 v2.8.0 移入**；该模块**已实现并落盘**） |
| 117 | 数据源 / 数据库问答（`common/errcode/datasource.py` 新增：连接失败 / 引擎不支持 / 表结构采集失败 / 语句被安全策略拒绝 / 语句执行失败 / 执行超时 / 结果超行数上限 / 数据源不存在或无权限访问 / 数据源名称重复 / 模型槽位未配置） | F061-datasource-qa |

> **模块编码分配口径（F061 / F058 补充）**：117 在分配前已扫**三个来源**并确认全空——① `src/backend/bisheng/common/errcode/*.py` 无 117xx；② 全部版本 `release-contract.md` 无 117 占用；③ `src/frontend/packages/locales/src/api_errors/*.json` 无 117xx（该处曾有后端不存在的遗留码，F058 的 11403 撞车即源于漏扫此来源）。
> **本版本编号现状**：v2.7.0 当前占用 **112**（F056）、**113**（F059）、**116**（F058）、**117**（F061）。其中 **114** 为前端 403 跳转哨兵（`11403`，**非本版本分配**）、**115** 属 v2.8.0（F060 术语库）。
> **编号连续性说明**：本表出现 `112 / 113 / 116 / 117` 的间隔**不是空缺**——114 为前端遗留哨兵、115 由 v2.8.0 的 F060 持有。**编号全局共享，不按版本连续**；分配前必须按上述三来源扫描确认（见 v2.8.0 契约同口径说明）。

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
| 2026-09-17 | **F058-doc-extract-template 由 v2.8.0 移入本版本**（使用户可见的交付版本与特性登记版本一致）。自 v2.8.0 移入：表1 两行（`doc_extract_template` / `doc_extract_run`）、表2 四条（**INV-12 ~ INV-15**）、表3 一行（F058 依赖图）、错误码表一行（**116**）。**编号不变**：F058 仍为 F058，仍占用 INV-12~INV-15 与错误码 116，仅登记位置改变。**本特性已实现**（Wave A–G 已提交，迁移 `f058_doc_extract` 已落盘），本次为**登记迁移，不涉及任何代码改动**；v2.8.0 保持存在（由 F060 及后续特性继续使用） | 无（纯版本归属调整） |
| 2026-09-17 | 登记 F061-datasource-qa（数据源表 + 数据源表元数据表 + 查询执行记录表 + 查询失败留档表 + AuthorizationModel 新增数据源资源类型 + 工作流 Agent 节点数据源配置收敛 + 数据源检索工具 + 错误码模块 117）。**新增不变量**：INV-19（数据源查询的安全与留痕边界，**不放宽任何既有不变量**）。**跨版本依赖**：消费 v2.5.0 F004-rebac-core 的 `AuthorizationModel`（新增一类资源 type definition）与 F005-role-menu-quota 的菜单权限机制，按 v2.5.0 INV-15 完成权限接入——**不修改 v2.5.0 任何既有文本**（其 INV-15 资源清单为开放枚举）。**Impacted Specs**：无（本特性不修改任何既有 Feature 的 AC；对 Agent 节点既有内联 `sql_agent` 配置改为**兼容读取**，既有流程对外行为不变）。**明确不涉及**：知识库对象 / `KnowledgeTypeEnum` / 知识库检索链路 / 知识库授权形状（F055 / F057 不受影响）；F004 的既有资源类型与既有元组不受影响 | 无（跨版本消费 F004 / F005，均为只读复用 + 一类新增 type definition） |
