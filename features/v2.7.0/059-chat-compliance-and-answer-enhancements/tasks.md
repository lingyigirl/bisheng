# Tasks: 对话合规与回答区增强（059-chat-compliance-and-answer-enhancements）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-17 /sdd-review spec 通过（4 项 gap 修复后复审；53 条 AC） |
| design.md | ✅ 已评审 | 2026-09-17 /sdd-review design 通过（2 high + 2 medium 修复后复审） |
| tasks.md | ✅ 已拆解 | 2026-09-17 拆解完成：**27 个任务 / 9 个 Wave，53 条 AC 全覆盖** |
| 实现 | ✅ 完成 | 27 / 27 完成。e2e 自动化 23 例（需真实后端运行）；手动清单见 [e2e-checklist.md](./e2e-checklist.md)。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 0/1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`。

**后端 Test-First（务实版）**：理想流程先写测试（红）再写实现（绿）；测试编写成本极高可标注 `**测试降级**: 手动验证 + TODO`；中间件 / DM8 / e2e 在 **CI** 跑。

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（🚧 规划中）。

**自包含任务**：每个任务内联文件、逻辑、AC 覆盖，实现期不必回读 spec。但**设计论证指向 design §X，不复制**（避免第三处漂移）。

**本 feature 特殊口径**：
- **两套鉴权并存**：平台词库的 3 个端点走 `_check_is_global_super`（**不是** `get_tenant_admin_user`，design K9）；租户侧命中日志与评价查询沿用既有租户鉴权。
- **两张表的方向相反**：平台表（词条、策略）**绝不加 `tenant_id` 列**（加即被静默过滤，design K1/坑 2）；评价事件表**必须**带 `tenant_id` 且**必须**登记进 `_TENANT_AWARE_MODEL_MODULES` 白名单（漏登记即跨租户可见，design K10/坑 2b）。这两个方向都反直觉，是本次最容易踩的两处。
- **表创建方式分两种**：`platform_sensitive_word` / `platform_sensitive_word_policy` / `message_feedback_event` 三个**全新表**走 SQLModel `table=True` + `create_all(checkfirst=True)` 自动建表，**不写 Alembic revision**；`sensitive_word_hit_log` 是**既有表加列**，**必须**写 Alembic revision（`create_all` 不改既有表）。
- **流式输出跨分片优先级边界**：租户动作=截断且租户词先出现时以租户话术收尾 —— **用户已明确接受，勿当 bug 修**（design §3 决策 3 / §8）。
- **不落库的三件事**：热词、关联问题、以及被拦截轮次的任何建议 —— 均不写任何持久化存储。

---

## Tasks

### Wave 0 — 基础设施（后端，无测试配对，可并行） ✅ 完成

- [x] **T001**: 错误码定义（模块 113）
  **文件**: `src/backend/bisheng/common/errcode/platform_sensitive_word.py`（新增）
  **逻辑**: 定义 `BaseErrorCode` 子类，模块编码 113（已在 release-contract 登记）：`11301` = 导入文件无法解析 / 无有效词条（spec AC-05）。命中拦截**复用 F056 的 11201 / 11202，不改语义、不新增**。参照 `sensitive_word.py` 的写法（`http_code`、`message`、`return_resp()`）。
  **依赖**: 无

- [x] **T002**: 平台词条表 + DAO
  **文件**: `src/backend/bisheng/sensitive_word/domain/models/platform_word.py`（新增）
  **逻辑**: SQLModel `PlatformSensitiveWord`（`table=True`）：`id` / `word`（String(255)，**唯一约束**）/ `source`（String(16)，`manual`|`import`，便于区分导入与手工补录）/ `created_by` / `create_time`。**绝不加 `tenant_id` 列**（design K1 —— 加了会被 `do_orm_execute` 静默过滤，词库变空）。
  DAO：`acount( keyword )` 分页计数、`alist(keyword, page, page_size)` 分页（按 id 倒序）、`acreate(word, source, created_by)`、`adelete_by_ids(ids)`、`alist_all_words()`（供自动机构建，只取 word 列）、`areplace_all(words, source, created_by)`（覆盖模式：同一事务内全删+全插）。字段类型遵循 C2（无 JSON 需求，纯字符串列即可）。
  **回滚方案**: 新增独立表、无外键依赖，回滚仅需 `DROP TABLE platform_sensitive_word`；**但有数据损失**——词条是人工导入/维护的内容，回滚前需先导出（`alist_all_words()` 可直接导出为文件），或确认可从信贷系统重新获取。这也是 `source` 列单独记录导入来源的原因之一。
  **依赖**: 无

- [x] **T003**: 平台策略表 + DAO
  **文件**: `src/backend/bisheng/sensitive_word/domain/models/platform_policy.py`（新增）
  **逻辑**: SQLModel `PlatformSensitiveWordPolicy`（`table=True`）**单行表**：`id` 固定为 1（或取首行）、`enabled`(Boolean) / `action`(String(16): `block`|`mask`) / `auto_reply`(String(500)) / `updated_by` / `update_time`。**绝不加 `tenant_id` 列**（同 T002）。
  DAO：`aget_policy()`（无行则返回默认对象：`enabled=False`、`action='mask'`、`auto_reply=''`）、`aupsert(enabled, action, auto_reply, updated_by)`。默认话术的**初始值**（需求原文，spec AC-09）在服务层给，不写死在表默认值里。
  **回滚方案**: 新增独立表、无外键依赖，回滚仅需 `DROP TABLE platform_sensitive_word_policy`；**无数据丢失风险**（策略为可重建的配置，重配一次即可）。
  **依赖**: 无

- [x] **T004**: 评价事件表 + DAO ⚠️ 含白名单登记
  **文件**: `src/backend/bisheng/chat_session/domain/models/feedback_event.py`（新增，目录不存在则创建）
  **逻辑**: SQLModel `MessageFeedbackEvent`（`table=True`）append-only：`id` / `tenant_id`（**必须有**）/ `user_id` / `message_id`（索引）/ `flow_id`（应用，可空）/ `from_value`(int) / `to_value`(int)（0/1/2，沿用 `LikedType`）/ `comment`（可空，String(4096)）/ `create_time`。
  DAO：`acreate(...)`、`abackfill_comment(message_id, user_id, comment, within_minutes=10)`（**只**回填空 `comment`、`to_value=2`、时间窗口内的最新一条，返回是否回填成功）、`alist(tenant_id, flow_id, feedback_type, start_time, end_time, page, page_size)`、`acount(...)`。分页查询参照 `hit_log.py` 的 `alist` 写法（注意租户过滤器会把 SELECT 包成 Row，需 `row[0]` 解包——见 `hit_log.py:92` 的既有注释）。
  **⚠️ 关键**：同时把 `"bisheng.chat_session.domain.models.feedback_event"` 加进 `src/backend/bisheng/core/database/tenant_filter.py` 的 `_TENANT_AWARE_MODEL_MODULES` 元组。**漏了这一步，该表既不被租户过滤（跨租户可见彼此评价）也不被 `before_flush` 回填 `tenant_id`（写空值）** —— 这正是 v2.5 的真实事故模式（design K10 / 坑 2b）。
  **回滚方案**: 新增独立表、无外键依赖，回滚仅需 `DROP TABLE message_feedback_event`；**删除即丢弃评价审计历史**（append-only，无其他副本可重建），回滚前需业务确认（合规口径）。**注意**：回滚时白名单元组里那一行可保留（指向已不存在的模块会被 `_force_import_all_models` 捕获并 warn，不影响启动），但更干净的做法是一并移除。
  **依赖**: 无

- [x] **T005**: `sensitive_word_hit_log` 加 `source` 列（Alembic revision）
  **文件**: `src/backend/bisheng/core/database/alembic/versions/v2_7_0_f059_hit_log_source.py`（新增 revision）+ `bisheng/sensitive_word/domain/models/hit_log.py`（加字段）
  **逻辑**: 既有表**加列必须写 Alembic revision**（`create_all` 不改既有表）。新增 `source` 列：`String(16)`，`nullable=True`，`server_default="'tenant'"`（既有行据此回填为租户来源，spec AC-17/AC-19 的历史行口径）。参照 `v2_6_0_f036_sensitive_word_policy.py` / `v2_7_0_f057_knowledge_department.py` 的既有写法；**只做 DDL + server_default**，不做任何数据回填 UPDATE（backend AGENTS.md 硬规矩）。ORM 侧同步加 `source: str | None` 字段并在 `acreate_async_best_effort` 增加同名可选参数。
  **回滚方案**: downgrade 侧写 `drop_column('sensitive_word_hit_log', 'source')`。**注意回滚有数据损失**：列被删即丢失"哪些命中来自平台词库"的归属信息，且无法从其他字段反推（`hits` 只存摘要）。因此**回滚需在确认无平台来源命中记录后执行**，否则该批审计数据将永久无法区分来源。
  **依赖**: 无

### Wave 1 — 平台词库服务（后端 Domain，Test-First） ✅ 完成

- [x] **T006**: 导入解析 + 差异计算单元测试
  **文件**: `src/backend/test/sensitive_word/test_platform_word_service.py`（新增）
  **逻辑**: 测**纯函数**（不碰 DB）：
  - 分隔符解析：换行 / 半角逗号 / 全角逗号 / 分号 / 全角分号 / 竖线混排；空行与空白行忽略；前后空白 strip
  - 去重：同词重复只留一条；**中英文标点混用**不产生重复词（沿用 F056 `WORD_SEPARATOR_RE` 口径）
  - 表头：csv/xlsx 首行是表头（如 `敏感词`/`word`）时忽略；单列多值按同一分隔符规则
  - 边界：空文件 → 空列表；全空白 → 空列表；超长词（>255）截断或跳过（择一并断言）；仅标点 → 空列表
  - xlsx：需 `openpyxl`（既有依赖，见 `knowledge_imp.py` 的 `pd.ExcelWriter`）；**给一个 3 行的 xlsx fixture**
  - 差异计算：`覆盖` 模式预览 = (新增集合, 删除集合, 去重数)；`追加` 模式预览 = (新增集合, 0, 去重数)；完全相同 → 新增 0 / 删除 0
  **覆盖 AC**: AC-02, AC-03, AC-04, AC-05
  **依赖**: T002

- [x] **T007**: 平台词库服务实现
  **文件**: `src/backend/bisheng/sensitive_word/domain/services/platform_word_service.py`（新增）
  **逻辑**: `parse_words_file(raw: bytes, filename: str) -> list[str]`（按扩展名分派 txt/csv/xlsx；解析失败或结果为空 → 抛 `11301`）；`preview_import(words, mode) -> ImportPreview`；`import_words(words, mode, operator_id)`（覆盖走 `areplace_all`，追加走批量 `acreate` 跳过已存在）；`list_words` / `add_word` / `delete_word`；词条增删后 `clear_cache()`（AC-07 立即生效）。**不含鉴权**（端点层做）。
  **测试**: T006 全绿。
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-07
  **依赖**: T006

- [x] **T008**: 平台自动机缓存 + 判定入口
  **文件**: `src/backend/bisheng/sensitive_word/domain/services/platform_word_service.py`（同 T007 文件内扩展）
  **逻辑**: 独立的 `_platform_automaton_cache`（**不要复用** F056 的 `_automaton_cache`——它的 key 含 `tenant_id`，会给 N 个租户各存一份相同的万级自动机，design 决策 4）。缓存 key = 词条内容摘要（复用 `_build_cache_key` 的 sha256 思路）。`build_platform_matcher() -> WordMatcher | None`（未启用或无词条 → None）与 `check_platform_text(text) -> SensitiveWordCheckResult`，返回形状与 F056 的 `WordMatcher` 对齐以便复用既有扫描代码。`clear_cache()` 时一并清。
  **测试**: 覆盖「词库变更后自动机重建」「平台与租户缓存互不影响」。
  **覆盖 AC**: AC-07, AC-14
  **依赖**: T007

### Wave 2 — 检测链路接入（后端 Domain，Test-First） ✅ 完成

- [x] **T009**: `ChatGuardService` 平台层单元测试
  **文件**: `src/backend/test/sensitive_word/test_chat_guard_platform.py`（新增）
  **逻辑**: mock 平台 DAO 与租户 DAO，测：
  - 平台未启用 / 词库空 → 完全走租户层，行为与 F056 一致（AC-14）
  - 平台命中输入 → 返回**平台话术**、**不调租户层**、日志 `source=platform`（AC-09, AC-12, AC-17）
  - 平台未命中 + 租户命中 → 租户话术、`source=tenant`（AC-19）
  - 两侧都未命中 → 放行
  - 输出方向：逐分片平台优先；**同分片双方都命中 → 平台动作 + 平台话术，且 block 截断点取双方更早者**（AC-12 的关键断言）
  - 输出方向：平台词在后续分片命中（租户 mask 场景）→ 平台策略正常生效
  - **已接受的边界**：租户 block 先命中 → 立即以租户话术收尾（断言此为**预期行为**，防止后人当 bug 改）
  - 平台词库在流式中途变更 → 已持有 matcher 的那一轮不受影响（AC-15）
  **覆盖 AC**: AC-09, AC-12, AC-14, AC-15, AC-17, AC-19
  **依赖**: T008

- [x] **T010**: `ChatGuardService` 平台层实现
  **文件**: `src/backend/bisheng/sensitive_word/domain/services/chat_guard.py`（扩展）
  **逻辑**: 在 `check_chat_input` / `start_output_guard` **内部**接入平台层（design 决策 2：检测语义收在 service，`api/services/guardrail.py` 装配层不动）。`OutputGuardState` 增加 `platform_matcher` 字段；`check_output_chunk` 按设计决策 3 的三条规则改写扫描顺序与截断点计算。**保持租户层既有分支语义不变**（只在其前面/旁边加平台判断）。日志写入传 `source`。
  **测试**: T009 全绿；F056 既有用例（`test_chat_guard.py` 等）**必须不破**。
  **覆盖 AC**: AC-08, AC-09, AC-10, AC-11, AC-12, AC-14, AC-15, AC-16, AC-17
  **依赖**: T009

- [x] **T010b**: OpenAI 兼容接口平台层生效验证
  **文件**: `src/backend/test/sensitive_word/test_open_endpoint_platform_guard.py`（新增）
  **逻辑**: 平台层收在 `ChatGuardService` 内部（T010），`open_endpoints` 两个入口（助手 `open_endpoints/api/endpoints/assistant.py:85-93`、工作流 `workflow.py:176`）通过既有的 `build_input_guard` 自动获得平台层能力，**无需改这些文件**。本任务用测试**证明**这一点而非假设：平台词库启用后，模拟 `open_endpoints` 的输入命中 → 返回 HTTP 400 + 错误码 **11201** + **平台话术**（AC-13）。同时验证 `tenant_id` 为空时 guard 为 None 的既有分支不受影响（`assistant.py:82` 的三元表达式）。
  **测试**: 命中返回 400/11201/平台话术；未命中放行。
  **覆盖 AC**: AC-13
  **依赖**: T010

### Wave 3 — 评价留痕（后端 Domain，Test-First） ✅ 完成

- [x] **T011**: 评价事件服务单元测试
  **文件**: `src/backend/test/chat_session/test_feedback_event_service.py`（新增，目录不存在则创建并搭 `conftest.py`）
  **逻辑**: 测 from/to 矩阵与回填：
  - 0→1、0→2、1→0、2→0、1→2、2→1 各写一条，`from_value`/`to_value` 正确（AC-22）
  - **无变更不写**：请求值与当前值相同 → 零写入（AC-27）——注意要与 `like_response` 既有的早期 return 对齐
  - 回填命中：窗口内存在 `to_value=2` 且 `comment` 为空的记录 → 写入原因（AC-23）
  - 回填未命中（窗口外 / 无对应记录 / 原因已填）→ **不报错、不补写独立事件**（design 决策 6）
  - 事件写入抛异常 → `like_response` 主流程不受影响（AC-24）
  - 查询：按时间 / 应用 / 评价类型筛选；租户范围与超管范围两种情况（AC-25, AC-26）
  **覆盖 AC**: AC-22, AC-23, AC-24, AC-25, AC-26, AC-27
  **依赖**: T004

- [x] **T012**: 评价事件服务实现 + 接入既有评价链路
  **文件**: `bisheng/chat_session/domain/services/feedback_event_service.py`（新增）+ `chat_message_service.py`（扩展）
  **逻辑**: 新建 `FeedbackEventService`（record / backfill_comment / list）。在 `chat_message_service.like_response` 的**早期 return 之后、更新 `liked` 前后**调用 `record(from_value=message.liked, to_value=data.liked, ...)`，**用 best-effort 包裹**（失败仅 warn，不影响点赞主流程，design 坑 8）。在 `comment_on_answer` 内调用 `backfill_comment`。既有会话计数逻辑（`add_like_count` / `add_dislike_count`）**一行不改**。
  **测试**: T011 全绿。
  **覆盖 AC**: AC-22, AC-23, AC-24, AC-26, AC-27
  **依赖**: T011

### Wave 4 — 热词与关联问题（后端 Domain，Test-First） ✅ 完成

- [x] **T013**: 回答建议服务单元测试
  **文件**: `src/backend/test/knowledge/test_answer_suggestion_service.py`（新增）
  **逻辑**: mock LLM 与检索入口，测：
  - 热词：正常返回词列表；LLM 返回非法 JSON / 带 markdown 代码块 → 解析成功或用 `extract_code_blocks` 兜底；解析失败 → 空列表（AC-32）
  - 关联问题：绑定了知识库 → 调 `retrieve_knowledge_documents` 并凝练；**未绑定知识库 → 跳过该半边、questions 为空**（AC-42）；检索空结果 → 空列表
  - 模型未配置（`get_knowledge_similar_llm` 返回 None）→ 两半均为空、**不抛异常**（AC-33 / design 坑 4）
  - 超时 → 空（AC-32, AC-41）
  - 两半独立失败：热词成功 / 关联问题失败 → 返回 `{keywords: [...], questions: []}`（反之亦然）
  - **不落库**：断言无任何 DAO 写调用（AC-35, AC-45）
  - **被拦截轮次不吃热词**：传入的平台命中词条文本**不得**出现在返回的 `keywords` 中（AC-37 —— 该断言是 INV-10 不泄露的防线之一，不可省）
  **覆盖 AC**: AC-32, AC-33, AC-37, AC-41, AC-42, AC-44, AC-45
  **依赖**: 无（mock 一切外部依赖）

- [x] **T014**: 回答建议服务实现
  **文件**: `src/backend/bisheng/knowledge/domain/services/answer_suggestion_service.py`（新增）
  **逻辑**: `generate(chat_id, message_id, question, answer, user_id, tenant_id, flow_id) -> {keywords, questions}`。
  - **热词**：一份**新写的** prompt 抽取实体/关键词（**不复用** `recommend_question` 的 prompt —— 那个是生成近义复述句，design 坑 6）
  - **关联问题**：解析应用绑定知识库（助手取 `AssistantLink.knowledge_id`，`database/models/assistant.py:75`；取不到即跳过）→ `retrieve_knowledge_documents`（F055 入口，`knowledge_flow_retrieval.py:155`）→ 再一份**新写的** prompt 凝练成追问句
  - **两半并发**跑（`asyncio.gather`），各自 try/except 降级为空列表
  - **模型调用必须异步化**：既有 `recommend_question` 用同步阻塞的 `LLMChain.predict`；本服务**不得**在 async 端点里直接 await 同步调用（design 坑 5 —— 会阻塞事件循环）。用 `asyncio.to_thread` / `run_in_executor` 包裹，或直接用异步 LLM 客户端
  - 模型槽位取 `qa_similar_model`；**未配置时返回空，不抛 `KnowledgeSimilarError`**（design 坑 4）
  **测试**: T013 全绿。
  **覆盖 AC**: AC-29, AC-30, AC-32, AC-33, AC-34, AC-35, AC-37, AC-38, AC-39, AC-41, AC-42, AC-44, AC-45
  **依赖**: T013

### Wave 5 — API 端点（后端） ✅ 完成

- [x] **T015**: 平台词库管理端点（超管鉴权）
  **文件**: `src/backend/bisheng/sensitive_word/api/endpoints/platform_words.py`（新增）+ `api/router.py`（注册）
  **逻辑**: `GET /platform-sensitive-words`（列表，keyword/page/page_size）、`POST`（单条新增）、`DELETE /{id}`（单条删除）、`POST /platform-sensitive-words/import/preview`（差异预览）、`POST /platform-sensitive-words/import`（执行，body 带 mode）。**全部依赖超管鉴权**：新写一个 `Depends(require_global_super_admin)` 包装 `_check_is_global_super`（`utils/http_middleware.py:145`），**不得使用 `get_tenant_admin_user`**（design K9 —— 它会放行当前租户的子管理员，造成租户可读写全局词库）。文件上传用 `UploadFile`，解析交给 T007 的服务。
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07
  **依赖**: T007

- [x] **T016**: 平台策略端点（超管鉴权）
  **文件**: `bisheng/sensitive_word/api/endpoints/platform_policy.py`（新增）+ `api/router.py`（注册）
  **逻辑**: `GET /platform-sensitive-word-policy`（返回 enabled / action / auto_reply；`auto_reply` 为空时返回**需求原文话术作为初始值**，spec AC-09）、`PUT`（超管保存）。同样走 `require_global_super_admin`。
  **覆盖 AC**: AC-01, AC-06, AC-08, AC-09
  **依赖**: T003, T015

- [x] **T017**: 命中日志端点扩展（source 过滤 + 租户侧脱敏）
  **文件**: `bisheng/sensitive_word/api/endpoints/hit_logs.py`（扩展）+ `chat_guard.py` 的 `alist_hit_logs`（扩展）
  **逻辑**:
  - 租户侧：响应中 **`source=platform` 的行，其 `hits` 必须清空**（在服务层构造响应时抹除，**不能只在页面隐藏** —— design K2 / spec AC-18）。可见字段保留时间、方向、应用、动作、命中**次数**（次数可由 `len(hits)` 或单独汇总得到，若保留次数则**不得**保留词条内容）。
  - 新增 `source` 查询参数；新增**超管范围**查询（走 `require_global_super_admin` + `bypass_tenant_filter()` 既有范式，spec AC-20）。
  **覆盖 AC**: AC-17, AC-18, AC-19, AC-20
  **依赖**: T005, T010

- [x] **T018**: 评价明细端点
  **文件**: `bisheng/chat_session/api/endpoints/feedback_events.py`（新增）+ `api/router.py`（注册）
  **逻辑**: `GET /message-feedback-events`：租户侧走既有租户鉴权（`get_tenant_admin_user`）+ 租户过滤；超管侧走 `require_global_super_admin` + `bypass_tenant_filter()`。支持时间 / 应用 / 评价类型筛选与分页。
  **覆盖 AC**: AC-25, AC-26
  **依赖**: T012

- [x] **T019**: 回答建议端点
  **文件**: `bisheng/knowledge/api/endpoints/answer_suggestion.py`（新增）+ `knowledge` 模块 router 注册
  **逻辑**: `POST /chat/suggestions`，body `{chat_id, message_id, question, answer}`，登录用户鉴权。调 T014 的服务，返回 `{keywords, questions}`。**归属 knowledge 模块**（service 与端点同模块自注册，由 `api/router.py:48` 的 `/api/v1` 前缀统管）——路径含 `chat` 只是语义命名，**不要放进 `chat_session` 模块**（会造成跨模块调用，arch-guard RULE-5；design §4.3 归属说明）。
  **覆盖 AC**: AC-29, AC-33, AC-38, AC-42
  **依赖**: T014

### Wave 6 — 前端 Platform ✅ 完成

- [x] **T020**: 平台词库管理页
  **文件**: `src/frontend/platform/src/pages/BuildPage/bench/PlatformSensitiveWords.tsx`（新增）+ `controllers/API/platformSensitiveWord.ts`（新增）+ 挂载到「安全围栏」tab
  **逻辑**: 词条分页表（关键词搜索、单条新增、单条删除）+ 导入对话框（选文件 → 选覆盖/追加 → **覆盖模式先展示差异预览再确认**，spec AC-03）。**HTTP 一律走 `controllers/API/` 封装**（C7，store 不得直接调 HTTP）。UI 只用既有 `bs-ui` 组件（Checkbox/Button/Input/Textarea/Dialog/Table），**不得引入新 UI 库**。全部文案走 i18n key（无硬编码中文，lint 强制）。
  **验证**: 超管登录 → 平台设置 → 安全围栏 → 词库管理：新增/删除/搜索词条；导入 txt 与 xlsx 各一次，覆盖模式确认预览数字与结果一致，追加模式确认既有词不被删。
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06
  **依赖**: T015

- [x] **T021**: 平台安全围栏页新增「强制词库」区块
  **文件**: `src/frontend/platform/src/pages/BuildPage/bench/LLMGuardrailPolicy.tsx`（扩展）+ `controllers/API/platformSensitiveWord.ts`（扩展）
  **逻辑**: 在既有 `LLMGuardrailPolicy` 上方新增一块**平台级**配置：启用开关、输出动作（block/mask）、命中话术（默认展示需求原文作为初始值）。**必须与既有租户级两块视觉上明确区隔**（标题与说明写明"对所有租户强制生效，租户不可修改"），避免管理员把两者混淆。
  **验证**: 超管登录 → 设置启用 + 话术 → 保存后刷新回显一致；以租户管理员登录 → **看不到该区块**且直接调接口被拒。
  **覆盖 AC**: AC-06, AC-08, AC-09
  **依赖**: T016

- [x] **T022**: 平台评价明细页 + 命中日志页 source 展示
  **文件**: `LLMGuardrailHitLogs.tsx`（扩展 source 列与筛选）+ 评价明细页（新增，可并入既有 LogPage 或 bench 目录）+ `controllers/API/`（扩展）
  **逻辑**: 命中日志表增加「来源」列（租户/平台）与筛选；平台来源行的命中词列显示为占位（**服务端已脱敏，前端不做二次判断**）。评价明细页：按时间/应用/评价类型筛选、分页，展示操作人、消息 ID、前后值、原因。
  **验证**: 造几条平台命中与租户命中 → 日志页来源列正确区分，平台行的词列无词条内容；点赞/取消/改踩后明细页出现 3 条且前后值正确。
  **覆盖 AC**: AC-17, AC-18, AC-19, AC-20, AC-25, AC-26
  **依赖**: T017, T018

### Wave 7 — 前端 Client ✅ 完成

- [x] **T023**: 回答区三个组件
  **文件**: `src/frontend/client/src/components/Chat/AnswerSuggestionBar.tsx`（新增）、`AiDisclaimer.tsx`（新增）
  **逻辑**:
  - `AnswerSuggestionBar`：热词芯片行 + 关联问题列表，**顺序固定**（热词在上）；点击**一律填入输入框、不自动发送**（AC-31, AC-40）；两个区域各自为空则各自不渲染；整体为空则整块不渲染（AC-32, AC-41）；点击行为复用现有输入框 setter（参照 appChat 的输入状态管理）
  - `AiDisclaimer`：i18n 文案行（AC-46, AC-47）；**不可配置**（AC-48）
  - 样式只用既有 token（`text-text-3` 一类），**不得用 `backdrop-blur`**（根 AGENTS.md 硬红线）
  **验证**: 见 T024 的端到端验证。
  **覆盖 AC**: AC-31, AC-32, AC-40, AC-41, AC-46, AC-47, AC-48
  **依赖**: T019

- [x] **T024**: 接入 `AiMessageBubble` 并覆盖全部展示面
  **文件**: `src/frontend/client/src/components/Chat/AiMessageBubble.tsx`（扩展）+ 请求层 `api/`（新增 suggestions 调用）
  **逻辑**:
  - **评价按钮入口确认**：`MessageFeedbackButtons` 已在 `AiMessageBubble` 的 action row 中经 `allowFeedback` 条件渲染（`AiMessageBubble.tsx:793`），本任务只需确认新组件不破坏该行布局，并确认其覆盖面（AC-21 的"覆盖全部展示界面"由 `AiMessageBubble` 作为唯一渲染通道保证）
  - 在回答区渲染 `AnswerSuggestionBar` 与 `AiDisclaimer`，**固定顺序：热词 → 关联问题 → 友情提醒**；友情提醒位置**不因前两者异步到达而改变**（AC-49）
  - 回答结束后发起一次 `/chat/suggestions`，**`message_id` 为空则不发请求**（被拦截轮次无 ID，design 坑 12 —— **绝不可为拦截轮次补 message_id**）
  - 请求失败/超时 → **静默不展示，无任何提示**（AC-32, AC-41）
  - `allowFeedback=false`（分享只读视图）时：不展示评价按钮与热词/关联问题，**但仍展示友情提醒**（AC-51 —— 提醒的判断**独立于** `allowFeedback`，design 坑 10）
  - 因 `AiMessageBubble` 是全部展示面（首页对话 / 知源 / 订阅 / 应用对话 / 灵思）的唯一渲染通道，改此处即全覆盖（AC-50）
  **验证**: 5 个展示面各发起一次对话：回答下方依次出现热词、关联问题、友情提醒，且**点赞/差评按钮仍在**（AC-21）；点热词与问题 → 文本进输入框且未发送；刷新页面 → 热词与问题消失、**友情提醒仍在**；分享链接打开 → 只有友情提醒。
  **覆盖 AC**: AC-21, AC-29, AC-30, AC-31, AC-32, AC-34, AC-35, AC-36, AC-38, AC-39, AC-40, AC-41, AC-43, AC-45, AC-49, AC-50, AC-51
  **依赖**: T023

- [x] **T025**: i18n 三语词条
  **文件**: `src/frontend/packages/locales/src/`（新增词条）+ 三语语言包 + 重新生成产物
  **逻辑**: 友情提醒、页面文案、导入对话框文案等全部新增 key 的 **zh-Hans / en / ja 三语**必须在同一 PR 内齐全（根 AGENTS.md 硬规矩）。`platform_sensitive_word.py` 的 11301 错误码文案走 `api_errors` 域（platform 用 `api_errors:<code>`、client 用 `api_errors.<code>`）；**生成物不得手改**（CI 校验）。跑 `pnpm check-i18n` 确认 key 对齐。
  **覆盖 AC**: AC-47
  **依赖**: T020, T021, T022, T023, T024

### Wave 8 — 端到端验证 ✅ 完成

- [x] **T026**: e2e 测试与验证清单
  **文件**: `src/backend/test/e2e/test_e2e_f059_chat_compliance.py`（新增）+ `features/v2.7.0/059-chat-compliance-and-answer-enhancements/e2e-checklist.md`（新增）
  **逻辑**: 自动化覆盖可脱网的链路：平台词库 CRUD 与导入（含 11301 错误路径）、平台策略读写、超管鉴权拒绝路径（模拟租户管理员调用 → 403）、评价事件写入与回填、命中日志租户侧脱敏（**断言响应体中不含词条内容**）。依赖真实 ws / 流式 / Milvus / 模型的部分按手动清单执行。
  **兼容性回归（AC-52 / AC-53）**：同一测试文件内加两组回归断言——
  - **AC-52**：跑 F036 两个既有场景的路由（知识库文件解析、订阅文章）在平台词库启用前后的行为一致（复用 F036 既有测试用例，确认不被本 feature 影响）
  - **AC-53**：平台策略未启用 + 租户未配置策略 → 全部对话入口行为与发布前一致（复用 F056 的 AC-17 回归用例，**注意其运行前提是平台层未启用**，见 F056 design §7 的说明）
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-17, AC-18, AC-19, AC-20, AC-22, AC-23, AC-24, AC-25, AC-26, AC-27, AC-28, AC-52, AC-53
  **依赖**: T015, T016, T017, T018, T019

---

## 实际偏差记录

> 实现期发现设计需要修正时追加一行（论证写在 design.md，此处只留指针）。

| 日期 | 任务 | 偏差 | design 更新 |
|------|------|------|------------|
| — | — | — | — |
