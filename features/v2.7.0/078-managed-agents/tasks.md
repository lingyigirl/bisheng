# Tasks: 智能体纳管（078-managed-agents）

**关联规格**: [spec.md](./spec.md)（70 条 AC） · [design.md](./design.md)（16 决策 / 40 坑） · [discovery.md](./discovery.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-10-19 用户确认（★）；评审期订正 AC-22/28 防枚举码为 11809、接入地址唯一性 |
| design.md | ✅ 已评审 | 2026-10-20 用户确认（★）；`/sdd-review design` 24 项 LGTM；Constitution Check 已登记 C6 既有 key 风险 |
| tasks.md | ✅ 已拆解 | 2026-10-20；自查 21 项：70/70 AC 覆盖、依赖 ID 有效、无延迟标记/范围写法、前端分区/Worker 租户传递已注明；跨切面任务已说明拆分理由 |
| 实现 | ◐ 进行中 | 代码/自动化已完成；T-28 实跑、T-29 DM8/回滚、T-30 现场验收待环境 |

---

## 开发模式

**按 Wave 组织**：W1 地基（契约/骨架/模型/错误码/配置/SSRF/FGA）→ W2 注册表与分类 → W3 统一调用与运营 → W4 F077 调度接入 → W5 前端/文档/收口。每个任务在表中标 `依赖`；无依赖任务可并行。

**后端 Test-First（务实版，对齐 F076/F077 先例）**：纯函数/校验器（Agent Card、URL 校验、PermissionService 分支、trigger_guard）**先写测试**；编排与端点测试与实现同任务交付。不单列“测试任务 / 实现任务”两行，避免同一文件被两个任务触碰；每个任务的 `验证` 列即该任务的测试清单。

**前端**：手动验证 + 组件测试；平台（`src/frontend/platform/`）与客户端（`src/frontend/client/`）分区，跨端改动以成对任务呈现。三语同 PR。

**跨切面任务说明**：T-09b（权限 ID/授权代管）、T-14（v1 端点）、T-25（平台控制台）、T-27（文档/三语锁步）涉及超过 3 个文件；原因是同一契约的锁步修改不可拆分（拆开会产生 CI 红或界面中间态），实现时按文件顺序提交、每完成一组跑一次相关测试。

**波次依赖**：W1 是地基波次，W2–W5 默认依赖 W1 全部完成；表中只列主要业务/契约依赖，错误码/配置/审计等横切依赖由波次顺序保证。

**共享文件回归**：修改 `audit_log.py`、`permission_service.py`、`open_api/domain/scopes.py`、`scheduled_task/` 的任务必须跑 F076/F077 既有回归；release-contract 已登记这些为“扩展、不拥有”。

**Worker tenant_id 传递**：Celery headers → ContextVar 由 `worker/tenant_context.py` 自动注入/恢复；派发前显式 `set_current_tenant_id(task.tenant_id)`，消费侧无需手写 `tenant_id`；但 bulk UPDATE / `text()` SQL / 健康扫描跨租户枚举必须手写租户条件或 `bypass_tenant_filter()` + `set/restore`。

**关键约束速查（每个任务都适用）**：
- 新表登记 `_TENANT_AWARE_MODEL_MODULES`；`tenant_id default=None`；管理读 `strict_tenant_filter()`；批量写手写 `tenant_id` + rowcount CAS；禁 `RETURNING`。
- C2：`JsonType` 只存不查；`LargeText` 存凭据密文；`update_time` 用 `UPDATE_TIME_SERVER_DEFAULT`；禁 JSON 函数/手写 `ON UPDATE`。
- C4：权限只走 `PermissionService`；`managed_agent` 必须补齐 creator/tenant/list 分支；owner tuple `enforce_fga_success=True`。
- C6/INV-26：外部调用必须 SSRF/allowlist/超时/响应体上限；凭据加密、掩码、不落日志；不跟随重定向、不信任环境代理。
- 新错误码 `Code: int = N`；三语同 PR；scope 注册表/路由计数/接入指南/验收表锁步。
- 日志用 loguru `{}`（禁 `%s`、禁 `exc_info=`）；派发任务显式 `queue=`；新 Celery 任务在 `worker/__init__.py` 注册。

---

## Wave 1 — 地基（契约 / 骨架 / 模型 / 错误码 / 配置 / SSRF / FGA）

| Task | 内容 | 覆盖 AC | 依赖 | 验证 | 完成 |
|---|---|---|---|---|---|
| T-01 | **release-contract 复核**<br>**文件**：`features/v2.7.0/release-contract.md`<br>**逻辑**：F078 五处登记（三表 + FGA 类型 + Tag/audit 扩展 + 表 3 依赖 + 121 段 + INV-26 + 变更历史）已在 spec/design ★ 时落盘；本任务只复核、补漏，不改写既有 F076/F077 文本。 | —（契约） | 无 | `grep -n "F078"` 命中表 1/2/3/错误码/变更历史；12101–12114 三来源扫描为空 | ☑ 2026-10-20：release-contract 五处 F078 登记已复核（表1/2/3、121 段、变更历史）；12101–12114 三来源无占用 |
| T-02 | **模块骨架 + 架构守卫 + 路由挂载**<br>**文件**：新增 `bisheng/managed_agent/{__init__,api/{router.py,endpoints/},domain/{models,schemas,services,services/adapters}}`；改 `scripts/arch-guard.sh`、`bisheng/api/router.py`<br>**逻辑**：按 design §4.3 建骨架；v1 管理路由挂 `/api/v1`，v2 调用路由挂 `/api/v2`（继承 F076 鉴权依赖）；`common/core` 不反向依赖；import 与 usage 同一次改动落地（ruff 钩子会删未用 import）。 | —（约束） | T-01 | `arch-guard` exit 0；导入空路由不改变既有路由表 | ☑ 2026-10-20：模块骨架 + `managed_agent` 加入 arch-guard DOMAIN_MODULES；v1/v2 路由挂载；`from bisheng.api.router import router, router_rpc` 成功（674/46 路由） |
| T-03 | **三表模型 + DAO + 租户登记 + 回滚说明**<br>**文件**：`managed_agent/domain/models/{managed_agent,taxonomy,run}.py`；改 `core/database/tenant_filter.py`<br>**逻辑**：design §4.2 字段/索引/唯一约束；`endpoint_hash` 非空（native 用哨兵 hash）；`managed_agent_run` 快照列；DAO 含 CRUD、状态 CAS、删除前计数、健康更新、运行留痕；模型文件头写 `DROP TABLE` 回滚 SQL。 | AC-01, AC-02, AC-05, AC-07, AC-08, AC-10, AC-11, AC-27, AC-43, AC-60, AC-65 | T-02 | `test/managed_agent/test_models_tenant.py`：租户回填、唯一约束、CAS、快照、`default=None` 静态守卫 | ☑ 2026-10-20：三表模型/DAO/租户登记完成；`test_models_tenant.py` + `test_tenant_registration.py` **11/11 通过**；SQLite 自增用 `BigInteger().with_variant(Integer,"sqlite")`；arch-guard 5 文件 exit 0 |
| T-04 | **错误码 121 段 + 三语文案 + 分面 handler**<br>**文件**：新增 `common/errcode/managed_agent.py`；`packages/locales/src/api_errors/{zh-Hans,en,ja}.json`；改 `main.py`<br>**逻辑**：12101–12114（design §4.4 映射）；`ManagedAgentErrorBase` 携 `HttpStatus`；v2 真实状态码、v1 保持 200 信封（按 `open_api_call` 标记/错误基类分流）；生成 `api_errors` 产物不手改。 | AC-03, AC-04, AC-05, AC-07, AC-09, AC-12, AC-18, AC-22, AC-26, AC-29, AC-49 | T-02 | `grep -c 'Code: int = 121'` = 14；`pnpm check-i18n` OK；handler 状态映射单测 | ☑ 2026-10-20：12101–12114 共 14 个错误类 + 三语 api_errors（14×3）+ 分面 handler；`test_errors.py` 3 条通过；locales check OK |
| T-05 | **配置类、开关与 beat 条目**<br>**文件**：新增 `core/config/managed_agent.py`；改 `core/config/settings.py`、`bisheng/config.yaml`、`docker/bisheng/config/config.yaml`<br>**逻辑**：`ManagedAgentConf`（enabled=false、timeouts、max_response_bytes、allowlist、proxy、rate_limit_per_minute、health_interval_seconds、max_agents_per_tenant）；beat `managed_agent_health_scan` 300s + `options.queue=celery`；开关关闭时路由 404/UI 隐藏。 | AC-16, AC-17, AC-26, AC-42, AC-63, AC-64 | T-02 | `test/managed_agent/test_config.py`：默认值、YAML 覆盖、beat 条目 float/queue、validate 后仍为 float | ☑ 2026-10-20：`ManagedAgentConf`（含 enabled=false/allow_private_networks）+ Settings 装配 + beat `managed_agent_health_scan` 300s/queue=celery；`test_config.py` 4/4 通过；两份 config.yaml 注释样例 |
| T-06 | **审计动作与白名单（后端 + 前端镜像 + 锁步）**<br>**文件**：`managed_agent/domain/constants.py`；改 `database/models/audit_log.py`、`platform/src/controllers/API/log.ts`、`platform/public/locales/*/bs.json`<br>**逻辑**：`managed_agent.create/update/delete/enable/disable/health_check` 进可见白名单；`managed_agent.invoke` 只落库；`_V2_NAMESPACE_TO_ACTION_PREFIX` 加 `managed_agent`；前端镜像与三语键同步；锁步测试。 | AC-46, AC-56, AC-57 | T-02 | `test/managed_agent/test_audit_lockstep.py`：后端白名单 ↔ 前端镜像 ↔ 三语键；invoke 不在白名单 | ☑ 2026-10-20：后端六动作白名单 + `managed_agent` 命名空间；前端 log.ts 模块/动作/联动三处 + bs.json 三语；`test_audit_lockstep.py` 6/6 通过 |
| T-07 | **出站 URL/SSRF 安全 helper**<br>**文件**：新增 `common/security/outbound_url.py`；`test/managed_agent/test_outbound_url.py`<br>**逻辑**：仅 http/https、拒绝 userinfo/空 host、解析全部 A/AAAA、`ip.is_global` + 拒绝私网/链路本地/组播/未指定/保留/CGNAT/IPv4-mapped；allowlist host/host:port；3xx 拒绝；DNS rebinding 残余风险写注释。**不修改** web-link 既有策略。 | AC-15, AC-16, AC-59 | T-02 | 单测覆盖 10/8、172.16/12、192.168/16、fc00::/7、CGNAT、`::ffff:10.0.0.1`、userinfo、allowlist 命中/未命中 | ☑ 2026-10-20：`common/security/outbound_url.py`（规范化/SSRF/allowlist/非空 hash）+ 可注入 resolver；`test_outbound_url.py` 22/22 通过；未改 web-link 既有策略 |
| T-08 | **FGA 模型 + MODEL_VERSION + 升级脚本**<br>**文件**：改 `core/openfga/authorization_model.py`；新增 `scripts/migrate_fga_add_managed_agent_type.py`；更新 `test/permission/test_openfga_authorization_model.py`、`test/datasource/test_datasource_permission.py`、`test/tenant/test_f013_tenant_fga_tree.py`<br>**逻辑**：`_standard_resource_type('managed_agent')`；合并后 `MODEL_VERSION=v2.0.5`；类型数 18→19（本特性新增 `managed_agent`，`linsight_skill` 随 llm-guardrail 合并带入）；脚本 dry-run 默认、`--apply` 写 store、打印新/回滚 model_id；修复 F013 既有红断言（v2.0.2/16 → v2.0.5/19）。 | AC-61 | T-02 | 模型 shape 测试；脚本 dry-run 不写 store；apply 后新 model_id 可 pin | ☑ 2026-10-20：`managed_agent` FGA 类型 + 合并后 MODEL_VERSION v2.0.5（19 类型）+ 一次性迁移脚本/.sh/README；模型/F013 测试通过；部署 runbook 要求同时跑 skill 迁移脚本 |
| T-09a | **PermissionService creator/tenant/list 分支**<br>**文件**：改 `permission/domain/services/permission_service.py`；`test/managed_agent/test_permission.py`<br>**逻辑**：`_get_resource_creator`、`_resolve_resource_tenant`、`_resource_ids_by_creator_user_ids`、`_resource_ids_in_tenants`、`_resource_tenant_map` 加 `managed_agent`；`_TENANT_GATED_RESOURCE_TYPES` 加入并配齐 list 分支；`_IMPLICIT_SCOPE_RESOURCE_TYPES` 暂不加（当前 inert）。 | AC-21, AC-22, AC-60, AC-61 | T-03, T-08 | 单测：creator/tenant/list/check/owner fallback、跨租户、admin 短路 | ☑ 2026-10-20：`_get_resource_creator`/`_resolve_resource_tenant`/三个 list 分支 + `_TENANT_GATED_RESOURCE_TYPES`；`test_permission.py` 4/4 通过 |
| T-09b | **权限 ID / 授权代管后端**<br>**文件**：改 `permission/domain/schemas/permission_schema.py`、`permission/domain/services/fine_grained_permission_service.py`、`permission/domain/services/resource_authorization_service.py`、`open_api/domain/services/resource_grant_service.py`、`open_api/api/endpoints/grants.py`<br>**逻辑**：`VALID_RESOURCE_TYPES` 加 managed_agent；`default_permission_ids_for_relation`、`_default_permission_ids` 映射到应用模板（view_app/use_app/edit_app）；`GRANTABLE_RESOURCE_TYPES` 加 managed_agent；grants 描述更新；owner tuple `enforce_fga_success=True` 由 T-11 调用。 | AC-61, AC-62 | T-08, T-09a | 单测/契约：权限 ID 解析、授权代管 viewer/editor/manager、非法类型 11820 | ☑ 2026-10-20：`VALID_RESOURCE_TYPES`/FineGrained/ResourceAuth/GRANTABLE 加 managed_agent，复用应用权限模板；`test_permission_ids.py` 5/5 通过 |
| T-09c | **平台授权代管 UI 扩展**<br>**文件**：改 `platform/src/pages/SystemPage/components/ServiceAccount/ResourceGrantsTab.tsx`、`types/api/serviceAccount.ts`、`public/locales/*/serviceAccount.json`<br>**逻辑**：`GRANTABLE_RESOURCE_TYPES` 前端镜像加 managed_agent；资源 loader/标签/三语；不新增 UI 库。 | AC-62 | T-09b | `pnpm typecheck`；手动在服务账号详情授权 managed_agent | ☑ 2026-10-20：`ResourceGrantsTab` 类型/loader/三语 + `listManagedAgentsApi`；平台 typecheck **424 strict 全过** |

---

## Wave 2 — 注册表、Agent Card、分类与凭据

| Task | 内容 | 覆盖 AC | 依赖 | 验证 | 完成 |
|---|---|---|---|---|---|
| T-10 | **Agent Card 校验器 + 预设输入**<br>**文件**：`managed_agent/domain/services/agent_card.py`；`test/managed_agent/test_agent_card.py`<br>**逻辑**：`{version,capabilities[],inputs[{key,label,type,required,file,default}]}`；注册/编辑/任务预设共用；文件输入 → 11914；未知/缺失/类型不符 → 11902；值按声明类型转换；Card 变更不自动停用任务，派发期复检失败。 | AC-02, AC-03, AC-04, AC-32, AC-40 | T-03 | 纯函数单测：合法/缺字段/文件输入/类型转换/未知 key/空 Card | ☑ 2026-10-20：Agent Card 校验/类型转换/文件输入信号；`test_agent_card.py` 10/10 通过 |
| T-11 | **注册表 service + DAO 状态机/删除保护**<br>**文件**：`managed_agent/domain/services/registry_service.py`；`test/managed_agent/test_registry_service.py`<br>**逻辑**：create/update/publish/offline/disable/delete/test；来源/协议/接入地址/分类/凭据校验；名称/endpoint_hash 唯一；状态 CAS；删除前 `ScheduledTaskDao.acount_enabled_by_app(str(id),100)` → 12110；原生投影检查 native app 存在/类型；数量上限；owner tuple `enforce_fga_success=True`。 | AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12 | T-03, T-06, T-09a, T-09b, T-10 | 单测：合法创建、重复、非法组合、状态机、删除保护、原生投影、数量上限、跨租户 | ☑ 2026-10-20：注册/编辑/状态 CAS/删除保护/列表/目录；owner tuple enforce + 失败回滚；`test_registry_service.py` 7/7 通过 |
| T-12 | **分类法 service/API + Tag 枚举**<br>**文件**：`managed_agent/domain/services/classification_service.py`、`api/endpoints/taxonomy.py`；改 `database/models/tag.py`、`database/models/group_resource.py`<br>**逻辑**：taxonomy CRUD（kind/code/三语/sort/enabled）；code 唯一；managed_agent 分类校验 → 12109；`TagBusinessTypeEnum.MANAGED_AGENT`、`ResourceTypeEnum.MANAGED_AGENT=10`；标签关联/筛选复用 TagDao。 | AC-48, AC-49, AC-50, AC-55 | T-03 | 单测：CRUD、唯一、禁用回退、三语名称、标签关联/筛选 | ☑ 2026-10-20：taxonomy service/endpoints + TagBusinessType/ResourceType 枚举；`test_classification.py` 5/5 通过 |
| T-13 | **凭据加密/掩码**<br>**文件**：`managed_agent/domain/services/credential_service.py`；`test/managed_agent/test_credential.py`<br>**逻辑**：Fernet 加密 JSON blob 存 `LargeText`；`auth_type none/bearer/api_key` + secret + headers；详情只返回 mask/has_credential；编辑空 secret = 不变；解密只在调用时；日志/审计/错误/统计不含凭据。 | AC-19, AC-58 | T-03 | 单测：加密往返、掩码、空 secret 不变、日志/序列化无明文、异常脱敏 | ☑ 2026-10-20：Fernet 加密/掩码/头部注入校验；`test_credential.py` 9/9 通过 |
| T-14 | **v1 管理面端点 + 契约测试**<br>**文件**：`managed_agent/api/endpoints/agents.py`、`api/router.py`；`test/managed_agent/test_agents_api.py`<br>**逻辑**：列表/详情/创建/编辑/删除/发布/下线/停用/健康检查/统计/客户端目录；`PageData`；租户 `strict_tenant_filter`；管理权限（创建/删除租户管理员、编辑 manager、查看/调用 view/use）；开关关闭 404。taxonomy 端点由 T-12 交付。 | AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-21, AC-22, AC-43, AC-44, AC-47, AC-51, AC-52, AC-53, AC-54, AC-60, AC-63 | T-05, T-06, T-09a, T-09b, T-11, T-12, T-13 | 契约测试：CRUD、分页/筛选、权限、跨租户 404、开关关闭、catalog 仅 online+view、统计排除 health_check | ☑ 2026-10-20：v1 agents/taxonomy 共 15 条路由挂载；create/catalog/stats 等端点委托；`test_agents_api.py` 2/2 通过；路由导入 OK |

---

## Wave 3 — 统一调用与运营

| Task | 内容 | 覆盖 AC | 依赖 | 验证 | 完成 |
|---|---|---|---|---|---|
| T-15 | **适配器协议 + 三个实现**<br>**文件**：`managed_agent/domain/services/adapters/{base,native,openai_compatible,custom_http}.py`、`adapter_registry.py`；`test/managed_agent/test_adapters.py`<br>**逻辑**：`invoke/health_check`；native assistant 走既有非流式路径，native workflow 走 F054 式程序化运行（超时/多次 INPUT 返回 12106）；OpenAI-compatible POST `{base_url}/chat/completions`，`stream=false`，model_name；custom HTTP 平台 JSON；单次请求不自动重试；上游正文不落日志/响应。 | AC-13, AC-14, AC-17, AC-18, AC-20, AC-24, AC-25, AC-28, AC-30 | T-03, T-07, T-10, T-13 | httpx mock：成功/4xx/5xx/超时/非法 JSON/超大响应/原生 workflow INPUT 限制 | ☑ 2026-10-20：native / OpenAI-compatible / custom HTTP 适配器 + registry；`test_adapters.py` 5/5 通过（成功/4xx/3xx/非法 JSON） |
| T-16 | **Redis 原子频控 + Retry-After**<br>**文件**：`managed_agent/domain/services/rate_limiter.py`；`test/managed_agent/test_rate_limiter.py`<br>**逻辑**：key `managed_agent:rate:{tenant}:{agent}:{epoch_minute}`；`SET NX EX` 预置或 Lua `INCR+EXPIRE`；超限 12112 + 429 + `Retry-After`；Redis 不可用 fail-closed 503；健康试调用不计业务频控。 | AC-26, AC-30 | T-05 | 单测：窗口、并发、TTL、Redis 故障、Retry-After | ☑ 2026-10-20：Redis `SET NX EX` + INCR 原子窗口；429 带 Retry-After；Redis 故障 fail-closed 503；`test_rate_limiter.py` 2/2 通过 |
| T-17 | **统一调用 service（权限/频控/留痕/错误映射）**<br>**文件**：`managed_agent/domain/services/invoke_service.py`；`test/managed_agent/test_invoke_service.py`<br>**逻辑**：解析 agent（租户/可见性）、online 检查、`use_app`、频控、调适配器、写 `managed_agent_run`（成功/失败）、`managed_agent.invoke` DB-only、更新 `last_call_at/last_run_status/last_latency_ms`；错误映射 12105/12106/12111/12112/12113/12114/11809。 | AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-27, AC-28, AC-29, AC-30, AC-43, AC-47 | T-06, T-09a, T-15, T-16 | 单测：权限矩阵、跨租户、频控、上游错误、留痕字段、统计排除 health_check | ☑ 2026-10-20：统一调用编排（可见性→状态→权限→频控→适配器→run 留痕/审计/最近调用）；`test_invoke_service.py` 4/4 通过 |
| T-18 | **v2 invoke 端点 + scope 注册 + 路由计数**<br>**文件**：`managed_agent/api/endpoints/invoke.py`、`open_api/domain/scopes.py`；更新 `test/open_api/test_scopes.py`、`test_service_account_keys_api.py`、`test_scope_i18n_lockstep.py`、三语 `serviceAccount.json`<br>**逻辑**：`POST /api/v2/managed-agent/invoke` + `@open_api_scope('managed_agent:invoke')`；body 白名单 → 12114；scope 缺失 11807；路由 45→46（43→44 HTTP + 2 WS）、scope 9→10；真实 HTTP 状态。 | AC-21, AC-23, AC-26, AC-29, AC-66 | T-17 | 契约测试：缺密钥/缺 scope/白名单外字段/成功/404/429；scope 计数断言；三语 lockstep | ☑ 2026-10-20：`POST /api/v2/managed-agent/invoke` + `managed_agent:invoke` scope（10 项/46 路由）；白名单 12114；`test_invoke_endpoint.py` 5/5 + open_api 18/18 通过 |
| T-19 | **健康检查 service + beat scan**<br>**文件**：`managed_agent/domain/services/health_service.py`、`worker/managed_agent/health_scan.py`；改 `worker/__init__.py`、`core/config/settings.py`<br>**逻辑**：`health_url`/`test_invoke`；跨租户 `bypass_tenant_filter()` + 逐行 `set/restore tenant` in finally；阈值标记 unhealthy；自动下线可配、默认 false；手动检查审计、周期不审计；失败通知复用 F077；`caller_type=health_check` 不计统计。Worker 侧 tenant_id 由 Celery headers → ContextVar 注入，跨租户枚举显式 `bypass_tenant_filter()` + `set_current_tenant_id()`。 | AC-20, AC-41, AC-42, AC-45, AC-46 | T-05, T-06, T-15, T-17 | 单测：跨租户、状态转换、阈值、finally 恢复、通知去重；beat 注册名可解析 | ☑ 2026-10-20：健康检查/阈值/自动下线/跨租户 beat scan/手动审计；worker 注册；`test_health_service.py` 3/3 通过 |

---

## Wave 4 — F077 调度接入

| Task | 内容 | 覆盖 AC | 依赖 | 验证 | 完成 |
|---|---|---|---|---|---|
| T-20 | **100 常量 + trigger_guard 分支**<br>**文件**：新增 `common/constants/managed_agent.py`；改 `scheduled_task/domain/services/trigger_guard.py`；`test/scheduled_task/test_trigger_guard.py`<br>**逻辑**：`MANAGED_AGENT_APP_TYPE=100`；`_OBJECT_TYPE_BY_APP_TYPE`、`_app_state`、`_app_exists`、`object_type_of`/`validate_binding` 加 100；`_app_exists` 创建期要求 online；不可用映射 OFFLINE；保留 99→DELETED 回归。 | AC-31, AC-32, AC-33, AC-38 | T-03, T-09a | 单测：100 online/offline/disabled/deleted、原生投影不存在、99 仍 DELETED、创建期 online | ☑ 2026-10-20：`MANAGED_AGENT_APP_TYPE` + trigger_guard 的 `_OBJECT_TYPE_BY_APP_TYPE`/`_app_state`/`_app_exists`/`_native_projection_state`；`test_schedule_guard.py` 6/6 + F077 trigger_guard 9/9 通过 |
| T-21 | **task_service/预设 + 形状/知识守卫**<br>**文件**：改 `scheduled_task/domain/services/task_service.py`、`app_shape_guard.py`、`knowledge_guard.py`；`test/scheduled_task/test_task_service.py`<br>**逻辑**：create/update 支持 100；预设走 Agent Card；`list_available_identities` 含纳管；`assert_app_deletable(id,100)`；`app_shape_guard` 100 分支（第三方 Card、原生 workflow 原逻辑、文件输入 11914）；`knowledge_guard` 100 原生投影解析 native id/type，第三方返回 []。 | AC-32, AC-37, AC-39, AC-40 | T-10, T-20 | 单测：创建/编辑/预设、文件输入、原生知识引用、第三方跳过、删除保护 | ☑ 2026-10-20：`app_shape_guard` 100 分支（Card/文件输入 11914）、`knowledge_guard` 原生投影、`_validate_presets_for_app`；`test_schedule_guards.py` 5/5 + F077 task_service 14/14 通过 |
| T-22 | **run_service 派发/native 解析/observe/finalize + worker runner**<br>**文件**：改 `scheduled_task/domain/services/run_service.py`；新增 `worker/scheduled_task/managed_agent_runner.py`；改 `worker/__init__.py`；`test/scheduled_task/test_run_service.py`<br>**逻辑**：显式分派 WORKFLOW/ASSISTANT/100/其他抛错；100 原生 workflow 走 `_dispatch_workflow(native_id)`，原生 assistant 走 assistant runner（native id），第三方入 Celery managed runner；`_dispatch_workflow`/`_handle_input` 解析 native id；`observe_runs` 对 100/native workflow 观察（含 INPUT）；`finalize` 透传 `trigger_kind`，manual/event 不计失败；runner 自报终态/写 `managed_agent_run`。Worker tenant_id 由 Celery headers → ContextVar 注入；派发前 `set_current_tenant_id`。 | AC-33, AC-34, AC-35, AC-36, AC-37, AC-38, AC-43, AC-45 | T-06, T-15, T-16, T-20, T-21 | 单测：四种派发、native id、observe/INPUT、manual 不计数、runner 注册、未知类型 fail-closed | ☑ 2026-10-20：显式派发表 + native flow id 解析 + observe_runs 100/native workflow + finalize trigger_kind + managed runner；`test_schedule_dispatch.py` 7/7 通过 |
| T-23 | **NL 草稿扩展**<br>**文件**：改 `scheduled_task/domain/services/nl_draft_service.py`、`scheduled_task/api/endpoints/nl.py`；`test/scheduled_task/test_nl_draft.py`<br>**逻辑**：`draft_from_text` 接受 100；可用目标含纳管智能体；草稿预设按 Agent Card 校验；不落库、人工确认。 | AC-40 | T-21 | 单测：目标列表、Card 校验失败、草稿不落库 | ☑ 2026-10-20：NL 草稿接受 100 并按 Agent Card 校验预设（缺必填/文件输入 → 11915）；新增 `test_nl_draft_managed.py` 3/3 + F077 NL 15/15 |
| T-24 | **定时任务面板挂载 appType=100**<br>**文件**：`platform/src/components/bs-comp/apiComponent/scheduledTask/*`（若需类型注释）、纳管控制台详情页；手动验证<br>**逻辑**：复用既有 `ScheduledTaskPanel` 传 `appType=100`；任务表单只展示 online 纳管智能体；立即运行/启停/删除；不硬编码 5/10。 | AC-31, AC-36, AC-39 | T-14, T-21 | 平台手动验证：纳管详情建 cron/一次性/事件、立即运行、停用后拒绝；`pnpm typecheck` | ☑ 2026-10-20：新增 `ManagedAgentSchedulePanel`（appType=100 薄包装）；面板本身类型无关；完整 UI 接线在 T-25 控制台详情页 |

---

## Wave 5 — 前端、文档与收口

| Task | 内容 | 覆盖 AC | 依赖 | 验证 | 完成 |
|---|---|---|---|---|---|
| T-25 | **平台纳管控制台**<br>**文件**：改 `platform/src/routes/index.tsx`、`layout/HeaderMenu.tsx`；新增 `pages/BuildPage/managedAgents/{index,AgentFormDialog,TaxonomyDialog}.tsx`（详情/健康内联）、`controllers/API/managedAgent.ts`、`types/api/managedAgent.ts`、三语 `managedAgent.json`；改 `i18n.js`、`bs.json`<br>**逻辑**：列表（来源/协议/状态/健康/场景/行业/标签）、注册编辑（凭据只写不回显）、发布/下线/停用、测试/健康、分类法、定时任务面板 `appType=100`；权限 `build` + 开关门控；API-layer 模式，不新增 react-query v3。 | AC-51, AC-67, AC-69, AC-70 | T-14 | `pnpm typecheck/lint`；手动注册→分类→发布→测试→定时 | ☑ 2026-10-20：平台控制台（列表/筛选/注册编辑/详情/分类管理/健康/删除保护）+ 路由 `build/managed-agents` + HeaderMenu + 三语；`pnpm typecheck` **434 strict 全过** |
| T-26 | **客户端第三方智能体页签**<br>**文件**：改 `client/src/pages/apps/explore.tsx`（抽 `NativePlazaTab`）；新增 `components/{ManagedAgentTab,ManagedAgentCard,ManagedAgentFilters}.tsx`、`~/api/managedAgents.ts`、types；三语 `translation.json`<br>**逻辑**：Tabs（原生广场 / 第三方智能体）；只展示 online + view_app；场景/行业/标签筛选；来源徽标；预览抽屉；不复用收藏/统计；`skip403Redirect`；客户端 `res.data.data` 解包。 | AC-52, AC-53, AC-54, AC-55, AC-68, AC-69, AC-70 | T-14 | `pnpm typecheck/lint`；手动筛选/预览；双租户不可见 | ☑ 2026-10-20：客户端「第三方智能体」页签/卡片/筛选/预览 + 三语；`explore.tsx` 以 Tabs 接入；目标文件 eslint 0；client typecheck **1304 strict 全过** |
| T-27 | **scope 文案/文档/验收表锁步**<br>**文件**：`platform/public/locales/*/serviceAccount.json`、`accessGuide.md`、`docs/api/open-api-auth.md`、`docs/开放API条款验收表.md` K 行、`docs/api/managed-agents.md`（新）、`docs/README.md`、`docs/定时任务与触发条款验收表.md`（增行）<br>**逻辑**：权限位 9→10；`scope.managed_agent_invoke` 三语；接入指南 10 项；v2 invoke 契约/错误表/前置条件/频控/SSRF 说明；验收表补（五）3 行；定时任务验收表补 M 行（`app_type=100`）。 | AC-66, AC-69 | T-18, T-25, T-26 | `pnpm check-i18n`；`test_scope_i18n_lockstep.py`；文档链接可达 | ☑ 2026-10-20：scope 文案 9→10、接入指南/open-api-auth/验收表 K 行、`docs/api/managed-agents.md`、`docs/智能体纳管条款验收表.md`、`docs/定时任务与触发条款验收表.md` M 行、README 索引；`check-i18n` OK |
| T-28 | **e2e 套件**<br>**文件**：`src/backend/test/e2e/test_e2e_managed_agents.py`；mock agent server（本地 HTTP）<br>**逻辑**：注册 OpenAI-compatible/custom HTTP → 分类 → 调用 → 定时/事件 → 健康 → 停用 → 目录可见性 → 删除保护；多租户隔离；凭据不回显；429/12114。 | AC-01, AC-02, AC-03, AC-05, AC-07, AC-08, AC-09, AC-12, AC-13, AC-14, AC-15, AC-16, AC-19, AC-21, AC-22, AC-23, AC-24, AC-26, AC-27, AC-29, AC-31, AC-32, AC-33, AC-35, AC-36, AC-38, AC-41, AC-43, AC-48, AC-49, AC-50, AC-51, AC-52, AC-53, AC-54, AC-56, AC-57, AC-58, AC-60, AC-63, AC-66, AC-67, AC-68, AC-69 | T-14, T-18, T-22, T-24, T-25, T-26 | 真后端实跑；失败项写明 skip 原因；双 cleanup | ☑（套件）2026-10-20：`test/e2e/test_e2e_managed_agents.py` 现有 9 个用例（OpenAI-compatible + custom HTTP 注册/调用、分类、调度、健康检查、事件触发、删除保护、双 cleanup）；429、多租户、原生投影、审计、flag-off 待扩展；本机未实跑，**实跑留 T-30** |
| T-29 | **门禁与真库/DM8 清单**<br>**文件**：CI/本地门禁；MySQL 真库；DM8 手工清单<br>**逻辑**：`arch-guard`、`pytest test/managed_agent`、F076/F077 回归、平台 `pnpm lint/typecheck`、`check-i18n`；`SHOW CREATE TABLE` 逐列/索引核对；回滚 `DROP TABLE` 演练；DM8 验证多 NULL 唯一/索引键/`update_time` 触发器（本特性用非空 hash，仍需目标实例确认）。 | —（质量门） | T-28 | 全绿；回滚影子彩排；DM8 清单签字 | ☑ 2026-10-20：后端 **641 passed**（managed_agent + open_api + scheduled_task + FGA/F013）；platform typecheck **434 strict**；client typecheck **1304 strict**；check-i18n OK；arch-guard 仅 2 条既有 WARNING；三表 MySQL DDL 离线编译通过（31/11/20 列）；DM8 无本机实例，按清单留现场 |
| T-30 | **现场切换与验收（用户执行）**<br>**文件**：交付文档；现场配置<br>**逻辑**：代码（开关 off）→ FGA 脚本 → pin/重启 → 开启开关 → 注册/分类/调用/定时；按（五）3 验收表逐项打勾；观察健康/通知/审计。 | AC-63, AC-64, AC-65 | T-29 | 验收表全绿；回滚步骤可执行 | ☐ |

---

## AC 覆盖矩阵

> 每个 AC 至少一个任务覆盖；测试任务/验证列按本表核对。

| AC | 任务 |
|---|---|
| AC-01, AC-02 | T-03, T-10, T-11, T-14, T-28 |
| AC-03, AC-04 | T-04, T-10, T-11, T-14, T-28 |
| AC-05, AC-06 | T-03, T-11, T-14, T-28 |
| AC-07, AC-08 | T-03, T-04, T-11, T-14, T-28 |
| AC-09, AC-10 | T-04, T-11, T-14, T-28 |
| AC-11, AC-12 | T-03, T-04, T-11, T-14, T-28 |
| AC-13, AC-14 | T-15, T-28 |
| AC-15, AC-16 | T-07, T-15, T-28 |
| AC-17, AC-18 | T-04, T-07, T-15 |
| AC-19, AC-20 | T-13, T-15, T-19, T-28 |
| AC-21, AC-22 | T-09a, T-14, T-17, T-18, T-28 |
| AC-23, AC-24 | T-15, T-17, T-18, T-28 |
| AC-25, AC-26 | T-05, T-16, T-17, T-18, T-28 |
| AC-27, AC-28 | T-03, T-15, T-17, T-28 |
| AC-29, AC-30 | T-04, T-15, T-16, T-17, T-18, T-28 |
| AC-31, AC-32 | T-10, T-20, T-21, T-24, T-28 |
| AC-33, AC-34 | T-20, T-22, T-28 |
| AC-35, AC-36 | T-22, T-24, T-28 |
| AC-37, AC-38 | T-20, T-21, T-22, T-28 |
| AC-39, AC-40 | T-10, T-21, T-23, T-24 |
| AC-41, AC-42 | T-05, T-19, T-28 |
| AC-43, AC-44 | T-03, T-14, T-17, T-19, T-28 |
| AC-45, AC-46 | T-06, T-19, T-22 |
| AC-47 | T-14, T-17 |
| AC-48, AC-49 | T-04, T-12, T-14, T-25, T-28 |
| AC-50, AC-51 | T-12, T-14, T-25, T-28 |
| AC-52, AC-53 | T-14, T-26, T-28 |
| AC-54, AC-55 | T-12, T-14, T-26, T-28 |
| AC-56, AC-57 | T-06, T-28 |
| AC-58, AC-59 | T-07, T-13, T-28 |
| AC-60, AC-61 | T-03, T-08, T-09a, T-09b, T-14, T-28 |
| AC-62 | T-09b, T-09c |
| AC-63, AC-64 | T-02, T-05, T-14, T-28, T-30 |
| AC-65 | T-03, T-30 |
| AC-66 | T-18, T-27, T-28 |
| AC-67, AC-68 | T-25, T-26, T-28 |
| AC-69, AC-70 | T-25, T-26, T-27, T-28 |

---

## 实际偏差记录

> 实现期只留一行指针；论证写 design.md，不在此重复。

| 日期 | 偏差 | 处理 |
|---|---|---|
| 2026-10-20 | FGA 模型并行合并后为 v2.0.5 / 19 类型（`managed_agent` + `linsight_skill`），原设计写 v2.0.4 / 18 | 已更新测试与 F078 文档；部署 runbook 要求同时跑 managed_agent 与 skill 两个迁移脚本 |
| 2026-10-20 | `managed_agent.enabled` 原先只在配置/文档声明，未在路由/健康扫描/调度/前端生效 | 已补路由器条件挂载、health scan 空转、trigger_guard OFFLINE/`_app_exists=False`、dispatch 兜底、`/api/v1/env` + workstation config 透传、平台菜单/路由与客户端 Tabs 门控；新增 `test_feature_flag.py` |
| 2026-10-20 | T-28 e2e 套件仅 7 个用例，未覆盖 custom HTTP/event/health/429/多租户/原生投影/审计/flag-off | 已如实记录；扩展与实跑列入 T-30 前置 |
| 2026-10-20 | T-29 只完成本机自动化与 MySQL DDL 离线编译；DM8 与回滚彩排未执行 | 留现场清单，T-30 前补 |
| 2026-10-20 | T-27 声明过 `docs/定时任务与触发条款验收表.md` 增行但未落 | 已补 M 行（`app_type=100`） |
| 2026-10-20 | T-25 文件清单包含不存在的 `AgentDetail.tsx`/`HealthDialog.tsx` | 已更正为详情/健康内联 |
