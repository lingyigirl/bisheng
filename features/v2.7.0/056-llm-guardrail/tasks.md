# Tasks: 大模型安全围栏（056-llm-guardrail）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-07 /sdd-review spec 通过（两处 low 已修复后复审） |
| design.md | ✅ 已评审 | 2026-09-07 /sdd-review design 通过（决策 3 HTTP 流式输出锚点 + §7 验证入口已补） |
| tasks.md | ✅ 已拆解 | 2026-09-07 拆解完成：19 个任务 / 5 个 Wave，18 条 AC 全覆盖 |
| 实现 | ✅ 完成 | 20 / 20 完成。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |
| e2e-test | ⚠️ 部分执行 | 自动化 e2e 覆盖 AC-01/02/03/06/07/08/15/17（需活后端，CI 跑）。**AC-05/10/11/12/14 的真实 ws/流式链路验证落在 [e2e-checklist.md](./e2e-checklist.md) 的 7 项手动清单，状态仍为「待执行」** —— 见 code review 的 H1 |
| code-review | ✅ 已执行 | 2026-09-18。代码维度（边界/权限/并发/泄漏/风格）**全通过**；跨分片匹配与 mask 泄漏经穷举实测无误。**H1**：核心 AC 的真实链路验证依赖未执行的手动清单；**H2**：design 未同步 F059 两层现状（**已回写**）。报告：[code-review-report.md](./code-review-report.md) |

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 0/1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**后端 Test-First（务实版）**：
- 理想流程：先写测试（红），再写实现（绿）
- 测试基础薄弱时，首个测试任务一并搭 pytest 基础设施（conftest.py、db fixture、mock helpers）
- 测试编写成本极高（如需完整 Milvus/ES mock）可标注 `**测试降级**: 手动验证 + TODO`，并在「实际偏差记录」说明
- 中间件 / DM8 / e2e 测试在 **CI** 跑，不依赖本地（见 `docs/SDD-Guide.md` §6）

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（`docs/SDD-Guide.md` §6，🚧 规划中）—— 落地后前端任务改写自动化断言。

**自包含任务**：每个任务内联文件、逻辑、AC 覆盖，实现期不必回读 spec。但**设计论证（为什么这么做）指向 design §X，不复制**（避免第三处漂移）。

**本 feature 特殊口径**：
- **无 Worker/Celery 任务**：检测必须在 API 层发生（有租户 ContextVar 与 login_user，design K6）；命中日志为进程内异步 best-effort 写（design 决策 5），不走队列。
- **前端 Client 零改动**：ws 拦截以普通 `end` 回复消息返回话术（design 决策 6B），client 聊天 UI 天然渲染；11201 仅面向 `/api/v2` 的 API 调用方。

---

## Tasks

### Wave 0 — 基础设施（后端，无测试配对，可并行）

- [x] **T001**: 错误码定义
  **文件**: `src/backend/bisheng/common/errcode/sensitive_word.py`
  **逻辑**: 定义 `BaseErrorCode` 子类 `SensitiveWordInputBlockedError`（Code=11201，附默认话术与 `return_resp()` 用法）、`SensitiveWordOutputTruncatedError`（Code=11202）。模块编码 112 已在 release-contract「已分配模块编码」登记，本任务只落代码。
  **依赖**: 无

- [x] **T002**: 命中日志 ORM 模型 + DAO
  **文件**: `src/backend/bisheng/sensitive_word/domain/models/hit_log.py`
  **逻辑**: 定义 SQLModel `SensitiveWordHitLog`（`table=True`，继承 SQLModelSerializable）：`tenant_id`（索引）、`business_type`（chat_input/chat_output）、`direction`（input/output，冗余便于查询）、`flow_id`/`chat_id`（可空）、`user_id`、`hits`（JsonType，`[{"word","count"}]` 摘要，**不存原文**）、`action`（block/mask）、`create_time`。DAO 提供 `acreate_async_best_effort(...)`——内部 `try/except Exception` 窄捕获 + `logger.warning`（design 决策 5 / spec AC-16）。字段类型一律 `dialect_helpers.JsonType`（C2）。
  **说明**: 新表走 `create_all(checkfirst=True)` 自动建表（backend AGENTS.md 整表创建规则），不写 Alembic revision。**回滚方案**：新增独立表无数据依赖，回滚仅需 `DROP TABLE sensitive_word_hit_log`；但删除即丢弃监测审计历史，回滚前需业务确认（合规口径）。
  **依赖**: 无

### Wave 1 — 检测引擎与对话门面（后端 Domain，Test-First）

- [x] **T003**: AC 自动机增量 API 单元测试
  **文件**: `src/backend/test/sensitive_word/test_ac_automaton_incremental.py`（目录不存在则本任务创建，并搭 `conftest.py` 基础 fixture——本模块单测以纯内存为主，无需 DB fixture）
  **逻辑**: 测 `find_all_incremental` 语义——分片边界切词（「敏感」+「词」两分片仍命中 1 次）、分片中间切词、跨 3+ 分片长词、多词命中计数、空分片、大小写归一（case_sensitive=False）、状态独立（不同 state 互不污染）、与既有 `find_all` 整段结果一致（对随机分片切法的计数和不变性）。
  **覆盖 AC**: AC-10, AC-12
  **依赖**: 无（先测后写，直接红在未实现的 API 上）

- [x] **T004**: AC 自动机增量 API 实现
  **文件**: `src/backend/bisheng/sensitive_word/domain/services/ac_automaton.py`
  **逻辑**: 新增 `find_all_incremental(state, text) -> tuple[Counter, state]`——`state` 为当前节点指针（None=root），逐字符迁移复用 `_Node.fail` 链，返回命中计数与新状态；既有 `find_all` 保留原语义（整段匹配），内部可委托增量 API（state=None → 从头）。不改 `_insert`/`_build_fail_links`。
  **测试**: T003 全部通过；既有 sensitive_word/knowledge 相关用例不破。
  **覆盖 AC**: AC-10, AC-12
  **依赖**: T003

- [x] **T005**: 对话门面（ChatGuardService）单元测试
  **文件**: `src/backend/test/sensitive_word/test_chat_guard.py`
  **逻辑**: mock DAO 层（`SensitiveWordPolicyDao` 与 hit_log DAO）测 `ChatGuardService`：
  - 策略解析：未启用 / 词表空 → `enabled=False` 直通（AC-02）；builtin+custom 合并去重；`extra_config.action` 解析（block/mask、非法值降级默认）
  - 租户隔离：不同 tenant_id 返回各自策略（AC-04）
  - 修改即时生效：upsert 后 automaton 缓存失效、新词立即命中（AC-03，依赖既有 `clear_cache`）
  - 命中日志：输入/输出命中各触发一次写（含 hits 摘要形状断言）（AC-07, AC-13）；日志写抛异常 → 检测结果不受影响（AC-16）
  - 输入方向动作恒为 block（忽略 action 配置，design §4.2）
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-07, AC-09, AC-13, AC-16
  **依赖**: T004

- [x] **T006**: 枚举扩展 + ChatGuardService 实现
  **文件**: `src/backend/bisheng/sensitive_word/domain/schemas.py`（枚举扩两个值，1 行）、`src/backend/bisheng/sensitive_word/domain/services/chat_guard.py`（新文件）
  **逻辑**: `SensitiveWordBusinessType` 新增 `chat_input` / `chat_output`。`ChatGuardService` 提供：
  - `check_chat_input(tenant_id, text) -> SensitiveWordCheckResult`——委托 `SensitiveWordPolicyService.check_text(business_type=chat_input)`；命中判定与既有语义一致
  - `start_output_guard(tenant_id) -> OutputGuardState`（惰性解析策略/自动机，策略无效返回 `enabled=False` 的直通 state）
  - `check_output_chunk(state, chunk) -> GuardedChunk`——增量匹配（T004 API），返回 `{safe_text, hits, blocked}`：未命中原样返回（AC-12）；命中按 action 执行 block（截断 + 标记收尾）或 mask（等长 `*` 打码，含跨分片词头缓冲窗口，design 坑 5）；命中即触发 hit_log best-effort 写（T002）
  - 状态按 chat 会话持有时长由调用方管理（design 决策 3），service 无状态
  **测试**: T005 全部通过
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-07, AC-09, AC-13, AC-16
  **依赖**: T002, T004, T005

### Wave 2 — 对话链路埋点（后端 API/chat 层，Test-First）

- [x] **T007**: ws 输入检测测试
  **文件**: `src/backend/test/sensitive_word/test_chat_client_input_guard.py`
  **逻辑**: mock `SensitiveWordPolicyService`/`ChatGuardService` 与下游执行，直接驱动 `ChatClient._handle_message` 分发前检测逻辑：
  - 输入命中 → 不进入既有 action 分发、不回执行（断言执行调用数为 0），以 `end` 消息返回策略话术（AC-05）
  - 未命中 → 原样进入分发、执行正常（AC-06）
  - 未配置 / 词表空 → 直通（AC-02）
  - 命中日志写入一次（AC-07）
  **覆盖 AC**: AC-02, AC-05, AC-06, AC-07
  **依赖**: T006

- [x] **T008**: ws 输入检测埋点实现
  **文件**: `src/backend/bisheng/common/chat/clients/base.py`
  **逻辑**: `_handle_message` 入口处（消息分发前）对携带用户文本的 action（`input` 的 data 与首条消息）调用 `check_chat_input`（design 坑 10：控制帧直通）；命中 → `send_response` 以 `end` 类型回话术并 return（不执行），同时 best-effort 落日志；未命中 / 策略无效 → 零改动直通（K3）。**共享文件影响范围**：所有 ws 对话（助手/工作流，v1/v2）都经此文件——埋点为「策略无效即直通」，未配置租户行为逐字不变（AC-17 回归口径）。
  **测试**: T007 全部通过
  **覆盖 AC**: AC-02, AC-05, AC-06, AC-07
  **依赖**: T007

- [x] **T009**: ws 输出检测测试
  **文件**: `src/backend/test/sensitive_word/test_chat_client_output_guard.py`
  **逻辑**: 驱动 `send_response` 输出检测逻辑（mock 门面）：
  - 流式命中 → 该分片起截断，后续分片不再外发，以话术收尾（AC-10）；跨分片词命中（利用 T003 语义）
  - mask → 命中词等长 `*`，其余原样继续（AC-11）
  - 未命中 → 输出文本逐字一致（AC-12）
  - 输出策略独立于输入策略（AC-09：输出命中但输入策略未配 → 输入不受影响）
  - 命中日志写入一次（AC-13）；`end` 最终消息同样受检（非流式口径）
  **覆盖 AC**: AC-09, AC-10, AC-11, AC-12, AC-13
  **依赖**: T006

- [x] **T010**: ws 输出检测埋点实现
  **文件**: `src/backend/bisheng/common/chat/clients/base.py`（与 T008 同文件，不同函数，顺序执行）
  **逻辑**: `send_response` 文本分片（`msg_type='stream'` 文本 payload 与最终 `end` 消息）过 `OutputGuardState`（`ChatClient` 实例属性，每会话生命周期，会话结束丢弃）；block 命中 → 截断后补发话术 `end` 消息（chat 历史入库为截断后文本，design 坑 6）；mask → 打码分片原路径外发（缓冲窗口 ≤ 最长词长度，design 坑 5）。**影响范围**同 T008：未配置租户逐字不变。
  **测试**: T009 全部通过
  **覆盖 AC**: AC-09, AC-10, AC-11, AC-12, AC-13
  **依赖**: T008, T009

- [x] **T011**: HTTP 兼容接口集成测试
  **文件**: `src/backend/test/sensitive_word/test_open_endpoints_guard.py`
  **逻辑**: TestClient 测 `POST /api/v2/chat/completions`：
  - 输入命中 → HTTP 400 + 错误码 11201 + 响应体带话术（AC-08），断言未进入执行链
  - 输入未命中 → 正常链路（AC-06 口径）
  - 输出检测（同步与 `stream=true`）——**测试降级**: 完整链路需真实模型/工作流执行，成本高；本任务只测「检测门面在端点内的调用接线」（mock 检测层断言被调 + 命中分支返回形态），端到端输出截断行为由 T017 e2e + 手动验证覆盖
  **覆盖 AC**: AC-06, AC-08, AC-14
  **依赖**: T006

- [x] **T012**: HTTP 兼容接口埋点实现
  **文件**: `src/backend/bisheng/open_endpoints/api/endpoints/assistant.py`
  **逻辑**: `/api/v2/chat/completions` 执行前 `check_chat_input`，命中 → `SensitiveWordInputBlockedError` 的 `return_resp()`（HTTP 400 + 11201 + 话术）；输出侧在响应生成处埋点（同步整段 + `stream=true` 逐分片增量，design 决策 3）：block → 截断补话术；mask → 打码续流。**影响范围**：仅该端点，ws 链路不动（design 坑 2）。
  **测试**: T011 全部通过
  **覆盖 AC**: AC-06, AC-08, AC-14
  **依赖**: T011

### Wave 3 — 前端 Platform（手动验证）

- [x] **T013**: Platform API 控制器扩展 + 命中日志 API
  **文件**: `src/frontend/platform/src/controllers/API/sensitiveWordPolicy.ts`（扩展）、`src/frontend/platform/src/controllers/API/sensitiveWordHitLog.ts`（新）
  **逻辑**: 策略控制器补充 `chat_input`/`chat_output` 两个 business type 的类型与常量；新控制器封装日志查询（时间/方向/应用筛选 + `PageData` 分页）与 `SensitiveWordHitLogItem` 类型。**注意既有坑**：axios 拦截器已解包 envelope，响应直接按裸对象建模，勿二次 `.data` 解包（F055 T-19 复盘）。
  **覆盖 AC**: AC-15
  **依赖**: T012

- [x] **T014**: i18n — api_errors 域错误码文案
  **文件**: `src/frontend/packages/locales/src/api_errors/{zh-Hans,en-US,ja}.json`（源文件，生成产物不手编、CI 校验）
  **逻辑**: 新增 11201（对话输入命中敏感词被拦截）与 11202（对话输出命中敏感词被截断/打码）三语文案，key 与后端错误码一致。**不新增中文硬编码**。
  **覆盖 AC**: AC-08, AC-15
  **依赖**: T001

- [x] **T015**: i18n — 页面 UI 文案三语
  **文件**: `src/frontend/platform/public/locales/{zh-Hans,en-US,ja}/bs.json`
  **逻辑**: 新增安全围栏设置页与命中日志页 UI key（页面名、开关、词表类型、动作 block/mask、话术、方向、筛选字段、空态等），三语 key 对齐。
  **覆盖 AC**: AC-15
  **依赖**: T013

- [x] **T016**: 安全围栏设置页
  **文件**: `src/frontend/platform/src/pages/BuildPage/bench/LLMGuardrailPolicy.tsx`（新，参考 `SubscriptionSensitivePolicy.tsx` 模式）
  **逻辑**: 租户级策略配置页——「对话输入」「对话输出」两个分区（独立开关/词表类型内置+自定义/话术；输出额外动作 block|mask 单选）；保存走既有 `PUT /api/v1/sensitive-word-policies/{business_type}`；页面文案全走 i18n（T015 key）。入口挂到既有「设置」导航（bench 区域，与订阅敏感词策略并列）。
  **覆盖 AC**: AC-01
  **手动验证**:
  - 打开 `http://172.19.70.141:3001` → 安全围栏设置页
  - 配置输入策略（开启 + 自定义词「测试词」+ 话术），保存后刷新页面值保留（AC-01）
  - 改词表保存后，立刻去对话入口验证新词生效（AC-03）
  - 未启用状态保存 → 对话不受影响（AC-02）
  **依赖**: T013, T015

- [x] **T017**: 命中日志页
  **文件**: `src/frontend/platform/src/pages/BuildPage/bench/LLMGuardrailHitLogs.tsx`（新）
  **逻辑**: 日志列表——时间/方向（输入|输出）/应用筛选 + 分页；行展示时间、方向、应用、命中词（chips）、次数、动作；空态提示；**不展示对话原文**（spec AC-15 口径，后端本就只存摘要）。文案全走 i18n（T015 key）。
  **覆盖 AC**: AC-15
  **手动验证**:
  - 先触发一次命中（见 T016 验证步骤），打开 `http://172.19.70.141:3001` 日志页
  - 按方向筛选命中记录，词/次数/动作与触发时一致
  - 无记录时空态正常
  **依赖**: T013, T015

### Wave 4 — e2e 与收尾

- [x] **T018**: e2e 测试与验证清单
  **文件**: `src/backend/test/e2e/test_e2e_f056_llm_guardrail.py`、`features/v2.7.0/056-llm-guardrail/e2e-checklist.md`
  **逻辑**: 按 `/e2e-test` 双层策略生成——API e2e（pytest+httpx，复用 `test/e2e/helpers/auth.py` 登录）：
  - AC-05/07：ws 对话输入命中 → 话术回复 + 日志表新增一行（不含原文）
  - AC-08/14：`/api/v2/chat/completions` 输入命中 400+11201；`stream=true` 输出命中截断
  - AC-17：未配置租户全入口回归（ws + HTTP 正常对话）
  - AC-18：既有知识库解析/订阅文章敏感词用例不破（回归）
  - AC-03/10/11/12 依赖真实流式链路，落 `e2e-checklist.md` 手动清单（配合 T015/T016 验证步骤）
  **覆盖 AC**: AC-03, AC-05, AC-07, AC-08, AC-10, AC-11, AC-12, AC-14, AC-17, AC-18
  **依赖**: T012, T016, T017

- [x] **T019**: /code-review 收尾
  **逻辑**: 全量实现完成后执行 `/code-review --base main`（7 维度），修复 high/medium；同步 design.md 实际偏差（修订历史 + 坑补充）；ruff/typecheck/lint 全绿后本任务完结，tasks 状态改 ✅ 已拆解。
  **依赖**: T018

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- T003/T004 增量 API 定稿为 `find_all_matches(state, text, capture_at)`（state 自携带全局位置 + 可捕获 emit 边界中间态），非原计划的 `(Counter, state)` → 更新 design 决策 3 / 坑 1
- T006 文件清单 3 → 4：`build_matcher`/`WordMatcher` 落 `sensitive_word_policy_service.py`、`in_progress` 落 `ac_automaton.py` → 更新 design §4.3
- T006 `check_output_chunk` 为 async（命中时 await 直写日志，单行 INSERT，不做 create_task 游离任务）→ 更新 design 决策 5
- T007/T008 事实校正：ws 存在**两个** client 实现（`base.py`/WorkflowClient 与 `client.py`/ChatClient 助手）；且 RULE-1 禁止 common 导入 domain → 检测逻辑改由 **api 层工厂（`api/services/guardrail.py`）+ `dispatch_client` 注入**，埋点位置不变（handle_message 收口）；4 个 ws 端点接线 → 更新 design 决策 2 / 坑 10
- T009/T010 输出落点修正：workflow 流式帧经 `_workflow_run`（ChatResponse 模型、文本在 `message['msg']`）、助手流式 token 经 `AsyncGptsLLMCallbackHandler`（api 层直连 domain 合法）；最终答案帧以 `full_text()` 重写；助手 client 与回调**共享同一守卫闭包**避免双重检测/日志 → 更新 design 决策 3 / 坑 5/6
- T012 HTTP 流式：mask 缓冲尾部在流结束后作为最后一个 delta 分片外发（flush 契约）→ 更新 design 决策 3
- T018 实现期修复：命中日志查询 500（租户自动过滤器把 SELECT 包装成 Row 元组, 需 `row[0]` 解包实体）+ v2 端点真实路径为 `/api/v2/assistant/chat/completions`（router 带 `/assistant` 前缀）+ 循环导入修复（callback 惰性导入 guardrail 工厂, 打断 `guardrail → common/chat/__init__ → client → callback` 环）→ 更新 design 坑 11（新增）
- T013a（拆解遗漏补充）：命中日志**查询端点**后端缺失——补 `GET /api/v1/sensitive-word-hit-logs`（DAO alist + ChatGuardService.alist_hit_logs + endpoint + 2 测试，PageData 分页、租户管理员权限）→ 更新 design §6.1
