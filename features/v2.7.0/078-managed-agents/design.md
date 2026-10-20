# F078 智能体纳管 — 设计（How）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动
>
> 调整原则：实现变化 → 覆盖更新本文档，只留“今天的状态”；每个决策保留“为什么 + 被否方案”；坑单列 §5。
> 偏差分级：推翻已 ★ 确认的决策 → 停下与用户重新确认；纯实现细节 → 直接改设计。

**关联**: [spec.md](./spec.md) · [discovery.md](./discovery.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-10-19

---

## 1. 目标与非目标

- **目标**：新增一个异构智能体控制面，把本平台应用（投影）与第三方智能体（低代码/高码）纳入同一注册表、权限、调用、调度、运营与分类体系；对外提供统一的 `/api/v2` 调用入口，对内复用 F076 身份与 F077 调度。
- **非目标**：
  - 不做 A2A 完整任务生命周期/Agent Card 自动发现/流式（Phase 2）。
  - 不做具名低代码连接器、应用市场/交易/计费、评测集成、故障转移。
  - 不把第三方智能体写入 `flow` / `assistant` 表，不扩展 `FlowType`。
  - 不新建工作流引擎；原生应用仍走既有运行链。
  - 不做代表用户调用、OAuth2 授权服务器。
  - 不在本期修复 F077 的 `last_run_at` 历史缺陷（见 §8）。

---

## 2. 关键约束

遵循 `docs/constitution.md` C1–C7（原文不重抄）；本节只列本特性特有的落地后果与硬约束。

1. **FGA 模型是“写一次”的部署物**：`MODEL_VERSION` 只是静态 DSL 的版本标记，运行期没有任何版本比对；已有 FGA store 必须通过一次性脚本写入新模型，否则 `managed_agent` 类型不存在，权限检查会把“无模型”表现成“无权限”。（F061 先例：`scripts/migrate_fga_add_datasource_type.py`。）
2. **双库 C2**：三张新表全部使用 `dialect_helpers.JsonType/LargeText/UPDATE_TIME_SERVER_DEFAULT`；状态/来源/协议/分类/健康等可查询字段必须是关系列；禁止 `RETURNING`、JSON 函数、手写 `ON UPDATE`。
3. **多租户 C3**：新模型必须登记 `_TENANT_AWARE_MODEL_MODULES`；子租户默认可见集是 `{child, ROOT}`，管理面读必须 `strict_tenant_filter()`，写必须显式 `tenant_id` + rowcount CAS。
4. **权限 C4**：所有权限判定走 `PermissionService`；`managed_agent` 不是 `Flow` 行，不能复用 `ApplicationPermissionService` 的 FlowType 列表路径，必须补齐 FGA 的 owner/existence/tenant/list 分支。
5. **安全 C6/INV-26**：外部调用必须做 SSRF/allowlist/超时/响应体上限；凭据加密、掩码、不落日志；不跟随重定向；不信任环境代理。
6. **开关**：`managed_agent.enabled` 默认 false；关闭时路由 404、菜单/页签隐藏，既有应用/F076/F077 行为不变。
7. **不引入重型依赖**：httpx 0.28.1 已在依赖；不新增 UI/状态库；不新增 Celery 队列（复用 `celery` 默认队列）。
8. **部署可回滚**：新表可 `DROP`；FGA 模型是追加式，回滚可继续 pin 旧 model_id；不改既有表结构。

---

## 3. 方案对比与选定（关键决策）

### 决策 1：领域模块、建表方式与功能开关

- **备选**：
  - A. 新顶层模块 `bisheng/managed_agent/`；三张 `table=True` 模型；启动 `create_all(checkfirst=True)` 建表；`managed_agent.enabled=false` 总开关。
  - B. 模型放进 `database/models/`；为建表写 Alembic 修订。
  - C. 用 `initdb_config` / 租户级策略做开关。
- **选定**：A。
- **原因**：F076/F077 的先例；在线 Alembic 在修订前先 `create_missing_model_tables`（`alembic_helpers/online.py:43-77`、`entrypoint.sh:44-56`），整表无需修订；总开关保证既有部署零影响。`initdb_config` 有 100s Redis 缓存，不适合作为安全开关。
- **何时重新考虑**：若同版本内需要给 `managed_agent` 加列/索引，必须补 Alembic 修订或重建开发表；`SKIP_MIGRATIONS=true` 的环境不会执行 Alembic，需确认启动路径仍调用 `create_db_and_tables`。

### 决策 2：双库列类型与状态存储

- **备选**：
  - A. `Integer` 自增 PK；`tenant_id Integer default=None`；所有状态用 `String(16)` + `StrEnum.value` + `server_default text("'...'")`；`JsonType` 只存不查询的 `agent_card`/`usage`；`LargeText` 存凭据密文；`update_time` 用 `UPDATE_TIME_SERVER_DEFAULT`。
  - B. `SQLEnum`/数据库枚举；`JsonType` 存分类/状态；手写 `LONGTEXT` / `ON UPDATE`。
- **选定**：A。
- **原因**：F035 事故（MySQL 枚举冻结、后续值 `Data truncated`）；DM8 把 `JsonType` 映射为 CLOB，不能 WHERE/ORDER BY；`UPDATE_TIME_SERVER_DEFAULT` 在 DM8 由启动触发器 `trg_<table>_update_time` 维护（`dialect_helpers.py:160-193`、`connection.py:288-295`）。
- **何时重新考虑**：状态集合需要数据库级约束时，改为应用层校验 + 迁移脚本，不要直接上 `SQLEnum`。

### 决策 3：Agent Card 与预设输入

- **备选**：
  - A. `agent_card` JSON 保存 `{version, capabilities[], inputs:[{key,label,type,required,file,default}]}`；专用校验器复用于注册/编辑/定时任务预设；文件输入拒绝 11914；未知/缺失/类型不符拒绝 11902。
  - B. 复用 F077 的“只校验 key/长度”；C. 单独建输入 schema 关系表。
- **选定**：A。
- **原因**：F077 `_validate_presets` 只校验 key/长度（`task_service.py:108-121`），无法满足 AC-32；C2 不允许对 JSON 做过滤；JSON 整读整写对 Agent Card 足够。
- **何时重新考虑**：需要按 capability/input 过滤或索引时，抽关系表并迁移。

### 决策 4：分类法与标签

- **备选**：
  - A. 单表 `managed_agent_taxonomy`（kind=scenario/industry，code 稳定，name_zh/name_en/name_ja，sort_order，is_enabled）；managed_agent 各存一个 `scenario_code`/`industry_code`；标签复用 `Tag`/`TagLink`。
  - B. 只用自由标签；C. 硬编码枚举；D. 多对多关联表。
- **选定**：A。
- **原因**：AC-48/49/50/55 要求按 code 过滤、排序、启停、三语展示；C2 要求可查询字段关系化；spec 用“指定场景/行业”单数。标签复用既有体系，新增 `TagBusinessTypeEnum.MANAGED_AGENT` 与 `ResourceTypeEnum.MANAGED_AGENT=10`。
- **何时重新考虑**：产品要求多场景/多行业时，加 `managed_agent_taxonomy_link` 关联表，再改筛选/API。

### 决策 5：删除语义、唯一性与接入地址规范化

- **备选**：
  - A. 硬删除 `managed_agent` 行；删除前检查启用中的定时任务（`ScheduledTaskDao.acount_enabled_by_app(str(id),100)` → 12110）；`managed_agent_run` 保留并冗余 `agent_name/source_kind/protocol` 快照；名称/接入地址可复用。
  - B. 软删除 + DB 唯一（名称永久占用）；C. 软删除 + 应用层活行唯一。
- **选定**：A。
- **原因**：AC-08/AC-39 + spec 边界“删除后调用留痕保留、不级联”；硬删除避免唯一约束与软删行冲突。唯一性用 `(tenant_id, name)` 和 `(tenant_id, endpoint_hash)`。`endpoint_hash = sha256(canonical_endpoint)`，**非空**：外部记录用规范化 URL 的 hash；原生投影用 `sha256("native:{native_app_type}:{native_app_id}")`，避免依赖 DM8 的“多 NULL 唯一”行为。`endpoint` 原文保留 `String(512)`。
- **原因（补充）**：MySQL 8.0 的 `(tenant_id, endpoint)` 其实能放进 3072 字节索引，但 DM8 的索引键上限随页大小/`MAX_SEC_INDEX_SIZE` 变化，hash 是跨库最稳的形态。
- **何时重新考虑**：需要保留已删除记录的管理视图时改软删，并重新设计唯一约束；需要同一接入地址多版本时，用同一记录的 `version` 字段而非多行。

### 决策 6：适配器协议

- **备选**：
  - A. 一个 `ManagedAgentAdapter` 协议：`invoke(config,input,session_id,caller) -> AgentResult(run_id,output,usage?)`、`health_check(config) -> HealthResult(ok,latency_ms)`；native / openai_compatible / custom_http 三个实现；单次请求、不自动重试。
  - B. 复用 openai SDK；C. 复用 `bisheng_langchain/gpts/tools/api_tools`；D. A2A/流式进 MVP。
- **选定**：A。
- **原因**：AC-13/14/18；httpx 0.28.1 已锁定；现有工具执行器会 log/raise `resp.text`、每次新建 aiohttp、无 SSRF/响应体上限（`gpts/tools/api_tools/openapi.py:111-135`、`base.py:53-84`），不能复用。`run_id` 是平台 UUID，上游响应 id 只存 metadata。
- **直调边界**：原生 assistant 走既有非流式路径；原生 workflow 复用 F054 的编程式运行（预写 data → `execute_workflow` → 轮询状态），超过一次人工 INPUT 或超时返回 12106；流式/多次交互放 Phase 2。
- **何时重新考虑**：需要流式/A2A 或需要 SDK 的高级特性时，在适配器层扩展，不改统一契约。

### 决策 7：出站 HTTP、SSRF 与 allowlist

- **备选**：
  - A. 每次请求 `async with httpx.AsyncClient(...)`；显式 connect/read/write/pool 超时；`follow_redirects=False`；`verify=True`；`trust_env=False`；显式 `Limits`；新增 `common/security/outbound_url.py`：仅 http/https、拒绝 userinfo/空 host、解析全部 A/AAAA、要求 `ip.is_global` 且拒绝私网/链路本地/组播/未指定/保留/CGNAT/`ipv4_mapped`；allowlist 按 host 或 host:port；3xx 一律拒绝；流式读取并计字节上限。
  - B. 直接复用 web-link 的 `_is_blocked_ip`；C. 跟随重定向；D. 使用环境代理。
- **选定**：A。
- **原因**：现有 web-link guard 只查 loopback/link-local/multicast/unspecified，放过 `10/8`、`172.16/12`、`192.168/16`、`fc00::/7`、CGNAT；httpx 默认 `trust_env=True`、5s 超时、无响应体上限。新 helper 与 web-link 解耦，避免改变既有内网导入行为。
- **残余风险**：预检与连接之间仍有 DNS rebinding TOCTOU；MVP 接受并在运维文档写明，银行环境建议叠加出站防火墙/allowlist。IP pinning 需要自定义 transport/SNI，放 Phase 2。
- **何时重新考虑**：安全评审要求 IP pinning 或租户级 allowlist 时，扩展 helper/transport。

### 决策 8：凭据存储与掩码

- **备选**：
  - A. 单个 Fernet 加密 JSON blob 存 `LargeText`（auth_type、secret、固定 headers）；`credential_type`/`has_credential` 用于掩码；编辑时空 secret = 不变；只在调用时解密；日志/审计/错误/统计不含凭据。
  - B. 明文或 JsonType；C. 单向哈希（无法回放）；D. 外部 KMS/OAuth2。
- **选定**：A。
- **原因**：F061 先例（`datasource.py:66-68`、`credentials.py:39-118`）；AC-19/AC-58。**已知风险**：`core/config/settings.py:23` 是硬编码共享 Fernet key，无轮换/租户隔离（C6/RULE-7 的既有缺口）；F078 不修，但在部署文档标注“换 key 会让所有已存凭据不可解密”，并给出重新录入流程。
- **何时重新考虑**：安全评审要求 KMS/轮换时，加 `MultiFernet` + 重加密作业。

### 决策 9：统一 v2 调用契约与错误映射

- **备选**：
  - A. `POST /api/v2/managed-agent/invoke` + `@open_api_scope('managed_agent:invoke')`；body 严格白名单 `{agent_id,input,session_id?}`；响应 `{run_id,output,usage?}`；仅 online；服务账号 owner 对 `managed_agent` 持 `use_app`（editor）；缺失权限位 11807；不可见/跨租户 404/11809；离线/停用/草稿 12105；上游失败 12106；非法/超限响应 12113；出站策略 12111；频控 12112/429；不返回上游正文/栈。
  - B. 复用 assistant/workflow 端点；C. 透传上游状态/正文；D. 自动重试 POST。
- **选定**：A。
- **原因**：AC-21..AC-30；F077 trigger 端点是 scope+identity 的现成样板（`trigger.py:49-72`）。**防枚举统一用 11809**（F076/F077 同口径），12101 只用于 v1 管理面“不存在/无权限”；spec AC-22/28 已按此订正。状态错误与可见性错误分开：先判可见/权限（404/11809），再判状态（12105）。
- **何时重新考虑**：需要异步任务/流式时，新增 run 查询/SSE，不改变同步契约。

### 决策 10：频控

- **备选**：
  - A. Redis 固定窗口，key `managed_agent:rate:{tenant}:{agent}:{epoch_minute}`；Lua 或 `SET NX EX` + `INCR` 原子化；超限 12112 + 429 + `Retry-After`；Redis 不可用 fail-closed 503。
  - B. 复用 F077 的 `INCR` 后首次 `EXPIRE`；C. token bucket；D. fail-open。
- **选定**：A。
- **原因**：AC-26/AC-30；F077 的 `INCR`→`EXPIRE` 非原子（`run_service.py:819-841`），崩溃会留下无 TTL 键；F077 的 429 未真正发出 `Retry-After`。健康检查试调用不计入业务频控，使用独立上限。
- **何时重新考虑**：需要分布式令牌桶/配额计费时，换 Redis 模块或网关限流。

### 决策 11：健康检查与运营

- **备选**：
  - A. `managed_agent` 存 `health_status/last_health_check_at/last_health_ok_at/consecutive_health_failures`；beat `managed_agent_health_scan` 跨租户枚举；`health_method=health_url|test_invoke`；阈值标记 unhealthy；自动下线可配、默认 false；手动检查审计，周期检查不审计；失败通知复用 F077 通道；统计聚合 `managed_agent_run`。
  - B. 只做手动检查；C. 每次周期检查都审计；D. 自动下线默认 true。
- **选定**：A。
- **原因**：AC-41/42/46；F077 beat 跨租户范式（`run_service.py:152-170`、`scan.py:21-39`）。周期审计会淹没审计页；test-invoke 可能创建会话/消耗第三方配额，需要独立频率上限与样例输入。健康试调用写 `managed_agent_run` 时 `caller_type=health_check`，统计默认排除该类型，避免健康检查抬高调用量。
- **何时重新考虑**：行内要求自动下线/恢复策略时，在配置与状态机中明确阈值。

### 决策 12：F077 `app_type=100` 派发注册表（fail-closed）

- **备选**：
  - A. 常量 `MANAGED_AGENT_APP_TYPE=100` 放中性模块；`_OBJECT_TYPE_BY_APP_TYPE` 加 `100→managed_agent`；`_app_state`/`_app_exists` 加分支；`_dispatch` 改为显式分派：WORKFLOW→`_dispatch_workflow`、ASSISTANT→`execute_assistant_run`、100→`_dispatch_managed_agent`、其他→抛错；不内联 HTTP。
  - B. 让 100 落入 assistant 兜底；C. 在 beat 进程内直接调用外部 HTTP。
- **选定**：A。
- **原因**：`_dispatch` 当前对非 WORKFLOW 一律走 assistant（`run_service.py:373-381`）；`_app_state` 只对 5/10 返回 OK，未知类型返回 DELETED（`trigger_guard.py:103-104`），所以 100 今天在创建期就被 11902 拦住。两处必须同 patch：先加 `_app_exists`/`_app_state`，再加显式 dispatch，否则会误跑 assistant 或误判失败。
- **补充**：`_app_exists` 必须校验 AC-32 的“创建时已上线”；原生应用允许离线建任务，托管智能体不允许。`_dispatch` 不能在 beat 进程内跑 HTTP：平台派发锁 TTL 15s，beat tick 20s，慢上游会阻塞所有租户。
- **何时重新考虑**：需要新增应用类型或独立 worker 队列时，扩展分派表与队列，不改兜底逻辑。

### 决策 13：原生投影运行路径与不可用状态

- **备选**：
  - A. `scheduled_task` 存 `app_type=100 + app_id=managed_agent.id`；runner 解析 `native_app_id/native_app_type`；原生 workflow 仍走 `_dispatch_workflow`（用 native id），`observe_runs` 对 100 且 native_app_type=WORKFLOW 继续观察（保留 INPUT）；原生 assistant 走 assistant runner（native id）；外部智能体走新 managed runner 自报终态；离线/停用/删除映射 `AppState.OFFLINE`（周期跳过、一次性等待、事件/立即运行拒绝）。
  - B. 把原生 app_type/id 直接存进 scheduled_task；C. 删除映射 DELETED（失败+自动停用）。
- **选定**：A。
- **原因**：AC-10/37/38；`_dispatch_workflow`/`_handle_input` 把 `task.app_id` 当 flow id（`run_service.py:383-406,472-523`），不解析会把 managed id 当 flow id；`observe_runs` 只观察 WORKFLOW（:426），原生 workflow 投影若自报会丢 INPUT 处理。AC-38 明确删除也按“跳过/等待/拒绝”，与 F077 的 DELETED→失败+自动停用不同，故映射 OFFLINE。
- **代价**：删除的托管智能体若仍有启用任务（正常会被 AC-08/39 拦住），其 cron 会永久跳过、一次性永久等待；这是 spec 选择，需在验收表写明。
- **何时重新考虑**：行内要求“删除即失败+通知”时，把删除映射回 DELETED，并改 spec AC-38。

### 决策 14：审计动作与调用留痕

- **备选**：
  - A. 管理动作 `managed_agent.create/update/delete/enable/disable/health_check` 进审计页白名单；`managed_agent.invoke` 只写 DB（对齐 `open_api.call`/`schedule.run`）；`managed_agent_run` 是权威调用留痕；周期健康检查不审计。
  - B. 所有动作都进白名单；C. 只依赖有界队列的 call audit。
- **选定**：A。
- **原因**：AC-27/43/46/56/57；`audit_log.py:191-249`；`call_audit_service.py` 的队列满会丢。扁平动作名让 `managed_agent`→`managed_agent.` 命名空间一对一，避免二级命名空间。
- **何时重新考虑**：合规要求每个 invoke 都有不可丢审计时，把 `managed_agent_run` 作为主表并加归档，不要依赖队列。

### 决策 15：FGA 模型、权限接入与 store 升级

- **备选**：
  - A. `_standard_resource_type('managed_agent')`；合并后的 `MODEL_VERSION=v2.0.5`（19 类型：本特性新增 `managed_agent`，`linsight_skill` 随 llm-guardrail 合并带入）；一次性脚本 `scripts/migrate_fga_add_managed_agent_type.py`（dry-run/apply，打印新旧 model_id）；补齐 `PermissionService` 的 creator/tenant/list 分支；`VALID_RESOURCE_TYPES`、`FineGrainedPermissionService.default_permission_ids_for_relation`、`resource_authorization_service._default_permission_ids`、`GRANTABLE_RESOURCE_TYPES` 加 `managed_agent`；owner tuple 写入 `enforce_fga_success=True`。
  - B. 只 bump `MODEL_VERSION`；C. 只加类型，不加 PermissionService 分支；D. 只加 `_TENANT_GATED_RESOURCE_TYPES` 不加 list 分支。
- **选定**：A。
- **原因**：验证结论——`MODEL_VERSION` 无运行期消费者；`FGAManager` 在 `model_id` 未 pin 时采用 store 里最新模型，pin 了就用 pin 的旧模型；必须一次性写入新模型。`PermissionService` 的 `_get_resource_creator`、`_resolve_resource_tenant`、`_resource_ids_by_creator_user_ids`、`_resource_ids_in_tenants`、`_resource_tenant_map` 都按字面 object_type 分派，未知类型返回 None/[]/{}；只加 frozenset 会导致“列表可见但 check 拒绝”或租户过滤失效。`FineGrainedPermissionService` 不给 `managed_agent` 映射时，权限 ID 解析为空，连 owner/admin 都会被拒。
- **部署顺序**：代码部署（开关 off）→ 运行 FGA 升级脚本 → pin 新 model_id 或让 manager 取最新 → 重启 API/Celery → 首次创建（owner tuple enforce）。
- **何时重新考虑**：平台要求权限 ID 独立（不复用 `view_app/use_app`）时，新增 managed-agent 权限模板并同步两份 dispatch。

### 决策 16：前端落点、契约与三语

- **备选**：
  - A. 平台：`build/managed-agents` 路由（`permission: 'build'`），`HeaderMenu` 加白名单/NavLink；`pages/BuildPage/managedAgents/`；API-layer 模式（`useState/useEffect/captureAndAlertRequestErrorHoc/AutoPagination`），不新增 react-query v3 用法；客户端：在 `/apps/explore` 内加 Tabs，抽出 `NativePlazaTab`，新增 `ManagedAgentTab/Card/Filters`；Phase 1 点击只开预览抽屉；不复用收藏/统计。
  - B. 新 `/apps/managed-agents` 路由；C. 平台用 `useQuery`；D. 直接复用 `AgentCard`/收藏/统计。
- **选定**：A。
- **原因**：AC-67/68/69/70；`HeaderMenu.tsx:24-49` 只认精确 build 路径；`explore.tsx` 346 行，内联会接近 600 行上限；客户端收藏/统计后端只接受 5/10（`plaza_service.py:14`）。平台 `request.ts` 解包 `data.data`，客户端 `request.ts` 返回完整信封，客户端目录要读 `res.data.data` 并传 `skip403Redirect`。
- **何时重新考虑**：需要独立导航/收藏/统计时，先把后端 `_SUPPORTED_TYPES` 与收藏表扩展为通用资源，再改路由。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**注册/管理**
平台「构建 → 智能体纳管」→ `POST/PUT /api/v1/managed-agents` → `registry_service` 校验来源/协议/接入地址/Agent Card/分类/凭据 → `OwnerService.write_owner_tuple(..., enforce_fga_success=True)` → 返回草稿；发布/下线/停用走 CAS。

**统一调用**
`POST /api/v2/managed-agent/invoke` → F076 密钥鉴权 + `managed_agent:invoke` scope → `managed_agent` 行按租户/可见性解析 → 频控 → `adapter_registry` 选择适配器 → `invoke` → 写 `managed_agent_run` + `managed_agent.invoke` 审计 → 返回 `{run_id,output,usage?}`。

**定时运行**
F077 beat 扫描 → 复检（`trigger_guard` 解析 100 → 注册表/native 可用性）→ claim/租约 → `_dispatch` 显式分派：
- 原生 workflow 投影 → `_dispatch_workflow(native_app_id)`，`observe_runs` 对 100/native workflow 继续观察并处理 INPUT；
- 原生 assistant 投影 → assistant runner（native id）；
- 第三方 → `execute_managed_agent_run`（Celery，`celery` 队列）→ 适配器 → 自报终态/失败计数/通知。

### 4.2 数据模型

**`managed_agent`**
`id` Integer PK；`tenant_id` Integer NOT NULL index default=None；`name` String(128) NOT NULL；`description` LargeText NULL；`source_kind` String(16)（native/low_code/high_code）；`protocol` String(32)（native/openai_compatible/custom_http）；`status` String(16) server_default `'draft'`（draft/online/offline/disabled）；`version` String(64) NULL；`native_app_id` String(64) NULL；`native_app_type` Integer NULL（5/10）；`endpoint` String(512) NULL；`endpoint_hash` String(64) NOT NULL；`model_name` String(128) NULL；`agent_card` JsonType NULL；`credential_ciphertext` LargeText NULL；`credential_type` String(16) server_default `'none'`；`has_credential` Boolean server_default 0；`health_url` String(512) NULL；`health_method` String(16) server_default `'test_invoke'`（health_url/test_invoke）；`health_status` String(16) server_default `'unknown'`；`consecutive_health_failures` Integer server_default 0；`last_health_check_at` DateTime NULL；`last_health_ok_at` DateTime NULL；`last_call_at` DateTime NULL；`last_run_status` String(16) NULL；`last_latency_ms` Integer NULL；`scenario_code` String(64) NULL；`industry_code` String(64) NULL；`user_id` Integer NOT NULL index（创建者/owner）；`create_time`/`update_time`。

约束：`uk_managed_agent_tenant_name(tenant_id,name)`；`uk_managed_agent_tenant_endpoint_hash(tenant_id,endpoint_hash)`；`idx_managed_agent_tenant_status(tenant_id,status)`；`idx_managed_agent_tenant_source(tenant_id,source_kind)`；`idx_managed_agent_tenant_class(tenant_id,scenario_code,industry_code)`；`idx_managed_agent_health(status,health_status,last_health_check_at)`。`mysql_charset=utf8mb4`。

**`managed_agent_taxonomy`**
`id` Integer PK；`tenant_id` Integer NOT NULL index default=None；`kind` String(16)（scenario/industry）；`code` String(64)；`name_zh` String(128) NOT NULL；`name_en`/`name_ja` String(128) NULL；`sort_order` Integer server_default 0；`is_enabled` Boolean server_default 1；`create_time`/`update_time`。唯一 `(tenant_id,kind,code)`；索引 `(tenant_id,kind,is_enabled,sort_order)`。

**`managed_agent_run`**
`id` BigInteger PK；`tenant_id` Integer NOT NULL index；`agent_id` Integer NOT NULL（无 FK）；`agent_name_snapshot` String(128)；`source_kind_snapshot` String(16)；`protocol_snapshot` String(32)；`run_id` String(64) NOT NULL；`trace_id` String(64) NULL；`caller_type` String(16)（api_key/schedule/manual/health_check）；`caller_id` String(64) NULL；`service_account_id` Integer NULL；`scheduled_task_id` Integer NULL；`trigger_kind` String(16)（cron/once/event/manual）；`status` String(16)（success/failed）；`error_code` Integer NULL；`error_summary` String(512) NULL（脱敏、无上游正文）；`latency_ms` Integer NULL；`upstream_status` Integer NULL；`usage` JsonType NULL；`create_time`。唯一 `(tenant_id,run_id)`；索引 `(tenant_id,agent_id,create_time)`、`(tenant_id,create_time)`、`(tenant_id,status,create_time)`。无 FK/级联，删除 agent 后留痕保留。

### 4.3 关键模块职责

| 模块/文件 | 职责 | 不做什么 |
|---|---|---|
| `managed_agent/api/endpoints/{agents,taxonomy,invoke}.py` | v1 管理面、分类法、v2 调用入口 | 不直接写 ORM/不内联业务规则 |
| `managed_agent/domain/services/registry_service.py` | 注册/编辑/状态机/删除检查 | 不调用外部 HTTP |
| `managed_agent/domain/services/agent_card.py` | Agent Card 校验/预设输入校验 | 不做协议调用 |
| `managed_agent/domain/services/adapter_registry.py` | 按 protocol 选适配器 | 不写业务表 |
| `managed_agent/domain/services/adapters/{native,openai_compatible,custom_http}.py` | 协议归一、调用、健康检查 | 不记录凭据/上游正文 |
| `managed_agent/domain/services/invoke_service.py` | 权限/频控/留痕/错误映射 | 不做具体协议 |
| `managed_agent/domain/services/health_service.py` | 健康检查编排/状态更新 | 不审计周期检查 |
| `managed_agent/domain/models/{managed_agent,taxonomy,run}.py` | 表模型 | 不写业务逻辑 |
| `worker/scheduled_task/managed_agent_runner.py` | 定时运行的外部/原生助手执行 | 不在 beat 进程内跑 HTTP |
| `common/security/outbound_url.py` | URL 校验/DNS/IP/allowlist | 不改变 web-link 既有策略 |
| `common/errcode/managed_agent.py` | 121 段错误码 | 不重复 118/119 语义 |
| 平台 `pages/BuildPage/managedAgents/` | 纳管控制台 | 不承载客户端消费 |
| 客户端 `pages/apps/explore.tsx` + `ManagedAgent*` | 目录/筛选/预览 | 不改原生广场行为 |

### 4.4 API 契约

**v1 管理面**（`resp_200` 200 信封）
- `GET /api/v1/managed-agents`（keyword/source_kind/protocol/status/scenario_code/industry_code/tag_id/page/page_size）→ `PageData`。
- `POST /api/v1/managed-agents`；`GET/PUT/DELETE /api/v1/managed-agents/{id}`。
- `POST /api/v1/managed-agents/{id}/publish|offline|disable`。
- `POST /api/v1/managed-agents/{id}/health-check`。
- `GET /api/v1/managed-agents/{id}/stats?start_time=&end_time=`。
- `GET /api/v1/managed-agents/catalog`（仅 online + view_app；客户端）。
- `GET/POST/PUT/DELETE /api/v1/managed-agent-taxonomies`。

**v2 调用面**（真实 HTTP 状态）
- `POST /api/v2/managed-agent/invoke`：body `{agent_id:int, input:object|string, session_id?:string}`；额外字段 12114；成功 200 `{run_id, output, usage?}`。
- 状态映射：12102/12104/12105/12109/12114→400；12103/12110→409；11807/12111→403；11809/12101→404；12112→429；12106/12107/12108/12113→502/503；缺 scope 11807。
- 频控 12112 + `Retry-After`；Redis 不可用 503。

**Scope**：`managed_agent:invoke`（group `app`，`(POST, /api/v2/managed-agent/invoke)`）；注册表 9→10；路由 45→46（HTTP 43→44 + 2 WS）。同步更新 `test_scopes.py`、`test_service_account_keys_api.py`、`test_scope_i18n_lockstep.py`、三语 `serviceAccount.json`、`accessGuide.md`、`docs/api/open-api-auth.md`、`docs/开放API条款验收表.md` K 行。

### 4.5 F077 接入清单

| 位置 | 改动 |
|---|---|
| `common/constants/managed_agent.py` | `MANAGED_AGENT_APP_TYPE=100`（中性模块，避免循环导入） |
| `trigger_guard.py:39-42` | `_OBJECT_TYPE_BY_APP_TYPE[100]="managed_agent"` |
| `trigger_guard.py:91-104` | `_app_state` 加 100：注册表存在/online + 原生投影可用；不可用→OFFLINE |
| `trigger_guard.py:174-181` | `_app_exists` 加 100：创建期要求 online（AC-32） |
| `trigger_guard.py:206-219` | `object_type_of`/`validate_binding` 支持 100 |
| `task_service.py` | `create/update` 支持 100；预设输入走 Agent Card；`list_available_identities` 支持 100；`assert_app_deletable(id,100)` |
| `app_shape_guard.py:63-74` | 100 分支：第三方用 Agent Card，原生 workflow 用既有形状校验；文件输入 11914 |
| `knowledge_guard.py:96-106` | 100 分支：原生投影解析 native id/type 并复用既有收集；第三方返回 [] |
| `run_service.py:373-381` | 显式分派（WORKFLOW/ASSISTANT/100/其他抛错）；100 外部/原生助手入 Celery，原生 workflow 走 `_dispatch_workflow(native_id)` |
| `run_service.py:383-406,472-523` | `_dispatch_workflow`/`_handle_input` 接受 resolved native id；版本写 native FlowVersion id |
| `run_service.py:412-458` | `observe_runs` 对 100 且 native_app_type=WORKFLOW 继续观察（含 INPUT）；外部/原生助手自报 |
| `run_service.py` finalize | 透传 `trigger_kind`，manual/event 不误加失败计数；managed runner 回写 `managed_agent_run` |
| `worker/__init__.py:19-20` | 注册 `managed_agent_runner` |
| `scheduled_task` model/schemas | 注释/描述加 100；`last_app_version_id` 对原生投影存 native 版本 |
| `nl_draft_service.py` | `draft_from_text` 接受 100，可用目标含纳管智能体，预设走 Agent Card（AC-40） |
| `test/scheduled_task/test_trigger_guard.py` | 保留 99→DELETED；新增 100 分支用例 |

### 4.6 FGA/权限接入清单

- `authorization_model.py`：`_standard_resource_type('managed_agent')`；合并后 `MODEL_VERSION='v2.0.5'`；类型数 18→19（本特性新增 `managed_agent`，`linsight_skill` 随 llm-guardrail 合并带入）。
- `permission_service.py`：
  - `_get_resource_creator` 加 managed_agent（bypass 读 `user_id`）；
  - `_resolve_resource_tenant` 加 managed_agent（bypass 读 `tenant_id`）；
  - `_resource_ids_by_creator_user_ids`、`_resource_ids_in_tenants`、`_resource_tenant_map` 加分支；
  - `_TENANT_GATED_RESOURCE_TYPES` 加 managed_agent（必须同时有上述 list 分支）；`_IMPLICIT_SCOPE_RESOURCE_TYPES` 暂不加（当前 inert，避免假安全）。
- `permission_schema.py`：`VALID_RESOURCE_TYPES` 加 managed_agent。
- `fine_grained_permission_service.py`：`default_permission_ids_for_relation` 加 managed_agent → 应用模板（view_app/use_app/edit_app/…）。
- `resource_authorization_service.py`：`_default_permission_ids` 加 managed_agent。
- `resource_grant_service.py`：`GRANTABLE_RESOURCE_TYPES` 加 managed_agent；`grants.py` 描述更新；前端 `ResourceGrantsTab` 加 loader/labels。
- 创建：`OwnerService.write_owner_tuple(..., enforce_fga_success=True)`；查看/编辑/删除/调用：`PermissionService.check`。
- 部署：一次性脚本 `scripts/migrate_fga_add_managed_agent_type.py`（dry-run 默认、`--apply`、打印新/回滚 model_id）；pin model_id 或让 manager 取最新。

### 4.7 前端落点

- 平台：`routes/index.tsx` 加 `build/managed-agents`（`permission: 'build'`）；`HeaderMenu.tsx:24-49` 加白名单 + NavLink；`pages/BuildPage/managedAgents/`（列表/注册编辑/详情/分类法/健康/测试）；`controllers/API/managedAgent.ts`、`types/api/managedAgent.ts`；三语 `managedAgent.json` + `bs.json` 菜单键；不新增 react-query v3 用法。
- 客户端：`pages/apps/explore.tsx` 抽 `NativePlazaTab`，加 `ManagedAgentTab`、`ManagedAgentCard`、`ManagedAgentFilters`；`~/api/managedAgents.ts`、types、`translation.json` 键；react-query v4 + useState；预览抽屉；不复用收藏/统计；`skip403Redirect`。
- 权限位文案：`scope.managed_agent_invoke` 三语；`api_errors` 12101–12114 三语（`packages/locales` 生成链，不手改生成物）。

### 4.8 其他注册点

| 位置 | 改动 |
|---|---|
| `bisheng/api/router.py` | 挂载 `managed_agent` 路由 |
| `core/database/tenant_filter.py` | 三个模型模块加入 `_TENANT_AWARE_MODEL_MODULES` |
| `database/models/audit_log.py` | 加 `managed_agent.create/update/delete/enable/disable/health_check` 到白名单/命名空间；`managed_agent.invoke` 只落库 |
| `frontend/platform/src/controllers/API/log.ts` + `bs.json` 三语 | 审计动作前端镜像 |
| `database/models/tag.py` / `group_resource.py` | `TagBusinessTypeEnum.MANAGED_AGENT`、`ResourceTypeEnum.MANAGED_AGENT=10` |
| `common/errcode/managed_agent.py` + `main.py` | 12101–12114 错误类与分面 handler；v2 真实 HTTP、v1 200 信封 |
| `core/config/managed_agent.py` + `core/config/settings.py` | `ManagedAgentConf`（enabled/timeouts/max_response_bytes/allowlist/proxy/rate_limit/health_interval/max_agents_per_tenant）；beat `managed_agent_health_scan`（300s，queue `celery`） |
| `worker/__init__.py` | 导入 `worker/scheduled_task/managed_agent_runner.py` |
| `scripts/migrate_fga_add_managed_agent_type.py` | FGA 一次性升级脚本（dry-run/apply） |
| `open_api/domain/scopes.py` | `managed_agent:invoke` scope + 路由计数 9→10 / 45→46 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `_dispatch` 对非 WORKFLOW 一律走 assistant | 100 通过复检后被 AssistantDao 按 managed id 误跑/误判失败并自动停用 | `run_service.py:373-381`；显式分派 |
| 2 | `observe_runs` 只观察 WORKFLOW | 原生 workflow 投影成功运行却被租约超时记为失败，INPUT 永远无人处理 | `run_service.py:412-458`；100/native workflow 分支 |
| 3 | `_dispatch_workflow`/`_handle_input` 把 `task.app_id` 当 flow id | managed id 被当 flow id，读错流程/“flow disappeared” | `run_service.py:383-406,472-523`；解析 native id |
| 4 | `knowledge_guard` 对未知类型返回 [] | 原生投影绕过 INV-25 知识可见性 | `knowledge_guard.py:96-106`；100 原生分支 |
| 5 | `app_shape_guard` 对非 WORKFLOW 直接 return | 文件类输入被接受，11914 永不触发 | `app_shape_guard.py:63-74`；100 分支 |
| 6 | 新模型未登记 `_TENANT_AWARE_MODEL_MODULES` | SELECT 跨租户、INSERT 落 ROOT（无默认值时 IntegrityError） | `tenant_filter.py:141-190`；登记模块 |
| 7 | 子租户默认可见集是 `{child, ROOT}` | 管理面读到 ROOT 行；dirty-flush UPDATE 不重新过滤 | `http_middleware.py:207-227`；`strict_tenant_filter()` + 显式 tenant |
| 8 | `PermissionService.check` 的 admin 短路在租户门之前 | 超管权限检查永远为真，权限层无法单独保证 AC-60 | `permission_service.py:102-104`；API 层先做租户作用域 |
| 9 | 批量 update/delete 不收租户过滤，DM8 不支持 RETURNING | 跨租户改数据 / DM8 `-5016` | 手写 `tenant_id` + rowcount CAS |
| 10 | 硬删/软删与唯一约束冲突 | 软删行永久占用名称，或同地址多行无法约束 | 决策 5：硬删 + 快照 + hash 唯一 |
| 11 | DM8 多 NULL 唯一语义未验证 | 如果采用 `endpoint_hash=NULL` 方案，原生行可能在 DM8 冲突 | 决策 5：hash 非空（native 哨兵串），不依赖 NULL 语义 |
| 12 | `SQLEnum` 会冻结取值 | 后续新增状态 `Data truncated`（F035 事故） | 决策 2：String + StrEnum + server_default |
| 13 | `JsonType` 在 DM8 是 CLOB | 按分类/状态过滤变成全表扫或报错 | 决策 2/4：可查询字段关系化 |
| 14 | `create_all` 不会改已存在的表 | 同版本加列不生效，开发/测试环境表停留在旧结构 | 决策 1；后续列变更补 Alembic |
| 15 | `MODEL_VERSION` 无运行期消费者 | 只改代码不写 store，`managed_agent` 类型不存在，表现为“无权限” | 决策 15；一次性脚本 |
| 16 | owner tuple 默认 best-effort | 创建成功但无 owner tuple，只有 creator fallback（还需分支） | `enforce_fga_success=True` |
| 17 | 子租户管理员代管授权依赖 `_resolve_resource_tenant` | 没有分支时租户管理员短路不可达，授权 11808 | 决策 15；加分支 |
| 18 | 只加 `_TENANT_GATED_RESOURCE_TYPES` 不加 list 分支 | `_resource_tenant_map` 返回 {}、过滤返回全量，列表隔离失效 | 决策 15；五处分支 |
| 19 | 审计锁步是静默的 | 动作不在白名单/前端镜像/三语中时页面看不到 | `audit_log.py:191-249`；锁步测试 |
| 20 | Scope 锁步是静默的 | 权限位无三语文案时显示 `scope.managed_agent_invoke` | `test_scope_i18n_lockstep.py`；同步 3 语 |
| 21 | 现有工具执行器会把上游正文写日志/异常 | 凭据/内部拓扑泄漏到日志与 UI | 新适配器只记状态+脱敏摘要 |
| 22 | httpx 默认 `trust_env=True`、5s、无响应体上限 | 环境代理绕过 SSRF 预检；慢上游超时；大响应打爆内存 | 决策 7；显式客户端 |
| 23 | 现有 web-link SSRF guard 不完整 | 10/8、172.16/12、192.168/16、fc00::/7、CGNAT 可出站 | 新 `outbound_url.py`；不改 web-link |
| 24 | 模块级 `AsyncClient` 跨事件循环 | “Event loop is closed / attached to a different loop” | 每请求客户端 |
| 25 | F077 频控 `INCR`→`EXPIRE` 非原子 | 崩溃留下无 TTL 键；429 未发 `Retry-After` | 决策 10；`SET NX EX`/Lua |
| 26 | `last_run_at` 从未写入，`_finalize` 用 NULL 算耗时 | F077 的 `last_duration_ms` 永远为空 | 本特性用 `managed_agent_run.latency_ms`；F077 缺陷另记 |
| 27 | manual/event 派发也走 `_finalize(count_failure=True)` | 手动运行失败会错误累计并可能自动停用 | 透传 `trigger_kind`，manual/event 不计失败 |
| 28 | 健康扫描忘 `finally` 恢复租户 | 下一个 agent 用错租户的权限/知识范围 | `bypass_tenant_filter` + `set/restore` in finally |
| 29 | Fernet key 是硬编码共享常量 | 换 key 全部凭据不可解密；仓库可读即可解密 | 复用但不修；部署文档标注 |
| 30 | 客户端 request 返回完整信封 | 直接复制平台代码会读错 `data.data/total` | 客户端 `res.data.data` |
| 31 | 客户端 403 会整页跳转 | 目录 GET 权限失败把用户带到 `/c/new?error=11403` | `skip403Redirect` |
| 32 | 收藏/统计后端只接受 5/10 | 复用会报错/空数据 | Phase 1 不复用 |
| 33 | `HeaderMenu` 只认精确 build 路径 | 新路由未加白名单会让整个 build 顶栏消失 | 加 `build/managed-agents` |
| 34 | 平台 react-query v3 / 客户端 Recoil 被 ESLint 冻结 | 新页面引入即 lint fail | 用 API-layer / react-query v4 |
| 35 | `explore.tsx` 346 行 | 内联 Tabs 接近 600 行上限 | 抽 `NativePlazaTab` |
| 36 | i18n 是 ratchet | 缺一语言/缺 api_errors/手改生成物 CI fail | 三语同 PR；生成链 |
| 37 | FGA 测试锁死版本/类型数 | 加类型后旧断言红 | 更新 openfga/datasource/F013 断言 |
| 38 | `worker/__init__.py` 不注册新 runner | Celery 找不到任务，定时运行静默失败 | 注册 `managed_agent_runner` |
| 39 | 遥测枚举没有 managed_agent | 复用 assistant telemetry 会类型不匹配/丢事件 | MVP 用 `managed_agent_run`；后续再加枚举 |
| 40 | 删除保护函数是泛型的 | 忘记传 100 会让有启用任务的 agent 被删 | `assert_app_deletable(id,100)` |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `/api/v1/managed-agents*` | HTTP 管理 API | 平台纳管控制台 |
| `/api/v1/managed-agent-taxonomies*` | HTTP 分类法 API | 平台控制台、客户端目录 |
| `/api/v1/managed-agents/catalog` | HTTP 目录 API | 客户端“第三方智能体”页签 |
| `/api/v2/managed-agent/invoke` | HTTP 调用 API + `managed_agent:invoke` | 外部系统 |
| `managed_agent_run` | DB 表（字段见 §4.2） | 运营统计、审计、排障 |
| `managed_agent.*` 审计动作 | `audit_log` | 审计页 |
| `MANAGED_AGENT_APP_TYPE=100` | 内部常量 | F077 调度 |
| `ManagedAgentAdapter.invoke/health_check` | 内部 Python 协议 | native / openai_compatible / custom_http 适配器 |
| `RegistryService` / `ManagedAgentDao` | 内部 Python API | v1 管理面、v2 调用面、调度 runner |
| Agent Card | JSON `{version, capabilities[], inputs[]}` | 注册/编辑、任务预设校验、健康试调用 |
| 统一调用请求/响应 | JSON `{agent_id,input,session_id?}` → `{run_id,output,usage?}` | 外部系统、客户端 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| F076 服务账号/密钥/scope | `/api/v2` 鉴权、授权代管 | 服务账号 owner 被停用/提权时 fail-closed |
| F077 调度 | `scheduled_task` 表/beat/复检/失败闭环 | `_dispatch` 兜底、`observe_runs`、INPUT、`trigger_kind` |
| F047 广场 | 目录/筛选/卡片范式 | 收藏/统计只支持 5/10，不复用 |
| F004 OpenFGA | `PermissionService`/模型 | 模型升级必须写 store；admin 短路先于租户门 |
| 既有 `Tag`/`TagLink` | 标签 | 新增枚举值，不改表 |
| Fernet helpers | 凭据加密 | 硬编码共享 key，无轮换 |
| httpx 0.28.1 | 外部调用 | 默认 trust_env/timeout/无响应体上限 |
| DM8/MySQL | 三张新表 | 多 NULL 唯一、索引键长度、update_time 触发器 |

### 6.3 兼容、开关与回滚

- 开关：`managed_agent.enabled` 默认 false；关闭时 `managed-agent*` 路由 404、平台菜单/客户端页签隐藏；已有的 `app_type=100` 任务按 OFFLINE 处理（跳过/等待/拒绝），不自动停用。
- 回滚：关闭开关 → 删除/停用路由；FGA 可继续 pin 旧 model_id；`DROP TABLE managed_agent_run/managed_agent_taxonomy/managed_agent`；无既有表数据迁移。
- 部署顺序：代码（开关 off）→ FGA 升级脚本 → pin model_id/重启 → 开启开关 → 注册/验收。
- 对客文档：`docs/api/managed-agents.md`（新）+ `docs/定时任务与触发条款验收表.md` 增行 + `docs/开放API条款验收表.md` K 行 9→10 + `docs/api/open-api-auth.md` 9→10 + 接入指南 9→10。

### 6.4 spec 订正记录

- AC-22/AC-28 的防枚举码由 12101 改为 **11809**（与 F076/F077 一致）；12101 保留给 v1 管理面。
- 边界“同一接入地址允许多记录”改为“同租户内唯一”（与 AC-05 一致）。
- 错误码范围 12101–12114（新增 12114 调用请求体非法）。

---

## 7. 测试与可观测

**单元/契约**
- `test/managed_agent/`：模型租户登记、CRUD 校验、Agent Card、分类法、适配器（httpx mock）、SSRF、凭据掩码、频控、invoke 错误映射、健康扫描、调度集成。
- FGA：`test_openfga_authorization_model.py`（v2.0.5/19 类型）、`test_datasource_permission.py`、`test_f013_tenant_fga_tree.py`（修复既有红）、`test_scope_i18n_lockstep.py`。
- F077 回归：`test_trigger_guard.py`（99→DELETED、100 分支）、`test_run_service.py`、`test_task_service.py`、`test_trigger_endpoint.py`。
- 前端：平台表单/筛选组件测试；`pnpm lint/typecheck/check-i18n`；客户端目录类型。

**e2e**
- 本地 mock OpenAI-compatible + custom HTTP server：注册 → 分类 → 调用 → 定时/事件 → 健康 → 停用 → 目录可见性 → 删除保护。
- 多租户：第二个租户不可见/不可调用；`strict_tenant_filter` 断言。
- DM8：人工在目标实例验证唯一索引多 NULL/索引键上限/`trg_*_update_time`；MySQL `SHOW CREATE TABLE` 逐列核对。

**可观测**
- `managed_agent_run` 是权威调用留痕；日志只记 `run_id/agent_id/status/latency/error_code`，不记输入/凭据/上游正文。
- 健康状态/失败次数/统计从表读取；不新增遥测枚举（MVP）。
- 关键告警：连续健康失败、定时运行连续失败（复用 F077 通知）。

**手动验证（本机）**
```bash
cd src/backend
uv run pytest test/managed_agent/ -q
uv run pytest test/e2e/test_e2e_managed_agents.py -q
# FGA 模型升级脚本先 dry-run（不写 store）
uv run python scripts/migrate_fga_add_managed_agent_type.py
# 真库核对
mysql -h <host> -u <user> -p <db> -e "SHOW CREATE TABLE managed_agent\G"
```
- 平台 UI：`构建 → 智能体纳管`，注册一个 mock OpenAI-compatible agent → 分类 → 发布。
- 客户端 UI：`应用中心 → 第三方智能体`，按场景/行业/标签筛选，打开预览抽屉。
- 外部调用：
```bash
curl -X POST http://localhost:7860/api/v2/managed-agent/invoke \
  -H "Authorization: Bearer $BS_SAK" -H "Content-Type: application/json" \
  -d '{"agent_id":1,"input":{"query":"hello"}}'
```
- 定时：在纳管详情的「定时任务」面板建 cron → 立即运行/等一轮 → 查看 `managed_agent_run` 与任务列表。
- 凭据只从环境变量传，不写入命令历史或文档。

---

## 8. 后续改进 / 不打算做的事

- A2A Agent Card 自动发现/任务生命周期、流式/SSE、具名低代码连接器（Phase 2）。
- 评测集成、计费、故障转移、自动扩缩容、应用市场。
- 遥测枚举 `managed_agent` 与现有 agent monitoring 的深度整合（先用 `managed_agent_run`）。
- F077 `last_run_at` 历史缺陷（`last_duration_ms` 为空）不在本特性修复；本特性用 `managed_agent_run.latency_ms`。若行内要求 F077 UI 也显示耗时，另开小修。
- Fernet key 轮换/租户隔离（既有 C6 缺口）；F078 只复用并在部署文档标注。
- 多场景/多行业分类（当前一场景一行业）。
- IP pinning/出站防火墙落地（当前 allowlist + 预检 + 残余 DNS rebinding 风险）。

---

## 9. 设计评审待确认

| # | 事项 | 建议 |
|---|---|---|
| 1 | 删除语义 | 硬删除 + `managed_agent_run` 快照；名称/地址可复用 |
| 2 | 分类基数 | 每 agent 一个场景 + 一个行业；多选后置 |
| 3 | 接入地址唯一 | 同租户唯一；hash 非空（原生用哨兵 hash），不依赖 DM8 多 NULL |
| 4 | 防枚举码 | v2 用 11809；v1 管理面用 12101；spec 已订正 |
| 5 | 原生投影权限 | 同时要求 `managed_agent` 运行权限与原生应用运行权限 |
| 6 | 健康检查自动下线 | 默认只标 unhealthy；自动下线可配、默认 false |
| 7 | 功能开关 | 部署级全局开关；不做租户级 |
| 8 | 原生 workflow 统一调用 | MVP 通过 F054 式程序化运行 + 超时；超过一次 INPUT 返回失败（或 Phase 2 再做流式） |
| 9 | F077 既有缺陷 | 只修 manual/event 失败计数透传；`last_run_at` 另记 |
| 10 | FGA 升级脚本 | 按 F061 先例，dry-run/apply + model_id pin；部署 runbook 必须执行 |
| 11 | C6 既有硬编码 Fernet key | F078 复用现有 key、不新增明文密钥；是否接受由评审确认。若要求满足 C6，需独立做 key 注入/轮换（先 `MultiFernet` 兼容旧数据） |

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-10-19 | 初版：16 项设计决策、三表模型、F077/FGA 接入清单、40 个坑、设计评审待确认 | F078 design 阶段 |
