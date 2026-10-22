# F076 开放 API 鉴权 — Spec Discovery 记录

> 调研日期 2026-10-15。本文记录 spec 动笔前的现状调研与关键决策点（★ 已由用户确认的决策以 ✅ 标注）。
> 决策依据进入 spec.md；实现细节在后续 design.md。
> 对应条款：北京农商银行《智能体平台建设项目供应商征集公告》**（四）3**「支持行内各系统经API调用智能体应用和工具；提供 API Key 与调用方鉴权」。
> 前置文档（分支 `local/beijing-ns-notes`）：`openapi-auth-vs-bjrcb-requirement.md`（条款对照 + 移植可行性实测）、`openapi-auth-implementation-plan.md`（实施规划书，tip `db168f811`）。
> 上游参照（只取口径、不移植）：`dataelement/bisheng` 钉 SHA `e058f58eb8d03cf582725d31e194eeeb16fce205`。

---

## 0. 一句话范围

在 `beijing-ns` 交付线**自研同口径**实现：行内系统凭 **API Key**（服务账号密钥）调用 `/api/v2` 的**智能体应用**（助手/工作流，**可指定启用平台工具**），配套密钥生命周期、管理面（三语）与调用审计；**不改 OpenFGA 模型、不引入上游 F048 权限子系统**。

---

## 1. 现状调研结论（可复用 vs 需新建）

### 1.1 `/api/v2` 端点全集：**44 条路由 = 42 HTTP + 2 WS**（三路交叉验证：静态 grep + 运行时 `app.routes` 枚举 + OpenAPI schema；无 open_endpoints 之外的 v2 端点）

| 组 | 条数 | 端点（方法 路径） | 建议 scope | 备注 |
|---|---|---|---|---|
| assistant | 4 | `POST /assistant/chat/completions`、`GET /assistant/info/{id}`、`GET /assistant/list`、`WS /assistant/chat/{id}` | invoke / read / read / invoke | **全组被平台免登录页使用**（见 §4.1）；请求模型 `OpenAIChatCompletionReq`（`api/v1/schemas.py:382`）**已声明 `tools: list[dict]` 占位（:395，当前零消费）** |
| workflow | 3 | `POST /workflow/invoke`、`POST /workflow/stop`、`WS /workflow/chat/{id}` | invoke / invoke / invoke | WS 被免登录页使用；invoke/stop 是平台「API 访问」面板对外示例 |
| flows | 1 | `GET /flows/{flow_id}` | workflow:read | 被免登录页使用（已有 guest 开关） |
| chat | 6 | `POST /chat/gen_title`、`GET /chat/history`、`POST /chat/liked`、`POST /chat/solved`、`POST /chat/comment`、`POST /chat/sync/messages` | chat:invoke | 前 2 个被免登录页使用；后 4 个**仓内无调用方**（疑似外部/遗留）；`sync/messages` 接受裸 `user_id`（§4.5） |
| filelib | 19 | 建/改/列/删知识库、`clear`、文件上传（**multipart**）、删文件、`delete_file`、`file/list`、`chunks`（**multipart**）、`chunks_string`、`download_statistic`（文件流）、`add_qa`、`add_relative_qa`、`update_qa`、删 QA、`detail_qa`、`retrieve`、`query_qa` | 读 6 / 写 13（逐条钉死，见 §4.2） | 最大端点族；`list_user_metadata` 实为读语义但写作 POST（§4.2 待钉）；`download_statistic` 可读服务器 `.log`（§4.6） |
| knowledge | 8 | 元数据字段 增/改/删/查、文件级用户元数据 增/改/删/列 | 读 2 / 写 6 | — |
| llm | 2 | `POST /llm/workbench/asr`（**multipart**）、`POST /llm/workbench/tts` | assistant:invoke | — |
| citation | 1 | `GET /citation/{citation_id}` | knowledge:read | 自述"for open API consumers" |

### 1.2 鉴权与身份现状（问题面）

- 路由挂载零鉴权：`src/backend/bisheng/api/router.py:105` `router_rpc = APIRouter(prefix='/api/v2', )` 无 `dependencies`；身份全部由端点函数体自造（`open_endpoints/domain/utils.py:26/53` 的 default operator）。
- **中间件会把"密钥假装成 JWT"**：`Authorization: Bearer bs-sak-…` 被 `_extract_http_access_token` 原样取出 → JWT 解码失败被静默吞 → `_set_tenant_context` 仍按 truthy token 执行 **`set_current_tenant_id(DEFAULT_TENANT_ID=1)`**；token_version/停用/visible_tenants 全部跳过（`utils/http_middleware.py:60-121、230-284`）。→ **鉴权依赖必须显式 `set_current_tenant_id(owner 租户)`（并建议 `set_visible_tenant_ids({1, owner 租户})`），否则子租户请求落在 ROOT 租户**。
- WS 中间件只读 cookie（`:394-408`），WS 上无任何租户 seed 与校验。
- 租户停用检查用的是解析出的租户（此时=1），**密钥所属租户的停用必须由鉴权依赖自查**。
- 全局异常处理把 HTTP 状态拍平：`handle_http_exception`（`main.py:29-35`）返回 `JSONResponse(content=msg)` → **HTTP 200 + body 业务码**（平台前端按此读）。**外部调用方需要真状态码 → 必须为 /api/v2 另设处理**（★ D6 已定）。

### 1.3 可直接复用（关键项）

| 设施 | 位置 | 用途 |
|---|---|---|
| 非 JWT 调用者 → 身份装载 | `open_endpoints/domain/utils.py:26-104`（含 `resolve_operator`） | 鉴权通过后装载 owner（含 `set_current_tenant_id`） |
| 凭据表形状 + 跨租户查 token 范式 | `share_link/domain/models/share_link.py:46-80`、`share_link/domain/services/share_link_service.py:32-60` | 新表字段形状 + `bypass_tenant_filter()` 先例 |
| 密钥脱敏纪律 | `datasource/domain/services/credentials.py`（`MASK`/`redact`/`to_safe_dict`/敏感名清单） | 密钥读路径 deny-by-default |
| 审计写入 | `AuditLogDao.ainsert_v2`（`database/models/audit_log.py:375-442`）+ 批量 `ainsert_audit_logs`（:325-335） | 逐调用/管理动作审计；结构化列已齐 |
| 多租户工具 | `bypass_tenant_filter()`（`core/context/tenant.py:99-131`）、`tenant_filter` 监听登记（`tenant_filter.py:39-123`） | 匿名查密钥；新模型须登记 |
| 真 HTTP 状态 + 信封先例 | `http_middleware.py:249-256/336-351`（401+19103、403+20004） | D6 的实现参照 |
| 配置节装配范式 | `core/config/openfga.py` + `settings.py:843` | 新增 `core/config/open_api.py` |
| Redis / 高熵 token / 迁移 helpers / 测试夹具 / 静态守卫范式 | `core/cache/redis_manager.py:48/66`、`common/utils/util.py:28`、`alembic_helpers/online.py:13/29`、`test/conftest.py`、`test/f040/test_static_scan.py` | 见规划书 §2 |

### 1.4 需新建（全空）

`bisheng/open_api` 模块（api/domain 分层 + 依赖 + marker + scope 注册表）、三张表（`service_account` / `api_credential` / `open_api_tenant_setting`）、`common/errcode/open_api.py`（118 段）、`core/config/open_api.py`、管理面 API + platform「系统→服务账号」页（三语）、三道 `/api/v2` 守卫测试、e2e、对外文档改写。**arch-guard `DOMAIN_MODULES`（`scripts/arch-guard.sh:21`）须补 `open_api`**。

---

## 2. 关键决策点（★ 全部已确认 ✅）

| # | 决策 | 结论 ✅ | 确认于 |
|---|---|---|---|
| D1 | 特性编号 | **076**（main/beijing-ns/guangxi-ns/wecom-ns 四线核验空闲） | 2026-10-15 |
| D2 | 工具口径 | **进一期**：助手/工作流请求体加 `tools[]`（白名单=该应用已绑定且启用的工具；不传=应用默认）；**不做工具直调端点** | 2026-10-15 |
| D3 | 服务账号 owner | **创建账号时同事务自动建**专用最小权限用户 `svc-*`（不可登录；创建+运行时双重防提权；删除时软停用） | 2026-10-15 |
| D4 | 租户级策略表 | **一期做**，随内核 W1 交付 | 2026-10-15 |
| D5 | **平台免登录页冲突** | guest 页依赖的 8 个端点**统一由 `enable_guest_access` 开关门控（默认关）**：关=与其余 v2 端点一样要密钥；开=匿名放行供平台免登录页 | 2026-10-15 |
| D6 | **HTTP 状态口径** | 鉴权/权限类失败返回**真实 HTTP 状态**（401/403/404/503）+ 信封 body（118xx）；为 /api/v2 增设专用异常处理 | 2026-10-15 |
| D7 | **裸 `user_id` 代用户** | 强制开启后**拒绝**（400 / 新错误码 **11817**）；迁移指引指向二期受控代表调用 | 2026-10-15 |
| D8 | **开关默认值与兼容期语义** | 部署级默认 **false**；**关 = 完全旧行为**（忽略 Authorization，连无效密钥也不拦）；**开 = 必须有效密钥、无回落**；W5 按清单切换，回退秒级 | 2026-10-15 |

> D5/D7 是本轮调研新发现、规划书未覆盖的两个冲突，已当场裁定；D6/D8 消除了规划书 §2.1 与 §2.10.3 的自相矛盾。

---

## 3. 已定（推荐口径，未获用户异议即入 spec）

- **密钥生命周期**：`expires_at` 可空（默认不过期，签发表单给 90/180/365 预设）、不设上限；**同账号多把并存**（轮换：先签新→观察 `last_used_at`→再吊销旧）；有效性单一判据"未撤销且未过期"（排他）；吊销/停用 ≤ 缓存 TTL（3s，上限 5s）生效；`last_used_at` best-effort 写（可 60s 合并）。
- **密钥格式与存储**：`bs-sak-` + `secrets.token_urlsafe(32)`（43 字符）；**只存 SHA-256 摘要**（唯一）；明文只在签发响应出现一次；此后任何接口/界面只回 mask（`bs-sak-********` + last4）。
  > **2026-10-22 增量修订**：用户要求后台可查看明文。现行口径见 spec AC-08/09/44 与 design 决策 5/12——校验仍用摘要，另存独立 Fernet 密文，管理员经显式 reveal 动作查看并写审计；存量密钥无密文不可查看。本条保留为当时的调研结论。
- **审计**：逐调用 `action=open_api.call`（credential/账号/scope/HTTP 状态/业务码/耗时/IP，best-effort 有界队列，丢日志不阻塞请求）+ 管理动作 `open_api.*`；**都不进平台点审计页白名单**（只落库取证，对齐上游处置）；保留期沿用平台现状，对客口径建议 ≥6 个月。
- **限流/配额/IP 白名单/幂等**：**不做**（对齐上游 F053 把 R8/P2 整档移出；不预埋字段）。
- **国密**：一期不单独实现（传输由行内 TLS/网关承载；库里只有不可逆摘要）；公告（十）5 归平台级合规条款。
- **owner 用户可见性**：在用户管理列表**可见**并标「服务账号专用」，禁止人工改角色/密码（后端保护），删除账号时软停用；席位口径待商务确认。
- **`delegate` 权限位**：保留在可签发目录并 UI 标注「预留」（签发不产生任何生效行为），二期 C2 启用。
- **管理页可见性**：默认可见（超管/租户管理员），env 开关可关。
- **提供 `GET /api/v2/auth/whoami`**（回显密钥自描述信息，免 scope 或最小 scope）——守卫测试的"whoami 例外"才有依据，也为对接方联调自检。
- **租户级开关的管理路径**：补 `GET/PUT /api/v1/open-api/settings`（或挂在服务账号前缀下）+ 管理页开关 + 审计动作 `open_api.settings.update`；权限 = 全局超管或当前租户管理员。
- **WS 密钥携带**：握手 **Authorization 头**（仅服务端对接方适用；浏览器场景由 D5 的 guest 开关承接）；**一期不主动中断已建立的会话**（吊销后"当前执行中的继续跑完，后续调用按新状态"），对客文档明示；上游式会话内 3s 重校验列为二期可选。
- **密钥不进 URL**：禁止 `?api_key=`/`?t=` 方案（仓内三处全量 URL 日志会落密钥：`http_middleware.py:306/356`、`main.py:34/40`、uvicorn WS 日志）。

---

## 4. spec 必须落死的技术边界（本轮实测结论）

### 4.1 guest 豁免面 = 8 个端点（D5 的开关覆盖清单，守卫测试冻结）
`POST /api/v2/assistant/chat/completions`、`GET /assistant/info/{id}`、`GET /assistant/list`、`WS /assistant/chat/{id}`、`WS /workflow/chat/{id}`、`POST /chat/gen_title`、`GET /chat/history`、`GET /flows/{flow_id}`。
证据：`client/.../StandaloneChatPage.tsx:82`（`mode==='guest'?'v2':'v1'`）、`useChatHelpers.ts:114-116`、`api-endpoints.ts:51`、`apps.ts:39/64/135`。**注意 `enable_guest_access` 现状只门控 3 个端点（assistant info/list、flows），需补齐其余 5 个**；且**新增端点默认强制**（关=要密钥），开关关闭时这 8 个端点同样要求密钥。

### 4.2 scope ↔ 端点矩阵逐条硬编码（上游 `scopes.py` 同款，**不得用通配**）
44 条逐条登记（含 2 条 WS 以 `WS` 方法串登记）；`POST /knowledge/file/list_user_metadata` 判为读语义 → 归 `knowledge:read`（spec 里注明）；三道守卫测试：① 所有 v2 路由必须带 marker（漏标 → 500/11811）；② 注册表↔真实路由**双向一致**；③ 开关为开时任意端点无凭据必 401。

### 4.3 失败语义映射（真 HTTP 状态，D6）
401（缺失/非法 11801、无效 11802、过期 11803、吊销 11804、账号停用 11805）/ 403（租户停用 11806、缺权限位 11807、资源未授权 11808、owner 被提权 11816、工具越权 11815）/ 404（防枚举 11809）/ 400（owner 校验 11810、`user_id` 拒绝 11817、重名 11813）/ 503（依赖不可用 11812/11814）/ 500（端点漏标 11811）。**流式（SSE）**：响应头发出前失败用 HTTP 状态；发出后失败走流内终态并记业务码。**WS**：依赖里 `raise WebSocketException(1008)`（预 accept → 客户端见握手 403）；**严禁在 WS 依赖里 raise HTTPException/BaseErrorCode**——本仓 handler 访问 `req.method` 会 AttributeError → 500 + traceback（已实机复现）。

### 4.4 multipart 的已知限制
FastAPI 先解析 body 再跑依赖（`routing.py:322-331` → `:378`）→ **未鉴权的大文件上传仍会被完整接收并落临时盘后才 401**（实测 1MB）。spec 记为已知限制（不阻塞一期）；审计/中间件**不得读 body**（BaseHTTPMiddleware 读 body 会整包缓冲）。

### 4.5 裸 `user_id` 的真实消费点（D7 拒绝面）
`filelib.py:173`（列表）、`:378`（file/list）、`:510`（add_qa，直写归属）、`:534`（add_relative_qa）、`:637`（retrieve）；`chat.py:87`（sync/messages 直写消息）。另有 `assistant.py:332-338` 的 `user_id` 只进日志（静默忽略）——spec 一并裁定。

### 4.6 其他必须写入 spec 的事实
- `download_statistic`（`filelib.py:493-504`）按 `file_path` 读服务器 `.log`（护栏：后缀 + 路径前缀）→ 归 `knowledge:read` 时注明"凭据可读解析日志"的安全语义。
- 现状文档 `docs/api/filelib-retrieve.md` §2 推荐**把 default operator 配成 super_admin**；F076 后 owner 必须非管理员 → **存量"靠超管短路"的调用行为会变**，切换指引须单列（否则现场会误归因）。
- 商业版网关拓扑（北京农商行是否启用**待确认**）：网关 WS 代理清单不含 `/api/v2/assistant/chat/**` 与 `/api/v2/workflow/chat/**`；且内容安全/限流等副作用发生在后端密钥鉴权**之前**（`docs/architecture/11-gateway.md:242/300/426`）。

---

## 5. 明确不做（本期范围外）

工具直调端点（D2）、`X-On-Behalf-Of`/`X-End-User` 身份传递、任务模式/日常模式开放 API、个人密钥 `bs-pat-`/AI 助手接入、`/api/v3` 免登录改造、服务账号作为 FGA 一等主体（二期可选）、上游 F048 移植、限流/配额/白名单/幂等（§3）、国密化（§3）。

---

## 6. 进入 spec 前的遗留待办

1. **release-contract 五处登记**（表 1 三对象 / 表 2 两条不变量 / 表 3 依赖+开发基线 / 模块编码 118 段 / 变更历史）——spec 定稿时一并落。
2. **错误码号位表更新**：11801–11816（规划书 §2.9）+ **11817 `OpenApiIdentityInputRejectedError`**（D7 新增）。
3. **对客口径澄清（建议商务/技术窗口书面确认）**：「和工具」按"调用时可指定启用工具"实现；若行方坚持"工具直调"，属新变更项。
4. **待确认项**：北京农商行是否启用商业版网关；服务账号是否计入用户数/席位口径。
