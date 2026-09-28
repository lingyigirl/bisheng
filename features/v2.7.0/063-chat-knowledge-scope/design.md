# Design: 对话知识范围默认口径（063-chat-knowledge-scope）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动
>
> 调整原则（详见 `docs/SDD-Guide.md` §3-§4）：
> - 实现变化 → **覆盖更新本文档**，只留"今天的状态"、不留旧快照；但每个决策保留"为什么 + 被否方案"和坑（护栏，见 §3/§5）
> - **偏差分级**：推翻已 ★ 确认的决策 → 停下与用户重新确认；纯实现细节 → 直接改 design，不必停
> - `tasks.md`「实际偏差记录」只留一行指针（如"T7 偏离 → 更新 design 决策3"），论证在本文档，不重复

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-23（实现已合入 `455e69c20`；同日经实现后 code-review 复审，§4.1 全量刷新）

> ✅ **已裁决（2026-09-22，需求方）：存量数据不做迁移，一律按全新口径处理** —— 见 §3 决策 8。
> 连带影响：spec 的 **AC-21** 原字面是「升级即按『全部』口径迁移」，已按此裁决**同步改写**（原字面会让「只选过知识空间」的用户升级后静默开始检索全公司知识库）。读旧版 spec 的人请注意这一处口径变更。

---

## 1. 目标与非目标

- **目标**：把日常问答输入区的知识范围从「逐库勾选」改为「组织知识库档位（全部 / 不使用）× 知识空间多选」的双态模型，让用户**不选任何东西就能问到全部有权限的组织知识库**，同时保留「只查某个知识空间」与「全部组织知识库 + 叠加知识空间」两种场景。
- **非目标**：
  - 不改组织知识库的归属与可见性规则（F057 / F062 的所有物，本特性只读复用其判定）。
  - 不改任务模式**执行态**（灵思）的知识选择与持久化链路（spec AC-25 的**修订后**范围）。任务模式**落地页**（`TaskModeChatInput`，复用日常输入区）**不在此列** —— 它跟随本特性口径，见决策 14（spec AC-25 修订时需求方裁决）。
  - 不改平台侧组织知识库配置入口自身的行为与字段（spec AC-23）。
  - 不新增表 / 字段 / 错误码 / FGA 元组（见 `release-contract.md` 已登记口径）。

---

## 2. 关键约束

全局架构铁律（DDD 分层 / 双 DB / 多租户 / 权限统一入口 / 错误码 / 前端 store 不直连 HTTP）遵循 `docs/constitution.md` C1–C7，本节不重抄。以下是本特性**特有**的硬约束：

1. **Recoil 已冻结**（`client/AGENTS.md`）：不得新增 atom / selector / `recoil` import（`no-restricted-imports` lint 强制）。档位状态只能落在既有的 `store.selectedOrgKbs` 值形态里，或走非 Recoil 的既有机制。
2. **`use_knowledge_base` 是对外入参，必须向后兼容**：旧客户端（不含本特性的构建）继续按「显式 id 列表」语义工作，其行为不得变化（契约 §6.2）。
3. **「全部组织知识库」的范围由既有可选集合定义，不由名字定义**：客户端取组织知识库列表的接口硬编码 `type=0`（`api/linsight.ts:128`），即 `KnowledgeTypeEnum.NORMAL`；`QA(1)` / `PRIVATE(2)` / `SPACE(3)` 都不属于「组织知识库」这个用户可见范畴。
4. **检索侧的成本随库数线性增长，且没有库数上限**：`queryChunksFromDB` 对每个组织知识库**逐个** `get_multi_knowledge_vectorstore`（串行 for 循环）。文档量有 `max_total_docs=100` 兜底，**库数没有**。这是「默认全部」引入的唯一真正新的成本项（决策 11）。
5. **INV-21**：新增的知识库读取路径（「全部」的展开就是一条）必须接入与其它入口**同一份**可见集合判定，不得按入口分叉。展开的候选集必须是可见集合的**超集**，最终由同一份逐库判定收口。
6. **求值依赖进程级部门映射缓存**：F062 的机构域判定走 `department_org_domain.get_department_mapping()`（进程级 TTL 缓存）；3 万部门级部署依赖该缓存，缓存不可用时退化为逐级 `parent_id` 回溯（慢但正确）。
7. **输入区被多入口复用**：日常问答页、订阅模块 AI 助手面板、独立对话页、分享页共用同一份 `selectedOrgKbs` 状态（spec AC-24）；但**只有日常问答页的 `ChatView` 负责持久化**（订阅面板不保存，spec AC-20）。

---

## 3. 方案对比与选定

### 决策 1：「全部」在请求链路上的表达方式

- **备选**：
  - A. **客户端解析三态 → 新增可选布尔字段** `use_knowledge_base.organization_knowledge_all`，为 `true` 时服务端展开为全部有权限的组织知识库；为 `false`/缺省时维持既有的显式 id 列表语义。
  - B. **空数组语义反转**：`organization_knowledge_ids: []` 由「一个都不查」改为「全部」。
  - C. **哨兵 id**：用 `[-1]` 之类的保留值表示「全部」。
  - D. **客户端枚举**：前端把全部 id 拉全后照旧发显式列表。
- **选定**：**A**
- **原因**：
  - B 是**语义反转**，会静默改变所有既有调用方：任务模式交接（`_to_linsight_submit`，`chat_service.py:2357`）、旧客户端、集成测试里 `UseKnowledgeBaseParam(organization_knowledge_ids=[])` 的既有断言（`test/workstation/test_query_chunks_visibility.py`、`test/linsight/test_linsight_knowledge.py`、`test/workstation/test_unified_chat_entry.py`）全部会换义。违反 §2 约束 2。
  - C 用魔法值穿透一个强类型字段，且与 `>50` 校验器的存在语义冲突（校验器无从判断 `-1` 不是「一个库」）。
  - D 三重堵死：`chat_schema.py:68` 有 `>50` 硬拒绝；`GET /api/v1/knowledge` 是**游标分页且移除了 `total`**（`knowledge.py:431-436`），没有便宜的「拉全」；spec AC-14 明确禁止展示清单，为拉全而翻页本身就是被禁的行为。
  - A 是**加性**变更：新字段缺省即旧语义，无需版本协商，也不需要动 `>50` 校验器（决策 5）。
- **何时该重新考虑**：若将来「全部」之外还需要「全部 ＋ 排除某几个」——那会引入负向授权，**已被 INV-20 / spec AC-12 禁止**，届时不是改这里，而是回到 spec 重新评审。

### 决策 2：「全部」的服务端展开点

- **备选**：
  - A. 在 **`_resolve_user_kb_selection`**（`chat_service.py:970`）展开 —— 它是 `UseKnowledgeBaseParam` → 具体知识库元数据的**唯一漏斗**。
  - B. 在 `queryChunksFromDB`（`workstation_service.py:1089`）展开。
  - C. 新建一个独立的「可见组织知识库枚举器」服务，各处调用。
- **选定**：**A**
- **原因**：
  - `_resolve_user_kb_selection` 的输出同时喂给两个下游：`search_knowledge_bases` 工具的 `kb_id_whitelist`（`chat_service.py:645`）与用户提示词里的 `<selected_knowledge_bases>` 区块。**只在 B 展开会漏掉提示词那一半** —— 模型会看不到「有哪些库可查」，也不会被告知库名与标签。
  - C 是超前设计：目前只有这一个漏斗需要展开；枚举逻辑本身只有「一条候选查询 + 一次批量判定」，新建一个独立的枚举器**服务类**只会多一层需要维护的间接。
    **与决策 3 的落点区分**（避免读成自相矛盾）：这里拒绝的是「新建一个独立服务类当展开点」；决策 3 要求的是「候选查询本身必须落在 knowledge 域的 DAO / 域服务，不能写进 workstation 域的 `chat_service.py`」—— 那是 C1 的**分层**要求，不是新增一层抽象。展开的编排（判断要不要展开、与知识空间合并、产出 `{id,name,…}`）仍然在 `_resolve_user_kb_selection` 里。
- **何时该重新考虑**：出现**第二个**需要「全部」语义的消费方（例：知识空间聊天入口若也要接入），再把它抽成共享函数。
- **⏩ 该条件已触发（2026-09-22，拆解期）**：第二个消费方就是 **`_to_linsight_submit`**（`chat_service.py:2287` / `:2357`）—— 它不走本漏斗、直接读 id 列表（坑 8），而灵思任务存储记的是提交期的**具体 id**，故「全部」必须在该处也展开（决策 10）。处置：抽成 `chat_service.py` 内的**同模块私有函数** `_expand_all_org_knowledge`，**三处**调用（① 日常漏斗 `chat_service.py:1043`、② 日常→灵思交接 `_resolve_submit_org_ids`、③ 灵思落地页 `LinsightWorkbenchImpl.submit_user_question`）；**不新建服务类**（本决策已否决该形态）。理由：几处展开若各写一遍，「全部」就有多个口径。

### 决策 3：「全部」的候选集与收口判定（INV-21 合规的实现方式）

- **备选**：
  - A. **超集候选 + 同一份逐库判定**：候选 = 当前租户内全部 `type=0` 的知识库 id（**依赖 C3 的租户自动注入**，绝不手写 `WHERE tenant_id = X` —— C3 明文禁止），再用 `KnowledgePermissionService.filter_knowledge_ids_by_permission_async(login_user, candidates, "use_kb")` 收口。
  - B. **复用平台列表的枚举**：调 `KnowledgeService._scan_visible_knowledge`。
  - C. **精确枚举**：自己实现「机构域前缀匹配」把判定重写一遍。
- **选定**：**A**
- **原因**：
  - A 与 **F062 决策 7「候选集用超集，不做精确枚举」**同构：判定式只有一份（`KnowledgePermissionService` → `FineGrainedPermissionService` → 含 `_implicit_department_library_level`），候选集偏大不会改变结果集，只会多做几次判定。
  - **为什么它与平台列表的结果必然一致**（spec AC-10 的论证）：平台列表对非管理员走 `accessible_ids(ReBAC can_read) ∪ creator_ids ∪ department_chain`，再逐行按 `use_kb` 收口 —— 它的候选集被**有意**设计成许可集的超集（F057 的注释原话：部门归属的行「would otherwise be absent from the ReBAC candidate set and never reach the per-row permission check」）。本特性的候选集（租户内全部 `type=0`）严格更大，仍是超集；两者对同一个逐库判定求交，结果集**相同**。
  - B 是分页扫描器（`page_size`、`cursor`、`name` 过滤都是为列表 UI 服务的），拿来枚举全量需要循环翻页，比一条候选查询更重，且会把列表的分页参数语义泄露进对话链路。
  - C 会把可见性判定复制成第二份实现 —— 正是 INV-21 明文要防的事。
- **落点（C1 分层要求）**：候选查询**不得**写在 `chat_service.py` 里 —— C1 明文「Never write ORM queries in Service」。**并且 C1 同一条紧接着明文「never add new DAO entry points for new features」—— 本特性不得新增 DAO 方法**（F062 按同一口径办理：复用既有 `DepartmentDao.aget_active_by_tenant`，见其 tasks.md「本 feature 特殊口径」）。故落点为**两处既有入口，本特性零新增**：
  - **候选**：`KnowledgeDao.aget_all_knowledge(knowledge_type=KnowledgeTypeEnum.NORMAL)`（既有，`knowledge.py:709`；`page=0, limit=0` ⇒ 不加 limit/offset，返回全量）。**只传类型，不传 `filter_department_ids`** —— 后者是平台列表的**浏览视图**收窄（`llm.py:1473` 同款用法），带上会把候选裁成「列表页当前视图」而不是超集。其 `select(Knowledge)` 是 SELECT ⇒ C3 的 `do_orm_execute` 自动注入租户条件（**不手写 `tenant_id`**；注意 `tenant_filter.py` 只拦 SELECT，这是本路径成立的前提）。
  - **收口**：`KnowledgePermissionService().filter_knowledge_ids_by_permission_async(login_user, candidate_ids, "use_kb")`（既有，`knowledge_permission_service.py:259`）—— 与 `workstation_service.queryChunksFromDB:1116`、`knowledge_department_service.py:174` 本就是同一条调用。**管理员跳过这一步**（`queryChunksFromDB` 的 `if org_kb_ids and not login_user.is_admin()`）：检索侧对管理员**不做** `use_kb` 收口，展开侧必须同口径，否则「全部」与检索路径不一致。
  - **编排**：`_resolve_user_kb_selection`（workstation 域）依次调上面两个既有入口。跨模块 **service → service** 调用是允许的：RULE-5 只禁止 **API 层**跨模块互相导入。
  - **代价（知情项）**：`aget_all_knowledge` 返回**整行 ORM 对象**而非 id，取值要读一遍 `.id`。当前规模可接受；若将来库数上万，正确做法也**不是**加 DAO 方法（C1 禁止），而是在既有方法上补投影参数 —— 那要动共享签名（它有 3 个调用方），属另案。
  - **为什么不照抄 F057 的 `department_stats`**：它确实在 domain service 里直接 `select(...)`（`knowledge_department_service.py:152-158`），但那是**既有反例**，不是先例，不能拿来给本特性开例。况且把收口放进 knowledge 域还多一个理由：INV-21 要求的「同一份可见集合判定」需要一个**可被其它入口复用**的家；写在 workstation 域，它就变成了第二份实现。
- **何时该重新考虑**：若 `filter_knowledge_ids_by_permission_async` 的批量判定被替换/改签名（生产代码共 3 个调用方，见 §6.2），或 ReBAC 的「允许集合」改成需要正向枚举（deny 语义的引入会推翻整个模型，而那是 INV-20 禁止的）。

### 决策 4：「全部」的范围 = `type=0`

- **备选**：
  - A. 仅 `KnowledgeTypeEnum.NORMAL (0)` —— 与客户端「组织知识库」列表（`type=0` 硬编码）以及平台列表的默认 `type` 完全一致。
  - B. `NORMAL (0) + QA (1)` —— 照 F062 决策 9「只作用于组织知识库（type=0/1）」的说法。
- **选定**：**A**
- **原因**：
  - 「全部组织知识库」这个短语里的**全部**修饰的是「组织知识库」，而组织知识库**作为一个用户可见范畴**在其唯一入口上就是 `type=0`。取 B 会让「默认全选」把用户从未在候选清单里见过、也从未勾选过的 QA 知识库拉进检索 —— 那是**扩权**，不是「全选」。
  - F062 决策 9 的 `type=0/1` 描述的是**可见性规则的适用面**（哪些类型的对象受机构域规则约束），不是「组织知识库」的枚举口径；两者不是同一个集合，不能互相代入。
- **何时该重新考虑**：若产品侧明确「QA 知识库也应默认纳入对话检索」，那是**改 spec AC-03 的范围**，不是改这里的实现。

### 决策 5：`>50` 校验器的处置

- **备选**：
  - A. **原样保留**，仅对显式 id 列表生效；「全部」路径不经过它。
  - B. 把上限提高（例如 500）。
  - C. 删掉校验器。
- **选定**：**A**
- **原因**：
  - spec AC-09 要求的是「数量超过 50 时照常全量」，而不是「提高上限」：`organization_knowledge_all` 路径根本不发列表，`>50` 自然不可能触发。保留校验器同时守住了旧客户端与 `knowledge_space_ids` 的既有边界。
  - B 只是把一个任意阈值换成另一个任意阈值，没有解决「用户有 300 个库」的场景，反而放宽了显式列表这条旧路径的约束面。
  - C 是破坏性改动：删掉校验器等于承认 `organization_knowledge_ids` 可以任意长，旧客户端的超大 payload 会直接压到下游检索层（§2 约束 4）。
- **何时该重新考虑**：显式 id 列表这条旧路径被彻底下线时（本特性之后，前端只剩两档；但**旧客户端与任务模式仍在用**，因此本期不删）。

### 决策 6：前端档位的承载方式（Recoil 冻结约束下）

- **备选**：
  - A. **复用既有的 `store.selectedOrgKbs` 原子**，在其值数组里放一个**合成标记项** `{ id: '__org_scope__', name: '', type: 'org', orgScope: 'all' | 'none' }`；标记项**不存在** = 「未显式设定」。
  - B. 新增一个 Recoil atom。
  - C. 档位另存一个 localStorage key（`bs:{uid}:orgKbScope`），用仓里已有的 `useLocalStorage`（`hooks/useLocalStorage.tsx`，自带 `storage` 事件同步）承接，不碰 Recoil。
- **选定**：**A**
- **原因**：
  - B 被 `client/AGENTS.md` 明文禁止（Recoil 冻结，lint 强制）。
  - C 看起来干净，但要额外解决两件事，而 A 都是**免费**的：① 订阅面板与日常问答页**同标签页内**的实时同步（`useLocalStorage` 的同步靠 `window` 的 `storage` 事件，同标签页不触发，得自己补一套发布订阅）；② spec AC-20 要求的**非对称持久化**（面板改动只在内存生效、刷新后回到日常页保存的值）—— 用 C 就得给 hook 加一个「写内存不写盘」的变体。A 直接继承现有结构：面板 `setSelectedOrgKbs` 改内存、只有 `ChatView` 的持久化 effect 写盘，AC-20 的行为**本来就成立**。
  - 三态之所以用「标记项存在性 + `orgScope` 取值」而不是「组织项有/无」：**「未显式设定」与「显式不使用」必须可分**（spec AC-02 兜底 vs AC-06 显式），而两者在「有没有组织项」这一维度上是同一个样子。
- **何时该重新考虑**：Recoil 冻结解除（Jotai 迁移落地）时，这一层应当整体重写为独立状态；届时不保留兼容层。

### 决策 7：三态与兜底口径的解析位置

- **备选**：
  - A. **客户端解析**：客户端知道自己选了哪些知识空间，据 spec AC-02 算出「全部 / 不使用」，只把**确定结果**发给服务端。
  - B. 服务端解析：把三态原样上报，由服务端套用兜底规则。
- **选定**：**A**
- **原因**：
  - 兜底规则的两个输入（档位是否显式、有没有选空间）**都在客户端**；上报三态等于让服务端为一件它无法独立判断的事承担状态机职责。
  - 既有实现已经是客户端派生：`useAiChat.ts:272-278` 里 `orgKbDisabled ? [] : ...` 就是这个模式，A 与之一致。
  - 服务端因此只需理解两种确定输入（`all=true` 或显式列表），契约面最小。
- **何时该重新考虑**：若出现**非前端**的调用方需要「默认全部」语义（例：开放 RPC `/api/v2` 的第三方集成），届时那侧要自己实现兜底，或把兜底下沉为服务端的第三种输入形态。

### 决策 8：存量数据不做迁移 —— 遗留记录一律等同「未显式设定」

- **备选**：
  - A（选定）. **不写任何迁移逻辑**：升级后直接套用新模型。存档里**认得**的形态只有两种 —— 知识空间项（`type:'space'`）与档位标记（`__org_scope__`）；**其余一切，含旧的逐项 `type:'org'` 项，一律丢弃**。旧存档里没有标记（旧实现从不产出它）⇒ 就是「未显式设定」⇒ 走决策 7 的兜底口径。
  - B. 以「旧存档里有没有组织项」为信号，把选过组织知识库的用户归一为显式「全部」。
  - C. 写一次性迁移标记 / 迁到新 key，升级后首次判定执行一次。
- **选定**：**A**（需求方 2026-09-22 裁决：**按全新口径处理，不引入迁移**）
- **原因**：
  - A 是唯一**没有迁移分支**的形态：没有版本号、没有一次性标记、没有「旧数据 → 新语义」映射表、没有「升级首次进入」这个特殊时刻。
  - B / C 都是「考虑迁移」的产物，且它们赖以判断的输入**本身不可靠**：旧实现允许用户显式清空到 `[]`（`ChatView.tsx:107-111` 把「任意数组（含空数组）」都当作用户选择），所以「他到底有没有在用组织知识库」在旧数据上**不可区分** —— 为一个分不清的信号建一套迁移规则，是在给不确定性编码。
  - 兜底口径本身就是为「没有显式设定」设计的（决策 7），旧存档天然落在这个语义里，不需要额外解释。
- **已知副作用（必须知情）**：升级前「勾了组织知识库 **且** 选了知识空间」的用户，升级后会落到「不使用组织知识库 + 原知识空间」——因为存档里没有标记 ⇒ 未显式设定 ⇒ 有空间即不使用。相对升级前，这**收窄**了他们的检索范围；相对 spec AC-21 原字面（迁移为全部），这**不替用户扩权**。这个收窄是「不做迁移」的必然结果，取舍是：**判断不了他当初的意图，就不替他扩张可见范围**。用户在输入区一键切到「全部」即可恢复。
- **为什么仍要丢弃旧逐项组织项**（这不是迁移，是正确性的下限）：新模型不再产出这种形态；若不丢弃，它们会继续出现在附件条上、并被 payload 当作显式 id 发出去 —— 等于把本特性要移除的「逐项勾选」从磁盘上**复活**。丢弃后由既有的持久化 effect 自然收敛到新形态，**不额外写一次盘、不加迁移时机**。
- **何时该重新考虑**：若出现「不得丢弃用户显式选择」的合规要求（例：审计要求可追溯用户历史授权范围），才需要 C 那样带版本号与留痕的迁移。届时那是**新增需求**，不是在 A 上打补丁。

### 决策 9：附件条的摘要卡与「移除即不使用」

- **备选**：
  - A. 用**一张**合成卡片代表「全部组织知识库」，复用 `KbCard`；移除它 = 显式置「不使用」（spec AC-15 / AC-16）。
  - B. 卡片刻上库数（「全部组织知识库（37）」）。
  - C. 沿用逐库卡片。
- **选定**：**A**
- **原因**：
  - C 直接违反 spec AC-14 / AC-17（卡片数不得随库总数增长），也是本次要消灭的展示过载本身。
  - B 需要一个**库数**，而列表接口已移除 `total`（游标分页），拿准确数量必须翻页到底 —— 为一个角标数字付全量遍历的代价不成立。AC-15 只需要判断「数量是否为 0」，用小页探测即可（决策 12 / §5 坑 6）。
  - A 的移除语义要对齐既有的 `AiChatInput.tsx:378` 处理器 —— 那里是 `filter(i => i.id !== kb.id)`，对合成标记项**不适用**，必须走单独的「置 `orgScope:'none'`」分支（§5 坑 4）。
- **何时该重新考虑**：产品要求卡片显示库数时，先解决「便宜的计数」问题（例如新增一个只读计数端点），不要用翻页硬凑。

### 决策 10：任务模式（灵思）交接的处置

- **备选**：
  - A. `_to_linsight_submit`（`chat_service.py:2357`）在 `organization_knowledge_all` 为真时，**复用决策 2 的展开结果**，把「全部」落成**具体 id 列表**后再交给灵思。
  - B. 让灵思侧也理解「全部」（改 `LinsightQuestionSubmitSchema`、`linsight_session_version` 的存储语义与任务内核）。
  - C. 什么都不做 —— 交接时「全部」退化成空列表。
- **选定**：**A**
- **原因**：
  - spec AC-25 要求任务模式的对外行为**保持本版本之前的状态**；灵思的任务表存的是**具体 id**（`linsight_session_version.organization_knowledge_ids`，注释原话：these are the SPECIFIC ids the user chose），B 会改动任务模式的状态模型与内核解析，正是 AC-25 划出去的范围。
  - C 是**静默行为变化**：用户在「全部」档位下切入任务模式，任务会一个组织知识库都不查，且没有任何提示。
  - A 让任务模式看到的是**用户此刻看到的同一批库**（同一份可见集合判定），语义上等价于「用户升级前手动全选后切任务模式」。
- **何时该重新考虑**：任务模式被要求支持「动态的、随时间变化的全部」时（即任务执行期而非提交期求值），才需要 B 那样的模型改造。

### 决策 11：「全部」的检索放大 —— 不收敛、但砍掉一处 N+1

- **备选**：
  - A. **不封顶、不抽样**（spec AC-09 明文禁止因数量静默收窄），但**砍掉「全部」路径上可避免的放大**：展开时跳过逐库标签聚合。
  - B. 对「全部」设一个内部上限（例如只检索前 N 个库）。
  - C. 原样不动，连标签聚合也照做。
- **选定**：**A**
- **原因**：
  - B 违反 spec AC-09（「不得因数量超过该值而拒绝请求、返回错误或**静默收窄范围**」），且用户无从察觉检索是不完整的 —— 比慢更糟。
  - C 的代价是实打实的 N+1：`_resolve_user_kb_selection` 对**每个**知识库调一次 `KnowledgeFileDao.aget_file_by_filters(page_size=500)` 再查一次标签（`chat_service.py:1006-1041`）。显式选 5 个库时是 10 次查询，落到「全部」就是 2N 次 —— 300 个库 600 次查询，只为给提示词补一段「可选标签」。
  - A 的取舍是明确的：**标签只影响模型调用 `search_knowledge_bases` 时能否用 tag 过滤**，属于提示词增强而非功能正确性；「全部」路径跳过它，功能等价、成本从 O(N) 降回 O(1)。显式路径（旧客户端 / 任务模式）**保持原样**。
  - 检索侧的逐库向量库初始化（§2 约束 4）本期**不动**：它属于既有实现的行为，改动会波及同一函数的所有消费方（F029 阶段、知识空间桶），超出本特性范围。代价在此**显式记录**，进 §8 后续项。
- **何时该重新考虑**：真实租户出现「百库 + 每轮问答明显变慢」的报障时，优先做两项：① 把 `get_multi_knowledge_vectorstore` 的逐库循环改成一次批量调用；② 给检索库数引入**用户可见**的收敛手段（而不是静默截断）。届时需要新的 spec 决策。

### 决策 12：组织知识库「是否存在」的探测

- **备选**：
  - A. 复用既有的 `useGetOrgToolList`（`hooks/queries/tools/queries.ts:73`），传 `page_size: 1`，只判断**有没有**库（spec AC-15 的显示条件）。
  - B. 在输入区挂载时翻页拉全量以取得真实库数。
  - C. 不做探测，`档位=全部` 时无条件显示摘要卡。
- **选定**：**A**
- **原因**：
  - C 会在「用户一个组织知识库都没有」时显示一张骗人的卡片 —— spec AC-15 明确排除数量为 0 的情况。
  - B 就是为了一个布尔判断付全量遍历；且列表接口没有 `total`（游标分页），拉全的代价随租户规模线性增长。
  - A 几乎零成本：`ChatKnowledge` 当前**已经**无条件调用了 `useGetOrgToolList`（`ChatKnowledge.tsx:153`，`PAGE_SIZE = 20`），也就是这个请求本来就发生在每次输入区挂载时；本特性把它降到 `page_size: 1`，反而更省。
- **实现落点修订（T-13 落地时发现）**：选定的仍是 A，但**调用方不是 `ChatKnowledge`** —— 逐项清单删掉后，两档「全部 / 不使用」**不需要库数**，把它留在那里就是一个**没有消费方的请求**。AC-15 的显示条件（档位 = 全部 **且** 库数 > 0）是附件条那张卡片的属性，故探测随卡片走：**T-15 在 `AttachmentBar` 与其挂载方 `AiChatInput` 各取一次**（后者要拿它决定欢迎语/附件条是否出现，即 `AiChatInput.tsx:330` 的 `hasSelectionTags`）。两处必须用**同一参数** `{ page: 1, page_size: 1 }`（不传 `name` / `sort_by` / `preferred_ids`），才能落进同一条缓存 —— 否则会在每次输入区挂载时发两个请求，比 A 的「几乎零成本」更差。**缓存键必须含 `page_size`**（code-review H1）：key 为 `['OrgTools', page, page_size, name, preferred_ids]`，最初漏了 `page_size`，而全应用共用一个 `QueryClient`，于是探测（`page_size:1`）与灵思组织库选择器（`page_size:50`）会塌成同一条缓存 —— 谁先挂载谁决定另一侧渲染什么（探测会把选择器截成一行）。见坑 17。
- **何时该重新考虑**：需要展示真实库数时（见决策 9）。

### 决策 13：组织知识库功能被关闭时的档位处置（spec AC-18）

- **备选**：
  - A. **归一与开关解耦**：功能开关只决定「入口是否显示」与「本轮是否下发组织知识库」，**不动**状态层里的档位标记；关闭期间标记原样留在原子与 localStorage，重新开启即恢复求值。
  - B. 关闭时把标记从状态里剥掉（沿用既有实现的形状），重新开启后用户回到「未显式设定」。
  - C. 关闭时把标记改写为 `orgScope:'none'`。
- **选定**：**A**
- **原因**：
  - **既有实现会销毁用户的显式选择**：`ChatView.tsx:116-120` 在功能关闭时执行 `saved.filter(item => item?.type !== 'org')`，而档位标记的 `type` 就是 `'org'`，会被一并剥掉；紧接着持久化 effect（`ChatView.tsx:167`）把剥掉后的数组写回 localStorage。用户显式设过的档位**永久丢失**，而 spec 边界情况明确要求「功能重新开启后该档位恢复求值」。
  - C 把「管理员临时关掉功能」偷换成「用户主动选择不使用」，重新开启后同样回不到原档位 —— 与 B 同病。
  - A 的最小改动是：水合时的过滤只针对**旧的逐项组织项**（带数字 id 的那些），保留标记项；`orgKbDisabled` 的作用点收敛到「不下发」与「不渲染入口」两处。
- **实现要点**：入口隐藏**已经存在**，无需新增 —— `ChatKnowledge.tsx:439 / 498 / 520` 三处都已判 `config?.knowledgeBase?.enabled !== false`。payload 侧把 `useAiChat` 现有的 `orgKbDisabled ? [] : ...` 改为「恒为不使用」（不发 `organization_knowledge_all`、`organization_knowledge_ids` 为空）。
- **何时该重新考虑**：若产品要求「关闭功能同时清空用户档位」（即把管理员开关当作对用户状态的一次性重置），那就是 B / C，需要一次显式的产品决策，并且必须向用户说明这次重置。

### 决策 14：任务模式**落地页**跟随新口径（spec AC-25 修订的由来）

- **发现（T-17 消费者审计）**：spec AC-25 的前提是「任务模式的知识选择是独立实现」，该前提**只对执行态成立**。
  - 执行态 `TaskModeInput` + `KnowledgeSpaceSelect`（`ExecutionFlow.tsx:269` 挂载）确实自带逐库选择器，是独立实现 —— **本次不动**。
  - **落地页不成立**：`TaskModeChatInput`（`Sop/index.tsx:56` 挂载）把日常 `AiChatInput` **整个复用**（`TaskModeChatInput.tsx:155`），因此必然继承两档入口与附件条摘要卡；要豁免就得**分叉组件**（复制一份输入区），与 F035 Track H「落地页 = 日常输入区 + 一个 taskMode 标志」的既定目标冲突。
  - 落地页还把知识选择写进**与执行态同一个 `taskModeContextState` 原子**（`:182`），只靠 key 区分（落地页 `'new'`；执行态为会话 id，见坑 15）。
  - 原判定在落地页是**错的**：`handleSend` 用 `context.knowledge.some(k => k.type === 'org')` 与 `context.knowledge.length > 0` 推导 `checked`（→ `org_knowledge_enabled`）与 `useKnowledgeBase`；档位标记的 `type` 也是 `'org'`（坑 13），「不使用」档下标记仍在数组里，两个判定都会报「用了组织知识库」。
- **备选**：
  - A. **落地页跟随新口径**：执行态维持原样，落地页的 `checked` / `useKnowledgeBase` 改由档位推导，同步修订 spec AC-25（把保护范围限定到执行态）。
  - B. 落地页保留逐项清单（把 `ChatKnowledge` 的旧实现留给落地页）—— 等于给同一个组件维护两套知识入口。
  - C. 只换 UI 不换判定（保留 `type === 'org'` / `length > 0`）。
- **选定**：**A**（需求方裁决 2026-09-22）
- **原因**：
  - C 不是「保持原状」而是**错误上报**：用户明确关掉组织知识库，「不使用」档下提交却告诉后端 `org_knowledge_enabled=true`。
  - 落地页的逐库 id **从来没有被传出去**：`convertTools`（`useLinsightManager.tsx:389`）只读 `checked` 布尔，提交 payload 里只有 `org_knowledge_enabled` / `personal_knowledge_enabled`。也就是说落地页的逐项选择**本就只以「有没有」的形式生效**，对齐两档不丢失任何已生效的能力。
  - **但这句只证了「不丢失」，没证「能生效」**（code-review H1' 复核）：`checked` 映射出的 `org_knowledge_enabled` 恰恰是任务执行期**不读**的字段 —— `task_exec._resolve_user_knowledge_bases` 只解析 `organization_knowledge_ids` / `knowledge_space_ids`（坑 16）。若只按本决策原样实现，落地页的档位与「全部组织知识库」摘要卡**纯属显示**：落地页创建的会话在其整个生命周期内不检索任何组织知识库，与下一条记录的对外行为变化**恰好相反**。兑现该变化需要一个**新增提交字段**（见「实现要点」）。
  - 对外行为变化只有一处，且已由需求方确认：**落地页首次提问的默认行为由「不检索组织知识库」变为「检索全部组织知识库」** —— 与 AC-01/AC-02 的默认口径一致。**兑现方式**：落地页无法枚举「全部」（它连库数都不知道），故提交 `organization_knowledge_all=true`（新增字段），由服务端在提交时展开为具体 id 后落库。
- **实现要点**：
  - 前端：`isOrgScopeAll(items, enabled)`（`utils/knowledgeScope.ts`）是唯一判定入口，同时喂 `checked`、`useKnowledgeBase` 与 `SubmissionState.organizationKnowledgeAll`（**同源同值**，单测第 4 条按状态表逐项断言）；`useLinsightManager` 把它直译为 payload 的 `organization_knowledge_all`（**显式 `false` 而非省略**，让「另两个生产者没设这个字段」读作「不展开」而不是「字段忘了」）。
  - 后端：展开发生在 `LinsightWorkbenchImpl.submit_user_question`（该路径的**唯一写入者**，且手上正好有 `login_user`），调用与日常漏斗**同一个** `_expand_all_org_knowledge`（函数内延迟 import，避免 linsight→workstation 模块级环）。落库的仍是**具体 id 列表** —— 灵思存储的唯一形态（决策 10）。展开失败退化为「本轮不查组织知识库」而非「查全部」（fail-closed）。
  - **只落地页写这个字段**：执行态输入区续的是已有会话、其 id 在首轮就已定死（AC-25），日常对话的灵思入口在到达 submit schema 之前已自行解析出 id，两者都不设它，因此**不存在二次展开**。
- **何时该重新考虑**：若将来要求落地页也提供「逐库挑选」，那是把逐项能力重新加回输入区 —— 本特性明确移除（spec §范围边界），需要新的产品决策。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**发送一轮问答（日常问答 / 订阅面板 / 独立对话页 / 分享页）：**

```
ChatKnowledge（输入区 + 菜单）
  └─ 用户改动 → setSelectedOrgKbs(store.selectedOrgKbs)        [Recoil 原子，唯一状态]
       ├─ ChatView 持久化 effect  → localStorage `bs:{uid}:selectedOrgKbs`   [只有这里写盘]
       └─ AttachmentBar 渲染 src/KbCard（每库一张 148px 卡）→ 移除 = onRemoveKb

点击发送 → useAiChat.sendMessage
  └─ 构造 payload.use_knowledge_base = toPayloadKnowledgeScope(items, hasSpaces, featureEnabled)
       （useAiChat.ts:277 正常发送；useAiChat.ts:741 重新生成/继续追问 —— **两处同一函数、同一实参**。
         分叉会表现成「第一次回答知道、重试就不知道」，这是 T-11 专门守的失败模式）
  └─ POST SSE → 后端 chat_service

后端（chat_service.py）
  ├─ _resolve_user_kb_selection(data, login_user)           ← 唯一 KB 解析漏斗（第 1021 行）
  │    ├─ organization_knowledge_all 为假（含旧客户端 / 任务模式）── 既有路径，**逐字未变**
  │    │    └─ KnowledgeDao.aget_list_by_ids(org_ids + space_ids)（第 974 行）
  │    │         → [{id,name,description,tags,source}]，**不做权限收口**（见下方「既有事实」）
  │    └─ 为真 ── 「全部」路径，本特性新增（第 1042 行分叉）
  │         └─ _expand_all_org_knowledge(login_user)（第 980 行）
  │              ├─ KnowledgeDao.aget_all_knowledge(knowledge_type=NORMAL)  全租户 type=0 候选
  │              │    只带 type 条件，靠 C3 的 do_orm_execute 注入租户条件；
  │              │    **无 limit、无分页、不带 filter_department_ids**（真实 SQL 见 T-04）
  │              ├─ 非管理员 → filter_knowledge_ids_by_permission_async(login_user, candidate_ids, "use_kb")（第 1011 行）
  │              │    **与检索侧 workstation_service.py:1116 是同一调用、同一 permission_id**（INV-21）
  │              └─ **fail-closed**：候选枚举或收口任一步抛异常 → 返回 `[]`；
  │                   收口失败**绝不**返回候选集本身（第 1002-1016 行，这是本特性最关键的一条）
  │         → 返回**整行 ORM 对象**（list[Knowledge]）；dict 化发生在漏斗末尾，不在 helper 里
  │         → 展开结果同时决定 org / space 分桶（桶按行的 `source`），因此**检索桶与提示词同源**
  ├─ _prepare_tools(...) → _build_knowledge_search_tool（第 623 行，kb_id_whitelist 第 646 行）
  └─ 用户提示词 <selected_knowledge_bases> 区块
       └─ 工具调用 search_knowledge_bases → WorkStationService.queryChunksFromDB（workstation_service.py:1089）
            ├─ _filter_visible_space_kb_ids：只筛知识空间桶；组织桶**原样直通**（workstation_service.py:985 注释 AC-14）
            ├─ 组织桶非管理员 → filter_knowledge_ids_by_permission_async(login_user, org_ids, "use_kb")（第 1116 行）← 权限收口
            │    └─ 被拒的库进 failures[]，前端渲染成失败 KB chip
            └─ 逐库 get_multi_knowledge_vectorstore → 检索 → 文档总量上限 100
```

> **既有事实，读 diff 时勿误读**：上图的**显式 id 路径不做收口** —— `aget_list_by_ids` 只受租户过滤，
> 所以同一租户内用户无授权的库名/描述会进提示词，直到检索侧才被 `use_kb` 挡下并渲染成失败 chip。
> F063 只给「全部」档位补上了收口，显式路径按 AC-25 **保持原样**。不要以为整个漏斗都收口了。

**落地页提交（任务模式 · 输入区发起）—— 本特性唯一一处对外行为变化（决策 14 / H1'）：**

```
TaskModeChatInput.handleSend（档位由落地页自己的状态给出）
  └─ useLinsightSubmit → payload.organization_knowledge_all = submission.organizationKnowledgeAll
  └─ POST /api/v1/linsight/workbench/submit

后端 LinsightWorkbenchImpl.submit_user_question（workbench_impl.py:255-282）
  ├─ organization_knowledge_all 为真时**在提交期物化**：
  │    └─ **局部 import** 复用同一份展开 _expand_all_org_knowledge(login_user)（第 269 行）
  │         （局部而非模块级：workstation 是下游模块，模块级 import 会成环）
  │         → org_ids = [kb.id for kb in ...] or None
  └─ LinsightSessionVersion.organization_knowledge_ids = org_ids（第 282 行）  ← 落库的是**具体 id**，不是布尔
       └─ 任务执行期 task_exec._resolve_user_knowledge_bases 只做 aget_list_by_ids：
            **不再做 use_kb 收口**，且不读 org_knowledge_enabled / personal_knowledge_enabled（坑 16）
```

**组织知识库功能被关闭时（spec AC-18）的分叉：**

```
config.knowledgeBase.enabled === false
  ├─ 入口不渲染（ChatKnowledge.tsx:387 的既有判断，无需新增）
  ├─ payload 恒为「不使用」（不发 organization_knowledge_all，ids 为空）
  └─ 状态层**不动**：档位标记必须活过关闭期，重新开启即恢复求值   ← 决策 13（既有实现会剥掉它）
```

**任务模式交接（日常页切任务模式）：**

```
useAiChat → payload.tools 非空 + 任务模式 → 后端 _to_linsight_submit(data, org_ids)（chat_service.py:2375）
  ├─ 日常链路先经 _resolve_submit_org_ids(data, login_user)（第 2350 行）把档位解成具体 id
  │    └─ 三态：`None` = 未设档位，**透传**请求自带的 id 列表（6 个既有调用方走这条）
  │              `[]` = 设了「全部」但一个都没权限（必须保持为空，不得回退到请求自带的 id）
  │              非空 = 展开结果
  │         ——`is not None` 这个判断是承重的，改成 `return []` 会让既有调用方的显式 id 交接**静默变空**
  └─ 直接读 use_knowledge_base.organization_knowledge_ids / knowledge_space_ids
       → LinsightQuestionSubmitSchema → linsight 任务存储（**具体 id**）；**不**再展开一次（AC-25 防线）
```

### 4.2 关键数据结构 / 字段约定

> 写**对外可见**的契约。

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `use_knowledge_base.organization_knowledge_ids` | `list[int]`，可选，默认 `[]` | **既有字段，语义不变**：用户显式选定的组织知识库 id。`>50` 抛校验错误 | 旧客户端、任务模式、既有集成测试 |
| `use_knowledge_base.knowledge_space_ids` | `list[int]`，可选，默认 `[]` | 知识空间 id，语义不变（`>50` 同样拒绝） | 同上 |
| `use_knowledge_base.organization_knowledge_all` | `bool`，**可选，新增**，默认 `false` | 为 `true` 时由**服务端**展开为「当前用户有权限的全部组织知识库」；为 `true` 时 `organization_knowledge_ids` 被忽略。与显式列表**互斥**（客户端只发其中一种） | 新客户端（日常问答 / 订阅面板 / 独立对话页 / 分享页） |
| `LinsightQuestionSubmitSchema.organization_knowledge_all` | `bool`，**可选，新增**，默认 `false` | **另一条链路**（灵思提交 `/api/v1/linsight/workbench/submit`）的同名同义字段。为 `true` 时服务端在**提交时**展开成具体 id 并写入本模型的 `organization_knowledge_ids` —— **落库的是具体 id，不是这个布尔**（任务执行期只读 id 列表，坑 16）。仅任务模式**落地页**会发它；另两个生产者（执行态输入区、日常对话的灵思入口）保持 `false`，因此不存在二次展开 | 灵思提交 → 任务执行 |
| `store.selectedOrgKbs`（前端原子） | `SelectedOrgKb[]`，`{id,name,type}` | 新增可选字段 `orgScope?: 'all' \| 'none'`，仅出现在合成标记项上 | 输入区、附件条、payload 构造 |
| 合成标记项 | `{id:'__org_scope__', name:'', type:'org', orgScope:'all'\|'none'}` | **不存在** = 「未显式设定」；`'all'` = 显式全部；`'none'` = 显式不使用。保留 id 常量集中定义 | 前端全部 `selectedOrgKbs` 消费者 |
| localStorage `bs:{uid}:selectedOrgKbs` | JSON 数组 | **key 不变**（不再新增 key，也不新增迁移标记）。磁盘上可能是旧形态（含多个 `type:'org'` 逐项）或新形态；**读时只认知识空间项与档位标记，其余丢弃**（决策 8），不做迁移 | ChatView 水合/持久化 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `hooks/Chat/useAiChat.ts`（2 处 payload 构造） | 把原子状态翻译成请求体，含三态 → `all`/显式列表的解析 | 不判断权限、不判断「哪些库存在」 |
| `components/Chat/ChatView.tsx`（水合 + 持久化两个 effect） | 唯一的状态持久化点；读盘时**只保留认识的形态、丢弃其余**（决策 8，非迁移） | 不渲染选择 UI |
| `components/Chat/Input/ChatKnowledge.tsx` | 输入区入口：组织知识库两档 + 知识空间多选清单 | 不再展示组织知识库清单、不再逐项勾选（AC-13/14） |
| `components/Chat/Input/AttachmentBar.tsx` | 附件条渲染（含「全部组织知识库」摘要卡） | 不持有状态 |
| `components/Chat/AiChatInput.tsx` | 把原子状态透传给入口与附件条；`onRemoveKb` 的落点 | 不做档位语义判断 |
| 后端 `workstation/domain/services/chat_service.py::_resolve_user_kb_selection` | `UseKnowledgeBaseParam` → 具体 KB 元数据的**唯一漏斗**；「全部」在此展开（编排），标签聚合在「全部」路径跳过 | 不写 ORM、不新增可见性判定（候选与收口都调 knowledge 域的只读入口） |
| 后端 `knowledge/domain/services/knowledge_permission_service.py::filter_knowledge_ids_by_permission_async` | 「全部」的逐库收口 + 管理员分支（**既有入口，只读复用，不新增**） | 本特性不改它、不新增同级入口 |
| 后端 `knowledge/domain/models/knowledge.py::KnowledgeDao.aget_all_knowledge` | 候选集（**既有入口，只读复用，不新增**；只传 `knowledge_type`） | 只读；不新增表 / 字段 / DAO 方法（C1）；租户过滤交给 C3 自动注入 |
| 后端 `workstation/domain/services/workstation_service.py::queryChunksFromDB` | 检索编排；组织桶的 `use_kb` 收口 | 不改可见性规则、不改逐库检索结构 |
| 后端 `knowledge/api/endpoints/knowledge.py::get_knowledge` | 平台侧列表（**不是**本特性要改的路径） | 本特性只依赖它的语义一致性（AC-10），不改它 |
| 后端 `linsight/domain/services/workbench_impl.py::submit_user_question` | 灵思会话版本的**唯一写入者**：`organization_knowledge_all=true` 时在此把「全部」展开成具体 id 再落库（决策 14），复用日常漏斗**同一个** `_expand_all_org_knowledge`；展开失败退化为「本轮不查」（fail-closed） | 不在端点层做（C1 分层）；不新写可见性判定；不碰执行态续轮 |
| 后端 `workstation/domain/services/chat_service.py::_resolve_submit_org_ids` / `_to_linsight_submit` | 日常对话的灵思交接：**不走** `_resolve_user_kb_selection`，自己把档位解析成具体 id 交给 submit schema（决策 10） | 落地页**不经**此路径（它只发布尔，由 submit 侧展开）；两者共用 `_expand_all_org_knowledge`，「全部」只有一个口径 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **空数组在现状语义里是「一个都不查」**（`_resolve_user_kb_selection` 第 985 行 `if not ids: return []`） | 会以为「发空 = 全部」是自然写法，从而静默反转所有既有调用方的语义（含任务模式与集成测试） | 决策 1；新字段而非改语义 |
| 2 | **组织桶在 `_filter_visible_space_kb_ids` 里是原样直通的**，权限收口发生在**下游**的 `filter_knowledge_ids_by_permission_async(..., "use_kb")`，且**管理员跳过这步** | 会误以为展开处必须自己做权限过滤；也会漏掉管理员分支（管理员拿到的是候选集本身，展开逻辑必须对管理员也给出正确的「全部」） | 决策 3；§4.1 |
| 3 | **展开的结果会进提示词**（`<selected_knowledge_bases>`），不只进检索 | 若用「超集候选」直接当结果，会把用户**无权使用**的知识库**名字与描述**泄露给模型 —— 这是信息泄露，不是功能瑕疵 | 决策 3：展开必须收口后再返回 |
| 4 | **附件条的移除处理器按 id 过滤**（`AiChatInput.tsx:378`：`filter(i => i.id !== kb.id)`） | 摘要卡的移除会走这条分支而**什么都不发生**（合成标记项的 id 不在正常库里） | 决策 9：为标记项加「置 `none`」的独立分支 |
| 5 | **组织知识库列表接口是游标分页且已移除 `total`**（`knowledge.py:431-436`） | 会想「取个库数而已」，然后为了一个角标写翻页到底的逻辑 | 决策 9 / 12：只判断 0 与非 0 |
| 6 | **「有没有组织库」的探测在 `AttachmentBar.tsx:263` 与 `AiChatInput.tsx:342` 两处**，各发一次 `useGetOrgToolList({page:1, page_size:1})`，**共用同一条 queryKey** 因而实际只发一次网络请求。该调用**已从 `ChatKnowledge` 移除**（现连 `useGetOrgToolList` 都不 import） | 会以为探测是某个单一组件的事而只改一处；也会以为它是新增的网络请求而过度设计（加缓存、加开关）——实际只是把既有请求的 `page_size` 调小。两处的 `page_size` 必须一致，否则谁先挂载谁决定另一侧拿到几条（坑 17） | 决策 12；坑 17 |
| 7 | **同一份原子还被 `MessagesView`、`useChatFunctions`（legacy hook，仍被 `useChatHelpers`/`useAddedHelpers` 引入）消费** | 改值形态时会漏掉消费者，导致编辑/重新生成路径拿到合成标记项后行为异常 | tasks 阶段逐一核对。**T-17 审计结论**：`useChatFunctions` 及其引入方**是死代码**（`src` 内无 `<ChatContext.Provider>` 挂载点，`useChatContext()` 恒为默认空对象；其唯一产物 `store.submission` 无任何读者），故只在原处加了「重建时必须走 `toPayloadKnowledgeScope`」的注释，未改动；`MessagesView` 只在「开启新对话」写 `setSelectedOrgKbs([])`（清空 = 回到兜底档，已注释） |
| 8 | **`_to_linsight_submit` 不走 `_resolve_user_kb_selection`**，而是直接读 id 列表 | 以为「改了漏斗就全覆盖」，结果任务模式静默退化为「不检索组织知识库」 | 决策 10 |
| 9 | **任务模式输入区复用了同名的 `selectedOrgKbs` prop，但吃的是灵思自己的 store**（`TaskModeChatInput.tsx:171`：`selectedOrgKbs={context.knowledge}`） | 会把「删掉组织清单」误伤到任务模式的选择器 | spec §范围已排除；tasks 阶段需断言任务模式 UI 零改动 |
| 10 | **管理员配置的 `orgKbs` 清单在 client 侧已无任何消费方**：`preferredIds` / `sortedOrgKbs` 已随本特性**整体删除**，`default_checked` 也不再播种（ChatView 改走 `normalizeStoredItems`） | 按旧叙述（「只影响排序与首次默认值」）去 grep 会**一无所获**，然后怀疑整份文档是否可信。当前事实是：该配置**不再影响**对话默认口径（AC-23），全仓 grep 只剩 `api/linsight.ts` 一个**没人传的**可选参数。也**不必**去改后端 `default_checked` 的产出——那从来不是让这里失效的原因 | 决策 12 / spec AC-23 |
| 11 | **组织树角标（`department_stats`）只统计 `department_id IS NOT NULL` 的库** | 会以为「全部」与角标不一致（角标不含未归属库）而误判为 INV-21 违规 | §6.1 附注：角标的口径**本来就**只覆盖已归属库；AC-10 的一致性锚点是**平台列表** |
| 12 | **`_resolve_user_kb_selection` 会打一行带全部 id 的日志**（第 1055 行） | 「全部」档位下这行日志会变得很长（几百个 id），可能顶到日志行上限 | tasks 阶段评估是否改为只打数量 |
| 13 | **功能开关的既有实现会「顺手」删掉用户状态**：`ChatView.tsx:116-120` 的 `filter(item => item.type !== 'org')` 与档位标记的 `type:'org'` 撞车，且随后被持久化 effect 写盘 | 管理员关一次组织知识库功能，用户显式设过的档位就被永久抹掉（写盘后不可恢复），重新开启也无从「恢复求值」 | 决策 13；水合过滤改为只针对旧的逐项组织项 |
| 14 | **同一个「水合后写盘」的 effect 既是归一器也是破坏者**（`ChatView.tsx:165-168` 只要 `selectedOrgKbs` 一变就写盘） | 任何在水合阶段做「过滤」的逻辑，实际上都是**不可逆的迁移**，不是临时视图 —— 改水合逻辑前必须想清它是视图还是写入 | 决策 8（不迁移，只在读时丢弃不认识的形态）与决策 13 都建立在这个事实上 |
| 15 | **落地页与执行态共用同一个 `taskModeContextState` 原子，而且 key 真的会重合**：两者都取 `conversationId \|\| 'new'`，落地页写的正是 `'new'`；执行态的 `ExecutionFlow` 在 `linsight.session_id` 尚未落进 store 时也回退到 `'new'`（`ExecutionFlow.tsx:274-278`）。执行态原判定是 `context.knowledge.length > 0` | 落地页设了「不使用」后，执行态会把档位标记当成「用户勾了组织知识库」而误报（`TaskModeInput.tsx:215/232`）。**这不是「将来 key 可能重合」，而是首轮会话创建前后的真实窗口** —— 踩上就向用户/后端报一个相反的档位 | 决策 14 + 坑 13 同一收口：`TaskModeInput` 改用 `hasPickedKnowledge(context.knowledge)`（只数真实条目），落地页侧用 `isOrgScopeAll`；两处都不再看原始 `length` |
| 16 | **任务执行期只认具体 id 列表，提交入参上的粗粒度布尔是「死字段」**：`task_exec._resolve_user_knowledge_bases` 只解析 `organization_knowledge_ids` / `knowledge_space_ids`，其 docstring 明写 `org_knowledge_enabled` / `personal_knowledge_enabled`「intentionally NOT consulted」 | 会以为「提交时 `org_knowledge_enabled=true` 就等于检索了组织知识库」，从而写出一个**UI 上完全正确、检索侧毫无作用**的实现（code-review H1'：落地页的档位与摘要卡曾纯属显示，会话一生不查任何组织库）；也会以为「改了布尔就等于改了行为」而漏掉 id 列表的交接 | 决策 10（交接必须交出具体 id）与决策 14（落地页以 `organization_knowledge_all` 提交、服务端**展开成 id 落库**）；坑 8 |
| 17 | **全应用共用一个 `QueryClient`，`queryKey` 少一个字段就会让参数不同的调用方塌成同一条缓存**（本次是 `['OrgTools', page, page_size, …]` 漏了 `page_size`） | 「同一份数据、不同分页」看起来天然该共用缓存，实际是**互相截断**：本特性的存在性探测（`page_size:1`）与灵思组织库选择器（`page_size:50`）命中同一条，谁先挂载谁决定另一侧渲染什么 —— 探测在前就把选择器截成一行，且**不报错**（比 404 隐蔽） | 决策 12 的落点；`hooks/queries/tools/queries.ts::useGetOrgToolList` 的 key 已含 `page_size`；新加带分页参数的 query 时先检查 key 是否带全参数 |
| 18 | **输入区有两处 legacy 互斥**（`AiChatInput.tsx`：加文件时、切「联网搜索」时），语义是「清掉这轮选中的 KB」—— 而档位标记**不是** KB，只是**恰好躺在同一个数组里**；`ChatKnowledge` 回写选中态时同理 | 两个方向都会静默出错：写 `onSelectedOrgKbsChange([])` 把标记一起删掉 ⇒ 兜底重新生效，用户显式设的「不使用」**变成「全部」**（把用户拒绝的检索打开）；判据写 `val.length > 0` 把标记读成「选了知识库」⇒ 点「不使用」会清掉刚挂上的附件 | 决策 9 / 坑 13 的同一收口，两个助手都在 `utils/knowledgeScope.ts`：**清空**走 `onlyOrgScopeMarker(items)`（只留标记）、**判据**走 `hasPickedKnowledge(items)`（只数真实条目）；两处调用点有静态断言钉住，助手本身有单测且经变异验证 |
| 19 | **`_resolve_submit_org_ids` 返回三态，`is not None` 这个判断是承重的**：`None` = 未设档位、**透传**请求自带的 id 列表（6 处既有调用方走这条）；`[]` = 设了「全部」但一个都没权限（**必须保持为空**，不得回退到请求自带的 id）；非空 = 展开结果 | 把 `return None` 改成 `return []`（看起来更「一致」）会让**所有既有调用方**的显式 id 交接**静默变成空列表** —— 任务模式丢组织库，且**不报错**。它与坑 16 讲的不是同一件事（那条是「布尔是死字段」，这条是「三态不能塌成两态」） | `chat_service.py:2350`；决策 10；§4.1 的交接流程 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `use_knowledge_base.organization_knowledge_all` | HTTP 入参（新增可选布尔） | client 前端（日常问答 / 订阅面板 / 独立对话页 / 分享页） |
| `LinsightQuestionSubmitSchema.organization_knowledge_all` | HTTP 入参（新增可选布尔，灵思提交） | client 前端（任务模式**落地页**） |
| 「全部」的展开结果——**原始形态** | 内部 Python 对象（`list[Knowledge]`，**整行 ORM**；`_expand_all_org_knowledge` 的返回值） | 三个调用方：`_resolve_user_kb_selection`（在本漏斗内 dict 化）、`_resolve_submit_org_ids`、`submit_user_question`（后两者只取 `.id`） |
| 「全部」的展开结果——**漏斗出口形态** | 内部 Python 数据（`list[dict]`，`{id,name,description,tags,source}`） | `_build_knowledge_search_tool`、用户提示词 `<selected_knowledge_bases>` 组装 |
| 组织知识库档位语义（全部 / 不使用 / 未显式设定） | 前端状态形态（`SelectedOrgKb.orgScope`） | 输入区、附件条、`MessagesView`、`useChatFunctions` |

> **附注（AC-10 的一致性锚点）**：INV-21 要求所有知识库读取入口共用同一份可见集合判定。「全部」与**平台知识库列表**的一致性是逐库判定同一份带来的**集合相等**（决策 3 的论证）；组织树角标是同一份判定在**已归属子集**上的汇总，天然比「全部」小（坑 11），这不构成 INV-21 违规 —— 角标从不宣称覆盖未归属库。

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `UseKnowledgeBaseParam`（`chat_schema.py:60`） | 内部/对外共用的入参模型 | 新字段必须**可选且默认 `false`**，否则旧客户端请求 422 |
| `KnowledgePermissionService.filter_knowledge_ids_by_permission_async` | 内部 Python API（生产代码**共 3 个调用方** = 本特性新增的 `chat_service.py:1011` + 既有的 `workstation_service.py:1116`、`knowledge_department_service.py:174`） | 它被改签名或语义会同时影响本特性与平台列表；这也正是 INV-21 想要的耦合 |
| `bisheng.workstation.domain.services.chat_service._expand_all_org_knowledge`（**跨域私有符号**） | 内部 Python API（async，入参 `UserPayload`，返回**整行 ORM**，异常不抛而返回 `[]`） | 由 `linsight` 域的 `workbench_impl.py:269` **在函数内局部 import** 调用——**必须保持局部**：workstation 是下游模块（它 import linsight 侧的服务），模块级 import 会成环。改它的签名或返回形状会同时打断两个域 |
| F062 的机构域判定（`PermissionService._implicit_department_library_level` + `department_org_domain` 映射缓存） | 进程级缓存 | 缓存不可用时退化为逐级回溯（慢但正确）；展开路径的判定量随库数增长，会把这份退化放大 |
| `GET /api/v1/knowledge` 的 `type=0` 口径 | 隐式数据契约 | 若「组织知识库」的定义改成含 QA(1)，本特性的「全部」范围随之失效（决策 4） |
| 灵思任务存储 `organization_knowledge_ids`（`linsight_session_version`） | 隐式数据契约 | 它存**具体 id**，所以交接必须展开（决策 10） |
| 系统二进制 / 第三方服务 | — | **无**：本特性不引入任何二进制依赖，也不引入新的第三方服务，只走既有的 DB / Milvus+ES / 权限判定链路 |
| 前端 i18n：三语言同 PR 补 key | 工程约束 | 两档标签 + 摘要卡文案需同时上 zh-Hans/en/ja，否则 CI `check-i18n` 失败 |

---

## 7. 测试与可观测

- **单元 / 集成（后端）**：新增测试放 `test/workstation/`（与既有 `test_query_chunks_visibility.py`、`test_unified_chat_entry.py` 同目录）。必须覆盖的正交项：
  1. `organization_knowledge_all=true` → 展开集合 == 用同一份 `use_kb` 判定对全量候选收口的结果（**含管理员分支**）；
  2. 展开结果**不含**用户无权使用的库（信息泄露断言，坑 3）；
  3. `all=true` 时 `organization_knowledge_ids` 被忽略（互斥语义）；
  4. 库数 > 50 时**不报错、不拒绝**（AC-09）；
  5. 旧客户端形态（只发显式列表、不发新字段）行为**完全不变**（回归）；
  6. `_to_linsight_submit` 在 `all=true` 时交出的是具体 id 列表（决策 10）；
  7. **功能关闭不销毁档位**（决策 13 / spec AC-18）：功能关闭 → 水合 → payload 为「不使用」→ 功能重开 → 档位**恢复到关闭前**。这条必须**构造性**断言（先存一个 `orgScope:'none'`，关闭再开启后读回它仍在），不要只断言「关闭时不下发」—— 那种断言在「标记被删掉」的实现下同样通过。
  8. **落地页档位落到 id 列表**（决策 14 / code-review H1'，`test/linsight/test_f063_landing_org_scope.py`）：`organization_knowledge_all=true` → 提交后写入版本的 `organization_knowledge_ids` 是展开结果；**只断言 id 的值不够**（硬编码一份也能过），必须同时断言 `_expand_all_org_knowledge` **被调用了 1 次** —— 那才是「「全部」只有一个口径」的证据；另需断言展开失败时落空（fail-closed，不回退成「查全部」）、以及不带该标志时显式 id 原样透传且不触发展开（AC-25）。
- **前端**：断言 `selectedOrgKbs` 的三态求值（含遗留形态读盘 —— 无标记即兜底、残留 `type:'org'` 逐项被丢弃）、payload 的两种形态互斥、以及 `useLinsightSubmit` 的 SSE 载荷按档位发出 `organization_knowledge_all`（含「生产者从未设该字段 → 显式 `false`」这条 AC-25 守卫）。**变异验证**：`container.innerHTML` 类断言对 Radix portal 恒为空（见既有教训），一律用 `screen` + 构造性断言（「出现 X」而非「不出现 Y」）；**接线上断言（源码文本匹配）必须用变异反证**——把实现改坏（例如让档位恒真/恒假）后它必须变红，否则它只是在数关键字。
- **E2E（`/e2e-test`，强制）**：至少覆盖 —— ① 什么都不选直接问答（全量组织知识库）；② 只看一个知识空间（不掺组织库）；③ 全部 + 叠加空间；④ 移除摘要卡 → 该轮不检索组织库；⑤ 刷新后档位保持；⑥ 任务模式**执行态** UI 与行为零改动（AC-25 修订后的保护范围）；⑦ 档位切「不使用」后平台列表与组织树角标不变（AC-12）。**另加**（决策 14）：任务模式**落地页**默认「全部」时，`linsight_session_version.organization_knowledge_ids` 落库的是**该用户有权限的全部组织库 id**（不是 `null`，也不是空）—— 这是本次唯一一处对外行为变化，断言必须落在**库里的 id 列表**上；断言 `org_knowledge_enabled` 是无效的，该布尔执行期不读（坑 16）。
- **可观测**：
  - `_resolve_user_kb_selection` 现有日志（第 1055 行）是「展开了多少库」的**唯一现存观测点**，坑 12 的日志瘦身要保住它（至少要打数量）。
  - `filter_knowledge_ids_by_permission_async` 自带 `[perf][knowledge.permission_map]` 计时日志（`knowledge_permission_service.py:279`），候选数/保留数都在里面 —— 「全部」路径的判定成本直接看这行。
  - `queryChunksFromDB` 的逐库失败会进 `failures[]` 并以失败 chip 呈现给用户，属于**用户可见**的观测面，不需要额外埋点。
- **手动验证**：
  - 后端（cwd `src/backend/`）：`uv run uvicorn bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1 --no-access-log`。检索链路真跑起来还需要 Celery worker（`uv run celery -A bisheng.worker.main worker -l info -c 20 -P threads -Q knowledge_celery -n knowledge@%h`）以及 Milvus / ES / Redis / MinIO 中间件（`docs/architecture/09-development-guide.md`）。
  - 前端（cwd `src/frontend/client/`）：`pnpm dev`（:4001；开发者本机注意 **QQ.exe 抢 127.0.0.1:4001** 的已知白屏问题）→ `/workspace`。
  - 账号：单租户模式下**首个注册用户自动成为 `super_admin`**，多租户模式需先建租户再建用户（根 `AGENTS.md §7`）。**两侧都要验**：用非管理员账号验「全部」的可见范围收口；用管理员账号验 `is_admin` 分支（`queryChunksFromDB` 对管理员**跳过** `use_kb` 收口，坑 2）。
  - 三个场景各发一轮：无任何选择 / 只选一个知识空间 / 全部 + 叠加知识空间；对照后端 `_resolve_user_kb_selection` 的 `resolved N kbs` 日志与前端 chip 表现。

---

## 8. 后续改进 / 不打算做的事

- **检索侧的库数放大（决策 11）**：`queryChunksFromDB` 逐库初始化向量库，本期不动。触发条件是真实租户报障（百库级 + 每轮明显变慢）；届时的两条路径已在决策 11 写明（批量初始化 / 引入**用户可见**的收敛手段）。
- **`_resolve_user_kb_selection` 的逐库标签聚合**：显式路径保持原样（O(N) 但 N 通常很小）；「全部」路径跳过。若将来「全部」也要标签过滤，正确做法是加一个按 `knowledge_id` 批量的 DAO 方法，而不是恢复 N+1。
- **显式 id 列表的下线**：前端本期只剩两档，但旧客户端与任务模式仍依赖显式列表，故 `organization_knowledge_ids` 与它的 `>50` 校验器**保留**。真正下线需要一次跨版本的兼容评估（契约 §6.2）。
- **任务模式的「动态全部」**：任务存储记的是提交期的具体 id；若产品要求任务执行期动态求值，需要改灵思的状态模型（决策 10）。
- **QA(1) 知识库是否纳入对话默认检索**：本特性明确取 `type=0`（决策 4）。若产品要扩到 `type=1`，那是 spec AC-03 的范围变更，不是实现调整。
- **前端状态的整体重构**：档位靠合成标记项塞在既有原子里，是 Recoil 冻结下的妥协（决策 6）。Jotai 迁移落地后应整体重写，不保留兼容层。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-22 | 初版：12 条决策 + 现状快照 + 12 条坑 | feature 设计阶段；实现未开工 |
| 2026-09-22 | `/sdd-review design` 首轮 5 项修复：新增决策 13（功能关闭时的档位处置 —— 既有实现会在关闭时销毁用户档位）、决策 3 补 C1 分层落点、修正 C3 笔误并写明禁止手写 `tenant_id`、§6.2 补「无二进制/第三方依赖」行、§7 补实跑命令与账号口径；坑表补 13 / 14 | design 评审 |
| 2026-09-22 | **决策 3 落点勘误（C1 BLOCKER）：由「新增 DAO 只读方法 + 新增 Service 只读入口」改为「复用两处既有入口，本特性零新增」** —— 拆 tasks 时对照 `docs/constitution.md:30` 发现原文自相矛盾：C1 同一句里既有「never write ORM queries in Service」（原文据此导出「新增 DAO 方法」），也有紧随其后的「**never add new DAO entry points for new features**」，后者直接否掉新增 DAO 方法。改为复用 `KnowledgeDao.aget_all_knowledge(knowledge_type=NORMAL)` + 既有 `filter_knowledge_ids_by_permission_async`（管理员跳过），并补记两项：候选**不得**带 `filter_department_ids`（那是列表视图收窄）、整行 ORM 取值的内存代价。**决策本身（超集候选 + 同一份逐库判定）不变，改的是落点**，同步更新 §4.3 | 拆解 tasks 时发现（决策 3 原文与 C1 第 30 行冲突） |
| 2026-09-22 | **决策 8 按需求方裁决重写：存量数据不做迁移**（原「读时归一 + 区分是否选过组织项」降为被否决方案 B），移除文首 ⚠ 待确认横幅；同步改写 **spec AC-21**、spec §范围边界与 §3 边界情况（原字面「升级即迁移为全部」会把「只选过知识空间」的用户静默扩权到全公司），并新增「已知副作用」条目 | 需求方裁决（design ★ 前的最后一项待定） |
| 2026-09-22 | **新增决策 14（任务模式落地页跟随新口径）+ 坑 15**，并修订 **spec AC-25**（保护范围限定到执行态、记录落地页跟随与那处已确认的默认翻转）；坑 7 由「待确认」改为 T-17 审计结论（`useChatFunctions` 为死代码） | T-17 消费者审计发现 AC-25 的前提只对执行态成立 |
| 2026-09-22 | **code-review 收尾（H1' 落地页兑现承诺）**：决策 14 补记「原文只证了『不丢失』、没证『能生效』」并写明兑现方式 —— 落地页新增提交字段 `organization_knowledge_all`，由 `submit_user_question` 在提交时展开成具体 id 落库（与日常漏斗**同一个** `_expand_all_org_knowledge`，闭环仍是一条「全部」口径）；§4.2 补该字段、§4.3 补两个职责行（`submit_user_question` / `_resolve_submit_org_ids`）、§6.1 补 outgoing、§7 补单测第 8 项并改写前端与 E2E 断言；新增**坑 16**（提交布尔是死字段、执行期只认 id 列表）与**坑 17**（共享 `QueryClient` 下 `queryKey` 漏 `page_size` 会让不同参数的调用方互相截断），并改写**坑 15** —— 落地页与执行态共用 `'new'` key 不是「将来可能」而是首轮会话创建前后的真实窗口（同步修正决策 12 落点里过期的缓存键形状） | `/code-review --base feat/2.7.0/045-minimax-tts-adapter`：复核发现「按决策 14 原样实现」的落地页档位对检索**零作用**（提示词与检索都只认 id 列表），需求方裁决「落地页兑现承诺」 |
| 2026-09-23 | **实现合入后的 code-review 复审（4 agent × 7 维度）**。修 **H3**：§4.1 数据流停在开工前 —— 补 `organization_knowledge_all=true` 分叉（候选 → 收口 → fail-closed，并写明展开结果**同时决定 org/space 分桶**，因为桶按行的 `source` 走）、补**落地页提交链路**（`submit_user_question` 提交期物化 → 落库具体 id → 执行期只做 `aget_list_by_ids`、不再收口）、补 `_resolve_submit_org_ids` 的三态语义（`None` 透传 / `[]` 全部但无权限 / 非空展开结果），并把行号与签名全量刷新（`chat_service.py` 因本特性整体下移 50~64 行）；新增**坑 18**（两处 legacy 互斥会销毁档位标记）；页头日期同步。**后端五条核心安全要求经逐条核对成立**（与检索侧同一份逐库判定 / 真 fail-closed / 不手写 `tenant_id` / 灵思不双重展开 / 旧形态逐字不变），未发现越权或信息泄漏类实现缺陷 | 实现合入后按 SDD 流程复审；同一轮审查抓出两条前端真回归（**H1** 档位标记被读成「选了知识库」而误清附件、**M1** 两处互斥清空整组导致「不使用」回弹成「全部」），已修并补测试 |
| 2026-09-23 | **本轮修复的测试侧收尾**：改写 AC-08 用例（原与 AC-02 输入字面相同）、AC-18 用例（原为同一表达式的重复调用，「关闭期」从未被建模），并为 `ChatKnowledge` 补 `onChange` 回写断言（该文件头部声称覆盖回写，实际 `onChange` 从未被断言 —— 把 `onSelect` 改成空函数，全部前端测试仍绿）。**六项变异逐一验证**：改坏实现后新断言分别变红，备份还原后工作区无残留注入行 | 复审指出「25 条 AC 全覆盖」中的 AC-08 / AC-18 / AC-20 / AC-24 存在虚报 |
| 2026-09-23 | **文档维度收尾（同一轮复审的 MEDIUM 项）**：§1 非目标第 2 条与决策 14 自相矛盾（原文按 AC-25 **修订前**的口径写「不改任务模式的知识选择与持久化链路」，而落地页恰恰改了）——已限定为**执行态**并指向决策 14；**坑 6** 改回事实（探测在 `AttachmentBar.tsx:263` 与 `AiChatInput.tsx:342` 两处、共用 queryKey，该调用已从 `ChatKnowledge` 移除）；**坑 10** 改为「该配置在 client 侧**已无任何消费方**」（原文「只影响排序与首次默认值」指向的 `preferredIds` / `sortedOrgKbs` 已被本特性整体删除，后人照原文 grep 会一无所获）；**§6.1** 的「展开结果」拆成**原始形态（整行 ORM）**与**漏斗出口形态（dict）**两行（原文把两者写成一回事，且消费者列错）；**§6.2** 修正 `filter_knowledge_ids_by_permission_async` 的调用方数量（原文「另有 3 个」→ 生产代码**共 3 个**），并补一行**跨域私有符号依赖** `chat_service._expand_all_org_knowledge`（含「必须保持函数内局部 import，否则成环」这一约束）；决策 2 的「两处调用」→**三处**、决策 3 的「4 个调用方」→**3 个**；新增**坑 19**（`_resolve_submit_org_ids` 的三态语义，`is not None` 是承重判断） | 实现后复审的文档维度 MEDIUM（M5 及文档同步项） |
