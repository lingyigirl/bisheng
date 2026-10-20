# F077 定时/事件触发与定时任务生成 — Spec Discovery 记录

> 调研日期 2026-10-16。本文记录 spec 动笔前的现状调研与关键决策点（★ 待用户确认；确认后按 076 口径以 ✅ 标注）。
> 决策依据进入 spec.md；实现细节在后续 design.md。
> 对应条款：北京农商银行《智能体平台建设项目供应商征集公告》**（五）1**「支持多渠道发布与任务触发：可一键发布至 API、Agent Portal 等渠道；定时/事件触发；手动或自然语言生成定时任务。」
> **条款四子项边界**：a「一键发布至 API 渠道」/ b「发布至 Agent Portal 等渠道」＝**两侧已有基础，本特性不做渠道侧改动**，仅作集成点消费（台账 §4.1/§4.2 证据）；c「定时/事件触发」/ d「手动或自然语言生成定时任务」＝**本特性交付对象**。
> 特性编号 **077**（`features/v2.7.0/` 现最大 076；2026-10-16 核验 077 空闲；目录 `077-scheduled-triggers`）。
> 前置证据：`agents_logs/architecture/2026-10-16/2026-10-16_北京农商行征集公告_条款清单与覆盖台账.md` §4；`features/v2.7.0/release-contract.md`（表 1/2/3、错误码 118 已占用 → 119 候选）；`docs/constitution.md`（C1–C7）；`features/v2.7.0/076-openapi-auth/discovery.md`（格式样板与 F076 边界）。
> 上游参照（只取口径、不移植）：`dataelement/bisheng` 钉 SHA `ca4ca8bf0e30b2b456d2f49eaea9dbe35f5ac4a5`（3.0.0-beta4 线）。F108「平台触发/调度子系统」＝**只有 PRD 规格、无任何实现**（迁移目录 `schedul|trigger|armed` 零命中），登记为净新建并 defer。
> **本文的产生方式（可复核）**：6 面并行只读探查（执行基座 / 上游 / 权限与身份 / cron 与自然语言 / 前端与可观测 / 渠道集成）合成草稿 → **2 路对抗审查**（需求完整性 / 技术风险与证据）→ **4 面验真与补写**（high 发现逐条验真、错误引用订正、10 个缺口决策点补写）。审查与验真**改掉**了草稿的 4 处事实/引用错误并补上一条被整体漏掉的约束面（F041 知识可见性），逐条见**附录 A**；正文所引证据均经二次核验。
>
> **★ 确认记录（2026-10-16，用户）**：**D1 = B**（cron + 入站 webhook）、**D3 = C**（服务账号专用身份）、**D8 = A**（监控 + 站内信）、**D17 = B**（支持一次性任务）四项已确认 ✅；其余 15 条（D2 / D4–D7 / D9–D16 / D18–D19）经用户确认**全部按 §2 推荐口径入 spec** ✅。
> **开发分支**：`feat/2.7.0/beijing-ns-077-wip`（2026-10-16 自 `beijing-ns-076-wip` HEAD 起；SDD 文档与代码同线，按需合入 `beijing-ns` 交付线）。

---

## 0. 一句话范围

在 `beijing-ns` 交付线**自研**实现：为平台应用（工作流 / 助手）提供 **cron 定时触发**（触发类型范围 ★D1 待确认）与 **定时任务生成**（手动表单 + 自然语言→草稿→**人工确认**→落库），执行复用既有工作流/助手运行链，以 **F076 服务账号专用身份**无人值守运行（非特权、创建时锁定、触发方不得指定身份），并配套**任务管理面、权限收口、运行租约与回收、失败闭环、审计与可观测**；**渠道侧（API 发布 / 免登录页 / iframe / 分享链）不做任何改动**——那是已有基础，本特性只把它当集成点消费；**不移植上游 F108**；**不引入任何新重型依赖**（cron 求值用 celery 自带 `crontab`，容错解析用已在依赖中的 `json_repair`）。

---

## 1. 现状调研结论（可复用 vs 需新建）

**总括**：执行基座已够用（工作流 RedisCallback + Celery + stateful 续跑 + F054 无人值守样板；F076 专用身份装载 + 租户 ContextVar 传播 + 跨租户 Beat 范式），cron 求值与容错解析**零新增依赖**；缺的是一条**调度控制面**：调度表、固定节拍扫描与原子 claim、**运行租约与 stale 回收**、身份与权限绑定（创建期校验 + 触发期复检）、生成流程（手动 + 自然语言）。「动态 cron 注册进 beat」在本仓**不可行**（beat 全静态、无 redbeat/apscheduler/croniter），只能在「固定节拍扫描（表驱动）」与「独立常驻进程」之间取舍（★D4）。

### 1.1 执行基座、运行监督与"超时"的真相

**可复用**
- 工作流运行三元组与 Redis 控制面：`worker/workflow/tasks.py:100-127`（`execute_workflow(unique_id, workflow_id, chat_id, user_id, source)`）、`worker/workflow/redis_callback.py:60-85`（`workflow:{unique_id}:{data,status,event,input,stop}`）；`status` key 固定 TTL 7 天（`:139`）、`stop` 24h（`:527`）。
- **F054 评测链＝现成的"编程式无人值守工作流运行"样板**（含 INPUT 答复、失败隔离）：`evaluation/domain/services/evaluation_service.py:427-508`。它证明答复协议是**跨进程**的（Redis 写 `input` + 把 `continue_workflow` 投递到同 hash_key 绑定的 worker 节点，`:444-447、:474-476`），监督者跑在 **API 进程**的 BackgroundTask（`evaluation/api/endpoints/evaluation.py:107,156`）。
- 租户传播：Celery header 注入/恢复 `worker/tenant_context.py:21-46`；「发布前 stamp 租户」`worker/knowledge/scheduler.py:521-535`；跨租户 Beat 遍历 `worker/information/reconcile.py:21-79`。
- Beat 基建与 file_scheduler 先例（Redis 锁 / 队列配额 / 事件触发 + beat 兜底 / reconcile）：`worker/knowledge/scheduler.py:308-322,557-571,572-616`；`core/config/settings.py:260-269`。
- 身份装载（非交互）：`open_api/domain/services/identity_service.py:35-66`（`load_owner_payload`）+ `:98-102`（`owner_is_privileged` 防提权）；默认操作员装载 `open_endpoints/domain/utils.py:27-124`。
- cron 求值：celery `crontab`（`core/config/settings.py:8,191` 已在用；`from_string` + `remaining_estimate`，实测边界见 §4）。

**「运行超时」的真相（本轮最重要的订正）**
- `WorkflowConf.timeout`（`core/config/settings.py:164-168`，代码默认 720）**不是运行超时**：它的**唯一真实消费点**是运行控制面 Redis key 的 TTL（`timeout*60+60` 秒，`redis_callback.py:85`）；随仓部署值由 `initdb_config.yaml:89-93` 提供为 **5（分钟）**（注释即「等待用户输入的超时时间」）。`Workflow(timeout)` 在 `workflow/graph/workflow.py:40` 只是**存下不读**（全仓无第二处读取），嵌套子流程（`agent_flow.py:147`、`loop.py:194`）同。
- 佐证：`WorkFlowWaitUserTimeoutError`（10527，`common/errcode/flow.py:30-32`）的**生产者全仓不存在**——"等待输入超时"从未实现。
- **无 celery 级限制**：`tasks.py:100,155` 是裸 `@bisheng_celery.task`（对照 `worker/approval/tasks.py:16-17` 显式 `time_limit=900/soft_time_limit=840`），`worker/config.py` 无全局 `task_time_limit`。
- **无僵尸运行清扫**：卡死判别只存在于**消费侧轮询循环**（`redis_callback.py:238-307` 的"WAITING 超 10s"/"状态超 24h"两条），无人值守且无监督者时**没有观察者**；worker 崩溃/重启后任务不重投（无 `acks_late`），status key 停在 RUNNING/INPUT 直至 7 天 TTL。worker 下所有 reconcile 任务都不触碰 workflow 运行。
- **`stop_workflow` 自身可无限等待**：`tasks.py:198-199` 的 `while status()==RUNNING: sleep(0.3)` 无超时。
- ⇒ **F077 必须自建**：运行租约（`started_at` + 心跳/stale 判定）+ 回收（到期未终态 → 置 failed 并**释放任务互斥**），否则 ★D6 的"单任务互斥 + 上一轮未结束则跳过"叠加"零回收"＝**一次卡死该定时任务永久静默停摆**。

**需新建**：调度表（租户 + cron/事件源 + 目标应用 + 运行身份 + 状态与 `last_*` + 租约字段）；扫描/claim 派发器；**运行监督器**（D12 与回收依赖它）；防重锁/并发闸；非交互身份绑定（创建期校验 + 触发期复检）；事件触发接收面（仅当选 D1=B，现状**零 inbound webhook**）；NL→cron 生成链路。唯一 `manual/cron` 字段先例 org_sync 是无运行时的配置外壳（`org_sync/domain/models/org_sync.py:112-131`，`aget_active_cron_configs` `:403-410` **全仓无调用方**）。

### 1.2 权限与身份

**可复用**
- 应用权限位与档位映射（唯一权威表）：`permission/domain/application_permission_template.py:15-47`（viewer=`view_app`；editor=+`use_app`/`edit_app`；manager=+publish/unpublish/share/manage_*）；服务端强制样板 `api/services/workflow.py:965-972`；运行准入 `use_app` 的用法 `open_endpoints/domain/utils.py:140-153`。
- admin 路径（不另立特权）：`permission/domain/services/permission_service.py:102-104`（超管直通）、`:1061-1069`（租户管理员短路）；`common/dependencies/user_deps.py:77-102`。
- F076 身份模型现成：服务账号创建即同事务建专用 `svc-*` 用户（零角色、不可交互登录）`open_api/domain/services/service_account_service.py:58-75`；装载与防提权见 §1.1；身份参数拒绝实现（INV-23③）`open_api/api/dependencies.py:53-93`。
- **授权链路**：管理员代管授权 `open_api/domain/services/resource_grant_service.py:118-198`（四类资源 knowledge_library / knowledge_space / assistant / workflow，档位 viewer/editor/manager）；**SA 必须被授到 editor 档才持有 `use_app`**（viewer 不满足运行准入，`application_permission_template.py:15-42,93-95`）。
- **触发期复检的两条路（spec 必须二选一）**：路线 A＝F076 既有逐请求复检链（`open_api/api/dependencies.py:106-185` 步骤①-⑦ + `open_endpoints/domain/utils.py:140-153`），撤权后**下一次触发生效、残余滞后 ≤10s**（`permission/domain/services/permission_cache.py:19`，`PermissionService.authorize` 主动失效 `permission_service.py:380`）；路线 B＝worker 内直调 F054 式，`execute_workflow` 链路**零权限检查**（`worker/workflow/tasks.py:54-97` 全文无 PermissionService），身份停用也不阻断。
- 审计：`database/models/audit_log.py:376-442`（`ainsert_v2`；`operator_id=0 → 'system'` `:407-408`；非 0 走 UserDao 反查名字 `:409-421`）；白名单机制 `:178-211` + 前端镜像 `platform/src/controllers/API/log.ts:88-123`（有后端测试断言两表一致）；best-effort 有界队列先例 `open_api/domain/services/call_audit_service.py:40-46,190-200`。

**需新建**
- 创建期校验**组合函数**（零件齐、组合缺）：① 创建者对目标应用 `edit_app`；② 目标应用存在且类型合法；③ 所绑 SA 存在且 `is_enabled`；④ SA owner 未被提权（11816）；⑤ SA owner 对目标应用持 `use_app`（`application_permission_service.py:358-381`）。缺 ③④⑤ 则 ★D3 落空。
- 「谁能建/改/停/看」的**服务端**强制（反例：`share_app` 的分享链生成端点无任何服务端校验，`share_link/domain/services/share_link_service.py:13-30`，F077 不得复刻）。
- 运行身份锁定语义：`created_by`（人类，管审计）与 `run_identity_user_id`（创建时锁定）分离；触发阶段不接受任何身份参数。
- **编辑者获取可用运行身份的通道（闭环缺口）**：SA 全部管理端点仅超管/租户管理员可调（`open_api/api/endpoints/service_account.py:110-115` 等），`SystemPage/index.tsx:44-48` 同样仅管理员可见 → **普通编辑者没有任何途径枚举/选择一个可用服务账号**（"我可见的可用运行身份"端点全仓未找到）。不做此项，★D3=C 在编辑者档落不了地（★D18）。
- 审计动作树 `schedule.task.*` / `schedule.run`；`schedule` 命名空间须在 `audit_log.py:216-221` 的 `_V2_NAMESPACE_TO_ACTION_PREFIX` 加一行**并同步前端镜像表**（否则白名单对不上，后端有测试）。

### 1.3 cron 与自然语言

**可复用**
- cron 解析/校验/预览零新依赖：celery `crontab.from_string` + `remaining_estimate`（实测边界见 §4；仓内**无** croniter/apscheduler/redbeat——`uv.lock`/`pyproject.toml` 0 命中，已复核）。
- **容错解析**：`json_repair` 已是直接依赖（`pyproject.toml:149`，锁 0.55.1）。先例：`workflow/nodes/input/input.py:140-147`（strict 先、`json_repair.loads` 兜底）、`linsight/domain/services/agent_factory.py:385-399`；另有 `workstation/domain/services/chat_service.py:613`、`evaluation/domain/services/answer_correctness.py:97`。（**订正**：草稿曾把出处写成 `doc_extract/.../extraction_service.py`，那里实际是 fence 正则 + `json.loads` + 抛错，见附录 A。）
- **「模型输出必须服务端校验」的锚点**：`doc_extract/domain/services/field_validator.py:1-7`（无结构化输出支持，正确性必须在此校验）+ `datasource/domain/services/sql_guard.py:1-3,46-51`（护栏是"useful filter, not a wall"）；设计口径见 `features/v2.7.0/061-datasource-qa/design.md:164-175`。注意：061 决策 8 在 SQL 场景**否决了**"执行前人工确认"，故 D5 的强制确认是**产品决策**（落库即持久化授权），不是该原则的直接推论。
- 结构化输出＝「prompt 约定 JSON + 容错解析 + 自写校验」（无原生结构化输出）。
- LLM 槽位与最省调用路径：槽位枚举 `common/models/config.py:10-37`、`llm/domain/services/model_slot.py:61+`、服务内调用样板 `datasource/domain/services/query_service.py:213-256`（构造需补 `app_id/app_type/app_name/user_id`）。
- 「LLM 生成 → 人工确认 → 落库」先例：`api/v1/assistant.py:125-176` + `AutoPromptDialog.tsx:30-91`；服务层自证"只调模型不写库"`api/services/assistant.py:527-534`。

**需新建**：cron 服务端校验与归一化（唯一解析器＝celery；`@daily`/周 `7`/多空格均拒绝，见 §4）；预览端点（人类可读 + 未来 N 次，**必须用显式 tz-aware 锚点自算**，见 §4 陷阱）；NL 解析端点 + 草稿（不落库）→ 确认页 → create；cron 输入/预览前端组件（platform/client 三语 locale 对 "cron" **0 命中**，无可用库，须自绘）；错误码 **119** 段。

### 1.4 前端

**可复用**
- 落点容器：「对外发布」页签共用 `components/bs-comp/apiComponent/index.tsx`——左侧菜单是纯数组 `menuItems`（`:42-58`，现 3 项），整面板 `share_app` 门控（`:36-39`）；入口：`editAssistant/index.tsx:158,170-188`、`flow/Panne.tsx:233-235`。
- 列表页模板：`pages/SystemPage/components/ServiceAccount/index.tsx`（表格 + 启停确认 + 详情切换 + 分页）；失败原因 Tooltip `pages/EvaluationPage/index.tsx:194-228`。
- NL 输入同构先例：`AutoPromptDialog.tsx:30-91`（textarea → 后端任务 + SSE → 可编辑回填）。
- API 层与错误文案：`controllers/request.ts:56-61`（自动解析 `api_errors:<code>`）；i18n 新 ns 须登记 `i18n.js:45`；`api_errors` 域 SSOT 在 `packages/locales`（生成物禁手改，CI 校验）。
- client 站内信消费面（失败通知零新增 UI 即可送达）：`components/NotificationsDialog.tsx:465-486`（未知 action_code 落通用文案）。

**需新建**：定时任务面板（位置 ★D11）+ 创建/编辑弹窗 + cron 输入与预览（自绘、bs-ui；禁新 UI/状态库、禁 frosted glass）；列表列（触发计划 / 下次执行 / 上次结果含失败原因）；client 三语文案键 + 跳转；平台三语业务文案。刷新策略＝打开即刷 + 手动刷新（不照抄评测页 6s 轮询）。

### 1.5 可观测与审计（含运行归因的实证缺口）

**可复用**
- F046 应用运行遥测**自动收录**（执行路径复用即免费获得）：`worker/workflow/tasks.py:107-127`（含 `status`/`input_snapshot`）、`open_endpoints/api/endpoints/assistant.py:274-305`；查询面 `telemetry_search/api/endpoints/agent_transactions.py:17-45`；监控页 `pages/LogPage/index.tsx`；节点级留档 `redis_callback.py:747-795`。
- `trace_id` = 运行 `unique_id`（`tasks.py:100-104`）→ 任务表存 `last_trace_id` 即可一跳监控详情（`agent_transactions.py:50-57` 支持按 trace 检索）。
- 站内信发送器 `message/domain/services/message_service.py:401-422`；外发白名单 `notification/external/_payload.py:6-35`（**新 action_code 默认不外发**）。

**需新建 / 必须写死**
- **监控无 task 维度**：`ApplicationProcessEventData` 字段仅 `app_id/app_name/app_type/chat_id/start_time/end_time/process_time/status/input_snapshot`（`common/schemas/telemetry/event_data_schema.py:145-165`）——**无 source、无 task、无 user**；查询过滤维度只有 status / app_id / app_type / keyword / 时间窗（`agent_transactions.py:35-63`）→ 同一应用的多个定时任务**在监控里不可区分**。一期靠 `last_trace_id` 一跳（★D19）。
- **`input_snapshot` 在无人值守下恒为空**：它取该 chat_id 下最新 `category='question'` 消息前 200 字符（`common/chat/utils.py:223-244`），而 `chat_id=""` 时所有消息写入点提前 return（`redis_callback.py:581-595`；F054 即此形态 `evaluation_service.py:437-447`）→ 无行可查。事故复盘时"这个任务跑了什么输入"一期无据（除自写合成 question 行或任务表落摘要，★D19）。
- **INPUT 暂停会被遥测记成 SUCCESS**：`tasks.py:108-127` 的 run_status 只按异常判定，INPUT 不抛异常 → 一条运行可能报两条（`agent_transactions.py:197` 注释"form 型一轮报两次"）→ 成功率统计须按 trace_id 去重或单列。
- 留存：遥测落统计 ES `base_telemetry_events`，**全仓无 ILM/rollover/清理**（未找到）；查询窗硬上限 90 天；`audit_log` 无清理作业。对客口径沿用 F076 先例（"沿用平台现状，建议 ≥6 个月"）。
- 隐藏风险：监控可见性按**当前**租户成员集合过滤且 5 分钟缓存（`telemetry_search/domain/services/call_logs.py:17-37`）——SA 被彻底移出租户后，**历史运行记录对该租户不可见**。

### 1.6 集成点（渠道侧消费 / 事件面 / 与 F076 边界）

**现状事实**
- 目标对象抽象＝`(app_id, app_type)`：助手是独立 `assistant` 表（`database/models/assistant.py:105-106`），「应用」列表是 Flow∪Assistant 并集（`database/models/flow.py:747-790`）；引用形状先例 `workstation/domain/models/user_app_favorite.py:19-22`。
- 结果落会话链路：worker 懒创建 `MessageSession`（`user_id`＝运行身份，`redis_callback.py:656-669`）；`chat_id=""` 完全不落库（`:581-595`）。应用会话列表按 `(flow_id, 当前登录用户)` 过滤（`workstation/api/endpoints/apps.py:199-207`）。
- 开放 API 编程调用面：工作流 v2 `session_id` 可传（`open_endpoints/api/endpoints/workflow.py:52,67-73,85`）；助手 v2 HTTP **无状态**（不落 session/消息，`assistant.py:105-115`）。
- F076 边界：INV-23（`release-contract.md:97`）——密钥不可回读 ⇒ **不能"调度器存密钥自调 /api/v2"**，身份装载走进程内 `load_owner_payload`；运行期防提权 `open_api/api/dependencies.py:159-172`。
- **事件触发面现状=零**：`webhook` 全后端仅命中出站机器人工具；`/api/v2` 新端点必须逐条登记 scope（`open_api/domain/scopes.py:40-133`，无通配，漏标 500/11811）；密钥是**裸 bearer、无请求签名**（`credential_validator.py:50-85`），防重放**无现成设施**；F076 明确不做限流/配额/幂等（`release-contract.md:194`）。

**需新建 / 待裁定**：任务表的目标解析与可见性校验；**触发期预检三要素**（应用存在/未删 + 运行身份可装载 + `use_app` 仍在，复用 F076 的 11805/11816 语义）；事件触发端点（若做）的载荷白名单、响应契约与触发放大护栏（★D1 影响面）；结果落点与失败通知的最终口径（★D8）。

**上游对照（不移植）**：F108 规格散见 `source_github/main:docs/PRD/3.0-release/3.0 应用工场 产品方案.md:125-126,323,362,365` 与技术架构 Review；**"自然语言生成定时任务"上游无规格**，须 F077 自定。

### 1.7 【本轮新增】知识可见性 F041 `user_auth` —— 定时运行下的语义（**必须写进 spec**）

六面探查整体漏掉了这条约束面，对抗审查抓到。**开关默认均关**：
- 工作流节点参数 `user_auth`（缺省 False）：`workflow/common/knowledge.py:213-222`、`workflow/nodes/agent/agent.py:287-290`；前端默认与兼容填充 `platform/src/util/flowCompatible.ts:52,61`。
- 助手列 `assistant.knowledge_auth`（默认 False）：`database/models/assistant.py:52-57`（迁移 `v2_6_0_f047_assistant_knowledge_auth.py`）。
- 语义权威（**两档＝ON 按运行使用者 / OFF 按配置者可见范围**，**不是"不过滤"**）：`features/v2.6.0/041-knowledge-space-select-flow-assistant/spec.md:19-20,89-93(AC-10/11/14),105(AC-19)`。

**判定落入的代码路径**：知识空间 `knowledge.py:383-384`（`identity_user_id = 运行用户 if 开关 ON else flow_user_id`）、`:396`；助手 `assistant_agent.py:356,366`；文档知识库 RagUtils 链 `knowledge.py:301-302,431-432` → `knowledge_flow_retrieval.py:196` → `knowledge_rag.py:158-163`（**ON → 按运行用户判 `can_read`**；**OFF → `get_list_by_ids` 无逐人过滤**）。例外：QA 检索节点硬编码 `check_auth=False`（`qa_retriever.py:29-33`）。

**无人值守 + 服务账号（★D3=C）下的后果**：
- **OFF（默认，即现存绝大多数应用）**：机器运行**借用人类创建者（可能是管理员）的可见集合**——这与 D3/INV-23② "以专用身份执行、永不继承特权"的表述**存在张力**（虽然那是 F041 有意的"安全有界共享"）。
- **ON**：按运行时用户＝影子服务账号（零角色）判定 → **大概率空**（除非显式授权）→ 任务"成功"但检索空/降级、**结果失真**。
- **INV-21**（`release-contract.md:95`）要求**任何新增知识读取路径**接入同一份可见集合判定——定时运行就是新路径，**必须登记**。
- ⇒ spec 必须逐档写死「定时运行的知识可见集合由谁判定」+ 创建期校验/提示（如"含空间检索且开关 ON 但 SA 未授权"时拒绝或告警）+ 对客口径。

---

## 2. 关键决策点（★ 待用户确认）

> 每条给选项、推荐与理由、影响面。D1–D12 来自六面探查草稿（经审查订正），D13–D17 由需求完整性审查触发补写，D18–D19 由技术风险审查触发补写。

### ★ D1 触发类型范围（仅 cron / + webhook / + 其它事件源）— ✅ **已确认 2026-10-16：B**
- **A**：仅 cron；「事件触发」由外部系统按需调既有 `/api/v2`（F076 面）承载。
- **B（推荐，✅ 采纳）**：cron + **入站 webhook** 触发面。
- **C**：A + B + 内部事件源（知识库更新等）——**前提不成立**：本仓无事件总线/无 MQ（Redis pub/sub 仅缓存失效）。
- 推荐理由：条款字面含「事件触发」；上游同口径（PRD`:126`「定时（cron）+ 事件（webhook）」）；C 缺基础设施且无客户需求锚点 → 列明确不做。
- 影响面与代价（若 B）：新增 `/api/v2` 触发端点（**逐条登记 scope**，漏标 fail-closed 500/11811）；凭据沿用服务账号密钥（裸 bearer，**防重放须自建** timestamp+nonce+Redis TTL，标准库即可）；载荷白名单只收 `task_id` +（可选）入参映射，**禁止 app_id/身份参数**（对齐 INV-23③，裸 `user_id` 一律 400/11817）；响应＝真 HTTP 状态 + 信封（401/403/404/503 既有口径）+ `{run_trace_id}`，fire-and-forget；**触发放大护栏**＝任务级互斥（与 D6 同锁）+ 每任务/全局频率上限 + 每应用任务数上限 + 每次触发整段占 Celery 线程（`workflow/graph/workflow.py:68-84`，池宽 100）的现实约束。若客户验收只测 cron，可降为 A 并登记对客偏差。

### ★ D2 目标对象（助手 / 工作流 / 两者）
- **A**：仅工作流。**B（推荐）**：工作流 + 助手。**C**：含工作站对话 / 灵思任务。
- 推荐理由：平台「应用」＝工作流 ∪ 助手（`flow.py:747-790`），客户表述面向"应用"；助手侧可复用 v2 无状态链（无人值守不需要交互面）。C 的灵思是一次性会话、终态丢弃模型，上下游两侧均判不可作通用执行内核。
- 影响面：B 须在 spec 写明"助手定时运行无会话落点"（见 D8）；数据模型统一 `(app_id, app_type)`。
- ⚠ 待验真项（技术风险审查提出，**降级为待澄清**）：助手侧"复用 v2 无状态 HTTP 链"的具体可调用入口需要 design 阶段钉死——**不得**走"调度器持密钥自调 `/api/v2`"（INV-23①），应在进程内直接调用助手服务层或走既有内部 chat 链路。

### ★ D3 运行身份（应用所有者 / 服务账号专用身份 / 新建专用身份）— ✅ **已确认 2026-10-16：C**
- **A**：创建者（人类）身份。**B**：应用所有者身份。**C（推荐，✅ 采纳）**：**F076 服务账号专用身份**（创建任务时选定并锁定）。**D**：为 F077 新建另一类专用身份。
- 推荐理由：F076 模型现成且已过评审（专用零角色用户 + `load_owner_payload` + `owner_is_privileged` 双重防提权），符合 INV-23②"永不继承管理员特权"；D 重复建设。A/B 的风险：权限随人/所有者漂移（调岗离职 → 任务静默失效或幽灵运行）。**默认操作员＝user 1（首注册超管）绝对禁止**。
- 影响面与代价：结果会话归影子用户（人类默认不可见，由 D8 补偿）；每租户多一个影子用户（席位口径 F076 已列待确认）；**与 F041 的交互必须按 §1.7 写死**；**编辑者档需要 D18 的通道**才能落地；若最终选择 A/B 或任何"代表用户运行"，按 INV-23 结尾条款**须先重新评审该不变量**（本特性默认不做，也不会静默做）。

### ★ D4 执行者（beat 固定节拍 / 独立常驻进程）
- **A（推荐）**：Celery beat **固定 30s tick** + DB 扫描 + 原子 claim → 复用 `execute_workflow` / 助手执行链；**派发时显式传 `queue=`**（不依赖 task_routes）。
- **B**：独立常驻调度进程 + 私队列。**C**：beat 内常驻轮询长任务（反模式，占死线程）。
- 推荐理由：与 file_scheduler 先例同构（beat 30s + 平台级 Redis 锁 + 事件触发补齐 + reconcile 兜底）；零新部署单元（信创/离线交付成本敏感）。B 的常驻收益本条款不需要。
- **配套（不可裁掉）**：① **运行租约 + stale 回收**（§1.1：现状无任何运行超时/回收，与 D6 互斥叠加会导致永久静默停摆）；② **运行监督器**（D12 与回收共用）；③ 全局「运行中」标记的租约必须有上限值与告警。
- 影响面：最细触发粒度受 tick 限制（30s 抖动，cron 本身分钟级可接受）；beat 无去重 → 自建平台级锁 + DB 原子 claim（**避免 `RETURNING`**，MySQL/DM8 双库，见 §4）；**task_routes 是"非空即整表替换"**（实测：`config.yaml` 给了 `celery_task.task_routers` 后默认两条整体失效）→ 派发一律显式 `queue=`（仓内 `execute_workflow` 全部派发点均如此）。

### ★ D5 自然语言生成定时任务的产品形态
- **A（推荐）**：平台「定时任务」面板内 NL 输入（一句话 → 解析草稿 → 表单预填/可编辑 → 确认 → 落库）。**B**：client 对话内生成（新工具 + 卡片协议）。**C**：两者皆做。
- 推荐理由：平台侧已有同构先例（`AutoPromptDialog` 的"后端任务 + SSE + 可编辑回填"）；任务管理面在平台（D11），生成与确认同屏、审计归因清晰。B 需新工具 + 卡片协议，列二期。
- **强制人工确认＝是（不随选项变化）**：模型输出＝不可信输入（§1.3 锚点）；cron 误读（"每天"→"每分钟"）直接变周期性资源消耗；落库即持久化授权。确认页必须回显**人类可读计划 + 下次运行时间（或一次性绝对时间）**。默认模型：优先新槽位 `scheduled_task_llm`，未配置回退租户默认对话模型（F061 口径）。

### ★ D6 漏跑补偿与重叠策略
- **A**：不补跑（错过即跳过）。**B（推荐）**：**合并补跑一次**（宕机跨多周期仅补一次）+ 单任务互斥（到期时上一轮未结束 → 本轮跳过并记 `skipped`）。**C**：全量补跑（否决：补跑风暴）。
- 推荐理由：全跳过客户感知强；合并一次成本最低。重叠不排队（排队需运行队列 + 幂等键，超一期）。
- 影响面：须 `next_run_at / last_run_at / last_status / 运行中标记 + 租约`，补跑与重叠判定必须在**原子 claim 内**完成；**一次性任务例外**（见 D17：互斥时顺延而非跳过）。

### ★ D7 时区口径
- **A（推荐）**：固定集群时区 `Asia/Shanghai`（现状 `worker/config.py:12-13` `enable_utc=False`）。**B**：任务级可配时区。
- 推荐理由：现状全局单时区、行内单区域交付；B 需自实现偏移/夏令时，收益不明。
- 影响面：UI 明确标注「按服务器时区（Asia/Shanghai）」；**预览与 `next_run_at` 必须用显式 tz-aware 锚点自算**（§4 陷阱 3）。

### ★ D8 结果落点与失败通知 — ✅ **已确认 2026-10-16：A**
- **A（推荐，✅ 采纳）**：落点＝**监控/审计自动收录**（零新建）+ 失败**站内信通知创建者**（创建时锁定）+ 任务列表状态列。**B**：A + 落会话（工作流生成 `chat_id`，以运行身份写 `MessageSession`；人类默认不可见）。**C**：落会话且投递给创建者（须复核创建者对被检索知识库的可见性，INV-21 合规口径，成本最高）。
- 推荐理由：工作流落会话机制在，但"落会话"必然撞上"哪个身份/谁可见"的裁定；助手 v2 链路根本无状态 → 一期以监控 + 通知为正式落点，回避跨身份展示风险。
- 影响面：**来源可区分性的正确做法**（订正草稿）：**不动公共遥测 schema**——用任务表 `last_trace_id` 一跳监控（`trace_id` 即运行 `unique_id`）。草稿原方案"给 session 事件 `source` 加第三值 `scheduled`"在 D8=A 下**没有生产者**（session 事件只在落会话时发出，`redis_callback.py:657-688`），且若真落会话还须先扩 Literal（否则 pydantic ValidationError 成为运行失败）+ 同步存量看板 `mid_sessions_increment` 的两个 metric 与"来源类型"维度（`telemetry_search/domain/init_dataset.py:201-256`），否则口径失真。若产品要求监控可按触发来源筛选，则改扩 `ApplicationProcessEventData`（登记表 1 + 改 F052 映射与查询）——**三选一，spec 写死**。

### ★ D9 权限模型（谁能建 / 谁可见）
- **A（推荐）**：建/改/停/删/立即运行＝应用级 `edit_app`（编辑者档起，**服务端强制**）；运行准入＝`use_app`（针对**运行身份**）；owner/manager/超管/租户管理员走既有短路兜底。
- **B**：新增应用级权限位 `manage_schedule`（可拆"可运行不可定时"；代价＝权限模板两处镜像 + 前端对账 + 回归）。**C**：纯管理面（与"业务用户手动生成"条款冲突，不推荐）。
- 影响面：**服务端必须强制**（反例见 §1.2）；「谁可停别人的任务」须写死（推荐 owner/manager + 管理员短路）；面板可见性现门是 `share_app` 而操作门是 `edit_app` → "看得到 ≠ 改得动"的收口须在 spec 写明。

### ★ D10 数据归属登记与错误码 119
- **A（推荐）**：**一张新表** `scheduled_task`（`tenant_id` 非空 + index，登记 `_TENANT_AWARE_MODEL_MODULES`）；运行历史一期由 `last_*` 字段 + 监控/审计承载，**不建独立运行历史表**。**B**：两张表（任务 + 运行历史）。
- 影响面：release-contract 五处登记（表 1 新表 + 若扩遥测字段/审计动作/来源枚举各一行；表 2 不变量候选；表 3 依赖行；错误码 119；变更历史）。**错误码 119** 三来源扫描本轮**全空可作候选**，design 阶段正式复核后分配。

### ★ D11 管理入口位置
- **A（推荐）**：「对外发布」页签左侧菜单加第 4 项「定时任务」（助手/工作流共用同一面板）。**B**：平台系统页新 tab（运维视角，可全局停用失控任务）。**C**：独立侧栏页面（需新 web_menu key + 回填迁移，不推荐）。
- 影响面：A 代价最低；语义同族（"定时运行"是发布后的一种运行形态）；若产品要求 tab 可见性与 D9 一致，需扩 `usePermissionIds` 并评估对既有面板的影响。B 可作为二期运维视角。

### ★ D12 无人值守遇人工输入节点（INPUT）
- **A**：视为"等待人工"并通知——**现状不可靠**（运行对象驻留 worker 内存，无 TTL 无回收）。**B（推荐）**：任务级「预设输入」自动提交；未配置则落 A 并通知。**C**：直接判失败。
- **订正（本轮验真）**：审查者称"落不了地"过强；正确结论是**有条件可行**——F054 证明答复协议**可跨进程**（Redis 写 input + 按同 hash_key 投 continue），但**必须新建"运行监督器"**：
  1. 监督器不能是 beat tick 自身（会占死 beat）：F054 式 API 进程 BackgroundTask（须定义 API 重启下的失败语义）或独立占线程的 Celery 监督任务；
  2. 续跑要求同 hash_key 绑定同一 worker 节点（`stateful_worker.py:113-140`，20s 心跳），节点死亡/worker 重启＝**必失败且不可恢复**（无 checkpoint，`graph_engine.py:295`）→ 必须定义终态与通知；
  3. 预设输入只能按**静态 label/key** 存（INPUT 节点运行时 key 每次重新生成，`workflow/nodes/input/input.py:49-50,76-80`，提交时按事件里的 `input_schema` 映射）；file 项需预签名 URL，无人值守的 file 预设要单独定义来源；
  4. F054 只处理**一轮** INPUT（二次 INPUT 会被当作结束并 `set_workflow_stop()`，`evaluation_service.py:483-484`）→ 多输入节点须自行设计；
  5. 现状缺口：INPUT 暂停被遥测记 SUCCESS、无 `needs_input` 终态、parked 对象无回收、事件/输入 key TTL＝`timeout*60+60`（部署 5min → 6 分钟）→ 监督器必须及时消费。
- 影响面：任务配置增加「输入预设」结构（尺寸上限）；含 INPUT 节点的工作流在创建任务时须校验/提示；运行超时**须自建**（§1.1）。

### ★ D13 任务与应用的版本 / 上下线关系
- **A（推荐）**：任务只绑 `(app_id, app_type)`、**不锁版本**；触发时服务端取"当下当前数据"（工作流=`Flow.data`、助手=当前行）；**触发前置校验要求目标应用 ONLINE**，OFFLINE → 跳过（记 `skipped`，不计失败）并在列表标注"应用已下线"。**B**：同 A 但不校验 ONLINE（对齐 v2 invoke 现状）。**C**：创建时锁版本（仅工作流成立，仿评测链）。**D**：每次触发要求重新确认（与无人值守矛盾，不做）。
- 事实基线：ONLINE 下禁止切版本/改数据（`api/services/flow.py:155-156,218-219`，`api/v1/workflow.py:327-330`）→「`Flow.data`＝当前已发布版本」是不变量；v2 invoke **不看 ONLINE**（`open_endpoints/api/endpoints/workflow.py` 全文无 `FlowStatus`），而平台会话与广场都要求 ONLINE（`workflow_client.py:121-122`、`plaza_service.py:165`）；助手**无版本体系**（全仓无 `AssistantVersion`）；锁版本的先例只有评测链，且非当前版本可被删除（`api/services/flow.py:119-123`）→ 删后任务必失效。
- 推荐理由：与"已发布应用的无人值守运行"语义一致（无人值守默认 fail-closed）；回避助手无版本与版本删除耦合两个硬伤；"跳过而非失败"把"故意下线"与"故障"分开，避免误触发 D15 的自动停用。
- 影响面：与 v2 invoke 的差异是**新规则**，须在对客文档登记；建议连带落"本次运行所用版本可回溯"（`schedule.run`/任务表记当次版本 id 或数据摘要）；应用被删除的处置归 D14。

### ★ D14 目标应用 / 运行身份 / 创建者被删除或停用后的任务处置
- **A**：仅触发期 fail-closed + 自动停用 + 通知（零改既有删除链）。**B（推荐）**：A **＋ 删除期服务端拦截**——删除工作流/助手时若存在"启用中"的定时任务，拒绝删除并报数量（新 119 错误码）；仅停用/历史任务不阻断。**C**：仅删除期拦截（旁路仍会留下跑不动的任务，不推荐）。
- 事实基线：本仓对"被引用物删除"的既定做法是**显式化**——数据源删除前扫引用并拒绝（`datasource/api/endpoints/datasources.py:384-393`，设计说明 `references.py:11-20`）、审批流程定义被引用拒删；服务账号**停用＝吊销全部密钥**、删除＝吊销→停用身份→软删（`service_account_service.py:150-166,188-244`），FGA 元组不主动清、随身份停用失效；知识空间删除**修剪引用 + 全员站内信**（`knowledge_space_service.py:1672-1706`）；集中孤儿处置范式 = 收敛到同一入口 + system 审计 + 通知（`tenant/domain/services/department_deletion_handler.py:1-7,38-117`）。**应用删除现状两条链均无引用检查**（`api/v1/flows.py:28-47`、`api/services/assistant.py:487-518`）。
- 推荐理由：拦截实现便宜（定时任务是自家表，查引用零扫描）；触发期预检覆盖停用/运维直改等非删除旁路，两者互补。
- 影响面：改两条既有删除端点 + 119 错误码 + 三语文案 + 回归；**权限错位**（删应用＝`delete_app` owner 档，管任务＝`edit_app` editor 档）须写死引导；通知收件人＝创建者，创建者已停用时兜底租户管理员/超管（`tenant/domain/services/inbox_helper.py:63-80`）。

### ★ D15 失败感知闭环（连续失败自动停用 + 通知控频）
- **A**：失败即发通知（最坏 1 条/分钟 → 单任务 1440 条/天，且平台前端不消费站内信＝看不见，否决）。**B（推荐）**：**两点式通知 + 连续失败自动停用**——任务级 `consecutive_fail_count`（仅 `failed` 计入；`skipped`/`needs_input` 不计）：**首败 1 条** + **达阈值自动停用 1 条**（阈值默认 **N=5**，可配）；恢复成功不补发。**C**：时间窗聚合 + 不自动停用（降级备选）。
- 事实基线：站内信管道**无去重/聚合/限频**（`message_service.py:65-103`）；平台前端**不消费**站内信（`feedbackEvent.ts:34,44`）→ 第一现场只能是任务列表状态列；本仓"有界失败→终止态"范式存在（`FailedTuple` `max_retries=3` → `mark_dead`；灵思工具环熔断 `tool_loop_middleware.py:238`）但**无定时类现成实现**；外发白名单默认不转发新 action_code。
- 影响面：只依赖 D10 已规划的 `last_*` + 一个计数器（不建新表）；**这是对草稿 §3"失败置 last_status=failed＋站内信"（每条都发）的订正**；前置依赖：**运行租约/回收**必须同时落地，否则"运行中"长期无人回收 → 任务永久 skipped、计数器永不推进，自动停用在最坏路径上失效。

### ★ D16 「自然语言生成」是否包含修改既有任务
- **A（推荐）**：一期 NL 只做**生成新任务**（草稿→确认→create）；修改走表单/编辑弹窗。**B**：A + NL 修改（同一链路，产物＝对既有任务的变更草稿/diff）。**C**：B + NL 启停/删除（寻址与误伤面最大，明确不做）。
- 推荐理由：条款字面是"生成"；仓内唯一"改写既有对象"的 NL 先例同样止于草稿（`AutoPromptDialog` + "只调模型不写库"）；B 的成本集中在"任务寻址歧义 + diff 确认页 + 并发编辑冲突"，一期收益不匹配。
- 影响面：A 需在 §0/§5 写死"NL＝新建，修改走表单"；若客户验收明确要求"用自然语言改任务"，按 B 升级（技术基础同源）。

### ★ D17 一次性（单次）任务是否支持 — ✅ **已确认 2026-10-16：B**
- **A**：不支持（只有周期 cron）。**B（推荐，✅ 采纳）**：支持——`schedule_type=cron|once`；`once` 存 `run_at`（Asia/Shanghai 绝对时间）；执行一次后**自动置 `disabled`**。**C**：用"日期型 cron + 执行后停用"模拟（否决：cron **无 year 字段**，日期型表达式按年重复，失败/跳过后次年同日会再触发）。
- 事实基线：celery crontab 五段无 year（实测：`0 9 16 10 *` 的下次触发是**次年**同日期）；仓内"到期执行一次"先例＝`sync_information_article.apply_async(..., eta=exec_time)`（`channel/domain/services/channel_service.py:1765-1766`）。
- 交互（须与 D6/D7/D15 写死）：once 到期恰遇互斥时**顺延到下一 tick**（只有一次机会，跳过即丢；周期任务维持"跳过记 skipped"）；失败即终态（等价 N=1 自动停用 + 通知）；确认页/NL 回显**绝对时间**；§3 的"最小间隔 1 分钟"仅约束 cron 分支。

### ★ D18 编辑者档如何获得"可用运行身份"（D3 的落地前提）
- **A（推荐）**：新增**只读端点**「按目标应用列出可用运行身份」（鉴权＝应用 `edit_app`；返回 账号 id/名称/是否已授 `use_app`/是否启用；**不给任何密钥信息**，不得绕过 `svc-*` 的 11821 保护）。**B**：任务创建限定管理员、编辑者走 F025 审批申请（成本高、与条款距离远）。
- 事实基线：SA 管理端点全部 `Depends(get_tenant_admin_user)`；平台面板仅 `isSuperAdmin || isChildAdmin` 可见；"我可见的可用服务账号"端点全仓未找到。
- 影响面：A 需新增一个只读端点（scope/鉴权走既有应用权限，不进 FGA 新资源类型）+ 前端下拉；不做则 D3=C 只能由管理员建任务，与"业务用户手动生成定时任务"的条款意图有落差。

### ★ D19 运行记录的 task 归因与留存
- **A（推荐）**：任务表存 **`last_trace_id`**（唯一必加的归因字段）+ `last_status`/起止/耗时；`schedule.task.*` 进审计页白名单、`schedule.run` 落库**不进**白名单（对齐 `open_api.call`）；运行明细靠 trace_id 一跳监控。**B**：另建运行历史表 + 在任务表落输入摘要（解"input_snapshot 恒空"与"监控无 task 维度"）。
- 事实基线：监控事件无 `source`/无 task/无 user 字段；查询无 task 维度；`input_snapshot` 在 `chat_id=""` 时恒为空；INPUT 双报（一轮报两次）会污染成功率。
- 推荐理由：A 成本最低且够回答"跑了没/成没成/在哪看"；B 的价值在事故复盘，建议二期。

---

## 3. 已定（推荐口径，未获用户异议即入 spec）

- **扫描 tick** 固定 30s（对齐 file_scheduler 先例）；触发延迟 ≤30s；**cron 粒度＝分钟**（不支持秒级）；**最小间隔 1 分钟**。
- **运行租约与回收（强制）**：运行中标记带租约，超时未终态 → 置 `failed` 并**释放任务互斥**；租约上限默认取任务级超时（默认 60 分钟，可配）；**celery 任务显式声明 `time_limit/soft_time_limit`**（对齐 `worker/approval/tasks.py:16-17` 先例）。
- **并发闸**：单任务至多 1 个运行（互斥，重叠跳过）；全局同时运行的定时任务数默认 **10**（可配），超限顺延到下一 tick；**每应用任务数上限默认 20**（可配）。
- **失败处理**：不自动重试（0 次，下一周期自然重试）；连续失败阈值 **5** 次自动停用（D15）。
- **cron 校验**：服务端一律 `crontab.from_string` 校验 + 多空格归一化；`@daily`、周 `7`、非 5 段明确报错并给三语文案；预览返回未来 5 次（**用显式 Asia/Shanghai tz-aware 锚点自算**，展示前取整到分钟）。
- **防重**：平台级 Redis 锁（对齐 `scheduler.py:308-322`）+ DB 原子 claim（`UPDATE ... WHERE` 判 `rowcount`；**禁用 `RETURNING`**——DM8 多行 UPDATE 会以 `[CODE:-5016]` 拒绝，仓内实锤 `database/models/department.py:550-573`，F076 DAO 亦用 `result.rowcount or 0`）。
- **任务状态机**：`enabled/disabled`（+ 一次性执行后自动 disabled）+ 运行中租约标记；`last_status ∈ success/failed/skipped/needs_input`。
- **审计**：管理动作 `schedule.task.create/.update/.enable/.disable/.delete/.run_now` **进**点审计页白名单（对齐 INV-15 精神：改了它后续每次运行都变）；运行动作 `schedule.run` 落库**不进**白名单（对齐 `open_api.call`）；`schedule` 命名空间须两处登记（后端 `_V2_NAMESPACE_TO_ACTION_PREFIX` + 前端镜像表）；`schedule.run` 的 operator 取值（运行身份 user_id 或 0=system）**二选一在 spec 钉死**（`operator_id=0 → 'system'` 是既有约定）。
- **通知**：失败站内信 action_code 沿用 `scheduled_task_failed`，收件人＝创建者（创建时锁定）；成功不发送；若启用外发须登记白名单（默认不外发）。
- **表与登记**：表 `scheduled_task`（模块目录名 design 定）；`scripts/arch-guard.sh` 的 `DOMAIN_MODULES` 补登记；新表**必须**登记 `core/database/tenant_filter.py:39-131` 的 `_TENANT_AWARE_MODEL_MODULES`（漏登记 → 行落 ROOT 租户）。
- **NL 生成链路**：prompt 要求"只输出 JSON"（shape：目标应用 / cron 或 run_at / 输入 / rationale）→ `json_repair` 容错 → **服务端硬校验**（cron 合法性 + 目标应用存在且有权限 + 输入尺寸上限）→ 返回草稿**不落库** → 确认页保存才 create；NL 输入尺寸上限（建议 2000 字符）。
- **平台侧刷新策略**：打开即刷 + 手动刷新。
- **测试口径**：扫描/判定写成**纯函数 + 显式 `now` 参数**（先例：`credential_validator.py:61`、`dao.py:118,127,173,394`），celery 任务只做薄壳；不引用 `task_always_eager`（**全仓零命中，本仓测试不用**）。

---

## 4. spec 必须落死的技术边界（本轮实测）

1. **动态 cron 注册不进 beat**：`beat_schedule` 全静态（`core/config/settings.py:184-276`），无自定义 scheduler / redbeat / apscheduler / croniter（`uv.lock` 0 命中，已复核）→ 只能"DB 表 + 固定 tick 扫描"。
2. **`crontab(str)` 是陷阱**：`core/config/settings.py:281` 用 `crontab(task_info["schedule"])` 转换字符串——实测（celery 5.5.3）`crontab("30 2 * * *")` 抛 `ValueError: Invalid weekday literal`，`crontab("sun")` 静默解析成 `minute={0}`；**F077 一律用 `crontab.from_string`**（同文件 `:275` 的正确写法）。附带：这是本仓一个**既有隐患**（任何人往 `config.yaml` 的 `beat_schedule` 写字符串表达式都会踩），建议单开 hotfix 处理，不属 F077。
3. **预览/next_run_at 的时区与锚点陷阱**：`remaining_estimate` 内部用 app 的真实 now 计算（`celery/schedules.py:572-639`）——**不得**把它与入参相加或连续回填（会漂移）；F077 必须用显式 tz-aware 锚点自算。
4. **celery crontab 实测边界**：接受 `0 9 * * 1-5`、`0 9 * * mon-fri`、`*/5 * * * *`；拒绝 `@daily`（非 5 段）、`0 9 * * 7`（周 0-6）、多空格、越界。**无 year 字段**（日期型表达式按年重复）。
5. **Beat tick 无租户 header**：`worker/tenant_context.py:29-37` 无 header 落 `DEFAULT_TENANT_ID` → tick 内必须 `bypass_tenant_filter` 跨租户枚举 + 逐条 `set_current_tenant_id`，派发前 stamp（先例 `scheduler.py:521-535`）。
6. **工作流执行模型**：整段占 Celery 线程（`workflow/graph/workflow.py:68-84` 同步 `while RUNNING`）；INPUT 态运行对象驻留 worker 内存（`tasks.py:22-24`），续跑必须同节点，worker 重启即丢（`tasks.py:134-136`）；**无 acks_late**（任务丢失不重投）。
7. **会话落点语义**：`chat_id=""` 不落会话（`redis_callback.py:581-595`）；非空时懒创建 `MessageSession`（`user_id`＝运行时用户）。
8. **助手 v2 HTTP 无状态**（`assistant.py:105-115`）→ 助手定时运行无会话落点（与 D8=A 自洽）。
9. **F076 密钥不可回读**（INV-23①）→ 调度器**不能**"存密钥自调 /api/v2"；身份装载走进程内 `load_owner_payload` + `owner_is_privileged`。
10. **租户硬约束**：新表登记 `_TENANT_AWARE_MODEL_MODULES`；租户自动过滤**只拦 SELECT**，bulk update/delete 与 `text()` SQL 须手写租户条件（`src/backend/AGENTS.md` §Known Pitfalls）。
11. **审计系统触发约定**：`operator_id=0 → 'system'`（`audit_log.py:407-408`）；系统触发的 `operator_tenant_id` 约定 = ROOT_TENANT_ID（`:390-396`）。
12. **入站面零设施 + scope 逐条登记**：`/api/v2` 新端点无通配、漏标 500/11811、缺位 403/11807；密钥裸 bearer 无签名 → 防重放自建。
13. **应用目标解析**：助手无 Flow 行；应用＝Flow ∪ Assistant；引用形状 `(app_id, app_type)`。
14. **权限执行口径**：应用权限位模板 + 服务端强制样板；admin 走 `check()` 短路与 `get_tenant_admin_user`，**不另立特权路径**。
15. **C2 双库（新表字段层）**：JSON 列用 `dialect_helpers.JsonType`（DM 上是 CLOB：**不能索引/唯一约束/走 JSON 函数**）、长文本用 `LargeText`、更新时间列用 `UPDATE_TIME_SERVER_DEFAULT`（DM 的 ON UPDATE 靠启动期触发器兜底）；JSON 查询用 `json_array_contains` / `json_search_exists`，**禁 `JSON_EXTRACT/CONTAINS/SEARCH`**；存在性判定用 inspect 助手，别用 `information_schema`；**cron 归一化后的短串用 `String`**（勿用 CLOB 做唯一约束）；表参数沿 `mysql_charset=utf8mb4, mysql_collate=utf8mb4_unicode_ci`；迁移导入 `update_time_server_default(conn)`（先例 `v2_8_0_f058_doc_extract.py:36-40,77`）。DM8 无 CI job（C2 由集中回归承担）。
16. **task_routes 语义**：`celery_task.task_routers` **非空即整表替换**（`settings.py:177-183` + 实测：本检出 config.yaml 只给 knowledge 一条 → 默认的 workflow 路由整体失效）→ 派发一律**显式传 `queue=`**（仓内全部 `execute_workflow` 派发点均如此）。
17. **确定性验证手段（不依赖真实等待/beat/broker）**：① 服务层显式 `now` 参数；② 直接调任务体的 `run_dispatch_round(*, scheduler=None)` 纯函数（先例 `worker/knowledge/scheduler.py:452` + `test/knowledge/test_file_scheduler_dispatch.py:16-75`）；③ 造"相对现在"的陈旧行（`test_file_scheduler_reconcile.py:68-69`）；④ 手工触发一次＝复用同一内部派发函数 + 测试断言 `apply_async/锁` 被调；⑤ e2e 用"造数（`next_run_at` 置过去）+ 手工触发 scan + 立即运行 + 断言任务列表/监控 trace"替代真实等待（仓内 e2e 对时间只用固定 3s sleep 等批量落库窗口，`test/e2e/test_e2e_openapi_auth.py:188-194`）。**CI 现状：`.github/workflows` 无任何 pytest 作业**（仅前端质量门），spec 不得把"CI 会跑"写成前提。
18. **运行归因**：`trace_id` = 运行 `unique_id`（`tasks.py:100-104`）；监控无 task 维度、`input_snapshot` 无人值守恒空、INPUT 双报须去重（§1.5）。

---

## 5. 明确不做（本期范围外）

- **渠道侧不改动**：API 发布 / 免登录页 / 需登录链 / iframe 嵌入 / 分享链均已具备；不做"多渠道选择 → 一键分发"改造、不做 "Agent Portal" 具名渠道、不做企微/钉钉/飞书/公众号第三方发布。
- **不移植上游 F108**：无表、无迁移、无 design/spec 可搬；「常驻生命周期（armed/paused/预算超限）、预算闸与花费审批、机队观测面板、零-LLM 游标水位、通知服务本体」全套不做。
- **不改 linsight / 工作站执行内核**（一次性会话、终态丢弃，上下游双证不可复用）。
- **不引入新依赖**：apscheduler / celery-redbeat / django-celery-beat / croniter / cron-descriptor 与任何前端 cron 库一律不引入；不新增 UI/状态库。
- **不做内部事件总线 / 内部事件源触发**（无 MQ）。
- **不做"定时任务持有密钥自调 /api/v2"**（INV-23①）。
- **不做按参数指定执行身份 / 代表用户运行**（INV-23③；如未来需要，先重新评审 INV-23 并登记）。
- **不做任务级自动重试 / 全量补跑风暴**；**不做 per-task 时区**（若 D7=A）。
- **不做"定时跑助手并留会话"**（助手 v2 无状态；除非 D8 升级）。
- **不做 rerun / 历史重跑**（regenerate 已移除）。
- **NL 不做修改既有任务与启停/删除**（若 D16=A）。
- **不做独立「我的定时任务」client 模块**（需新 web_menu key + 回填迁移）；失败通知走既有站内信。
- **平台前端不做站内信消费改造**。
- 沿用 F076 既有边界：不做任务模式/日常模式开放 API、个人密钥、`/api/v2` 限流/配额/幂等。
- **不顺手修 `settings.py:281` 的 `crontab(str)` 隐患**（独立 hotfix；见 §4.2）。

---

## 6. 进入 spec 前的遗留待办

1. **release-contract 登记（spec 定稿时一并落）**：表 1（`scheduled_task` 表；若扩遥测 schema/审计动作/来源枚举/`ApplicationTypeEnum` 各一行）；表 2（**不变量候选**：「调度触发的执行身份创建时锁定、触发方不得指定」+「无人值守运行非特权，引用 INV-23」+ **INV-21 的新消费者登记**）；表 3（F077 依赖行，对 F076 服务账号模型的依赖强度）；错误码 **119** 段（分配前按三来源复核）；变更历史。
2. **错误码 119 号位表预分配**：cron 非法 / 目标应用不存在或无权限 / 任务不存在 / 数量超限 / 运行身份不可用 / （若 B）事件触发鉴权失败与防重放拒绝 / 删除应用时存在启用任务。
3. **三语文案**：platform（新 ns 或 `api.*` 扩展；新 ns 登记 `i18n.js:45`）；client 通知文案与跳转；`api_errors` 119 段三语（`packages/locales` SSOT，生成物禁手改，三语同 PR）。
4. **e2e 验证手段**（`/e2e-test`，按 §4.17 的确定性口径）：① 造数 + 手工 scan → 触发 → 任务列表 `last_status` + 按 `last_trace_id` 查监控；② 多租户：租户 A 的任务不在租户 B 触发；③ 权限矩阵：无 `edit_app` 建任务被拒（服务端）、运行身份无 `use_app` 触发期被拒；④ NL 生成草稿 → 确认 → 落库全链；⑤ 立即运行；⑥（若 D1=B）webhook 触发 + 防重放；⑦ 漏跑合并 / 重叠跳过 / 一次性顺延；⑧ 连续失败达阈值自动停用 + 通知。
5. **待用户确认项**：§2 的 **D1–D19**。
6. **待澄清（证据不足或需产品/客户窗口确认）**：
   - 客户验收口径：「事件触发」是否必须随本特性交付（决定 D1）；「手动或自然语言生成」验收是否要求 NL 形态；是否要求"只跑一次"（决定 D17）。
   - 结果可见性要求（决定 D8）；助手侧"无人值守调用入口"的具体实现（design 钉死，见 D2 待验真项）。
   - SA 影子用户的席位口径（F076 遗留）；`schedule.run` 的 operator 语义（D10/§3）。

---

## 附录 A：本轮对抗审查与验真改了什么（可追溯）

| # | 草稿原说法 | 验真结论 | 处置 |
|---|---|---|---|
| 1 | 「运行超时沿用 `WorkflowConf.timeout`（默认 720 分钟）」 | **错**。它只是 Redis 控制面 key 的 TTL；部署值 5 分钟（`initdb_config.yaml:93`）；**全仓无运行超时、无僵尸回收、无 acks_late**；`WorkFlowWaitUserTimeoutError`(10527) 无生产者 | 正文订正；§1.1 新增「超时的真相」；§3 增"运行租约与回收（强制）"；§4 落边界 |
| 2 | 「D12=B 预设输入可复用 F054」 | **部分对**。协议可跨进程复用（Redis 写 input + 按 hash_key 投 continue），但**必须新建运行监督器**，且 5 条前提条件（同节点绑定/静态 label/单轮 INPUT/TTL 6 分钟/无 needs_input 终态） | D12 改为"有条件可行"+5 条条件 |
| 3 | （**全文未提** F041 `user_auth` / `knowledge_auth`） | **漏项，且是重大约束面**：OFF=借配置者可见集合、ON=按影子账号（大概率空）；INV-21 要求新读取路径接入同一判定 | 新增 §1.7 专节 + D3/D5/§6 联动 |
| 4 | 「session `source` 加第三值 `scheduled`」+ e2e 断言 | **自相矛盾**：D8=A 不落会话则无生产者；且真落会话须先扩 Literal（否则 ValidationError 成运行失败）+ 同步存量看板 `mid_sessions_increment` 口径 | D8 改为"不动 schema，用 `last_trace_id` 关联"；给出三选一 |
| 5 | `json_repair` 出处＝`extraction_service.py:158-166` | **标错出处**（那里是 fence 正则 + `json.loads` + 抛错）；真实使用点 4 处（`workflow/nodes/input/input.py:140-147` 等），依赖已在 `pyproject.toml:149` | §1.3 引用订正 |
| 6 | 「模型输出＝不可信输入」出处＝`sql_guard.py:44-50` | **行号错位 2 行**（实为 `:46-51`）且更贴切锚点在 `field_validator.py:1-7`；061 design 决策 8 在 SQL 场景**否决**执行前确认 → D5 的强制确认是产品决策而非该原则推论 | §1.3 引用订正 + 加限定语 |
| 7 | F060 提案路径（检出内不存在） | 该文件在**上游** ref（`source_github/main`，上游 F060；本仓对应 F031，裸写 F060 会被读成 term-library） | 引用改为上游 ref + 标注编号差异 |
| 8 | 「新扫描任务须显式 task_routes」 | **不完整**：`task_routers` 非空即整表替换（实测），本检出形态下改默认不生效 | §3/§4 改为"派发显式传 `queue=`" |
| 9 | 缺"版本/上下线关系、删除级联、失败洪泛、NL 是否含修改、一次性任务" | 需求完整性审查提出的 5 个 high/medium 缺口 | 补写为 D13–D17 |
| 10 | 缺"编辑者如何拿到服务账号、运行归因与留存" | 技术风险审查提出的闭环缺口 | 补写为 D18–D19；§1.5 补运行归因实证 |

> 另有 5 条低危已被直接吸收：C2 双库字段口径（§4.15）、时间类功能确定性验证（§4.17）、`task_always_eager` 不存在（§3 测试口径）、监控可见性按当前租户成员过滤的隐藏风险（§1.5）、存量 `crontab(str)` 隐患（§4.2）。
