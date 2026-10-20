# F078 智能体纳管 — 改动清单与使用说明

> 对应公告（五）3：「统一纳管本平台、其他低代码平台或高码开发的第三方智能体，实现统一接入、调度与运营；支持按场景/行业/标签分类展示。」
> 分支：`feat/2.7.0/beijing-ns-078-wip`；上游无同能力实现（无可移植代码）。

---

## 1. 这次解决了什么

- **本平台应用**：新增「纳管」投影，把已有助手/工作流登记进统一注册表，补场景/行业/标签分类；应用本身不改、不复制。
- **第三方智能体**：新增独立注册表与适配器（OpenAI-compatible / 自定义 HTTP），第三方智能体不写入 `flow`/`assistant` 表。
- **统一调用**：新增 `POST /api/v2/managed-agent/invoke` + `managed_agent:invoke` 权限位，复用 F076 服务账号密钥与身份。
- **统一调度**：F077 的定时/事件触发支持 `app_type=100`；原生工作流投影复用 F077 工作流链路（含 INPUT），第三方/原生助手走 managed runner。
- **运营**：健康检查、调用留痕（`managed_agent_run`）、基础统计、失败通知、审计。
- **分类展示**：场景/行业 taxonomy + 现有标签；平台纳管控制台 + 客户端「第三方智能体」页签。

---

## 2. 后端改动

### 2.1 新模块 `bisheng/managed_agent/`

| 位置 | 内容 |
|---|---|
| `domain/models/{managed_agent,taxonomy,run}.py` | 三张表：注册表、分类法、调用留痕；`JsonType`/`LargeText`/`UPDATE_TIME_SERVER_DEFAULT`，租户列 `default=None` |
| `domain/models/dao.py` | CRUD、状态 CAS、删除前计数、健康更新、调用统计；管理读 `strict_tenant_filter`，批量写手写 `tenant_id` |
| `domain/services/agent_card.py` | Agent Card 解析/预设输入校验；文件输入不可定时化（11914） |
| `domain/services/registry_service.py` | 注册/编辑/上线/下线/停用/删除；标签关联（`TagLink`，create/update/delete 同步）；owner tuple `enforce_fga_success=True`，失败回滚行 |
| `domain/services/classification_service.py` | 场景/行业 CRUD；被引用时禁止删除 |
| `domain/services/credential_service.py` | Fernet 加密凭据、掩码、空 secret = 不变 |
| `domain/services/adapters/*` | native / OpenAI-compatible / custom HTTP；SSRF/allowlist/超时/响应体上限 |
| `domain/services/invoke_service.py` | 可见性→状态→权限→频控→适配器→留痕/审计 |
| `domain/services/rate_limiter.py` | Redis `SET NX EX` + INCR 固定窗口；429 + `Retry-After` |
| `domain/services/health_service.py` | 手动/周期健康检查、阈值、自动下线开关、跨租户 beat |
| `api/endpoints/{agents,taxonomy,invoke}.py` | v1 管理面 + v2 调用面 |

### 2.2 既有文件改动

| 文件 | 改动 |
|---|---|
| `api/router.py` | 挂载 v1/v2 路由 |
| `core/database/tenant_filter.py` | 三个模型登记租户过滤 |
| `common/errcode/managed_agent.py` + `main.py` | 12101–12114；v2 真状态、v1 200 信封；429 `Retry-After` |
| `core/openfga/authorization_model.py` | `managed_agent` 类型；合并后 `MODEL_VERSION=v2.0.5`（19 类型；`linsight_skill` 随 llm-guardrail 合并带入） |
| `scripts/migrate_fga_add_managed_agent_type.py/.sh` | 按 store 写一次新模型（dry-run 默认） |
| `permission/domain/services/permission_service.py` | creator/tenant/list 分支 + `_TENANT_GATED_RESOURCE_TYPES` |
| `permission/.../fine_grained_permission_service.py`、`resource_authorization_service.py`、`permission_schema.py` | 权限 ID 映射/白名单 |
| `open_api/domain/scopes.py` | `managed_agent:invoke`（9→10，路由 45→46） |
| `open_api/.../resource_grant_service.py` | 授权代管支持 `managed_agent` |
| `database/models/{tag,group_resource}.py` | `MANAGED_AGENT` 业务/资源类型 |
| `database/models/audit_log.py` | `managed_agent.*` 动作 + 命名空间 |
| `core/config/managed_agent.py`、`settings.py`、两份 `config.yaml` | `ManagedAgentConf`（enabled 默认 false）+ health beat |
| `scheduled_task/...` | `app_type=100`：trigger_guard / task_service / app_shape_guard / knowledge_guard / run_service / nl_draft / worker runner |
| `worker/__init__.py` | 注册 health scan 与 managed runner |

---

## 3. 前端改动

### 平台（`src/frontend/platform`）

- 新页面 `pages/BuildPage/managedAgents/`：列表/筛选（含标签）、注册编辑（含标签多选）、详情（含调用统计面板）、分类管理、健康检查、启停/删除、定时任务面板。
- 路由 `build/managed-agents` + HeaderMenu「智能体纳管」。
- API `controllers/API/managedAgent.ts`、类型 `types/api/managedAgent.ts`。
- 服务账号授权代管增加 `managed_agent` 类型。
- 三语 `managedAgent.json` + `bs.json` 菜单/审计键 + `serviceAccount.json` 权限位文案。

### 客户端（`src/frontend/client`）

- `pages/apps/explore.tsx` 增加 Tabs：原生应用广场 / 第三方智能体。
- `ManagedAgentTab/Card/Filters`：只展示 online + 有查看权限的纳管智能体；场景/行业/标签筛选、标签展示、预览抽屉。
- `api/managedAgents.ts` + 三语 `translation.json` 键。

---

## 4. 部署与使用

### 4.1 部署顺序

1. 部署代码（`managed_agent.enabled` 默认 false，既有行为不变）。
2. 执行 FGA 升级脚本（每 store 一次，**两个都跑**）：
   ```bash
   cd src/backend
   bash scripts/migrate_fga_add_managed_agent_type.sh --dry-run
   bash scripts/migrate_fga_add_managed_agent_type.sh --apply
   # skill 脚本默认只读；写入用 --apply，报告路径按环境替换
   .venv/bin/python scripts/migrate_fga_skill_permissions.py --report /tmp/fga-skill-dry-run.json
   .venv/bin/python scripts/migrate_fga_skill_permissions.py --apply --report /tmp/fga-skill-apply.json
   ```
   合并后的模型是 `v2.0.5` / 19 类型（`managed_agent` + `linsight_skill`）。只跑 managed_agent 脚本时，若 store 已有 `managed_agent` 但缺 `linsight_skill`，脚本会判定“已存在”而 no-op；两个脚本任一顺序都能收敛到完整模型。按输出 pin `openfga.model_id` 或重启让 manager 取最新模型。
3. 在目标环境 `config.yaml` 开启：
   ```yaml
   managed_agent:
     enabled: true
     egress_allowlist: ["agents.example.com"]
   ```
   重启 API/worker；如用 compose，确认 `backend_beat` 常驻（定时任务计划轮次依赖它）。
4. 外部调用还需：`open_api.enforce_keys: true`、服务账号密钥带 `managed_agent:invoke`、服务账号被授予目标智能体 `editor`。

### 4.2 使用路径

- 平台：**构建 → 智能体纳管** → 新建（本平台投影或第三方）→ 上线 → 详情配置定时任务。
- 客户端：**应用中心 → 智能体广场 → 第三方智能体** 页签，按场景/行业筛选。
- 外部调用：
  ```bash
  curl -X POST https://<host>/api/v2/managed-agent/invoke \
    -H "Authorization: Bearer $BS_SAK" \
    -H "Content-Type: application/json" \
    -d '{"agent_id":1,"input":{"query":"你好"}}'
  ```

### 4.3 回滚

- 关闭 `managed_agent.enabled`（路由 404、菜单隐藏；已有 `app_type=100` 任务按 OFFLINE 跳过/等待/拒绝）。
- FGA 可继续 pin 旧 `model_id`（模型只增不改）。
- 删表：`DROP TABLE managed_agent_run; DROP TABLE managed_agent_taxonomy; DROP TABLE managed_agent;`（无既有表数据迁移）。
- 摘除 `_TENANT_AWARE_MODEL_MODULES` 三条登记与路由。

---

## 5. 验证记录

- 后端回归 **644 passed**（`config=config.yaml`：`test/managed_agent` + `test/open_api` + `test/scheduled_task` + FGA/F013）；现场配置 `config=config.celery-private.yaml` 下 **643 passed, 1 skipped**（开关打开时路由挂载用例按设计跳过）。
- 平台 `pnpm typecheck` **434 strict 全过**；客户端 `pnpm typecheck` **1304 strict 全过**；`check-i18n` OK。
- 客户端/平台本次改动文件 eslint 0。
- MySQL DDL 离线编译：31/11/20 列；DM8 无本机实例，按清单留现场。
- e2e 套件 `test/e2e/test_e2e_managed_agents.py` **9 passed**（本机运行实例，启用 `allow_private_networks` + allowlist `127.0.0.1`）。
- 现场验收报告 §6 的 9 项问题已逐项核实与修复，见 `docs/F078-报告问题修复说明.md`。

---

## 6. 已知边界

- A2A 完整任务生命周期/流式、具名低代码连接器、市场/计费/评测/故障转移不在本期。
- 原生工作流同步调用只支持至多一次人工输入；多输入/文件输入走定时任务或原生工作流接口。
- 健康检查默认只标记 unhealthy，不自动下线（`auto_offline_on_health_failure` 默认 false）。
- 复用既有硬编码 Fernet key（平台既有 C6 缺口）；换 key 会导致已存凭据不可解密。
- `docs/report.md` 为并行会话文件，不在本特性提交范围。
