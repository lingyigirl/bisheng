# F078 智能体纳管 — Spec Discovery 记录

> 调研日期 2026-10-19。本文记录 spec 动笔前的现状调研与关键决策点（★ 待用户确认；确认后以 ✅ 标注）。
> 决策依据进入 spec.md；实现细节在后续 design.md。
> 对应条款：北京农商银行《智能体平台建设项目供应商征集公告》**（五）3**「智能体纳管：统一纳管本平台、其他低代码平台或高码开发的第三方智能体，实现统一接入、调度与运营；支持按场景/行业/标签分类展示。」
> 特性编号 **078**（`features/v2.7.0/` 现最大 077；2026-10-19 核验 078 空闲；目录 `078-managed-agents`）。
> 开发分支：`feat/2.7.0/beijing-ns-078-wip`（2026-10-19 自 `feat/2.7.0/beijing-ns-077-wip` HEAD `438e8b0d4` 切出；F077 调度接入是本特性的依赖）。
> 前置证据：`agents_logs/architecture/2026-10-16/2026-10-16_北京农商行征集公告_条款清单与覆盖台账.md` 行 49；`features/v2.7.0/077-scheduled-triggers/`（调度基座）；`features/v2.7.0/076-openapi-auth/`（服务账号身份）；`docs/constitution.md`（C1–C7）；`features/v2.7.0/release-contract.md`。
> 上游参照（只取口径、不移植）：`dataelement/bisheng` 钉 SHA `ca4ca8bf0e30b2b456d2f49eaea9dbe35f5ac4a5`（3.0.0-beta4）。上游**无** agent registry / Agent Card / A2A / 第三方智能体对象；F108 调度子系统只有 PRD 且 defer。
> **本文的产生方式（可复核）**：3 路并行只读探查（当前分支注册与权限 / 当前分支调度与分类 / 上游全仓）+ 主会话逐点验真（`FlowType`、F077 app_type、Tag、plaza、FGA 模型、错误码 121 三来源扫描）合成。所有“缺失”均为**未找到功能级实现**，不是同名模块推断。

---

## 0. 一句话范围

在 `beijing-ns` 交付线**新建一个异构智能体控制面**：用 `managed_agent` 注册表统一登记本平台应用（投影）与第三方智能体（低代码/高码），通过适配器把不同协议归一为统一调用契约，复用 F076 服务账号身份与 F077 调度基座实现权限化调用、定时/事件调度、健康检查与基础运营；用场景/行业/标签分类并在平台纳管控制台与客户端目录展示。**不把第三方智能体写入 `Flow` 表**，**不移植上游**（无可移植实现），**不新建工作流引擎**。

---

## 1. 现状调研结论（可复用 vs 需新建）

### 1.1 本平台应用：管理面完整，但类型封闭

**可复用**
- 应用类型与生命周期：`FlowType` = ASSISTANT(5) / WORKFLOW(10) / WORKSTATION(15) / LINSIGHT(20) / CHANNEL_ARTICLE(25) / KNOLEDGE_SPACE(30)（`src/backend/bisheng/database/models/flow.py:33-39`）；上下线 `api/v1/workflow.py:360-365`、`api/services/assistant.py:664-709`。
- 权限：`PermissionService.check/authorize`（`permission/domain/services/permission_service.py:82,291`）、应用权限位模板（`permission/domain/application_permission_template.py:15-47`）、FGA 资源类型（`core/openfga/authorization_model.py:288-310`）。
- 目录：客户端应用中心 `/apps`、智能体广场 `/apps/explore`、平台构建 → 应用 `/build/apps`；广场服务 `workstation/domain/services/plaza_service.py:14,91-153`（硬过滤 5/10）。
- 标签：`Tag`/`TagLink`（`database/models/tag.py:16-72`），应用列表与广场按标签筛选（`api/services/workflow.py:58,185`；`plaza_service.py:123-133`）。
- 审计/监控/评测/配额：`database/models/audit_log.py:40-42,227-248`；`telemetry_search/`；`evaluation/domain/models/evaluation.py:11-14`；`role/domain/services/quota_service.py:28-38`。

**需新建/改造**
- `FlowType` 是封闭枚举，**不新增外部类型**；外部智能体不能成为 `Flow` 行。
- `Flow`/`Assistant` 无 source/origin/endpoint 字段（`flow.py:52-73`；`assistant.py:20-52`）。
- 应用创建只支持 assistant/workflow（`frontend/platform/src/pages/BuildPage/CreateApp.tsx:37,157,191`；`api/v1/schemas.py:255-258`）。
- 无“纳管本平台应用”的注册/分类入口；原生应用无法按场景/行业分类。

### 1.2 第三方智能体：两侧均无

**当前分支**
- 无 agent registry / Agent Card / A2A / 第三方智能体模型；`rg` 与目录扫描均未找到功能级实现。
- `/api/v2/workflow/invoke` 强校验原生 `flow_type == WORKFLOW`（`open_endpoints/api/endpoints/workflow.py:39-61`）；`/api/v2/assistant/chat/completions` 按原生 assistant UUID 加载（`assistant.py:37-61`）。
- 无任意 endpoint 代理；F076 的 `/api/v2` 是“外部调用本平台”，方向相反。
- 工作流导入/导出是浏览器端图 JSON（`frontend/platform/src/util/flowUtils.ts:336-361`），导入后在本平台执行，不是外部代理；后端无应用导入导出 API（`api/v1/flows.py:22-58`）。
- MCP（`mcp_manage/`）是工具客户端；OpenAPI 工具（`tool/`）是工具级接入；外部 OpenAI-compatible 模型是模型接入。三者都不是智能体纳管。

**上游 `ca4ca8bf0`**
- `FlowType` 与当前分支相同（`ca4ca8bf0:src/backend/bisheng/database/models/flow.py:33-39`）。
- 全仓无 agent registry / Agent Card / A2A / 第三方/外部智能体功能级实现。
- `public_endpoints/` 是原生 assistant/workflow 的对外发布面；`dsh_market` 是桌面插件市场；`mcp_manage` 是 MCP 工具；应用广场是原生应用中心。
- F108 调度子系统：PRD-only、defer，服务原生“应用管家”（`docs/PRD/3.0-release/3.0 应用工场 产品方案.md:362,385`）。
- 3.0-beta2 skill pack + PAT 是**外部智能体调用 BiSheng**（反向），明确拒绝逐平台适配与 OAuth 授权服务器（PRD D18）。
- **结论：无可 cherry-pick 的实现。**

### 1.3 统一调用 / 调度 / 运营：只覆盖原生 5/10

- 统一鉴权面：`/api/v2` + `verify_open_api_access`（`api/router.py:133`）；scope 注册表 9 项（`open_api/domain/scopes.py`）。
- 调用：仅 assistant/workflow 两个端点，无通用 agent 调度器。
- 调度（F077）：`scheduled_task.app_type` 仅 5/10（`scheduled_task/domain/schemas/task.py:25`；`trigger_guard.py:39-42`；`run_service.py:373-380`）。
- 运营：审计/评测/配额/监控均按原生类型；第三方智能体无对象、无指标、无配额。
- 服务账号授权代管仅 4 类资源：`knowledge_library/knowledge_space/assistant/workflow`（`open_api/domain/services/resource_grant_service.py:34-38`）。

### 1.4 分类与展示：标签有，场景/行业无

- 标签：`TagBusinessTypeEnum` 有 `application`（`tag.py:16-19`）；`TagLink.resource_type` 用 `ResourceTypeEnum`（`database/models/group_resource.py:14-22`，现 1/3/4/5/6/7/8/9）。
- 场景/行业：`Flow`/`Assistant` 无字段，列表 API/UI 无筛选；全仓应用级 `scenario`/`industry` 未找到。
- 目录：应用中心/广场/平台应用列表均只显示 5/10（`plaza_service.py:14`；`workstation/api/endpoints/apps.py:103`）。
- 现有“分类”文案是标签页或应用类型（“未分类”=无标签），不是业务分类模型。

### 1.5 可复用零件（不重复造）

| 零件 | 复用方式 |
|---|---|
| F076 服务账号/专用身份/密钥/授权代管 | 运行与调用身份；新增 `managed_agent` 授权类型 |
| F077 调度基座 | 复用 beat 扫描、租约/互斥、失败计数、通知、审计；新增 app_type 常量与派发分支 |
| 应用权限模板 | 复用 `view_app/use_app/edit_app/...` 语义，新建 `managed_agent` FGA 类型 |
| `Tag`/`TagLink` | 增加 `managed_agent` 业务类型与资源类型，复用标签筛选 |
| 应用广场/应用中心 | 新增“第三方智能体”页签与卡片，复用筛选/收藏交互 |
| Fernet 加解密 | `core/config/settings.py:26-30` 的 `encrypt_token/decrypt_token` |
| httpx | 已在依赖（`pyproject.toml:146`，0.28.x），外部适配器直接使用，不新增重型依赖 |
| 审计表/错误码/i18n 链 | 复用 `audit_log`、`common/errcode`、`packages/locales` 生成链 |

---

## 2. 关键决策点（★ 待用户确认）

### ★ D1 纳管对象范围 — ✅ 已确认 2026-10-19：B
- A. 只纳管第三方；B. 本平台 + 第三方；C. 只纳管本平台。
- **推荐 B**：条款明确“本平台、其他低代码平台或高码开发的第三方智能体”；本平台应用通过注册表投影进入统一目录。

### ★ D2 第三方接入协议（MVP）— ✅ 已确认 2026-10-19：B
- A. 仅自定义 HTTP；B. OpenAI-compatible + 自定义 HTTP；C. 再加 A2A。
- **推荐 B**：覆盖大多数低代码/高码智能体；A2A 放 Phase 2（除非投标明确要求 A2A）。

### ★ D3 本平台应用如何进入注册表 — ✅ 已确认 2026-10-19：A
- A. 注册表投影（`native_app_id` + `native_app_type`，不改 `Flow`/`Assistant` 表）；B. 给 `Flow`/`Assistant` 加场景/行业字段；C. 不纳管本平台。
- **推荐 A**：避免改核心表与现有应用链路；分类元数据统一放注册表。

### ★ D4 统一调用入口
- A. 新增 `POST /api/v2/managed-agent/invoke` + scope `managed_agent:invoke`；B. 复用现有 `/api/v2` 端点；C. 不开放外部调用。
- **推荐 A**：统一入参/出参、独立 scope、便于频控与审计；不改变既有端点。

### ★ D5 客户端展示形态
- A. Phase 1 独立“第三方智能体”页签，Phase 2 合并统一广场；B. 一开始就合并进现有广场；C. 只在平台控制台展示。
- **推荐 A**：降低对现有广场链路的侵入；独立页签已满足“分类展示”。

### ★ D6 调度是否纳入本特性 — ✅ 已确认 2026-10-19：A
- A. 纳入（Phase 1.5，复用 F077）；B. 本特性只做接入/展示，调度另立特性。
- **推荐 A**：条款含“调度与运营”；不做则只能称“接入 + 展示”。

### ★ D7 运营范围（MVP）
- A. 健康检查 + 调用留痕/统计 + 失败通知；B. 再加评测/成本；C. 只做审计。
- **推荐 A**：足以支撑“运营”表述；评测/成本后置。

### ★ D8 凭据存储
- A. 复用 Fernet 加密列；B. 接外部密钥服务；C. 不存凭据（仅匿名调用）。
- **推荐 A**：与既有数据源/服务商密钥口径一致，不新增依赖。

### ★ D9 权限模型
- A. 新 FGA 类型 `managed_agent` + 复用应用权限位语义；B. 复用 `workflow` 类型 + ID 前缀；C. 仅角色表。
- **推荐 A**：类型隔离，避免与原生 workflow 混淆；符合 C4 单入口。

### ★ D10 场景/行业分类法
- A. 新增 `managed_agent_taxonomy`（场景/行业两 kind）+ 复用 `Tag`；B. 只用自由标签；C. 硬编码枚举。
- **推荐 A**：可租户自定义、可三语展示、可按 code 过滤；满足“场景/行业/标签”三类。

### ★ D11 错误码
- A. 121 段（12101–12114）；B. 复用 119/118。
- **推荐 A**：121 经三来源扫描为空（backend `common/errcode`、release-contract、locales）；不污染调度/开放 API 语义。

### ★ D12 平台管理落点
- A. `BuildPage` 下“智能体纳管”（与应用并列）；B. `SystemPage` 下；C. 独立顶级菜单。
- **推荐 A**：纳管是应用生命周期的一部分；按权限位与开关门控。

### ★ D13 外部智能体鉴权方式
- A. none / bearer / api_key 三种；B. 仅 bearer；C. 支持 OAuth2 client credentials。
- **推荐 A**：覆盖常见形态；OAuth2 后置，避免本期引入授权服务器。

### ★ D14 本平台应用是否必须显式纳管
- A. 必须显式“纳管本平台应用”后才进入统一目录/分类；B. 自动投影全部原生应用；C. 不投影。
- **推荐 A**：避免一次性把存量应用全部暴露到新目录；由管理员选择纳管范围。

### ★ D15 调用流式
- A. MVP 非流式 JSON，流式 Phase 2；B. MVP 即支持 SSE。
- **推荐 A**：条款未要求流式；先稳定同步契约与错误映射。

> **★ 确认记录（2026-10-19，用户）**：**D1 = B**（本平台 + 第三方）、**D2 = B**（OpenAI-compatible + 自定义 HTTP）、**D3 = A**（注册表投影）、**D6 = A**（调度纳入 F078）四项已确认 ✅；其余 D4 / D5 / D7–D15 经用户确认**按推荐口径入 spec** ✅。

---

## 3. 推荐口径（2026-10-19 用户确认按此入 spec ✅）

1. **对象边界**：`managed_agent` 是独立领域对象，不写入 `Flow`；原生应用只以 `native_app_id` 投影。
2. **来源/协议**：`source_kind = native | low_code | high_code`；`protocol = native | openai_compatible | custom_http`。
3. **状态机**：`draft → online → offline → disabled`；只有 `online` 可被调用/调度；删除前必须无启用中的定时任务。
4. **调用契约**：同步 JSON `{agent_id, input, session_id?}` → `{run_id, output, usage?}`；错误码映射 + 上游状态码写入 metadata。
5. **身份**：外部调用走 F076 密钥 + `managed_agent:invoke`；服务账号专用身份需对 agent 有 `use_app`；定时运行沿用 F077 锁定身份。
6. **租户**：注册表、分类法、运行记录全部租户隔离；跨租户不可见；beat 显式 `bypass_tenant_filter()` + `set_current_tenant_id()`。
7. **安全**：出站 URL 仅 http/https，DNS 解析后拒绝私网/链路本地/元数据地址；allowlist；禁重定向；超时/响应体上限；凭据加密、不回显、不落日志。
8. **分类**：场景/行业用 taxonomy code，标签复用 `Tag`；客户端筛选按 code/tag；名称按三语展示。
9. **审计**：管理动作进审计页；调用动作只进 DB（类比 `open_api.call`）。
10. **开关**：`managed_agent.enabled` 默认 false；关闭时路由 404/菜单隐藏，既有部署零影响。

---

## 4. spec 必须落死的技术边界（本轮实测）

- **FGA 模型变更**：`core/openfga/authorization_model.py` 新增 `_standard_resource_type('managed_agent')`；合并 llm-guardrail 后 `MODEL_VERSION=v2.0.5` / 19 类型（`managed_agent` + `linsight_skill`）。部署需按既有模型写入流程（一次性脚本写 store，`MODEL_VERSION` 本身不触发运行期 reload）。`permission_service.py` 的 `_IMPLICIT_SCOPE_RESOURCE_TYPES`、`_TENANT_GATED_RESOURCE_TYPES` 与 owner/existence 分支必须同步，否则列表可见但 check 失败。
- **不新增 `FlowType`**：F077 `scheduled_task.app_type` 是 int 列，用 `MANAGED_AGENT_APP_TYPE = 100` 常量；所有 `app_type` 分支必须显式处理 100，不能落入 assistant 兜底。
- **C2 双库**：新表用 `dialect_helpers.JsonType/LargeText/UPDATE_TIME_SERVER_DEFAULT`；分类过滤走关系列；禁止 `RETURNING` 与 JSON 函数。
- **C3 租户**：新表 `tenant_id default=None` 自动填充；批量 update/delete 手写 `tenant_id`；beat 跨租户显式处理。
- **C4 权限**：只走 `PermissionService.check/authorize`；不直读 `role_access`；F076 授权代管增加 `managed_agent` 类型。
- **错误码 121**：12101–12114；类必须写 `Code: int = N`；三语文案同 PR 进 `packages/locales/src/api_errors/`。
- **Scope 9→10**：`open_api/domain/scopes.py` 增 `managed_agent:invoke`；同步 `serviceAccount.json` 三语、`accessGuide.md`、`docs/api/open-api-auth.md`、`docs/开放API条款验收表.md` K 行、`test_scopes.py`；锁步测试自动兜底。
- **新表建表**：SQLModel `table=True` + 启动 `create_all(checkfirst=True)`；无需 Alembic 修订（除非同版本迁移依赖）；回滚 `DROP TABLE`。
- **前端**：平台 `Vite5 + Zustand + react-query v3 + bs-ui + @/`；客户端 `Vite6 + Recoil + @tanstack + shadcn + ~/`；不混用；不新增 UI/状态库；单文件 ≤600 行；三语同 PR；无 `backdrop-filter`。
- **依赖**：httpx 已在依赖；不新增重型组件；如确需新增，按 `.claude/rules/version-control.md` 说明场景并锁版本。
- **不得假借**：MCP 工具、OpenAPI 工具、外部模型 endpoint、应用导入导出、F108 PRD 都不能算作本条款实现。

---

## 5. 明确不做（本期范围外）

- A2A Agent Card 自动发现与任务生命周期、流式/SSE（Phase 2）。
- 具名低代码平台连接器（Coze/豆包/钉钉等）——MVP 只做通用 HTTP/OpenAI-compatible。
- 应用市场/交易/计费；评测集成；故障转移；自动扩缩容。
- 把第三方智能体写入 `Flow` 表；扩展 `FlowType`；改造既有工作流引擎。
- 复用 `dsh_market` 作为纳管入口；把 MCP 工具注册表当 agent registry。
- 代表用户调用（须先重新评审 INV-23）；OAuth2 授权服务器。
- 本平台存量应用自动全部投影（D14 推荐显式纳管）。

---

## 6. 进入 spec 前的遗留待办

1. **★ D1–D15 逐条确认**（D1/D2/D3/D6/D9/D10/D14 影响最大）。
2. 复核错误码 121 三来源仍为空（`common/errcode/*.py` / 全部 `release-contract.md` / `packages/locales/src/api_errors/*.json`）。
3. 复核 FGA 模型写入流程与 `MODEL_VERSION` bump 的部署步骤（现有部署是否 `force_write_model`）。
4. 确认 `managed_agent` 在 FGA 模型中的关系模板是否直接复用 `_standard_resource_type`（owner/manager/editor/viewer/can_delete）。
5. 确认客户端“第三方智能体”页签的入口与权限门控（`view_app` / 新权限位）。
6. 确认原生投影的纳管范围与存量迁移策略（D14）。
7. 确认外部 mock agent 的验收协议（OpenAI-compatible `/chat/completions` + custom HTTP `POST`）。
8. 确认调度接入的 `app_type=100` 与 F077 既有失败计数/通知口径完全一致。

---

## 附录 A：调研证据与已排除的易混淆项

| 易混淆项 | 为什么不是纳管 |
|---|---|
| `FlowType.WORKSTATION/LINSIGHT/CHANNEL_ARTICLE/KNOLEDGE_SPACE` | 平台内部应用类型，不是第三方智能体 |
| 工作流导入/导出（`flowUtils.ts`） | 导入后在本平台执行，不是代理外部智能体 |
| MCP（`mcp_manage/`） | MCP 工具客户端/配置，无 agent 对象、身份、上下线、调度 |
| OpenAPI/自定义 HTTP 工具（`tool/`） | 工具级接入，可被工作流调用，但不是被纳管的智能体 |
| 外部 OpenAI-compatible 模型 | 模型接入，不是智能体接入 |
| F076 服务账号/接入指南 | 外部系统调用本平台，方向相反 |
| 应用广场/应用中心/`dsh_market` | 原生应用目录 / 桌面插件市场，不是第三方智能体目录 |
| F108 调度子系统 | PRD-only、defer，服务原生应用；上游无实现 |
| 3.0-beta2 skill pack + PAT | 外部智能体消费 BiSheng，反向；明确不做逐平台适配 |
| `tag`/`group` | 通用标签/用户组；`group` 是权限分组，不是场景/行业分类 |

**当前分支证据（代表性）**：`database/models/flow.py:33-39,52-73`；`database/models/assistant.py:20-52`；`scheduled_task/domain/schemas/task.py:25`；`scheduled_task/domain/services/trigger_guard.py:39-42`；`workstation/domain/services/plaza_service.py:14,91-153`；`database/models/tag.py:16-72`；`open_api/domain/scopes.py`；`open_api/domain/services/resource_grant_service.py:34-38`；`core/openfga/authorization_model.py:41,288-310`；`permission/domain/services/permission_service.py:62-77`。

**上游证据（代表性）**：`ca4ca8bf0:src/backend/bisheng/database/models/flow.py:33-39`；全仓 `git grep` 无 agent registry / A2A / third-party agent 功能级命中；`docs/PRD/3.0-release/3.0 应用工场 产品方案.md:362,385`（F108 defer）；`docs/PRD/3.0-beta2/3.0 开放 API 鉴权与身份传递 PRD.md:1609`（D18 不做逐平台适配）。
