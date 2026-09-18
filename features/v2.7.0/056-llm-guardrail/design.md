# Design: 大模型安全围栏（056-llm-guardrail）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)（待设计评审后编写）
**版本**: v2.7.0
**最后更新**: 2026-09-18（**同步 F059 两层检测现状**：§4.2 `source` 字段与行粒度、§4.3 双 matcher、§5 坑 13-15、§6.1 双端点与脱敏口径；见修订历史）

---

## 1. 目标与非目标

- **目标**：把平台既有的敏感词策略基础设施（F036）扩展到 **LLM 对话链路**，为所有对话入口提供输入拦截与流式输出过滤，并落命中监测日志——补齐「大模型安全围栏」中关键词检测这一环。
- **非目标**：
  - 不做语义级内容安全（谐音/变形/上下文）检测——二期以可插拔 provider 接入，本设计只为其留出动作/日志扩展空间。
  - 不改造知识库文件解析与订阅文章两个既有 F036 场景。
  - 不做 flow 级作用域、不做历史数据回扫。

---

## 2. 关键约束

遵循 `docs/constitution.md` C1–C7 与 `features/v2.7.0/release-contract.md`（错误码段分配见 §6）。本功能特有约束：

| # | 约束 | 对方案的影响 |
|---|---|---|
| K1 | **流式实时性**：输出以 SSE 分片流式回传，不能攒完整段再检测（首字延迟不可接受） | 输出检测必须**增量**匹配；AC 自动机需新增带状态 API |
| K2 | **入口一致性**：对话入口 ≥5 处（v1/v2 的助手与工作流 WebSocket + `/api/v2/chat/completions` HTTP），任一入口漏埋点 = 围栏有洞 | 检测点必须选在**共享收口层**（ChatManager/ChatClient），不逐端点埋点 |
| K3 | **零回归**：未配置策略的租户，行为与发布前逐字一致（AC-02/AC-17）——**前提是平台级强制词库（F059 / INV-10）未启用**；启用后该层独立于租户策略无条件生效 | 租户层检测点必须「策略无效即直通」：策略读取失败、未启用、词表空 → 直接放行。平台层为**独立的先决判断**，不并入本直通逻辑（F059 实现时在其之前短路） |
| K4 | **性能**：词表规模可达万级，输入检测 <10ms（AC-06）；输出检测为逐分片增量，单分片开销应亚毫秒级 | 复用既有 AC 自动机 + 策略级进程缓存，检测路径不查库 |
| K5 | **双 DB（C2）**：新表与字段必须用 `dialect_helpers`（JsonType/LargeText），禁 MySQL 专有语法 | 命中日志表、extra_config 解析按此约束设计 |
| K6 | **检测须在 API 层发生**（有租户 ContextVar 与登录用户），而非 Celery worker（无 HTTP 上下文） | 输入检测放 API/ws 层；命中日志的 tenant_id 从当前上下文取 |

---

## 3. 方案对比与选定

### 决策 1：复用 F036 模块扩展，还是新建独立 guardrail 模块

- **备选**：
  - A. 在 `bisheng/sensitive_word/` 内扩展——新增 `chat_input`/`chat_output` 两个 business_type、增量检测 API、接入点埋点。
  - B. 新建 `bisheng/guardrail/` 独立模块——自带策略表/引擎/前端页，与 F036 并存。
- **选定**：A
- **原因**：F036 的引擎（AC 自动机 + 按词表摘要的进程缓存）、策略模型（`business_type` 为 String(64)，加新枚举值只是新行，**零迁移**）、管理 API（GET/PUT `/sensitive-word-policies/{business_type}`）全部直接可用；B 会造出两套策略配置入口与两套引擎，管理员困惑、代码重复。
- **何时该重新考虑**：若未来需要与知识库/文章场景完全解耦的独立存储或独立权限模型。

### 决策 2：输入检测点选在哪

- **备选**：
  - A. 各 ws/HTTP 端点分别埋点（v1/v2 助手 + v1/v2 工作流 + chat/completions 共 5 处）。
  - B. 埋点在两个 ws client 的 **`handle_message`** 消息分发收口（`common/chat/clients/base.py` 工作流侧 + `common/chat/client.py` 助手侧——**实现期事实校正：ws 有两个 client 实现**，助手走 `common/chat/client.py::ChatClient`，非初版假设的单一实现）+ `/api/v2/chat/completions` 端点单独埋点。
  - C. 埋在工作流引擎层（graph 执行入口）。
- **选定**：B
- **原因**：B 是真实收口点——v1/v2 的助手/工作流 ws 都经 `ChatManager.dispatch_client`（`common/chat/manager.py:203`）落到这两个 client 实现，`handle_message` 是它们共同的入站分发口（先于线程池提交，拦截即零执行）；A 有五处埋点，新增入口（如工作站独立链路）会静默漏检；C 不覆盖不经过引擎的路径（且引擎层拿不到与租户隔离一致的用户上下文，违背 K6）。**注入方式（实现期确定）**：`common/` 禁止导入 domain 服务（C1 / RULE-1），检测逻辑不直连 `sensitive_word`——由 **api 层工厂 `api/services/guardrail.py::build_input_guard`** 构造闭包，经 `dispatch_client` 注入 client，4 个 ws 端点接线。
- **何时该重新考虑**：出现第三个 ws client 实现或绕过 ChatManager 的新对话链路时。

### 决策 3：输出检测点与增量匹配

- **备选**：
  - A. 埋点在 workflow LLM 节点（`workflow/nodes/llm/llm.py`）生成后——只覆盖工作流型应用的 LLM 输出。
  - B. 各输出链路的**真实发射口**就近埋点（实现期按实际帧形状确定）：workflow 流式分片在 `WorkflowClient._workflow_run` 事件循环（`RedisCallback` 产出 `ChatResponse` 模型，文本在 `message['msg']`）；助手流式 token 在 `AsyncStreamingLLMCallbackHandler.on_llm_new_token`（`api/v1/callback.py`，api 层可直连 domain）；HTTP 兼容接口输出在其端点（`open_endpoints/api/endpoints/assistant.py`）的响应生成处。
- **选定**：B，并配套给 `ACAutomaton` 新增带状态增量 API `find_all_matches(state, text, capture_at) -> (state_after, matches, captured_state)`——扫描状态自携带全局字符位置，跨分片续匹配；`capture_at` 可捕获 emit 边界处的中间状态（mask 缓冲窗口需要）。所有埋点共用同一检测门面（`ChatGuardService` + api 层工厂 `build_output_guard`），保证三条链路口径一致（spec AC-14）。**助手 client 与回调共享同一守卫闭包**（client 注入到回调，最终答案帧 `full_text()` 重写），避免双重检测/双重日志（design 坑 6）。
- **原因**：初版假设的 `send_response` 单收口与真实帧流向不符（实现期发现）：workflow 流式帧不经 `send_response` 而直接 `send_json`，助手 token 由回调直写 websocket——逐链路就近埋点更贴合现状且无架构改造。AC 自动机的匹配过程本身就是逐字符状态迁移，携带节点状态即可跨分片续匹配，**增量改造极小**（现有 `find_all` 每次从 root 开始——`ac_automaton.py`，无法直接用于流式）。HTTP 链路独立实现是现状（决策 2 已确认），与其改造其架构、不如在其出口就近埋点——埋点成本与检测口径由共享门面收敛。
- **何时该重新考虑**：若各链路重构为统一输出通道，多处埋点可合并回单点；若再新增输出链路（如批处理/异步生成通道），该通道需自行接入。

### 决策 4：动作（action）与检测选项的存储

- **备选**：
  - A. 给 `sensitive_word_policy` 表加列（`action`、`hit_threshold` 等）——需 Alembic 迁移。
  - B. 放进既有 `extra_config` JsonType 字段（`sensitive_word_policy.py:47`），一期约定 `{"action": "block"|"mask", ...}`。
- **选定**：B
- **原因**：零迁移、双 DB 安全（`dialect_helpers.JsonType` 已兼容 DM8）；一期动作语义只有 2 个枚举值，JSON 足够表达。加列与严格 schema 校验留到动作语义稳定后的二期（届时连同 `scope_type='flow'` 一起落列）。
- **何时该重新考虑**：动作类型扩展到需要列级索引/约束时。

### 决策 5：命中日志的写入方式

- **备选**：
  - A. 同步写 DB（检测点直接 INSERT）。
  - B. 异步 best-effort 写（后台 task / 队列），失败仅告警。
- **选定**：B
- **原因**：K4 性能约束下，对话关键路径不能背日志写延迟；spec AC-15 已明确日志失败不影响主流程。写法遵循 backend 错误处理规范：窄异常捕获 + 注释说明为何可忽略 + `logger.warning`。落点：`ChatGuardService` 检测命中时 `await` 直写（单行 INSERT、仅命中时发生），不做 `create_task` 游离任务——避免任务随请求取消而丢日志，且直写几毫秒只出现在命中这一罕见事件上。
- **何时该重新考虑**：若合规要求日志「先落库才放行」的强一致口径（届时改为同步写 + 失败放行开关）。

### 决策 6：输入被拦截时的返回形态

- **备选**：
  - A. ws 发 `error` 类消息帧 / HTTP 直接 400——前端需专门处理新帧类型。
  - B. ws 侧以**正常回复消息**（`end` 类型）返回配置话术，不执行对话；HTTP 侧返回 400 + 错误码 11201 + 话术。
- **选定**：B
- **原因**：client 端聊天 UI 天然渲染普通回复消息，**前端零改动**即可展示话术；聊天历史里也自然留痕（无需新消息类型进 history）。HTTP 兼容接口按 OpenAI 错误体例返回 400 + 错误码，调用方可编程处理。
- **何时该重新考虑**：若前端需要区分「被拦截」与「正常回答」做特殊样式（届时加 remark/类型标记，仍复用 end 通道）。

### 决策 7：策略作用域

- **备选**：
  - A. 一期即支持租户 + flow 两级（flow 级策略覆盖租户级）。
  - B. 一期仅租户级（沿用 F036 既有 `scope_type='tenant'`），flow 级二期。
- **选定**：B
- **原因**：F036 表虽已有 `scope_type/scope_id` 列，但 service 层只实现了 tenant 解析；flow 级需要「flow→tenant 回退链 + 策略修改的应用级缓存失效 + 前端应用级配置入口」，摊子显著变大。一期先把全链路围栏做透，flow 级作为明确排期项。
- **何时该重新考虑**：多应用共用租户但围栏口径差异成为真实诉求时（评估为二期第一优先）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**对话链路（ws）**：
`ws endpoint（api/v1/assistant.py:214 / api/v1/workflow.py:160 / open_endpoints 同构）→ ChatManager.dispatch_client（common/chat/manager.py:203）→ ChatClient._handle_message（common/chat/clients/base.py:62）→ workflow_client 提交执行（RedisCallback）→ 结果经 ChatClient.send_response（base.py:98）以 msg_type='stream'/'end' 分片回传 ws`

**对话链路（HTTP 兼容接口）**：
`POST /api/v2/chat/completions（open_endpoints/api/endpoints/assistant.py:31）→ 独立实现（不经 ChatManager）→ 同步/流式返回`

**敏感词检测现状（F036）**：
`SensitiveWordPolicyService.check_texts（sensitive_word_policy_service.py:252）→ 读策略（SensitiveWordPolicyDao.get_policy，同步 session）→ 词表解析（builtin words.txt lru_cache + custom 文本）→ ACAutomaton.find_all（ac_automaton.py:46，整段从 root 开始匹配）`
既有接入点仅两处：知识库文件解析（`knowledge/rag/knowledge_file_pipeline.py:113` 门禁 + `ContentSafetyTransformer` 命中抛异常，`transformer/content_safety.py:34`）与订阅文章（`channel_service.py:343` 命中限制查看）。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `SensitiveWordBusinessType` | Enum（`schemas.py:9`）：`knowledge_space_file_parse` / `channel_article` → 新增 `chat_input` / `chat_output` | 策略按业务场景分表（同一张表不同行） | 策略 API、检测点 |
| `extra_config`（chat 场景新增语义） | JSON：`{"action": "block"|"mask", "case_sensitive": bool, "max_hits": int}` | 一期动作语义放此，避免迁移（决策 4）；输出方向默认 `mask`，输入方向固定拦截 | 检测点、设置页 |
| 流式分片 | `send_response(category, msg_type, message, ...)` 中 `msg_type='stream'` 的文本 payload；最终回复 `msg_type='end'` | 输出检测的增量输入单元 | ChatClient 各调用方 |
| `sensitive_word_hit_log`（新表） | tenant_id / business_type / direction(input\|output) / flow_id / chat_id / user_id / hits(JsonType) / action / create_time / **source（F059 新增，String(16)，`tenant`\|`platform`，server_default `'tenant'`；NULL 按 `tenant` 处理——只可能来自发版中途的旧进程，反向兜底会抹掉租户有权看到的词）** | 命中监测日志，仅摘要不存原文（INV-11） | 平台日志页、审计 |
| 命中日志的**行粒度** | **F059 起每层各写一行**：同一分片同时命中两层时产生**两行**（各自 `source`），而不是合并成一行归给「胜出层」——合并会把另一个层的词从该层的视图里抹掉 | 检测点、日志页 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `sensitive_word/domain/services/sensitive_word_policy_service.py` | 策略读写、词表解析、自动机缓存、检测（新增 `build_matcher`/`WordMatcher` 门面，`check_texts` 重构复用同一构建链） | 不做对话链路感知（只认 tenant+business_type） |
| `sensitive_word/domain/services/chat_guard.py` | 对话围栏门面：`check_chat_input` / `start_output_guard` / `check_output_chunk`（block/mask 语义、跨分片缓冲、命中日志触发）。**F059 起为两层**：`OutputGuardState` 同时持有租户层与平台层**两套** matcher / `tail_state` / `last_counted_pos`（字段见 `chat_guard.py:72-78`），两层各自扫描、各自计数、各自写一行命中日志（靠 `source` 区分），判定时平台层优先 | 不做 ws/HTTP 埋点（埋点在调用方，Wave 2） |
| `sensitive_word/domain/services/ac_automaton.py` | AC 多模式匹配（新增带状态增量匹配 `find_all_matches` + `in_progress` 中间态判定） | 不读库、不管策略 |
| `common/chat/clients/base.py` | 消息分发（输入检测埋点）、流式发射（输出检测埋点） | 不定义策略语义 |
| `open_endpoints/api/endpoints/assistant.py` | `/api/v2/chat/completions` 输入检测埋点 + 400/11201 返回；同步/流式输出检测埋点（决策 3） | 不改 ws 链路 |
| `sensitive_word/api/endpoints/policies.py` | 策略 GET/PUT（既有端点，枚举自然扩展） | 不新增端点 |
| 命中日志 DAO（新增，domain/repositories 或 Dao 类） | 异步 best-effort 写 hit_log | 不参与检测判定 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `ACAutomaton.find_all` 每次从 root 开始匹配，**没有跨调用状态** | 流式逐分片调用会漏掉跨分片截断的敏感词（如「敏感」+「词」两个分片） | 新增 `find_all_matches(state, text, capture_at)`（状态自携带全局位置 + emit 边界中间态捕获），检测点按 chat 会话持状态（会话结束时丢弃） |
| 2 | **ChatManager 只收口 ws 对话**；`/api/v2/chat/completions` 是独立实现、不走 ChatManager | 只埋 ws 点会漏掉整个 OpenAI 兼容接口（输入、输出两条链路都会漏）——围栏有洞 | 该端点输入（决策 2）与输出（决策 3）均单独埋点，共用同一检测门面 |
| 3 | 策略修改的即时生效依赖 `clear_cache()`（`sensitive_word_policy_service.py:75`）：自动机缓存 key 含词表摘要，但**策略记录本身**（enabled/词表类型）也进了 key，改策略必须清缓存 | 漏清缓存 → AC-03 失败，管理员改了词表不生效 | 复用既有 aupsert 后的 clear_cache；检测点绝不自行缓存策略行 |
| 4 | `words.txt`（`domain/data/words.txt`）是**单行大文件**，靠 `load_builtin_words` lru_cache 加载 | 按行读会读到一行几十 KB；重复读文件拖慢检测 | 沿用既有 normalize_words + lru_cache，不另写读取逻辑 |
| 5 | mask（打码）时命中词可能跨分片：当前分片只有词的前半段 | 过早输出前半段 → 打码失败泄漏词头 | `OutputGuardState.tail` + `tail_state`：仅当自动机处于「可延展成更长模式」的中间态时缓冲尾部（≤ 最长词-1 字符，经 `capture_at` 捕获 emit 边界状态续扫），且命中词永不跨 emit 边界被拆开发出；跨分片重叠词按各自完成位置计数一次，与 F036 全量 `find_all` 语义一致（随机分片一致性用例锁定） |
| 6 | 输出截断后，chat 历史里保存的是截断后文本（`send_response` 带 add 参数负责入库） | 用户事后翻历史看不到被过滤了什么，以为模型没说完 | 截断收尾话术 + 命中日志留痕（日志含命中词），历史文本本身不含违规内容（合规上这是期望行为） |
| 7 | 新表 `sensitive_word_hit_log` 走 SQLModel `table=True` → 在线 alembic upgrade 前 `create_all(checkfirst=True)` 自动建表，**不需要 create-table revision**（backend AGENTS.md 整表创建规则） | 误写 migration 或以为要手动建表 | 按既有 domain 模块范式声明模型即可；DM8 兼容字段用 `dialect_helpers` |
| 8 | 检测必须在 API 层（有租户 ContextVar + login_user），ws 执行在 Celery worker 中**无 HTTP 租户上下文** | 在 worker 里读策略/写日志会拿错 tenant 或报缺上下文 | 输入检测在 `_handle_message`（API 进程）；输出检测在 `send_response`（同进程）；日志 tenant_id 从 login_user 取 |
| 9 | 错误码段 112 目前空闲（channel 模块占 190 段；`errcode/` 已用段见 release-contract 模块编码表） | 与既有模块撞号 → 用户收到错乱的错误提示 | 新增 `common/errcode/sensitive_word.py`，模块号 112：11201=输入命中拦截、11202=输出命中截断 |
| 10 | ws 消息分发里 `action` 有多种（init_data / check_status / input / stop），用户文本只出现在部分动作的 data 里 | 对控制帧做检测会误拦正常连接流程 | 只对用户文本消息 payload 检测（input 动作的 data 与首次消息），控制帧直通 |
| 11 | 租户自动过滤器的 `do_orm_execute` 会把 SELECT 包装成 **Row 元组**（字段名为实体名），`session.exec(select(Model))` 返回的不是实体 | 直接访问 `row.id` 抛 KeyError 'id'（e2e 实测 500），行取数全线崩溃 | `hit_log.py` alist 用 `row[0]` 解包实体（e2e 验证） |
| 12 | `api/v1/callback.py` 顶层导入 `api/services/guardrail.py` 会形成 import 环：guardrail → common/chat/__init__ → client.py → callback → guardrail | 应用启动即 ImportError（实测后端起不来） | callback 在 `_ensure_output_guard` 内惰性导入工厂打断环 |
| 13 | **mask 的打码与计数必须用两份不同的匹配列表**（F059 两层重构引入）：`*_all` = 本缓冲区**全部**匹配（供打码与 emit 边界判定）、`*_matches` = 仅水位线**之后**的匹配（供计数/写日志）。一个词可能上一分片已计数、被扣在 `tail` 里，本分片重扫时**又**被找到——它已过水位线故不在计数列表里，但**仍必须出现在打码列表里** | 用计数列表去打码 → 该词**原样吐出**（实测：F056 原本传的是未过滤列表，F059 重构时误传过滤后列表，成为静默泄漏回归） | `chat_guard.py:311` 起注释写明「conflating them is a leak」，两列表在 `:323`（平台层）与 `:333`（租户层）分别构造 |
| 14 | **block 截断点取两层中最早的命中**：若平台层命中但租户层同分片也命中，只截到平台词位置会把**租户敏感内容原样吐出**（租户策略在该路径上静默失效） | 平台层启用后租户围栏出现漏洞 | `chat_guard.py:365` 的 `first_start = min(... for pos, word, _ in all_seen)`，注释指明跨两层的取最早 |
| 15 | `state.auto_reply` 在 block 时被**重写**为胜出层的话术（平台命中则用平台话术），因为 API 层守卫读的是 `state.auto_reply` | 平台拦截却用租户话术收尾，话术与判定来源不符 | `chat_guard.py:375-377` |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `SensitiveWordBusinessType.chat_input / chat_output` 枚举扩展 + `extra_config.action` 语义 | Python API / 配置口径 | 平台设置页、检测点、二期语义 provider |
| `GET/PUT /api/v1/sensitive-word-policies/{business_type}`（既有端点，枚举扩展） | HTTP API | platform 前端「大模型安全围栏」设置页（复用 `controllers/API/sensitiveWordPolicy.ts` 模式） |
| `GET /api/v1/sensitive-word-hit-logs`（新增，方向/应用/时间筛选 + `PageData` 分页，租户管理员）。**F059 起返回值含 `source` 字段**，且 `source=platform` 的行其 `hits` 在**服务端**置空（`_to_hit_log_item(redact_platform_hits=True)`）——`hit_count` 保留，故租户能区分「围栏生效」与「系统故障」，但拿不到平台词库内容（INV-10） | HTTP API | platform 前端「命中日志」页 |
| `GET /api/v1/sensitive-word-hit-logs/all`（**F059 新增**，跨租户 + 可选 `tenant_id` 筛选，`require_global_super_admin`，**不脱敏**——平台管理员拥有词库本身，无遮蔽对象） | HTTP API | platform 前端平台级命中日志视图 |
| 错误码 11201 / 11202（`common/errcode/sensitive_word.py` 新增） | HTTP/ws 错误契约 | `/api/v2/chat/completions` 调用方、前端错误码文案（`api_errors` 域，三语） |
| `sensitive_word_hit_log` 表 | DB 表 | 平台日志页（只读）、审计 |
| `ACAutomaton.find_all_incremental` | 内部 Python API | 流式输出检测点（二期语义 provider 可复用） |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| F036 `sensitive_word` 模块（策略表/DAO/缓存/词表） | 内部 Python API | 若 F036 未来改缓存或表结构，本 feature 的缓存语义（决策 4/坑 3）需同步 |
| 所有 ws 对话输出必经 `ChatClient.send_response` | 隐式数据流契约 | 若新增绕过 ChatClient 的输出通道（如新客户端类型），围栏静默失效——新增通道需自查本 feature |
| ws 消息分发动作集（`_handle_message` 的 action 约定） | 隐式消息契约 | 若新增携带用户文本的 action 类型，输入检测需同步覆盖 |
| 错误码分配表（release-contract 模块编码） | 流程契约 | 提交前登记模块 112，避免撞号 |

---

## 7. 测试与可观测

- **单元**：AC 增量匹配（跨分片词、最长词边界、多词命中）、词表解析边界（空/重复/大小写）、action 语义（block 截断点 / mask 等长打码）、策略「无效即直通」矩阵（未启用/空词表/读策略异常）。
- **集成**：ws 输入拦截（不调模型、返回话术、日志落库）；`/api/v2/chat/completions` 输入拦截（400 + 11201）；流式输出截断（伪造流分片，验证命中分片即停、话术收尾、历史入库为截断后文本）。
- **e2e**：`/e2e-test features/v2.7.0/056-llm-guardrail`——配置策略 → 助手/工作流对话输入命中 → 验证话术回复与日志；未配置租户全入口回归（AC-17）。**注意**：AC-02/AC-17 的回归用例需在**平台级强制词库未启用**的前提下运行（F059 上线后，平台层启用会使「未配置租户行为不变」不成立）。
- **手动验证**（本地环境 172.19.70.141：平台 :3001 / 客户端 :4001 / 后端 :7860，以租户管理员账号登录平台）：
  - 平台 `http://172.19.70.141:3001` → 安全围栏设置页：开关 / 词表（内置+自定义）/ 动作（block/mask）/ 话术 CRUD；改词表后立即生效（AC-03）
  - 客户端 `http://172.19.70.141:4001/workspace` → 发起对话：输入命中验证话术回复且无模型调用（AC-05）；输出命中验证截断（AC-10）/ 打码（AC-11）
  - `POST http://172.19.70.141:7860/api/v2/chat/completions`：输入命中返回 400 + 11201（AC-08）；`stream=true` 验证输出实时截断（AC-14）
  - 平台日志页按时间 / 方向 / 应用筛选命中记录（AC-15）
- **可观测**：命中事件 `logger.info`（含方向、flow_id、命中词数，不含原文）；检测耗时 debug 日志（性能回归用）；日志写失败 `logger.warning`；平台日志页兜底展示。

---

## 8. 后续改进 / 不打算做的事

| 事项 | 说明 |
|---|---|
| 语义级内容安全（谐音/变形/上下文） | 可插拔 provider（第三方内容安全 API 或本地模型），在 AC 未命中时做二次判定；本设计的 action/日志结构已兼容 |
| flow 级策略作用域 | `scope_type='flow'` + flow→tenant 回退链 + 应用级缓存失效（决策 7） |
| `action=audit` 仅审计不拦截 | extra_config 扩展即可，等合规口径明确 |
| 命中日志接入 telemetry pipeline | 现为一期自建表；量大后可并流到既有遥测体系 |
| 多模态（图片/文件）内容检测 | 一期仅文本；文件内容检测沿用 F036 知识库管线口径 |

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-07 | 初版（待 /sdd-review design 评审） | 需求评估结论落档：F036 基础设施复用 + 对话链路双检测点 |
| 2026-09-08 | Wave 0-1 实现落库：增量 API 定稿 `find_all_matches`（决策 3/坑 1/坑 5 更新）；`build_matcher` 门面抽取（§4.3 + chat_guard 行）；日志写入改为命中时 await 直写（决策 5）；错误码 11201/11202 与 hit_log 表落地（T001/T002） | T003-T006 实现期发现：坐标传递易错改由状态自携带；日志游离任务生命周期不可控 |
| 2026-09-08 | Wave 2 实现落库：ws 双 client 事实校正 + RULE-1 注入方案（决策 2 更新）；输出埋点按真实帧流向三链路落点（决策 3 更新：`_workflow_run` 事件 / 助手回调 / HTTP 端点，共享守卫闭包）；输入拦截返回帧定稿为 `over`/`end`（决策 6B 落地） | T007-T012 实现期发现：助手存在独立 ChatClient 实现；`send_response` 非流式收口；回调与 client 双重守卫风险 |
| 2026-09-08 | Wave 4 实现落库：e2e 6/6（真实链路：策略 round-trip / HTTP 400+11201 / 命中日志无原文）；/code-review 修复 2 项（block 分支 emit_len 防御性下限；workflow_client 鸭子类型消除新增 RULE-1 违规）+ 坑 11/12 落档（Row 元组解包 / 循环导入惰性导入） | e2e 与 code-review 暴露：租户过滤 Row 包装、guardrail↔callback 循环导入、workflow_client 类型导入违宪 |
| 2026-09-08 | Wave 3 实现落库：命中日志查询端点补充（§6.1，tasks 拆解遗漏）；平台「安全围栏」tab（策略配置 + 命中日志页，`DialogueWork` 新 tab）；i18n 三语（bs.json guardrail 组 + api_errors 11201/11202） | T013-T017 实现期：前端控制器按拦截器解包语义类型化（勿二次 .data 解包，F055 T-19 口径） |
| 2026-09-18 | **code-review 执行**（报告：[code-review-report.md](./code-review-report.md)）。代码维度全通过（跨分片匹配与 mask 泄漏经穷举实测）；HIGH 两条均在测试覆盖与文档同步：① AC-05/10/11/12/14 的真实链路验证依赖**从未执行**的手动清单；② design 未同步 F059 两层现状 | 本次审查发现 F059 落地后本 design 已滞后：检测链路变为两层，但 §4.2/§4.3/§6.1 仍按单层描述 |
| 2026-09-18 | **回写 F059 两层现状**：§4.2 补 `source` 字段与「每层各写一行」的行粒度；§4.3 补双 matcher/双 tail_state；§5 新增坑 13（mask 必须用未过滤列表，否则泄漏词）、坑 14（block 取两层最早命中）、坑 15（auto_reply 按胜出层重写）；§6.1 补 `/all` 跨租户端点与平台词服务端脱敏口径 | 上条 code-review 的 H2/M3 文档漂移 |
