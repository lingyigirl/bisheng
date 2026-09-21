# Design: 术语库（060-term-library）

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
**版本**: v2.7.0（**2026-09-21 由 v2.8.0 移入**）
**最后更新**: 2026-09-21（设计阶段；实现尚未开始）

---

## 1. 目标与非目标

- **目标**：让模型听懂**本租户的行话**。管理员维护一份租户级术语表（标准词 + 别名 + 释义），对话时按**本轮用户提问**匹配，把命中的术语解释追加进系统提示词。
- **非目标**（spec §「本次明确排除」的 How 侧复述，取舍理由见决策）：
  - **不是知识库**：不切块、不向量化、不进 Milvus/ES、不进 `KnowledgeTypeEnum`
  - **不是受控资源**：不进 FGA、不写授权元组（决策 2）
  - **不是自动抽取**：那是 F059 `answer_suggestion_service` 在做的事；本特性只做人工维护 + 人工导入

---

## 2. 关键约束

本功能**特有**的硬性约束（决定下面方案对比的取舍空间）：

- **全局架构铁律遵循 `docs/constitution.md` C1–C7，本节不重抄。** 本 feature 的具体落点：
  - **C1**（分层）：新建 `term_library` 三层模块；`common/` 不得 import 领域服务——**这条直接决定了注入的实现形态**（决策 4）
  - **C2**（双 DB）：新表用 `dialect_helpers.JsonType`（别名字段是 JSON 数组）+ `LargeText`（释义可能较长）；平台的元数据表仍须 MySQL+DM8 双兼容
  - **C3**（多租户）：新表带 `tenant_id`；**并且必须把模型模块登记进 `bisheng/core/database/tenant_filter.py` 的 `_TENANT_AWARE_MODEL_MODULES`**。
    > **为什么「带列」还不够**：过滤是**启动时按 `SQLModel.metadata` 自动发现**的（`_discover_tenant_aware_tables` 只认「有 `tenant_id` 列」），而 `metadata` 只有在模型模块**被 import 过**之后才填得满。`_TENANT_AWARE_MODEL_MODULES` 就是那个「保证 import」的清单。
    > **漏登记的后果是静默的**：表能读写，只是**没有租户过滤**——跨租户可见。F061 把这条列为「易漏，坑 1」，并注明既有先例（`knowledgefile` / `flowversion` 曾因此把子租户资源写进 root）。
    > 登记位置与 F061 相同（`tenant_filter.py:74-82` 那一段的紧邻下方）。**验证方式**：断言模块名在 `_TENANT_AWARE_MODEL_MODULES` 中——该列表是字符串常量，读代码不可靠。
  - **C4**（权限）：**本特性不接 `PermissionService`**（决策 2）——用的是菜单/角色级 `web_menu` 口径，与知识库一致
  - **C5**（错误码）：模块 **115**，已在 v2.7.0 契约登记（2026-09-21 由 v2.8.0 移入）
  - **C7**（前端）：管理页取数走 `@/controllers/API/`，store 不调 HTTP

> **Constitution Check（门禁）**：C1–C7 **全部 PASS**，无违反。
> - C1 分层：新增 `term_library` 三层；注入纯函数在 domain、调用点在 api/workflow/workstation；**`common/` 不参与**（正是决策 4 选 B 的理由之一）
> - C2 双 DB：`JsonType` + `LargeText`（已在本仓确认二者存在）
> - C3 多租户：`tenant_id` 列 + `_TENANT_AWARE_MODEL_MODULES` 登记（见上）
> - C4 权限：本特性**不接** `PermissionService`（决策 2 已论证），用菜单/角色口径——**不违反 C4**，因为 C4 约束的是「受控资源的权限校验要走统一入口」，而术语库按定义不是受控资源
> - C5 错误码：模块 115，契约已登记
> - C6 密钥：本特性**不涉及任何凭据**（与 F061 数据源不同）
> - C7 前端：走 API 层
- **契约不变量（v2.7.0，2026-09-21 随本特性移入）**：INV-16（按租户隔离）、**INV-17（部门归属不是授权边界、不限定注入范围）**、INV-18（注入是运行时只读行为、不写任何持久化配置）
- **性能约束**：匹配与注入不得阻塞对话事件循环（spec AC-28）；注入开销不随库规模增长到影响该轮响应

### 2.1 ⚠️ INV-17 的方向与 F057 相反（最容易搞错的一条）

| | F057 知识库 | **F060 术语库** |
|---|---|---|
| 部门字段的性质 | **可见性边界** | **纯展示元数据** |
| 实现 | `PermissionService._implicit_department_library_level`(`permission_service.py:1185-1209`) 按**用户的** `department.path` 前缀判定 | **不得**有任何由部门派生的 resolver |
| 影响注入/检索范围 | 是 | **否**（注入一律用租户**全库**） |
| 管理页筛选 | 可用 | 可用（仅展示） |

> **实现期禁止事项**：不得在 `permission_service.py` 增加任何 `_implicit_department_term_*`；不得在任何注入路径上读 `department_id`。
> 契约原文已警告「二者不可类推」，spec AC-19 是它的 AC 化。

---

## 3. 方案对比与选定

### 决策 1：新建独立 `term_library` 领域对象，不进知识库体系

- **备选**：
  - A. 扩展 `KnowledgeTypeEnum` 加一类「术语表」，蹭知识库的列表页/授权/部门归属
  - B. 新建独立领域对象 + 独立模块
- **选定**：**B**
- **原因**：
  - 走 A 等于**给一份没有文档的配置交向量库的房租**：`KnowledgeService.create_knowledge_base` 强制 mint `collection_name`/`index_name` 并调 `_init_knowledge_indices_sync`，而 `model`（embedding 模型）是**必填**字段——术语没有文档、不切块、不向量化，这些字段对它全是空转。
  - 走 A 还要背 F055 的 `effective_date`/`expire_date`、`weight`/`priority` 与 F057 的 `department_id`——而 F057 的部门**是可见性边界**，套到术语库上直接违反 INV-17（决策见 §2.1）。
  - 与 F061 决策 1 同构（数据源同样未进 `KnowledgeTypeEnum`），已有先例与先例的教训。
- **何时该重新考虑**：若后续出现「术语与知识库必须在同一列表页统一管理」的强需求，可再加一层「统一资源入口」，但**不要为此把它们塞进同一张表**。

### 决策 2：不接 FGA——用与知识库一致的菜单/角色权限口径

- **备选**：
  - A. 新增 FGA 资源类型 `db_term`，按资源授权（仿 F061 数据源）
  - B. 复用**知识库的菜单/角色口径**：读 `knowledge`、写 `create_knowledge`
  - C. 完全放开：任何登录用户可读写
- **选定**：**B**（用户 2026-09-21 定案：「术语库的权限和知识库的权限设置一致」）
- **原因**：
  - **A 与本特性的资源模型不符**。数据源（F061）是**一堆需要分权的资源**，所以按资源授权成立；术语库是**一个租户一份的共享配置**，没有「这份术语归谁」的语义——把一份配置拆成 N 个可授权资源，管理员要维护的授权数量与词条数同量级，没有任何人需要这种粒度。
  - **C 太松**：术语内容会进入所有人的对话提示词，属于治理工具，与敏感词策略同性质；而 F059 的租户敏感词策略同样是菜单级。
  - **B 与「管理页放在知识库区」自洽**（决策 3）：页面门控 `knowledge` ≡ 读接口门控，**能打开页面的人就是能读的人**；写操作再收一道 `create_knowledge`，与知识库现有的「进得去区 ≠ 能建资源」完全一致。
- **何时该重新考虑**：若出现「不同部门各自维护自己的术语且互不可见」的需求——但**那会与 INV-17 直接冲突**，需先推翻不变量，属重新评审范畴。

### 决策 3：管理页作为知识库页的第三个 Tab

- **备选**：
  - A. 知识库页（`/filelib`，已有「文件数据」「问答库」两个 Tab）加第三个 Tab
  - B. 独立路由 + 侧边栏入口（仿 F061 数据源页）
  - C. 工作台配置页（`/build/client`）加 Tab
- **选定**：**A**（用户 2026-09-21 定案，随决策 2 一起）
- **原因**：
  - **门控必须与接口门控同集合**。`/filelib` 的 `permission: 'knowledge'` 正是读接口的门控；C 的 `workstation` 与之不重合，会产生「能开页但操作 403」与「有知识库权限却进不去页」双向错配。
  - 术语库与知识库在同一交互层，符合用户对「知识来源」的心智模型。
- **代价（已知）**：`KnowledgePage/index.tsx` 带 `@ts-strict-ignore` 且用 `window.LibPage` 处理默认 Tab。**新 Tab 组件本身是全新文件、全量 strict**，只是宿主页的老问题不因本特性而加重。
- **何时该重新考虑**：若术语库内容膨胀到 Tab 装不下（当前是「列表 + 弹窗 + 导入」，量级不构成问题）。

### 决策 4：注入实现为**纯函数**，三个落点各自调用（不穿 `dispatch_client`）

- **备选**：
  - A. 严格模仿 F056：新增参数经 `ChatManager.dispatch_client` → `base.py`/`client.py` → 传给 prompt 构造处
  - B. 一个纯函数（`build_term_block`），三个落点各自调用
- **选定**：**B**
- **原因**：
  - **A 的缝合点用错了**。查证：F056 的 `input_guard`/`output_guard` 是**前后过滤器，从不碰系统提示词**，且它们停在 `ChatClient` 边界（`base.py:32-33` / `client.py:66-67`）。契约原文写「复用 `guardrail.py` 的注入模式」是把「**对象怎么传**」误记成了「**提示词在哪注入**」——**契约已由本设计订正**（见 §10 偏差记录）。
  - **A 也统一不了形状**：三个落点里只有助手走 `dispatch_client`；工作流节点（`workflow/nodes/agent/agent.py`）与工作台（`workstation/domain/services/chat_service.py`）**本就不走**，仍要各自接。为「形状统一」多穿两层，换来的是一个并不统一的形状。
  - **B 让职责不混**：把「术语块」与「守卫」捆进同一个参数，会让一个参数承载两种生命周期与两种失败语义（守卫失败要拦；注入失败要静默降级）。
  - **C1 约束**：纯函数放 `term_library/domain/services/`，调用点在 `api/services`（助手）、`workflow/nodes`（节点）、`workstation/domain`（工作台）——**都是领域/服务层，不违反「`common/` 不得 import 领域服务」**，因为 `common/` 根本不参与。
- **何时该重新考虑**：若后续要求「术语注入必须先过某个守卫」（如注入内容也要过敏感词），那时需要一个统一的中枢——但那会引入新的安全语义，应单独评审。

### 决策 5：助手对话改为**逐轮**注入（`prompt` 传 callable）

- **备选**：
  - A. 会话开始时把术语块拼进 prompt 字符串，整会话不变
  - B. `create_react_agent(prompt=<callable>)`，每轮按本轮提问重新拼
- **选定**：**B**（用户 2026-09-21 定案，理由是 spec AC-25 / INV-18「立即生效」）
- **原因**：
  - **A 会让 AC-25 不成立**。`AssistantAgent` 在**同一会话内跨轮复用**（`common/chat/client.py:250-259` 仅在切会话时重建），A 之下「会话中途新增术语」要等下次会话才生效——与 INV-18 的字面要求不符。这与 F061 AC-52 是**同一类问题**（per-session vs per-turn），F061 的解法是 per-call 判权，此处是 per-turn 拼块。
  - **B 是框架原生支持，不是自造**：已在本仓验证 `create_react_agent` 的 `prompt` 接受 `Callable[[State], PromptValue | str | ...]`，且 `_get_prompt_runnable` 对 callable 分支正是 `RunnableCallable(prompt)`。
  - **工作流节点与工作台本就不受影响**：节点每次运行重算（`agent.py:597` 在 `_run` 内）、工作台每轮重算（`chat_service.py:1600`）。
- **⚠ 代价（必须处理）**：callable 在**每次模型调用**时执行，而 ReAct 一轮内可能调用模型多次（工具调用 → 观察 → 再调用）。因此：
  - `build_term_block` 必须**按 (tenant_id, 本轮提问) 缓存**，否则一次 ReAct 会重复匹配 N 次；
  - 缓存键**不含轮次计数**，同一轮的多次调用必须命中同一份结果（否则多步之间注入内容会变）。
- **何时该重新考虑**：若逐轮注入的实测开销不可接受——但匹配是自动机 + 缓存，量级远小于一次 LLM 调用。

### 决策 6：复用 F059 的 AC 自动机与内容寻址缓存

- **备选**：
  - A. 逐词 `in` 判断 / 正则拼接
  - B. 复用 `ACAutomaton`（F059）
- **选定**：**B**
- **原因**：
  - **A 会随库规模劣化**：500 条词 × 每次提问 = 500 次子串扫描，且别名还要乘一遍；自动机是单趟扫描。
  - **`ACAutomaton` 已被本仓验证可用**，且 `find_all_matches` 返回 `(end_position, word)`——**AC-23 的定序（按首次出现位置）正需要这个位置信息**，逐词 `in` 只能拿到布尔值，还得自己再找位置。
  - **缓存照搬 `sensitive_word_policy_service` 的内容寻址模式**：`sha256(词表内容)` 作 key，变更时 `clear_cache()`（push 失效，非 TTL）。理由见该文件 `_build_cache_key` 的既有注释。
- **⚠ 不复用 F059 的缓存实例**：那是一个 **platform/超管**服务的类级缓存，且其 key 含 platform 语义（`platform_word_service.py:87-91` 专门写了「为何不复用 F056 的缓存」）。术语库**自建**缓存，理由同构：缓存键的所有权应当与数据的租户归属一致。
- **何时该重新考虑**：若 F060 的库规模上限（500）未来大幅上调，需重新评估自动机构建成本；当前量级下构建一次是毫秒级。

### 决策 7：解析器抽共享函数，而非直调 F059 的服务

- **备选**：
  - A. 直接调 `PlatformWordService.parse_words_file`
  - B. 把纯解析部分抽到共享模块，F059 委派（公开签名不变），F060 调用
  - C. F060 自建解析器
- **选定**：**B**
- **原因**：
  - **A 的依赖方向是错的**：`PlatformWordService` 是**平台/超管**服务，术语库是**租户级**功能——租户功能反向依赖平台服务，且会把 F059 的模块（及其平台语义）拖进租户路径；其失败抛的是 **11301（F059 的错误码）**，会漏进租户流程。
  - **C 与契约冲突**：契约明确规定「若 F059 先落地解析器，F060 应改为调用而非另写」（且 F059 **确已落盘**，2026-09-17）。两份解析器必然在支持格式、表头规则、编码回退上漂移——spec AC-11 正是为禁止这一点而写。
  - **B 两边都对**：F059 公开签名不变（无破坏性变更），F060 抛自己的 11505，依赖方向变成「两者都向下依赖一个无状态工具」。
- **代价**：改动一个**已发布**文件（`platform_word_service.py`）。抽取是机械的、有测试卡着，且是契约明写的方向。
- **何时该重新考虑**：不需要——这是契约已确认的硬要求。

### 决策 8：导入文件的列语义定死为三列；**不支持 txt**

- **背景（实现 T-05 时发现的缺口）**：spec AC-07 写了「txt / csv / xlsx」，AC-10 要求「释义非空」，但**列格式在两份文档里都没定义**。而契约规定「F060 应复用 F059 的导入解析器」——F059 的解析器**只抽词**，没有「释义列」这个概念。**共享的前提是两边格式一致，实际不一致**，这是设计期的遗漏。
- **备选**：
  - A. 定 `标准词, 别名, 释义` 三列，csv/xlsx 支持，**txt 不支持**
  - B. 三种格式都支持，txt 用制表符分隔三列
  - C. 复用 F059 的「一行一个词」格式，术语的释义留空
- **选定**：**A**（用户 2026-09-21 定案）
- **原因**：
  - **C 不可行**：AC-10 要求导入遵守与手工录入**同一套**校验，而释义非空是其中一条——一行一个词的格式导入后**每一条都会被拒**，等于提供一个必然失败的入口。
  - **B 的收益不抵成本**：txt 与 csv 在三列语义下实际是**同一套分隔规则**（制表符 vs 逗号），只是扩展名不同；多一个格式分支、多一组测试，而用户导出词表用的本来就是 Excel/CSV。
  - **A 把不支持的格式挡在入口**：明确报「不支持 txt」比让用户传上去再看到「全部被拒」好。
- **共享解析器的边界因此收窄**：**只共用「读成行」这一层**——编码回退（utf-8-sig / gbk）、NUL 检测、xlsx 读取、表头识别；**列语义各归各**（F059 是「每格一个词」，F060 是「前三列 = 标准词/别名/释义」）。这仍满足契约的意图（去掉重复的编码与格式处理），但不再假装两者格式相同。
- **何时该重新考虑**：若客户实际提供的词表就是「一行一个词 + 另附一份释义表」，那时需要的是**两份文件关联**的导入，属新需求。

---

## 4. 详细设计

### 4.1 数据流

```
管理员侧
  KnowledgePage 第三个 Tab（/filelib, permission: 'knowledge'）
    ├─ 列表（部门筛选 + 分页）      → GET    /api/v1/term-library
    ├─ 创建/编辑弹窗                → POST   /api/v1/term-library
    │                                 PUT    /api/v1/term-library/{id}
    ├─ 删除                         → DELETE /api/v1/term-library/{id}
    └─ 导入（预览 → 覆盖/追加）      → POST   /api/v1/term-library/import/preview
                                      POST   /api/v1/term-library/import
                ↓
  term_library/api/endpoints/*.py      （门控：读 knowledge / 写 create_knowledge）
                ↓
  term_library/domain/services/term_service.py   （编排、校验、上限、唯一性）
                ↓
  term_library/domain/models/{dao,term}.py       （DAO 强制 tenant_id）

对话侧（三个落点，各自调用同一纯函数）
  ┌────────────────────────────┬──────────────────────────────────────┐
  │ assistant_agent.py:446     │ prompt=callable（逐轮，决策 5）        │
  │ workflow/nodes/agent:597   │ callable（节点每次运行）              │
  │ workstation chat_service   │ 拼 sys_prompt 时追加（逐轮）          │
  └────────────────────────────┴──────────────────────────────────────┘
                ↓
  term_library/domain/services/injection.py
      build_term_block(tenant_id, user_query) -> str
        ① 取本租户全库（DAO，带租户过滤）
        ② 构建/命中 AC 自动机（内容寻址缓存，决策 6）
        ③ 匹配 → 归并别名 → 定序（AC-23）→ 按字符数封顶 → 拼区块
        ④ 未命中返回 ""（AC-22：不注入空区块）
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `term_library_term.standard_term` | varchar | 标准词；**租户内唯一**（AC-02） | 匹配、注入、管理页 |
| `term_library_term.aliases` | JSON（`dialect_helpers.JsonType`） | 别名数组，可为空 | 匹配（进自动机）、注入区块列出 |
| `term_library_term.definition` | `LargeText` | 释义，**≤200 字**（AC-04）。用 LargeText 而非 varchar 是因为 DM8/MySQL 的 varchar 语义与长度单位不一致，既有代码已有此惯例 | 注入区块正文 |
| `term_library_term.department_id` | int NULL | **纯展示元数据**（INV-17，见 §2.1） | **仅管理页筛选**；**注入路径不得读它** |
| `term_library_term.tenant_id` | int NOT NULL | 租户隔离（INV-16/C3）。**光有列不够**：模型模块必须登记进 `tenant_filter.py` 的 `_TENANT_AWARE_MODEL_MODULES`，否则过滤是静默失效的（见 §2 约束 C3 与坑 10） | DAO 强制过滤 |
| 注入区块 | 纯文本，形如 `【本轮相关术语】\n- 标准词（别名：…）：释义\n…`（**格式待实现期定稿**，见 §9 待定项） | 追加进系统提示词；**不落库**（INV-18） | 模型 |
| 缓存键 | `sha256(词表内容)` + `case_sensitive` | 内容寻址（决策 6）；变更时 push 失效 | 注入模块内部 |

### 4.3 关键模块职责

| 模块 | 职责 | 边界 |
|---|---|---|
| `term_library/domain/models/` | ORM + DAO | 不写业务编排；DAO 强制租户过滤 |
| `term_library/domain/services/term_service.py` | CRUD 编排、唯一性、上限、部门筛选 | 不直接写 ORM（走 DAO）；不 import api |
| `term_library/domain/services/injection.py` | **纯函数** `build_term_block`：匹配 + 归并 + 定序 + 封顶 + 拼块 | 无 DB 写、无状态；**不得读 department_id** |
| `term_library/api/endpoints/` | HTTP 端点 + 门控 | 不跨模块 import（RULE-5） |
| `term_library/domain/services/term_importer.py` | 包装共享解析器、执行 F060 侧校验（11505/11502/11503/11504） | 不自写解析 |
| `common/errcode/term_library.py` | 错误码 115xx | — |
| **（改）**`sensitive_word/domain/services/tabular_import.py` | **新增**：纯解析共享函数 | 无 DB、无服务状态 |
| **（改）**`sensitive_word/domain/services/platform_word_service.py` | `parse_words_file` 改为**委派**共享函数，**公开签名不变** | 不得改变 F059 的对外行为 |

### 4.4 三个注入落点的具体形态

| 落点 | 现状 | 改动 |
|---|---|---|
| **助手**（`api/services/assistant_agent.py:446`） | `prompt = self.assistant.prompt`（字符串） | 改为 callable：从 `state["messages"]` 取**本轮最后一条用户消息**，调 `build_term_block`，返回 `[SystemMessage(原文 + 术语块)] + messages`。`prompt_has_citation_rules` 那套「已含则不再注入」的思路对术语块**不适用**（术语块每轮不同，必须每轮重算） |
| **工作流节点**（`workflow/nodes/agent/agent.py:597`） | `_init_agent(system_prompt)` 在 `_run` 内，**此时还不知道该节点输入**（用户提示词在 `_run_once:747` 才 format） | 同样改 callable；节点已有 `self.tenant_id` |
| **工作台**（`workstation/domain/services/chat_service.py:1600-1608`） | 每轮拼 `sys_prompt` 字符串 | 在该处追加 `build_term_block(...)` 的返回值（空串则不追加）。本轮提问在该函数作用域内可得 |

---

## 5. 已知坑 / 反直觉事实

| # | 坑 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **契约说的 F056 缝合并不能注入提示词** | 按契约去穿 `dispatch_client`，做完了发现守卫从不碰 prompt，白改两层且没统一形状 | §3 决策 4；契约已订正 |
| 2 | **INV-17 的方向与 F057 相反** | 照 F057 给部门加 resolver → 术语按部门可见，**违反不变量**且注入范围被悄悄收窄 | §2.1；spec AC-19 |
| 3 | **callable 每次模型调用都执行**（不是每轮一次） | 一次 ReAct 重复匹配 N 次；若缓存键含轮次计数，多步之间注入内容会**变**，同轮答案自相矛盾 | §3 决策 5 的代价段 |
| 4 | **助手 agent 跨轮复用** | 以为改 callable 就够了 → 不，还要意识到 `prompt` 一次性传入后**不能靠重建 agent 生效**，只能靠 callable 每轮求值 | §3 决策 5 |
| 5 | **工作流节点 `_init_agent` 早于用户提示词 format** | 在 597 行拿不到节点输入 → 必须用 callable，不能用「先拼字符串」 | §4.4 |
| 6 | **`ACAutomaton.find_all_matches` 返回 `(end_position, word)`** | 若用 `find_all`（返回 `Counter[str]`）拿不到位置 → **AC-23 的定序无法实现** | §3 决策 6；§4.2 |
| 7 | **F059 的缓存是 platform 服务的类级缓存** | 直接复用 → 租户数据进平台缓存，或反之；且其 key 含 platform 语义 | §3 决策 6 末段 |
| 8 | **`KnowledgePage/index.tsx` 带 `@ts-strict-ignore`** | 以为整个特性都没 strict 检查 → 新 Tab 组件**是全新文件，必须全量 strict** | §3 决策 3 的代价 |
| 9 | **注入内容会进提示词** | 若把它写进 `assistant.prompt` 或应用配置 → **违反 INV-18**，且用户提示词字段被悄悄改掉 | spec AC-26；§4.4 |
| 10 | **租户过滤靠「带 `tenant_id` 列」自动发现，但 metadata 要先 import** | 以为「加了 `tenant_id` 列就自动隔离」→ 不登记模块则**表照常读写、只是没有租户过滤**，跨租户可见且**不报错**。F061 曾为此把「登记」单列一个任务（T-03，标注「易漏，坑 1」） | §2 约束 C3；任务须与 F061 T-03 同构 |
| 11 | **`ACAutomaton` 的匹配是**大小写敏感的（由调用方 lower 后传入）** | 在 F059 里这是有意的（拦词误拦/漏拦的取舍）；术语匹配直接照搬会**漏掉大小写不同的提问** → 传入前按本特性的口径处理（§9 待定项 3） | §9 待定项 3 |

---

## 6. 对外契约 / 依赖

### Outgoing（本特性提供）

| 契约 | 形式 | 谁会消费 |
|---|---|---|
| `/api/v1/term-library`（CRUD + 部门筛选 + 分页） | HTTP API | platform 前端（知识库页第三个 Tab） |
| `/api/v1/term-library/import/preview` · `/import` | HTTP API | 同上 |
| `build_term_block(tenant_id, user_query) -> str` | 领域服务纯函数 | 三个对话落点（助手 / 工作流节点 / 工作台） |
| 错误码 115xx | 错误码 | 前端错误提示（三语） |

### Incoming（本特性依赖）

| 依赖 | 用途 | 稳定性 |
|---|---|---|
| `sensitive_word/domain/services/ac_automaton.py` | 匹配 | 复用，不改 |
| `sensitive_word/domain/services/tabular_import.py`（**新增**） | 导入解析 | 本特性与 F059 共同依赖 |
| `DepartmentDao.aget_subtree_ids` | 部门子树筛选 | 复用，不改 |
| `create_react_agent(prompt=callable)` | 逐轮注入 | **框架行为，已在 langgraph>=1.2,<2.0 验证**；升级需复验（§6 表下注） |
| `AuthzService` / `WebMenuResource` | `knowledge` / `create_knowledge` 门控 | 复用，不改 |

> **框架依赖的风险**：`prompt` 接受 callable 是 langgraph 的**公开签名**（已验证 `_get_prompt_runnable` 的 callable 分支），但它不是本项目的代码——langgraph 升级若移除该形态，逐轮注入会失效。**实现期须有一条测试钉住它**（构造一个 callable prompt 的 agent 并断言它被调用），使升级失败在测试而非线上。

---

## 7. 测试与可观测

### 7.1 分层策略

| 层 | 覆盖什么 | 用什么 |
|---|---|---|
| **纯函数单测** | `build_term_block` 的**匹配 / 别名归并 / 定序 / 封顶 / 未命中返回空**（spec AC-20~23）——**本特性的核心逻辑集中在这里**，且无 IO，是性价比最高的一层 | `test/term_library/test_injection.py` |
| **Service 单测** | CRUD 的校验与上限（AC-02/03/04/06）、导入的预览与两种模式（AC-07~10）、部门筛选（AC-13~15）| `test/term_library/test_term_service.py`（DAO 打桩，**service 是被测对象，不 mock 掉**） |
| **API 测试** | 门控口径（AC-16~18：读 `knowledge` / 写 `create_knowledge`，**含「无权限被拒」的用例**）、端点形状、错误码 | `test/term_library/test_term_api.py` |
| **注入落点测试** | **三个落点各一条**：断言注入真的发生在该落点（AC-24）。这类「接线」最容易漏——F061 的 `ChatInput` 缺 import 就是这类，组件自测全绿也测不到 | `test/term_library/test_injection_sites.py` |
| **不变量测试** | INV-17（部门不影响注入范围）、INV-18（不改任何持久化配置）、AC-29~32（不进知识库体系、无术语时零变化） | `test/term_library/test_invariants.py` |
| **e2e** | 「改词 → 下一轮对话即生效」（AC-25 的行为断言，需真栈 + LLM） | `test/e2e/`（CI） |

> **框架依赖须钉住**（§6 表下注）：必须有一条测试构造 `prompt=<callable>` 的 agent 并断言 callable 被调用——`prompt` 接受 callable 是 langgraph 的**公开签名**，但不是本项目的代码，升级可能移除它。

### 7.2 每条 AC 的验证口径

- **AC-20~23**（匹配/归并/定序/封顶）：纯函数单测，**每条 AC 一个用例**，定序用「构造同位置不同长度」的输入断言全序。
- **AC-24**（三条落点）：各一条接线断言（见上）。
- **AC-25**（立即生效）：e2e；单测层用「两次调用之间改词库 → 第二次结果变」近似。
- **AC-26**（不写持久化配置）：**静态断言**——注入路径不出现 `assistant.prompt` / 配置写入；再补一条行为断言（改词后 `AssistantInfo.prompt` 不变）。
- **INV-17**（部门不影响注入范围）：静态断言（注入模块不出现 `department_id`）+ 行为断言（两个部门各一条术语，任一部门用户的提问都能命中全部的术语）。
- **AC-29**（不进知识库体系）：静态断言（`KnowledgeTypeEnum` 无术语成员）+ 行为断言（建术语不产生 Milvus/ES 索引）——**与 F061 T-44 同形**。

### 7.3 手动验证一遍（可操作路径）

1. **起服务**：
   - 后端：`cd src/backend && uv run uvicorn bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1 --no-access-log`
   - 平台前端：`cd src/frontend && pnpm start -- --host 0.0.0.0`（dev :3001）
   - 客户端（验注入）：`:4001`（base `/workspace`）
2. **建词**：platform 登录（超管）→「知识库」→「术语库」Tab → 新建：标准词「不良贷款率」/ 别名「不良率」/ 释义「不良贷款占贷款总额的比例」→ 保存。
3. **验注入**：client 用一个**配置了助手的应用**发起对话，提问含「不良率」→ 回答应体现「不良贷款率」的语义（而不是把它当普通词）。**对照**：先问一个不含任何术语的问题 → 对话行为与加词前一致（AC-22 / AC-32）。
4. **验立即生效**：**不重开会话**，在同一会话里改释义（例如把比例口径改成「五级分类口径」）→ 下一轮提问应体现新释义（AC-25）。
5. **验不落库**：改词后在 platform 打开该助手详情 → **提示词字段仍是原样**（AC-26）。
6. **验权限**：用一个只有 `knowledge`、没有 `create_knowledge` 的账号登录 → 能进术语库 Tab、能看列表、**新建/导入按钮不可用或被拒**（AC-16~18）。
7. **验导入**：准备一个 xlsx（`标准词,别名,释义` 三列）→ 上传 → 看预览差异 → 选追加 → 确认 → 列表出现新词条。

### 7.4 可观测

- **日志**：注入命中数、注入字符数、是否触发封顶（`logger.debug` 级，避免每轮刷屏；封顶触发用 `info`——它是「库该治理了」的信号）。
- **失败降级**：注入**任何**异常（DB 不可用、自动机构建失败）SHALL 记 `warning` 并**返回空串继续对话**——术语库是增强，不是对话的前置条件。**此行为须有单测**。

---

## 8. 后续改进 / 不打算做的事

| 项 | 为何不做 | 何时可重新考虑 |
|---|---|---|
| **四个对话入口的注入**（渠道文章聊天 / 知识空间聊天 / 知识 RAG 管道 / linsight 任务内核） | spec §7 已登记为**已知缺口**；每处是独立的提示词组装点，接入需各自评估。**不因本特性而变差**（本来就没有术语注入），也不因本特性而变好 | 出现「任务模式听不懂行话」的实际投诉时，作为独立 Feature 走 spec 评审 |
| **术语自动抽取** | F059 `answer_suggestion_service` 已在做（从对话抽取候选词），本特性只做人工维护 + 导入 | 若抽取结果需要「一键入库」，那是 F059 与本特性的集成，应单独评审 |
| **按应用/按会话选择性生效** | 注入范围是「租户全库」（INV-17 明令部门不限定范围）；引入按应用作用域会与「术语库是一份共享配置」的定位冲突 | 若出现「某术语只在某应用成立」的真实需求——但需先解决「同一份库在不同应用语义冲突」的问题 |
| **术语的版本化 / 优先级 / 冲突消解** | spec 明确排除；500 条规模下人工可管 | 库规模或更新频率显著上升时 |
| **把注入结果落库（供审计）** | **与 INV-18 冲突**（注入是运行时只读行为）。若确需审计「这轮注入了什么」，应落**独立的日志表**而非写进任何配置 | 若出现合规审计要求，需先改不变量 |
| **F059 缓存与本特性缓存的合并** | 两者的 key 语义不同（platform vs tenant），F059 自己就专门写了「为何不复用 F056 的缓存」 | 不需要 |

---

## 9. 待定项（实现期定稿）

| # | 项 | 定稿 | 落地位置 |
|---|---|---|---|
| 1 | 注入区块的**精确文案与格式** | **已定稿，按倾向实现**：区块头 `【本轮相关术语】`，每条一行 `- 标准词（别名：a、b）：释义`（无别名时省略括号段）。截断时追加一行 `（还有 N 条本轮命中的术语未展开）`。**注意**：模型遵循度未做实测（需真 LLM），属 §2.1 e2e 的观察项 | `injection.py` 的 `_BLOCK_HEADER` / `_ALIAS_TEMPLATE` / `_render` / `_render_capped` |
| 2 | 单轮注入的**字符上限具体值** | **定为 2000 字符**（模块级常量 `MAX_BLOCK_CHARS`，可调）。**未实测**对回答质量的影响——实测需真 LLM，同上 | `injection.py: MAX_BLOCK_CHARS` |
| 3 | 是否区分 `case_sensitive` | **定为不区分**（倾向落地）。实现上**两侧都 fold**：自动机构建时把标准词与别名 lower，提问也 lower 后再扫。**只 fold 提问是那个「看起来够用」的半吊子做法**——`AB` 永远匹配不到 `ab`，且症状表现为「ASCII 术语不生效」而不像大小写 bug（坑 11 的正面处理） | `injection.py` 的 `_build_automaton` / `_match` |

> **⚠ 三项均未经真模型验证**：1 的「模型遵循度」和 2 的「上限对回答质量的影响」在设计里就写明要实测，而本机没有可用的 LLM 环境。当前取值是**合理初值**，定稿依据是「与设计倾向一致」而非「实测结论」。真栈验证见 [e2e-checklist.md](./e2e-checklist.md) §2.1。

---

## 10. 偏差记录

（实现期回填：与本文档不一致的落地、被推翻的决策、新增的坑）

> 逐任务的完整记录（含每条变异验证结果）在 [tasks.md](./tasks.md) §实际偏差记录。此处只收**与本文档直接冲突、需要改设计**的部分。

### 10.1 被推翻的设计结论

| # | 设计原文 | 实现期结论 | 影响 |
|---|---|---|---|
| 1 | **§6 表下注**：「`prompt` 接受 callable 是 langgraph 的公开签名（**已验证** `_get_prompt_runnable` 的 callable 分支）」 | **结论正确但不充分**。`_get_prompt_runnable` 用 `inspect.iscoroutinefunction(prompt)` 区分同步/异步两种 callable：**同步 callable 返回协程会被直接丢弃**（`coroutine was never awaited` → 模型侧 `ValueError: Invalid input type <class 'coroutine'>`）。注入必须用**真正的 `async def`**，不能用 lambda 返回协程 | 落点实现改用 `term_prompt_callable` 工厂（`async def` 闭包）。§4.4 第一行「改为 callable」的表述需按此理解 |
| 2 | **§4.4 第一/二行**：「`prompt` 由字符串改为 callable」（三处落点统一口径） | **只对两个 langgraph 分支成立**。助手与工作流节点各有一个 **ReAct 分支**走 `ConfigurableAssistant(assistant_message=str)`，其 `prompt.partial()` **本就接受零参 callable**（`BasePromptTemplate._merge_partial_and_user_variables` 的 `v if not callable(v) else v()`），但只能零参、且**在异步路径上不能做 I/O** | 最终形状改为**「块解析在知道本轮提问的那一层完成，prompt 层只做拼接」**。新增模块 `term_library/domain/services/prompt.py` |
| 3 | **§4.3 模块职责表**：`injection.py` 列为「**纯函数**…无状态」 | `build_term_block` 有**单条记忆化缓存**（`_CACHE_KEY` / `_CACHE_VALUE`），并非无状态。这是决策 6「内容寻址缓存」的落地，设计表里写「无状态」与决策 6 自相矛盾——**以决策 6 为准** | 无 |
| 4 | **§4.3**：`（改）sensitive_word/.../tabular_import.py`（T-05 新增共享函数） | T-05 已落盘；**T-09 未再改动该文件**（`git diff` 为空）。原计划让它多担「空文件判定」，实测该守卫**无法变异验证**（F059 侧本就自带空判定），故不写入已发布文件 | §10.2 新增坑 1 |
| 5 | **§3 决策 2** 的权限口径 | 落地为**读查 `knowledge`、写查 `create_knowledge`**。补充事实：本仓 `knowledge` 键**在此之前没有任何调用方**（知识库的读接口不设门控，只有写接口查 `CREATE_KNOWLEDGE`）。故本特性读接口**比既有知识库更严** | 若出现「能读知识库列表却读不了术语库」的账号，原因在此 |

### 10.2 实现期新增的坑

| # | 坑 | 会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **共享读取层的「空文件」守卫无法变异验证** | 在 `tabular_import.read_rows` 里加空文件判定后，删掉它两个 importer 仍然一致（各自都有兜底）——**一条无法失败的断言等于没测**，而且它会改动 F059 已发布文件 | tasks.md §T-09 偏差 1；共享层**未改动** |
| 2 | **ReAct 分支的 `assistant_message` 是「每次模型调用」重算** | 实测两次模型调用的回合里 builder 被调 2 次。对 F060 是**加强**（每步都拿到最新词表）；但若哪天把 I/O 放进 builder，一个回合会读两次库 | `prompt.py` 的设计前提；`build_term_block` 缓存兜底 |
| 3 | **`agent_runnable` 在 ReAct 执行器里是 `RunnableSequence`，没有 `.llm`** | 曾试图在 executor 内部重建 agent 以注入每轮提示词，`AttributeError: 'RunnableSequence' object has no attribute 'llm'` | **该改动已整体回退**（`bisheng_langchain/` 零 diff）——原 executor 本就支持零参 callable，不需要改 |
| 4 | **CSV 逗号列切分不能吞掉空列** | `[,，]+` 会把 `标准词,,释义`（别名留空的**最常见行**）的两个逗号并作一个，释义串进别名列 → 整行按「释义为空」被拒 | `term_importer.COLUMN_SEPARATOR_RE` 用不可重复的 `[,，]`；坑由**修正测试夹具**（原先用制表符拼「csv」，与共享层在制表符上切列**互相抵消**，真实 csv 路径一行未测）后才暴露 |
| 5 | **`ast.FunctionDef` 抓不到 `async def`** | 静态扫描「注入路径不调写方法」时，DAO 全是 `async def` → writer 集合为空 → 断言**静默空转通过** | `test_invariants.py` 用 `ast.AsyncFunctionDef`，并加一条「集合必须非空」防再次空转 |
| 6 | **文本级静态断言会被自己的 docstring 打红** | `'PermissionService' not in source` 对「文档里提了这个词」与「代码里调了它」不加区分 | 静态扫描一律走 AST，只认真实名字引用（`test_invariants.py`） |
| 7 | **第一行释义恰好是「释义」会被当表头丢掉** | 表头识别是共享层既有规则（`HEADER_TOKENS` 含 `释义`/`别名`），命中即丢首行。无表头且首行释义写成「释义」的文件会**静默少一条** | 已知限制，未处理（共享层既有取舍）；见 tasks.md §T-09 偏差 7 |

---

## 11. 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-21 | **§9 三项待定项定稿**（区块格式 / 字符上限 2000 / 大小写不敏感且两侧 fold）；**§10 回填实现期偏差**：5 条被推翻的设计结论 + 7 条新增坑 | 实现骨架完成（T-01 ~ T-18）；由 T-12/13/14 实测 langgraph 的 `iscoroutinefunction` 分派、T-15 的静态扫描、T-09 的 CSV 夹具修正发现 |
| 2026-09-21 | 初版（7 条决策 + 9 条坑 + 4 项待定） | 设计阶段；基于 Spec Discovery 的 7 项用户决策 + 全链路代码盘点（F056 缝合点、F059 自动机/缓存/解析器、F057 部门展开、langgraph callable prompt） |
| 2026-09-21 | **新增决策 8（导入列语义与 txt 取舍）**：实现 T-05 时发现 spec/design **都没定义导入文件的列格式**，而契约要求复用 F059 的解析器——后者只抽词、没有释义列，**共享的前提不成立**。定 `标准词, 别名, 释义` 三列（csv/xlsx，表头可省），**不支持 txt**（写成三列的 txt 与 csv 同规则，无独立价值；写成一行一​​个词则必然被 AC-10 拒）。共享边界随之收窄为「只共用读成行那一层」。**同步修订 spec AC-07/AC-11 与 tasks T-05/T-09** | 实现期；由「要共享的解析器到底共享什么」这一步发现 |
| 2026-09-21 | **补 §7 测试与可观测 / §8 后续改进 / Constitution Check**（`/sdd-review design` 前自检发现模板 §7/§8 缺失；C1–C7 结论显式补上）。同时把 §2 与 §4.2 的「登记租户白名单」改为真实机制名 `_TENANT_AWARE_MODEL_MODULES` 并说明「光有列不够」的静默失效 | 对照 `features/_templates/design.md` 与 `design-checklist.md` 自检 |
| 2026-09-21 | **订正契约两处过期前提**（同步回写契约）：① 「F059 解析器尚未落盘」不成立（`4e21b89cc` 已落），F060 按契约自身的兜底条款改为**抽共享解析函数**而非自建；② 契约原写「复用 `guardrail.py` 经 `dispatch_client` 注入」——查证 `input_guard`/`output_guard` 是**前后过滤器，从不碰系统提示词**，该处**不是**提示词注入点。F060 改为**三个落点各自调用同一纯函数** | 设计期盘点；契约原文把「对象传递模式」误记为「提示词注入缝合点」 |
