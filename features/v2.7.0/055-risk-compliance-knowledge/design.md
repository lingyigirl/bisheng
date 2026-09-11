# Design: 风险合规知识库（055-risk-compliance-knowledge）

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-06

---

## 1. 目标与非目标

- **目标**：让既有知识库/检索链路支撑风险合规场景的四个硬需求——四大分类、时效性（生效/失效日期）、知识权重与调用优先级、检索质量参数（top_k/阈值）——并提供业务人员自检闭环（命中测试 + 标签中心）。
- **非目标**：重建知识库底座（上传/解析/双写/权限全部复用）；chunk 级时效；外部数据定时抓取管道；节点级动态权重。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7（分层 / 双 DB / 多租户 / 权限 / 错误码）。
- **零回归**：未配置新参数的既有知识库与工作流，检索行为必须与现在完全一致（AC-11/AC-13）。
- **双引擎同构**：时效/标签过滤必须同时注入 Milvus expr 与 ES query，两边口径一致（否则混合检索两侧召回不一致）。
- **Milvus schema 演进**：document 级新字段要进已存在数据的 collection——用 add field（带默认值）而非重建；ES mapping 直接加字段。
- **标签粒度是文件级**（TagLink 关联的是 file_id，chunk 不挂标签）——过滤只能走 `document_id in [...]` expr，无法做 chunk 级过滤。
- 检索构建链路的现状：文档库路径 `workflow/common/knowledge.py`，知识空间路径 `knowledge/domain/services/space_flow_retrieval.py`；**两条路径都要支持新能力**（合规库可能建在任一侧）。
- 依赖上游：`release-contract.md`（表 1 已登记本 Feature 的字段与检索注入归属）、`docs/architecture/04-knowledge-rag.md`（底座）。

---

## 3. 方案对比与选定

### 决策 1：时效字段的落点（KnowledgeFile vs Knowledge）

- **备选**：
  - A. 文档级（`KnowledgeFile`）加 `effective_date` / `expire_date`；检索期查 MySQL 得「生效中」文件 id 集合，以 `document_id in [...]` expr 注入（与既有 metadata_filter 的 MySQL 预过滤机制同构）
  - B. 知识库级（`Knowledge`）——整个库一个生效区间
  - C. 走既有 `user_metadata` 自定义字段——无「当前日期」语义、知识空间路径自定义字段不可过滤（见坑 2），只能凑合
  - D. 入库时把日期写入 chunk 的 Milvus/ES metadata——初版方案，被否：修改日期要等下次重解析才生效，违背 AC-08「修改即时生效」
- **选定**：A
- **原因**：制度修订的粒度天然是「文件」（新文件替换旧文件）；库级太粗（一个库混装制度/案例，无法整体生效/失效）；C 是变通不是语义；D 无法即时生效且引入 Milvus schema 演进成本。A 复用 metadata_filter 已验证的 MySQL 预过滤 → expr 注入路径（`space_flow_retrieval.py:64-76`），零新存储改造、零重解析依赖。
- **何时该重新考虑**：出现「单条款级修订必须精确到段落」的强需求时，升级为 chunk 级（成本：chunk 元数据 + 双引擎按 chunk 过滤 + 重写版本替换逻辑）。

### 决策 2：时效过滤的注入方式（默认注入 + null 容忍）

- **备选**：
  - A. 检索默认注入「生效中」过滤（MySQL 查 `(effective_date is null or effective_date <= now) and (expire_date is null or expire_date >= now)` → document_id 集合注入）——null 视为无限
  - B. 仅当工作流节点显式打开「时效过滤」开关才注入
  - C. 用 metadata_filter 的手写条件表达（用户在节点里自己拼）
- **选定**：A（并同时在 metadata_filter 提供 `today` 动态表达式，供自定义时间条件使用）
- **原因**：A 对历史数据零冲击（旧行日期为 null → 全部通过，行为与现在一致）；B 要用户记得开开关，合规场景「忘了开」= 失效制度被召回，风险不可接受；C 把合规正确性交给手工配置，且 metadata_filter 目前只支持固定日期/变量引用，无「今天」语义。
- **何时该重新考虑**：出现「故意检索历史失效版本」的场景时，加节点级开关（默认仍开）。

### 决策 3：权重与优先级的实现形态

- **备选**：
  - A. 知识库级 `weight`（默认 1.0）+ 两级 `priority`（默认 0=普通，1=优先）：检索后处理——优先组先召回 top_k，不足再补普通组；最终排序按「RRF 融合分数 × weight」降序
  - B. 把权重并入 RRF 权重参数——RRF 权重是通道级的（向量/关键词），语义不符
  - C. 节点级 per-KB 权重配置——UI 复杂、配置分散
- **选定**：A
- **原因**：库级配置一次、处处生效，符合「业务人员维护知识内容」的定位；RRF 分数是 rank-based（`1/(rank+60)`），乘权重只影响相对序、不破坏阈值语义；两级优先足够覆盖「制度优先于案例」的真实场景，多级链式优先级复杂度不值。
- **何时该重新考虑**：需要三级以上优先级或「按问题类型动态换优先组」时，升级为节点级配置（spec 已排除，二期）。

### 决策 4：标签过滤进检索（独立参数 vs metadata 变通）

- **备选**：
  - A. 检索节点新增 `tags` 过滤参数：`TagDao.aget_resources_by_tags(tag_ids, KNOWLEDGE_FILE)` 得 file_ids → 并入 `document_id in [...]` expr（与 metadata_filter 的既有注入点合并，AND 语义）
  - B. 把分类标签建成 metadata 字段，用既有 metadata_filter equals 过滤——零新代码但 label 不可管理、space 路径不可用（坑 2）
- **选定**：A
- **原因**：知识空间对话路径已有完全同款的实现可参照（`knowledge_space_chat_service.py:439-476`——tags → file_ids → expr）；标签有既有管理模型（Tag/TagLink），做成 metadata 字段是放弃标签体系。AC-03 的 and/or 语义沿用 metadata_filter 的 ConditionCases 口径。
- **何时该重新考虑**：标签需要 chunk 级命中（如「只检索打了某标签的段落」）时，把标签写入 chunk 元数据。

### 决策 5：top_k / 分数阈值的实现

- **备选**：
  - A. 节点参数透传：`top_k` 覆盖既有固定 `{"k": 100, "param": {"ef": 110}}`（`workflow/common/knowledge.py:209`）；`score_threshold` 在 RRF 融合后过滤
  - B. 只在最终结果上截断/过滤（不改变检索阶段的 k）
- **选定**：A
- **原因**：截断式 top_k 浪费召回预算（先召 100 再截 N），且 rerank 输入质量不必要地大；阈值过滤基于 RRF 融合分数（rank-based），与向量相似度分数不可比——实现上阈值在工具返回后（rerank 之后）过滤，但过滤键是 `rrf_score`（不随 rerank 变化），故与「rerank 前」结果集等价。ef 与 k 联动（ef = max(k+10, 110)），未配置时沿用 k=100 保证零回归（AC-13）。
- **何时该重新考虑**：需要 per-库不同的 top_k/阈值时，把参数从节点级下沉为库级默认值。

### 决策 6：预设分类标签的初始化

- **备选**：
  - A. 创建知识库时自动初始化四个分类标签（行内制度/监管法规/行业分析/历史案例；已存在同名则复用，幂等）
  - B. 前端在打标弹窗中引导手动创建
- **选定**：A（前端可一键「应用预设分类」触发，也可创建时自动）
- **原因**：AC-01 要求创建即可选；B 增加操作步骤且各库标签不一致（不利于跨库检索口径统一）。
- **何时该重新考虑**：分类体系需要可配置（行方自定义分类树）时，升级为租户级分类模板。

### 决策 7：命中测试的实现形态

- **备选**：
  - A. 新只读端点 `POST /knowledge/{id}/hit_test`（问句 → 复用与工作流完全相同的检索构建函数 → 返回片段/文档/分数/被时效排除的文件数）+ 知识库详情页弹窗
  - B. 前端本地模拟（拉 chunk 做关键词匹配）
- **选定**：A
- **原因**：spec AC-14 要求「测试与生产同一套检索逻辑」，B 天然违背；A 的关键是把检索构建抽成可复用入口（文档库路径与 space 路径各一个），hit_test 与工作流检索调用同一个函数，杜绝漂移。
- **何时该重新考虑**：需要调试中间态（每库召回明细、RRF 前分数）时，端点返回分级诊断信息。

### 决策 8：外部数据分流（本期只固定口径）

- **备选**：
  - A. 静态资料（评级报告/行业分析）走既有上传入库；实时查询（企查查/天眼查/征信）走 MCP 工具，不入库
  - B. 全部实时数据也定时抓取入库
- **选定**：A
- **原因**：实时数据入库必然过期（企业工商变更、征信更新），过期知识比没有知识更危险（合规场景的「幻觉」来源之一）；MCP 工具在会话内取实时值，语义正确。B 仅适合「可归档快照」类数据（评级报告 PDF），那本来就是静态资料入库。
- **何时该重新考虑**：出现「历史工商/征信快照需要留痕对比」的需求时，为快照类数据设计独立归档库（另起 feature）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**入库**（既有，不改）：
`POST /knowledge/upload → MinIO → FileScheduler（Redis Lua 公平调度）→ celery knowledge_celery → Load（MinerU/etl4lm/PaddleOCR 等）→ Transform（摘要/敏感词/SimHash/分块 SplitterTransformer）→ Ingest（Milvus 双写 + ES）`
关键文件：`worker/knowledge/file_worker.py:295`、`rag/pipeline/base.py:41-81`、`rag/knowledge_file_pipeline.py:41-49`。

**本 Feature 新增的两条改动线**：
- 字段维护线：知识库/文档编辑 API → 写入 `Knowledge.weight/priority`、`KnowledgeFile.effective_date/expire_date` → 检索期实时读取（**无需重解析**，AC-08）。已有 chunk 的 Milvus/ES metadata 补字段在**下次重解析时写入**；过滤条件对 null 容忍（决策 2），所以存量数据行为不变。
- 检索注入线：工作流知识节点（`workflow/common/knowledge.py` 的 `RagUtils.__init__`）→ 构建双引擎 retriever → 注入（metadata_filter AND 标签过滤 AND 时效过滤）→ 混合检索 + RRF（`core/ai/rerank/rrf_rerank.py`）→（可选 rerank）→ 权重/优先级后处理（决策 3）→ 阈值过滤 → top_k 截断。

**命中测试**：`POST /knowledge/{id}/hit_test` → 复用检索构建 → 返回命中列表 + 排除统计。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `Knowledge.weight` | float，默认 1.0，0 表示不参与多库召回 | 库级权重 | 工作流检索后处理 |
| `Knowledge.priority` | int，默认 0；1=优先组 | 两级优先级 | 工作流检索后处理 |
| `KnowledgeFile.effective_date` / `expire_date` | datetime，可空；null=无限制 | 文件级生效区间；检索期查 MySQL 预过滤（与 metadata_filter 同机制），修改即时生效无需重解析 | 检索注入（document_id 集合） |
| 检索节点新参数 `tags` / `top_k` / `score_threshold` | tags: tag_id 数组；top_k: int；score_threshold: float | 未配置时沿用既有默认 | 工作流节点配置 |
| metadata_filter 的 `today` 表达式 | 条件值填 `today`（大小写不敏感） | 动态当前日期 | metadata_filter 条件值解析 |
| 预设分类标签名 | `行内制度` / `监管法规` / `行业分析` / `历史案例` | business_type=knowledge、business_id=KB id，幂等初始化 | 前端分类组织 + 检索 tags 参数 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `knowledge/domain/models/knowledge.py`（扩展） | Knowledge 增 weight/priority 字段 + DAO 写入 | 不拥有既有字段语义 |
| `knowledge/domain/models/knowledge_file.py`（扩展） | KnowledgeFile 增 effective_date/expire_date + 非生效文件 id 查询 DAO + 编辑 API 透出 | 不触发重解析 |
| `knowledge/domain/services/knowledge_flow_retrieval.py`（新增，T-10 抽取） | 文档库检索构建唯一入口 `retrieve_knowledge_documents`——tags/时效/版本 scope、优先级双轮召回、权重/阈值/top_k 后处理；hit_test 与工作流节点调用同一函数（决策 7） | 不含向量库对象缓存（由 RagUtils 传入 `knowledge_vector_list`） |
| `knowledge/rag/risk_compliance_filters.py`（新增） | 纯函数：k/ef 联动、scope 子句组合、权重重排、优先级合并、后处理（阈值/top_k）——双链路从同一点导入防漂移（坑 3） | 不触 DB / 向量库 |
| `workflow/common/knowledge.py`（扩展） | 节点参数解析（tags/top_k/score_threshold，未配置=None 零回归）+ `metadata_filter_fn` 适配 + 跨问句向量库缓存 | 不再内联检索构建（委托共享函数） |
| `knowledge/domain/services/space_flow_retrieval.py`（扩展） | space 路径同款注入（async 版纯函数复用同一批 filters） | — |
| 命中测试端点（新增，只读） | 复用 `retrieve_knowledge_documents` + 排除统计输出 | 不写入任何向量数据 |
| 前端 KnowledgePage（扩展） | 标签中心页、时效/权重编辑、命中测试弹窗 | — |
| 标签中心复用 `knowledge/tags` CRUD（既有） | 标签建/改/删（级联在 `TagDao.delete_business_tag`，文档库与空间两调用方共享） | 不动 space 标签库 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | 标签粒度是**文件级**：TagLink 关联 file_id（`resource_type=KNOWLEDGE_FILE`），chunk 不挂标签 | 试图做 chunk 级标签过滤 → 无数据可用 | 标签过滤一律 `document_id in [...]` expr（决策 4） |
| 2 | 知识空间路径的自定义 metadata 字段**不可过滤**（仅内置 6 字段，`knowledge.py:288-292` 注释 AC-08） | 时效做成 user_metadata 字段 → space 库失效 | 时效走独立字段（决策 1），不走 metadata 系统 |
| 3 | 文档库与知识空间是**两条检索链路**（`workflow/common/knowledge.py` vs `space_flow_retrieval.py`），能力演进长期不同步 | 只改一条 → 另一侧行为漂移 | 本 Feature 两条都改；e2e 双路径各跑 |
| 4 | 检索 k 是硬编码 `{"k": 100, "param": {"ef": 110}}`（`knowledge.py:209`），ef 必须大于 k | 只改 k 不改 ef → Milvus 召回异常 | top_k 与 ef 联动（决策 5） |
| 5 | tenant 自动过滤只拦 SELECT——新增的 bulk 写/删要手写 tenant_id 条件（`core/database/tenant_filter.py`） | 字段批量维护跨租户泄漏 | 维护 API 全部走 ORM 单行更新 |
| 6 | 时效过滤走 MySQL 预过滤而非 chunk metadata——存量文档无需任何向量库改造，null 容忍天然满足（旧行日期为 null → 全部通过） | 若误走 chunk metadata 方案：修改日期要等重解析才生效（违背 AC-08）、且要动 Milvus schema | 检索期 MySQL 查询 + `document_id in [...]` 注入（决策 1 选型 A） |
| 7 | 前端打标弹窗 `handleCreateTag` 曾把完整响应体当 TagItem 用（id=undefined → 保存 400）——2026-09-06 已修（解包 `result.data`） | 新建标签后保存不生效、标签不显示 | 回归测试覆盖新建→保存→列表展示（AC-02） |
| 8 | RRF 分数是 rank-based（`1/(rank+60)`），与向量相似度不可直接比 | 阈值过滤放错位置（融合前）→ 阈值语义混乱 | 阈值基于 `rrf_score` 过滤（rrf_score 不随 rerank 变化，实现上在工具返回后过滤与「rerank 前」结果集等价） |
| 9 | 分类标签的 business_id 绑定具体知识库（`business_type=knowledge`），跨库不共享 | 预设标签重复建、检索时各库标签 id 不可互换 | 预设初始化幂等 + 检索 tags 参数按所选知识库解析 |
| 10 | 既有 `RRFRerank` 融合后**不产出分数**（决策 3/5 的「rrf_score × weight」「融合后阈值」依赖它） | 直接读 metadata 拿不到分数 → 权重排序/阈值静默失效 | F055 在 `RRFRerank.compress_documents` 末尾写 `doc.metadata["rrf_score"]`（增量字段，两个既有消费方不受影响） |
| 11 | 双引擎各按 k 召回，融合去重后总数**可能超过 k**；未配置 top_k 时必须保持既有行为（AC-13） | 无条件截断 → 既有工作流召回变少 | 仅当节点显式配置 top_k 时才截断（`postprocess_risk_docs`）；检索期 k/ef 联动 |
| 12 | 时效预过滤依赖 F055 迁移（effective_date/expire_date 列）；未跑迁移的环境查询会报未知列 | 检索链路因迁移时序问题整体崩溃 | 与版本过滤同款 best-effort：SQLAlchemyError 时记 warning 跳过时效过滤，不破坏既有检索 |
| 13 | tags 选中但该库/空间没有任何文件关联时，`document_id in []` 在 Milvus expr 中**非法** | 空列表 expr → 检索 500 | 逐库解析 tag 范围；空命中 → 跳过该库/空间（与 metadata filter 无命中跳过的既有约定一致） |
| 14 | 删除标签的两个调用方（文档库 `KnowledgeService.delete_knowledge_tag` / 知识空间）共用 `TagDao.delete_business_tag`；bulk DELETE 不享受租户自动过滤 | 只改一方 → 另一方残留孤儿 TagLink；跨租户 tag_id 误删 | 级联在该 DAO 内实现：先租户自动过滤的 SELECT 校验归属，再按已验证 id 删 Tag + 全部 TagLink（同一事务，幂等返回 True） |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| 知识库/文档编辑响应中的 weight/priority/effective_date/expire_date 字段 | HTTP API | platform 前端 |
| `PUT /knowledge/file/effective_period`（`{knowledge_id, file_id, effective_date?, expire_date?}`，null=不限；effective>expire → 400） | HTTP API | platform 前端（文档元数据弹窗） |
| `POST /knowledge/{id}/hit_test`（`{question}` → `{hits: [{document_id, document_name, chunk, chunk_index, score, knowledge_id}], excluded: {expired, not_yet_effective}}`；读权限） | HTTP API | platform 前端（命中测试弹窗） |
| 工作流检索节点新参数（tags/top_k/score_threshold/weight 排序语义） | 工作流 Flow 数据 | 智能体构建者；未配置时行为与旧版一致 |
| `retrieve_knowledge_documents` / `postprocess_risk_docs` 等共享函数 | 内部函数契约 | hit_test 端点、工作流节点、文件上传临时路径三方共用（单一构造点，改一处三处同步） |
| metadata_filter 的 `today` 表达式 | 既有参数扩展 | 使用 metadata_filter 的工作流 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| 双引擎 metadata 过滤与 AND 组合机制（`space_flow_retrieval.py:64-76`） | 内部函数 | 机制变更需同步本 Feature 注入点 |
| `TagDao`（`aget_resources_by_tags` 等） | 内部 API | 标签表结构变化需同步 |
| `KnowledgeFileDao` 的 MySQL 预过滤查询 | 内部 API | 时效过滤的 document_id 集合来源；表结构变化需同步 |
| 既有版本管理（`knowledge_document_version`） | 内部机制 | 时效与版本替换的交互：新版本继承原文件生效区间，旧版本随文件一起失效 |
| MCP 工具注册（cnfic 服务） | 既有能力 | 实时数据分流依赖工具可用性，不在本 Feature 内维护 |

---

## 7. 测试与可观测

- **单元**：过滤条件构造（null 容忍、today 表达式、tags→file_ids）、权重/优先级排序、top_k/ef 联动、预设标签幂等。
- **集成**：文档库与知识空间两条检索链路各跑一遍注入验证（真实 Milvus/ES）。
- **e2e**（`test/e2e/test_e2e_f055_risk_compliance_knowledge.py`，13 例）：真实链路（upload→process→解析嵌入→hit_test）验证预设标签、标签级联、时效过滤（未来生效/已失效排除、清除即恢复）、权重写入口径、权限配对、错误路径。多库权重/优先级排序与节点 tags/top_k/阈值的工作流对话验证在 `e2e-checklist.md` 手动清单（单测已覆盖同构建函数语义）。
- **手动**：`https://172.19.70.141:3001/filelib`（admin / Tansun@2020）——知识库详情页命中测试弹窗；标签中心建/改/删；失效文档列表标识；工作流检索节点配置 tags/top_k/阈值后跑一次对话比对召回。
- **可观测**：检索注入条件与召回数计入既有日志（检索构建链路 debug 日志）；命中测试端点返回排除统计（失效排除数）。

---

## 8. 后续改进 / 不打算做的事

- 节点级动态权重（覆盖库级默认）——spec 排除，二期。
- chunk 级生效日期与条款级修订——版本管理 + 文件级时效暂够用；出现强需求再升级。
- 外部数据定时抓取入库管道——另起 feature；本期只固定「静态入库 / 实时 MCP」口径。
- 过期文档自动归档/物理清理——过期仅影响检索可见性；归档策略等行方运营规范明确后再设计。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-06 | 初版（8 项决策 + 现状快照，基于 2026-09-06 三个探查代理的代码级 gap 分析） | spec 评审通过（用户确认） |
| 2026-09-06 | 实现完成回写：§4.3 补 `knowledge_flow_retrieval.py` / `risk_compliance_filters.py` 两个抽取模块（T-10 共享构建函数）；§6.1 补 `PUT /knowledge/file/effective_period` 与共享函数契约；坑表补 10-14（rrf_score 写入、超 k 截断守卫、时效预过滤降级、空标签跳过、TagDao 级联落点）；决策 5/坑 8 阈值位置措辞修正（rrf_score 为键，与 rerank 顺序无关）；§7 e2e 口径与 `e2e-checklist.md` 对齐 | T-18/T-19（e2e + code review 偏差回写） |
