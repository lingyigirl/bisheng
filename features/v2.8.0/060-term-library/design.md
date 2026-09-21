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
**版本**: v2.8.0
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
  - **C5**（错误码）：模块 **115**，已在 v2.8.0 契约登记
  - **C7**（前端）：管理页取数走 `@/controllers/API/`，store 不调 HTTP
- **契约不变量（v2.8.0）**：INV-16（按租户隔离）、**INV-17（部门归属不是授权边界、不限定注入范围）**、INV-18（注入是运行时只读行为、不写任何持久化配置）
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
  - **A 的缝合点用错了**。查证：F056 的 `input_guard`/`output_guard` 是**前后过滤器，从不碰系统提示词**，且它们停在 `ChatClient` 边界（`base.py:32-33` / `client.py:66-67`）。契约原文写「复用 `guardrail.py` 的注入模式」是把「**对象怎么传**」误记成了「**提示词在哪注入**」——**契约已由本设计订正**（见 §9）。
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
  - **C 与契约冲突**：v2.8.0 契约明确规定「若 F059 先落地解析器，F060 应改为调用而非另写」（且 F059 **确已落盘**，2026-09-17）。两份解析器必然在支持格式、表头规则、编码回退上漂移——spec AC-11 正是为禁止这一点而写。
  - **B 两边都对**：F059 公开签名不变（无破坏性变更），F060 抛自己的 11505，依赖方向变成「两者都向下依赖一个无状态工具」。
- **代价**：改动一个**已发布**文件（`platform_word_service.py`）。抽取是机械的、有测试卡着，且是契约明写的方向。
- **何时该重新考虑**：不需要——这是契约已确认的硬要求。

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
| `term_library_term.tenant_id` | int NOT NULL | 租户隔离（INV-16/C3）；**必须登记进租户白名单** | DAO 强制过滤 |
| 注入区块 | 纯文本，形如 `【本轮相关术语】\n- 标准词（别名：…）：释义\n…`（**格式待实现期定稿**，见 §7） | 追加进系统提示词；**不落库**（INV-18） | 模型 |
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
| 11 | **`ACAutomaton` 的匹配是**大小写敏感的（由调用方 lower 后传入）** | 在 F059 里这是有意的（拦词误拦/漏拦的取舍）；术语匹配直接照搬会**漏掉大小写不同的提问** → 传入前按本特性的口径处理（§7 待定项 3） | §7 待定项 3 |

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

## 7. 待定项（实现期定稿）

| # | 项 | 倾向 | 何时定 |
|---|---|---|---|
| 1 | 注入区块的**精确文案与格式** | 形如 `【本轮相关术语】\n- 标准词（别名：a、b）：释义`；需实测模型对格式的遵循度 | T-08 实现期 |
| 2 | 单轮注入的**字符上限具体值** | 初值 2000 字符（可调常量）；实测对回答质量的影响后定 | T-08 实现期 |
| 3 | 是否区分 `case_sensitive` | 倾向**否**：术语匹配大小写不敏感更符合「听懂行话」的意图（与 F059 拦词不同，那里大小写敏感与否是误拦/漏拦的取舍） | T-08 实现期 |

---

## 8. 偏差记录

（实现期回填：与本文档不一致的落地、被推翻的决策、新增的坑）

---

## 9. 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-21 | 初版（7 条决策 + 9 条坑 + 4 项待定） | 设计阶段；基于 Spec Discovery 的 7 项用户决策 + 全链路代码盘点（F056 缝合点、F059 自动机/缓存/解析器、F057 部门展开、langgraph callable prompt） |
| 2026-09-21 | **订正 v2.8.0 契约两处过期前提**（同步回写契约）：① 「F059 解析器尚未落盘」不成立（`4e21b89cc` 已落），F060 按契约自身的兜底条款改为**抽共享解析函数**而非自建；② 契约原写「复用 `guardrail.py` 经 `dispatch_client` 注入」——查证 `input_guard`/`output_guard` 是**前后过滤器，从不碰系统提示词**，该处**不是**提示词注入点。F060 改为**三个落点各自调用同一纯函数** | 设计期盘点；契约原文把「对象传递模式」误记为「提示词注入缝合点」 |
