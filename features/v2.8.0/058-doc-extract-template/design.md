# Design: 文档抽取模板（058-doc-extract-template）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动
>
> 调整原则（详见 `docs/SDD-Guide.md` §3-§4）：
> - 实现变化 → **覆盖更新本文档**，只留"今天的状态"、不留旧快照；但每个决策保留"为什么 + 被否方案"和坑（护栏，见 §3/§5）
> - **偏差分级**：推翻已 ★ 确认的决策 → 停下与用户重新确认；纯实现细节 → 直接改 design，不必停

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.8.0
**最后更新**: 2026-09-17（设计阶段；实现尚未开始）

---

## 1. 目标与非目标

- **目标**：为平台提供「按文档类型做结构化抽取」的能力底座 —— 管理员把「文档类型 → 要抽哪些字段 + 结果长什么样」配成模板，调用方丢一份文档进来就能拿到结构化字段与可读文本。抽取前的文档解析复用知识库既有的 loader 栈，不新建解析体系。
- **非目标**：
  - **不是开箱即用的证照识别产品**：交付后能配模板、能试抽取、能被 API 调用；但「在对话里上传身份证自动出卡片」属场景接入，见 §8。
  - 不新增文档解析能力，不新增 OCR/VLM 服务配置项 —— 识别走既有 `settings.get_knowledge()` 的 provider 配置。
  - 不写入、不修改知识库的任何数据（spec AC-30）。
  - 不做嵌套字段类型（发票明细行等），v1 字段类型仅标量。
  - 不引入异步任务队列（v1 同步返回）。

---

## 2. 关键约束

本功能特有的硬性约束（决定 §3 方案对比的取舍空间）。

- **全局架构铁律遵循 `docs/constitution.md` C1–C7，本节不重抄。** 本 feature 的具体落点：C1（新建 `doc_extract` 三层模块，service 层复用 knowledge 的 loader 栈，不跨 API 层导入）、C2（新表的 JSON 列用 `dialect_helpers.JsonType`、长文本用 `LargeText`）、C3（两张新表均带 `tenant_id`，租户过滤由事件监听器自动注入，不手写 `WHERE tenant_id = X` —— **但仅带列还不够，必须登记进白名单，见下**）、C4（见决策 8）、C5（错误码模块 **114** —— 113 已被 v2.7.0 的 F059 占用，见 §6.1）、C7（前端取数走 `controllers/API/`，store 不调 HTTP）。
- **C3 的隐性前提：新模型必须登记进 `_TENANT_AWARE_MODEL_MODULES`**（`core/database/tenant_filter.py:39`）。该常量是租户过滤的**模型白名单**，`_force_import_all_models()` 靠它保证相关 ORM 模块在 `_discover_tenant_aware_tables()` 扫描前已导入；`register_tenant_filter_events()` 有 `_initialized` 一次性保护（`:143`），**扫过就不再扫**。不在白名单且不在导入链上的模型，其表会**静默绕过** `before_flush` 自动填充与 `do_orm_execute` 过滤 —— 该文件注释明确记录了 v2.5 就是这样把 `knowledgefile`/`flowversion` 等表的数据写进了 root 租户。**因此 `extract_template.py` / `extract_run.py` 两个模块路径必须加入该常量**，不能只依赖「路由注册会顺带导入」。
- **对话 LLM 层没有结构化输出支持**（见 §5 坑 1）：`core/ai/llm/` 下无 `response_format` / `json_schema` / `with_structured_output`。抽取结果的格式约束必须自建。（注意别被 grep 到的同名参数误导 —— TTS 与 rerank 客户端也有 `response_format`，但含义无关。）
- **图片链路是否可用取决于环境是否配了远程解析服务**（见 §5 坑 3）：本地 loader 模式下 `_init_image_loader()` 直接抛 `KnowledgeFileNotSupportedError`。因此「解耦 OCR 依赖」是本 feature 的实施顺序前提（§9）。
- **信创部署目标**：客户环境可能跑本地小模型，JSON mode 不可依赖；前端须避开 `backdrop-filter`（根 AGENTS.md §4 明令，信创浏览器性能）。
- **模型槽位是租户级**，不做模板级覆盖（spec 范围边界）。新增 `ConfigKeyEnum.DOC_EXTRACT_LLM`，落在 **`tenant_system_model_config`** 表（F022 之后系统级模型槽位在此，不在 `config` 表），读写走 `TenantSystemModelConfigDao`。
- **性能与容量口径**（spec 把这些列为设计参数，在此定值）：
  - **文档体量上限 20 MB**（超出 → 11610）。依据：证照类图片/PDF 典型在 1–5 MB，20 MB 足以覆盖高分辨率扫描件，同时避免单请求占用过多解析资源。
  - **分类置信度阈值 0.6**（低于 → 11606）。依据：留出调参空间的同时不至于把明显命中的文档判为未识别；先取保守值，上线后按实际误判率调整（spec 已知缺口已记录「阈值不可按模板配置」）。
  - **字段校验重试次数 2**（含首次共 3 次尝试）。依据：模型偶发格式错误是常态，1 次重试不够、3 次以上边际收益低且放大延迟。
  - **抽取接口超时：同步等待上限 120 秒**，超出即按 11608 返回。
  - **本地抽取文本可用阈值：100 字符**（决策 6 的判据）。依据：证照类文档的字段信息量远超此数；低于该值基本可判定「没抽出实质内容」→ 转识别服务。取保守低值是为了避免把「短但有效」的文书误送去 OCR。

---

## 3. 方案对比与选定

### 决策 1：新建 `doc_extract` 模块，不扩展 `knowledge` 模块

- **备选**：
  - A. **新建 `bisheng/doc_extract/`**，自己的 api/domain 两层，解析环节通过 service 层复用 knowledge 的 loader 栈。
  - B. 扩展 `bisheng/knowledge/`，把模板与抽取塞进知识库模块。
  - C. 只做工作流节点，不建独立模块。
- **选定**：A
- **原因**：
  - `release-contract` 要求每个领域对象只有一个 Owner Feature。模板与抽取记录是全新对象，独立模块才能干净地拥有其写入行为；塞进 knowledge 会让「临时抽一份证照」与「知识库管理」混在同一个 Owner 下，后续场景接入时边界更糊。
  - 架构守卫 RULE-5 只禁 **API 层**跨模块互相导入，**service 层**复用 loader 栈是合规的 —— 这是 A 可行的前提，已核实 `sensitive_word` 等近期模块同为此结构。
  - C 被用户明确否决：交付范围定为「能力底座 + 管理页」，工作流节点推到后续（spec 范围边界）。
- **何时该重新考虑**：若后续场景接入时发现几乎所有调用方都同时需要知识库上下文（如「入库时顺带抽元数据」成为主用法），可评估合并算子。

### 决策 2：把「文档类型枚举」从代码里抽出来变成数据

- **备选**：
  - A. **模板即枚举**：判定候选集由本租户已启用模板的「适用范围」动态拼出。
  - B. 沿用 `file_encoding.py` 的写法，每接一个部署就改一次 `CLASSIFY_PROMPT` 的硬编码枚举。
  - C. 让判定走独立的分类模型或规则引擎。
- **选定**：A
- **原因**：`CLASSIFY_PROMPT`（`knowledge/rag/pipeline/transformer/file_encoding.py:78`）已经把「LLM + 固定枚举 + 正则校验 + 失败兜底」这条路走通了，**本 feature 不是发明新机制，是把它一般化** —— 那段枚举（政策制度/标准规范/… × 业务域）本质上是首钢部署的硬编码配置，换个客户就得改代码。A 把「有哪些文档类型、每类怎么描述」交给管理员，代码里不再有任何具体文档类型的字面量。
- **为什么不用 C**：独立分类模型需要标注数据与训练流程，且对「管理员随时新增一类证照」的响应能力不如 LLM 提示词；规则引擎无法处理「营业执照」与「统一社会信用代码证书」这类语义相近的类型。
- **何时该重新考虑**：若模板数量增长到提示词放不下（经验值：候选超过 30 类时判定准确率会明显下降），需改为两阶段判定（先粗分类再细判）或引入向量检索预筛。

### 决策 3：分类与抽取分两次模型调用，不合并

- **备选**：
  - A. **两次调用**：先分类拿「候选类型 + 置信度」，再按模板字段定义抽取。
  - B. 一次调用：让模型同时输出类型与所有候选模板的字段。
- **选定**：A
- **原因**：两者要的输出形态冲突 —— 分类要「从 N 个候选里选一个 + 一个置信度」，抽取要「按一个确定的 schema 填字段」。塞进一次调用会让输出格式互相打架（尤其在没有 JSON mode 的前提下，§5 坑 1）。此外 A 让分类结果可缓存、可人工覆盖、可作为独立可观测点（spec AC-31 要求留痕「判得准不准」）。
- **代价**：M-1a + M-2b 路径下每次抽取多一次模型调用。接受 —— 该路径本来就要跑解析 + 抽取，多一次判定调用的边际成本有限，而可控性的收益更实在。
- **何时该重新考虑**：若调用量增长到模型成本成为瓶颈，或测得判定准确率足够高可以「一次判定 + 命中率高时免抽取校验」，再评估合并。

### 决策 4：结构化输出自建「提示词约束 + 校验 + 重试」，不依赖 JSON mode

- **备选**：
  - A. **自建**：把字段定义序列化进提示词，要求输出 JSON；`json.loads` → 逐字段类型/必填校验 → 失败重试 ≤2 次。
  - B. 依赖服务商的 `response_format: json_schema`。
  - C. 用 `with_structured_output` 之类的框架能力。
- **选定**：A
- **原因**：B 与 C 在当前代码库都**不存在**（§5 坑 1），且部署环境跑的是信创本地模型，未必支持 JSON mode —— 依赖它等于把可用性押在模型服务商的实现上。A 的核心复杂度在于「校验器要说什么话」：错误信息必须带 `field_key` / 期望类型 / 实际值，这样重试时才能把失败原因回灌给模型（这也是 spec AC-26「指明失败的字段」的落点）。
- **代价**：需要正经实现校验器与重试循环，且弱模型的首次成功率会比原生 JSON mode 低。这是刻意的取舍：**可用性 > 首次成功率**。
- **何时该重新考虑**：若平台统一接入的模型服务普遍支持 `json_schema`，可加一条快速路径，但**不得移除 A 作为兜底** —— 信创环境的模型能力是变化的。

### 决策 5：解析接缝复用 `TempFilePipeline`，不另起一套

- **备选**：
  - A. **复用**：通过 `TempFilePipeline` + `settings.get_knowledge()` 的 provider 配置走既有 loader 栈。
  - B. 直接调 `PaddleOcrLoader` 等具体 loader。
  - C. 自己写一套简化的解析。
- **选定**：A
- **原因**：`TempFilePipeline` 已被 **4 处既有调用点**复用 —— `linsight/domain/services/workbench_impl.py:456`（灵思工作台，文件解析）与 `:1281`（同文件第二处，另一条链路）、`workflow/nodes/input/input.py:309`（工作流输入节点）、`workstation/domain/services/chat_service.py:72`（工作台对话）。**本 feature 将是第 5 个调用方** —— 即它是接进一个已经在服役的接缝，不是发明新接缝。B 会把「选哪个 loader」的逻辑复制一份（该逻辑现在是 `_build_pdf_loader` 单一来源，见 `base_file_pipeline.py:207`），随 provider 配置变化而漂移。C 是重复造轮子。
- **隔离方式**：全部 knowledge 依赖收敛在 `domain/services/parse_adapter.py` 一个文件里。它是本模块唯一接触 loader 栈的内部文件 —— 后续 OCR/VLM 选型变化只动这一处。
- **何时该重新考虑**：若知识库侧的 `TempFilePipeline` 接口发生破坏性变更，或本 feature 需要「不影响知识库配置的独立解析链路」，则评估拆出。

### 决策 6：预处理分流按「文字层可用性」判断，而非按文件扩展名

- **备选**：
  - A. **先探文字层**：PDF/OFD 尝试本地抽取，拿到实质内容就用；空/过少再走识别服务。图片格式直接走识别服务。
  - B. 按扩展名硬分流：`.pdf` 走本地、`.png` 走 OCR。
  - C. 一律走 OCR/识别服务。
- **选定**：A
- **原因**：同一扩展名下情况各异 —— **电子营业执照**是 PDF 且带完整文字层（走本地零成本、且更准），而**扫描件 PDF** 文字层为空（必须走识别）。B 会把前者也送去 OCR，白白产生成本与识别误差。C 更浪费。
- **实现口径**：判据是「本地抽取产出的文本长度是否达到可用阈值」（**100 字符，取值见 §2**），而非「有无文字层」的严格判定 —— 有些扫描件会带一层垃圾 OCR 文本。
- **何时该重新考虑**：若发现某类文档的本地抽取总是产出噪声文本（长度够但内容无意义），需要引入更严的质量判据。

### 决策 7：渲染器独立实现，不复用既有两条模板路径

- **备选**：
  - A. **新写轻量 `{{field_key}}` 渲染器**。
  - B. 复用 `workflow/nodes/prompt_template.py::PromptTemplateParser`。
  - C. 复用 `workflow/nodes/report/docx_replace.py::DocxReplacer`。
- **选定**：A
- **原因**：B 是 `{{#var#}}` 语法且**刻意做变量剥离**（`remove_template_variables`，把已知变量替换回 `{var}` 形式），语义与「用字段值填充」相反；C 产出 docx，而 spec 要求的是可读文本。复用任一条都会引入不必要的耦合与语义错配。A 只需几十行，且能在**保存时**就校验出悬空引用（spec AC-09）—— 复用 B/C 做不到这一点，因为它们的模板是运行期才解析的。
- **何时该重新考虑**：若后续要支持富文本/表格渲染，届时评估是否引入真正的模板引擎。

### 决策 8：模板不进 OpenFGA，用 `get_tenant_admin_user` 收口（C4 的适用边界）

- **备选**：
  - A. **只用 `Depends(UserPayload.get_tenant_admin_user)`**，不写任何 FGA 元组。
  - B. 把模板注册为 OpenFGA 资源类型，创建时调 `PermissionService.authorize()`。
- **选定**：A
- **原因**（**这条是 C4 的边界判定，必须记录证据**）：C4 规定「资源创建必须调用 `PermissionService.authorize()`」，但「资源」在此有明确外延 —— `permission/domain/schemas/permission_schema.py:8` 的 `VALID_RESOURCE_TYPES` 列举了九类：`knowledge_space` / `knowledge_library` / `folder` / `knowledge_file` / `workflow` / `assistant` / `tool` / `channel` / `dashboard`。**模板不在此列。** 更直接的同构前例是 F036/F056 的 `sensitive_word_policy`：同为「租户级配置 + 管理员 CRUD」，**完全不调用 `authorize`**，只靠 `get_tenant_admin_user` 收口（`sensitive_word/api/endpoints/policies.py:19`）。
  
  C4 治理的是「资源级权限」（谁能看/改/用**这一个**知识库、工作流），而模板是**租户级配置** —— 不存在「A 能改模板 X、B 只能看模板 X」的需求，粒度对不上。
- **顺带解决 spec AC-36 的 super_admin 口径**：`get_tenant_admin_user` 的实现（`common/dependencies/user_deps.py:78`）第一分支就是 `_check_is_global_super(user.user_id)`，**已天然涵盖平台超级管理员**，与 spec AC-36 的「租户管理员与平台超级管理员中的任一类」完全吻合，无需额外判断。
- **何时该重新考虑**：若产品要求「模板可对特定角色/用户授权使用」（如某部门只能用某几类模板），则需升级为资源级权限并登记进 `VALID_RESOURCE_TYPES`。

### 决策 9：模板删除用软删，使类型标识可复用且记录保持可读

- **备选**：
  - A. **软删**（`is_deleted` 标记）：标识可被新模板复用，既有抽取记录仍指向旧行。
  - B. 硬删：删掉行，抽取记录里的 `template_id` 变悬空。
  - C. 硬删 + 抽取记录级联删除。
- **选定**：A
- **原因**：spec 同时要求「删除后该类型标识可被重新用于新建模板」（AC-08）与「既有抽取记录保持可读」（AC-31 / 范围边界）。B 会让历史记录的外键悬空（虽然可由 `doc_type` 冗余字段兜底，但「哪个模板」就永久丢失了）；C 直接违反「记录可追溯」的核心诉求。A 用一行标记同时满足两者。
- **与 AC-07 的配合**：类型标识的唯一性判定**只针对未删除的模板**（`UNIQUE(tenant_id, doc_type)` 因此**不能**建在库层 —— 软删行会挡住同名新建）。唯一性在 service 层校验，库层只建普通索引。
- **何时该重新考虑**：若模板数量增长到软删行影响查询，可引入归档表分离。

### 决策 10：模型槽位独立，不蹭 `knowledge_llm`

- **备选**：
  - A. 新增 `ConfigKeyEnum.DOC_EXTRACT_LLM`。
  - B. 复用 `KNOWLEDGE_LLM`（知识库的抽取标题/摘要模型）。
- **选定**：A
- **原因**：语义不同 —— 知识库的槽位服务于「标题/摘要/自动打标」，本 feature 服务于「证照字段抽取」，对模型的能力要求不一样（后者需要更强的指令遵循与格式稳定性）。蹭用会导致「为了改善证照抽取而升级模型，顺带改变了知识库入库的模型」这种隐式耦合。B 的唯一优势是省一次配置，但配置成本远小于耦合成本。
- **实现**：沿用 `LLMService.get_bisheng_llm_sync(model_id=..., app_id=..., app_type=..., user_id=...)`，与 `knowledge_space_auto_tag_service.py:70` 同模式。

### 决策 11：模板变更留痕复用既有审计链路（v2 路径），不自建审计表

> **2026-09-17 实现期修正**：本节初版写的是走 `AuditLogService` 的 `EventType` / `ObjectType` / `SystemId` 枚举（legacy 路径）。**实现时发现平台已迁移到 v2 审计路径**（`AuditLogDao.ainsert_v2` + 各模块自有的 `AuditAction` 枚举），`llm` / `tenant` / `admin` / `approval` 均已改用。下面按实际实现重写。

- **备选**：
  - A. **复用 v2 审计路径**（`AuditLogDao.ainsert_v2` + 本模块 `DocExtractAuditAction`），并把 action 加进审计页白名单。
  - B. 只加 `create_user` / `create_time` / `update_time` 三列。
  - C. 自建一张 `doc_extract_template_audit` 表。
  - D. 走 v2 写库但不加白名单（仅留存，UI 不可见）。
- **选定**：A
- **原因**：**spec AC-38 / INV-15 要的是「谁在什么时候改了哪个模板」这类**事件流水**，不是「当前行最后被谁改过」。B 只能回答最后一个修改者，改了三次就只剩最后一次 —— 不满足 AC-38，只能算便捷展示位（表结构里保留了这三列，但**不得**用它们替代留痕）。C 会造出一张与 `audit_log` 职责重叠的表，且绕过既有审计查阅入口。D 满足「留存」但不满足 AC-38 里「**可事后查阅**」的字面要求 —— 运维得调接口才能看，不算「可被看到」。
- **v2 路径的关键事实**：`ainsert_v2` **不写 `system_id`**。审计页的可见性由 `AuditLogDao._ui_visible_predicate()`（`audit_log.py:222`）决定，判据是 `system_id IS NOT NULL OR action IN (_UI_VISIBLE_V2_ACTIONS)`。**v2 行必然 `system_id` 为空，所以能不能被看到，只取决于 action 是否在白名单里。** 这就是 A 与 D 的分界。
- **实现落点（三处必须同步，缺一处则该行写了但查不到，且不报错）**：
  1. `database/models/audit_log.py` 的 **`_UI_VISIBLE_V2_ACTIONS`** 追加 5 个 action（本模块已加）；
  2. 同文件 **`_V2_NAMESPACE_TO_ACTION_PREFIX`** 增加 `'doc_extract': 'doc_extract.'`（审计页模块筛选用，本模块已加）；
  3. 前端 `platform/src/controllers/API/log.ts` 的镜像清单 + 三语 `log.eventTypeEnum.*` 词条（该文件头注释明确要求「Keep in sync with backend `_UI_VISIBLE_V2_ACTIONS`」）。
- **action 命名用枚举而非裸字符串**：`bisheng/doc_extract/domain/constants.py::DocExtractAuditAction`。理由与 `tenant/domain/constants.py` 相同 —— **写错一个字母的行照样写进库，只是永远匹配不上白名单、在页面上静默消失**。枚举 + `DOC_EXTRACT_AUDIT_ACTIONS` 元组让「白名单是否覆盖全部 action」可被测试断言。
- **写入时机**：`TemplateService` 在每个写操作**成功之后**调用（失败不留痕）。写入是 **best-effort**：审计写失败只记日志，不因此让管理员的编辑失败 —— 丢一行日志好过丢一次编辑。
- **与本 feature 记录检索的区别**（勿混淆）：AC-32 的「抽取记录检索」查的是 `doc_extract_run`（决策 12），与本节查 `audit_log` 是两回事 —— 前者是抽取结果，后者是模板变更事件。
- **何时该重新考虑**：若审计模块未来被整体替换或要求各模块自带审计表，再迁移；若 v2 路径改变可见性判据（例如改为按 `target_type` 过滤），白名单机制需同步复核。

### 决策 12：抽取记录的检索复用「列表 + 筛选」形态，不建独立页面

- **备选**：
  - A. **管理页内新增「抽取记录」tab**，按模板 / 时间范围 / 状态筛选 + 游标分页。
  - B. 独立页面 + 独立路由。
  - C. 不提供界面，只提供 API。
- **选定**：A
- **原因**：spec AC-32 要求「可检索与查阅」，而 spec 用户故事 3 的诉求是「业务反馈抽错了时能定位」—— 这是个**排障动作**，不是日常浏览，放管理页里与模板配置相邻最省事。C 会让 AC-32 的「可查阅」落空（只有 API 不算「可被运维人员看到」）。B 为一个排障功能占一个顶级入口，过重。
- **权限**：AC-32 限定「租户管理员与平台超级管理员」，即与模板管理同一门槛 —— 复用 `get_tenant_admin_user`（决策 8），无需新权限档位。
- **分页**：沿用平台既有的游标分页链路（`docs/architecture/13-cursor-pagination.md`），不引入 offset 分页。
- **何时该重新考虑**：若记录量增长到影响管理页加载，或业务需要非管理员自助查询自己的抽取，再拆独立页并放宽权限。

### 决策 13：`dry_run` 的管理员门槛在服务层补做，不拆端点

- **背景**：spec 里两种模式的主体不同 —— AC-37 规定 **M-1a 对任意登录用户开放**，而 AC-36 / AC-41 规定 **M-1b 由租户管理员经管理界面发起**。二者却由同一个抽取端点承载（靠 `dry_run` 区分）。
- **备选**：
  - A. **单端点 + 服务层补校验**：端点的 Depends 用 `get_login_user`（放行所有登录用户），进服务层后若 `dry_run=True` 再校验管理员身份，否则 11611。
  - B. 拆成两个端点：`/extract`（登录用户）与 `/trial_extract`（管理员依赖）。
  - C. 端点的 Depends 直接用 `get_tenant_admin_user`，两种模式都要求管理员。
- **选定**：A
- **原因**：
  - **C 被 AC-37 否决** —— 它会让普通登录用户无法发起业务抽取，而后续场景接入（工作流节点、对话附件）的调用方恰恰不是管理员。
  - **B 与 spec 已确认的接口形态不符**：spec 在 §2 的 M-1 表里把试抽取定义为「以验证为目的的调用」（与业务抽取并列的调用目的，不是独立的动作），是同一动作的不同目的，不是两个动作。拆端点会让「试抽取」变成一个平行的 API 面，后续每个场景都要想「我用哪个」。
  - **A 的代价**：Depends 链看不到请求体，因此无法在依赖注入层按 `dry_run` 分流，只能进服务层判断。这是框架限制，不是设计偏好。
- **为什么必须校验**：试抽取会引用 `template_id`（AC-43 要求模板存在），若不限制主体，普通用户可借 `dry_run=true` 探测/使用自己本无权访问的模板 —— 这是权限绕过。**这一条不可省。**
- **实现口径**：服务层复用与 `get_tenant_admin_user` 相同的判定 —— `_check_is_global_super(user_id)` 或 `user.has_tenant_admin(get_current_tenant_id())`（坑 10）。**抽成一个共享 helper**，避免两处口径漂移。
- **何时该重新考虑**：若抽取端点将来独立对外开放（`/api/v2` RPC），应拆分成两个端点以在网关层表达不同权限。

### 决策 14：v1 同步返回，不引入异步任务（但接口形态不阻止后续异步化）

- **备选**：
  - A. **同步**：请求内完成解析 + 判定 + 抽取 + 渲染。
  - B. 异步：入 Celery 队列，返回任务 id 轮询。
- **选定**：A
- **原因**：单份证照 + 可用模型通常秒级返回；B 会让调用方（后续的工作流节点、对话附件）都要处理轮询与超时，复杂度前置。spec 明确把异步列为**本次排除**。
- **风险与兜底**：一旦上扫描件 + 本地推理模型，分钟级是常态，届时同步会撞超时。因此：
  - 同步等待上限设为 **120 秒**（§2），超时按 11608 返回，**不让请求无限挂起**。
  - 接口**返回体形态**（见 §4.2：`ok` / `fields` / `markdown` / `template` / `confidence` / `route` / `missing_fields`）设计为「异步化后可作为任务结果原样返回」，使后续引入 `status` + `task_id` 时不破坏既有字段。
- **何时该重新考虑**：出现「单次抽取耗时中位数 > 30 秒」或「批量抽取」需求时，改异步。

---

## 4. 系统现状（接手必读）

> **本 feature 尚未实现**，本节描述的是设计意图下的目标结构 + 复用的既有链路现状。实现后须按实际代码校正。

### 4.1 数据流

**模板配置（写）**：管理页 → `POST/PUT/DELETE /api/v1/doc_extract/templates` → `TemplateService` → 保存前校验（名称非空、适用范围非空、字段定义合法、呈现形式无悬空引用、类型标识唯一）→ 写 `doc_extract_template`。

**抽取（M-1a 业务抽取）**：

```
请求（文件 + 可选 doc_type）
  → spec §3.1 前置校验表逐条判定，命中即返回 113xx 并停止
  → parse_adapter：探测文字层 → route ∈ {text, ocr}
  → [M-2b] classifier_service：候选集 = 本租户已启用模板 → LLM 判定 → {doc_type, confidence}
  → extraction_service：字段定义序列化进提示词 → LLM → json.loads → 校验 → 重试 ≤2
  → renderer：render_template + fields → markdown
  → 落 doc_extract_run（`preprocess_route` 列记录走的是 text 还是 ocr；该列在对外返回体中名为 `route`）
  → 返回 {fields, markdown, template, confidence, route}
```

**抽取（M-1b 试抽取）**：与上同，但跳过 spec §3.1 序号 5–8，恒按 M-2a 使用编辑器中的定义，**不落记录**。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `doc_extract_template.tenant_id` | INTEGER NOT NULL，索引 | C3 自动注入过滤 | — |
| `doc_extract_template.name` | VARCHAR(128) NOT NULL | 模板名 | 管理页、判定提示词 |
| `doc_extract_template.doc_type` | VARCHAR(64) NOT NULL，索引（**非唯一**） | 类型标识，如 `business_license`；唯一性由 service 层对未删除行校验（决策 9） | 调用方显式指定、判定结果 |
| `doc_extract_template.description` | TEXT NOT NULL | **适用范围** —— 判定时喂给模型的该类描述 | 分类提示词拼装 |
| `doc_extract_template.field_schema` | `JsonType` | 字段定义数组（见下） | 抽取提示词、校验器、渲染器、管理页 |
| `doc_extract_template.render_template` | `LargeText` | 呈现形式，可为空（AC-11） | 渲染器、管理页 |
| `doc_extract_template.enabled` | SMALLINT NOT NULL | 启用位 | 判定候选集 |
| `doc_extract_template.is_deleted` | SMALLINT NOT NULL DEFAULT 0 | 软删标记（决策 9） | 唯一性校验、列表 |
| `doc_extract_template.create_user` / `create_time` / `update_time` | INTEGER / DATETIME | 「最后修改人 + 时间」。**这只是便捷展示位，不满足 AC-38 的完整留痕** —— 它只知道最后一次，见决策 11 | 管理页列表 |
| `doc_extract_run.tenant_id` | INTEGER NOT NULL，索引 | C3 | — |
| `doc_extract_run.template_id` | INTEGER NOT NULL | 指向模板（可能是软删行） | 追溯、记录检索 |
| `doc_extract_run.doc_type` | VARCHAR(64) NOT NULL | **冗余**，模板改名/删除后仍可追溯 | 记录检索 |
| `doc_extract_run.determined_by` | VARCHAR(16) NOT NULL | **判定方式**：`explicit`（M-2a，未经过判定）/ `auto`（M-2b）。**必须有这一列**：spec AC-33 明确要求「如实标记『未经过判定』**而非留空或填默认值**」—— 用 `confidence = NULL` 兼作标记就是「留空」，AC-33 禁止；独立列才能让该标记是个**显式值** | 追溯、记录检索 |
| `doc_extract_run.confidence` | FLOAT NULL | 判定置信度；`determined_by='explicit'` 时为 NULL | 追溯 |
| `doc_extract_run.preprocess_route` | VARCHAR(16) | `text` / `ocr`，排障用 | 追溯（回答「解析没出文字」） |
| `doc_extract_run.fields` | `JsonType` | 抽出的结构化字段 | 记录检索 |
| `doc_extract_run.markdown` | `LargeText` | 渲染结果 | 记录检索 |
| `doc_extract_run.status` | SMALLINT | 成功 / 失败 | 记录检索 |
| `doc_extract_run.error_code` / `error_message` | INTEGER / VARCHAR | 失败原因 | 追溯 |
| `doc_extract_run.user_id` / `create_time` | INTEGER / DATETIME | 审计 | — |

`field_schema` 结构（v1 仅标量类型）：

```json
[
  {"key": "company_name", "label": "企业名称",         "type": "string", "required": true},
  {"key": "credit_code",  "label": "统一社会信用代码", "type": "string", "required": true},
  {"key": "valid_to",     "label": "营业期限至",       "type": "date",   "required": false}
]
```

`type` 取值：`string` / `number` / `date` / `boolean`。

**抽取请求体**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | multipart | 待抽取文档 |
| `doc_type` | string，可选 | M-2a 的类型标识；**缺省即 M-2b 自动判定**。试抽取时忽略此字段 |
| `template_id` | int，可选 | **M-1b 试抽取必填**：指明试的是哪个模板（AC-41 / AC-43 都以此定位模板）。M-1a 下忽略 |
| `dry_run` | bool，默认 false | `true` = M-1b 试抽取 |
| `trial_definition` | JSON，可选 | **仅 M-1b 使用**：编辑器里**尚未保存**的 `{description, field_schema, render_template}`。M-1b 必须携带此字段——否则服务端拿不到「编辑器中的定义」，AC-41 无从执行 |

> **为什么试抽取要回传定义**：AC-41 要求试抽取作用于「编辑器中当前正在编辑的字段定义与呈现形式」而非已保存的版本。服务端无从知晓客户端的未保存状态，因此必须由请求携带。这也解释了 AC-42：服务端要对这份**未保存的定义**做 AC-09/AC-10 的合法性校验（试抽取不落库、不写回，所以它是一次「用这份定义跑一遍」的纯计算）。
>
> 对照：模板的**新增/编辑保存**走 `templates` 端点（`trial_definition` 不参与，走正常保存校验路径）。

**对外返回体**（成功；后续异步化时保持不变，决策 14）：

```json
{
  "ok": true,
  "fields":    {"company_name": "广西某某有限公司", "credit_code": "91450100MA5K..."},
  "markdown":  "## 广西某某有限公司\n统一社会信用代码：91450100MA5K...\n",
  "template":  {"id": 12, "doc_type": "business_license", "name": "营业执照",
                "field_schema": [ ... ]},
  "confidence": 0.94,
  "route":     "ocr",
  "missing_fields": ["valid_to"]
}
```

| 返回字段 | 说明 | 对应 AC |
|---|---|---|
| `ok` | 本次是否产出结果。**试抽取的失败也走 `ok: false` 的 200 响应**，不走 HTTP 错误码 —— 因为前端 `captureAndAlertRequestErrorHoc` 对错误响应会弹 toast（坑 15） | — |
| `template.field_schema` | 回传本次所用字段定义，使前端能渲染「字段名 ↔ 值」对照，而非只有 key | AC-45 |
| `missing_fields` | 未抽到的字段 key（按 §4.3 `field_validator` 的口径：只可能是非必填字段） | AC-45 |

**试抽取失败的返回体**（HTTP 200，`ok: false`）：

```json
{
  "ok": false,
  "error_code": 11605,
  "error_message": "文档无可用文字层，且当前环境未配置识别服务",
  "reason": "no_parser"
}
```

`reason` 枚举覆盖 **AC-44 的 7 项**与 **AC-42 的「编辑器定义不合法」**：

| `reason` | 错误码 | 来源 AC | 触发条件 |
|---|---|---|---|
| `doc_unreadable` | 11609 | AC-44 | 文档不可读或为空 |
| `over_size` | 11610 | AC-44 | 超出 20 MB |
| `model_unconfigured` | 11608 | AC-44 | 租户未配 `doc_extract_llm` |
| `no_parser` | 11605 | AC-44 | 无文字层且未配识别服务 |
| `ocr_failed` | 11608 | AC-44 | 识别服务调用失败或超时 |
| `model_failed` | 11608 | AC-44 | 抽取阶段模型调用失败或超时 |
| `field_invalid` | 11607 | AC-44 | 字段校验重试耗尽 |
| `definition_invalid` | 11603 / 11604 | **AC-42** | 编辑器中的定义未通过合法性校验（字段定义非法 → 11603；呈现形式悬空引用 → 11604） |
| `template_missing` | 11611 | **AC-43** | 试抽取时该模板已被其他管理员删除 |
| `forbidden` | 11611 | **AC-37 / AC-36** | 非管理员发起 `dry_run=true`（决策 13） |

> **后三项为什么也走 `ok: false` 而非 HTTP 错误码**：它们同样是「管理员在调参过程中预期会撞上」的情形，弹 toast 反而打断节奏。**但 `forbidden` 是唯一的例外见下方说明。**

> **业务抽取（M-1a）沿用 HTTP 错误码**，不走 `ok: false` —— 调用方是程序，需要靠状态码分支。只有试抽取（面向人、且失败是预期内的调参过程）用 200 + 标记。

> **`forbidden` 的处理（决策 13 的落点）**：这是一个**越权尝试**，不是调参失败。虽然它也只可能发生在 `dry_run=true` 的请求上，但**必须以 HTTP 错误码返回**（非 200），不要混进 `ok: false` 的 200 响应 —— 否则越权探测会被静默吞掉，日志与监控也看不到。这与「试抽取失败走 200」的口径不冲突：那条只适用于**已通过鉴权之后**的执行期失败。

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `doc_extract/api/endpoints/templates.py` | 模板 CRUD 端点；`Depends(get_tenant_admin_user)` 鉴权（决策 8） | 无业务逻辑，不自校验 |
| —（端点鉴权口径） | `templates.py` 与 `records.py`：**恒为** `get_tenant_admin_user`。`extraction.py`：Depends 层为登录用户，`dry_run=true` 的管理员校验在**服务层内**补做（决策 13） | 见决策 13 |
| `doc_extract/api/endpoints/extraction.py` | 判定 / 抽取端点 | 同上 |
| `doc_extract/api/endpoints/records.py` | 抽取记录检索端点（AC-32，决策 12）：按模板 / 时间范围 / 状态筛选 + 游标分页；`Depends(get_tenant_admin_user)` | 不返回跨租户数据（C3 自动过滤） |
| `doc_extract/domain/services/template_service.py` | 模板 CRUD 编排；保存前全部校验（名称、适用范围、字段定义、呈现形式悬空引用、类型标识唯一）；写成功后调 `AuditLogService` 留痕（决策 11） | **不做**权限判定（由端点依赖提供）；不直接写 ORM |
| `doc_extract/domain/services/record_query_service.py` | 抽取记录检索编排 | 不做写入（写入在 extraction_service） |
| `doc_extract/domain/services/classifier_service.py` | 拼装判定提示词、调模型、解析结果、置信度比较 | 不选模板（只报类型与置信度） |
| `doc_extract/domain/services/extraction_service.py` | 抽取主流程编排：调前置校验 → parse_adapter → 判定 → 抽取 → 渲染 → 落记录 | 不实现校验器与渲染器的内部逻辑 |
| `doc_extract/domain/services/parse_adapter.py` | **本模块唯一接触 knowledge loader 栈的文件**：文件落临时目录 → `TempFilePipeline` → 拿文本；按需降级到识别服务；探测文字层定 route | 不做抽取、不碰模板 |
| `doc_extract/domain/services/field_validator.py` | 字段级校验（类型、必填），产出带 `field_key` / 期望 / 实际的错误 | 不调模型 |
| `doc_extract/domain/services/renderer.py` | `{{field_key}}` 渲染；未抽到字段按无内容处理 | 不做校验（悬空引用在保存时已拦） |
| `doc_extract/domain/services/prompt_builder.py` | 字段定义 → 提示词的序列化；判定候选集 → 提示词的序列化 | 不调模型 |
| `doc_extract/domain/models/extract_template.py` / `extract_run.py` | ORM 模型 + DAO | 不写业务逻辑 |
| 前端 `pages/BuildPage/bench/DocExtractTemplates.tsx` | 模板管理 tab：列表 + 新建/编辑 | 直连 HTTP（须走 `controllers/API/`） |
| 前端 `pages/BuildPage/bench/DocExtractTemplateEdit.tsx` | 编辑表单 | 同上 |
| 前端 `pages/BuildPage/bench/DocExtractRecords.tsx` | 抽取记录 tab（AC-32 / 决策 12）：筛选 + 表格 + 详情 | 同上 |
| 前端 `pages/BuildPage/bench/DocExtractComponents/` | `FieldSchemaEditor` / `RenderTemplateEditor`（编辑呈现形式时提示当前可用字段变量，AC-40）/ `TrialExtractDialog` | 同上；拆分原因：单文件 ≤600 行（根 `AGENTS.md §4`） |
| 前端 `pages/BuildPage/bench/DialogueWork.tsx` | **tab 宿主**：新增「文档抽取」tab（参照 F056 的 `guardrail` tab 写法） | **AC-36 的入口可见性控制点** —— 该文件的 `TabsList` 目前是**无条件渲染**的，非管理员只要看到 tab 入口就违反 AC-36。收口方式二选一：走角色菜单 `web_menu`（`layout/MainLayout.tsx:130` 的既有机制），或在 tab 渲染处按用户角色条件渲染。**不得默认无条件放出** |

---

## 5. 已知坑 / 反直觉事实

> 本 feature 设计阶段的前置走查结论。实现过程中新发现的坑须追加到本表。

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **对话 LLM 层没有结构化输出支持**：`core/ai/llm/`（chat_openai_compatible / chat_qwen / chat_minimax …）下**无** `response_format` / `json_schema` / `with_structured_output`。注意 `response_format` 在仓库里**有**出现（6 个文件），但都不在对话 LLM 调用路径上：`core/ai/tts/` 两个客户端（指音频格式）、`core/ai/rerank/xinference/client/restful/`（该目录下 vendored 的**文生图/ASR 句柄**，指返回图片 URL 还是 base64）。**最危险的一处是 `core/ai/rerank/xinference/_compat.py`：它直接定义了 `ResponseFormatJSONObject` / `ResponseFormatJSONSchema` 两个类** —— grep 到它们极易误判「平台已支持 JSON Schema 结构化输出」，实际那是 vendored SDK 的类型定义，与对话链路无关。`LLMModelType` 只有 `llm/embedding/rerank/asr/tts`，无 vision 类型 | 以为「调个 JSON mode 就行」→ 在信创本地模型上做不到，抽取结果全靠字符串解析。反向误判同样有害：grep 到 `response_format` 就以为链路已具备 | 决策 4 的自建链路；`extraction_service` + `field_validator` |
| 2 | **多模态消息构造是有的，共两处**：`workstation/domain/services/chat_service.py:1559-1562`（图片拼成 `{"type": "image_url", ...}` 后送 `HumanMessage`，:1581）与 `core/ai/llm/chat_openai_reasoning.py:37-46`（`_convert_standard_image_block()` 把 data-url / 远程 URL 转成同一形态） | 与我最初「无多模态构造代码」的判断相反 —— **VLM 直连路径比预想的更可行**：若要新增 vision 分支，这是可参照的现成写法，不必从零构造消息体 | §9 第二阶段的备选路径；`parse_adapter` 的 vision 分支若要做，参照 `chat_openai_reasoning.py:37` 的转换函数 |
| 3 | **本地 loader 模式下图片直接抛错**：`_init_image_loader()`（`base_file_pipeline.py:257`）在 loader 是 `LocalPdfLoader` 时于 `:260` 抛 `KnowledgeFileNotSupportedError` | 以为「统一输入」开箱即用 → 实际图片链路**完全取决于环境是否配了 paddle_ocr / mineru / etl4lm**；未配置时图片请求全部失败 | `parse_adapter` 的前置检查 → 11605；实施顺序见 §9 |
| 4 | **`CLASSIFY_PROMPT` 是硬编码枚举的现成先例**（`file_encoding.py:78`），含「LLM + 固定枚举 + 正则校验 + 失败兜底」完整模式 | 重新发明分类链路，或反过来直接复用它的硬编码枚举（那是首钢部署特有的，换个客户就错） | 决策 2 将其一般化；`classifier_service` 参照其结构但枚举来自数据 |
| 5 | **`TempFilePipeline` 有 4 处既有调用点**：`workbench_impl.py:456` 与 `:1281`（同文件两处，易只搜到一处）、`workflow/nodes/input/input.py:309`、`workstation/domain/services/chat_service.py:72`。本 feature 接入后为第 5 处 | 误以为它是「给知识库用的」或只服务于 1–2 个场景 → 改动时低估影响面；尤其 `workbench_impl.py:1281` 那处容易被漏（同文件搜到 456 就以为找全了） | `parse_adapter` 只**调用**不修改；若确需改动须回归全部 4 处既有调用点 |
| 6 | **`_build_pdf_loader` 是 loader 选择的单一来源**（`base_file_pipeline.py:207`），etl4lm → mineru → paddle_ocr → local 四级降级写在一处 | 在别处另写一套「选哪个 loader」的逻辑 → 与 provider 配置漂移 | `parse_adapter` 通过 `TempFilePipeline` 间接使用，不复制该逻辑 |
| 7 | **`FileExtensionMap` 决定 loader 与 transformer**（`base_file_pipeline.py:26`）；`.png/.jpg/.jpeg/.bmp` 映射到 `_init_image_loader` | 以为图片走的是「OCR loader」→ 实际它走的是 PDF loader 分支，只是传入的扩展名不同 | 同上；`route` 字段记录实际走的哪条 |
| 8 | **`PromptTemplateParser` 的语法是 `{{#x#}}` 且会剥离变量**（`remove_template_variables` 把 `{{#x#}}` 还原成 `{x}`） | 误复用它做渲染 → 本 feature 的 `{{company_name}}` 语法根本匹配不上、原样留在输出里；即便改成 `{{#x#}}`，剥离逻辑也会把已知变量还原成 `{x}` 字面量（渲染**静默出错**，不报错） | 决策 7 独立实现渲染器 |
| 9 | **C4 的「资源」有明确外延**：`VALID_RESOURCE_TYPES` 只列九类，模板不在其中；`sensitive_word_policy` 是「租户级配置不走 FGA」的同构前例 | 按「C4 说资源创建必须 authorize」字面执行 → 给模板写无意义的 FGA 元组，或反过来在该走 FGA 的地方跳过 | 决策 8 已记录证据；spec 评审时据此判定不违反 C4 |
| 10 | **`get_tenant_admin_user` 已涵盖 super_admin**（第一分支 `_check_is_global_super`） | 额外加一层 admin 判断 → 冗余且可能与全局口径不一致 | 决策 8；端点直接用该依赖 |
| 11 | **`UNIQUE(tenant_id, doc_type)` 不能建在库层**：软删行会挡住同名新建，与 spec AC-08 冲突 | 加了库级唯一约束 → 删除模板后无法用同标识新建（AC-08 失败），且 SQLite/DM8 报错信息不直观 | 决策 9；唯一性在 service 层对未删除行校验 |
| 12 | **`knowledge` 表是「大而热」的多租户表**，本 feature 对它只读 | 图省事在其上加列/加约束 → 触碰生产热点表（F057 决策 3 已踩过同类判断） | spec AC-30 明令只读；`parse_adapter` 不写知识库 |
| 13 | **多租户过滤靠白名单，不靠「表里有 `tenant_id` 列」**：`_TENANT_AWARE_MODEL_MODULES`（`core/database/tenant_filter.py:39`）必须在 `register_tenant_filter_events()` 首次调用前导入到位，而该函数有 `_initialized` 一次性保护。**不在白名单的模型静默绕过过滤**（v2.5 曾因此把子租户数据写进 root） | 新建两张表、加了 `tenant_id` 列、C3 看起来「已满足」→ 实际过滤从未生效，跨租户数据泄漏，**且不报任何错**。测试若只查「有没有 tenant_id 列」也发现不了 | 两个模型模块路径必须加入 `_TENANT_AWARE_MODEL_MODULES`；写一条跨租户隔离的用例钉住（参照 `test_f057_department_candidate_union.py` 的思路） |
| 14 | **平台请求拦截器已解包 `response.data.data`**（`controllers/request.ts` 对 `status_code===200` 直接返回内层） | 前端再写 `res?.data` 恒为 `undefined` —— F057 的计数徽标曾因此整条链路静默失效 | 前端 API 层直接使用解包值；参照 F057 坑 3 |
| 15 | **`captureAndAlertRequestErrorHoc` 对 `silent` 请求仍会弹 toast**（`silent` 只让拦截器跳过全局处理，reject 的是信封对象） | 想靠 `silent` 压掉预期内的失败提示（如试抽取的字段校验失败）→ 依然弹窗 | 试抽取的失败展示须走**成功响应 + 结果内标记**，而非依赖 HTTP 错误码 + silent；参照 F057 坑 19 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `POST /api/v1/doc_extract/extract` | HTTP API | 后续：工作流节点、对话附件、知识库元数据抽取 |
| `POST /api/v1/doc_extract/classify` | HTTP API | 同上（只要类型不要字段的场景） |
| `GET/POST/PUT/DELETE /api/v1/doc_extract/templates[/{id}]` | HTTP API | platform 管理页 |
| `GET /api/v1/doc_extract/records` | HTTP API | platform 管理页的记录 tab（决策 12） |
| `tenant_system_model_config` 表的 `doc_extract_llm` 槽位 | DB 行（**不是 `config` 表** —— F022 已把系统级模型槽位从 `config` 迁到租户表，读路径见 `llm/domain/services/llm.py:287` 的 `TenantSystemModelConfigDao.aresolve`） | 本 feature；租户模型配置页 |
| `ExtractionService.extract()`（`doc_extract/domain/services/extraction_service.py`） | 内部 Python API | 后续场景接入直接调用（不经 HTTP）。**注意是 domain service 而非另立一个 `DocExtractService` 门面** —— 场景接入时直接依赖它即可 |
| **错误码 11601–11611 的三语文案** | `packages/locales/src/api_errors/{zh-Hans,en,ja}.json` | 前端错误提示；**CI `pnpm check-i18n` 会校验后端错误码覆盖率，漏加会导致 CI 失败** |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `TempFilePipeline` + `settings.get_knowledge()` provider 配置 | 内部 API + 配置 | 知识库侧若改其构造参数或产出形态，本 feature 静默变化（坑 5） |
| `AuditLogService` 与 `EventType` / `ObjectType` 枚举 | 内部 API + DB | 决策 11 的留痕挂在其上；若审计模块重构或枚举被清理，留痕静默失效（不报错，只是查不到） |
| `LLMService.get_bisheng_llm_sync` | 内部 API | 签名或返回类型变更 |
| `TenantSystemModelConfig` 槽位机制 | 内部 API + DB | 新增 `DOC_EXTRACT_LLM` 需确认无键名冲突 |
| `UserPayload.get_tenant_admin_user` | 内部 API | 其 super_admin 涵盖行为是 AC-36 的实现依据（坑 10），若变更需同步 |
| **环境需已配置识别服务** | 部署前置条件 | 未配置时图片链路不可用（坑 3）；这是**本 feature 唯一的环境硬依赖**，须写入部署文档 |
| 平台请求拦截器的解包行为 | 隐式前端契约 | 拦截器改层级则前端取值需同步（坑 14） |

---

## 7. 测试与可观测

- **测试分层**：
  - **模块单测**（`test/doc_extract/`）：模板 CRUD 与全部保存期校验、字段校验器（各类型 + 必填 + 额外字段）、渲染器（含未抽到字段）、判定结果解析与兜底、前置校验表的 11 条优先级、**决策 13 的 `dry_run` 管理员门槛**（普通用户带 `dry_run=true` 必须被拒）、LLM 全部 mock（**不打真实模型**）。
  - **表 DDL 常量**：`test/fixtures/table_definitions.py` 须新增两张表的 SQLite DDL（现有约定是手写 DDL 而非 `create_all`），这是单测能跑的前提。
  - **端到端**（`test/e2e/test_e2e_f058_doc_extract.py`）：SDD 强制项。覆盖模板 CRUD → 试抽取 → 业务抽取 → 记录检索的完整链路。
- **重点边界**（spec §4）：LLM 返回非 JSON、返回 JSON 但缺必填、返回额外字段、模板引用不存在的字段 key、置信度低于阈值、无可用解析器、渲染模板含 `{{}}` 但字段值为 null、显式指定 vs 自动判定两条路径。
- **可观测**：抽取记录表本身就是主要观测面（`preprocess_route` 回答「解析没出文字」、`confidence` 回答「判得准不准」、`fields` 回答「抽出了什么」）。日志需在**每条 spec §3.1 前置校验被拒时**打点（因为被拒不落库，见 spec 已知缺口）。模板变更的留痕在 `audit_log`（决策 11），按 `system_id` + `event_type` 过滤查阅（该 DAO 不支持 `object_type` 过滤）。
- **交付物清单**（易漏项）：后端模块、两张表的 Alembic 迁移、`test/fixtures/table_definitions.py` 的两张 DDL、**`packages/locales` 的三语错误码文案**（决策见 §6.1）、前端管理页 + i18n namespace。
- **手动验证路径**（具体到可执行）：
  1. platform 起在 `:3001`，进 `对话工作台 → 文档抽取` tab，建模板 `doc_type=business_license`，字段含 `company_name`（必填）与 `valid_to`（非必填）。
  2. 点「试抽取」传样件 → 看 JSON 与呈现结果、`route`、`missing_fields`。
  3. 业务抽取：`curl -X POST http://localhost:7860/api/v1/doc_extract/extract -H "Authorization: Bearer <token>" -F "file=@license.pdf" -F "doc_type=business_license"` → 期望返回 `ok:true` 与 `route:"text"`。
  4. 同一份拍照件重跑 → 期望 `route:"ocr"`；若环境未配识别服务则得 11605（这本身就是 AC-18 的正确行为，不是故障）。
  5. 查记录：`GET /api/v1/doc_extract/records?template_id=<id>`；查留痕：审计页按 `doc_extract_template` 模块筛选。
- **信创环境必测**：前端管理页在 信保安全浏览器（Chromium 108）下的滚动与交互流畅度（根 AGENTS.md §4 的性能教训）。

---

## 8. 后续改进 / 不打算做的事

- **场景接入**（另起 feature）：工作流「文档抽取」节点、对话/助手附件接入、知识库入库元数据抽取。本 feature 的 `ExtractionService.extract()` 已按「可被内部直接调用」设计。
- **模板导入 / 导出**：跨环境搬运模板目前只能人工重配。
- **模板版本历史**：当前只能通过 `audit_log` 查「谁改的、何时改的」（AC-38 / 决策 11），**查不到「改前是什么」** —— `audit_log` 记的是事件，不带变更前后快照。若成为痛点，评估在 `note` 字段里存 diff 或给模板加版本表。
- **试抽取结果不可回看**：spec AC-34 规定不落记录（代价见 spec 已知缺口）。若痛点成立，评估单列一张试抽取记录表（与 `doc_extract_run` 分离，避免污染）。
- **字段级置信度**：当前只有文档级。证照类场景可能需要「这个字段不太确定」的粒度。
- **嵌套字段类型**：发票明细行等。扩展 `type` 枚举即可，不动表结构。
- **按模板配置判定阈值**：当前为全局 0.6。
- **异步抽取**：触发条件见决策 14。
- **模板级模型覆盖**：当前为租户级槽位。

---

## 9. 实施顺序（关键）

**先解耦 OCR 依赖，后接图片链路。** 具体：

1. **第一阶段**：模板 CRUD + 前置校验表 + 字段校验器 + 渲染器 + 判定 + 抽取，全部走 **`text` 路径（不依赖任何外部识别服务）**，单测可完全离线跑通。
2. **第二阶段**：`parse_adapter` 的 `ocr` 分支（依赖环境配置），以及 e2e。
3. **第三阶段**：前端管理页（模板管理 tab + 记录 tab + 试抽取）。
4. **第四阶段**：`packages/locales` 三语错误码文案 + Alembic 迁移 + `table_definitions.py` DDL（这些可与第三阶段并行，但**不可漏** —— 迁移与 i18n 覆盖率都是 CI/启动会卡住的项）。

这样安排的理由：图片链路能否跑通**完全取决于环境是否配了远程解析服务**，而这一点在需求澄清时是「待确认」状态（spec 关联 PRD 段落）。若把图片链路排在最前，一旦环境不具备，整个 feature 阻塞；按此顺序，**即使识别服务最终不可用，第一 + 第三阶段交付的底座仍然可用**。

**第二阶段的备选路径**（若环境既不配 OCR 也无可用的多模态模型）：此时图片链路确实不可交付，按 11605 明确报错即可，**不要**为了「让图片能跑」临时引入新的外部服务 —— 那会违反「不新增服务配置项」的非目标（§1）。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-17 | 初版 | 设计阶段产出（实现尚未开始） |
| 2026-09-17 | 补决策 11（审计复用）/ 12（记录检索 UI）/ 13（`dry_run` 鉴权）；修正坑 1 的「无结构化输出」范围（误含多模态，实为 `chat_service.py:1559` 有）与坑 5 的调用点数（实为 4 处既有，非 3 处）；补试抽取请求契约与失败返回体 | design 评审发现 spec AC-32/AC-38/AC-45 无实现落点，且引用核对查出两处事实失准 |
