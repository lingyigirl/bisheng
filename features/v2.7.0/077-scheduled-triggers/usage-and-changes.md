# F077 定时/事件触发 — 改动清单与使用说明

> 面向：**交付/运维**（怎么开、怎么看、怎么排障）与**平台开发**（改了哪些文件、体现在哪）
> 对接方请看接口契约：[`../../docs/api/scheduled-task-trigger.md`](../../docs/api/scheduled-task-trigger.md)
> 对客验收：[`../../docs/定时任务与触发条款验收表.md`](../../docs/定时任务与触发条款验收表.md)
> 代码锚点：`feat/2.7.0/beijing-ns-077-wip`（Wave 1–6 已完成；T-28 现场执行待做）

---

## 一、这套东西解决什么问题

此前平台的应用**只能由人手动运行**（或由外部系统按需调 `/api/v2`）。客户条款（五）1 要求「定时/事件触发；手动或自然语言生成定时任务」，F077 补的就是这条**调度控制面**：

- 给应用配一个**触发计划**（cron 周期，或一次性绝对时间），到点由平台自动运行；
- 行内系统可在业务事件发生时，凭**服务账号密钥**调一个触发端点，让指定任务立刻跑一轮；
- 任务可以用**表单**配，也可以用**一句话自然语言**生成草稿、人工确认后保存；
- 每轮运行有**互斥、并发上限、超时回收、失败重试节奏、失败通知与审计**，不会静默停摆。

**明确不改变的事**：既有的发布渠道（API/免登录页/嵌入/分享链）一行未动；`/api/v2` 既有 44 个端点的行为不变。

## 二、改动清单（改了什么 → 体现在哪）

### 2.1 数据模型（1 张新表，随 `create_all` 建，无 Alembic revision）

| 表 | 用途 | 关键字段 |
|---|---|---|
| `scheduled_task` | 一条任务一行，**运行态也收在这一行**（同一任务至多一个运行） | 目标应用 `(app_id, app_type)` / 触发计划 `schedule_type + cron_expression` 或 `run_at` / 运行身份 `service_account_id + run_identity_user_id`（创建时锁定）/ `preset_inputs`（JsonType）/ `next_run_at`（扫描唯一依据）/ 租约 `run_lease_token + lease_expires_at` / 最近结果 `last_*` / `consecutive_fail_count` / `blocked_reason` |

回滚：`DROP TABLE scheduled_task` + 从 `core/database/tenant_filter.py` 摘除登记（模型文件头有说明）。真库已演练（MySQL：建表逐列核对 + 回滚 SQL 影子彩排）。

### 2.2 后端：新模块与执行链

| 位置 | 作用 |
|---|---|
| `bisheng/scheduled_task/`（新模块） | `api/` 管理面 9 条 + 事件触发 1 条；`domain/services/` 的纯函数与编排（下表） |
| `worker/scheduled_task/scan.py` | beat 薄壳：每轮回收过期租约 → 枚举到期 → 判定/写回/派发 → 监督过 |
| `worker/scheduled_task/assistant_runner.py` | 助手的无人值守运行器（进程内 `AssistantAgent`，`chat_id=""`，补 `APPLICATION_PROCESS` 遥测） |
| `worker/__init__.py` | 注册两个新任务（漏注册 = NotRegistered） |

服务层分工（接手时按这张表找代码）：

| 文件 | 做什么 |
|---|---|
| `cron_schedule.py` | cron 解析/归一化/人类可读/下一跳自算（唯一解析器 `crontab.from_string`） |
| `run_planner.py` | 纯决策：四种处置变体（NORMAL/SKIP/FAIL/DEFER）+ 同步入口的 REJECT |
| `trigger_guard.py` | 触发前四查 + **创建期**绑定校验（应用/身份/权限） |
| `knowledge_guard.py` | 创建期知识可见性（仅开关为开的引用；无授权 11913） |
| `app_shape_guard.py` | 可定时化形状（多输入节点 / 文件类输入 → 11914） |
| `task_service.py` | 管理面 CRUD/启停/删除/立即运行/预览/可用身份；每动作独立 `edit_app` |
| `nl_draft_service.py` | 自然语言 → 草稿（不落库；服务端硬校验） |
| `run_service.py` | 编排：平台锁、回收、扫描、claim、派发、监督过、终态、通知、审计、事件触发防重放/频控 |

### 2.3 前端（platform）：入口与页面

- **入口**：应用编辑器 →「对外发布」→ 左侧第 4 项「**定时任务**」（助手/工作流共用同一面板）。
- **可见性**：该入口按应用的 **`edit_app`（编辑者档）** 判定，**不继承**页签前 3 项的 `share_app` 门；原有 3 个页签口径不变。
- **列表**：触发计划 / 下次执行 / 上次结果（失败原因 Tooltip）/ 启停状态 / 操作（编辑、立即运行、启停、删除）。
- **创建/编辑弹窗**：周期或一次性、cron 预览（人类可读 + 未来 5 次）、运行身份下拉（不可用的账号会标注原因）、预设输入逐项编辑；**新建时有自然语言输入框**（草稿预填，确认保存才落库）。
- **文案**：`public/locales/{zh-Hans,en-US,ja}/scheduledTask.json`（各 61 键）+ `bs.json` 的 `api.scheduledTasks`。

### 2.4 审计（复用既有 `auditlog`）

| 动作 | 是否进平台审计页 | 说明 |
|---|---|---|
| `schedule.task.create` / `.update` / `.enable` / `.disable` / `.delete` / `.run_now` | **是** | 改了任务后续每轮行为都变，必须可追溯（对齐 F058 模板的 INV-15 精神） |
| `schedule.run` | **否**（只落库取证） | 频率由调度决定，进页面会刷屏（对齐 `open_api.call` 的处置） |

对应两处登记：后端 `_UI_VISIBLE_V2_ACTIONS` / `_V2_NAMESPACE_TO_ACTION_PREFIX`，前端 `controllers/API/log.ts` 镜像（有锁步测试）。

### 2.5 配置（`config.yaml`，可选段）

`scheduled_task:` 段，11 个键，全部有代码默认值、可不配：`global_max_running=10` / `max_tasks_per_app=20` / `run_timeout_seconds=3600` / `consecutive_failure_threshold=5` / `replay_window_seconds=300` / `event_rate_limit_per_minute=10` / `preset_input_max_items=20` / `preset_input_max_chars=2000` / `nl_max_chars=2000` / `preview_count=5` / `dispatch_lock_ttl_seconds=15`。
**扫描节拍不在这个段里**：beat 条目 `scheduled_task_scan` 在 `CeleryConf`（默认 **20 秒**，数值秒；可被 config.yaml 的 `beat_schedule` 覆盖）。**本特性没有总闸**——要停调度直接停该 beat 条目。

### 2.6 错误码（119 段）

14 个新码（11901/11906 由既有 404/403 承载）。事件触发面回**真 HTTP 状态**（含新增的 **409 / 429**），管理面沿用平台既有信封（HTTP 200 + 业务码）。完整表见接口契约 §3.1。

### 2.7 对既有代码的改动（为什么必须动那里）

| 文件 | 改动 | 原因 |
|---|---|---|
| `bisheng/api/router.py` | 挂两个新 router | 全局路由唯一注册处 |
| `bisheng/core/config/settings.py` | `Settings.scheduled_task` + beat 条目 | beat 全静态，不加默认条目 = 存量部署升级后拿不到调度 |
| `bisheng/core/database/tenant_filter.py` | 新表登记 | 漏登记 = 行落 ROOT 租户 |
| `bisheng/database/models/audit_log.py` + `platform/.../log.ts` | 白名单与镜像 | 双份手维护、有锁步测试 |
| `bisheng/open_api/domain/scopes.py` | 权限位 8 → **9**（`schedule:trigger`） | 事件触发的对外契约 |
| `bisheng/api/v1/flows.py` / `api/services/assistant.py` | 删除前拦截（有启用中的任务 → 11908） | **唯一**一处对既有行为的可感知变化 |
| `client/.../NotificationsDialog.tsx` + client 三语 | 两个 action_code 的文案 | 失败通知的消费面 |
| `scripts/arch-guard.sh` | `DOMAIN_MODULES` 补新模块 | C1 的机器执行臂 |

## 三、端到端使用流程（谁做什么）

1. **管理员/租户管理员**：系统 → 服务账号 → 建账号 → 授予目标应用**可编辑（editor）档** → 签发含 `schedule:trigger` 的密钥（核对是否要事件触发）。
2. **应用编辑者**：打开应用的「对外发布 → 定时任务」→ 新建：选计划（或写一句自然语言生成草稿）→ 选运行身份 →（可选）填预设输入 → 保存。
3. **三种触发**：
   - 到点自动跑（平台每 20 秒扫描一次）；
   - 行内系统调 `POST /api/v2/schedule/trigger`（带密钥、时间戳、随机数）；
   - 控制台点「立即运行」。
4. **看结果**：任务列表的「上次结果」（失败原因在悬停提示里）→ 点「监控」按 `run_trace_id` 看运行详情；失败会收到站内信（首败一条、连续 5 次自动停用一条）。
5. **排障**：先看任务列表的 `last_error`（`overlap` / `app_offline` / `app_deleted` / `no_use_app` / `owner_privileged` / `account_unavailable` …），再看服务端日志关键字 `schedule.scan.round` / `schedule.run.finalized` / `schedule.run.reclaim` / `schedule.trigger.replay_rejected`。

## 四、常见问题

| 现象 | 首要怀疑 | 处置 |
|---|---|---|
| 任务创建被拒 11905 | 运行身份对目标应用没有 `use_app` | 到「服务账号 → 资源授权」授到 **editor** 档 |
| 任务创建被拒 11913 | 应用开启了"用户知识库权限校验"，运行身份读不到所绑知识 | 给该身份授予知识范围，或改用开关关闭的引用 |
| 任务创建被拒 11914 | 应用含多个人工输入节点或文件类输入 | 一期不支持，改造成单输入/无文件后再定时化 |
| 定时不跑 | beat 是否在跑 / `scheduled_task_scan` 条目是否被删 | `celery call bisheng.worker.scheduled_task.scan.scan_scheduled_tasks` 手工跑一轮看日志 |
| 事件触发 404/11809 | 任务不存在 / 已停用 / **密钥所属账号对该应用无运行权限** | 三者刻意不可区分；按后两条排查 |
| 事件触发 409/11910 | 有新一轮在跑 | 等本轮结束；平台不做排队 |
| 事件触发 429/11911 | 触发频率超限（默认 10 次/分/任务） | 降低频率或调 `event_rate_limit_per_minute` |
| 任务一直「等待应用上线」 | 一次性任务到期时应用不在线 | 应用上线后会自动补跑一次；否则重新安排 |
| 审计页看不到逐轮记录 | 设计如此 | `schedule.run` 只落库，按 SQL 查 |

## 五、与 F076（开放 API 鉴权）的关系

- **共用身份与密钥**：本特性的运行身份就是 F076 的服务账号专用身份；事件触发的密钥、防枚举、错误码（118 段）全部复用 F076。
- **共用总闸**：事件触发要求 F076 的部署总闸 `open_api.enforce_keys` 打开（它是唯一的前置开关）；本特性自身没有开关。
- **边界**：F077 不清算 F076 的密钥、不改其任何行为；`tools[]` 等开放 API 语义不受影响。

## 六、版本与回滚

- 新增表随 `create_all` 自动建；**无 Alembic revision**；
- 回滚：`DROP TABLE scheduled_task` + 摘除租户登记；停用调度 = 停 beat 条目（管理面照常）；
- 对既有行为的唯一影响 = 删除应用时的引用拦截（11908），停用本特性（停 beat）后依然生效——它保护的是数据一致性，不是调度。
