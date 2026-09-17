# 文档抽取模板（Doc Extract Template）设计

- **日期**：2026-09-17
- **状态**：设计已确认，待写实现计划
- **定位**：能力底座。本次交付后端能力 + 平台端模板管理页；对话附件 / 工作流节点 / 知识库元数据等场景接入留待后续迭代。

---

## 1. 背景与问题

针对特定文档类型（身份证、营业执照、发票、合同等）做结构化抽取，目前平台没有对应机制。现状：

| 已有能力 | 位置 | 局限 |
|---|---|---|
| 文档分类（LLM + 固定枚举） | `knowledge/rag/pipeline/transformer/file_encoding.py::CLASSIFY_PROMPT` | 枚举硬编码首钢文档类型 × 业务域，非通用 |
| 文档解析（多 loader） | `knowledge/rag/pipeline/loader/` | 产出 markdown 供检索，无结构化字段概念 |
| 临时解析（不落库） | `knowledge/rag/temp_file_pipeline.py::TempFilePipeline` | 已被 4 个场景复用，是最合适的接缝 |
| `{{var}}` 变量替换 | `workflow/nodes/prompt_template.py` | `{{#var#}}` 语法 + 变量剥离，语义不同 |
| docx 模板填充 | `workflow/nodes/report/docx_replace.py::DocxReplacer` | 产出 docx，非结构化字段 |

**关键缺口**：全仓库无 `response_format` / `json_schema` / `with_structured_output` 任何结构化输出支持。LLM 层的 `LLMModelType` 仅有 `llm / embedding / rerank / asr / tts`，无 vision 类型，也无多模态消息构造代码。

本设计**不发明新机制**，而是把上述已有模式一般化：把硬编码的文档类型枚举抽成管理员可配的数据，把 loader 栈接成可复用的抽取前置。

---

## 2. 已确认的设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 使用场景：先做**能力底座**，场景后续接 | 用户明确选择 |
| D2 | 模板形态：**字段 Schema + 呈现模板**双段 | 上游可消费（JSON）+ 下游可读（Markdown），且可在保存期校验 |
| D3 | 类型判定：**先自动分类，命中则套模板**；调用方可显式指定 `doc_type` 强制跳过 | 兼顾无感与可控 |
| D4 | 输入形态：**图片 + 可解析文本 PDF/Word 都要支持** | 业务实际两种都有 |
| D5 | 交付范围：**后端能力 + 平台端模板管理页** | 工作流节点等后续 |
| D6 | 模块落点：**新建 `bisheng/doc_extract/` 模块** | release-contract 要求领域对象唯一 Owner；边界干净 |
| D7 | 落 `doc_extract_run` 记录表 | 试抽取要能回看、场景接入要可追溯、企业审计常态 |
| D8 | 模型槽位**租户级**，不做模板级覆盖 | 先简单，需要时再加 |
| D9 | 抽取**先同步**，API 留异步口子 | 当前量级同步够用；一旦上扫描件 + 本地推理模型，分钟级是常态，届时改接口是破坏性变更 |
| D10 | 分类与抽取**分两次模型调用** | 一次调用会让「候选集 + 置信度」与「字段 Schema 约束」的输出格式互相打架；分类结果可缓存、可人工覆盖 |
| D11 | 试抽取**不落库**（dry_run） | 调模板时反复试不污染记录表 |

---

## 3. 架构

### 3.1 模块布局

```
src/backend/bisheng/doc_extract/
├── api/
│   ├── router.py
│   └── endpoints/
│       ├── templates.py        # 模板 CRUD
│       └── extraction.py       # 分类 / 抽取
└── domain/
    ├── models/
    │   ├── extract_template.py
    │   └── extract_run.py
    ├── schemas/doc_extract_schema.py
    └── services/
        ├── template_service.py
        ├── classifier_service.py
        ├── extraction_service.py
        └── parse_adapter.py     # 文档预处理接缝
```

分层仿 `bisheng/sensitive_word/`（同为近期新增的小模块，结构最贴合）。

**`parse_adapter.py` 是本模块唯一接触 knowledge loader 栈的文件**。架构守卫 RULE-5 只禁止 **API 层**跨模块互相导入；service 层复用 knowledge 的 loader 栈是合规的。将该依赖收敛到单一文件，使得后续 OCR/VLM 选型变更时改动面可控。

### 3.2 领域对象归属（对齐 release-contract）

本设计新增两个领域对象，需在 `features/v2.7.0/release-contract.md` 表 1 登记：

| 领域对象 | Owner | 说明 |
|---|---|---|
| 抽取模板（`doc_extract_template`） | 本特性 | 新增独立表；仅本特性写入 |
| 抽取记录（`doc_extract_run`） | 本特性 | 新增独立表；仅本特性写入 |
| 文档解析能力（loader 栈 / TempFilePipeline） | knowledge 模块 | **非 Owner**，仅读取/调用，不修改其行为 |

### 3.3 数据模型

**表 1：`doc_extract_template`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | int | 多租户，由 C3 ContextVar 自动注入 |
| `name` | varchar(128) | 模板名，如「营业执照」 |
| `doc_type` | varchar(64) | 类型标识，如 `business_license`；同租户内唯一 |
| `description` | text | **判定说明** —— 分类时喂给模型的该类描述 |
| `field_schema` | JSON | 字段定义数组（见下） |
| `render_template` | text | 呈现模板 |
| `enabled` | smallint | 是否启用（仅启用的模板进入分类候选集） |
| `create_user` / `create_time` / `update_time` | | 审计字段 |

唯一约束：`UNIQUE(tenant_id, doc_type)`。

`description` 字段的设计意图：它就是现有 `CLASSIFY_PROMPT` 中「适用于国家/行业法规、集团红头文件、管理政策…」这种逐类描述的数据化。分类时由所有启用模板的 `name + description` 动态拼出候选枚举，替换硬编码。

`field_schema` 结构：

```json
[
  {"key": "company_name", "label": "企业名称",         "type": "string", "required": true},
  {"key": "credit_code",  "label": "统一社会信用代码", "type": "string", "required": true},
  {"key": "valid_to",     "label": "营业期限至",       "type": "date",   "required": false}
]
```

`type` 取值（v1 仅标量）：`string` / `number` / `date` / `boolean`。

**不含 `array` / `object`** —— 嵌套类型的校验与渲染复杂度显著更高，而身份证、营业执照这类目标场景均为扁平标量。发票明细行等嵌套结构留待后续，届时扩展 `type` 枚举即可，不影响表结构。

**表 2：`doc_extract_run`**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `tenant_id` | int | |
| `template_id` | int | 命中的模板 |
| `doc_type` | varchar(64) | 冗余，便于模板改名后追溯 |
| `source_file_name` | varchar(512) | |
| `classify_confidence` | float | 分类置信度；显式指定类型时为 NULL |
| `preprocess_route` | varchar(16) | **走的哪条预处理链路**：`text` / `ocr` / `vision`，排障用 |
| `fields` | JSON | 抽取出的结构化字段 |
| `markdown` | text | 渲染结果 |
| `status` | smallint | 成功 / 失败 |
| `error_code` / `error_message` | | 失败原因 |
| `user_id` / `create_time` | | |

**模型槽位**：新增 `ConfigKeyEnum.DOC_EXTRACT_LLM = "doc_extract_llm"`（`bisheng/common/models/config.py`）。不蹭 `knowledge_llm` —— 两者语义不同，且证照字段抽取值得单独挂更强的模型。调用沿用 `LLMService.get_bisheng_llm_sync(model_id=..., app_id=..., app_type=..., user_id=...)`，与 `knowledge_space_auto_tag_service.py:70` 同模式。

---

## 4. 抽取执行链路

```
文件 ──→ ① 预处理 parse_adapter
          本地文字层可用？ ──是─→ loader 出 markdown（无 OCR 成本）  route=text
          否 ↓
          配了远程 OCR？ ──是─→ PaddleOCR / MinerU / ETL4LM         route=ocr
          否 ↓
          配了 VLM？ ──是─→ 图片送视觉模型                          route=vision
          否 └─→ 报 DOC_EXTRACT_NO_PARSER（明确告知缺哪个配置）

        ──→ ② 分类（调用方指定 doc_type 则跳过）
            候选集 = 本租户 enabled 模板的 name + description
            输出 {doc_type, confidence}  ≥ 阈值 → 命中
            低于阈值 → 报 DOC_EXTRACT_TYPE_UNKNOWN，交由调用方决策

        ──→ ③ 抽取
            LLM → json.loads → 逐字段校验 → 失败重试 ≤2
            仍失败 → 报字段级错误（哪个 key、期望类型、实际值）

        ──→ ④ 渲染  render_template + fields → Markdown

        ──→ ⑤ 落 doc_extract_run（dry_run 时跳过）

        返回 {fields, markdown, template, confidence, route}
```

### 4.1 复用面

预处理与知识库入库**共用同一条 loader 栈**。`TempFilePipeline` 已被灵思工作台（`linsight/domain/services/workbench_impl.py:456`）、工作流输入节点（`workflow/nodes/input/input.py:309`）、工作台对话（`workstation/domain/services/chat_service.py:63`）使用。OCR/VLM 选型走 `settings.get_knowledge()` 的既有 provider 配置（`ParseType.PADDLE_OCR` / `MINERU` / `ETL4LM`），**底座不新增一套服务配置**。

### 4.2 已知约束

`_init_image_loader()` 在本地 loader 模式下直接抛 `KnowledgeFileNotSupportedError()`（`knowledge/rag/base_file_pipeline.py:256`）。**图片链路能否走通完全取决于环境是否配置了远程 OCR 或 VLM 服务。**

因此实现顺序上，**解耦 OCR 依赖优先**：先打通「文本类文档 + 分类 + 抽取 + 渲染」这条不依赖外部服务的链路并可测，图片链路作为 `parse_adapter` 的第二个分支后接。即使服务可用性最终否掉，底座仍然可用。

### 4.3 结构化输出

无原生 JSON mode 可用，底座自建：提示词约束（把字段 Schema 序列化进 prompt）+ `json.loads` + 逐字段类型/必填校验 + 失败重试 ≤2 次。

在信创环境跑本地模型时，这条链路通常比依赖原生 JSON mode 更靠得住；代价是需要正经实现校验与重试。

### 4.4 渲染器

独立的轻量 `{{field_key}}` 渲染器。**不复用** `PromptTemplateParser`（`{{#var#}}` 语法且做变量剥离，语义不同），**不复用** `DocxReplacer`（产出 docx）。

**保存模板时即校验**：渲染模板中引用了 `field_schema` 中不存在的 key → 保存报错，而非运行期静默留空。

---

## 5. API

所有接口挂 `/api/v1/doc_extract`，在 `bisheng/api/router.py` 注册（照 `sensitive_word` 的 include 模式）。

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/templates` | 模板列表 | `get_tenant_admin_user` |
| POST | `/templates` | 新建模板 | `get_tenant_admin_user` |
| GET | `/templates/{id}` | 模板详情 | `get_tenant_admin_user` |
| PUT | `/templates/{id}` | 更新模板 | `get_tenant_admin_user` |
| DELETE | `/templates/{id}` | 删除模板 | `get_tenant_admin_user` |
| POST | `/classify` | 文档分类（候选集 + 置信度） | 登录用户 |
| POST | `/extract` | 抽取（`dry_run` 参数控制是否落库） | 登录用户 |

**权限模型**：模板是**租户级配置**，非资源级权限，因此走 `Depends(UserPayload.get_tenant_admin_user)`，**不接入 OpenFGA**。抽取接口对登录用户开放（后续场景接入时按调用方需要收窄）。

---

## 6. 前端

### 6.1 落点

`pages/BuildPage/bench/DialogueWork.tsx` 新增 tab（照 F056 LLM 护栏的 `LLMGuardrailPolicy` / `LLMGuardrailHitLogs` 模式）。

```
pages/BuildPage/bench/
├── DocExtractTemplates.tsx      # 列表 + tab 入口
├── DocExtractTemplateEdit.tsx   # 新建/编辑表单
└── DocExtractComponents/
    ├── FieldSchemaEditor.tsx    # 字段 Schema 编辑器（增删行）
    ├── RenderTemplateEditor.tsx # 呈现模板编辑器
    └── TrialExtractDialog.tsx   # 试抽取
```

拆分原因：单文件 ≤ 600 行硬限制（根 AGENTS.md §4）。`FieldSchemaEditor` 本身即一个带状态的表格组件，塞进编辑页必然超限。

### 6.2 页面结构

```
模板管理
├─ [模板列表]  名称 | 类型标识 | 字段数 | 状态 | 操作(编辑/复制/停用)
│              ＋新建模板
└─ [新建/编辑]
   ├─ 基本信息   名称 / 类型标识 / 判定说明(多行)
   ├─ 字段定义   FieldSchemaEditor
   │             key | 中文名 | 类型(下拉) | 必填(开关) | 删除
   ├─ 呈现模板   RenderTemplateEditor（可用变量提示条，点击插入）
   └─ [保存]  [试抽取 →]
```

**试抽取弹窗**：传样件 → 展示「识别类型 + 置信度 + 走的链路」→ 左右对照 JSON 与渲染结果 → 字段级高亮（缺失 / 类型不符）。**不落库**。

### 6.3 前端约定

- 按 `@/controllers/API/` 模式新增 `docExtract.ts`，用 `captureAndAlertRequestErrorHoc` 包裹
- **不用 `react-query` v3**（已冻结，lint-enforced）—— 走 API Layer Pattern 手写
- i18n 新 namespace `docExtract`，`zh-Hans` / `en-US` / `ja` **同 PR 交付**
- UI 组件用 `@/components/bs-ui/`，图标用 `@/components/bs-icons/`
- 403 由响应拦截器统一处理，业务代码不加 403 分支
- 不使用 `backdrop-filter` / `backdrop-blur-*`（根 AGENTS.md §4 明令）

---

## 7. 错误码

新增 `bisheng/common/errcode/doc_extract.py`（照 `sensitive_word.py` 的独立文件约定）。

| 错误码 | 场景 |
|---|---|
| `DocExtractTemplateNotFound` | 模板不存在 |
| `DocExtractTemplateTypeDuplicate` | 同租户 `doc_type` 重复 |
| `DocExtractTemplateInvalidSchema` | 字段 Schema 非法 |
| `DocExtractTemplateInvalidRenderRef` | 渲染模板引用了不存在的字段 key |
| `DocExtractNoParser` | 无可用解析器（未配 OCR / VLM 且文件无文字层） |
| `DocExtractTypeUnknown` | 分类置信度低于阈值 |
| `DocExtractFieldInvalid` | 抽取结果字段级校验失败（带 `field_key` / 期望类型 / 实际值） |
| `DocExtractLlmFailed` | 模型调用失败或重试耗尽 |

错误码需带 `template_id` / `field_key` 等上下文，供前端精确定位。

---

## 8. 测试

| 层 | 位置 | 覆盖 |
|---|---|---|
| 模块单测 | `test/doc_extract/` | 模板 CRUD、字段校验器、渲染器、分类解析与兜底、LLM 调用 mock |
| 表 DDL 常量 | `test/fixtures/table_definitions.py` | **须新增两张表的 SQLite DDL 常量**（现有约定为手写 DDL，非 `create_all`），这是单测跑起来的前提 |
| 端到端 | `test/e2e/test_e2e_f0XX_doc_extract.py` | SDD 强制项；`F0XX` 待版本号确定后落定 —— v2.7.0 当前最高为 F057，若纳入该版本则为 **F058** |

重点边界：

- LLM 返回非 JSON
- 返回 JSON 但缺必填字段
- 模板引用了不存在的字段 key
- 分类置信度低于阈值
- 无可用解析器
- 渲染模板含 `{{}}` 但字段值为 null

测试目录下须有 `__init__.py`；`asyncio_mode=auto`。

---

## 9. 明确不做（后续迭代）

- 工作流「文档抽取」节点
- 对话 / 助手附件接入
- 知识库入库元数据抽取
- 模板级模型覆盖
- 异步抽取任务 + 轮询
- 多页 / 多证件批量拆分
- 字段级置信度与人工纠错回写

---

## 10. 范围声明

本次交付的是一个**能力底座**，不是开箱即用的证照识别产品。

交付后可以：配置模板、试抽取验证、通过 API 被调用。

**不**包含：在对话里上传身份证就自动出卡片。那是场景接入，属下一期。

---

## 11. 实施顺序

1. **解耦 OCR 依赖优先** —— 先打通「文本类文档 + 分类 + 抽取 + 渲染」这条不依赖任何外部服务的链路，并可测
2. 图片链路作为 `parse_adapter` 的第二个分支后接
3. 前端管理页
4. e2e

第 1 步的独立可测性意味着：即使 OCR / VLM 服务可用性最终被否掉，底座本身仍然交付且可用。

---

## 12. 参考

- `docs/constitution.md` — C1（分层）/ C3（多租户）/ C4（权限）/ C7（前端 store）
- `features/v2.7.0/release-contract.md` — 领域对象归属表（本特性需登记）
- `docs/SDD-Guide.md` — SDD 流程
- `src/backend/AGENTS.md` — 后端编码约定
- `src/frontend/platform/AGENTS.md` — 平台端前端约定
