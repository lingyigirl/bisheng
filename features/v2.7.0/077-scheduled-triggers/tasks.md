# Tasks: 定时/事件触发与定时任务生成（077-scheduled-triggers）

**关联规格**: [spec.md](./spec.md)（59 条 AC） · [design.md](./design.md)（16 决策 / 19 坑） · [discovery.md](./discovery.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-10-16 用户确认（★）；评审期裁定 6 条待澄清，余 1 条（运行身份移出租户后的历史可见性） |
| design.md | ✅ 已评审 | 2026-10-16 用户确认（★）；`/sdd-review design` 24 项通过、Constitution Check 无违反 |
| tasks.md | ✅ 已拆解 | 2026-10-16；`/sdd-review tasks` 首轮 2 项发现（AC-08/AC-51/AC-53 未覆盖 → 已补进 T-15/T-06/T-17/T-24；T-06/T-17 跨前后端 → 拆出 T-06a/T-17a）已修复，复检 AC 覆盖 **59/59**、依赖引用全部有效 |
| 实现 | 🔲 未开始 | 0 / 30 完成。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 开发模式

**按 Wave 组织**：W1 地基可并行；W2 调度内核是心脏（纯函数先行、编排后行）；W3/W4 可并行（身份权限面 / 事件触发面）；W5 前端与生成链路；W6 收口（守卫 + e2e + 文档 + 门禁）。每个任务标 `依赖:`。

**后端 Test-First（务实版）**：纯函数（cron/planner/guard）**测试先行**；编排与端点测试跟随实现同任务交付。测试落 `src/backend/test/scheduled_task/`（`asyncio_mode=auto`，不引 `task_always_eager`）。

**前端**：手动验证（每任务附验证步骤）；Playwright 交互测试仍属规划（SDD-Guide §6 🚧）。**前后端分区**：后端任务与 platform（builder 端）/ client（终端用户端）任务分开，跨端改动以成对任务呈现（如 T-06 → T-06a）。**例外与理由**：审计白名单这类"后端 + 前端镜像表双份手维护、有锁步测试"的改动必须**成对同 PR**（拆到一半会出现 CI 红或审计页看不到的中间态），故以 `T-06 / T-06a` 形式紧邻交付，而非跨 Wave。

**Test-First 的呈现形式（本特性口径）**：纯函数（cron / planner / trigger_guard）在任务内**先写测试后写实现**；编排与端点任务的测试与实现同任务交付（`验证` 列即该任务的测试清单）。这是与本仓 076 先例一致的务实版 TDD——任务粒度是"一个可验证的垂直切片"，不在 tasks.md 里再拆成"测试任务 / 实现任务"两行（避免同一文件被两个任务触碰）。

**Worker 任务的租户传递**：Celery headers → ContextVar 由 `worker/tenant_context.py` 自动注入/恢复；派发前显式 `set_current_tenant_id(task.tenant_id)`（stamp 在发布时生成），消费侧无需手写 `tenant_id`（但 bulk UPDATE / `text()` SQL 必须手写租户条件）。

**关键约束速查（每个任务都适用）**：new 表必须登记租户过滤 + `tenant_id default=None`；bulk UPDATE/SQL 手写 `tenant_id`；禁 `RETURNING`；JSON 列用 `dialect_helpers.JsonType`；派发一律显式 `queue=`；日志用 loguru `{}`（禁 `%s`、禁 `exc_info=`）；新错误码写 `Code: int = N` 且三语同 PR。

---

## Wave 1 — 地基（契约 / 骨架 / 模型 / 错误码 / 配置）

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-01 | **release-contract 登记复核**<br>**文件**：`features/v2.7.0/release-contract.md`<br>**逻辑**：五处登记（表 1 两行、INV-25 + INV-21 新消费者、表 3 依赖行、119 段、变更历史）已于 spec ★ 时落盘；design 期回填 11916 / 409 / 429 / 助手侧遥测上报点亦已落。本任务 = **复核并补漏**（含 F076 侧 INV-23 文本未被改动）<br>**依赖**：无 | —（契约） | ☑ 2026-10-16：`grep -n "F077" release-contract.md` 8 处；`11916` 3 处；表格校验无异常行；INV-23 文本未被改动 | ☑ |
| T-02 | **模块骨架与架构守卫**<br>**文件**：新增 `src/backend/bisheng/scheduled_task/{__init__,api/{router.py,endpoints/},domain/{models,services,schemas}}`（worker 侧 `worker/scheduled_task/` 随 T-11/T-12 建立）；改 `scripts/arch-guard.sh:21`、`api/router.py`<br>**逻辑**：按 design §4.3 建骨架；`management_router` 挂 `/api/v1`、`rpc_router` 挂 `router_rpc`（继承 F076 鉴权依赖）；`common/core` 不反向依赖<br>**依赖**：T-01 | —（约束） | ☑ 2026-10-16：`arch-guard` 两文件 exit 0；模块与全局路由导入 OK（空路由不增挂载）；踩坑记录——import 与 usage 必须同一次改动落地，否则 ruff 钩子删未用 import（AGENTS.md §Known Pitfalls 已载） | ☑ |
| T-03 | **表模型 + DAO + 租户登记 + 回滚说明**<br>**文件**：`scheduled_task/domain/models/scheduled_task.py`；改 `core/database/tenant_filter.py:39-131`<br>**逻辑**：design §4.2 的 25 列（`preset_inputs` 用 `JsonType`、`update_time` 用 `UPDATE_TIME_SERVER_DEFAULT`、`tenant_id default=None`）；三个索引；DAO 含 `claim/release/skip/fail/defer-标记` 条件 UPDATE（判 `rowcount`，**禁 RETURNING**）与按应用计数<br>**回滚**：走 `create_all`（**不建 Alembic revision**）；回滚 = `DROP TABLE scheduled_task` + 从 `_TENANT_AWARE_MODEL_MODULES` 摘除登记（回滚 SQL 注释写进模型文件头）<br>**依赖**：T-02 | AC-01, AC-14, AC-15 | ☑ 2026-10-16：`test/scheduled_task/test_models_tenant.py` **10/10 通过**（租户回填/默认值、claim 的三种 CAS 分支、槽位校验、失败计数与自动停用、阻塞幂等、令牌校验、needs_input 哨兵、租约回收、并发计数）；arch-guard 三个文件 exit 0。**既有红（与本特性无关）**：`test/tenant/test_tenant_id_default_guard.py` 在本分支上本就失败，唯一 offender 是 `bisheng/linsight/domain/models/linsight_skill.py:29`（F035，2026-06-17，`tenant_id default=1`）——新表 `scheduled_task` 不在 offender 列表内；已按 F076 先例记录、不顺手修（属他人特性范围） | ☑ |
| T-04 | **错误码 119 段 + 三语文案 + 分面 handler**<br>**文件**：新增 `common/errcode/scheduled_task.py`；`packages/locales/src/api_errors/{zh-Hans,en,ja}.json`；F077 专用异常处理器<br>**逻辑**：11901–11916（design §3 决策 15 表；11901/11906 由既有 404/403 承载不独立抛、不落文案）；`ScheduledTaskErrorBase` 携 `HttpStatus`；**带 `open_api_call` 标记走真状态码、否则委托既有信封**（v1 拦截器行为不变）<br>**依赖**：T-01 | 全部拒绝路径（文案侧） | ☑ 2026-10-16：`grep -c 'Code: int = 119'` = **14**（11902-05/07-16；11901/11906 按设计不定义）；三语 14×3 条已入 `packages/locales` 并**重生成 6 个产物**；`pnpm locales:check` 与 `pnpm check-i18n` 均 **OK（no new drift）**；handler 注册断言 True、429/409 状态映射实测正确 | ☑ |
| T-05 | **配置类与装配**<br>**文件**：新增 `core/config/scheduled_task.py`；改 `core/config/settings.py`（Settings 字段 + `CeleryConf.validate` 的 beat 条目）、`bisheng/config.yaml`（样例段）<br>**逻辑**：`ScheduledTaskConf` 键表见 design §3 决策 8（**无 enabled 总闸**）；beat 条目 `scheduled_task_scan`：`"schedule": 20.0`（数值秒）+ `"options": {"queue": "celery"}`<br>**依赖**：T-02 | AC-05, AC-12, AC-15, AC-37, AC-38, AC-41, AC-44, AC-55 | ☑ 2026-10-16：`test/scheduled_task/test_config.py` **4/4 通过**（默认值=设计键表、YAML 覆盖生效、beat 条目 `schedule` 为 **float 20.0** 且 `options.queue=celery`、`validate()` 后仍是 float）；`config.yaml` 补注释掉的样例段（含"节拍不在此配"的说明） | ☑ |
| T-06 | **审计动作与白名单（后端）**<br>**文件**：`scheduled_task/domain/constants.py`；改 `database/models/audit_log.py:178-221`<br>**逻辑**：`schedule.task.*` 六动作进 `_UI_VISIBLE_V2_ACTIONS`、`schedule.run` **不进**；`_V2_NAMESPACE_TO_ACTION_PREFIX` 加 `'schedule': 'schedule.'`；写入内容边界 = 只存标识与摘要（不含密钥材料、对话原文、输入正文，AC-51/INV-11）<br>**影响范围**：修改共享的 `audit_log` 白名单（release-contract 表 1 已登记为"扩展、不拥有"）；既有条目逐字不动<br>**依赖**：T-02 | AC-49, AC-50, AC-51 | ☑ 2026-10-16：`constants.py` 动作枚举（六管理动作 + `schedule.run`，附 `UI_VISIBLE_ACTIONS` 元组）；`audit_log.py` 白名单 +6、`_V2_NAMESPACE_TO_ACTION_PREFIX` 加 `'schedule'`；锁步测试断言 `schedule.run` **不在**白名单 | ☑ |
| T-06a | **审计白名单前端镜像 + 锁步测试**<br>**文件**：改 `platform/src/controllers/API/log.ts`（`getModulesApi` + 六行 actions + `getActionsByModuleApi` 补 `case 'schedule'`）；`platform/public/locales/*/bs.json`（模块名与动作名键，camelCase 避 i18next 的 `.` 嵌套）；新增 `test/scheduled_task/test_audit_lockstep.py`<br>**逻辑**：前端镜像与后端白名单逐条对齐；锁步测试照 `test/doc_extract/test_audit_lockstep.py`（断言两侧集合一致）<br>**必须与 T-06 同一 PR**（双份手维护 + 锁步测试，拆开会出现审计页看不到或 CI 红的中间态）<br>**依赖**：T-06 | AC-49 | ☑ 2026-10-16：`log.ts` 三处齐备（模块项 + 六动作 + `case 'schedule'`）；**顺手补齐 F058 起就缺的 `case 'doc_extract'`**（模块能选、动作下拉恒空，已在代码注释与本行说明）；三语 7 键 × 3 语言入 `bs.json`；`test/scheduled_task/test_audit_lockstep.py` **6/6 通过**（含前端镜像与三语键存在性断言）；`pnpm check-i18n` OK、改动文件 eslint exit 0 | ☑ |

## Wave 2 — 调度内核（纯函数 → 编排 → tick → 派发）

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-07 | **cron 纯函数 + 单测**<br>**文件**：`scheduled_task/domain/services/cron_schedule.py`；`test/scheduled_task/test_cron_schedule.py`<br>**逻辑**：`validate_and_parse`（唯一 `crontab.from_string`；拒绝清单 → 11903）、`normalize`、`describe`、`next_runs(now, n)`（**显式 tz-aware 锚点自算，禁用 `remaining_estimate`**；DOM/DOW 同受限时 OR 语义；闰日上限兜底）<br>**依赖**：T-04 | AC-06, AC-07, AC-09 | ☑ 2026-10-16：**76/76 通过**；独立复核与 celery 对拍 **170/170**（日/周不同时受限的形态）、逐分钟 OR oracle 950 组仅 47 处差异且**全部**是首轮缺陷。复核抓到 2 处已修：① 稀疏表达式（`0 0 29 2 *` 求 5 次）凑不齐 n 时曾误抛 11903 → 改为返回已找到的（AC-07 措辞是「至多 5 次」）；② `describe("0 0 * 2 1")` 曾丢月份限制、文案与真实计划矛盾 → 已修 + 两条回归测试。**已知有意偏离**：DOM/DOW 同时受限取 OR（crontab(5) 标准），celery 引擎是 AND——模块 docstring 已写明，对拍排除该形态 | ☑ |
| T-08 | **调度判定纯函数 + 单测**<br>**文件**：`scheduled_task/domain/services/run_planner.py`；`test/scheduled_task/test_run_planner.py`<br>**逻辑**：`decide_due`、`merge_catchup`（锚定 now ⇒ 跨 N 周期只补一次）、`overlap_disposition`（周期 SKIP / once DEFER）、`cap_room`、`lease_state`（`lease_expires_at == now` 两侧）<br>**依赖**：T-07 | AC-11, AC-12, AC-13, AC-15 | ☑ 2026-10-16：**41/41 通过**；复核枚举 **216 组合矩阵**（cron|once × 租约三态 × 应用三态 × 身份两态 × 名额 0\|1 × 三种触发）与设计推导**逐项 0 差异**，DEFER/REJECT 恒 `has_plan_writeback=False`。复核抓到的 3 处已按裁定修：① **DTO 统一**——planner 不再自建 `RecheckOutcome`/`AppState`，改 import `trigger_guard` 的（原两套同名类型靠 StrEnum 值相等侥幸互通）；② **失败记账单一写入者**——manual/event 遇身份不可用/应用已删除改为 `REJECT`；③ **event 并入 keep 计划**。设计决策 4/9 已回写这两条裁定 | ☑ |
| T-09 | **触发前复检纯函数 + 单测**<br>**文件**：`scheduled_task/domain/services/trigger_guard.py`；`test/scheduled_task/test_trigger_guard.py`<br>**逻辑**：四查（应用存在且 ONLINE / SA 行可用 / owner 可装载且未提权 / owner 持 `use_app`）+ **SA 行 `resource_owner_user_id` 与任务锁定身份一致**；输出 = 处置变体建议<br>**依赖**：T-03 | AC-16, AC-21, AC-22, AC-23 | ☑ 2026-10-16：`trigger_guard.py`（应用三态 OK/OFFLINE/DELETED × 身份六态，`RecheckOutcome` 回带 payload 供派发直接使用）+ `describe_failure` 短原因；**反向断言**：身份不可用时 `payload is None`（绝不回退为其他身份）；测试 9/9 | ☑ |
| T-10 | **编排 `run_cycle` / `observe_runs` + 单测**<br>**文件**：`scheduled_task/domain/services/run_service.py`；`test/scheduled_task/test_run_cycle.py`<br>**逻辑**：轮首平台级 Redis 锁 → 租约回收 → 跨租户枚举（`bypass_tenant_filter` + `(status,next_run_at)`）→ 逐条复检 → **四种变体的单条原子 claim**（唯一写点）→ 派发前 `set_current_tenant_id`；`observe_runs`：终态 → `finalize`；INPUT → 预设输入自动提交 / CAS 写 `needs_input` + 通知一次；租约过期 → 判失败<br>**依赖**：T-08, T-09 | AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-46 | ☑ 2026-10-19：`run_service.py`（平台锁 → 回收 → 跨租户枚举 → 逐条复检/判定/原子写回/派发 → 监督过 → 终态/通知/审计；派发/读态/通知可注入）+ **12 条单测**（真实 DAO + 内存 SQLite：NORMAL 双写、SKIP 不计失败、FAIL 首败一条、once 不消耗 + blocked_reason、并发名额、锁跳过、派发异常立即判失败、监督过的 SUCCESS/FAILED/INPUT 哨兵一次性）。实现期两处修正：① **naive/aware 边界**——库列是 naive 本地时间，只有 cron 求值边界补 `ZoneInfo`，否则 DB 比较混类型（实测 `ValueError: anchor must be timezone-aware`）；② 单条异常不拖垮整轮 | ☑ |
| T-11 | **tick 薄壳与 beat 接线**<br>**文件**：`worker/scheduled_task/scan.py`；`worker/__init__.py`；`core/config/settings.py`（T-05 已加条目）<br>**逻辑**：`scan_scheduled_tasks`（`acks_late=True` + 显式 `time_limit/soft_time_limit`）→ `run_cycle(now)`；任务必须被 import 注册<br>**依赖**：T-05, T-10 | AC-10 | ☑ 2026-10-19：`worker/scheduled_task/scan.py` 薄壳（`acks_late` + 显式时限 + 崩溃只记日志不抛，避免 beat 条目持续失败）+ `worker/__init__.py` 注册；实测注册名 `bisheng.worker.scheduled_task.scan.scan_scheduled_tasks`（beat 条目同名任务路径可解析） | ☑ |
| T-12 | **派发适配（工作流 + 助手 + 遥测补点）**<br>**文件**：`run_service.dispatch` 的工作流分支；`worker/scheduled_task/assistant_runner.py`<br>**逻辑**：工作流 = F054 序列（`RedisCallback(chat_id="")` → `set_workflow_data(deepcopy)` → `WAITING` → `execute_workflow.apply_async(queue=worker_node)`；`hash_key = unique_id`）；助手 = 进程内 `AssistantAgent(invoke_user_id=…)`（**不持密钥、不走 HTTP**，显式 `queue="celery"`）+ `finally` 写 `APPLICATION_PROCESS`（复用既有 schema）+ 回写终态<br>**依赖**：T-10 | AC-45, AC-48, AC-52 | ☑ 2026-10-19：工作流派发在 `run_service._dispatch_workflow`（F054 序列：`RedisCallback` 预写 data → WAITING → `execute_workflow.apply_async(queue=绑定节点)`；派发时取当前 `FlowVersion` 落 `last_app_version_id`）；助手在 `worker/scheduled_task/assistant_runner.py`（进程内 `AssistantAgent(chat_id="", invoke_user_id=运行身份)`、**执行级二次复检**、`finally` 补 `APPLICATION_PROCESS` 遥测并走同一 `_finalize`）。单测覆盖：派发参数/queue（T-10 的编排测试）；助手路径的遥测与 `chat_id=""` 由 e2e（T-25）覆盖 | ☑ |

## Wave 3 — 身份 / 权限 / 知识可见性 / 删除级联 / 失败闭环（可与 W4 并行）

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-13 | **运行身份装载与复检落地**<br>**文件**：`run_service`（复检调用）；`task_service`（创建期校验组合）<br>**逻辑**：`load_owner_payload` + `owner_is_privileged`；创建期四条件（`edit_app` / 应用合法 / 身份可用 / `use_app`）与触发期四查共用 T-09 函数；**≤10s 撤权窗口**复用 `PermissionService` 缓存失效（不自建缓存）<br>**依赖**：T-09 | AC-18, AC-19, AC-20, AC-22, AC-23, AC-27 | ☑ 2026-10-19：触发期四查在 T-09 已落；本次补**创建期** `validate_binding`（应用存在 → 服务账号可用 → owner 可装载未提权 → `use_app`，通过后返回要锁定的 owner）；单测覆盖 4 类身份拒绝 + 快路径（owner 被锁定）；不出现任何按参数指定身份的路径 | ☑ |
| T-14 | **知识可见性创建期校验**<br>**文件**：`scheduled_task/domain/services/knowledge_guard.py`<br>**逻辑**：`collect_app_knowledge_refs`（工作流遍历 `Flow.data` 的知识节点 + `user_auth`；助手 `knowledge_auth` + 绑定）**抽用运行期同一解析逻辑**；对 ON 档引用逐个判读权限（文档库走 `judge_knowledge_permission` 同一函数；空间走 `space_flow_retrieval`）→ 无授权 11913 + 缺授权清单；OFF 档不查<br>**依赖**：T-13 | AC-28, AC-29 | ☑ 2026-10-19：`knowledge_guard.py`——`collect_workflow_refs`（纯函数，解析 `nodes[*].data.group_params[*].params[*]` 的 `knowledge`/`advanced_retrieval_switch`，含旧形态顶层 `user_auth`；与运行期读同一份参数）+ 助手侧 `AssistantLink` + `knowledge_auth`；ON 档逐个判读（文档库 `judge_knowledge_permission`、空间 `_user_can_read_space`）→ 11913 回带缺授权清单；**OFF 档零查询**（单测断言）。测试 12 条 | ☑ |
| T-15 | **管理面服务 + 端点**<br>**文件**：`scheduled_task/domain/services/task_service.py`、`api/endpoints/{tasks,nl}.py`、`api/router.py`<br>**逻辑**：CRUD / 启停 / 删除 / 立即运行 / cron 预览 / 列表组装（人类可读计划 + 下次执行 + 上次结果）；**每入口独立 `edit_app` 服务端强制**（403 信封）；`GET /available-identities`（`edit_app` 鉴权、零凭据信息、区分 usable）；**enable 时重算 `next_run_at = next_runs(now,1)`**（AC-02：停用期到期轮不触发）；一次性任务的创建（绝对时间 ≥ now、创建时写入 `next_run_at`）与**终态自动 disabled**（AC-08，落点与 T-10 的 finalize 对齐）；**租户隔离**：全部读取走租户自动过滤，跨租户一律 404（AC-53）<br>**依赖**：T-07, T-13 | AC-01, AC-02, AC-03, AC-04, AC-05, AC-08, AC-17, AC-24, AC-25, AC-26, AC-53 | ☑ 2026-10-19：`task_service.py` + `schemas/task.py` + `api/endpoints/tasks.py`（9 条路由实测挂载）；创建/编辑的校验顺序与写库列有 13 条单测（权限缺省 403、管理员放行、应用/身份两类错误可区分、每应用上限、cron/一次性/预设输入校验、换身份同步锁 owner、enable 重算 `next_run_at`、删除、列表需 app 或管理员、cron 预览）。**离端点级 403 契约测试**：按仓内 076 先例直接调端点函数（依赖注入桩管理员）留到 e2e（T-25）覆盖 | ☑ |
| T-16 | **删除拦截（AC-30）**<br>**文件**：改 `api/v1/flows.py`（`delete_flow`）、`api/services/assistant.py`（`delete_assistant`）<br>**逻辑**：删前查"该应用启用中的定时任务数" >0 → 11908 + 数量 + **引导下一步**（由持编辑权限者先停用/删除任务）；0 命中零副作用<br>**依赖**：T-03 | AC-30 | ☑ 2026-10-19：`task_service.assert_app_deletable`（同步，供两个同步删除端点）+ `api/v1/flows.py` / `api/services/assistant.py` 接入（放在权限校验之后）；DAO 增 `count_enabled_by_app_sync`（同步会话、租户过滤生效）。测试：启用中 → 11908 + 数量、停用/零任务不阻断、同步计数只数启用中的 | ☑ |
| T-17 | **失败闭环与通知（后端）**<br>**文件**：`run_service.finalize`；`message` 模块调用（`send_generic_notify`）<br>**逻辑**：`consecutive_fail_count`（仅 failed 计入、success 清零）；首败一条 / 达阈值自动停用一条；收件人 = 创建者，停用/删除时兜底租户管理员/超管，全空则 `logger.error`；外发默认不发生；留痕与通知正文只含标识与摘要（AC-51）<br>**依赖**：T-10 | AC-31, AC-32, AC-46, AC-50, AC-51, AC-54, AC-55, AC-56 | ☑ 2026-10-19：随 T-10 的 `run_service` 一并落地（`_apply_failure` / `_reclaim_expired` / `_finalize` 三条路径的首败·阈值·删除通知，`_notify` 走 `send_message(action_code=…)`，收件人兜底超管、全空 `logger.error`；正文只含任务名与原因摘要）。单测：首败恰好一条、阈值自动停用、`needs_input` 只通知一次、成功清零。**未单测**：兜底收件人链路的真实查询（依赖用户/超管数据，留 e2e） | ☑ |
| T-17a | **失败通知的 client 消费面**<br>**文件**：`client/src/components/NotificationsDialog.tsx`（`NOTIFICATION_ACTION_TEXT_KEYS` 加两个 action_code）；`client/src/locales/{zh-Hans,en,ja}/translation.json`<br>**逻辑**：`scheduled_task_failed`（含"已自动停用"变体）与 `scheduled_task_needs_input` 的文案与跳转；未知 action_code 的既有兜底不回归<br>**依赖**：T-17 | AC-54, AC-59 | ☑ 2026-10-19：`NotificationsDialog.tsx` 的 `NOTIFICATION_ACTION_TEXT_KEYS` 加两个 action_code；client 三语各加 2 键；`pnpm check-i18n` OK、改动文件 eslint exit 0。手动可见性留 e2e/现场（T-25） | ☑ |

## Wave 4 — 事件触发面（可与 W3 并行）

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-18 | **触发端点与 scope 登记**<br>**文件**：`scheduled_task/api/endpoints/trigger.py`；改 `open_api/domain/scopes.py`、`api/router.py`<br>**逻辑**：`POST /api/v2/schedule/trigger`；`@open_api_scope("schedule:trigger")` + 注册表登记；**恒强制密钥**（`get_open_api_principal() is not None`）；body 白名单 `{task_id, inputs?}`（差集校验 → 400/11916；裸 `user_id` 复用依赖层 → 400/11817）；404 防枚举（不存在/不在授权范围/已停用不可区分）；受理返回 `{task_id, run_trace_id}`；**不提供运行查询**<br>**依赖**：T-10 | AC-33, AC-34, AC-35, AC-36 | ☑ 2026-10-19：`api/endpoints/trigger.py` + scope 注册表新增 `schedule:trigger`（**9 项权限位**）；恒强制密钥（principal 为空即 401/11801，与总闸无关）；白名单差集 → 11916（裸 `user_id` 由 F076 依赖更早拦为 11817）；防枚举三合一 404（缺失/停用/不在授权范围）；受理回 `{task_id, run_trace_id}`。**F076 守卫已同步**：路由矩阵 44→45（43 HTTP + 2 WS）、权限位 8→9，三条计数断言更新后全绿 | ☑ |
| T-19 | **防重放 / 频控 / 并发拒绝**<br>**文件**：`trigger.py`（头解析）+ `run_service.accept_trigger`<br>**逻辑**：`X-Bs-Timestamp` ± 窗口（默认 300s）+ `X-Bs-Nonce` **单次往返** `SET NX EX`（TTL=2×窗口）；缺头/超窗/重放 → 400/11912；任务级频控（`INCR` + 首写 `EXPIRE`，默认 10 次/分）→ 429/11911（带 `Retry-After`）；已有运行中 → 409/11910；全局满 → 409/11909；**拒绝均不产生运行、不计连续失败**<br>**依赖**：T-18 | AC-37, AC-38 | ☑ 2026-10-19：`run_service.assert_not_replayed`（`SET NX EX` 单次往返，TTL=2×窗口）与 `consume_trigger_quota`（固定窗口 `INCR`+首写 `EXPIRE`，默认 10 次/分）；`run_now` 泛化 `trigger_kind`，事件触发的互斥拒绝映射 **11910**（立即运行才是 11909）；Redis 不可用 → 503/11812。测试 8 条（缺头/超窗/重放/超频/Redis 故障/白名单/三种 404/成功路径与 EVENT 语义） | ☑ |

## Wave 5 — 生成链路与前端

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-20 | **NL 草稿链路**<br>**文件**：`scheduled_task/domain/services/nl_draft_service.py`、`api/endpoints/nl.py`；改 `common/models/config.py`（槽位 `SCHEDULED_TASK_LLM`）<br>**逻辑**：prompt 约定 JSON → `json_repair` 容错 → **服务端硬校验**（cron/应用与权限/尺寸/必要项）→ 返回草稿**不落库**；非法 → 11915；未配槽位回退租户默认对话模型；输入 ≤ 2000 字符<br>**依赖**：T-07, T-15 | AC-41, AC-42, AC-43 | 单测：非法输出四类各一 → 11915；**调用前后表行数不变**；为空/超长拒绝；确认后落库走 create 同一路径 | ☐ |
| T-21 | **预设输入 + INPUT 监督过落地**<br>**文件**：`run_service.observe_runs` 的 INPUT 分支；`task_service` 的预设输入校验<br>**逻辑**：预设按**静态 label/key** 存（≤20 项 × 2000 字符）；INPUT 事件到达时按 `input_schema` 映射 → `set_user_input` → `INPUT_OVER` → `continue_workflow(queue=同绑定节点)`（**顺序不可反**）；未配预设 → CAS 写 `needs_input` + 通知一次；含多输入节点/文件类 → 创建期拒绝 11914<br>**依赖**：T-10, T-15 | AC-44, AC-45, AC-46, AC-47 | 单测：三步顺序断言、静态映射、CAS 去重；创建期拒绝两类应用各一例 | ☐ |
| T-22 | **platform 面板**<br>**文件**：`apiComponent/index.tsx`（menuItems + 逐项门控 + activeMenu 初值）、新增 `apiComponent/scheduledTask/*`、`controllers/API/scheduledTask.ts` + `types/api/scheduledTask.ts`<br>**逻辑**：列表（计划/下次执行/上次结果 + 失败原因 Tooltip + 启停）、创建/编辑弹窗（应用/计划/运行身份/预设输入）、cron 预览（服务端算、自绘）、NL 输入 + 确认页（回显人类可读计划与时间）、立即运行、删除；**入口按 `edit_app` 可见（不继承 `share_app`）**；**AC-48 提示文案**（不产生会话，结果看任务列表与监控）；不写 403 分支<br>**依赖**：T-15, T-20 | AC-39, AC-40, AC-48, AC-57, AC-58 | 手动：编辑者（无 `share_app`）可见并可管理；查看者不可见；cron 非法有可读提示；NL 草稿不落库、确认后才出现；`pnpm lint/typecheck` 绿 | ☐ |
| T-23 | **三语文案（前端全量）**<br>**文件**：`platform/public/locales/{en-US,zh-Hans,ja}/scheduledTask.json`；`platform/src/i18n.js:45`；审计筛选键入 `bs.json`；client 通知键<br>**逻辑**：面板全部文案三语齐备；新 ns 登记；错误码文案由 T-04 的 `packages/locales` 承担<br>**依赖**：T-22 | AC-59 | `pnpm check-i18n` + `@bisheng/locales build --check` OK；三语键一一对齐 | ☐ |

## Wave 6 — 收口（守卫 / e2e / 交付文档 / 门禁）

| Task | 内容 | 覆盖 AC | 验证 | 完成 |
|---|---|---|---|---|
| T-24 | **四道守卫测试**<br>**文件**：`test/scheduled_task/{test_audit_lockstep,test_models_tenant,test_scope_route_matrix}.py`（部分在 T-06/T-18 已建，本任务收口）<br>**逻辑**：① 审计白名单 ↔ 前端镜像锁步 + `schedule.run` 不进白名单；② scope 注册表 ↔ 路由双向一致；③ 新表租户回填 + `tenant_id default=None`；④ `/api/v2` 新端点漏 marker → 500/11811<br>**依赖**：T-06, T-18, T-03 | AC-49, AC-50, AC-36 | 四道全绿；故意摘 marker / 摘白名单条目时对应测试变红（自检有效性） | ☐ |
| T-25 | **e2e 套件**<br>**文件**：`src/backend/test/e2e/test_e2e_scheduled_triggers.py`（数据前缀 `e2e-sched-` + 双 cleanup）<br>**逻辑**：design §7 的 8 项（造数 + 手工触发 scan 替代真实等待）：运行归因一跳 / 站内信 / 多租户隔离 / 权限矩阵 / 事件触发全链（含重放与频控）/ 漏跑与重叠与一次性顺延 / 连续失败自动停用 / 应用下线三档处置<br>**依赖**：T-10, T-15, T-18, T-19, T-21 | AC-10…AC-58 中可 e2e 验证者（清单见 design §7） | `/e2e-test features/v2.7.0/077-scheduled-triggers` 全绿；无留残余数据 | ☐ |
| T-26 | **对客文档与（五）1 验收表**<br>**文件**：`docs/api/scheduled-task-trigger.md`（事件触发契约：头/体/响应/状态码表含 **409/429**/防重放语义/前置条件 `enforce_keys`）；`docs/开放API条款验收表.md` 同体例新增（五）1 章节<br>**逻辑**：登记**两条对客差异**（① 下线即不执行；② 事件触发不提供运行查询）+ 事件触发前置条件 + `scheduled_task:` 配置段说明；验收表按 AC 分层（现场可验收 / 实验室验证）<br>**依赖**：T-18 | AC-33, AC-34, AC-37 | 文档通读无与 design 冲突的表述；验收表含现场填写栏 | ☐ |
| T-27 | **门禁收口与真库演练**<br>**文件**：CI/本地门禁；预发或本机 MySQL；DM8 清单<br>**逻辑**：`arch-guard` / 平台 `pnpm lint` + `typecheck` / `check-i18n` / `pytest test/scheduled_task/`；MySQL 上建表 + 全流程 + **回滚 SQL 演练**（T-03）；DM8 按 C2 清单预检（`RETURNING` 禁用、JSON 列 CLOB 行为、`update_time` 触发器）<br>**依赖**：T-24, T-25 | —（质量门） | 全部门禁绿或既有红可解释；真库演练与回滚演练留档 | ☐ |
| T-28 | **现场切换与验收（用户执行）**<br>**文件**：交付文档；现场配置<br>**逻辑**：按 design §7 手工验证 8 步逐条过；打开 `open_api.enforce_keys`（事件触发前置）；按（五）1 验收表逐项打勾；观察首批运行的 `last_status` 与站内信<br>**依赖**：T-26, T-27 | AC-10, AC-16, AC-33, AC-55, AC-57 | 验收表全绿；无回滚需求（本特性无总闸，回退 = 停 beat 条目） | ☐ |

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- （尚无——实现期开始后逐条追加）

