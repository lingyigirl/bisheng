# Design: 对话合规与回答区增强（059-chat-compliance-and-answer-enhancements）

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
**版本**: v2.7.0
**最后更新**: 2026-09-17（初版）

---

## 1. 目标与非目标

- **目标**：在 F056 已有的对话围栏链路上**加一层平台级强制词库**（超管维护、全租户强制、内容不外泄），并补上对话留痕（评价事件流水）与回答区的三类增强（场景热词 / 关联问题 / 友情提醒）。
- **非目标**：
  - 不重写 F056 的检测链路（复用 `ChatGuardService` 与 AC 自动机，仅在判定顺序上插入平台层）
  - 不做信贷系统直连同步（词库靠人工导入）
  - 不做语义级内容安全（沿用 F056 口径）
  - 热词/关联问题不落库、不做效果回收

---

## 2. 关键约束

遵循 `docs/constitution.md` C1–C7 与 `features/v2.7.0/release-contract.md`。本功能特有约束：

| # | 约束 | 对方案的影响 |
|---|---|---|
| K1 | **全局数据 vs 租户自动过滤（C3）**：平台词库对全部租户可见，而 `tenant_filter.py` 的 `do_orm_execute` 会给所有带 `tenant_id` 的表注入 `WHERE tenant_id = <当前>` | 平台表**不设 tenant_id 列**（`_discover_tenant_aware_tables` 按列存在性发现，无列即不被过滤），从根上绕开而非逐处 bypass |
| K2 | **命中词不得回流租户侧**（INV-10） | 命中日志的租户查询路径必须对平台来源的行**在响应构造处**抹掉 `hits`，不能只在页面隐藏 |
| K3 | **不得阻塞流式输出**（AC-30/AC-39） | 热词与关联问题一律**回答结束后**生成，不进入任何分片处理路径 |
| K4 | **既有评价交互零变更**（AC-28） | 评价事件只在 `like_response` **早期 return 之后**写入，不改变任何既有分支 |
| K5 | **关联问题依赖应用绑定的知识库**——助手走 `AssistantLink`（`assistant_link.knowledge_id`），工作流/知识空间的绑定在 flow 节点配置里，形态不同 | 关联问题的知识库解析必须**容忍取不到**（取不到即静默不展示，AC-42），不做兜底猜测 |
| K6 | **热词不需知识库**（AC-34） | 热词链路与关联问题链路**分开实现**，只是共用一次 HTTP 往返与模型槽位 |
| K7 | **双 DB（C2）**：新表用 `dialect_helpers.JsonType` / `LargeText`，禁 MySQL 专有语法 | 平台词条表、平台策略表、评价事件表按此约束设计 |
| K8 | **API 层才能拿到租户 ContextVar 与登录用户**（F056 既定） | 全部检测与留痕写入都在 API 层，不进 Celery |
| K9 | **平台词库的鉴权不能复用 `get_tenant_admin_user`**：该方法（`common/dependencies/user_deps.py:78`）的设计是「全局超管 **或** 当前租户的子管理员」，后者会读 `has_tenant_admin(tid)` 放行 | 平台词库的全部管理端点必须**另行**走 `_check_is_global_super(user.user_id)`（`utils/http_middleware.py:145`），否则租户管理员可读写全局词库，击穿 AC-06 与 INV-10 |
| K10 | **新增带 `tenant_id` 的表必须进 `_TENANT_AWARE_MODEL_MODULES` 白名单**（`core/database/tenant_filter.py:51+`）。该白名单是硬编码的，表发现按**列存在性**在启动时一次性完成 | 评价事件表若不加进白名单，会**静默失去租户过滤**——租户互相看到彼此的评价明细，且 `before_flush` 也不回填 `tenant_id`（写入为空值） |

---

## 3. 方案对比与选定

### 决策 1：平台级词库存哪张表

- **备选**：
  - A. 复用 F036/F056 的 `sensitive_word_policy`，用保留 `tenant_id`（如 0）存全局行 — 零新表
  - B. 新建独立的全局表（词条表 + 策略表），**不带 tenant_id 列** — 两张新表
- **选定**：**B**
- **原因**：A 的致命问题是 `tenant_filter.py:do_orm_execute` 会给该表注入 `WHERE tenant_id = <当前>`，且 `before_flush` 会自动回填 `tenant_id`——全局行既**写不进去**（被回填成当前租户）也**读不出来**（被过滤掉），要靠逐处 `bypass_tenant_filter()` 兜，任何一个漏掉的读路径都会静默变成"词库为空、不检测"。而 `_discover_tenant_aware_tables()` 是**按列存在性**发现表的：不带 `tenant_id` 列的表天然不进过滤集合，从根上消除这类漏点。
- **配套约束**：平台两张表**永远不得**补 `tenant_id` 列。一旦有人"为了统一"补上，过滤会立刻静默生效、词库变空（见 §5 坑 2）。平台表加列需走本 feature 的评审。
- **鉴权配套**：平台词库的管理端点必须走 `_check_is_global_super` 而非 `get_tenant_admin_user`（见 K9）。
- **何时该重新考虑**：若将来需要平台级策略也支持"某租户覆盖"（即决策 4 的强制语义被推翻），两张表要合并成一张带 `scope_type` 的表，届时 A 的隔离问题需要重新设计。

### 决策 2：平台层面的注入点

- **备选**：
  - A. 在 `api/services/guardrail.py` 的 `build_input_guard` / `build_output_guard` 闭包里先跑平台检测，再调既有 `ChatGuardService`
  - B. 在 `ChatGuardService` 内部（`check_chat_input` / `start_output_guard`）插入平台层
  - C. 在 `common/chat/client.py` 的既有 guard 调用点前面再加一个 guard
- **选定**：**B**
- **原因**：`guardrail.py` 是 API 层的**装配**代码（把 guard 闭包注入 `ChatManager.dispatch_client`），检测语义写在装配层会让"平台优先短路"这条规则散在多个入口；C 则违反 C1（`common/` 不得 import domain，`common/chat/client.py` 恰恰是被注入方）。放在 `ChatGuardService` 内部，所有入口（助手 ws / 工作流 ws / 助手回调 / OpenAI 兼容接口）自动一致，与 F056 决策 2 选的收口层同一个理由。
- **何时该重新考虑**：若平台层将来需要独立于租户层开关（如"只对某些入口强制"），收口层内部的短路会变成入口分支条件。

### 决策 3：平台层与租户层的判定顺序

- **备选**：
  - A. 两份词表**合并**后一次检测，命中后按词条归属分别套话术（可能一条消息回两段话术）
  - B. **平台先检、命中即短路**；未命中再走租户层
  - C. 合并检测，命中后统一用平台话术（租户话术彻底失效）
- **选定**：**B**
- **原因**：A 的"一条消息回两段话术"在对话场景不可接受（用户看到两段割裂的提示）；C 会让租户配的自定义话术静默失效，租户侧无法理解。B 的语义最清晰，且命中日志能明确记来源（AC-17）。

**输入方向**（无歧义，直接短路）：平台自动机先扫，命中即写 `source=platform` 日志并返回平台话术、**不再跑租户层**（spec AC-12）。

**输出方向**（用户确认口径：**平台词优先，然后才是租户词**）：逐决策点优先，规则如下——

1. **每个分片先扫平台自动机**（带跨分片 hold-back tail）。平台命中 → 按**平台动作与平台话术**产出该片，本片不再按租户策略处理。
2. 平台未命中 → 再扫租户自动机，沿用 F056 原有逻辑。
3. **同一分片内双方都命中 → 平台获胜**：用平台动作 + 平台话术；若动作是 block，**截断点取两方命中位置中最早的那个**（而不是平台词自己的位置）——否则会把租户策略本应拦下的词原样发出去，等于租户策略在这条路径上失效。

**跨分片的技术边界（必须知情）**：截断是**不可逆**的——分片已 emit 就给用户看到了。因此当**租户动作是 block** 且租户词在本片命中时，必须立即截断，无法"再等等看后面会不会出现平台词"。要让平台词跨分片也绝对优先，唯一办法是**缓冲整段输出再检测**，那会摧毁流式（首字延迟不可接受，违背 K1 与 F056 决策 3 的全部理由）。

- 受影响范围仅限：**租户动作 = block** 且租户词先出现、平台词出现在其后分片 —— 此时用户看到的是租户话术。
- 若**双方动作都是 mask**，或租户是 mask：不截断、继续流，后续分片的平台命中仍能正常生效，**无此问题**。

- **已知边界 —— 用户已知情并明确接受（2026-09-17）**：上述跨分片情形**不修**。触发它需要同时满足「租户输出动作=截断」+「租户词先于平台词出现」，影响面窄；而两条修法都有不可接受的代价（缓冲整段 = 牺牲流式首字延迟；强制租户用 mask = 收窄 F056 已交付的配置能力）。**后人请勿把此行为当成 bug 修复** —— 它是流式 + 双层策略的固有产物，不是实现缺陷。
- **何时该重新考虑**：若客户后续坚持"平台词在输出中一旦出现就必须用平台话术，哪怕租户词在更早的分片" —— 只能改为整段缓冲后检测（牺牲流式），或对租户策略的输出动作强制为 mask（使其不产生不可逆截断）。这两条都要重新与用户确认。

### 决策 4：平台词库的变更生效与缓存

- **备选**：
  - A. 复用 F056 的 `_automaton_cache`（key 里带 `tenant_id`）— 零新缓存
  - B. 平台词条单独一个进程级缓存，key = 词条内容摘要 — 与租户缓存解耦
- **选定**：**B**
- **原因**：F056 的缓存 key 含 `tenant_id`，平台词库对所有租户是同一份，按租户缓存会存 N 份相同的自动机（万级词表 × N 租户的内存浪费）；且平台侧一次导入要失效的应该是**一份**而不是 N 份。独立的 `_platform_automaton_cache` + 内容摘要 key，导入后 `clear_cache()` 只清这一份。
- **进行中的流式不中断**（AC-15）：`OutputGuardState` 在 `start_output_guard` 时**持有 matcher 引用**，缓存失效不影响已持有的对象——该轮沿用旧判定，下一轮重建。这是既有机制的天然结果，无需额外代码。
- **何时该重新考虑**：词表规模大到单份自动机构建超过百毫秒时，需要改成增量构建或持久化自动机。

### 决策 5：热词与关联问题怎么送到前端

- **备选**：
  - A. 服务端在回答结束后通过同一条 ws **主动推送**一条新消息类型（需扩 `ChatResponse.type` 白名单 + 全部 5 个前端入口处理）
  - B. 前端在**收到回答结束事件后发起一次 HTTP 请求**，一次性取回热词 + 关联问题
- **选定**：**B**
- **原因**：A 要改 ws 协议（`api/v1/schemas.py:184` 的 `type` 是白名单校验）、要为 5 个展示面各写一遍 ws 分支、还要在服务端管理"这条连接属于哪条消息"的生命周期。B 的请求由前端在已知 `message_id` 与回答文本后发起，服务端无状态；"不落库、刷新即消失"（AC-35/AC-43）也天然成立——历史加载路径根本不发这个请求。F056 的坑 6 已经证明 ws 侧任何改动都会牵动历史回显，B 完全避开。
- **代价**：前端需要在每条回答结束时多发一次请求（含被拦截轮次——由前端判断该轮无回答则不请求）；一次请求同时要热词与关联问题（合并往返，见决策 7）。
- **何时该重新考虑**：若产品要求"用户没打开页面也要生成"或需要服务端预生成提速，才回到 A。

### 决策 5b：热词/关联问题的 `answer` 由前端回传还是服务端按 `message_id` 查库

- **备选**：
  - A. 服务端凭 `message_id` 查 `chatmessage` 取回答文本
  - B. 前端把 `answer` 文本回传（连同 `question` 与 `message_id`）
- **选定**：**B**
- **原因**：两条**都是实质理由**，不是便利性取舍——
  1. **守卫后文本**：流式场景下落库的答案可能被 `output_guard` 改写（block 截断 + 话术收尾 / mask 打码，`common/chat/client.py:392-401` 用 `full_text()` 重写最终答案）。前端持有的正是**用户实际看到的**那份文本；走 A 需要服务端精确复现"这个用户此刻看到的是哪个版本"，在流式中途刷新、`end_cover` 覆盖等情形下容易取错。
  2. **省一次查库与一次权限判定**：A 要先查 `chatmessage`，还要顺带确认该 message 属于当前用户/会话，多一个越权面。
- **代价**：请求体大小随回答长度增长（长回答可达数十 KB）；服务端不能信任该文本，只当作**输入素材**（生成失败即静默丢弃，不落库，无注入面）。
- **何时该重新考虑**：若请求体大小成为问题（如超长回答 + 弱网），改为服务端查库并接受上述版本歧义。

### 决策 6：评价事件的写入位置与差评原因回填

- **备选**：
  - A. 在 `/liked` 与 `/chat/comment` 两个接口各写一条事件，不做回填
  - B. 在 `/liked` 写事件（带 from/to 状态），`/chat/comment` **回填**到该次点踩的事件
  - C. 改前端把 comment 并入 `/liked` 一次提交
- **选定**：**B**
- **原因**：前端是"点踩先弹原因框、提交才落库"（`MessageFeedbackButtons.tsx`），一次点踩实际产生**两次**请求。A 会让一次评价拆成两行；C 要改前端与 `ChatInput` schema，且旧客户端不传 comment 时行为分叉。B 保持后端契约不变（两个接口签名都不动），代价只是回填时要在窗口内找"刚写的那条"。
- **回填的查找口径**：按 `(message_id, user_id, to_value=2)` 取**时间窗口内最新的一条 `comment` 为空**的事件。窗口取 10 分钟（覆盖用户思考+输入原因的时间）。**找不到就放弃回填**，不报错、不补写独立事件——原因本身已存在 `chatmessage.remark`（`chat_imp.py:comment_answer`），审计信息不丢。
- **何时该重新考虑**：若前端改成"原因与评价一次提交"（C），回填逻辑可整体删除。

### 决策 7：热词与关联问题共用一个端点还是两个

- **备选**：
  - A. 两个端点（`/suggestions/keywords` 与 `/suggestions/questions` 或复用 `/qa/auto_question`）
  - B. 一个端点，响应体 `{keywords: [], questions: []}`，两者各自可空
- **选定**：**B**
- **原因**：两者**同时**触发、**同时**需要、**同时**展示；两个端点会让前端并发两次请求、两次错误处理、两个加载态。合成一个端点后，服务端可以在一次调用里决定"这次要不要跑知识库检索"（未绑定知识库就跳过 questions 那半边，省一次检索 + 一次 LLM）。
- **模型槽位**：两者共用 `qa_similar_model`（用户确认：先按一个做，之后看效果）。注意该槽位在 `LLMService.get_knowledge_similar_llm` 里**未配置时返回 None**，而既有 `recommend_question` 拿到 None 会**抛 `KnowledgeSimilarError`**——F059 必须把它降级为"静默不展示"（AC-33/AC-42），不能沿用抛错路径。
- **何时该重新考虑**：若热词质量与关联问题质量需要不同的模型调优，拆成两个槽位（届时端点可保持不变，只是内部取不同 llm）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**强制词库（输入）**

用户 ws 发消息 → `ChatClient.handle_message`（`common/chat/client.py:97`）→ 调用注入的 `input_guard` → `api/services/guardrail.py:build_input_guard` → **`ChatGuardService.check_chat_input` 内部先跑平台层**（决策 2：检测语义收在 service，装配层不动）→ 平台词库命中则写日志(`source=platform`)并返回平台话术，**短路结束** → 未命中再走租户层（F056 原逻辑）→ 租户命中写日志(`source=tenant`)并返回租户话术 → 都未命中则放行，进入模型调用。

拦截返回走 `send_response("answer","end", blocked_reply)`（`client.py:99-103`）——**该帧不带 message_id 且不落 `chatmessage`**，这是前端识别"本轮被拦截"的唯一依据（见 §5 坑 12）。

**强制词库（输出）**

`build_output_guard` 的闭包在首个分片到达时 `start_output_guard` → **平台 matcher 与租户 matcher 并存于 `OutputGuardState`** → 每个分片先扫平台自动机（命中即按平台 action 截断/打码 + 平台话术收尾），未命中再扫租户自动机（F056 原逻辑）→ 流结束时 `flush_output` 冲掉 hold-back 缓冲。

**评价留痕**

前端 `MessageFeedbackButtons` → `likeChatApi(messageId, liked)` → `POST /api/v1/liked` → `ChatMessageService.like_response` → 更新 `chatmessage.liked` 与会话计数（既有）→ **新增：写一条 `message_feedback_event`（from/to）**（早期 return 之后）→ 用户填原因 → `POST /api/v1/chat/comment` → `comment_on_answer` → 写 `chatmessage.remark`（既有）→ **新增：回填该次点踩事件的 comment 字段**。

**回答区增强**

前端收到回答结束（**该轮 `message_id` 非空**——被拦截轮次无 ID，不发请求，见 §5 坑 12）→ `POST /api/v1/chat/suggestions`（body: `chat_id`、`message_id`、`question`、`answer`；`answer` 为前端持有的**守卫后**文本，见决策 5b）→ `knowledge/api/endpoints/answer_suggestion.py` → `AnswerSuggestionService`：
- **热词**：一次 LLM 调用从问答文本抽词（**不碰知识库**）
- **关联问题**：解析应用绑定的知识库（助手走 `AssistantLink.knowledge_id`，`database/models/assistant.py:75`）→ 取不到则跳过这半边 → `retrieve_knowledge_documents`（F055 入口，`knowledge_flow_retrieval.py:155`）→ 一次 LLM 调用凝练成问题
- 两半**并发**跑，各失败各为空数组 → 返回 `{keywords, questions}` → 前端挂在对应 `message_id` 下方

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `platform_sensitive_word.word` | str(255)，唯一 | 平台词条（单库，无分类/生效期） | 检测链路、词库管理页 |
| `platform_sensitive_word_policy` | 单行表 | `enabled` / `action`(block\|mask) / `auto_reply` | 检测链路、平台设置页 |
| `sensitive_word_hit_log.source` | str(16)，可空 | `tenant` / `platform`；历史行回填 `tenant` | 命中日志页、超管筛选 |
| `message_feedback_event` | 见 §4.3 | append-only 评价流水 | 评价明细页（租户 + 超管） |
| `POST /api/v1/chat/suggestions` 请求 | `{chat_id, message_id, question, answer}` | 回答结束后前端发起 | 客户端 5 个展示面 |
| 同响应 | `{keywords: string[], questions: string[]}` | 任一为空数组表示该半无内容 | 同上 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `common/dependencies/super_admin.py`（新增） | 超管专属鉴权依赖 `require_global_super_admin`：仅放行全局超管，fail-closed | **不得**改用它复用 `get_tenant_admin_user`（后者放行租户子管理员，见 §5 坑 20） |
| `sensitive_word/api/endpoints/platform_words.py`（新增） | 词库 CRUD + 导入预览/执行（全部走超管鉴权） | 不做解析（在 service） |
| `sensitive_word/api/endpoints/platform_policy.py`（新增） | 平台策略读写（超管鉴权）；`auto_reply` 为空时返回需求原文并标注 `is_default` | 不写租户策略 |
| `chat_session/api/endpoints/feedback_events.py`（新增） | 评价明细查询：租户侧（显式租户过滤）与超管侧（bypass + 可选租户筛选） | 不写事件（在 service） |
| `sensitive_word/domain/models/platform_word.py`（新增） | 平台词条表 + DAO（分页/搜索/增删/批量替换） | 不做导入文件解析 |
| `sensitive_word/domain/models/platform_policy.py`（新增） | 平台策略表（单行）+ DAO | 不读租户策略 |
| `sensitive_word/domain/services/platform_word_service.py`（新增） | 导入解析（分隔符/表头/去重）、差异预览、自动机构建与缓存 | 不做鉴权（在 endpoint 层） |
| `sensitive_word/domain/services/chat_guard.py`（扩展） | 平台层判定前置 + 短路 + 日志 source | 不改租户层既有分支语义 |
| `chat_session/domain/models/feedback_event.py`（新增） | 评价事件表 + DAO（写入 / 回填 / 查询） | 不写 `chatmessage` |
| `chat_session/domain/services/chat_message_service.py`（扩展） | 在 `like_response` 末尾写事件；`comment_on_answer` 回填 | 不改既有计数与日志逻辑 |
| `knowledge/domain/services/answer_suggestion_service.py`（新增） | 热词抽取 + 关联问题检索→凝练，合并返回 | 不落库、不写任何表 |
| `knowledge/api/endpoints/answer_suggestion.py`（新增） | `/chat/suggestions` 端点，自注册进 `knowledge` 模块 router | 不写业务逻辑（编排在 service） |
| `client/src/components/Chat/AnswerSuggestionBar.tsx`（新增） | 热词芯片 + 关联问题列表，点击填入输入框 | 不自动发送 |
| `client/src/components/Chat/AiDisclaimer.tsx`（新增） | 友情提醒行（i18n） | 无服务端配置 |

> **端点归属说明**：`/chat/suggestions` 的**服务实现**在 knowledge 模块（关联问题要用 `retrieve_knowledge_documents`，属于知识检索能力），端点**同模块自注册**（`knowledge/api/endpoints/` 下），由 `api/router.py:48` 的 `/api/v1` 前缀统管。
> 不放进 `chat_session` 模块——那会造成 `chat_session/api` → `knowledge/domain` 的跨模块调用，且 arch-guard RULE-5 禁止 API 层跨模块导入。
> 路径含 `chat` 只是语义命名（对话场景的追问建议），不代表归属 chat 模块。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `tenant_filter.py` 的 `before_flush` 会给**所有带 tenant_id 列的表**自动回填当前租户 | 平台全局行写进去变成某租户的私行，之后所有租户都读不到；排查时会以为"写了但没生效" | 平台两张表**不设 tenant_id 列**（决策 1） |
| 2 | 该表发现机制是按**列存在性**做的（`_discover_tenant_aware_tables`），不是按表名白名单 | 有人"为了统一"给平台表补一个 tenant_id 列，过滤会立刻静默生效、词库全空 | 同上；平台表加列需评审 |
| 2b | 反过来：**带 `tenant_id` 的新表若没进 `_TENANT_AWARE_MODEL_MODULES` 导入白名单，同样会静默失去过滤**——白名单（`tenant_filter.py:51+`）是硬编码的，且在启动时一次性 `_discover_tenant_aware_tables()` | 评价事件表的模块没被 import 到，`SQLModel.metadata` 里就没有它 → 既不被 `do_orm_execute` 过滤（**租户互相看到彼此评价**）也不被 `before_flush` 回填（`tenant_id` 写空）。v2.5 的 knowledgefile/flowversion/roleaccess 就是这么漏的，文件里的注释是那次事故的记录 | 评价事件表落地时同步登记进 `_TENANT_AWARE_MODEL_MODULES` |
| 3 | F056 的自动机缓存 key 含 `tenant_id`（`_build_cache_key`） | 平台词库若复用该缓存，会在 N 个租户下各存一份相同的万级自动机 | 平台侧独立缓存（决策 4） |
| 4 | `get_knowledge_similar_llm` 在**未配置模型时返回 None**，而 `recommend_question`（`knowledge_imp.py:712`）拿到 None 会**抛 `KnowledgeSimilarError`** | 直接复用会变成"没配模型就报错"，而 AC-33/AC-42 要求静默不展示 | 新增链路自行取值 + 判空短路，不复用 `recommend_question` 的抛错路径 |
| 5 | `recommend_question` 用 `LLMChain.predict` 是**同步阻塞**调用，而 `/chat/suggestions` 是 async 端点 | 直接 await 一个同步调用会**阻塞事件循环**，并发下整个服务卡住 | 走线程池（`run_in_executor`）或改用异步 LLM 客户端 |
| 6 | 既有 `recommend_question` 的 prompt 是「生成**语义相似**的问题」（输入 `What is the capital of France` → 输出 `Which city is the capital of France`） | 直接复用会产出**近义复述**而非"用户接下来想问什么"，产品效果与预期不符 | 关联问题另写 prompt（生成后续追问）；热词另写 prompt（抽取实体） |
| 7 | 前端点踩是**两次请求**（`/liked` 然后 `/chat/comment`），而 `/chat/comment` 只带 `message_id` + `comment`，**不带评价类型** | 回填时若不加 `to_value=2` + 时间窗口限定，会回填到更早的、或别人留下的差评事件上 | 回填查找口径见决策 6 |
| 8 | `like_response` 开头有 `if message.liked == data.liked: return` 的早期返回 | 事件写在它之前会产生大量"点了个已经点过的"空事件，且与 AC-27 冲突 | 事件写在早期 return **之后** |
| 9 | `ChatResponse.type` 是**白名单校验**（`api/v1/schemas.py:184`） | 想加 ws 消息类型推热词会直接校验失败 | 选 B 方案（HTTP 拉取），不碰 ws 协议（决策 5） |
| 10 | 前端 `AiMessageBubble` 的 `allowFeedback` 在分享页为 false（`AiChatMessages.tsx:292` 的 `!shareToken`） | 照搬该开关会连友情提醒一起关掉，而 AC-51 要求分享页**仍展示提醒** | 提醒独立于 `allowFeedback` 判断 |
| 11 | 响应式接口的租户过滤会命中**所有** SELECT，包括超管查询 | 超管查全量评价/日志需要显式跨租户口径，否则只看得到自己租户 | 沿用 `_check_is_global_super` + `bypass_tenant_filter()` 既有范式 |
| 13 | `check_output_chunk` 开头的 `if not state.enabled: return` 会在**平台层解析之前**返回 | 租户策略关闭时 `enabled=False` → **整个平台层被跳过**，功能在最需要它的场景（纯平台强制）下完全不工作。**平台层解析必须在 `enabled` 早退之前**，解析成功后它会把 `enabled` 置回 True | `_ensure_platform_resolved` 调用点移到早退之前（T010 实测发现） |
| 14 | 输出方向两层共用 `tail` 缓冲，但各自有独立扫描状态；`emit_len=0`（整块扣留、下轮重扫）时，扫描状态必须回退到**本轮扫描前**的状态 | 回退到"扫描后"状态会让扫描位置跳过即将被重扫的文本，命中位置越界——表现为漏判或错位打码。这是 F056 原代码没有的分支，重构时容易引入 | `_rewind_scan_state` 的 fallback 取 `prior_state` 而非 `state_after` |
| 15 | `api/services/guardrail.py:75` 在 `blocked` 时读的是 **`state.auto_reply`**（不是局部变量）来收尾回复 | 平台拦截若没把 `state.auto_reply` 指向平台话术，用户看到的将是**租户话术**——一个静默的行为错误，且 lint 只会提示"变量未使用" | 命中且 block 时同步 `state.auto_reply`（T010 实测发现） |
| 16 | `retrieve_knowledge_documents`（`knowledge_flow_retrieval.py:155`）是**同步函数**，不是协程 | 直接 `await` 会在**第一次真实提问**时抛 `TypeError`。单元测试若把该函数 stub 成 `async def` 则完全掩盖此问题 | 走 `asyncio.to_thread`；测试的 stub 必须保持**同步**（T014 实测发现） |
| 17 | `LLMService.get_knowledge_similar_llm` 返回**同步** chat model（`get_bisheng_llm_sync`），而 `/chat/suggestions` 是 async 端点 | 直接调用会阻塞事件循环，并发下拖垮整个服务 | 同样走 `asyncio.to_thread` + 超时 |
| 18 | 前端 `AiMessageBubble` **不带 `key`**，重新生成（regenerate）会复用同一个组件实例 | 用布尔的"已请求"ref 会在首个回答后**永久闩锁**，重新生成的回答永远拿不到建议 | ref 记录**是哪个 messageId 已请求**，而非"是否请求过" |
| 19 | client 侧 `ChatMessage.messageId` 是**字符串**，而建议接口要数字 | `message_id` 直接透传会被后端拒；且非数字 id 恰是"未落库"的信号（如被拦截轮次） | 前端 `Number(message.messageId)`，NaN/0 由服务端空输入分支兜住 |
| 20 | `require_global_super_admin` **必须 fail-closed**，且不能复用 `get_tenant_admin_user` | 后者会放行**当前租户的子管理员**；用在平台端点上等于把跨租户词库交给每个租户管理员（击穿 INV-10）。检查异常时若放行，一次 FGA 故障就是全量越权 | 独立依赖 + 异常即拒绝（T015） |

| 21 | 输出层有**两份**匹配列表，混用会泄露：`*_all` = 本缓冲区**全部**匹配（用于打码与 emit 边界），`*_matches` = **仅越过水位线**的（用于计数/日志）。被扣留在 tail 的词会在下一分片重扫时**再次命中**，但已过水位线 → 从过滤列表中消失 | 用过滤列表去打码，那个词就会**原样输出**（F059 双层重构引入的回归，F056 传的是未过滤列表）。已用 fuzz 验证：修复前 ~62/1200 分片组合泄露，修复后回到 F056 基线（0 真泄露） | `chat_guard.py`：`all_seen` 供 `_mask`/`_resolve_emit_len`/block 判定，`all_new` 仅供计数 |
| 22 | 一个分片可能**同时**命中平台层与租户层。日志若合并成一行并标 `source=platform` | 租户侧查询会把该行的 **全部**命中词抹掉，租户看不到**自己策略**命中的词（违反 AC-19），且 `hit_count` 重复计数 | 按层分别写日志：谁命中写谁那一行 |
| 23 | `LLMService.get_knowledge_similar_llm` 看着像 getter，实则**同步**做 DB 读取 + 模型客户端构造（含凭据解密） | 在 async 路径直接调用会阻塞事件循环数个往返；与坑 5/16 同类但更隐蔽，因为它不像"重操作" | `asyncio.to_thread` 包裹（T014 / code-review 发现） |
| 24 | 点踩的**评价与原因是两个并发请求**（前端同一 tick 发出、互不 await） | 原因请求可能先于事件行落地 → 回填找不到行 → **原因静默丢失**（恰是该功能要解决的场景）；若窗口内存在旧的空原因差评事件，还会**错填到那一行** | 回填带**有界重试**（3 次 / ~0.4s，best-effort 不抛错） |
| 25 | 无 BOM 的 UTF-16 文本会"成功"按 UTF-8 解码（隔字节为 NUL） | 导入出**永远匹配不上**的 NUL 垃圾词并报成功，11301 永不触发，词库被污染 | `_read_text` 检测 NUL → 抛 11301 |
| 26 | 建议服务解析模型输出时若"取第一个找到的 list" | 模型返回**两个 key**（很常见）时，关联问题那半会拿到**热词**列表、真问题被丢弃 | `_parse_list(key=...)` 显式指定，不靠探测 |

| 12 | 被拦截轮次（输入命中）**没有模型输出**，前端判断依据是**该轮结束帧不带 `message_id`**——拦截路径走 `send_response("answer","end", blocked_reply)`（`common/chat/client.py:99-103`），不落 `chatmessage`、无 ID | 若前端无条件请求 suggestions，会拿用户提问去抽词，**把命中词条回流给用户**（击穿 INV-10）。**且绝不能在拦截路径"顺手补一个 message_id"**——那正是打开泄露口的改动 | 前端：`message_id` 为空则不发请求；服务端：AC-37 二次拦截 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `/api/v1/platform-sensitive-words`（列表/搜索/新增/删除） | HTTP API，**鉴权 `_check_is_global_super`** | 平台词库管理页 |
| `/api/v1/platform-sensitive-words/import`（预览 + 执行） | HTTP API，**鉴权 `_check_is_global_super`** | 平台导入对话框 |
| `/api/v1/platform-sensitive-word-policy`（读/写） | HTTP API，**鉴权 `_check_is_global_super`** | 平台安全围栏页新增区块 |
| `/api/v1/chat/suggestions` | HTTP API（登录用户） | 客户端 5 个展示面 |
| `/api/v1/message-feedback-events` | HTTP API（租户 / 超管两种查询范围） | 评价明细页 |
| `PlatformSensitiveWordService.check_input / build_matcher` | 内部 Python API | `ChatGuardService` |

> **鉴权口径（K9）**：三个平台词库端点**一律** `Depends(_check_is_global_super 的依赖包装)`，**不得**用 `get_tenant_admin_user`——后者会放行当前租户的子管理员（`user_deps.py:78-101`）。命中日志的**租户侧**查询端点沿用 F056 既有的 `get_tenant_admin_user`（那是租户自己的数据），**超管侧**查询走全局超管依赖。

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `ChatGuardService` 的既有判定与日志写入 | 内部 Python API | F056 改签名/语义会静默坏掉本 feature 的平台层短路 |
| `retrieve_knowledge_documents`（`knowledge_flow_retrieval.py:155`） | 内部 Python API | 签名或默认行为变更会影响关联问题的检索口径 |
| `qa_similar_model` 槽位（`tenant_system_model_config`） | 配置契约 | 部署方未配置时功能不可用（须静默降级，见坑 4） |
| `chatmessage.liked` / `remark` 的语义（0/1/2） | 隐式数据契约 | `LikedType` 枚举改动会让 from/to 值失去含义 |
| `tenant_filter.py` 的表发现机制 + `_TENANT_AWARE_MODEL_MODULES` 白名单 | 框架行为 | 双向风险：平台表加 `tenant_id` 列 → 词库变空（坑 2）；评价事件表**漏登记白名单** → 失去租户过滤、跨租户可见（坑 2b） |
| `_check_is_global_super`（`utils/http_middleware.py:145`，带 Redis 缓存 `user:{id}:is_super`） | 内部 Python API | 平台端点鉴权的唯一口径；缓存失效时（如超管权限变更未清缓存）会有短暂窗口 |
| i18n 三语词条（友情提醒、页面文案、`api_errors` 域的 113 段） | 生成物契约 | 漏一种语言会被 `pnpm check-i18n` 拦下 |

---

## 7. 测试与可观测

- **单元**：导入解析边界（分隔符/表头/空/重复/超长词）、平台与租户两层短路顺序（含"同时命中"）、平台自动机缓存的失效与进行中流式不中断、评价事件 from/to 矩阵（含无变更不写）、回填窗口命中与未命中、热词/关联问题的空值与异常降级。
- **集成**：ws 输入命中平台词 → 不调模型 + 平台话术 + 日志 source=platform；租户词 → 租户话术 + source=tenant；OpenAI 兼容接口命中平台词 → 400 + 11201；租户查询命中日志时平台来源行的 `hits` 被抹除。
- **e2e**：见 `e2e-checklist.md`（含"平台词库内容不经租户界面泄露"的专项验证）。
- **手动验证**（本地环境 172.19.70.141：平台 :3001 / 客户端 :4001 / 后端 :7860；平台登录凭据沿用 F055 `design.md` §7 的既有记录，**不在此重复**。AC-06 的拒绝路径需两个账号：全局超管 + 一个**租户子管理员**）：
  - 平台以**超管**账号进词库管理页 → 导入一份词表（覆盖/追加各一次）→ 确认差异预览数字与实际一致
  - 以**租户管理员**账号访问同一入口 → 应被拒绝
  - 客户端发起命中平台词的提问 → 平台话术；租户管理员查命中日志 → 看到记录但无命中词
  - 点赞/取消/改踩 → 评价明细页出现对应条数与前后值；点踩填原因 → 原因回填到该条
  - 正常提问结束后 → 回答下方依次出现热词、关联问题、友情提醒；刷新页面 → 前两者消失、提醒仍在
- **关键日志**：平台词库导入（条数/模式/操作人）、导入失败（错误码 11301）、热词/关联问题生成失败（仅服务端 warn，不打扰用户）。

---

## 8. 后续改进 / 不打算做的事

- **不做**：词条的生效期/优先级/分类、导入历史与回滚、评价导出报表、热词的效果回收、关联问题的历史回看。
- **已知短板**：热词与关联问题各多一次 LLM 调用，未做并发合并；两者共用 `qa_similar_model`，无法分别调参（用户已确认先合一看效果）。
- **已知短板（已接受，不修）**：流式输出下，当租户输出动作=截断且租户词先于平台词出现时，该轮以租户话术收尾（平台词跨分片无法绝对优先）。用户 2026-09-17 明确接受，理由与修法代价见 §3 决策 3 —— **勿当 bug 修**。
- **重写触发条件**：热词与关联问题需要独立模型调优 → 拆双槽位；平台策略需要支持租户覆盖 → 两张表合并为带 `scope_type` 的单表（见决策 1 的重新考虑条件）。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-17 | 初版 | spec 评审通过后编写 |
| 2026-09-17 | §5 增坑 13–20（实现期实测发现）；§8 补已知短板 | T010/T014/T015/T023 实现与 /code-review 暴露 |
| 2026-09-17 | §5 增坑 21–26；§4.3 补 3 个新模块 | /code-review 两路独立审查（安全 + 逻辑边界）发现 |
