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

> 注：INV-8 / INV-9 为 v2.6.0 既有约束的回填登记（原 v2.6.0 release-contract 未编号记录，此处补记，与 F022/F020 行为一致）。编号续接 v2.6.0 的 INV-1 ~ INV-7。F045 不改动这些不变量，仅在其框架内注册新的 TTS 客户端。

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
