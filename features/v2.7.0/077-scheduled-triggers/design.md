# F077 定时/事件触发与定时任务生成 — 设计（How）

> **本文档定位 — How 的唯一真相**
>
> spec 只回答 What（[spec.md](./spec.md)，59 条 AC，已过 ★）；本文回答 **怎么做**：模块与分层、数据模型、调度机制、状态机、接口契约、前端落点、错误码号位、已知坑。
> 本文**只保留今天的现状**（不写历史快照）；每条关键决策写清「选它 / 否定了什么、为什么 / 何时该重新考虑」；「已知坑」写成「现象 → 根因 → 对策」，避免后人重走死路。
> 调研依据（现状事实与全部 文件:行 证据）见 [discovery.md](./discovery.md)；版本级归属、不变量与错误码登记见 [release-contract.md](../release-contract.md)。
>
> **本文的产生方式**：3 面并行起草（调度内核决策 / 身份权限与接口决策 / 现状·坑·测试）→ **2 路对抗审查**（接手可用性 / spec 与契约合规）→ 主会话裁定 16 处跨节冲突后装配。裁定结论已直接写进下文（不再保留两套口径）。

**关联**: [spec.md](./spec.md)（59 条 AC，已过 ★） · [discovery.md](./discovery.md)（调研与 19 项决策） · [release-contract.md](../release-contract.md)（归属 / INV-25 / 119 段） · tasks.md（已拆解）
**版本**: v2.7.0 ｜ **特性**: 077-scheduled-triggers ｜ **最后更新**: 2026-10-16（初版）

---

## 1. 目标与非目标

**目标**：把「定时/事件触发」与「定时任务生成」做成一条**可无人值守、可审计、失败可见**的调度控制面——任务落在应用（工作流/助手）上，以 F076 服务账号专用身份运行，按 cron 或一次性时间触发（或由外部系统经 `/api/v2` 事件触发），运行结果可从任务列表一跳进既有监控。

**非目标（本期明确不做，见 spec §范围边界）**：渠道侧任何改动；上游 F108 的常驻生命周期/预算闸/机队观测；内部事件总线；代表用户运行；调度器持密钥自调 `/api/v2`；灵思与工作站对话的定时化；含多人工输入节点或文件类输入项的应用；任务级重试/全量补跑/排队；任务级时区；NL 修改既有任务；独立历史运行表；外部运行查询接口；任何新依赖与新 UI/状态库。

**成功判据**：spec 的 59 条 AC 全部可被本设计支撑，且**对既有行为的影响可枚举**——见决策 16.3 的「纯新增清单」与唯一一处既有行为变化（AC-30 的删除拦截）。

---

## 2. 关键约束（动手前必须知道）

**全局铁律遵循 [docs/constitution.md](../../../docs/constitution.md) C1–C7**（DDD 分层 / 双 DB / 多租户自动注入 / 权限统一入口 / 错误码 / 无硬编码密钥 / 前端 store 不直连 HTTP），本节不重抄。

**Constitution Check 结论**（逐条核对，无违反）：C1——新模块 `bisheng/scheduled_task/`（api/domain 分层）+ `bisheng/worker/scheduled_task/`（薄壳），两处补进 `scripts/arch-guard.sh:21` 的 `DOMAIN_MODULES`；判定逻辑全在 domain，celery 壳只做入口。C2——新表用 `dialect_helpers` 方言件，原子 UPDATE **禁 `RETURNING`**。C3——`scheduled_task` 登记 `_TENANT_AWARE_MODEL_MODULES`，`tenant_id` 用 `default=None`（静态守卫 `test/tenant/test_tenant_id_default_guard.py:1-11`）。C4——权限一律走 `ApplicationPermissionService` / `PermissionService`，不直读 `RoleAccessDao`。C5——错误码 119 段，`Code: int = N`，三语文案随 PR。C6——不落任何密钥材料。C7——platform 经 `@/controllers/request`。

本特性**特有**的硬约束：

| # | 约束 | 说明（含证据） |
|---|---|---|
| 1 | **运行租约与回收必须自建** | 全仓无运行超时：`WorkflowConf.timeout` 的唯一真实消费点是 Redis key TTL（`worker/workflow/redis_callback.py:85`，部署值 5 分钟 `bisheng/initdb_config.yaml:89-93`），`Workflow(timeout)` 只存不读；工作流 celery 任务是裸 `@bisheng_celery.task`（`worker/workflow/tasks.py:100`，对照 `worker/approval/tasks.py:16-17` 的 `time_limit=900/soft_time_limit=840`）；无僵尸清扫（卡死判别只在消费侧轮询 `redis_callback.py:238-307`）。**不建租约 ⇒ 互斥永不释放 ⇒ 一次卡死该定时任务永久静默停摆**（AC-15 的由来）。F077 自有 celery 任务一律显式声明 `time_limit/soft_time_limit`。 |
| 2 | **调度载体只能是"固定节拍扫描"** | beat 全静态（`core/config/settings.py:184-276` 逐条 `if ... not in self.beat_schedule` 补默认），仓内无 redbeat/apscheduler/croniter（`uv.lock` 0 命中）→ 动态 cron 注册不进 beat。 |
| 3 | **cron 口径三件套** | ① 唯一解析器 = celery `crontab.from_string`（仓内正确写法 `core/config/settings.py:191,275`；`:281` 的 `crontab(str)` 是陷阱）；② celery crontab **无 year 字段** → 一次性任务必须独立字段表达（D17）；③ 预览与 `next_run_at` 必须用**显式 tz-aware 锚点自算**（`remaining_estimate` 内部读真实时钟）。周取值 0–6、拒绝 `@daily` 别名（AC-06）。 |
| 4 | **派发一律显式 `queue=`** | `celery_task.task_routers` **非空即整表替换**（`settings.py:177-183`）；本检出 `bisheng/config.yaml:45-48` 只路由 knowledge → 默认的 workflow 路由整体失效。仓内全部 `execute_workflow` 派发点都显式 `queue=`（`open_endpoints/api/endpoints/workflow.py:106`、`evaluation/domain/services/evaluation_service.py:447`、`common/chat/clients/workflow_client.py:183`）。beat 条目本身也带 `options.queue`（见决策 8；先例 `file_scheduler_dispatch` 无 options，靠 task_routers——F077 不走那条靠不住的路）。 |
| 5 | **工作流执行模型的三根内存支柱不可恢复** | 整段占 Celery 线程（`workflow/graph/workflow.py:68-84` 同步 `while RUNNING`）；INPUT 暂停时运行对象驻留 **worker 内存**（`worker/workflow/tasks.py:22-24` 的 `_global_workflow`），续跑必须命中同一节点（`:134-136` 对象不在即失败）；图引擎 `MemorySaver()` 无持久化 checkpoint（`workflow/graph/graph_engine.py:295`）、无 `acks_late`。⇒ 监督与回收**不得依赖发起运行的进程存活**；续跑派发走 `workflow_stateful_worker` 绑定队列（节点存活阈值 20s、心跳 5s，`worker/utils/stateful_worker.py:24`）。 |
| 6 | **定时运行没有会话落点，归因只能靠 `last_trace_id`** | `chat_id=""` 时所有消息写入点提前 return（`redis_callback.py:581-595`）；助手侧同（v2 HTTP 无状态 → 本特性助手运行器同样 `chat_id=""`）⇒ 两型一致无会话（AC-48）；监控 `input_snapshot` 在无人值守下恒空（`common/chat/utils.py:233-244`）。`trace_id` = 运行 `unique_id`（`worker/workflow/tasks.py:100-104`）⇒ 任务表存 `last_trace_id` 一跳监控（`telemetry_search/api/endpoints/agent_transactions.py:45-62`）。 |
| 7 | **运行身份边界 = F076 专用身份，进程内装载** | 密钥属高敏凭据（INV-23）⇒ **不采用**"调度器存密钥自调 `/api/v2`"（避免密钥落盘与自调链路）；身份装载走进程内 `load_owner_payload` + 运行期防提权 `owner_is_privileged`（`open_api/domain/services/identity_service.py:35-66,98-102`）。触发请求体出现身份参数（裸 `user_id`）一律 400/11817（`open_api/api/dependencies.py:57-93`）——事件触发端点复用该依赖，不自建身份解析。 |
| 8 | **触发前复检每次触发都做，全量三查** | AC-21：应用存在且 **ONLINE**（`Flow.status` `database/models/flow.py:28-31`；`Assistant.status` `database/models/assistant.py:17,40`）+ 身份存在/启用/未提权 + 身份对应用持 `use_app`（`ApplicationPermissionService`，`permission/domain/services/application_permission_service.py:357-381`）。**注意**：v2 invoke 现状**不校验** ONLINE——本特性"下线即不执行"是新增规则，属对客差异（见 §6.1）。 |
| 9 | **租户硬约束** | 新表登记 `_TENANT_AWARE_MODEL_MODULES`（漏 → 行落 ROOT 租户）；租户自动过滤**只拦 SELECT**，bulk update/delete 与 `text()` SQL 必须手写 `tenant_id` 条件（`src/backend/AGENTS.md` §Known Pitfalls）；Beat tick 无租户 header、落 `DEFAULT_TENANT_ID`（`worker/tenant_context.py:29-37`）⇒ 跨租户枚举 `bypass_tenant_filter()` + 逐条 `set_current_tenant_id()`。 |
| 10 | **C2 双库字段与语句级红线** | JSON 列 = `JsonType`（DM 上是 CLOB：**不能索引 / 不能走 JSON 函数**）、长文本 = `LargeText`、更新时间列 = `UPDATE_TIME_SERVER_DEFAULT`（`core/database/dialect_helpers.py:193,196-226,275-286`）；cron 归一化短串用 `String`；原子 claim/回收用 `UPDATE ... WHERE` 判 `rowcount`、**禁 `RETURNING`**（DM8 多行 `RETURNING` 以 `[CODE:-5016]` 拒绝，实锤 `database/models/department.py:550-609`）；存在性判定用 inspect 助手，不用 `information_schema`。 |
| 11 | **`/api/v2` 新端点必须双登记** | 端点上 `@open_api_scope(...)` marker + `open_api/domain/scopes.py` 注册表逐条登记 `(METHOD, path)`；守卫测试**双向零差异**（`test/open_api/test_route_matrix.py:53-62`），漏 marker 运行期 500/11811。 |
| 12 | **零新依赖、零新 UI/状态库** | cron 求值用 celery 自带 `crontab`；NL 容错解析用已在依赖中的 `json_repair`（`pyproject.toml:149`，先例 `workflow/nodes/input/input.py:139-147`）；不引入 apscheduler / celery-redbeat / croniter / 前端 cron 库。NL 生成按"模型输出＝不可信输入"处理：prompt 约定 JSON + 容错解析 + **服务端硬校验**（锚点 `doc_extract/domain/services/field_validator.py:1-7`）。 |

---

## 3. 方案对比与选定（关键决策）

### 决策 1：模块名 = `bisheng/scheduled_task/`（一个名字贯穿表 / 错误码 / 领域模块；审计命名空间保留已登记的 `schedule`）

- **选定**：新领域模块 `bisheng/scheduled_task/`（布局 `<module>/{api/router.py, api/endpoints/, domain/services/, domain/models/, domain/schemas/}`，`src/backend/AGENTS.md` §Quick Map）；worker 侧薄壳 `bisheng/worker/scheduled_task/`（体例对齐 `worker/approval/`）。登记一次落全：`scripts/arch-guard.sh:21` 的 `DOMAIN_MODULES` 加 `scheduled_task`（不加则新模块对守卫隐形）；`core/database/tenant_filter.py:39-131` 的 `_TENANT_AWARE_MODEL_MODULES` 加新表模块路径；`worker/__init__.py` 补任务 import（`worker/main.py:21` 的 `include=["bisheng.worker"]` 是任务注册中枢，漏 import = NotRegistered）；`database/models/audit_log.py:216-221` 的 `_V2_NAMESPACE_TO_ACTION_PREFIX` 加 `'schedule': 'schedule.'`。
- **API 面归属（本节已裁定，以下文为准）**：v1 管理面（CRUD / 立即运行 / 可用运行身份 / cron 预览 / NL 草稿）与 v2 触发端点**都放模块内** `scheduled_task/api/`，`api/router.py` 双导出 `management_router`（挂 `/api/v1`）与 `rpc_router`（挂 `/api/v2`），先例 `open_api/api/router.py:17-25`。RULE-5 只禁跨模块 **api** 导入；模块端点引用 `open_api/domain/scopes.py` 的 `open_api_scope` 装饰器是 api→domain，放行（`scripts/arch-guard.sh:67-83` 实核）。**不落 `open_endpoints/api/endpoints/`**——那会让本特性端点散在两个模块里，且 `open_endpoints` 的既有端点全部是"薄委托 + 默认操作员"形态，与 F077 的身份模型不同源。
- **被否**：① `bisheng/schedule/`——与表名 `scheduled_task`、错误码文件 `common/errcode/scheduled_task.py` 割成两个名字；`schedule` 留给未来的「调度子系统本体」（F108 类常驻生命周期）；② 塞进 `open_endpoints`——v1 管理面（会话用户）与 v2 触发面（密钥身份）鉴权门不同源，混一个模块必然串线；③ `bisheng/task/`——过泛且撞词（仓内已有 `mark_task`、`linsight_execute_task`）。
- **何时重新考虑**：若 F108 类「调度子系统」进入范围（常驻生命周期、机队面板），引擎另开 `bisheng/schedule/`，本模块退为任务域；若产品采纳"运维全局视角"页面（discovery D11-B），management 与 ops 的职责切分再议。

### 决策 2：执行者 = Celery beat 固定节拍 tick + DB 扫描 + 单条原子 claim（Redis 锁只管"轮次互斥"，正确性只压在 claim 上）

- **选定**：beat 条目 `scheduled_task_scan`，`"schedule": 20.0`（**数值秒，不是字符串**——字符串会落进 `settings.py:278-281` 的 `crontab(str)` 隐患；默认值写进 `CeleryConf.validate`，体例 `settings.py:260-269`，YAML 可覆盖），且带 `"options": {"queue": "celery"}`（见 §5 坑 12）。tick 任务 `bisheng.worker.scheduled_task.scan::scan_scheduled_tasks`（`acks_late=True`，对齐 `worker/knowledge/scheduler.py:557-566`）→ 薄壳调 `scheduled_task/domain/services/run_service.py::run_cycle(now)`（纯编排、显式 `now`，先例 `worker/knowledge/scheduler.py:452` 的 `run_dispatch_round(*, scheduler=None)`）。一轮的流程与不变量：
  1. 取**平台级 Redis 锁**（`SET NX EX` + token + Lua 比对释放，照 `worker/knowledge/scheduler.py:308-322`；TTL 略小于 tick）——多 beat 实例下同时只有一轮在派发；
  2. **先回收**（决策 3 的租约过期回收），**再枚举到期**：单条跨租户查询 `WHERE status='enabled' AND next_run_at <= :now`（`bypass_tenant_filter()`；beat tick 无租户 header）。**扫描唯一依据 = `next_run_at`**（一次性任务创建时把 `run_at` 写入 `next_run_at`；`run_at` 只用于展示与"等待上线"语义）；
  3. **逐条复检**（AC-21，决策 9）→ 复检结果决定进入哪种处置变体（见决策 4），**claim 永远是唯一的原子写点**；
  4. claim = 单条条件 `UPDATE ... WHERE id=:id AND status='enabled' AND next_run_at <= :now AND (lease_expires_at IS NULL OR lease_expires_at <= :now) SET run_lease_token/run_started_at/lease_expires_at/last_planned_at=:slot, next_run_at=:next`，**以 `rowcount` 判是否抢到**（CAS 先例 `approval/domain/models/approval_request.py:112-134`）——抢互斥、消耗本槽、写回下次在同一语句内完成；bulk UPDATE 不吃租户自动过滤，`tenant_id` 条件手写；
  5. 派发前 `set_current_tenant_id(task.tenant_id)`（Celery header 在此刻生成，stamp 先例 `worker/knowledge/scheduler.py:521-535`）→ 派发（决策 5）→ 用完 reset。
- **tick 默认值偏离 discovery §3 的"固定 30s"**：AC-10 要求偏差 ≤30s，而 tick=30s 时最坏情形（槽位刚过一轮、轮内处理耗时）会顶破 30s。**默认取 20s 留余量**（配置可覆盖）；理由与重新评估点见 §7 的 AC-10 口径。这是**设计对 discovery 推荐值的收紧**，不改变 spec 的 ≤30s 承诺。
- **被否**：① 独立常驻调度进程（新增部署单元与存活监控，信创/离线交付成本敏感——D4）；② 动态 cron 注册进 beat（`beat_schedule` 全静态，无 redbeat/apscheduler）；③ 一次性任务用 `apply_async(eta=...)`（先例 `channel/domain/services/channel_service.py:1764-1766`）——消息在 broker 等、无 `acks_late`，且绕开互斥/并发闸/租约，等于给同一语义开第二条分发路径；④ beat 内长轮询（占死节拍线程，见决策 6）。
- **何时重新考虑**：单轮扫描量到"tick 装不下"→ 先补索引分批再考虑常驻进程；客户要求秒级触发或跨集群调度时，常驻调度器的收益才成立。

### 决策 3：租约 = 运行中标记自带 `lease_expires_at`（默认 60 分钟），回收者 = 下一 tick 的"扫描前置回收"

- **选定**：字段 `run_lease_token / run_started_at / lease_expires_at`（**存 deadline 而非时长**：改配置只影响后续租约，存量判定不漂移）。tick 第一段跨租户回收：`UPDATE ... SET run_lease_token=NULL, run_started_at=NULL, lease_expires_at=NULL, last_status='failed', last_error='运行超时（租约到期）', last_finish_at=:now WHERE lease_expires_at IS NOT NULL AND lease_expires_at <= :now`，并推进连续失败计数/通知（按 AC-54 首败口径）。第二道保险 = claim 的 WHERE 把过期租约视同空闲（回收偶发没跑也不造成永久互斥）。
- **与 celery `time_limit/soft_time_limit` 的关系：只做纵深防御、不作保证**——① 本仓 worker 用 `-P threads`（`src/backend/AGENTS.md` 命令段），线程池无法杀线程；② `execute_workflow` 是共享任务（WS 会话 / v2 invoke / 评测都在派，`worker/workflow/tasks.py:100-127`），F077 **不得给它加全局时限**；F077 自有任务（扫描、助手运行器）按 `worker/approval/tasks.py:16-17` 体例声明即可。AC-15 明确"不依赖发起运行的进程仍然存活"——把判失败建在 celery 时限上，在 `-P threads` 下等于没有。
- **被否**：① 独立清扫任务——同样由 beat 驱动，liveness 依赖一模一样，白多一个条目；② 只在 claim 里放行过期租约——被停用/无人再触发的任务永远不会被显式判失败，`last_status` 停在上一次结果，AC-15 落空；③ 复用 `WorkflowConf.timeout` 当运行超时——它只是 Redis key 的 TTL，改语义会连带断掉控制面 key 的过期策略（§5 坑 13）。
- **何时重新考虑**：运行时长上限要做成任务级/应用级分级时，字段与判定不变，只换租约值的来源；平台统一迁移到 prefork 池后，可把 celery 硬时限升为第二道保证。

### 决策 4：互斥/并发/漏跑/一次性的四种"处置变体"——复检先于 claim，claim 是唯一原子写点

**统一编排（三种入口共用）**：`复检 → 决定变体 → 单条原子 claim（写清该变体的结果）→ 派发`。复检只决定"进哪个变体"，**不自己写库**；所有写都收敛到 claim 那一条 UPDATE，因此不存在"先占租约再回滚"的路径。

| 变体 | 触发条件 | claim 写什么 |
|---|---|---|
| **NORMAL（正常占用）** | 复检通过 且 全局并发未满 | 占租约 + `next_run_at = next_runs(now,1)`（**锚定 `now`**——跨 N 个周期只补跑一次，天然免补跑风暴，AC-13）+ `last_planned_at = 本应触发的槽位`（供 AC-10 偏差对账） |
| **SKIP（周期跳过）** | 周期任务且（上一轮未结束 或 目标应用下线） | **不占租约**；`next_run_at = next_runs(now,1)`、`last_status='skipped'`、`last_error='overlap'`/`'app_offline'`、`last_finish_at=now`；不计失败、不发通知（AC-11/AC-16） |
| **FAIL（记账失败）** | 运行身份不可用 / 目标应用已删除 | **不占租约**；`next_run_at = next_runs(now,1)`、`last_status='failed'`、`last_error=原因`、推进 `consecutive_fail_count` 并按 AC-54 通知；应用已删除的**额外自动停用**（AC-31） |
| **DEFER（不消费、下轮再试）** | 一次性任务遇（上一轮未结束 或 应用下线）或 全局并发已满 | **什么都不写**（不改 `next_run_at`、不占租约、不动 `last_*`）——"只有一次机会"不因重叠或等待被消耗（AC-11/AC-16）；一次性任务遇应用下线时**另外**置 `blocked_reason='app_offline'`（幂等小 UPDATE），上线后由下一轮 NORMAL 清除 |

- **单任务互斥**：即 claim 的 `AND (lease_expires_at IS NULL OR lease_expires_at <= :now)`。
- **全局并发上限（默认 10，可配）= 平台级跨租户**：保护的是共享的 `workflow_celery` 线程池（每轮 DAG 整段占一个线程）。计数 = `SELECT COUNT(*) ... WHERE lease_expires_at > :now`（bypass 租户过滤；过期租约不计，计数自愈，不需要对账）。取数时机 = 每轮开头数一次、轮内每 claim 一个本地 +1——Redis 轮次锁保证同时只有一轮在派发，不必把计数塞进 claim 的 SQL。超限 → **DEFER**（周期与一次性一致，AC-12）。
- **立即运行 / 事件触发 = 非计划轮次，一律"只拒绝、不记账"（W2 实现期裁定）**：两者走同一 claim 原语但 `require_due=False`（用户点"立即运行"时任务通常未到期）且 `keep_next_run_at=True`——**均不改变既定计划与「下次执行」**（AC-17 的原则适用于任何非计划轮次：只有计划轮次推进计划）。遇互斥 / 全局满 / 应用下线 / 身份不可用 / 应用已删除 → 直接 `REJECT` 并回可区分的 119 段错误（11909/11910/11907/11905），不排队、不重试、**不写任务行**（失败记账的单一写入者见决策 9）。
- **一次性任务**：`schedule_type='once'` 且 `next_run_at` 初始 = `run_at`；claim 时不推进 `next_run_at`（终态时才 `disabled`，AC-08）。
- **被否**：① 立即运行/事件触发遇互斥改为**排队**——需要运行队列 + 幂等键 + 顺序语义，超一期范围，且 AC-17 已裁定"拒绝并提示"（人触发的动作要即时反馈）；② 全局闸用 Redis 计数器——多一次故障面（计数与 DB 真相可能漂移），DB COUNT 在 30s 一轮的量级下成本可接受；③ 一次性任务遇互斥直接丢弃——D17 明确"只有一次机会，不因重叠丢失"；④ 复检失败写终态再回滚租约（先 claim 后复检）——多一条回滚正确性依赖，且一次性任务会被"已派发"消耗机会，与 AC-16 冲突。
- **何时重新考虑**：多 beat 实例且 Redis 锁在故障窗口失效 → 全局上限会轮间短暂超发，届时应改带租约的 Redis 计数器或独立配额表；客户要求租户级配额 → 口径由"平台全局"改"按租户"，多一个配置项。

### 决策 5：派发适配 = 工作流复用 `execute_workflow`（显式 queue + 预写 data + `chat_id=""`），助手走进程内 `AssistantAgent`（不持密钥、不走 HTTP）；运行标识即监控 `trace_id`

- **工作流**：按 F054 序列（`evaluation/domain/services/evaluation_service.py:437-447`）：`unique_id = generate_uuid()`（即 trace_id）；`hash_key` 取 `unique_id`；`worker_node = workflow_stateful_worker.find_task_node_sync(hash_key)`；`RedisCallback(unique_id, app_id, chat_id="", user_id=run_identity_user_id)` → `set_workflow_data(copy.deepcopy(flow.data))`（**传副本**，避免写脏 ORM，先例 `open_endpoints/api/endpoints/workflow.py:95-101`）→ `set_workflow_status(WAITING)` → `execute_workflow.apply_async([unique_id, app_id, "", run_identity_user_id], queue=worker_node)`——`_execute_workflow` 从 Redis 读 data，不预写必炸 "workflow data not found"（`worker/workflow/tasks.py:66-68`）。`chat_id=""` 即不落会话/消息（`redis_callback.py:581-595`，与 AC-48 自洽）；`source` 传既有值（`"platform"`），**不新增枚举**（`NewMessageSessionEventData.source` 是 `Literal["platform","api"]`，落会话分支才会消费它，扩值会先炸 pydantic 校验——discovery 附录 A#4）。
- **助手**（**不持密钥自调 `/api/v2`**，INV-23①）：新建 `worker/scheduled_task/assistant_runner.py::execute_assistant_run`，镜像 v2 无状态核心：`AssistantAgent(assistant_info, chat_id="", invoke_user_id=run_identity_user_id)` → `init_assistant()` → `run(question)`（先例 `evaluation/domain/services/evaluation_service.py:531-547` 与 `open_endpoints/api/endpoints/assistant.py:127-146`）；async 用 `run_async_task` 包装（先例 `worker/approval/tasks.py:16-24`）；派发**显式 `queue="celery"`**（默认队列；`workflow_celery` 专供 DAG 的纪律见 `worker/config.py:11-12`）+ 显式 `time_limit/soft_time_limit`。预设输入 = 问题文本；finally 回写任务行终态（助手侧没有工作流那样的 Redis 状态面，**这是两型的不对称点**）。
- **助手侧必须补一条遥测上报点**：v2 端点自己写 `APPLICATION_PROCESS`（`open_endpoints/api/endpoints/assistant.py:274-305`），复用既有 schema、**不新增字段**。不补则 AC-52 的一跳监控对助手任务无数据。
- **`unique_id` ↔ `trace_id` ↔ 版本标识**：`execute_workflow` 入口 `trace_id_var.set(unique_id)`（`worker/workflow/tasks.py:100-104`）→ 任务行存 `last_trace_id` 即可一跳详情（AC-52）。本次所用版本标识：工作流派发时取当前 `FlowVersion.is_current=1` 的版本 id 落 `last_app_version_id`（`database/models/flow_version.py:141,218`；ONLINE 下禁切版本/改数据保证 `Flow.data`＝当前发布版本，`api/services/flow.py:155-156,218-219`）；助手无版本体系，记 NULL（AC-52 的两型口径）。
- **被否**：① 助手走 HTTP 自调 `/api/v2`——需要调度器持有并落盘密钥、再自调鉴权链路，与 INV-23 的高敏凭据边界相悖；② 给助手另建一套"带状态的会话式"执行——AC-48 明确不落会话，平白多一套身份与可见性问题；③ 工作流侧自建执行器——重复实现 DAG 引擎与 stateful 绑定，且会丢掉 F046/F052 的"监控免费获得"。
- **何时重新考虑**：工作流执行迁到独立执行服务时派发面随之改；若产品把 D8 升级为"落会话"，先扩 `Literal` + 同步存量看板口径 + 重评 INV-21，再改这里。

### 决策 6：运行监督 = 并入 tick 的**无状态过**（`observe_runs`）；否定"per-run 阻塞式监督任务"

- **选定**：每个 tick 在扫描之后跑 `run_service.observe_runs(now)`——对每个"持有未释放租约"的任务读其运行的 Redis 终态/暂停态并分流：
  1. **终态（SUCCESS / FAILED）** → `finalize`（决策 13/§4.1 第 8 步）；
  2. **INPUT（暂停）** → 若该任务配了预设输入：从事件列表取本轮输入事件（`redis_callback.py:176-181`）拿到 `node_id` 与 `input_schema`，按**静态 label/key** 映射（复刻 `build_form_payload` 口径 `evaluation_service.py:776-800`；**只有文件类项的运行时 key 每轮重生成**，而文件类在一期已被 AC-47 拒绝，故静态映射是唯一口径）→ **先** `set_user_input({node_id: payload})` **再** `set_workflow_status(INPUT_OVER)` **再** `continue_workflow.apply_async([...], queue=find_task_node_sync(hash_key))`（顺序照抄 F054 `evaluation_service.py:474-476`，反了 continue 会因取不到 input 抛 `IgnoreException` `tasks.py:134-140`；续跑必须落在挂着运行对象的同一 worker 节点）。**必须当轮完成**：input/data key 的 TTL = `WorkflowConf.timeout*60+60`（部署值 5 分钟 → 6 分钟），`get_user_input` 读后即删（`redis_callback.py:507-511`），**不得攒批或延迟重试**；写 input 成功但 continue 派发失败时**不重派**，直接交由租约到期收敛为失败（更简单、可预期）；
  3. **INPUT 且未配预设输入** → 以**条件 UPDATE（CAS）**置 `last_status='needs_input'`（`WHERE id AND last_status <> 'needs_input' AND run_lease_token=:tok`），**rowcount=1 才**按 AC-54 口径通知一次——用现有的状态列兼作"已通知"哨兵，不新增标志位；该轮不悬挂：租约到期由决策 3 回收为失败并计入连续失败（AC-46）；
  4. **租约过期且无终态** → 判 `failed`、释放租约、计连续失败（AC-15）。
- **为什么不是"随 claim 同批派出的 per-run 阻塞式监督任务"（F054 式）**：① 每个运行要独占一条 Celery 线程长轮询（最长 60 分钟），全局并发 10 就是 10 条常驻线程，收益只是"INPUT 即时答复"；② 监督进程死亡即监督消失，而 AC-15 明确"不依赖发起运行的进程仍然存活"；③ 观察动作只是"读 Redis + 写一条 DB"，无状态过单轮毫秒级返回，不占死 tick。**代价**：INPUT 答复延迟 ≤1 个 tick（默认 20s）——工作流单轮本就是分钟级，可接受；且必须在 6 分钟的事件 TTL 内完成（tick 停摆超过 TTL 时该轮由租约回收为失败，不悬挂）。**何时重新考虑**：产品要求秒级 INPUT 答复 → 引入独立监督任务或加大 tick 频率（届时要同时解决"监督者随进程死亡"的问题）。

### 决策 7：触发计划 = `crontab.from_string` 单一解析器；下一跳时间 = 显式 Asia/Shanghai tz-aware 锚点自算，绝不回填 `remaining_estimate`

- **选定**：解析唯一入口 `crontab.from_string`（正确写法先例 `settings.py:191,275`），**禁 `crontab(str)`**（`:281` 的既有隐患）。纯函数集 `domain/services/cron_schedule.py`：`validate_and_parse`（`ValueError` → 11903）、`normalize`（折叠连续空白）、`describe`（人类可读）、`next_runs(now, n)`（下次/未来 N 次）。归一化后的 5 段短串存 `cron_expression`（`String`，非 CLOB）。
- **下一跳算法自算**：`anchor` 显式 tz-aware（`ZoneInfo("Asia/Shanghai")`），只复用 `crontab.from_string` 解析出的字段集合（`minute/hour/day_of_month/month_of_year/day_of_week`），按"日粗筛 + 当天时分展开"推进，复刻 DOM/DOW 同时受限时的 OR 语义，扫描上限若干年兜底闰日表达式（`0 0 29 2 *`）。**不得用 `remaining_estimate` / `is_due().next` 回填**：实测它们以**真实 now** 为基准（`crontab.from_string('0 9 * * *').remaining_estimate(datetime(2026,10,16,8,0))` 返回 `-7:45:57`），拿它加锚点或连续回填必然漂移。
- **时区边界**：DB 存 naive 本地时间（平台列惯例，`worker/config.py:12-13` `Asia/Shanghai` + `enable_utc=False`）；纯函数入参出参 tz-aware，读库值后补 `ZoneInfo("Asia/Shanghai")` 再算；调用侧用 `datetime.now(ZoneInfo("Asia/Shanghai"))` 而非裸 `datetime.now()`。**预览与列表"下次执行"共用同一函数**（AC-07 要求同口径）；复杂表达式回退结构化描述；不引入 cron 描述库。
- **被否**：① `crontab(str)`（静默错解析，§5 坑 1）；② 自写正则解析表达式——解析语义与执行必须同源，否则"预览说的"与"实际跑的"会分叉；③ 引入 croniter/描述库——spec 明确排除新依赖。
- **何时重新考虑**：客户要求秒级或 `@别名` → 属范围变更，先评审；celery 大版本升级改变 `crontab` 解析语义时重跑对拍用例（§7）。

### 决策 8：全部可配项 = 新配置段 `scheduled_task`（`core/config/scheduled_task.py`）；beat 条目留在 `CeleryConf`；**本特性无总闸**

- **选定**：新建 `core/config/scheduled_task.py` 的 `ScheduledTaskConf(BaseModel)`（先例 `core/config/open_api.py`），`Settings` 加字段 `scheduled_task`（体例 `settings.py:844` 的 `open_api`）；`config.yaml` 新增 `scheduled_task:` 段。

| 键 | 默认 | 依据 |
|---|---|---|
| `global_max_running` | `10` | AC-12 全局并发上限 |
| `max_tasks_per_app` | `20` | AC-05 |
| `run_timeout_seconds` | `3600` | AC-15 租约上限（60 分钟） |
| `consecutive_failure_threshold` | `5` | AC-55 |
| `replay_window_seconds` | `300` | AC-37（±5 分钟） |
| `event_rate_limit_per_minute` | `10` | AC-38 任务级放大量护栏 |
| `preset_input_max_items` / `preset_input_max_chars` | `20` / `2000` | AC-44 |
| `nl_max_chars` | `2000` | AC-41 |
| `preview_count` | `5` | AC-07 |
| `dispatch_lock_ttl_seconds` | `15` | 决策 2（略小于 tick） |

- **没有 `enabled` 总闸**（与决策 16 一致，本节为定稿）：所有路径都是纯新增，没有需要灰度隔离的存量行为；现场要停调度可直接停 beat 条目。**何时重新考虑**：客户要求"分租户灰度"或"现场先关自动触发、只留管理面" → 加部署级开关（默认 true；关时不注册 beat 条目、管理面照常）。
- **beat 节拍不放进本段**：`CeleryConf.validate` 已按"缺省补齐 + YAML 覆盖"管理 beat 条目（`settings.py:171-283`），复制一份即两处真相。默认值必须用**数值秒 20.0**（避开 `:278-281` 的 `crontab(str)` 隐患），并带 `options: {queue: celery}`。
- **为什么放 YAML 不放 DB**：① 消费方全是 beat/worker 进程，进程启动即读；② 这些是平台级（跨租户）运维旋钮；③ 与 `fair_scheduler` 先例一致；④ DB 配置层有 100s 级缓存 TTL，误改排查成本更高。**推翻触发点**：一旦做"运维界面紧急停用失控任务"，该开关必须进 DB——YAML 改完要重启，救不了急。

### 决策 9：运行身份装载与复检 = 入口级"三查"必做 + 助手运行器内二次断言；全程走 `PermissionService` 单入口、不自建缓存

- **选定**：
  1. **装载复用 F076 现成件**：`load_owner_payload`（`open_api/domain/services/identity_service.py:35-66`）+ `owner_is_privileged`（`:98-102`）。任务行同时锁存 `service_account_id` 与 `run_identity_user_id`；触发期先断言该 SA 行仍存在、未停用、且 `resource_owner_user_id == run_identity_user_id`——**换 owner 后的旧身份不再是任何服务账号的专用身份**，一律 fail-closed 11905（AC-18 的"运行期锁定"由此精确落地）。
  2. **入口级复检（必做，三处共用同一函数）**：新建 `scheduled_task/domain/services/trigger_guard.py`（纯函数 + 显式 `now`），被扫描器（claim 之前）、事件触发端点、立即运行端点三处调用。四查：目标应用存在且 ONLINE（`Flow.status` / `Assistant.status`）、SA 行可用、owner 可装载且未提权、owner 对目标应用持 `use_app`。
  3. **助手运行器内再断言一次**（自有任务体，零额外成本）：它本就要 `load_owner_payload` 拿身份去执行，顺手断言"应用仍 ONLINE + 身份仍可用"，失败按 AC-22 记失败。**工作流路径不做执行级复检**——它复用共享的 `execute_workflow`（不得修改），且续跑要落在别的进程；**由此存在一个已写明的 TOCTOU 窗口**：从 claim 到 worker 真正开始执行之间（队列等待），撤权/下线不会中断这一轮——与 F076"吊销不中断已建立会话"同精神，对客文档写明。
  4. **AC-23 的 ≤10 秒达成方式 = 复用判定链、不自建缓存**：撤权经 `PermissionService.authorize(revokes=…)` 会同步失效权限缓存（`permission/domain/services/permission_service.py:375-380`）→ 通常即时；绕过该失效路径的变动由 L2 TTL=10s 兜底（`permission/domain/services/permission_cache.py:19`）→ 最坏 T+10s 生效。"可用运行身份"查询与触发复检共用同一条链，同享该窗口。**注意区分两个窗口**：判定生效 ≤10s（AC-23）；**已派发的一轮不中断**。
- **被否**：① 只在扫描器复检一次——事件触发/立即运行的同步拒绝语义落空，且队列等待期窗口不可控；② 只靠 worker 复检——一次性任务会被"已派发"消耗唯一机会（违反 AC-16）、下线应用白占并发槽、事件触发拿不到即时拒绝；③ 每次直查 FGA、禁缓存——每触发 ×3 面各一次 FGA 往返，只买到"0 秒 vs ≤10 秒"的差价，且绕过 `PermissionService` 即绕开 C4 单入口；④ F077 自建复检结果缓存——叠加第二个 TTL 直接击穿 ≤10s 上界。
- **代价与边界**：复检失败原因对外收敛为 11905 一类（细分原因由 `trigger_guard.describe_failure` 给出短标记、写进 `last_error`，如 `app_offline,owner_privileged`）；**复检失败按变体记账**——身份不可用 → 计划轮次 FAIL（计连续失败，AC-22）；应用下线 → 周期 SKIP / 一次性 DEFER / 事件与立即运行拒绝（AC-16）。
- **失败记账的单一写入者（W2 实现期裁定，2026-10-16）**：**只有计划轮次写失败状态与计数**；事件触发与立即运行遇身份不可用或应用已删除时一律回 `REJECT`（端点同步回 11905/11907），**不写任务行**——否则同一故障会被"事件 + 下一轮扫描"重复计数，提前触发 AC-55 的自动停用。任务的健康由计划轮次在一个 tick 内必然判到（AC-22 的"按失败记账"由此不被多入口分叉）。
- **何时推翻**：客户要求撤权零窗口 → 触发期改用 `PermissionService.check(..., consistency="HIGHER_CONSISTENCY")`（`permission_service.py:82-105` 已支持），代价是每触发一次 FGA；要求"FGA 故障时放行"则与 fail-closed 冲突，须先改不变量。

### 决策 10：知识可见性 = 复用既有身份通路（零新参数）；创建期按"绑定引用 × 开关"逐个判定；运行期不回退（结构性成立）

- **选定**：
  1. **注入运行身份的唯一方式是运行链路的既有身份参数**：工作流 `execute_workflow(..., user_id=运行身份)` → 节点内 `self.user_id`（`workflow/nodes/base.py:44`），`flow_user_id` 由 `_execute_workflow` 从 `Flow.user_id`（配置者）解析（`worker/workflow/tasks.py:59-62,85`）→ F041 两档判定（`workflow/common/knowledge.py:383`：`identity_user_id = self.user_id if 开关 ON else self.flow_user_id`）**自动成立**；助手侧注入点 `AssistantAgent(invoke_user_id=运行身份)`（`api/services/assistant_agent.py:355-366`）。**不需要显式传"知识身份"参数、不需要改节点与 RAG 链路**——定时运行与交互运行在节点内不可区分，INV-21"同一份判定、不得按入口分叉"结构性保证。
  2. **创建期校验（AC-29，`domain/services/knowledge_guard.py`）**：`collect_app_knowledge_refs(...)` 解析引用——工作流遍历 `Flow.data` 收集知识检索节点（rag / knowledge_retriever / qa_retriever / agent 内联知识）及每节点 `user_auth` 开关（读取口径同 `workflow/common/knowledge.py:213-222`）；助手取 `knowledge_auth`（`database/models/assistant.py:52-57`）+ 绑定链接，**抽用运行期同一解析逻辑**（`api/services/assistant_agent.py:334-347`）为共享函数，避免两套解析。对**开关为 ON 的每个引用**，用运行身份 payload（`abuild_scoped_login_user(owner_user_id, tenant_id)`，`knowledge/domain/services/space_flow_retrieval.py:44`）逐个判定读权限——文档库走 `knowledge/domain/services/knowledge_flow_retrieval.py:196` → `knowledge_rag.py:158-163`（`judge_knowledge_permission`，`knowledge/domain/models/knowledge.py:571-600`）的**同一函数**，空间走 `space_flow_retrieval.py`；任一无授权 → 11913 拒绝，错误回带缺授权范围清单。开关 OFF 的引用不查（按配置者可见范围检索是 F041 有意的"安全有界共享"，INV-25④ 已登记，不是待修缺陷）。
  3. **运行期"授权被撤不得回退"是结构性成立**：ON/OFF 由开关值决定，与授权结果无关——不存在"ON 判定失败后重试 OFF"的代码路径；F077 的义务是**不引入任何 catch-后回退**，并用 e2e 锁定（开关 ON + 撤权 → 空命中/降级、本轮不因此判失败）。
  4. QA 检索节点硬编码 `check_auth=False`（`workflow/nodes/qa_retriever/qa_retriever.py:29-33`）——既有口径，交互与定时一致，**不改**（改它会变更既有交互行为，属独立特性）。
- **被否**：① 新增显式"知识身份"参数——在既有链路开第三个口径，与 INV-21 正面冲突，且要动节点与 RAG 全链；② 创建期"全引用一律要求授权"（含 OFF 档）——把 F041 的默认档误伤成"定时场景必须逐个授权"；③ 运行期授权缺失时回退配置者范围"保任务成功"——spec 已明确禁止。
- **何时推翻**：F041 的 `user_auth` 语义演进（多档判定）时，创建期校验器与共享解析器跟随；客户要求 OFF 档也按运行身份收窄 → 先回 F041 重评 INV-25④。

### 决策 11：应用级权限 = 每入口独立 `edit_app` 服务端强制；「可用运行身份」= 新只读端点（`edit_app` 鉴权、零凭据信息）

- **选定**：
  1. 六个管理动作（列表 / 创建 / 编辑 / 启停 / 删除 / 立即运行）在**服务层入口**逐个调 `ApplicationPermissionService.has_any_permission_async(login_user, app_type, str(app_id), ["edit_app"])`，不通过 → `UnAuthorizedError`（403 信封，`common/errcode/http_error.py:4-6`）——复用 `api/services/workflow.py:965-972` 的样板。管理员准入由 `PermissionService` 五级短路自动获得（超管 `permission_service.py:101-104`、租户管理员 `:1061-1067`），**不另立特权路径**（AC-26）。每个入口独立校验（AC-25），不做"一个路由级依赖管全部"——`app_id` 来源各入口不同，拒绝文案需按动作可读；**不得只依赖前端门控**（反例 `share_link/domain/services/share_link_service.py:13-30`）。
  2. 「可用运行身份」只读端点（D18 落地）：`GET /api/v1/scheduled-tasks/available-identities?app_id=&app_type=`——鉴权 = 登录态 + 上述 `edit_app` 守卫（403）；返回本租户 SA 清单，逐项 `{service_account_id, name, owner_user_id, owner_name, enabled, has_use_app, usable}`（`usable = enabled ∧ has_use_app ∧ owner 未提权`），满足 AC-24 的"区分可用 / 已列但缺运行权限"；**不返回任何密钥、凭据、scope 信息**，也不提供任何 SA 管理能力。数据来源：`ServiceAccountDao.alist`（`open_api/domain/models/dao.py:271`，租户过滤在列）+ 逐 owner 用同一权限判定（每租户 SA 数小，无 N+1 压力）。
  3. 事件触发的"授权范围"是**另一条独立判定**（密钥所属 SA 对任务目标应用持 `use_app`，**且任务归属该 SA**，见决策 12），与编辑者 `edit_app` 互不替代——两个面各自的准入在 AC-25 / AC-33 分开登记。
- **被否**：① 新增应用级权限位 `manage_schedule`（D9=B）——权限模板双镜像 + 前端对账 + 回归成本，收益无客户需求锚点；② 可用身份端点沿用 `get_tenant_admin_user`——编辑者档永远拿不到运行身份，D3=C 在编辑者档落空；③ 端点顺带返回密钥/凭据信息——违反 INV-23① 与 AC-24 明文。
- **何时推翻**：客户要求"定时任务管理权与编辑权分离"→ 按 D9=B 升级为独立权限位；F004 判定的短路口径变化时本决策跟随，不自行实现。

### 决策 12：事件触发面 = `POST /api/v2/schedule/trigger`（新 scope `schedule:trigger`）+ 恒强制密钥；防重放 = Redis `SET NX EX`；白名单拒绝落在端点入口

- **选定**：
  1. **路径与登记**：`POST /api/v2/schedule/trigger`（模块内 `scheduled_task/api/endpoints/trigger.py` 自带 `rpc_router`，加入 `api/router.py` 的 `router_rpc`，与 F076 `open_api_rpc_router` 同型）；`open_api/domain/scopes.py` 的 `OPEN_API_SCOPES` 增一项 `schedule:trigger`（`(("POST", "/api/v2/schedule/trigger"),)`）。两道守卫自动覆盖：漏 marker → 500/11811；注册表 ↔ 路由双向零差异（`test/open_api/test_route_matrix.py:44-66`）。管理面权限位目录（`/api/v1/open-api/scopes`）自动多出该位，供管理员签发。
  2. **恒强制密钥**（不受 F076 总闸影响）：F076 的 `enforce_keys` 关时"完全旧行为"的语义是**不改变既有调用方**；触发端点是纯新增路由、没有旧行为，故入口显式断言 `get_open_api_principal() is not None`（读法同 `open_endpoints/domain/utils.py:130-137`），否则 401/11801——不改 F076 任何文件的路由级依赖语义。**启用事件触发的前置条件**（对客文档必写）：部署级 `open_api.enforce_keys` 打开 + 调用方持有效密钥（租户开关不影响带密钥请求）。
  2a. **任务归属校验（2026-10-22 安全收口）**：加载任务后、白名单/`use_app` 前校验 `task.service_account_id == principal.service_account_id`，不匹配一律 404/11809（与不存在/停用不可区分）。否则 A 账号 Key 可触发 B 账号任务，而任务按 B 的运行身份执行（confused deputy）。跨账号集中触发不再允许；需要集中运维时为运维账号单独建任务。参见 spec AC-33。
  3. **防重放**：请求头 `X-Bs-Timestamp`（epoch 秒）+ `X-Bs-Nonce`（16–64 字符高熵随机串）。校验顺序：缺头 / 超窗（|now − ts| ≤ `replay_window_seconds`，默认 300）→ 400/11912；nonce 原子占用 = `SET sched:nonce:{nonce} 1 NX EX <2×窗口>`（**必须单次往返**，不得两步 `setnx+expire`——`core/cache/redis_conn.py:86-105` 的 `asetNx` 是两步实现，不采用；先例 `worker/knowledge/scheduler.py:308-312`）→ 占用失败 → 400/11912。Redis 不可用 → 503/11812 fail-closed。
  4. **请求体白名单**：body 仅 `{task_id, inputs?}`。拒绝额外字段**不用 `extra='forbid'`**（其 `RequestValidationError` 走 422 信封，与 AC-35 的 400 冲突），而在端点入口对原始 JSON 做键集合差集校验，非空 → 400/11916；缺 `task_id` / 类型错走既有 422（沿用既有信封，HTTP 200）。裸 `user_id` **复用** F076 依赖层的先行拒绝（`open_api/api/dependencies.py:57-93` → 400/11817），不在 F077 重写；`inputs` 内的键值**不参与身份判定**（身份在任务上锁定），与 F076 只扫顶层同口径。
  5. **响应形状**：受理即 `resp_200({"task_id": …, "run_trace_id": "<unique_id>"})`——`run_trace_id` = 运行 `unique_id` = 监控 `trace_id`；fire-and-forget，不等运行完成（AC-34）。**不提供外部运行查询接口**（对客差异②）。
  6. **护栏**：任务级互斥与计划轮次共用同一原子 claim，竞争失败 → 409/11910（**拒绝而非幂等**，AC-38）；全局并发闸满 → 409/11909；任务级频率上限（默认 10 次/分钟，Redis 固定窗口 `INCR` + 首写 `EXPIRE`）超限 → 429/11911（可带 `Retry-After`）。以上拒绝均不产生运行、不计连续失败、不改变"上次结果"。
- **被否**：① `POST /api/v2/schedule/{task_id}/trigger`（路径参数）——与 AC-35"请求体仅接受任务标识 + 入参映射"的字面口径不符，且 task_id 进 URL 多一份访问日志暴露面；② nonce 落 DB——热路径读写 + 要清理作业；Redis NX+EX 单次往返即原子；③ 防重放信息进 body——污染白名单口径；④ 触发面沿用总闸语义（关时匿名可触发）——新端点无旧行为可兼容，匿名触发属安全退化；⑤ `extra='forbid'` + 422——与 AC-35 的 400 相悖。
- **何时推翻**：客户 ESB 不接受 409/429 → 退回 400 + 119 码区分（一处映射表）；将来引入请求签名（HMAC）时 timestamp/nonce 并入签名方案，不并存两套。

### 决策 13：审计 = 低频**同步 best-effort** 落 `audit_log`；`schedule.run` 只取证不进白名单；通知去重用状态列 CAS 哨兵

- **选定**：
  1. **动作命名**（枚举落 `scheduled_task/domain/constants.py`）：管理动作 `schedule.task.create / update / enable / disable / delete / run_now`；运行动作 `schedule.run`。
  2. **白名单两处登记**：后端 `audit_log.py` 的 `_UI_VISIBLE_V2_ACTIONS`（`:178-211`，精确匹配）加 6 个管理动作、`_V2_NAMESPACE_TO_ACTION_PREFIX`（`:216-221`）加 `'schedule': 'schedule.'`；前端镜像 `platform/src/controllers/API/log.ts`：`getModulesApi` 加模块项、`actions` 加 6 行、**`getActionsByModuleApi` 的 switch 补 `case 'schedule'`**（漏它则模块筛选下拉为空——既有 `doc_extract` 同缺口的修正参照 `src/test/logActions.test.ts`）+ 三语 key（camelCase，避 i18next 的 `.` 嵌套）。守卫测试照 `test/doc_extract/test_audit_lockstep.py:1-54`。
  3. **operator 口径**：管理动作 = 发起人 `user_id`；`schedule.run` = **任务当前运行身份 user_id**（`ainsert_v2` 自动反查显示 `svc-*`，`audit_log.py:407-421`）；系统自动处置（超时判失败、达阈值自动停用）= `operator_id=0` → `'system'`。写入 `target_type='scheduled_task'`、`target_id=task_id`、metadata 只存标识与摘要（不含密钥、对话原文、输入正文——AC-51/INV-11）。
  4. **写入方式 = 同步 best-effort**（`ainsert_v2` 包 try + `logger.exception`，失败不改变业务结果；范式 `doc_extract/domain/services/audit.py:32-72`）。**写点**：`schedule.run` 写在**本模块自己的 `finalize`（worker 进程内、与遥测同类 best-effort）**，**不改共享的 `execute_workflow`**（它被 WS 会话 / v2 invoke / 评测共用，且 `chat_id=""` 无法与 F054 评测区分）。被否：复用 `call_audit_service` 有界队列——那是 API 进程内的高频"每请求一写"设施，而 F077 的运行动作由 tick 观察收敛、管理动作是人工低频点击；且审计页要求"操作完即可查"，2s 批量窗口反而制造困惑。
  5. **站内信**：`send_generic_notify`（`message/domain/services/message_service.py:400-421`）；收件人 = 创建者（任务行锁定）；两个 action_code：`scheduled_task_failed`（首败与达阈值共用，后者正文含"已自动停用"）与 `scheduled_task_needs_input`；两者都不在 `notification/external/_payload.py:6-35` 外发白名单 → 默认不外发（AC-56）。client 加文案键 + 三语（`client/src/components/NotificationsDialog.tsx:43-70` 的 `NOTIFICATION_ACTION_TEXT_KEYS`）。
  6. **两点式去重（首败 + 达阈值 + needs_input）**：首败由 `consecutive_fail_count` 的 `0→1` 转移唯一触发；阈值通知由 `N-1→N` 唯一触发；needs_input 通知由 **CAS 写 `last_status='needs_input'` 的 rowcount=1** 唯一触发（决策 6）。三个转移都在持租约的原子写入点，天然幂等；**不新增通知标志位**。
  7. **收件人兜底**：创建者已停用/删除 → 当前租户管理员 + 全局超管（`tenant/domain/services/inbox_helper.py:63-87`）；全部解析为空 → `logger.error` 大声记录（不静默，AC-32）；发送本身 best-effort。
- **被否**：① 每败一条通知（D15=A）——单任务最坏 1440 条/天，且平台前端不消费站内信；② 独立通知状态表 / `last_notified_*` 字段——三个状态转移已能唯一确定"该不该发"；③ 在共享的 `execute_workflow` 里写定时审计（见 4）；④ 管理动作走异步队列——低频人工动作，不值得牺牲"读己之写"。
- **何时推翻**：事件触发高频致 `schedule.run` 写入 >10 条/秒 → 改有界队列 + 丢包计数（复用 F076 参数与告警）。

### 决策 14：前端落点 = `apiComponent` 第 4 项 + 按 `edit_app` 增补门控（不继承 `share_app`）；cron 输入/预览自绘；新命名空间 `scheduledTask`

- **选定**：
  1. **挂载**：「对外发布」容器 `components/bs-comp/apiComponent/index.tsx` 的 `menuItems`（`:42-58`，现 3 项）追加第 4 项「定时任务」→ 新组件目录 `apiComponent/scheduledTask/`（列表 + 创建/编辑弹窗 + NL 输入 + 确认页）。助手与工作流共用同一面板，两个挂载点不动（`editAssistant/index.tsx:170-172`、`flow/Panne.tsx:233-235`）。
  2. **入口可见性按 `edit_app` 判定、不继承 `share_app`**（AC-57 的实现方式）：`:38` 的 `usePermissionIds(permissionType, resourceIds, ['share_app'])` 改为 `['share_app','edit_app']`；原 3 个菜单项仍仅 `canManageShare` 时列出（**既有页签口径不变**），「定时任务」仅 `canEditApp` 时列出；整面板早退（`:74-82`）改为 `!canManageShare && !canEditApp` 才拦截；`activeMenu` 初值从写死的 `'api-access'` 改为"第一个可用项"。管理员经 `usePermissionIds` 的 admin 短路自动可见；前端权限缓存只影响入口显隐滞后，服务端强制不受影响。
  3. **组件选型（bs-ui 既有件，零新库）**：列表 = `table`/`badge`/`tooltip`/`switch`/`pagination`；弹窗 = `dialog`/`input`/`select`/`textarea`；危险动作确认 = `bsConfirm`；提示 = `use-toast`。样板 `pages/SystemPage/components/ServiceAccount/index.tsx`。**列表必须含**：人类可读触发计划、下次执行、上次结果（失败原因 Tooltip）、启停；**AC-48 的提示文案**（"定时运行不产生应用会话与消息，结果请从任务列表与监控查看"）落在创建/编辑弹窗与 NL 确认页。资源归因展示：任务行用 `last_trace_id` 一跳既有监控详情，**监控侧字段与筛选维度零改动**（AC-52）。
  4. **cron 输入与预览自绘**：输入 = 单行 `input`（5 段文本；服务端是唯一解析器）；预览 = 调预览端点取"人类可读解释 + 未来 5 次"（服务端算，前端只渲染）；一次性 = 日期 + 时间输入并标注「服务器时区 Asia/Shanghai」（AC-08/AC-09）；**不引入任何前端 cron 库**。NL = `textarea` + 后端解析 + 可编辑回填（同构先例 `AutoPromptDialog.tsx`），确认页完整回显人类可读计划与下次运行时间（AC-42）。
  5. **i18n**：新 ns `scheduledTask` 登记进 `platform/src/i18n.js:45`（与 F076 `serviceAccount` 同型）；三语文件 `public/locales/{en-US,zh-Hans,ja}/scheduledTask.json`；审计筛选键进 `bs.json`；错误码文案进 `packages/locales`；client 通知键进 client 三语。
  6. **API 层**：新建 `platform/src/controllers/API/scheduledTask.ts`（+ `types/api/scheduledTask.ts`）；错误经 `captureAndAlertRequestErrorHoc`，119 段文案由 `controllers/request.ts:56-61` 自动落三语；**不写 403 分支**（root §4 硬规则）。
- **被否**：① 独立组件挂到编辑器其他位置——违反 AC-57"在「对外发布」中"，且助手/工作流会分叉两套入口；② 继承整面板 `share_app` 门控——`edit_app` 编辑者看不到（违反 AC-57）；③ 放宽原页签口径——违反"面板其他页签可见性口径不变"；④ 引入 cron 组件/描述库——spec 明确排除，且会养成第二套解析口径。
- **何时推翻**：产品要求运维全局视角（D11-B 二期 tab）或 client 端管理入口 → 另立交互与权限设计。

### 决策 15：错误码 119 段（11901–11916，其中 11916 为本文新增）与三语文案

- **选定**：新建 `common/errcode/scheduled_task.py`（模块 119）；每类写作 `Code: int = N`（`check-i18n.mjs` 的正则据此对账，无三语文案直接 CI 红）；基类 `ScheduledTaskErrorBase(BaseErrorCode)` 携 `HttpStatus`，注册 F077 专用处理器：**请求带 `open_api_call` 标记（密钥鉴权的 `/api/v2` 调用）→ 落真 HTTP 状态；否则委托既有信封路径**——v1 管理面口径逐字不变（403/404 仍走 `UnAuthorizedError`/`NotFoundError` 的既有信封与前端拦截器）。

| 码 | 语义（定稿） | v1 管理面 | v2 触发面 |
|---|---|---|---|
| 11901 | 任务不存在 / 无权限访问 / 跨租户 | 由既有 404（`NotFoundError`）承载，不独立抛 | 由 11809/404 承载（防枚举） |
| 11902 | 任务配置非法（必要项缺失 / 应用类型非法 / 预设输入非法或超限） | 信封 | — |
| 11903 | 周期表达式非法（非 5 段 / 秒级 / 越界 / 别名） | 信封 | — |
| 11904 | 每应用任务数超限（回带当前数量） | 信封 | — |
| 11905 | 运行身份不可用（不存在 / 停用 / 被提权 / 无目标应用运行权限） | 信封 | 409 |
| 11906 | 创建者无目标应用管理权限 | **由既有 403（`UnAuthorizedError`）承载** | — |
| 11907 | 目标应用不可用（已下线 / 已删除：事件触发与立即运行拒绝执行） | 信封 | 409 |
| 11908 | 应用存在启用中的定时任务不可删除（回带数量 + 引导下一步） | 信封 | — |
| 11909 | 同步触发被拒（**立即运行**：已有运行中 / 全局已满；**事件触发**：全局已满） | 信封 | 409 |
| 11910 | 事件触发被拒（该任务已有运行中） | — | 409 |
| 11911 | 事件触发频率超限 | — | 429（可带 `Retry-After`） |
| 11912 | 触发请求防重放拒绝（缺/超窗时间戳、随机数重复） | — | 400 |
| 11913 | 知识范围无授权（创建期，回带缺授权范围清单） | 信封 | — |
| 11914 | 目标应用不支持定时化（多人工输入节点 / 文件类输入项） | 信封 | — |
| 11915 | 自然语言生成结果非法（草稿被拒、不落库） | 信封 | — |
| **11916** | **【本文新增】触发请求体非法（白名单外字段 / 入参映射非法）** | — | 400 |

- **11916 的新增依据**：11901–11915 无号位承载"白名单外字段"（AC-35 与 11817 并列的另一类拒绝）；挤进 11902 会让"创建/编辑配置非法"与"触发请求非法"共码，与 F076"每类拒绝独立码"的口径相悖。release-contract 的 119 行已声明"号位以 design 阶段定稿为准"，**随本文回填 11916**（纯追加）。
- **复用不重造**：11801–11805/401、11807/403、11811/500、11812/503、11817/400（v2 面）；11809/404 承载任务不可见。**不重复申领任何 118 段码。**
- **v2 新增两个状态码**：**409 / 429**（F076 词表 401/403/404/400/500/503 之外）——对客文档单列。
- 三语同批范围（AC-59）：`packages/locales/src/api_errors/{zh-Hans,en,ja}.json` 的 11902–11905、11907–11916 + platform 新 ns + client 通知键。11901/11906 由既有码承载，**不落文案**（避免 frontend-only dead key）。
- **被否**：① 11906 独立成类——与平台"权限拒绝一律 403 信封"（F057/F062 先例）及 AC-24/25 的"一律 403"双写冲突；若对客错误码清单要求独立可见，切换为独立 11906 类（403 语义）即可（记录为评审可切换项）；② 白名单外字段塞进 11912——防重放与字段越界是两类可修复动作；③ 全部 119 码在 v1 也落真 HTTP 状态——会改变平台 v1 的 403/404 拦截器行为（`platform/src/controllers/request.ts:160-166`），故按 `open_api_call` 标记分面。
- **何时推翻**：v2 整面改造状态码词表时随 F076 同步改；11916 若被评审否决，备选并入 11902（代价：分类变糊）。

### 决策 16：兼容与开关 = 无特性级开关（全新增量）；唯一既有行为变化 = AC-30 的两处拒删

- **选定**：
  1. **本特性不需要任何新开关**（无 `enabled` 总闸，与决策 8 一致）。所有路径纯新增：新表、新 beat 条目、新路由（`/api/v1/scheduled-tasks*` 与 `/api/v2/schedule/trigger`）、新面板项、新审计命名空间、新 i18n ns；既有 44 条 v2 路由零改动（F076 三道守卫继续通过）。
  2. **事件触发的前置开关（外部依赖，非本特性开关）**：部署级 `open_api.enforce_keys` 必须打开（决策 12.2）；对客文档与验收表登记该前置条件。
  3. **纯新增清单（对既有零影响）**：新表（`create_all` 建表，无需建表 revision；登记租户过滤与 `DOMAIN_MODULES`）；新 beat 条目（既有条目逐字不动）；新路由；遥测/监控零改动（不新增 schema 字段、不改筛选维度，只存 `last_trace_id`）；NL 用新槽位 `scheduled_task_llm`（未配置回退租户默认对话模型，F061 口径；`ConfigKeyEnum` 加枚举值）；运行不落会话、不触碰既有会话链路。
  4. **唯一对既有动作的可感知变化 = AC-30**：删除工作流/助手时，若存在启用中的定时任务则拒绝（11908 + 数量 + 引导）。插入点两处：`api/v1/flows.py:34-58` 的 `delete_flow` 与 `api/services/assistant.py:487-518` 的 `delete_assistant`。保证：0 命中时零副作用（纯读查询）；无定时任务的应用删除流程逐字不变。
- **被否**：① 加部署级 `scheduled_task.enabled` 总闸——没有存量行为要隔离，开关只多一个"开了但没人知道为什么没跑"的排查面（现场要停调度可直接停 beat 条目）；② 加租户级灰度开关——无客户灰度需求；将来要分租户灰度，建租户级设置表而非复用部署配置；③ 顺手修 `crontab(str)` 隐患——独立 hotfix。
- **何时推翻**：客户要求分租户灰度或"现场先关自动触发、只留管理面"→ 加部署级开关（默认 true；关时不注册 beat 条目、管理面照常）；AC-30 的拒删被客户认为打断既有运维流程 → 改"告警不阻断"需先回 spec 改 AC-30。

---

## 4. 系统现状（接手必读）

### 4.1 一次定时触发的完整链路

**三个入口汇聚到同一条编排链**：beat tick 扫描（计划轮次） / `POST /api/v2/schedule/trigger`（事件触发） / `POST /api/v1/scheduled-tasks/{id}/run`（立即运行）。链路 = **复检 → 变体判定 → 原子 claim → 派发 → worker 执行 → 监督过（tick）→ 终态回写 → 通知/审计**。

1. **tick 扫描**：beat 条目 `scheduled_task_scan`（默认 20s）→ 薄壳 `worker/scheduled_task/scan.py::scan_scheduled_tasks` → `run_service.run_cycle(now)`（纯编排、显式 now）。轮首取平台级 Redis 锁（多实例只有一个扫描轮）。
2. **回收 + 枚举到期**：先按决策 3 回收过期租约；再在 `bypass_tenant_filter()` 下单条跨租户查询 `status='enabled' AND next_run_at <= now`（索引 `(status, next_run_at)`）。
3. **逐条复检**（决策 9 的入口级三查）→ 决定处置变体（NORMAL / SKIP / FAIL / DEFER，决策 4）。
4. **原子 claim**（决策 2 第 4 步；单一写点，含 `next_run_at` 的推进与 `last_planned_at` 的槽位留痕）。
5. **派发**：`set_current_tenant_id(task.tenant_id)` → 工作流派 `execute_workflow(queue=worker_node)` / 助手派 `execute_assistant_run(queue="celery")` → reset。事件触发与立即运行**不走** due 判定，只走 3→4→5 的同一组函数（入口差异只在拒绝码与变体细节）。
6. **worker 执行**：工作流复用现有引擎，终态写 Redis `workflow:{unique_id}:status`（`worker/workflow/tasks.py:36-51`），`finally` 自动报 F046/F052 应用遥测 ⇒ 监控收录**免费获得**；助手在自有运行器的 `finally` 里补一条同构 `APPLICATION_PROCESS`（决策 5）。
7. **监督过**（`observe_runs(now)`，每个 tick）：读持有租约任务的运行态 → 终态则 `finalize`；INPUT 则按决策 6 分流（预设输入自动提交 / CAS 写 `needs_input` + 通知一次）；租约过期则判失败。
8. **终态回写（`finalize`）**：任务行 `last_run_at / last_finish_at / last_status / last_duration_ms / last_error（摘要截断）/ last_trace_id / last_app_version_id`；释放租约；`consecutive_fail_count` 递增（仅 failed）或清零；**一次性任务终态即自动 `disabled`**；按 AC-54 发首败/阈值通知；写 `schedule.run` 留痕（operator = 运行身份）。

### 4.2 关键数据结构

**表 `scheduled_task`**（新建；SQLModel `table=True` 走 `create_all`，整表新建**不需要建表 revision**——`src/backend/AGENTS.md` §Schema ownership）：

| 字段 | 类型（切法） | 可空 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | int PK autoincrement | 否 | — | 与 `db_datasource` / `user_app_favorite` 同款 |
| `tenant_id` | int | 否 | **`default=None`**（静态守卫要求） | 参与复合索引 |
| `name` | String(128) | 否 | — | |
| `description` | String(512) | 是 | — | |
| `app_id` | String(64) | 否 | — | 应用 id（`user_app_favorite.app_id` 同款 max_length=64） |
| `app_type` | int | 否 | — | `FlowType` 值：assistant=5 / workflow=10 |
| `schedule_type` | String(16) | 否 | server_default `'cron'` | `cron` / `once` |
| `cron_expression` | String(64) | 是 | — | **归一化后**的 5 段短串（`String` 非 CLOB，C2） |
| `run_at` | DateTime | 是 | — | 一次性任务的绝对时间（服务器时区口径）；**只用于展示与"等待上线"语义**，扫描不看它 |
| `service_account_id` | int | 否 | — | 运行身份所属服务账号（触发期断言 `resource_owner_user_id` 一致） |
| `run_identity_user_id` | int | 否 | — | F076 专用身份 user id（INV-25 的运行期锁定；换 owner 即失效） |
| `preset_inputs` | `JsonType` | 是 | — | `[{key,label,value}]`，**静态 label 键**；≤20 项 × 2000 字符；DM 上为 CLOB，**只整读整写、永不作为查询条件** |
| `status` | String(16) | 否 | server_default `'enabled'` | `enabled` / `disabled` |
| `next_run_at` | DateTime | 是 | — | **扫描唯一依据**；once 初始 = `run_at`；每轮 claim/skip/fail 后推进到 `next_runs(now,1)` |
| `last_planned_at` | DateTime | 是 | — | 本轮"本应触发"的计划槽位（AC-10 偏差对账用） |
| `last_run_at` | DateTime | 是 | — | 本次运行开始时间 |
| `last_finish_at` | DateTime | 是 | — | 本次运行结束时间（AC-14 的"结束时间"，`last_duration_ms` 的姊妹列） |
| `last_status` | String(16) | 是 | — | `success` / `failed` / `skipped` / `needs_input`；**`needs_input` 兼作"已通知"哨兵**（CAS 写） |
| `last_duration_ms` | int | 是 | — | 结束 − 开始 |
| `last_error` | String(512) | 是 | — | 失败/跳过原因摘要（截断），如 `overlap` / `app_offline` / 超时；只存标识与摘要 |
| `last_trace_id` | String(64) | 是 | — | 监控一跳的唯一归因字段（= 运行 `unique_id`） |
| `last_app_version_id` | String(64) | 是 | — | 本次所用版本标识（工作流＝当轮 `FlowVersion.is_current=1` 的 id；助手 NULL） |
| `blocked_reason` | String(32) | 是 | — | 非消费性阻塞原因（当前只有 `app_offline`）：一次性任务等待上线期间置位，条件满足后清除；**不进 `last_status`**（避免污染"上次结果"列） |
| `run_started_at` | DateTime | 是 | — | "运行中"标记的开始时间（列表可见，AC-14） |
| `run_lease_token` | String(64) | 是 | — | claim 令牌（释放/续期核对，防误释放他人租约） |
| `lease_expires_at` | DateTime | 是 | — | claim 时算出的 **deadline**（存 deadline 而非时长，改配置不漂移存量） |
| `consecutive_fail_count` | int | 否 | server_default `0` | 仅 failed 计入；success 清零；skipped / needs_input 不计 |
| `created_by` | int | 否 | — | 创建者（通知收件人创建时锁定）；与 `run_identity_user_id` 分离 |
| `create_time` / `update_time` | DateTime | 否 | `CURRENT_TIMESTAMP` / `UPDATE_TIME_SERVER_DEFAULT` | 表参数沿 `mysql_charset=utf8mb4, mysql_collate=utf8mb4_unicode_ci` |

**索引**：`(status, next_run_at)`（跨租户到期扫描——最热查询）；`(tenant_id, app_id, app_type)`（管理面列表、每应用任务数上限 AC-05、删除拦截计数 AC-30）；`(lease_expires_at)`（监督过回收）。**唯一约束：不设**——`name` 允许重名；AC-05 的"计数后插入"接受极小并发越界窗口（同一应用并发创建），不做额外锁。

**为什么这样切**：单表 + `last_*` 摘要，不建独立运行历史表（D10/D19）——"同一任务至多一个运行"使任务行本身就是运行时状态的最佳载体，互斥/租约/展示三件事在同一条 UPDATE 里收口；`run_at` 与 `next_run_at` 并存是因为"原始承诺时间"（列表回显、等待上线）与"扫描派生时间"是两种语义；`preset_inputs` 用 `JsonType` 整存（上限受控、无独立查询面）；`(app_id, app_type)` 引用而非外键（应用两型分表，删除由 AC-30 + AC-31 双保险覆盖，不引入级联）。

**谁在消费这张表**：本模块的扫描（`(status,next_run_at)`）、监督过（`lease_expires_at`）、管理面列表与校验（`(tenant_id,app_id,app_type)`）；跨模块**没有**直接读者——外部（前端、对接方）拿到的是 HTTP 契约（§6.1），监控归因靠 `last_trace_id` 跳进而非读本表。**对外可见的字段契约** = §4.2 表 + §6.1 的接口响应；其余字段（租约、计数、阻塞原因）是实现细节，改它们不影响对外。

### 4.3 关键模块职责

**新建（分层 `scheduled_task/{api/{router,endpoints}, domain/{models,services}}`；worker 侧薄壳另放 `worker/scheduled_task/`）**：

| 模块 | 做什么 | **不做什么** |
|---|---|---|
| `domain/models/scheduled_task.py` | SQLModel `ScheduledTask` + `ScheduledTaskDao`（CRUD + claim/release 条件 UPDATE + 按应用计数） | 不做权限判定；不派发；bulk SQL 手写 tenant 条件 |
| `domain/services/cron_schedule.py` | 纯函数：`validate_and_parse`（唯一用 `crontab.from_string`）、`normalize`、`describe`、`next_runs(now, n)` | 不读时钟（`now` 入参）；不写库；不引第三方 cron 库 |
| `domain/services/run_planner.py` | 纯决策：`decide_due`、`merge_catchup`、`overlap_disposition`、`cap_room`、`lease_state` | 不碰 DB/Redis/celery（单测直调） |
| `domain/services/trigger_guard.py` | 入口级复检纯函数（应用 ONLINE / SA 可用 / owner 未提权 / `use_app`），显式 `now` | 不写库；不装载 celery；三处入口共用同一函数 |
| `domain/services/knowledge_guard.py` | 创建期知识校验：`collect_app_knowledge_refs`（与运行期同一解析逻辑）+ 对 ON 档引用逐个判读权限 → 11913 | 不改 RAG 链路；不引入第三种可见性口径 |
| `domain/services/run_service.py` | 编排：`run_cycle(now)`（回收 + 扫描）、`observe_runs(now)`、`recheck`、`claim`、`dispatch`、`finalize`、`run_now`、`accept_trigger`（防重放/频控/防枚举） | 不实现 celery 壳；不解析请求对象；不读密钥材料 |
| `domain/services/task_service.py` | 管理面：CRUD、创建期校验组合（AC-20）、删除拦截钩子（AC-30）、可用运行身份查询（AC-24）、列表组装 | 不绕过 `ApplicationPermissionService`；不返回密钥/凭据 |
| `domain/services/nl_draft_service.py` | NL→草稿：槽位模型调用（新槽位 `scheduled_task_llm`，未配回退租户默认对话模型）+ `json_repair` + 服务端硬校验，**草稿不落库** | 不持久化草稿；不信任模型输出（非法 → 11915） |
| `domain/constants.py` | 审计动作名等枚举（防拼写漂移） | — |
| `api/endpoints/tasks.py` / `nl.py` | `/api/v1/scheduled-tasks*`（CRUD / 预览 / 立即运行 / 可用身份）与 NL 草稿端点 | 不 import `database/models`（RULE-3）；不做终审 |
| `api/endpoints/trigger.py` | `/api/v2/schedule/trigger`：受理 + 返回运行标识；防重放/频控/白名单 | 不等运行完成；不提供外部运行查询；不接受身份输入 |
| `api/router.py` | `management_router`（挂 `/api/v1`）+ `rpc_router`（挂 `/api/v2`）双导出 | — |
| `worker/scheduled_task/scan.py` | celery 薄壳 `scan_scheduled_tasks` → `run_cycle()` | 不写业务判定；不占线程等待运行 |
| `worker/scheduled_task/assistant_runner.py` | `execute_assistant_run`：助手无人值守执行（镜像 v2 无状态核心），`finally` 补遥测 + 回写终态 | 不落会话/消息；不支持多输入/文件类（创建期已拒） |

其余新建：`common/errcode/scheduled_task.py`（11901–11916）、`core/config/scheduled_task.py`（`ScheduledTaskConf`，见决策 8 键表）、`packages/locales` 119 段三语文案、platform 面板新 i18n ns（登记 `platform/src/i18n.js:45`）、`src/backend/test/scheduled_task/`（§7）。

**既有文件改动点（动什么 / 为什么必须在那里动）**：

| 文件 | 动什么 | 为什么必须在那里动 |
|---|---|---|
| `core/config/settings.py` | ① `CeleryConf.validate` 增 `scheduled_task_scan` 默认条目（20.0 + `options.queue=celery`）；② `Settings` 增 `scheduled_task` 字段 | beat 全静态：不在这里补默认条目，存量部署升级后拿不到新调度 |
| `worker/__init__.py` | import 新任务 | 任务注册中枢（`include=["bisheng.worker"]`），漏 import = NotRegistered |
| `api/router.py` | include 模块的 management_router 与 rpc_router | 全局路由唯一注册处；rpc 挂在带 F076 鉴权依赖的 `router_rpc` 下 |
| `open_api/domain/scopes.py` | `OPEN_API_SCOPES` 增 `schedule:trigger` | scope 注册表是权限位唯一真相；守卫测试双向零差异 |
| `database/models/audit_log.py` | `_UI_VISIBLE_V2_ACTIONS` +6、`_V2_NAMESPACE_TO_ACTION_PREFIX` +`'schedule'` | 白名单与模块映射唯一处；漏登记 = 审计行写了但审计页看不到 |
| `platform/src/controllers/API/log.ts` | 镜像表 +6、模块项、`getActionsByModuleApi` 补 `case 'schedule'`、三语 key | 与后端白名单双份手维护、有测试断言一致性 |
| `core/database/tenant_filter.py` | `_TENANT_AWARE_MODEL_MODULES` 增新表模块路径 | 漏登记 = 行落 ROOT 租户（`tenant_filter.py:74-78` 的明文警告） |
| `scripts/arch-guard.sh` | `DOMAIN_MODULES` 增 `scheduled_task` | C1 的机器执行臂 |
| `api/v1/flows.py`（`delete_flow`）与 `api/services/assistant.py`（`delete_assistant`） | 删前查"启用中的定时任务数" >0 则拒绝（11908 + 数量 + 引导） | 应用删除两条链的唯一点；现状两条链均无引用检查 |
| `common/models/config.py` | `ConfigKeyEnum` 增 `SCHEDULED_TASK_LLM` | 模型槽位枚举唯一处 |
| `platform/src/components/bs-comp/apiComponent/index.tsx`（+ 两处挂载点） | `menuItems` 增第 4 项；整面板早退改为逐项门控；`activeMenu` 初值改"第一个可用项" | 面板门控就写在这里；不改则无 `share_app` 的编辑者永远看不到新入口（AC-57 落空） |
| `client/src/components/NotificationsDialog.tsx` | 加两个 action_code 的文案键 + 三语 | 站内信消费面的文案映射唯一处 |

---

## 5. 已知坑 / 反直觉事实

1. **`crontab(str)` 是静默陷阱** —— 现象：`crontab("30 2 * * *")` 抛 `ValueError: Invalid weekday literal`，而 `crontab("sun")` **不报错**、静默解析成 `minute={0}`（每小时都跑）。根因：`crontab()` 构造器把字符串当"weekday 字面量"，不解析表达式。对策：一律 `crontab.from_string`；校验错误以它的异常为准，不自写正则。**这是本仓既有隐患**（`settings.py:281` 对 config.yaml 的字符串表达式就是这么转的），**不顺手修**（独立 hotfix）。
2. **crontab 无 year 字段** —— 现象：`0 9 16 10 *` 的下个触发在**次年**同日期。对策：一次性任务走独立 `schedule_type=once`（"日期型 cron + 执行后停用"会失败/跳过后次年再触发，D17 已否决）。
3. **`remaining_estimate` 用的是真实 now** —— 现象：预览/`next_run_at` 直接调它并与入参相加或连续回填，序列随时间漂移（测试里每次结果不同）。根因：`celery/schedules.py:572-639` 内部读真实时钟（实测 `-7:45:57`）。对策：自算（决策 7）——显式 tz-aware 锚点 + 取整到分钟 + `now` 全部入参；预览与列表"下次执行"共用同一函数。
4. **INPUT 暂停会被遥测记成 SUCCESS，且一轮报两行** —— 现象：成功率被污染，一个 form 型运行在监控里两行（execute + continue）。根因：`worker/workflow/tasks.py:111` 的 `run_status` 只看 `sys.exc_info()`，INPUT 不抛异常；两段共享 trace_id。对策：归因/统计按 `trace_id` 去重（`agent_transactions.py:197-203` 的 collapse 注释已实证）；**定时运行自身的成败口径不用遥测**——用监督过按 Redis 终态回写的 `last_status`。
5. **监控没有 task 维度，`input_snapshot` 无人值守恒空** —— 现象：同一应用多个定时任务在监控里不可区分；复盘"这个任务跑了什么输入"无据。根因：`ApplicationProcessEventData` 无 task/source 字段（`event_data_schema.py:145-165`）；`input_snapshot` 按 chat_id 取最新 question 行，`chat_id=""` 时无行可查。对策：唯一归因 = `last_trace_id` 一跳；**不动公共遥测 schema**（扩字段在 D8=A 下无生产者，且牵动存量看板口径）；输入摘要二期再做。
6. **站内信管道无去重/聚合/限频，且平台前端不消费站内信** —— 现象：逐次失败都发 ⇒ 最坏 1 条/分钟、单任务 1440 条/天，而第一现场（平台）根本看不到。根因：`message_service.py:65-103` 直落库；平台前端无消费面。对策：去重责任放任务侧（`consecutive_fail_count` 状态转移 + needs_input 的 CAS 哨兵）；**第一现场做成任务列表状态列**。
7. **新 action_code 默认不外发** —— 现象：`scheduled_task_failed` 不会走 E+ 外发。根因：`notification/external/_payload.py:6-35` 的白名单 + `message_service.py:96-101` 钩子。对策：AC-56 正要求"默认不外发、登记才外发"——行为自洽；要外发须登记白名单（并回写 spec）。
8. **审计白名单与前端镜像表两处手维护、有测试锁步** —— 现象：`schedule.task.*` 漏进任一处 = 审计行写入但审计页/筛选项永远看不到（无报错）。对策：两处同 PR 改 + namespace 映射 + `getActionsByModuleApi` 的 `case 'schedule'`（漏它则模块筛选下拉为空）+ i18n key（camelCase 避 i18next 的 `.` 嵌套）。
9. **新表漏登记租户过滤会落 ROOT 租户，且"表还能用、测试还能过"** —— 现象：静默跨租户。根因：`_TENANT_AWARE_MODEL_MODULES` 是启动期一次性快照（`tenant_filter.py:39-132`）。对策：建表同 PR 登记 + 租户用例（`test/open_api/test_models_tenant.py` 范式）；`tenant_id` 用 `default=None`（静态 AST 守卫）。
10. **Beat tick 没有租户 header** —— 现象：tick 内直接查租户表被 ROOT 上下文过滤成空或抛错。对策：跨租户枚举 `bypass_tenant_filter()`；**派发前**逐条 `set_current_tenant_id`（Celery header 在此刻生成）。
11. **多实例部署下 beat 各自触发** —— 现象：两个 beat 同一 tick 各跑一轮 ⇒ 同任务双派发（若 claim 不原子）。对策：**两把锁都要**——平台级 Redis 锁串行化扫描轮 + DB 原子 claim（防与立即运行/事件触发并发，`rowcount` 判定）。
12. **扫描任务不显式指定队列会掉进默认 `celery` 队列** —— 现象：只跑 `workflow_celery`/`knowledge_celery` 的部署永远不执行扫描（任务投到没人消费的队列，无反压、无报错）。根因：`task_routers` 非空即整表替换（`settings.py:177-183`），本检出只路由 knowledge。对策：**beat 条目带 `options.queue='celery'`**（先例 `file_scheduler_dispatch` 不带 options、靠 task_routers——F077 不走那条靠不住的路）+ 部署文档写明"默认队列 worker 必须常驻"。
13. **"工作流超时"根本不存在** —— 现象：卡死的运行让互斥永不释放（叠加互斥 = 永久静默停摆）。根因：`WorkflowConf.timeout` 只是 Redis key TTL（部署值 5 分钟）；任务无 `time_limit`；`WorkFlowWaitUserTimeoutError`(10527) 无生产者；无僵尸清扫。对策：租约 + 每 tick 回收；新 celery 任务显式 `time_limit/soft_time_limit`。**别去"修" `WorkflowConf.timeout`**——它被其它链路当 TTL 消费。
14. **运行对象驻留内存、无 checkpoint** —— 现象：worker 重启/节点死亡后运行不可恢复；INPUT 续跑若被派到别的节点必失败。根因：`_global_workflow` 内存字典 + `MemorySaver()` + `continue_workflow` 取不到对象即失败。对策：续跑派回 `find_task_node_sync(hash_key)` 的绑定节点；**把"节点死亡"当一类运行失败由租约回收**，不试图恢复内存对象。
15. **INPUT 的运行时 key：只有文件类项每轮重生成** —— 现象：预设输入若按"运行时 key"存，下一轮对不上、静默丢失。根因：文件类项 key 每次重生成 `file_<uuid8>`（`workflow/nodes/input/input.py:60-68`），普通字段的运行时 key 就是静态 key。对策：预设只按**静态 label/key** 存（AC-44），答复时用当轮事件里的 `input_schema` 做 label→runtime key 映射（F054 同款）；**一期已拒绝文件类输入（AC-47），静态映射是唯一口径**。
16. **F076 密钥属高敏凭据 + 请求禁身份参数** —— 现象：任何"调度器存密钥自调 `/api/v2`"的方案都要求调度器持有并落盘密钥，与 INV-23 的凭据边界相悖；触发体里带 `user_id` 一律 400/11817。对策：身份装载走进程内 `load_owner_payload`；事件触发端点复用依赖装好的 principal，**不新增任何身份输入面**。
17. **DM8 禁 `RETURNING`；JSON 列在 DM 上是 CLOB** —— 现象：多行 `UPDATE ... RETURNING` 被 DM 以 `[CODE:-5016]` 拒绝；对 JSON 列用 JSON 函数/建索引直接失败。对策：claim/回收判 `rowcount`（F076 DAO 同款 `result.rowcount or 0`）；cron 归一化串用 `String`；**不做任何基于 `preset_inputs` 的 SQL 侧查询**（要查整读后在 Python 过滤）。
18. **监控可见性按"当前租户成员集合"过滤且 5 分钟缓存** —— 现象：专用身份被移出租户/停用后，该租户看**历史**运行记录会消失。根因：`telemetry_search/domain/services/call_logs.py:17-37` 按 `UserTenant` 当前成员求 user_ids + 300s 缓存。对策：一期接受现状（spec §5 的最后一条待澄清即此）；**任务列表的 `last_*` 是稳定落点**（不受监控侧过滤影响），对客文档写明"运行明细请从任务列表进入"。
19. **`-P threads` 池杀不掉线程** —— 现象：给任务加 `time_limit` 不等于能中断执行。对策：celery 时限只作纵深防御，真正的"判死"在租约（决策 3）；**不要因为加了 time_limit 就以为卡死有解**。

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 位置 | 说明 |
|---|---|---|
| 事件触发接口 | `POST /api/v2/schedule/trigger` | 头：`Authorization: Bearer bs-sak-…` + `X-Bs-Timestamp` + `X-Bs-Nonce`；体：`{task_id, inputs?}`；回：`{task_id, run_trace_id}`。真 HTTP 状态 + 信封码（**新增 409 / 429** 两个状态码）。**不提供运行结果查询接口** |
| 定时任务管理 API | `/api/v1/scheduled-tasks*` | CRUD / 启停 / 立即运行 / cron 预览 / NL 草稿 / 可用运行身份；权限 = 目标应用 `edit_app`（服务端强制） |
| 权限位 | `open_api/domain/scopes.py` 的 `schedule:trigger` | 出现在管理面权限位目录（`/api/v1/open-api/scopes`），供管理员签发给对接方 |
| 审计动作名 | `schedule.task.create/.update/.enable/.disable/.delete/.run_now`（进审计页白名单）、`schedule.run`（只落库取证） | 对客/运维可据此筛选与对账 |
| 站内信 action_code | `scheduled_task_failed`、`scheduled_task_needs_input` | 默认不外发；要外发须登记 `notification/external/_payload.py` 白名单 |
| 配置段 | `config.yaml` 的 `scheduled_task:`（见决策 8 键表） | 运维旋钮；改后需重启（YAML 层） |
| **两条对客差异（必须登记进验收表与迁移文档）** | ① 定时/事件触发下**应用下线即不执行**（周期跳过 / 一次性等待上线 / 事件与立即运行拒绝）——与既有 `/api/v2` 调用面不校验上线状态不同；② 事件触发**只返回受理与运行标识**，不提供外部运行查询 | release-contract 变更历史 2026-10-16 行已登记 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 来自 | 用途 / 边界 |
|---|---|---|
| 服务账号专用身份（创建 / 装载 / 防提权 / 停用断链） | F076（`open_api/domain/services/{service_account_service,identity_service}.py`） | 运行身份的全部生命期；**只读复用，不修改其任何行为** |
| `/api/v2` 鉴权依赖与既有 118 段码 | F076（`open_api/api/dependencies.py`、`common/errcode/open_api.py`） | 事件触发的凭据校验、防枚举、身份参数拒绝（11801–11805/11807/11811/11812/11817） |
| 运行遥测与监控查询 | F046 / F052（`worker/workflow/tasks.py` 的 finally 上报、`telemetry_search/**`） | 定时运行"监控免费获得"；**不新增字段、不改筛选维度**；助手侧**新增一处上报点**（决策 5，已登记 release-contract） |
| 权限判定 | `PermissionService` / `ApplicationPermissionService`（C4 单入口） | 应用级 `edit_app` / `use_app`、管理员五级短路、撤权缓存失效（AC-23 的 ≤10s 来源） |
| 应用与工作流执行链 | `Flow` / `Assistant` 模型、`execute_workflow`、`AssistantAgent`、`workflow_stateful_worker` | 派发与执行；**共享任务不加时限、不改语义** |
| 知识检索可见性判定 | F041（`user_auth` / `knowledge_auth`）与 F057/F062 的既有判定链 | 创建期校验（11913）+ 运行期判定；**接入同一份判定，不新增口径**（INV-21/INV-25④） |
| 审计表 / 站内信 / 三语生成链 / 配置装配 | 既有平台设施 | 写入与展示；均只追加，不改既有键值 |
| 调度基础设施 | Celery（beat 静态注册 + `crontab.from_string`）、Redis | 计划轮次与计时；**零新增依赖** |

**风险点速查（上游改什么会破坏我）**：F076 改 `load_owner_payload` / `owner_is_privileged` / 118 段码或依赖层行为 → 复检与事件触发面出错（复检依赖其返回语义）；F046 / F052 改遥测 schema 或监控查询的筛选维度 → 一跳归因断裂（`last_trace_id` 跳过去查不到）；`execute_workflow` 改参数签名 / queue 语义 / `_global_workflow` 的内存模型 → 派发与 INPUT 续跑失败（**共享任务，不得改**）；`workflow_stateful_worker` 的绑定算法或心跳阈值变更 → 续跑派错节点；F041 改 `user_auth` / `knowledge_auth` 语义 → 知识可见性口径分叉（须先回评 INV-25④）；应用权限模板改档位映射（`viewer/editor` 与 `view_app/use_app/edit_app` 的对应）→ `edit_app` / `use_app` 判定漂移；`audit_log` 的白名单或 `ainsert_v2` 的 operator 约定变更 → 审计可见性与归属出错（有锁步测试兜底）；`notification` 外发白名单机制变更 → 通知默认不外发的承诺需重验。

---

## 7. 测试与可观测

- **单元 / 契约测试**（落 `src/backend/test/scheduled_task/`，体例同 `test/open_api/`）：
  - **cron 纯函数**：`validate_and_parse / normalize / describe / next_runs` —— AC-06 的"接受清单"（数字 / 范围 / 列表 / 步进 / `mon-fri`）与"拒绝清单"（秒级 / 非 5 段 / `@daily` / 周 `7` / 越界 / 多空格的归一化）逐条参数化；`next_runs` 用**显式 `now` + 固定 tz-aware 锚点**断言序列；做一次"与 celery 在注入同一 nowfun 后一致"的对拍回归。
  - **调度判定纯函数**：`run_planner` 的 due / 漏跑合并（跨 N 周期只一次）/ 重叠分流（周期 SKIP、once DEFER）/ 并发闸 / 租约过期边界（`lease_expires_at == now` 两侧）全部用显式 `now` 参数化；**不引用 `task_always_eager`**（全仓零命中，本仓测试不用）。
  - **编排测试（注入 mock 协作对象）**：`run_cycle` / `observe_runs` 注入 stub（`MagicMock(spec=...)`）+ 可 patch 的派发间接层，断言 `apply_async` 的 `queue=`、claim 的 UPDATE 条件与 `rowcount` 分支、通知/审计被调与去重（首败一条、needs_input 一次）；写法照 `test/knowledge/test_file_scheduler_dispatch.py`；陈旧行用"相对现在"构造（照 `test_file_scheduler_reconcile.py:68-69`）。celery 任务体只做薄壳：测试直调领域函数，不依赖 broker/beat。
  - **四种处置变体**：NORMAL / SKIP / FAIL / DEFER 各至少一条正反用例；**DEFER 必须断言"什么都没写"**（不改 `next_run_at`、不占租约）；一次性任务遇下线**不消耗机会**（`blocked_reason` 置位 → 上线后被 NORMAL 清除并只跑一次）。
  - **AC-02 的停用跨期**：停用期间越过多个计划点 → 重新启用后**首个 tick 不触发**（`next_run_at` 在 enable 时重算为 `next_runs(now,1)`）。
  - **管理面契约**：权限矩阵 allow/deny 配对（无 `edit_app` → 403；超管 / 租户管理员放行）；11901–11916 每条拒绝路径精确断言（码 + 文案齐备）；`created_by` 与 `run_identity_user_id` 分离（创建期四条件各一条反例）。
  - **NL 草稿**：非法模型输出（坏 cron / 不存在应用 / 超尺寸 / 缺必要项）→ 11915；**草稿不落库**（调用前后表行数不变）；输入为空 / 超 2000 字符拒绝。
  - **守卫（静态，四道）**：① `_UI_VISIBLE_V2_ACTIONS` ↔ `log.ts` 镜像锁步 + `schedule` namespace 映射（照 `test/doc_extract/test_audit_lockstep.py`）；② scope 注册表 ↔ 路由双向一致（`test_open_api/test_route_matrix.py` 自动覆盖新端点）；③ 新表租户回填 + `tenant_id default=None`；④ **`schedule.run` 不进白名单**（对齐 `test/open_api/test_audit.py:225` 同法）。
- **e2e 口径**（`test/e2e/test_e2e_scheduled_triggers.py`；数据前缀 + 双 cleanup，依赖真中间件）：**造数 + 手工触发 scan + 立即运行替代真实等待**——① 造一条 `next_run_at` 置过去的任务 → 调 `run_cycle(now)`（或 celery call）→ 断言列表 `last_status/last_trace_id`；② 按 `last_trace_id` 查监控交易列表；③ 站内信（收件人 = 创建者，首败一条）；④ 多租户：租户 A 的任务不在租户 B 出现；⑤ 权限矩阵：无 `edit_app` 建任务 403、运行身份无 `use_app` 触发期拒绝；⑥ 事件触发全链（受理 → 重复 nonce 拒绝 → 运行中再触发拒绝 → 频控）；⑦ 漏跑合并 / 重叠跳过 / 一次性顺延（`run_planner` 的 e2e 镜像）；⑧ 连续失败达阈值自动停用 + 通知；⑨ 跨账号任务触发（Key A 触发 Sa B 的任务）→ 404/11809，同账号任务仍受理。时间类断言只允许固定短 sleep 等批量落库窗口（先例 `test/e2e/test_e2e_openapi_auth.py:188-195`）。
- **AC-10（≤30s）的测量口径**：偏差 = **`last_planned_at`（计划槽位）→ claim 完成时刻**；预算 = tick 周期（默认 20s）+ 轮内处理余量 < 30s；**因 SKIP/DEFER 顺延造成的偏差不计**（spec AC-10 的排除项）。验收时若现场想收紧，改 `celery_task.beat_schedule` 的条目值即可（配置层，不需改码）。
- **门禁**：`uv run pytest test/scheduled_task/`（**CI 现状无 pytest 作业**——测试靠开发机与集中回归，勿把"CI 会跑"当前提）；`arch-guard`（`DOMAIN_MODULES` 补名）；`pnpm check-i18n`（119 段三语 + 生成物对账）；alembic 单 head（整表新建，无建表 revision）。
- **手工验证一遍**（本地可复现；命令均单行可粘）：
  1. 起服务：`cd src/backend && export config=config.yaml && uv run uvicorn bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1`；四个终端分别起 knowledge / workflow worker、`beat`，以及——**必须**——默认队列 worker：`uv run celery -A bisheng.worker.main worker -l info -c 100 -P threads -Q celery -n default@%h`（扫描任务落这里，不起它则"任务投出去没人消费"）。
  2. 平台 `http://localhost:3001` → 系统 → 服务账号：建账号 → 签发密钥（复制明文，仅此一次）→ 授权该账号对目标工作流 **editor 档**（须 editor 才持有 `use_app`）。
  3. 打开目标应用编辑页 →「对外发布」→ 左侧「定时任务」（有 `share_app` 时为第 4 项）：手动建一条 `*/2 * * * *`（运行身份选刚建的服务账号），确认列表立即出现、显示人类可读计划与下次执行；或先 NL 输入「每小时提醒一次」→ 确认页应回显「每小时的第 0 分钟」+ 下次运行时间 → 修改后保存（草稿不落库，点保存才落）。
  4. 点「立即运行」→ 列表出现运行中；≤1 个 tick 后上次结果 = 成功，可点「监控」一跳进入该次运行详情（对上 `last_trace_id`）。
  5. 事件触发（密钥取自第 2 步）：`curl -s -X POST -H "Authorization: Bearer bs-sak-<key>" -H 'Content-Type: application/json' -H "X-Bs-Timestamp: $(date +%s)" -H 'X-Bs-Nonce: n1' -d '{"task_id":"<id>"}' http://localhost:7860/api/v2/schedule/trigger` → 200 + `run_trace_id`；**nonce 换成别的值再发一次 → 200（新运行）**；**把 nonce 换回 `n1` → 400/11912**；`X-Bs-Timestamp` 取 `date +%s` ± 3600 → 400/11912；运行中（换新 nonce）再触发 → 409/11910；body 加 `"user_id":1` → 400/11817；body 加 `"app_id":"x"` → 400/11916。
  6. 下线处置：把该工作流置为下线 → 手工触发一轮（`uv run celery -A bisheng.worker.main call bisheng.worker.scheduled_task.scan.scan_scheduled_tasks`）→ 列表显示「已跳过（应用已下线）」且连续失败计数不变；上线后按计划恢复。
  7. 失败闭环：停用运行身份（系统 → 服务账号 → 停用）→ 触发一轮 → 创建者收到首败站内信一条（再失败不重复）；连续失败达 5 次任务自动停用并再收一条。
  8. 审计对账：平台审计页按 `schedule.task.*` 筛选可见创建/启停/立即运行；`select action,operator_id,target_id,create_time from audit_log where action like 'schedule.%' order by create_time desc limit 20;` 应同时看到 `schedule.run`（**不进**审计页白名单）。
- **可观测口径**：日志用 loguru `{}` 占位（禁 printf `%s`；禁 `exc_info=`，异常用 `logger.exception`）。关键节点与关键字：`schedule.scan.round`（due/claimed/skipped/deferred/reclaimed 计数）、`schedule.run.finalized`（task/trace/status/duration）、`schedule.run.reclaim`、`schedule.run.needs_input`、`schedule.trigger.replay_rejected`、`schedule.nl.draft`（只记尺寸与结果）——**任何日志不得出现密钥材料、对话原文与输入正文**（INV-11/AC-51）。指标（可选）复用 `common/services/metric_log.py::emit_metric`（新 domain 默认放行，无需改 `_DOMAIN_SWITCH`；`metric_log.enabled=false` 时零开销）。

---

## 7.1 手工验收修复（2026-10-20）

针对 `docs/v2.7.0-manual-2026-10-19/report.md`（**本地证据，不入库**）的验收缺口，实现期修正如下（不改变对客契约）：

1. **`last_run_at` 写入点 = claim**：此前只有 `run_started_at`，`_finalize` 依赖 `last_run_at` 导致耗时恒 NULL。claim 后各路径（计划轮次、立即运行、事件触发、助手 worker、observe）统一重读任务，终态耗时由 `last_run_at/run_started_at` 计算；`afinalize` 总是写 `last_duration_ms`（含显式 NULL），避免旧值残留。**口径**：列表耗时 = claim 到终态（含队列等待）；监控 `process_time` 从 worker 实际执行开始，两者可能不同。
2. **系统处置留痕统一落 `schedule.run`**：计划轮次失败、租约超时、达阈值/应用删除/一次性终态自动停用均以 `operator_id=0`（system）留痕，不新增 `schedule.task.disable` 行（保持不进审计页白名单）。轮次内的系统审计累积后**锁外批量**写，避免 15s 派发锁被逐条 commit 拉长。
3. **租约回收的租户姿态**：`_reclaim_expired` 在设置任何任务租户上下文之前运行，`areclaim_expired` 必须包 `bypass_tenant_filter()`；逐行处置前再 `set_current_tenant_id(row.tenant_id)`，否则多租户 beat 会在回收阶段 `NoTenantContextError`。
4. **触发计划三语**：列表/预览/NL 草稿新增 `schedule_display`（语义 token），前端按 `scheduledTask` 命名空间渲染；`schedule_description` 保留为中文兼容/兜底。一次性任务同样输出结构化 `once` 形态。
5. **F076 11806 补齐**：带密钥请求的中间件看的是 ROOT 租户，鉴权依赖必须用凭据的 `tenant_id` 自查停用租户（Redis 黑名单 + DB 兜底；缓存 miss 也回查 DB，root 仅跳过额外 DB 查询）。
6. **计划失败展示（复核 #11）**：以 `last_duration_ms` 区分真实运行；计划轮次 FAIL/SKIP 时隐藏「开始」与监控链接，Tooltip 提示「本轮为计划轮次失败，未实际运行」；`needs_input` 作为暂停中的真实运行单独放行。
7. **预设值变化可追溯（复核 #14）**：审计 metadata 增加顶层 `preset_values_changed` 布尔标记；`preset_inputs` 仍只存 key、不存 value（AC-51）。
8. **11806 缓存未命中（复核 #15）**：Redis 黑名单 miss 时回查租户行（fail-closed）；新增 cache-miss 契约测试。
9. **工作流版本标识**：新增 `FlowVersion.id → last_app_version_id` 落库单测；助手无版本体系，保持 NULL。

完整运行态结果、剩余环境/覆盖限制见报告 §9/§10。

---

## 8. 后续改进 / 不打算做的事

- **不接入 F056 对话输入/输出围栏**（明确表态）：定时运行没有对话参与方（输入来自任务配置、输出落监控，不投递给用户），平台的合规围栏针对**对话入口**；作为"新增执行路径的显式豁免"登记在 §6.2 与 release-contract 变更历史。**若行方合规要求覆盖无人值守路径**，另立变更（届时需定：拦截后的运行终态、通知与文案），不在本期。
- **不做独立运行历史表**（D10）：`last_*` + 监控 + `schedule.run` 留痕已足以回答"跑了没/成没成/在哪看"；事故复盘需要的"输入摘要"二期再做。
- **不做外部运行查询接口**（对客差异②）；若对接方要求"查询运行结果"，二期评估 `/api/v2` 只读运行查询（要同步定 scope 与防枚举口径）。
- **不做 per-run 阻塞式监督任务**（决策 6 的被否项）；若产品要求秒级 INPUT 答复再评估。
- **不做任务级时区 / 任务级重试 / 排队 / 配额**（spec §5 明确排除）；租户级配额若客户要求，按决策 4 的"何时重新考虑"扩配置。
- **`crontab(str)` 隐患、`doc_extract` 的 `getActionsByModuleApi` 缺口**：都是既有问题，按独立 hotfix 处理，不混入本特性（后者若顺手可一并修，需单独提交与说明）。
- **不做 `scheduled_task.enabled` 总闸**（决策 8/16）；客户要求"现场先关自动触发"时再加（默认 true，关时不注册 beat 条目）。

---

## 修订历史

| 日期 | 变更 |
|---|---|
| 2026-10-16 | 首版：3 面并行起草 + 2 路对抗审查（接手可用性 / spec 契约合规）后装配。**装配期裁定 16 处跨节冲突**，要点：① 运行监督定为"并入 tick 的无状态过"（否定 per-run 阻塞式监督任务）；② 编排顺序定为"复检 → 变体 → 单条原子 claim → 派发"（claim 是唯一写点，取消"先 claim 后回滚"路径）；③ 统一字段集与命名（补 `last_planned_at / last_finish_at / blocked_reason / service_account_id`；`run_lease_token` / `last_error` / `last_app_version_id` / `next_runs` 为定稿名）；④ 扫描唯一列 = `next_run_at`（`run_at` 只作展示与等待上线语义）；⑤ v2 触发端点定为模块内 `rpc_router` 双导出；⑥ 配置落 `core/config/scheduled_task.py` 且**无总闸**；⑦ 新增 11916；⑧ 助手运行器补遥测上报点（已登记 release-contract）；⑨ tick 默认由 discovery §3 的 30s 收紧为 **20s**（AC-10 的 30s 上限需留轮内余量——对 discovery 推荐值的收紧，不改 spec 承诺）；⑩ 修正 e2e 的 curl（timestamp/nonce 走请求头）与手工验证的默认队列 worker 命令。 |

