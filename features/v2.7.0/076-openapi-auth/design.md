# F076 开放 API 鉴权 — 设计（How）

> **本文档定位**：本特性**唯一的 How 真相**——关键决策（含被否方案与推翻条件）、接手必读的现状、代码里看不出的坑、对外契约。需求口径见 [spec.md](./spec.md)；执行流水见 [tasks.md](./tasks.md)。
> 上游口径参照（只取口径、不移植）：`dataelement/bisheng` 钉 SHA `e058f58eb8d03cf582725d31e194eeeb16fce205`，读法见 `docs/upstream-sync.md` §3.2。
> 修订历史见文末。

---

## 1. 目标与非目标

**目标**：在 `beijing-ns` 交付线自研实现「行内系统凭 API Key 调用 `/api/v2` 智能体应用（可指定工具）+ 密钥生命周期 + 管理面 + 审计」，满足公告（四）3，且**不改 OpenFGA 授权模型、不引入上游 F048 权限子系统**。

**非目标**（与 spec「本次明确排除」一致，此处只列会诱使设计走偏的几条）：
- 不做工具直调端点（工具只作为应用调用参数存在）；
- 不让服务账号成为一等授权主体（不改授权模型）；
- 不改造 `/api/v3`、不做 PAT/身份传递/任务模式开放 API；
- 不做限流/配额/IP 白名单/幂等（对齐上游 R8/P2 的范围裁定，不预埋字段）。

---

## 2. 关键约束（动手前必须知道）

**全局铁律遵循 `docs/constitution.md` C1–C7**（DDD 分层 / 双 DB / 多租户自动注入 / 权限统一入口 / 错误码 / 无硬编码密钥 / 前端 store 不直连 HTTP），本节不重抄。

**Constitution Check 结论（逐条核对，无违反）**：C1——新模块 `src/backend/bisheng/open_api/` 按 DDD 分层，`common/core` 不反向依赖，需把模块名补进 `scripts/arch-guard.sh:21`；C2——三张新表按 SQLModel `create_all` 建，如需迁移用 `dialect_helpers` 方言件；C3——三表登记进 `tenant_filter`、`tenant_id` 默认 None、匿名查密钥显式 `bypass_tenant_filter`、依赖内重建租户上下文（§3 决策 9）；C4——授权走 `PermissionService.authorize()`、判定走 `check()`，不改授权模型；C5——申领 118 段、`Code: int = N`、三语齐备；C6——只存 SHA-256 摘要、明文只出现一次；C7——前端经 `@/controllers/request`。

本特性**特有**的硬性约束：

| # | 约束 | 说明 |
|---|---|---|
| 1 | 状态聚合延迟 ≤5 秒 | 吊销/停用/编辑走"清缓存 + TTL 兜底"，TTL 默认 3 秒、上限 5——spec AC-05/11/13/34 时延口径的来源 |
| 2 | 兼容优先（破坏性变更处置） | `/api/v2` 强制鉴权会打断存量调用方：开关默认关、关闭时完全旧行为（AC-21），随交付给切换与回退步骤（`.claude/rules/version-control.md`） |
| 3 | 上游数据格式依赖 | 只对齐上游**口径**（`bs-sak-` 前缀 / 权限位命名 / 错误码语义），不依赖上游代码；上游锚点 SHA 见文首 |
| 4 | 部署形态依赖（**待确认**） | 北京农商行是否启用商业版网关未定：网关不代理 `/api/v2/assistant\|workflow/chat/**` 的 WS，且内容安全/限流发生在后端鉴权**之前**（`docs/architecture/11-gateway.md:242/300/426`）——两套拓扑的鉴权落点与前置副作用需写进交付文档 |
| 5 | 迁移形态 | 整表新建走 SQLModel `table=True`（`create_all`）；如需 Alembic 则 DDL-only、单 head、`alembic_helpers/online.py:13/29` 幂等守卫 |

---

## 3. 方案对比与选定（关键决策）

> 每条记录：**选定** / 被否备选 / **何时该推翻**。

### 决策 1：服务账号的身份模型 = 专用最小权限用户（非 FGA 主体）

- **选定**：`service_account.resource_owner_user_id` 指向一个由平台在创建账号时自动创建的专用用户；运行时装它为 `UserPayload`（复用 `open_endpoints/domain/utils.py:53` 的装载方式），资源可见范围 = 该用户的既有 ReBAC 授权；管理端"资源授权"底层调用 `PermissionService.authorize()`。
- **被否**：① 上游式独立 FGA 主体（`service_account:{id}`）——需改授权模型 DSL、bump `MODEL_VERSION`、改 `_expand_subject` 等约 10 处，且上游实测证明该体系依赖 F048 整层，与"不引入 F048"冲突；② 影子用户不由平台创建（管理员手建）——运维易错、边界不可控。
- **代价**：服务账号与一个真实用户共享身份语义（审计上用 `actor_kind=service_account` + credential_id 区分）。
- **何时推翻**：客户/合规明确要求"服务账号必须是独立主体、不与任何自然人共享权限"→ 升为二期 FGA 主体改造（改动清单见规划书 §2.4）。

### 决策 2：鉴权接入点 = 路由级依赖 + 端点级权限位标记（fail-closed）

- **选定**：`api/router.py:105` 的 `router_rpc` 加 `dependencies=[Depends(verify_open_api_access)]`；依赖内按开关分流；每个端点用 `@open_api_scope("<scope>")` 打标，依赖经 `request.scope["endpoint"]` 读取 marker；无 marker → 500/11811。
- **被否**：① 纯中间件——拿不到端点对象（marker 读取依赖 routing 层），且 `BaseHTTPMiddleware` 读 body 会整包缓冲；② 逐端点手写校验——44 处重复，漏一处即裸奔。
- **注意**：路由级依赖**对 WS 同样生效**（FastAPI 0.121 已验证，见 §5 坑 3）；依赖签名用 `HTTPConnection`（http/websocket 通吃），WS 分支必须 `raise WebSocketException(1008)`。
- **何时推翻**：若 FastAPI 大版本升级改变了 WS 依赖注入行为（升级时用守卫测试回归）。

### 决策 3：失败语义 = 真实 HTTP 状态 + 专用异常处理

- **选定**：为 `/api/v2` 注册专用异常处理，使 118xx 对应的失败返回**真实 HTTP 状态**（401/403/404/400/503/500）；响应体仍为统一信封（`status_code/status_message/data`）。
- **被否**：沿用平台"HTTP 200 + 信封"惯例——行内系统按 401 做重试/熔断/告警会全部失效；且上游 v2 密钥接口文档明确声明 400/401/403/404/503。
- **实现要点**：现状 `handle_http_exception`（`main.py:29-35`）返回 `JSONResponse(content=msg)` 无状态码——**不能复用它**；新增的 handler 必须**不访问 `req.method`**（WS scope 下不存在，会 AttributeError）。SSE 场景：响应头发出前失败用 HTTP 状态；发出后走流内终态并记业务码。
- **何时推翻**：若平台统一改造信封与状态码口径（全局变更），本特性跟随。

### 决策 4：平台免登录面 = 统一挂 `enable_guest_access` 开关（默认关）

- **选定**：guest 页依赖的 **8 个端点**（清单见 §6.1）统一由 `enable_guest_access` 门控；关 = 与其余端点一样要求密钥；开 = 匿名放行。现状该开关只门控 3 个端点（`assistant.py:317/339`、`flow.py:21`），需补齐其余 5 个。
- **被否**：① 直接豁免这 8 个端点——外部系统可匿名调用主要端点，条款形同虚设；② 给 guest 页发 share-token（更强，但 +3~5 天且要改链接生成与前端）。
- **何时推翻**：若产品决定把免登录页迁移到独立通道（`/api/v3` 式），本开关随之下线。

### 决策 5：密钥存储 = SHA-256 摘要 + 查看用 Fernet 密文（2026-10-22 修订）

- **选定**：`bs-sak-` + `secrets.token_urlsafe(32)`（43 字符，断言长度）；校验仍用 `token_hash = sha256(明文)`（唯一）+ `key_prefix` + `last4`；另存 `token_ciphertext = Fernet(明文)`（`token_ciphertext` 可空），仅供管理员经**显式查看动作**解密，查看写审计。比对用 `hmac.compare_digest`。
- **被否**：① 只存摘要（原决策）——用户明确要求后台可随时查看明文，无法满足；② 明文直接落库——不可接受；③ SM3（国密）——与上游口径偏离且依赖 OpenSSL 构建（延后）。
- **代价**：数据库 + 源码中的 Fernet 密钥可解出明文；风险与既有 datasource 凭据同一量级。控制措施：密文不进入列表/缓存/审计、查看仅限租户管理员、每次查看留审计、响应 `no-store`。
- **何时推翻**：行方安全评审不接受可查看，或要求存储侧国密 → 回到"只存摘要"并加配置开关关闭查看（本增量不预埋该开关）。

### 决策 6：工具参数 = 应用调用请求体的 `tools[]`（不建独立端点）

- **选定**：`POST /api/v2/assistant/chat/completions` 与 `POST /api/v2/workflow/invoke` 请求体新增可选 `tools` 数组（元素 `{tool_key, ...}`，最终形态实现期定）；取值限该应用**已绑定且启用**的工具；越权 → 403/11815；不传 = 应用默认（向后兼容）。应用详情响应（`assistant/info`、`flows/{id}`）补"可用工具清单"。
- **被否**：工具直调端点（spec 明确排除）。
- **注意**：`OpenAIChatCompletionReq` 已有一个 **未被消费的 `tools: list[dict]` 占位**（`api/v1/schemas.py:395`）——实现时先确认其语义再复用，不要假定它是现成能力；`schemas.py:475/500` 还有另外两处 tools 声明（Ideas/chat 输入条），**勿混用**。
- **何时推翻**：客户明确要求工具直调 → 另立特性。

### 决策 7：身份输入 = 拒绝调用方裸 `user_id`

- **选定**：强制开启后，请求中出现裸 `user_id`（query/body/form）一律 400/11817；引用点：`filelib.py:173/378/510/534/637`、`chat.py:87`。`assistant.py:332-338` 的"只进日志"用法一并禁止。
- **被否**：① 静默忽略——对接方看到"传了没生效"难排查；② 保留——外部可指定任意身份，含提权面。
- **何时推翻**：二期做受控代表调用（`X-On-Behalf-Of` + 范围限定）时，用新参数替代，不复用裸 `user_id`。

### 决策 8：审计 = 复用 `audit_log` + 有界队列 best-effort 写

- **选定**：逐调用 `action="open_api.call"`、管理动作 `open_api.*`，写既有 `audit_log`（`database/models/audit_log.py`，`AuditLogDao.ainsert_v2` 单条 / `ainsert_audit_logs` 批量）；写入 best-effort（队列满丢审计不阻塞请求）；**不进**平台点审计页白名单（`_UI_VISIBLE_V2_ACTIONS`），只落库取证。
- **被否**：① 新建审计表——与平台审计治理分叉；② 请求内同步写——把审计可用性绑到业务链路上。
- **何时推翻**：平台统一审计治理上线（保留期/归档）时，本特性跟随（对客口径同步从"≥6 个月"改为平台口径）。
- **实现参数（SDD 未定，实现期落定）**：队列 `QUEUE_MAXSIZE=1000`、批大小 `BATCH_SIZE=50`、批量窗口 `FLUSH_INTERVAL_SECONDS=2`、队列满**丢最新一条并计数** + 丢包告警限流 60s；关停时**直接丢弃**手上这批（不在事件循环收尾阶段做 DB I/O）。动作词表落 `open_api/domain/constants.py::OpenApiAuditAction`（枚举而非裸串：拼错会行入库但永远查不到）。

### 决策 9：租户上下文在依赖内显式重建（不是选型，是必须）

- **背景**：中间件会把 `Authorization: Bearer bs-sak-…` 当 JWT 解码、失败后仍 `set_current_tenant_id(1)`（详见 §5 坑 1）。
- **选定**：依赖校验通过后显式 `set_current_tenant_id(owner 租户)` + `set_visible_tenant_ids({1, owner 租户})`，并**在请求结束时 reset**（对齐上游 `access_context.py` 的处置）。
- **例外**：匿名查密钥本身必须 `bypass_tenant_filter()`（调用方租户未知）。

### 决策 10：强制口径——"强制谁"要说清（实现期澄清，2026-10-15）

- **背景**：spec AC-34 说"部署级与租户级同时开启才强制"，但**无名请求识别不出租户**——实现时才发现这句话需要一个可执行的口径。
- **选定**：
  1. **部署总闸关** → 完全旧行为：忽略 Authorization，连无效密钥也不拦（AC-21 的字面条件就是"强制鉴权关闭"，这里指总闸）。
  2. **部署总闸开 + 无名请求** → 按**默认租户**（旧路径的执行身份所在租户，`DEFAULT_TENANT_ID`）的租户开关判定：开=401/11801；关=放行旧行为（分租户灰度）。
  3. **带密钥的请求** → **永远校验、永远按密钥身份执行**，不因租户开关关闭而回落默认操作员。
- **被否**：把"租户开关关"延伸为"连呈现的合法密钥也忽略"——那会让一个带着密钥的调用方反而以**默认操作员（常配超管）**身份执行，是"带密钥反而提权"的安全退化。
- **代价/边界**：多租户部署里，无名请求按默认租户判定，无法按"实际发起租户"逐一放行——要彻底关闭无名通道，把默认租户的开关也打开即可（fail-closed 方向）。
- **何时推翻**：若客户要求"按租户灰度无名流量"，需要调用方提供租户标识（新的显式机制），届时应先在 spec 层评审。

### 决策 11：每 Key 智能体白名单（2026-10-22 新增）

- **背景**：scope 是功能维度、资源授权挂在服务账号的专用身份上，因此**同一账号下的所有 Key 共享可见资源**。用户要求不同 Key 能调用不同的具体智能体。
- **选定**：`api_credential.allowed_apps`（JsonType，可空）存 `[{type, id}]`，覆盖 `assistant` / `workflow` / `managed_agent`。NULL = 不限制（兼容存量）；`[]` = 全拒绝；非空 = 只允许列表内对象。判定顺序：scope（端点族）→ 白名单（具体对象）→ 资源授权（ReBAC 档位），三者同时满足才放行。
- **被否**：① 把资源授权改成 Key 级——FGA 主体是专用身份，改模型成本高且与 F076 决策 1 冲突；② 新建 `api_credential_agent` 子表——列表小、与既有 `scopes` JSON 同构，JSON 足够且读取随凭据缓存一次完成；③ 用 scope 编码具体对象（`assistant:<id>`）——scope 是端点级注册表，无法表达"请求体里的目标对象"。
- **语义**：白名单只收窄、不扩权；写入期只校验资源对账号**可见**（避免只读 Key 被挡），运行期档位不足仍按 11809/11808；`schedule/trigger` 属于间接调用，按 `task.app_type/app_id` 一并判定。
- **何时推翻**：若需要按"用户/部门"给 Key 授权，或白名单规模超过数百条 → 升级为独立授权表/子表。

### 决策 12：明文查看 = 独立密文 + 显式审计动作（2026-10-22 新增）

- **选定**：签发时同一事务写 `token_hash` 与 `token_ciphertext`；新增 `POST /api/v1/service-accounts/{id}/keys/{key_id}/reveal`，仅租户管理员可调用，返回 `no-store`，写审计动作 `open_api.api_key.reveal`（不含明文）。存储层用项目既有 `encrypt_token/decrypt_token`（`core/config/settings.py`），不引入新加密库。
- **被否**：① 只提供"重新签发"——不满足随时查看；② 明文落库——不可接受；③ 单独建密钥表——列表查询本就不序列化该列，收益不足以抵消新表/租户登记成本。
- **边界**：存量 Key 无密文 → 11823，需重新签发；密文损坏或 `secret_key` 变更 → 11823；已吊销/已过期仍可查看（吊销 ≠ 删除，便于取证）。
- **何时推翻**：行方要求"查看必须二次认证/审批" → 在 reveal 端点前接入审批或 MFA，不改存储。

---

## 4. 系统现状（接手必读）

### 4.1 一次带密钥调用的完整链路

```
行内系统 → Authorization: Bearer bs-sak-<43>
  │
  ├─ CustomMiddleware（http_middleware.py）
  │    · 把 Bearer 当 JWT 解析 → 失败静默吞 → 仍 set_current_tenant_id(1)   ⚠ 必须被依赖覆盖
  │    · /api/v2 不在豁免表 → 无 token_version/停用校验；visible_tenant_ids 不设置
  ▼
├─ 路由级依赖 verify_open_api_access（open_api/api/dependencies.py）
│    ① 开关判定（部署级 ∧ 租户级；关 → 直接 yield 默认操作员上下文，AC-21）
│    ② 提取 Bearer → 格式校验（^(bs-sak-)[A-Za-z0-9_-]{43}$）→ 失败 401/11801
│    ③ sha256 → Redis 正缓存（oapi:cred:{digest}，TTL 3s）→ 未命中查库（bypass_tenant_filter）→ 回填
│    ④ hmac.compare_digest → 撤销/过期/账号停用/租户停用 → 401/11802·11803·11804·11805、403/11806
│    ⑤ marker：None → 500/11811；scope 不足 → 403/11807
│    ⑥ 裸 user_id 检查 → 400/11817
│    ⑦ 装载 owner：set_current_tenant_id(owner 租户) + set_visible_tenant_ids({1, owner 租户})
│       + 防提权校验（owner 已是管理员 → 403/11816）；建 PermissionActor/ContextVar
│    ⑧ yield；finally 全部 reset
  ▼
├─ 业务端点（assistant / workflow / filelib / knowledge / citation / llm / chat）
│    · 资源可见性按 owner 的既有 ReBAC 判定（PermissionService，单入口不变）
│    · tools[] 白名单校验（应用绑定关系）→ 越权 403/11815
  ▼
└─ 调用审计（中间件或依赖后置写）：open_api.call + status/业务码/耗时/密钥掩码
```

### 4.2 关键数据结构

**表 `service_account`**（租户感知）：`id` PK / `tenant_id` / `name`(128, 租户内唯一) / `description`(512) / `resource_owner_user_id`(BigInt, index) / `disabled_at` / `deleted_at` / `created_by` / `create_time` / `update_time`。

**表 `api_credential`**（租户感知）：
`id` PK / `tenant_id` / `service_account_id`(index) / `name`(128) / `key_prefix`(16, 固定 `bs-sak-`) / `last4`(4) / **`token_hash`(64, unique)** / **`token_ciphertext`(512, 可空；Fernet 密文，仅查看用)** / `scopes`(JsonType, 数组) / **`allowed_apps`(JsonType, 可空；`[{type,id}]`，NULL=不限制、`[]`=全拒绝)** / `expires_at`(可空，排他) / `revoked_at` / `revoke_reason`(32: manual/batch/reissued/subject_disabled/subject_deleted) / `last_used_at` / `created_by` / `create_time` / `update_time`。
`is_valid_at(now) = revoked_at is None and (expires_at is None or expires_at > now)`；`key_mask = key_prefix + "********" + last4`。

**表 `open_api_tenant_setting`**（租户感知，主键 `tenant_id`）：`enforce_keys`(Bool, 默认 0) / `update_time`。**缺行 = 未启用**（fail-closed）。

**scope 注册表**：12 项可签发（`workflow:invoke`/`workflow:read`/`assistant:invoke`/`assistant:read`/`llm:workbench`/`chat:invoke`/`knowledge:read`/`knowledge:write`/`diagnostics:read`/`schedule:trigger`/`managed_agent:invoke`/`delegate`）；隐含规则一条：`knowledge:write` ⇒ `knowledge:read`；每条 scope 硬编码其 `(METHOD, path)` 端点集合（逐条登记，不用通配，WS 用 `WS` 方法串）；`delegate` 本期可签发但无生效行为。

**错误码 118 段**：11801 缺失/格式 / 11802 无效 / 11803 过期 / 11804 吊销 / 11805 账号停用 / 11806 租户停用 / 11807 缺权限位（回带 `required`）/ 11808 资源未授权 / 11809 防枚举 404 / 11810 owner 校验失败（400）/ 11811 端点漏标（500）/ 11812 鉴权后端不可用（503）/ 11813 账号重名（400）/ 11814 授权服务不可用（503）/ 11815 工具越权（403）/ 11816 owner 被提权（403）/ 11817 裸 user_id（400）/ 11818 `tools[]` 在本次调用形态下无法生效（400，T-18）/ 11819 签发期指定未登记权限位（400，T-21）/ 11820 授权代管资源类型或档位不支持（400，T-22）/ 11821 专用身份禁止在用户侧人工管理（400，T-26）/ **11822 密钥白名单指定了不可分配的智能体（400，2026-10-22 增量）/ 11823 密钥不可查看明文（400，2026-10-22 增量）**。**权威清单以 release-contract 的 118 段登记行为准**（本行为设计期口径，实现期新增的码在此追加）。

**对外 JSON 形状（设计定稿，实现期不得随意改名）**：

| 接口 | 请求 | 响应（`data` 内） |
|---|---|---|
| `POST /api/v1/service-accounts` | `{name, description?}` | `{id, name, description, resource_owner_user_id, enabled, key_count, last_used_at?}` |
| `GET /api/v1/service-accounts` | `?keyword&page&page_size` | `{data: [同上形状], total}` |
| `POST /api/v1/service-accounts/{id}/keys` | `{name, scopes: [], allowed_apps?: [{type,id}] \| null, expires_at?}` | `{id, key_mask, plaintext, scopes, allowed_apps, expires_at, create_time}` |
| `PATCH /api/v1/service-accounts/{id}/keys/{key_id}` | `{name?, scopes?, allowed_apps?: [...] \| null, expires_at?}` | `{id, name, key_mask, scopes, allowed_apps, expires_at, revoked_at?, last_used_at?, status, revealable}` |
| `GET /api/v1/service-accounts/{id}/keys` | — | `{data: [{id, name, key_mask, scopes, allowed_apps, expires_at, revoked_at?, last_used_at?, status, revealable}], total}` |
| `POST /api/v1/service-accounts/{id}/keys/{key_id}/reveal` | — | `{id, key_mask, plaintext, scopes, allowed_apps, expires_at}`（`no-store`；写审计） |
| `GET /api/v1/service-accounts/{id}/assignable-agents` | — | `{data: [{type, id, name, level, invocable}], total}` |
| `POST /api/v1/open-api/settings` | `{enforce_keys: bool}` | `{tenant_id, enforce_keys, update_time}` |
| `GET /api/v2/auth/whoami` | — | `{account_id, account_name, key_mask, scopes: [], allowed_apps: [...] \| null, expires_at?, tenant_id}` |

错误信封统一 `{status_code, status_message, data}`；`11807` 时 `data.required` 回带缺失权限位，`11815` 时 `data.tool_key` 回带工具标识。

### 4.3 关键模块职责（新建 + 改动点）

**新建**（分层：`open_api/{api/{router,endpoints,dependencies}, domain/{models,services,schemas}}`；**DAO 按新模块约定放 `domain/models/`**——对齐 datasource / doc_extract 先例，不设独立 `repositories/` 目录；职责边界 = 做什么 + **不做什么**）：

| 模块 | 做什么 | **不做什么** |
|---|---|---|
| `open_api/domain/services/credential_service.py` | 签发（生成/哈希/密文/落库）、吊销、改名/改 scope/改白名单/改期、按显式动作解密查看 | 不做资源授权判定；不在列表/缓存/审计中返回密文；查看必须由管理端点显式调用 |
| `open_api/domain/agent_scope.py` + `domain/services/agent_scope_service.py`（2026-10-22 增量） | 白名单归一化/判定、可分配智能体目录、写入期"资源对账号可见"校验 | 不授予资源；不替代 PermissionService；不把 `[]` 当"不限制" |
| `open_api/domain/services/credential_validator.py` | Bearer 解析、缓存查证、撤销/过期/主体活性校验 → principal | 不判资源可见性；不在缓存缺失且 DB 故障时放行（fail-closed） |
| `open_api/api/dependencies.py` | 门禁：开关判定、marker/scope、身份输入检查、上下文装载与 reset | **不读请求体**；不做业务分支；不吞异常（WS 用 1008，HTTP 用 118xx） |
| `open_api/domain/services/tenant_setting_service.py` | 读/写租户级 `enforce_keys`（5s 缓存） | 不决定部署级开关（与运算在依赖内完成） |
| `open_api/domain/services/call_audit_service.py` | 入队 + 批量落库（best-effort） | 不阻塞/不改变调用结果；不读请求体；不写明文 |
| `open_api/api/endpoints/{service_account, service_account_keys, settings, grants, auth}` | 管理面 CRUD/密钥/开关/授权代管/可分配智能体目录 + whoami + reveal | 权限判定与资源授权全部下沉到 PermissionService，端点不做终审 |
| platform `pages/SystemPage/components/ServiceAccount/**` + `controllers/API/serviceAccount.ts` | 管理界面（列表/创建/详情/密钥/授权） | store 不直连 HTTP（C7）；不承担对接方文档 |

其余新建：`common/errcode/open_api.py`、`core/config/open_api.py`（`OpenApiConf`：enforce_keys / credential_cache_ttl_seconds / service_account_idle_days）、三语 locale（新 namespace 登记进 `i18n.js`）。

**改动**：`api/router.py:105`（挂依赖）、`Settings` 装配（`settings.py`）、`scripts/arch-guard.sh:21`、`core/database/tenant_filter.py` 登记、`enable_guest_access` 覆盖 8 端点、`/api/v2` 专用异常处理（`main.py`）、对外文档 `docs/api/*`。

---

## 5. 已知坑 / 反直觉事实

1. **中间件把密钥当 JWT 并污染租户**：`_extract_http_access_token` 对任意 Bearer 原样返回；`_decode_jwt_subject` 失败静默返回 None；`_set_tenant_context` 仍 `set_current_tenant_id(DEFAULT_TENANT_ID=1)`（`http_middleware.py:60-121`）。→ 不自 seed 租户的端点（chat 族）会在 **ROOT 租户**执行，多租户下是跨租户面。**依赖内必须重设租户**（决策 9）。
2. **全局异常处理把 HTTP 状态拍平**：`handle_http_exception`（`main.py:29-35`）返回无状态码的 `JSONResponse` → HTTP 200。要真 401 必须另设处理（决策 3）。
3. **WS 上 raise HTTPException 会 500**：本仓 handler 访问 `req.method`，WS scope 无 method → AttributeError → 握手 500 + traceback（实机复现）。WS 依赖一律 `raise WebSocketException(1008)`（预 accept → 客户端见 403）。
4. **FastAPI 先解析 body 再跑依赖**（`routing.py:322-331` → `:378`）：未鉴权的大文件上传会被完整接收后才 401（已知限制）；**审计/中间件绝不能读 body**（BaseHTTPMiddleware 读 body 即整包缓冲）。
5. **v1 的 `?t=` 是死参**：`AuthJwt.get_subject(auth_from='websocket')` 只用 cookie，query token 被覆盖（`auth.py:169-171`）——WS 传参方案不要照抄 v1 习惯。
6. **`enable_guest_access` 只门控 3 个端点**（assistant info/list、flows），免登录页用的其余 5 个端点（含两个 WS）**没有任何门**——不能把"开关关"当成免登录面已被关掉的依据（决策 4 要补齐）。
7. **WS 的 accept 在共享代码**：`chat_manager.accept_client`（`common/chat/manager.py:166-168`）v1/v2 共用——若将来用 `Sec-WebSocket-Protocol` 方案需改它并评估 v1 影响（本期不用）。
8. **bulk update / raw SQL 没有租户自动注入**：`last_used_at` 回写、批量吊销如用 bulk 必须手写租户条件（C3 的例外条款）。
9. **路由级依赖对 WS 生效但失败语义不同**：HTTP 上 401 是真状态；WS 上是"拒绝握手"。守卫测试要分别断言。
10. **`download_statistic` 读服务器 `.log`**（`filelib.py:493-504`）：归 `knowledge:read` 后，"有密钥即可推导日志路径读取"——设计上保留既有护栏（后缀/路径前缀），并在对外文档提示该端点语义。
11. **存量文档推荐 default operator = super_admin**（`docs/api/filelib-retrieve.md` §2）：切换后按 owner 判定，现场可能表现为"切开关后 403"——迁移指引必须单列该排查条目，W4 一并改写该文档。
12. **密钥进 URL 必落日志**（仓内三处）：`http_middleware.py:306/356`、`main.py:34/40`、uvicorn WS 日志——禁止 `?api_key=`/`?t=` 方案（spec AC 已由"握手 Authorization 头"承接）。
13. **管理面 11805 是 HTTP 401，而 platform 把任何 401 当登录失效**：`OpenApiAccountDisabledError`（11805）`HttpStatus = 401`，且 `handle_open_api_error` 对管理面同样给真状态（坑 2 的反向面）；而 `platform/src/controllers/request.ts:186-207` 对**任何** 401 一律清 `ws_token/UUR_INFO` 并整页跳根路径。→ 在停用账号上点"签发密钥"会把管理员踢出登录。T-24 已落两道防线（停用态禁用签发入口 + 提交前重读账号），**后续任何会返回 401 的业务错误都必须先核对这条**（能不上 401 就别上）。
14. **权限引擎有两套档位词表**：FGA 模型的关系是 `owner/manager/editor/viewer`，而 `PermissionLevel` 枚举（`get_permission_level` 的返回、`batch_check` 的入参）是 `owner/can_manage/can_edit/can_read`；代管 API 对外说前者。映射在 `resource_grant_service._LEVEL_TO_RELATION`（实测两套词表同构：给 `editor` 元组，`check(editor)` 与 `check(can_edit)` 同真）。**同一处的第二个坑**：`list_accessible_ids` 对管理员返回 `None`、`get_permission_level` 对管理员恒返回 `owner`——凡"查某个身份能看到什么"的代码，绝不能把管理员当 `login_user` 传进去（列表会恒空、档位会恒为最高）。T-24a 把 `list_grants` 的该参数从签名上删掉，杜绝误传。
15. **授权写入默认是 fail-soft**：`PermissionService.authorize(enforce_fga_success=False)`（默认）在 FGA 写失败时把失败丢进 `FailedTuple` 补偿队列并**静默返回成功**。代管授权是管理员的显式动作，看不到失败就等于骗人——`resource_grant_service` 两处写入一律 `enforce_fga_success=True`（失败仍进队列供重试，但调用方拿得到错误）。新接口若要求"写必须落地"，记得显式打开这个开关。
16. **授权是"设定档位"而不是"叠加"**：FGA 是并集金字塔（`viewer ⊂ editor ⊂ manager`），只写新元组不清旧元组的话，"从 editor 降为 viewer"会留下 editor 元组——账号实际权限高于界面所示。`mutate_grants` 在授予时先撤同资源上其它直接档位（实测：授 editor→降 viewer 后 FGA 只剩 `viewer`）。
17. **怎么认"专用身份"，别按名字认**：`User` 行上**没有任何标记字段**，`svc-` 只是命名约定——管理员建号没有前缀保留，人工真能撞出 `svc-x`；反过来账号的 owner 也可能被显式指给一个真人（换 owner）。判据是两条一起用：**① 反向存在性**（`service_account.resource_owner_user_id == user_id`，软删账号也算）+ **② `user.external_id IS NULL`**（平台创建的 `svc-*` 没有登录身份；人类用户一律有——管理员建号写 `user_name`、注册必填）。少了 ② 会把"被指派为 owner 的真实用户"的日常账号动作一起锁死；只看 ① 之外的任何名字启发式都会误报或漏报。判定要**跨租户**（`bypass_tenant_filter()`）：身份归属哪个租户不改变"它由平台代管"这件事，按租户过滤会让 ROOT 管理员操作子租户身份时漏判。
18. **专用身份的十条写入防线与两个盲区**：用户侧四个入口（改角色/启停/重置密码/自改密码）、旧版用户组三个（换组/设组管理员/批量设成员）、部门三个（加成员/设部门管理员/保存人员编辑）、F003 用户组三个（含"全量同步成员"这个与"加成员"并列的姊妹入口）（`identity_service.assert_not_service_identity[_async]`）；只拦"新增"，移除类动作不受影响。**盲区**：① 已登录用户可用 11821 探测某个 user_id 是不是专用身份（信息量极小，且守卫在权限校验之前触发）；② 若历史数据里身份已进过部门/用户组，组织侧"删除本地人员"等入口仍可达——加成员守卫落地后不再新增这类行。

19. **调用审计能记什么、记不到什么**（T-27 实测口径）：命中点在鉴权依赖的 `yield` 前后——**成功路径拿不到响应体**（`/api/v2` 有 SSE 流，缓冲响应体即破坏流式），所以成功调用按信封惯例记 `200/200`；端点上抛的 118xx 用异常自身的 `HttpStatus/code`；校验错误与非 open-api 异常都是"HTTP 200 + body 业务码"，分别记 422/500（**例外的例外**：端点内 `return resp_500(...)` 这类"HTTP 200 但业务码非 200"的响应，审计只能记成 200——要精确就得读 body，代价是破坏流式，不做）。**WS 会话拿不到 close code**（关闭由 `chat_manager` 执行，依赖看不到），只记 `method=WS` + 会话时长 + `http_status=0`。**凭据校验阶段就失败的调用（11801/11802）不记审计**——没有可归因身份，记了等于给无效密钥扫描留一个灌表的口子；而"过了凭据校验才被拒"的（11805/11807/11816…）**要记**，它们的归属信息由 `_resolve_principal` 边解析边塞进 `audit_ctx` 口袋。
20. **调用面的可见性收口只在"密钥鉴权调用"上生效**（T-29 落定）：AC-23(404/11809 防枚举)原先在调用面**完全没落地**——工作流 invoke/stop/WS 连可见性检查都没有，chat 会话面与 QA 面同样无归属校验，其余端点虽会拒但用的是 legacy 码 + HTTP 200 信封（可枚举）。收口分两半：① **中央映射**——密钥调用在依赖里给 `conn.scope["open_api_call"]` 打标，全局错误处理器把"资源不存在/不可见"的一族 legacy 码（403/404/10400/10962/10970/10971/18000/18010/18040）统一成 11809 + 真 HTTP 404；② **补检查**——工作流三端点、chat 会话面六个、QA 面六个、助手对话面（原先把业务拒绝吞成 500）改走统一口径。**全部以 `get_open_api_principal() is not None` 为闸**：总闸关/无名旧请求的错误口径逐字不变（spec AC-21）。**为什么不是全局改码**：11808(可见但档位不足,403) 是管理面语义，调用面凡"不可见/不存在"一律 11809——两条口径不能混。
21. **`NULL` 与 `[]` 是两种语义，全程用 `is None`**（2026-10-22）：白名单 NULL = 不限制，`[]` = 全拒绝。JSON 列 → Redis payload → principal frozenset → helper → `/assistant/list` 五处都必须保留空列表；任何 `if not allowed_apps` 都会把全拒绝洗成全放行。缓存反序列化写 `payload.get("allowed_apps")`，不能写 `or None`/`or []`。
22. **缓存必须升版且必须带新字段**（2026-10-22）：`oapi:cred:` → `oapi:cred:v2:`；`to_cache_payload`/`from_cache_payload` 必须带 `allowed_apps`。否则第二次请求（缓存命中）会丢失白名单，表现为"第一调 404、第二调 200"的随机安全退化。`token_ciphertext` **绝不进缓存**。
23. **`/assistant/list` 不能在端点后置过滤**（2026-10-22）：`AssistantService.get_assistant` 内部已分页，后置过滤会让 `total` 与页内容不一致；必须把 `allowed_ids` 折进 `AssistantDao.get_all_assistants` 的 `assistant_ids` 预过滤。**且该 DAO 把 `[]` 当"不过滤"**——合并后的集合为空时必须提前返回 `([], 0)`，否则空白名单会列出全部助手。
24. **`assert_open_api_app_visible` 的 `workflow` 硬编码是既有 bug**（2026-10-22 复核）：助手会话的 `message.flow_id` 是助手 UUID，用 `"workflow"` 查 FGA 会误判为不可见。改为 `object_types` 元组，会话面传 `("workflow","assistant")`；助手对话/信息/WS 仍走显式的 `"assistant"` 判定。不要在 helper 里用 try/except 吞 FGA 异常。
25. **PATCH 只校验新增白名单条目**（2026-10-22）：资源授权被撤销后，既有条目会让整条 PATCH 永久 11822；用 `existing` 集合跳过未变更条目。UI 也不要静默丢弃陈旧条目。
26. **回退不要 `downgrade` 掉 `allowed_apps`**（2026-10-22）：删列会把所有 Key 恢复为账号级可见范围（权限放大）；生产回滚只回退代码，保留可空新列。`token_ciphertext` 删列只丢失查看能力。
---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

- **`/api/v2` 鉴权契约**：`Authorization: Bearer bs-sak-<43>`；失败返回真实 HTTP 状态 + 信封（错误码见 §4.2）；身份=服务账号专用用户；裸 `user_id` 被拒（400/11817）。
- **guest 豁免清单（被守卫测试冻结）**：`POST /assistant/chat/completions`、`GET /assistant/info/{id}`、`GET /assistant/list`、`WS /assistant/chat/{id}`、`WS /workflow/chat/{id}`、`POST /chat/gen_title`、`GET /chat/history`、`GET /flows/{flow_id}`——仅当 `enable_guest_access` 开启时匿名可达。
- **管理 API**（`/api/v1`，租户管理员或超管；跨租户 404）：`/service-accounts`（CRUD/启停/授权代管）、`/service-accounts/{id}/keys`（签发/编辑/吊销/全量吊销/**查看明文**）、`/service-accounts/{id}/assignable-agents`（可分配智能体目录）、`/open-api/settings`（租户开关）、`/open-api/scopes`（可签发权限位目录）。
- **自检**：`GET /api/v2/auth/whoami`（需密钥、不需 scope；回显 `allowed_apps`）。
- **审计动作名**（供审计治理与对账）：`open_api.call`、`open_api.service_account.*`、`open_api.api_key.*`（含 `open_api.api_key.reveal`）、`open_api.settings.update`。
- **配置项**：`open_api.enforce_keys`（部署级，默认 false）、`credential_cache_ttl_seconds`（默认 3，上限 5）、`service_account_idle_days`（默认 90，用于列表 idle 提示）。
- **数据表**：§4.2 三张（租户感知；对外无直接契约，但运维侧需知）。

### 6.2 我依赖别人的（Incoming）

| 依赖 | 用途 | 风险点（什么变化会破坏我） |
|---|---|---|
| `PermissionService.check/authorize` | 资源授权与可见性判定 | 签名或主体模型变更（如升级为 `(type,id)` 二元主体）→ owner 装载与授权调用需同步改；二期 FGA 主体改造的落点也在此 |
| 默认操作员装载链路（`open_endpoints/domain/utils.py:26/53/77`） | 开关关时的旧行为；owner 装载的范式 | 上游 3.0 已移除 default operator 配置——若我们将来整段同步上游，**开关关的兼容路径会失效**，须提前决策 |
| 用户服务（自动创建专用用户） | spec AC-02 的实现载体（**新增依赖**） | 用户创建的校验规则（命名/密码策略）、用户列表与席位统计口径、删号流程变化都会影响 `svc-*` 的创建与呈现；实现期须确认复用面与"禁改角色/密码"保护钩子 |
| `AuditLogDao.ainsert_v2` / `ainsert_audit_logs` | 审计写入 | 签名或必填字段变化（现要求显式传 `tenant_id/operator_tenant_id`）；审计页白名单机制若改为"全部动作可见"，`open_api.*` 会被暴露到点审计页（当前有意不进） |
| Redis（`core/cache/redis_manager.py:48/66`） | 凭据正缓存 | 获取方式/序列化变更；**Redis 不可用时必须 fail-closed**（AC-24），不得回落查库失败即放行 |
| i18n 生成链（`packages/locales` + `check-i18n.mjs:97`） | 118 段三语文案 | 生成器/校验规则变化会令新码 CI 失败；`Code: int = N` 的写法要求不变 |
| arch-guard（`scripts/arch-guard.sh:21`） | 分层守卫 | 列表维护方式变化（如改为自动发现）时须确认 `open_api` 仍被覆盖 |
| 测试夹具（`test/conftest.py` 的 `test_client`/`mock_redis`/`mock_openfga` 等） | 测试 | 夹具改名或语义变化会打断新测试；`mock_openfga` 的行为决定授权用例的形态 |
| 商业版网关（独立 Java 仓，是否启用**待确认**） | 部署拓扑 | 网关路由/过滤器变更会改变鉴权与内容安全的先后顺序（与 §5 坑 12 互为对偶）；若网关配置了**响应体日志**，`reveal` 的明文会落网关日志，交付时须确认管理面响应不被记录 |
| `AssistantService.get_assistant` / `AssistantDao.get_all_assistants`（2026-10-22） | `/assistant/list` 白名单过滤 | 服务签名/分页语义变化会破坏 `allowed_ids` 预过滤；`assistant_ids=[]` 的"不过滤"语义是已知陷阱 |
| 纳管智能体注册表 / `ManagedAgentDao`（F078） | `assignable-agents` 名称解析与 `managed_agent` 白名单 | 功能开关关闭时不返回条目；取 id/名称的 DAO 变化需同步 |

---

## 7. 测试与可观测

- **单元**：密钥生成/哈希/比对、`is_valid_at` 边界（过期排他）、scope 隐含规则、owner 防提权、租户设置与运算；**2026-10-22 增量**：密文加解密/摘要复核、`NULL` vs `[]`、缓存命中后白名单不丢失、白名单归一化、`/assistant/list` 空白名单提前返回。落 `src/backend/test/open_api/`。
- **契约（v2 端点）**：每个 scope 的代表端点做 **allow/deny 配对**；错误码精确断言（11801/11807/11811/11815/11817/11822/11823）；WS 握手拒绝单独断言；**白名单 allow/deny 覆盖全部智能体寻址端点（含 `schedule/trigger` 间接调用）**。
- **守卫（静态，四道）**：① 全部 `/api/v2` 路由必须带 marker；② scope 注册表 ↔ 真实路由**双向一致**（whoami 例外显式声明）；③ 开关为开时任意端点无凭据必 401；④ **智能体寻址路由矩阵**：app 相关 scope 下的每条路由必须出现在 `GUARDED` / `FILTER_ONLY` / `NON_AGENT_EXEMPT`，且 handler 源码含白名单 helper。写法照 `test/f040/test_static_scan.py`。
- **e2e**（`/e2e-test` 规范）：签发 → 调用助手/工作流 → 吊销后 401 → 过期后 401 → 越权 403 → 工具指定三用例；**同账号两把不同白名单的 Key 互不越界、reveal 与签发明文一致、旧 Key 400/11823、编辑白名单 ≤5s 生效**；数据前缀 `e2e-openapi-` + 双 cleanup；依赖真 Redis。
- **门禁**：`arch-guard`（DOMAIN_MODULES 补名）、`pnpm check-i18n`（三语 + 错误码对账）、alembic 单 head、前端 lint/typecheck、`pnpm locales:check`。
- **可观测**：调用审计字段见 spec AC-36；服务端日志只记掩码与 credential_id，禁明文；reveal 另记 `open_api.api_key.reveal` 管理审计（不含明文）。

**手动验证一遍（本地可复现）**：

```bash
B=http://localhost:7860   # 后端；平台端管理页 http://localhost:3001
# 1. 管理页「系统 → 服务账号」：建账号（用户管理里应出现对应 svc-* 用户）→ 签发密钥 → 复制明文
# 2. 自检（确认密钥可用）
curl -s -H "Authorization: Bearer bs-sak-<key>" $B/api/v2/auth/whoami        # 200：账号/掩码/权限位/有效期
# 3. 开关关（默认）：旧行为不变
curl -s -o /dev/null -w '%{http_code}\n' $B/api/v2/assistant/list           # 200
# 4. 打开本租户开关后：无密钥必须 401（真 HTTP 状态）
curl -s -o /dev/null -w '%{http_code}\n' $B/api/v2/assistant/list           # 401，body="status_code":11801
curl -s -H "Authorization: Bearer bs-sak-<key>" $B/api/v2/assistant/list    # 200
# 5. 越权与防枚举
curl -s -H "Authorization: Bearer bs-sak-<key>" "$B/api/v2/filelib/file/list?knowledge_id=<未授权库>"   # 404/11809
curl -s -X POST -H "Authorization: Bearer bs-sak-<key>" -H 'Content-Type: application/json' \
  -d '{"model":"<assistant_id>","messages":[],"tools":[{"tool_key":"nope"}]}' \
  $B/api/v2/assistant/chat/completions                                      # 403/11815
# 6. 吊销后 5 秒内重复第 4 步 → 401/11804；查库确认审计
#    select action,target_id,audit_metadata from audit_log where action='open_api.call' order by create_time desc limit 5;
# 7. 免登录面：关闭 enable_guest_access 后访问 /workspace/chat/assistant/<id> → 被拒；开启后恢复
```

（管理员账号为本部署的超管；工具 `tool_key` 取应用已绑定工具的清单。）

---

## 8. 后续改进 / 不打算做的事

**二期候选（按价值排序）**：① 服务账号成为一等授权主体（决策 1 的推翻条件）；② 受控代表调用（`X-On-Behalf-Of` + 范围限定，替代裸 `user_id`）；③ WS 会话内密钥重验（上游 `credential_watcher` 式 3s 轮询、失效即断）；④ 限流/配额（可与平台 QPM/TPM 治理合并）；⑤ PAT/个人密钥与 AI 助手接入；⑥ 免登录面迁移到独立通道后下线 guest 开关。

**不打算做**：工具直调端点、`/api/v3` 改造、国密存储。（2026-10-22 修订：密钥可回显已由决策 12 纳入，采用"独立密文 + 显式审计查看"，不再是排除项。）

---

## 修订历史

| 日期 | 变更 |
|---|---|
| 2026-10-15 | 初稿：基于 spec discovery（`discovery.md`，8 项 ★ 决策）与六路调研/对抗校验（44 端点全量、WS/multipart/中间件边界实机验证、上游口径提取） |
| 2026-10-15 | T-02 落地：§4.3 明确分层与 DAO 位置（DAO 放 `domain/models/`，不设 `repositories/`，对齐 datasource/doc_extract 先例） |
| 2026-10-15 | T-16 落地：新增**决策 10**（强制口径：总闸关=全旧行为；无名请求按默认租户开关；带密钥永远按密钥身份执行）——spec AC-34 在实现期的必要澄清 |
| 2026-10-15 | T-24 落地：§5 新增坑 13（管理面 11805 是 HTTP 401，而 console 把任何 401 当登录失效 → 停用态签发会把管理员踢出登录；已落【停用禁用签发 + 提交前重读账号】两道防线） |
| 2026-10-15 | T-24a 落地：§5 新增坑 14/15/16（两套档位词表 + 管理员短路、授权写入必须可感知、授权是设定档位而非叠加）；`resource_grant_service`/`grants.py` 同批修复 T-22 的两处读取缺陷（列表恒空、撤销恒 404） |
| 2026-10-16 | T-26 落地：§5 新增坑 17/18（专用身份的判据=反查 owner + external_id 为空，且跨租户判定；十条用户/组织侧写入防线与两个已知盲区）；新增错误码 11821（用户侧人工管理专用身份 → 400） |
| 2026-10-16 | T-27 落地：决策 8 补实现参数（队列 1000 / 批 50 / 窗口 2s / 满则丢并计数、关停丢弃）；§5 新增坑 19（调用审计的实测口径：成功按信封记 200/200、WS 无 close code、凭据阶段失败不记、已鉴权被拒要记） |
| 2026-10-16 | T-29 落地：§5 新增坑 20（调用面可见性收口的形状：密钥调用打标 + 中央映射 + 补检查，全部以 principal 为闸）；同批补齐 `INV-23 ③` 的 body 形态 `user_id` 校验（原只查 query/form） |
| 2026-10-22 | **增量修订（用户三项调整）**：决策 5 改为"摘要 + Fernet 密文"；新增决策 11（每 Key 智能体白名单：NULL=不限制、`[]`=全拒绝、只收窄不扩权）、决策 12（明文查看 = 独立密文 + 显式审计动作）；§4.2 增两列、11822/11823、reveal/assignable-agents/whoami 形状；§4.3 增 agent_scope 模块；§5 增坑 21–26（NULL vs `[]`、缓存升版、assistant/list 预过滤、workflow 硬编码、PATCH 校验新增条目、回退放宽权限）；§6 增 reveal/目录端点与依赖风险；§8 移除"密钥可回显" |
