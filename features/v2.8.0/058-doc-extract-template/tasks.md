# Tasks: 文档抽取模板（058-doc-extract-template）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.8.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-17 编写，经 7 轮 /sdd-review spec（46 条 AC）；第 5 轮做过结构重构（§2 调用模式 + §3.1 前置校验表） |
| design.md | ✅ 已评审 | 2026-09-17 编写，经 3 轮 /sdd-review design（Constitution Check C1–C7 通过）；14 决策 + 15 坑 |
| tasks.md | ✅ 已拆解 | 2026-09-17 拆解完成：37 个任务 / 9 个 Wave，46 条 AC 全覆盖 |
| 实现 | 🔲 未开始 | 0 / 37 完成。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3–§4 |
| e2e-test | 🔲 待执行 | — |
| code-review | 🔲 待执行 | — |

---

## 开发模式

**按 Wave 组织任务**：无依赖的归 Wave 0/A（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**任务分类**：

| 分类 | 任务 |
|---|---|
| 基础设施 | T-01 ~ T-06 |
| 后端 Domain（纯函数） | T-07 ~ T-12 |
| 后端 Domain（服务） | T-13 ~ T-21 |
| 后端 API | T-22 ~ T-26 |
| 前端 Platform | T-27 ~ T-33 |
| 收尾 / 第二阶段 | T-34 ~ T-37 |
| 前端 Client | **无**（本 feature 前端改动全在 platform 子应用） |
| Worker | **无** —— 本 feature 全部同步执行，不引入 Celery 任务（design 决策 14）。因此无 tenant_id 经 headers → ContextVar 的传递需求；租户上下文由 FastAPI 请求链路的既有 ContextVar 提供 |

**后端 Test-First（务实版）**：每个服务任务与它的测试任务成对出现，**测试任务排在实现任务之前**（先红后绿）。LLM 调用**全部 mock**，不打真实模型。

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（🚧 规划中）。

**实施顺序的硬约束（design §9）**：**先解耦 OCR 依赖**。**除 T-35（OCR 分支）外的全部后端 + 前端任务都不依赖任何外部识别服务**，可完全离线跑通；OCR 分支排在 Wave H 的最后阶段。这样即使广西环境最终没有可用的识别服务，本 feature 的底座仍然完整可用。

---

## ⚠️ 与并行工作流 F059 的冲突点（开工前必读，2026-09-17 实测）

`features/v2.7.0/059-chat-compliance-and-answer-enhancements/` 正在**同一工作区、未提交**地推进。以下文件双方都要改，**逐个任务串行提交、不要攒着一起交**。

| 文件 | F059 已做的改动 | F058 要做的改动（本文件中的任务） |
|---|---|---|
| `core/database/tenant_filter.py` | 已加 `bisheng.chat_session.domain.models.feedback_event` 到 `_TENANT_AWARE_MODEL_MODULES` | T-05 追加 `doc_extract` 两个模块路径 —— **追加到列表，不要重排/覆盖** |
| `test/fixtures/table_definitions.py` | 尚未改（但 F059 的三张新表 `platform_sensitive_word` / `platform_sensitive_word_policy` / `message_feedback_event` 也**都需要**加 DDL，**双方极可能在此文件再撞一次**） | T-04 追加两张表 —— 同样**只追加** |
| `api/router.py` | 尚未改 | T-26 追加 `doc_extract` router |
| `database/models/audit_log.py` | 尚未改 | T-13 追加三处枚举 |
| `packages/locales/src/api_errors/*.json` | 尚未改 | T-34 追加 11601–11611 三语 |
| Alembic versions 目录 | 已加 `v2_7_0_f059_hit_log_source.py`（未提交） | T-03 的 `down_revision` 需指向它 —— 见该任务的说明 |

**编号已让号**：错误码模块 **114**（113 归 F059）、**INV-12 ~ INV-15**（INV-10/11 归 F059）。勿改回。

---

## Tasks

### Wave 0 — 基础设施（无测试配对，全部并行）

#### T-01 错误码定义

**依赖**: 无

**改动**：新增 `src/backend/bisheng/common/errcode/doc_extract.py`（1 个文件）

11 个错误码类，继承 `BaseErrorCode`，模块号 **116**：

| 码 | 类名建议 | Msg 口径 |
|---|---|---|
| 11601 | `DocExtractTypeUnavailableError` | 指定的文档类型不可用（不存在或已停用） |
| 11602 | `DocExtractTypeDuplicateError` | 文档类型标识已存在 |
| 11603 | `DocExtractTemplateInvalidError` | 模板定义非法 |
| 11604 | `DocExtractRenderRefInvalidError` | 呈现形式引用了不存在的字段 |
| 11605 | `DocExtractNoParserError` | 无可用识别服务 |
| 11606 | `DocExtractTypeUnknownError` | 类型未识别（含无可判定候选、置信度低于阈值） |
| 11607 | `DocExtractFieldInvalidError` | 字段校验失败 |
| 11608 | `DocExtractContentFetchError` | 内容获取失败（模型未配置 / 调用失败 / 识别服务失败） |
| 11609 | `DocExtractDocUnreadableError` | 文档不可读或为空 |
| 11610 | `DocExtractDocTooLargeError` | 文档超出体量上限 |
| 11611 | `DocExtractTemplateMissingError` | 模板不存在或无权限访问 |

**为什么**：见 design §2 的 C5 落点 —— 113 已被 v2.7.0 的 F059 占用，故用 116。

**验证**：`python -c "from bisheng.common.errcode.doc_extract import DocExtractTypeUnavailableError"` 无导入错误。

**覆盖 AC**: AC-01

---

#### T-02 ORM 模型 + DAO

**依赖**: 无

**改动**：新增 `src/backend/bisheng/doc_extract/domain/models/extract_template.py`、`extract_run.py`、`__init__.py`（3 个文件）

**`doc_extract_template` 列**（design §4.2）：`id` / `tenant_id`(NOT NULL, 索引) / `name` VARCHAR(128) NOT NULL / `doc_type` VARCHAR(64) NOT NULL **普通索引，非唯一** / `description` TEXT NOT NULL / `field_schema` `JsonType` / `render_template` `LargeText` / `enabled` SMALLINT NOT NULL / `is_deleted` SMALLINT NOT NULL DEFAULT 0 / `create_user` / `create_time` / `update_time`。

**`doc_extract_run` 列**：`id` / `tenant_id`(索引) / `template_id` INTEGER NOT NULL / `doc_type` VARCHAR(64) NOT NULL（**冗余**）/ `determined_by` VARCHAR(16) NOT NULL / `confidence` FLOAT NULL / `preprocess_route` VARCHAR(16) / `fields` `JsonType` / `markdown` `LargeText` / `status` SMALLINT / `error_code` INTEGER / `error_message` VARCHAR / `user_id` / `create_time`。

**DAO 方法签名**（直接消费方 T-15 / T-18 / T-21 / T-25 按此对齐；T-24 是端点层，不直接消费 DAO）：

```
ExtractTemplateDao:
  get_by_id(template_id) / aget_by_id(template_id)          -> ExtractTemplate | None
  get_by_doc_type(doc_type, include_deleted=False)          -> ExtractTemplate | None
  list_templates(enabled_only: bool = False)                -> list[ExtractTemplate]
  create(data: dict) / update(template_id, data: dict)
  soft_delete(template_id)

ExtractRunDao:
  create(data: dict)
  list_runs(template_id=None, status=None, start_time=None, end_time=None,
            cursor=None, limit=20)                          -> (list[ExtractRun], next_cursor)
```

**关键约束**：
- **JSON 列必须用 `dialect_helpers.JsonType`、长文本用 `LargeText`**（C2 双 DB）。不得用 `sqlalchemy.JSON` / `LONGTEXT`。
- **`UNIQUE(tenant_id, doc_type)` 不得建在库层**（design 决策 9 / 坑 11）—— 软删行会挡住同名新建，与 AC-08 冲突。唯一性在 T-15 的 service 层校验。
- `determined_by` **不可省**（design 决策 12：AC-33 禁止用 `confidence = NULL` 兼作标记）。

**覆盖 AC**: AC-31, AC-33

---

#### T-03 Alembic 迁移

**依赖**: T-02

**改动**：新增 `src/backend/bisheng/core/database/alembic/versions/v2_8_0_f058_doc_extract.py`（1 个文件）

`revision = "f058_doc_extract"`；`upgrade()` 在 `table_exists` 守卫下建两张表 + 索引；`downgrade()` 对称 drop（**回滚方案**：drop 两张表与索引；因是新表、无存量数据依赖，回滚安全）。

**`down_revision` 怎么填**：截至 2026-09-17，v2.7.0 的最新迁移是 **`f059_hit_log_source`**（F059，**尚未提交**）。v2.8.0 在 v2.7.0 之后，故：

```python
down_revision = "f059_hit_log_source"
```

**但 `f059_hit_log_source` 未提交**。若 F059 最终改名 / 撤销 / 换 revision id，本迁移会挂空。开工时**必须先确认 F059 的迁移已落地**；若尚未，先填当前已提交的最新 head，并在 F059 落地后由**后落地方**改一次 `down_revision`（两个 tip 并存时用 `alembic merge` 生成合并节点）。

**约束**：**纯 DDL**（`alembic/AGENTS.md` §5），不得写数据迁移。

**验证**：`alembic upgrade --sql` 离线干跑不报 `NoInspectionAvailable`（用共享的 `table_exists` / `index_exists` 而非裸 `sa.inspect`）。

**覆盖 AC**: —

---

#### T-04 测试 fixture：table_definitions 加两张表

**依赖**: T-02

**改动**：`src/backend/test/fixtures/table_definitions.py`（1 个文件）

新增两张表的 SQLite DDL 常量，并挂进 `create_all_tables` / `create_tables` 的派发。

**为什么**：该文件的 DDL 是**手工维护**的（不是 `create_all`），模型加了表而 fixture 没加，单测会在 INSERT 时报 `no such table: doc_extract_template`。这是单测能跑起来的前提。

**冲突提示**：F059 的三张表也需要加在此文件。**只追加，不要重排既有常量**。

**验证**：跑任一涉及新表的 fixture 测试，不报表不存在。

**覆盖 AC**: —

---

#### T-05 多租户白名单登记

**依赖**: T-02

**改动**：`src/backend/bisheng/core/database/tenant_filter.py`（1 个文件）

`_TENANT_AWARE_MODEL_MODULES` 追加：

```python
"bisheng.doc_extract.domain.models.extract_template",
"bisheng.doc_extract.domain.models.extract_run",
```

**为什么**（design §2 硬前提 + 坑 13）：该常量是租户过滤的**模型白名单**，`register_tenant_filter_events()` 有 `_initialized` 一次性保护，`_discover_tenant_aware_tables()` **扫过就不再扫**。不在白名单且不在导入链上的模型，其表会**静默绕过** `before_flush` 自动填充与 `do_orm_execute` 过滤 —— v2.5 就是这样把 `knowledgefile`/`flowversion` 的数据写进了 root 租户。**只加 `tenant_id` 列不够。**

**冲突提示**：F059 已在此列表插入过一行。**追加即可，不要重排**。

**验证**：跑 T-22 的跨租户隔离用例。

**覆盖 AC**: AC-35

---

#### T-06 模型槽位 `DOC_EXTRACT_LLM`

**依赖**: 无

**改动**：`src/backend/bisheng/common/models/config.py`（1 个文件）

`ConfigKeyEnum` 新增：

```python
DOC_EXTRACT_LLM = "doc_extract_llm"  # Document extraction default model configuration
```

**为什么必须有这个任务**：design 决策 10 要求模型槽位独立、不蹭 `knowledge_llm`；slot 落在 **`tenant_system_model_config`** 表（F022 之后系统级模型槽位在此，**不在 `config` 表**），读写走 `TenantSystemModelConfigDao`。spec §3.1 序号 3（租户未配置模型 → 11608）与 T-20 的用例 1 都依赖它存在。

**验证**：`ConfigKeyEnum.DOC_EXTRACT_LLM.value == "doc_extract_llm"`；确认 `tenant_system_model_config` 中无同名键冲突。

**覆盖 AC**: AC-01（spec §3.1 序号 3 的前置条件）

---

### Wave A — 纯函数层（Test-First，零外部依赖）

#### T-07 字段校验器测试

**依赖**: T-01

**改动**：新增 `src/backend/test/doc_extract/test_field_validator.py`（1 个文件）

**覆盖要点**：
- 四种类型（string / number / date / boolean）各自的通过与失败
- **必填字段缺失 → 失败**（AC-14 后半）；**非必填字段缺失 → 不失败**（AC-14 前半）
- **空字符串与字段缺失同等视为「未抽取到」**（AC-15）
- **额外字段被忽略、不判失败、不触发重试**（AC-27）
- 错误信息**带 `field_key` / 期望类型 / 实际值**（AC-26 的落点）

**覆盖 AC**: AC-14, AC-15, AC-26, AC-27

---

#### T-08 字段校验器实现

**依赖**: T-07

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/field_validator.py`（1 个文件）

**逻辑**：输入 `(fields: dict, schema: list[dict])` → 输出 `(ok: bool, errors: list[dict])`。错误项含 `field_key` / `expected` / `actual`，供重试时回灌给模型。

**不做什么**：不调模型、不碰模板存储。

**测试**: T-07 全部通过。

**覆盖 AC**: AC-14, AC-15, AC-26, AC-27

---

#### T-09 渲染器测试

**依赖**: T-01

**改动**：新增 `src/backend/test/doc_extract/test_renderer.py`（1 个文件）

**覆盖要点**：
- `{{field_key}}` 正常替换
- **未抽到的字段按「无内容」渲染**：不报错、**不残留占位符**、不影响其余字段（AC-29）
- 呈现形式为空 → 产出空文本，不报错（AC-11）
- 多字段混排时的替换顺序与边界

**特别注意**：本 feature 的语法是 `{{field_key}}`，**不是** `{{#var#}}`。不要误用 `PromptTemplateParser`（design 决策 7 / 坑 8）。

**覆盖 AC**: AC-28, AC-29

---

#### T-10 渲染器实现

**依赖**: T-09

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/renderer.py`（1 个文件）

**逻辑**：轻量 `{{field_key}}` 替换；未抽到的 key 渲染为空串。

**不做什么**：不做悬空引用校验（在保存时已拦，见 T-15）。

**测试**: T-09 全部通过。

**覆盖 AC**: AC-28, AC-29

---

#### T-11 提示词构造测试

**依赖**: T-01

**改动**：新增 `src/backend/test/doc_extract/test_prompt_builder.py`（1 个文件）

**覆盖要点**：
- 字段定义 → 抽取提示词：四种类型都正确序列化；必填/非必填标注正确
- 候选模板集 → 判定提示词：**只含已启用模板**（AC-17）；候选为空时的行为
- **提示词里不得出现任何具体文档类型的字面量**（design 决策 2 的核心 —— 枚举来自数据而非代码）

**覆盖 AC**: AC-16, AC-17, AC-25

---

#### T-12 提示词构造实现

**依赖**: T-11

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/prompt_builder.py`（1 个文件）

**逻辑**：两个纯函数 —— 字段定义序列化、判定候选集序列化。

**参照**：结构可参照 `knowledge/rag/pipeline/transformer/file_encoding.py:78` 的 `CLASSIFY_PROMPT`（LLM + 固定枚举 + 正则校验 + 失败兜底），**但枚举来自数据**（design 决策 2 / 坑 4）。

**不做什么**：不调模型。

**测试**: T-11 全部通过。

**覆盖 AC**: AC-16, AC-17, AC-25

---

### Wave B — 模板服务

#### T-13 模板变更留痕（复用 AuditLogService）

**依赖**: T-01

**改动**：`src/backend/bisheng/database/models/audit_log.py`、`src/backend/bisheng/api/services/audit_log.py`（2 个文件）

**三处枚举都要动，缺一会静默失效**（design 决策 11）：
- `EventType` 加本模块取值（参照 `CREATE_CHANNEL` / `DELETE_CHANNEL`）
- `ObjectType` 加 `DOC_EXTRACT_TEMPLATE = "doc_extract_template"`
- **`SystemId` 加本模块取值**
- `AuditLogService` 加 `create_/update_/delete_/enable_/disable_doc_extract_template` 方法（参照 `create_build_workflow`，`:253`）

**为什么必须动 `SystemId`**：审计页可见性白名单 `AuditLogDao._ui_visible_predicate()`（`audit_log.py:217`）是 `system_id IS NOT NULL OR action IN (_UI_VISIBLE_V2_ACTIONS)`。**`system_id` 为空且 `action` 不在白名单 → 留痕写了但审计页查不到**（不报错，静默）。

**注意**：既有查询入口 `AuditLogDao.get_audit_logs`（`:239`）**没有 `object_type` 过滤参数**，查阅靠 **`system_id` + `event_type`**。

**分层说明**：`domain/services` → `api/services/audit_log` 不违反 C1（RULE-5 只扫 `/api/endpoints/`），全仓库已有 10+ 处同向先例。

**冲突提示**：F059 尚未改此文件。

**验证**：建一个模板，去审计页按本模块筛选，应能看到该条。

**覆盖 AC**: AC-38

---

#### T-14 模板服务测试

**依赖**: T-04, T-05, T-13

**改动**：新增 `src/backend/test/doc_extract/test_template_service.py`（1 个文件）

**覆盖要点**：
- 创建成功 + 列表可见（AC-04）
- **新建默认启用**（AC-06）
- **名称 / 适用范围 / 零字段 / 缺标识 / 标识重复 / 类型越界 → 11603**（AC-10）
- **呈现形式为空可保存**（AC-11）
- **呈现形式悬空引用 → 11604**（AC-09）
- **标识重复 → 11602，且仅对未删除行判定**：删除后可用同标识重建（AC-07 + AC-08）
- **不存在 / 非本租户的模板 → 11611**，且不区分二者（AC-05）
- 停用的语义：从业务抽取排除（AC-12）
- 修改后即时生效（AC-13）
- 写成功后调 `AuditLogService` 留痕（AC-38）
- **断言「删除后同标识可重建」** —— 这是库层唯一约束最容易踩的坑（design 坑 11）

**覆盖 AC**: AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-38

---

#### T-15 模板服务实现

**依赖**: T-14

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/template_service.py`（1 个文件）

**逻辑**：CRUD 编排 + 保存前全部校验（名称非空、适用范围非空、字段定义合法、呈现形式悬空引用、**类型标识对未删除行唯一**）。写成功后调 T-13 的审计方法留痕（**失败不留痕**）。

**不做什么**：不做权限判定（由端点依赖提供，见 T-23）；不直接写 ORM（走 T-02 的 DAO）。

**测试**: T-14 全部通过。

**覆盖 AC**: AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-38

---

### Wave C — 解析接缝（text 路径）

#### T-16 解析适配器测试

**依赖**: T-01

**改动**：新增 `src/backend/test/doc_extract/test_parse_adapter.py`（1 个文件）

**覆盖要点**：
- 有文字层的文档 → 返回 `(text, route="text")`，文本非空（AC-22）
- 无文字层（或因故抽不出内容）→ 标记需转识别服务，**本阶段返回 11605**（AC-21 的图片侧在 T-35）
- 文本长度低于 **100 字符**（design §2 定值）→ 判定为「没抽出实质内容」
- 源文件不被修改（与 T-20 的 AC-30 用例呼应）

**测试降级**：**无** —— 本阶段只覆盖 text 路径，可用 fixture 文件离线跑通，全部用例可自动化。需要真实识别服务的用例属 T-35（那里才是降级点，理由见该任务）。

**覆盖 AC**: AC-21（text 侧）, AC-22

---

#### T-17 解析适配器实现（text 分支）

**依赖**: T-16

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/parse_adapter.py`（1 个文件）

**逻辑**：
- 文件落临时目录 → 走 `TempFilePipeline`（`knowledge/rag/temp_file_pipeline.py`）拿文本
- **探测文字层定 route**：本地抽取产出的文本长度 ≥ **100 字符**（design §2）→ `route="text"` 且直接用；否则标记需转识别服务
- 返回 `(text: str, route: str)`

**关键约束**：
- **本文件是本模块唯一接触 knowledge loader 栈的文件**（design 决策 5）—— 后续 OCR/VLM 选型变化只动这一处
- **只调用、不修改 `TempFilePipeline`** —— 它已有 **4 处既有调用点**（`workbench_impl.py:456` 与 `:1281`、`input.py:309`、`chat_service.py:72`），改它等于改四个场景（design 坑 5）
- 不要复制「选哪个 loader」的逻辑 —— 那是 `_build_pdf_loader` 的单一来源（design 坑 6）

**本任务范围**：**只做 text 分支**。`ocr` 分支属 T-35。

**测试**: T-16 全部通过。

**覆盖 AC**: AC-21（text 侧）, AC-22

---

### Wave D — 文档类型判定

#### T-18 判定服务测试

**依赖**: T-11, T-15

**改动**：新增 `src/backend/test/doc_extract/test_classifier_service.py`（1 个文件）

**覆盖要点**：
- 候选集**只含本租户已启用模板**（AC-17）：停用、已删、其他租户的模板不得入选
- 置信度 ≥ 阈值 → 返回命中类型与置信度（AC-16）
- **置信度 < 阈值 → 11606**（AC-19）
- **租户无已启用模板 → 11606**（spec §3.1 序号 6）
- 模型返回非 JSON / 非法格式 → 按失败处理并落到 11608
- 模型调用超时 → 11608
- **LLM 全部 mock**

**覆盖 AC**: AC-16, AC-17, AC-18, AC-19

---

#### T-19 判定服务实现

**依赖**: T-18

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/classifier_service.py`（1 个文件）

**逻辑**：拼装判定提示词（T-12）→ 调 `LLMService.get_bisheng_llm_sync(model_id=..., app_id=..., app_type=..., user_id=...)`（参照 `knowledge_space_auto_tag_service.py:70`）→ 解析结果 → 与阈值 **0.6**（design §2）比较。

**不做什么**：**不选模板**（只报类型与置信度；「采用命中类型对应的模板执行抽取」是 T-21 编排层的动作，即 AC-18 的后半段 —— 其断言在 T-20 用例 7b，不在本任务的测试里）。

**测试**: T-18 全部通过。

**覆盖 AC**: AC-16, AC-17, AC-19

---

### Wave E — 抽取主流程

#### T-20 抽取服务测试

**依赖**: T-08, T-10, T-17, T-19

**改动**：新增 `src/backend/test/doc_extract/test_extraction_service.py`（1 个文件）

**spec §3.1 前置校验表（11 条，按序）** —— 本任务的测试必须逐条覆盖：

| 序 | 条件 | 适用模式 | 码 |
|---|---|---|---|
| 1 | 文档不可读或为空 | 全部 | 11609 |
| 2 | 文档超出体量上限（20 MB） | 全部 | 11610 |
| 3 | 当前租户未配置本特性所用模型 | 全部 | 11608 |
| 4 | 文档无文字层且环境未配置可用识别服务 | 全部 | 11605 |
| 5 | 指定的文档类型不存在或已停用 | 仅 M-1a + M-2a | 11601 |
| 6 | 当前租户无已启用模板 | 仅 M-1a + M-2b | 11606 |
| 7 | 判定阶段模型调用失败或超时 | 仅 M-1a + M-2b | 11608 |
| 8 | 判定置信度低于阈值 | 仅 M-1a + M-2b | 11606 |
| 9 | 识别服务调用失败或超时 | 全部 | 11608 |
| 10 | 抽取阶段模型调用失败或超时 | 全部 | 11608 |
| 11 | 模型结果不符字段定义，且重试耗尽 | 全部 | 11607 |

**覆盖要点**：
1. **上表逐条**，且**构造同时命中多条的请求，断言返回序号最靠前者**（AC-01）
2. **序号 1–8 被拒 → 不落记录**（AC-02）
3. **序号 9–11 失败 → M-1a 落失败记录、M-1b 不落**（AC-03）
4. **M-2a 不经过判定**（AC-20）：断言无判定调用、`determined_by='explicit'`
5. **抽取成功 → 产出符合字段定义的结构化结果**（AC-25）
6. **模型结果不符定义 → 重试 ≤2 次，耗尽 → 11607 且指明字段**（AC-26）
7. **额外字段 → 忽略并判通过，不重试**（AC-27）
7b. **M-2b 且置信度达阈值 → 采用命中类型对应的模板执行抽取**（AC-18）：断言编排层用的是判定返回的那个模板
8. **M-1a 成功 → 落记录，`determined_by='auto'`**（AC-31）
9. **M-1b 试抽取 → 不落记录**（AC-34）
10. **不修改源文档、不写知识库、不产生向量**（AC-30）：用 mock 断言全程无 `KnowledgeFile` 写入、无向量库调用、源文件内容与 mtime 不变
11. **`dry_run=True` 且调用者非管理员 → 拒绝**（design 决策 13）—— 这是防越权探测的门槛，必须在服务层单测里钉住；同时断言拒绝走的是**失败路径而非 `ok:false` 的 200 响应**

**测试降级**：无。需要 DB 的部分用既有 fixture；LLM 与解析全程 mock，可离线跑通。

**覆盖 AC**: AC-01, AC-02, AC-03, AC-20, AC-22, AC-25, AC-26, AC-27, AC-30, AC-31, AC-33, AC-34

---

#### T-21 抽取服务实现

**依赖**: T-20

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/extraction_service.py`（1 个文件）

**逻辑**（design §4.1）：前置校验（按上表 §3.1）→ `parse_adapter`（T-17）→ 判定（T-19，**M-2b 时**；命中后采用其模板，即 AC-18 后半）→ 抽取（LLM + T-08 校验 + 重试 ≤2）→ 渲染（T-10）→ 落记录（M-1a：成功与序号 9–11 的失败均落）。

**关键约束**：
- **模型槽位解析**：`model_id` 由 `ConfigKeyEnum.DOC_EXTRACT_LLM`（T-06）经 `TenantSystemModelConfigDao` 解析取得；解析不到即 spec §3.1 序号 3 的 11608（租户未配置模型），**在解析文档前就拒绝**
- **同步执行，超时上限 120 秒**（design §2 / 决策 14）—— 绝不无限挂起
- **重试次数 2**（含首次共 3 次尝试）
- 模型调用失败/超时**不重试**（AC-24 的 11608），与「结果不合规需重试」是两回事
- `determined_by` 落 `'explicit'`（M-2a）或 `'auto'`（M-2b）
- **`ExtractionService.extract()` 是本 feature 对外的内部 Python API**（design §6.1），后续场景接入直接依赖它

**不做什么**：不实现校验器与渲染器的内部逻辑（已由 T-08 / T-10 提供）。

**还需在本文件内实现 `dry_run` 的管理员门槛**（design 决策 13）—— **这是该门槛的唯一执行点**，端点层（T-24）不重复校验：`dry_run=True` 时校验调用者是否为租户管理员或平台超管，否则拒绝。判定复用 `_check_is_global_super(user_id)` 或 `user.has_tenant_admin(get_current_tenant_id())`，**抽成共享 helper 放在本文件**，供 T-24 的端点复用 —— 避免与 `get_tenant_admin_user` 口径漂移。**这一步不可省**：否则普通用户可借 `dry_run=true` 探测无权访问的模板。

**测试**: T-20 全部通过。

**覆盖 AC**: AC-01, AC-02, AC-03, AC-18, AC-20, AC-22, AC-25, AC-26, AC-27, AC-30, AC-31, AC-33, AC-34

---

### Wave F — API 层

#### T-22 API 集成测试

**依赖**: T-15, T-21

**改动**：新增 `src/backend/test/doc_extract/test_doc_extract_api.py`（1 个文件）

> 本任务**排在实现（T-23 ~ T-26）之前**，按契约先写断言（Test-First）。

**覆盖要点**：
- 模板 CRUD 的 happy path + 各错误码的对外表现（AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13）
- **非管理员访问 templates → 拒绝**（AC-36）
- **非管理员访问 records → 拒绝**（AC-36 / AC-32）
- **登录用户可发起 M-1a**（AC-37）
- **非管理员带 `dry_run=true` → 拒绝，且返回 HTTP 错误码而非 `ok:false`**（AC-36）
- **跨租户隔离**：租户 A 的模板在租户 B 不可见、不可用于判定（AC-35）
- 试抽取失败返回 `ok:false` + 正确的 `reason`（AC-44）
- 试抽取成功返回 `missing_fields` 与 `template.field_schema`（AC-45）
- **编辑器定义不合法 → 拒绝试抽取**（AC-42）
- **模板已被删 → 11611**（AC-43）
- 记录检索的筛选与游标分页（AC-32）

**覆盖 AC**: AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-32, AC-35, AC-36, AC-37, AC-42, AC-43, AC-44, AC-45

---

#### T-23 端点：模板 CRUD

**依赖**: T-15

**改动**：新增 `src/backend/bisheng/doc_extract/api/endpoints/templates.py`（1 个文件）

`GET/POST/PUT/DELETE /api/v1/doc_extract/templates[/{id}]`。**启停走 `PUT /templates/{id}` 带 `{enabled: 0|1}`**（不另开动词；`enable`/`disable` 是 T-13 的审计事件名，不是 API 路径）。鉴权 **`Depends(UserPayload.get_tenant_admin_user)`** —— 该依赖第一分支即 `_check_is_global_super`，已天然涵盖平台超级管理员，与 AC-36 完全吻合（design 决策 8 / 坑 9、10）。

**不做什么**：无业务逻辑，不自校验（校验在 T-15）。

**测试**: T-22 中模板相关用例通过。

**覆盖 AC**: AC-36

---

#### T-24 端点：判定 / 抽取

**依赖**: T-21

**改动**：新增 `src/backend/bisheng/doc_extract/api/endpoints/extraction.py`（1 个文件）

`POST /api/v1/doc_extract/classify`、`POST /api/v1/doc_extract/extract`。

**请求契约**（design §4.2）：`file` / `doc_type`(可选) / `dry_run`(默认 false) / **`template_id`(M-1b 必填)** / `trial_definition`(M-1b 必填)。

**鉴权**：Depends 层为**登录用户**。`dry_run=true` 的管理员校验**唯一执行点是 T-21 的 `ExtractionService.extract()` 内部**（本端点不做第二次校验）—— 端点只负责把 `dry_run` 透传下去。

**为什么在服务层**（design 决策 13）：FastAPI 的 `Depends` 链**看不到请求体**，无法按 `dry_run` 分流；而拆端点又与 spec 的「同一动作、不同目的」定义不符。**该校验不可省** —— 否则普通用户可借 `dry_run=true` 探测无权访问的模板。

**管理员判定复用**：`_check_is_global_super(user_id)` 或 `user.has_tenant_admin(get_current_tenant_id())`。**抽成共享 helper**，避免与 `get_tenant_admin_user` 口径漂移。

**返回契约**：
- M-1a：成功 `ok:true` + `fields`/`markdown`/`template`/`confidence`/`route`/`missing_fields`；**失败走 HTTP 错误码**
- M-1b：**失败也走 200 + `ok:false`** + `error_code`/`error_message`/`reason`；**唯一例外是 `forbidden`（越权）必须走 HTTP 错误码**，不得被 200 吞掉

**测试**: T-22 中判定/抽取/试抽取用例通过。

**覆盖 AC**: AC-36, AC-37, AC-42, AC-43, AC-44, AC-45

---

#### T-25 记录检索：service + 端点

**依赖**: T-21

**改动**：新增 `src/backend/bisheng/doc_extract/domain/services/record_query_service.py`、`src/backend/bisheng/doc_extract/api/endpoints/records.py`（2 个文件）

**service 职责**（design §4.3）：抽取记录检索编排 —— 按模板 / 时间范围 / 成功失败筛选 + 游标分页（`docs/architecture/13-cursor-pagination.md`）。**不做写入**（写入在 T-21）。

**端点**：`GET /api/v1/doc_extract/records`，`Depends(get_tenant_admin_user)`。

**为什么门槛与模板管理一致**：记录含抽取出的字段内容，可能含证照类敏感信息（AC-32）。

**测试**: T-22 中记录检索用例通过。

**覆盖 AC**: AC-32

---

#### T-26 Router 注册

**依赖**: T-23, T-24, T-25

**改动**：新增 `src/backend/bisheng/doc_extract/api/router.py`；`src/backend/bisheng/api/router.py` 追加注册（2 个文件）

照 `sensitive_word` 的两行模式（`from ... import router as doc_extract_router` + `router.include_router(doc_extract_router)`）。

**冲突提示**：F059 尚未改 `bisheng/api/router.py`。**只追加自己那两行**。

**验证**：起服务，`/docs` 能看到 `/api/v1/doc_extract/*` 全部端点。

**覆盖 AC**: —

---

### Wave G — 前端 Platform（手动验证）

#### T-27 前端 API 层

**依赖**: T-26

**改动**：新增 `src/frontend/platform/src/controllers/API/docExtract.ts`（1 个文件）

导出函数（供 T-29 ~ T-32 对齐）：

```
listTemplates() / getTemplate(id) / createTemplate(data) / updateTemplate(id, data)
/ deleteTemplate(id) / toggleTemplate(id, enabled)
classify(file) / extract({file, doc_type?, dry_run?, template_id?, trial_definition?})
listRecords({template_id?, status?, start_time?, end_time?, cursor?})
```

**关键约束**：
- 用 `@/controllers/request.ts` 的封装，**不得直接 `import axios`**
- **不用 `react-query` v3**（已冻结，lint-enforced）—— 走 API Layer Pattern 手写
- **平台请求拦截器已解包 `response.data.data`**（对 `status_code===200` 直接返回内层）—— 再写 `res?.data` 恒为 `undefined`（design 坑 14）

**验证**：页面调用后能在 network 面板看到请求、且返回值非 `undefined`。

**覆盖 AC**: —

---

#### T-28 i18n namespace

**依赖**: 无

**改动**：新增 `src/frontend/platform/public/locales/{zh-Hans,en-US,ja}/docExtract.json`（3 个文件）

**三语同 PR 交付**。**不得硬编码中文**（lint-enforced）。

**验证**：`pnpm check-i18n` 通过（键在三种语言间对齐）。

**覆盖 AC**: —

---

#### T-29 模板管理 tab（列表 + 编辑页骨架）

**依赖**: T-27, T-28

**改动**：新增 `src/frontend/platform/src/pages/BuildPage/bench/DocExtractTemplates.tsx`、`DocExtractTemplateEdit.tsx`（2 个文件）

> 编辑页中的「字段定义区」与「呈现形式区」**由 T-30 提供组件**，本任务只搭骨架并预留挂载位。

**列表**：名称 / 类型标识 / 字段数 / 状态 / 操作（编辑、启停、删除）+ 新建。

**编辑页**：基本信息（名称 / 类型标识 / 适用范围）+ 字段定义区（T-30）+ 呈现形式区（T-30）+ 保存。

**手动验证**：
- 起 platform（`:3001`）→ 进 `对话工作台 → 文档抽取` tab
- 新建模板 `doc_type=business_license`，字段含 `company_name`（必填）与 `valid_to`（非必填）
- 保存 → 列表可见、默认启用
- **6 类非法输入逐一验**：空名称、零字段、缺标识、字段标识重复、字段类型越界、呈现形式引用不存在的字段 → **保存前即被拦并提示**（AC-46）
- **类型标识与既有未删除模板冲突 → 保存被拦**（AC-46 / AC-07，返 11602）
- 删除模板 → 用同标识可重建（AC-08 的前端侧）

**覆盖 AC**: AC-39, AC-46

---

#### T-30 字段 Schema 编辑器 + 呈现模板编辑器

**依赖**: T-29

**改动**：新增 `src/frontend/platform/src/pages/BuildPage/bench/DocExtractComponents/FieldSchemaEditor.tsx`、`RenderTemplateEditor.tsx`（2 个文件），并挂进 T-29 预留的挂载位。

`FieldSchemaEditor`：增行 / 删行 / 改字段标识 / 中文名 / 类型（下拉）/ 必填（开关）。

`RenderTemplateEditor`：编辑呈现形式，**提示当前可用字段变量，点击插入**（AC-40 的落点）。

**拆分原因**：单文件 ≤ 600 行（根 `AGENTS.md §4`）。`FieldSchemaEditor` 本身即带状态的表格组件，塞进编辑页必然超限。

**手动验证**：加两个字段后，呈现形式编辑器的变量提示条应出现这两个 key；点击能插入 `{{key}}`。

**覆盖 AC**: AC-40

---

#### T-31 试抽取弹窗

**依赖**: T-29

**改动**：新增 `src/frontend/platform/src/pages/BuildPage/bench/DocExtractComponents/TrialExtractDialog.tsx`（1 个文件）

传样件 → 展示判定结果（类型 + 置信度）、`route`、结构化结果与呈现结果、**未抽到的字段标识**。

**关键约束**：
- **作用于编辑器中未保存的定义**，且**恒用当前编辑的这个模板**，不因判定命中别的类型而改用其他模板（AC-41）
- 试抽取失败 → **走 `ok:false` 的 200 响应**展示原因，**不依赖 HTTP 错误码 + silent**（design 坑 15：`captureAndAlertRequestErrorHoc` 对 `silent` 请求仍会弹 toast）
- **定义不合法 → 拒绝试抽取并告知**（AC-42）
- **模板已被删 → 11611**（AC-43）
- 失败原因按 `reason` 枚举分类展示（AC-44）
- 成功时标识未抽到的字段（AC-45）
- **不写回模板**，未保存的改动不因此被保存或丢弃（AC-41）

**手动验证**：
- 改字段定义后**不保存**，直接点「试抽取」→ 应跑通且用的是改动后的定义
- 试抽取后未保存的改动仍在（未被保存也未被丢弃）
- 传一个空文件 → 应展示 11609 的原因

**覆盖 AC**: AC-41, AC-42, AC-43, AC-44, AC-45

---

#### T-32 抽取记录 tab

**依赖**: T-27, T-28

**改动**：新增 `src/frontend/platform/src/pages/BuildPage/bench/DocExtractRecords.tsx`（1 个文件）

筛选（模板 / 时间范围 / 成功失败）+ 列表 + 详情（`fields` / `markdown` / `preprocess_route` / `determined_by` / `confidence`）。游标分页。

**手动验证**：跑一次业务抽取后，该条应出现在列表；按模板与状态筛选生效；详情里 `route` 与 `determined_by` 正确。

**覆盖 AC**: AC-32

---

#### T-33 tab 宿主与入口可见性

**依赖**: T-29, T-31, T-32

**改动**：`src/frontend/platform/src/pages/BuildPage/bench/DialogueWork.tsx`（1 个文件）

新增「文档抽取」tab（照 F056 的 `guardrail` tab 写法）。

**关键约束 —— AC-36 的落点**：该文件的 `TabsList` **目前是无条件渲染的**，非管理员只要看到 tab 入口就违反 AC-36。收口方式二选一：走角色菜单 `web_menu`（`layout/MainLayout.tsx:130` 的既有机制），或在 tab 渲染处按用户角色条件渲染。**不得默认无条件放出。**

**手动验证**：用非管理员账号登录 → 进对话工作台 → **不应看到**「文档抽取」tab；管理员账号 → 应看到。**两侧都要验**（只验一侧无法证明收口生效）。

**覆盖 AC**: AC-36

---

### Wave H — 第二阶段与收尾

#### T-34 三语错误码文案

**依赖**: T-01

**改动**：`src/frontend/packages/locales/src/api_errors/{zh-Hans,en,ja}.json`（3 个文件）

各加 11601–11611 十一条。

**为什么**：错误码文案的唯一来源是 `packages/locales`（`api_errors` 域）。**CI 的 `pnpm check-i18n` 会校验后端错误码覆盖率，漏加会导致 CI 失败**。生成产物（`platform/public/locales/*/api_errors.json`、`client/src/locales/*/api_errors.gen.json`）**不得手工编辑**。

**冲突提示**：F059 尚未改此文件。

**覆盖 AC**: —

---

#### T-35 解析适配器：OCR 分支（第二阶段）

**依赖**: T-17

**改动**：`src/backend/bisheng/doc_extract/domain/services/parse_adapter.py` + `src/backend/test/doc_extract/test_parse_adapter_ocr.py`（2 个文件）

**逻辑**：无文字层且环境配了识别服务 → 经该服务获得内容，`route="ocr"`（AC-23）；未配置 → **11605**，并指明缺失哪项配置。

**为什么排在这里**：**图片链路能否跑通完全取决于环境是否配了远程解析服务**，而这一点需求澄清时是「待确认」状态。排在 Wave H 意味着 —— **即使识别服务最终不可用，Wave 0 ~ G 交付的底座仍然完整可用**（design §9）。

**识别服务已配置但调用失败 → 11608**（AC-24），与模型调用失败同码。

**备选路径**：若环境配的是多模态模型而非 OCR，可参照 `workstation/domain/services/chat_service.py:1559` 或 `core/ai/llm/chat_openai_reasoning.py:37` 的现成写法构造图片消息（design 坑 2）。**但不得为此新增外部服务配置项** —— 那违反 design §1 的非目标。

**Test-First 说明**：本任务把测试与实现合并在同一任务内 —— 因为两者的边界由**外部服务的实际响应形态**决定，在拿到真实响应前无法先写断言。

**测试降级**：依赖真实识别服务的用例标 `**测试降级**: 手动验证`。**理由**：识别服务是**外部依赖**，且其可用性在本特性开工时尚未确认；为此搭一套 OCR mock 服务的成本远高于收益，而 mock 出的行为也无法证明真实服务可用。

**降级的后果与兜底**（重要，勿掩盖）：**AC-23 / AC-24 在识别服务不可用时不具备自动化验证手段**，只能靠人工对着真实服务跑一遍。替代方案（`parse_adapter` 内注入 mock 客户端以断言分流逻辑）**降级为可选**：若 T-36 执行时环境已有可用识别服务，则把 AC-23/AC-24 的断言并入 T-36 并解除本降级；若没有，必须在 `e2e-report.md` 与「实际偏差记录」中**显式写明这两条 AC 未被自动化验证**，不得默认视为通过。

**覆盖 AC**: AC-21（图片侧）, AC-23, AC-24

---

#### T-36 端到端测试

**依赖**: T-22, T-33（**不依赖 T-35**）

**改动**：新增 `src/backend/test/e2e/test_e2e_f058_doc_extract.py`，产出 `e2e-report.md`（2 个文件）

**覆盖链路**：模板 CRUD → 试抽取 → 业务抽取 → 记录检索，以及审计留痕可见性。

**必须覆盖的两个跨租户 / 权限场景**（单测容易漏）：
- 租户 A 的模板在租户 B 不可见、不可用于判定（AC-35）
- 非管理员既看不到 tab 入口、也无法用 `dry_run` 绕过（AC-36）

**覆盖 AC**: AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18, AC-19, AC-20, AC-21, AC-22, AC-25, AC-26, AC-27, AC-28, AC-29, AC-30, AC-31, AC-32, AC-33, AC-34, AC-35, AC-36, AC-37, AC-38, AC-41, AC-42, AC-43, AC-44, AC-45

**不覆盖**（本文件是 API 级 e2e，`test/e2e/` 下全部 26 个用例均无浏览器驱动）：纯界面 AC —— AC-39（管理界面存在）、AC-40（逐字段编辑 + 变量提示）、AC-46（保存前拦截非法输入）。这三条由 T-29 / T-30 的手动验证覆盖；AC-36 的入口可见性由 T-33 的手动验证覆盖（本文件只断言其 API 侧拒绝）。
**另**：AC-23 / AC-24 的覆盖取决于识别服务可用性，见 T-35 的降级说明。

---

#### T-37 版本契约回填与文档同步

**依赖**: T-36

**改动**：`features/v2.8.0/058-doc-extract-template/{design.md,tasks.md}`、`features/v2.8.0/release-contract.md`（3 个文件）

- 若实现中产生偏差 → 更新 `design.md`（**只留今天的状态**）并在本文件「实际偏差记录」写一行指针
- 核对 `release-contract.md` 的模块编码表与 INV 表与实际实现一致
- 更新本文件顶部状态表

**覆盖 AC**: —

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- （待填）
