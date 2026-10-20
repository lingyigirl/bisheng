# 智能体纳管 API（F078）

> 适用版本：v2.7.0。覆盖公告（五）3「智能体纳管」的对接面：本平台应用投影、第三方智能体注册、统一调用、定时调度与分类展示。
> 管理面走 `/api/v1`（平台控制台）；外部调用走 `/api/v2`（服务账号密钥 + 权限位）。

## 1. 前置条件

1. 部署配置 `managed_agent.enabled: true`（默认 false；关闭时所有纳管路由 404、菜单/页签隐藏）。
2. 部署后执行一次 FGA 升级脚本，把 `managed_agent` 资源类型写入 OpenFGA store：
   ```bash
   bash scripts/migrate_fga_add_managed_agent_type.sh --dry-run
   bash scripts/migrate_fga_add_managed_agent_type.sh --apply
   ```
   写入后按部署惯例 pin `openfga.model_id` 或重启让 manager 取最新模型（见脚本输出）。
3. 外部调用需要 F076 的服务账号密钥，且密钥带 `managed_agent:invoke` 权限位；服务账号的专用身份还需在「系统 → 服务账号 → 授权」里被授予目标智能体的 `editor`（运行）档。
4. 出站调用受 `managed_agent.egress_allowlist` 约束；空列表 = 全部拒绝。仅支持 http/https，禁止私网/元数据地址、重定向与环境代理。

## 2. 管理面（`/api/v1`，平台控制台）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/managed-agents` | 列表（keyword/source_kind/protocol/status/scenario_code/industry_code/page/page_size） |
| POST | `/api/v1/managed-agents` | 登记（本平台投影或第三方） |
| GET/PUT/DELETE | `/api/v1/managed-agents/{id}` | 详情/编辑/删除（有启用中定时任务时删除返回 12110） |
| POST | `/api/v1/managed-agents/{id}/publish` | 上线 |
| POST | `/api/v1/managed-agents/{id}/offline` | 下线 |
| POST | `/api/v1/managed-agents/{id}/disable` | 停用 |
| POST | `/api/v1/managed-agents/{id}/health-check` | 手动健康检查（失败返回 12108） |
| GET | `/api/v1/managed-agents/{id}/stats` | 调用量/成功率/平均延迟（默认排除健康检查） |
| GET | `/api/v1/managed-agents/catalog` | 客户端目录（仅 online + 当前用户可见） |
| GET/POST/PUT/DELETE | `/api/v1/managed-agent-taxonomies` | 场景/行业分类维护 |

登记请求示例（第三方 OpenAI-compatible）：

```json
{
  "name": "crm-assistant",
  "source_kind": "high_code",
  "protocol": "openai_compatible",
  "endpoint": "https://agents.example.com/v1",
  "model_name": "crm-v1",
  "health_method": "test_invoke",
  "scenario_code": "customer-service",
  "industry_code": "banking",
  "credential": {"auth_type": "bearer", "secret": "<secret>"},
  "agent_card": {
    "version": "v1",
    "inputs": [{"key": "query", "type": "string", "required": true}]
  }
}
```

- `source_kind=native` 时必须给 `native_app_id` + `native_app_type`（5=助手 / 10=工作流），且 `protocol=native`。
- 凭据写入后**永不回显**；详情只返回 `has_credential` / `credential_type` / 掩码。编辑时 secret 留空 = 不变。
- 接入地址在同一租户内唯一；原生投影使用内部 hash，不占用接入地址。

## 3. 统一调用（`/api/v2`）

```http
POST /api/v2/managed-agent/invoke
Authorization: Bearer bs-sak-...
Content-Type: application/json

{"agent_id": 1, "input": {"query": "你好"}, "session_id": "optional"}
```

成功：

```json
{"status_code": 200, "status_message": "success", "data": {"run_id": "...", "output": "...", "usage": null}}
```

- 请求体只接受 `agent_id` / `input` / `session_id`；其他字段返回 12114。
- 只调用已上线智能体；可见性/权限失败按防枚举返回 404/11809，状态失败返回 12105。
- 频控：每租户+智能体每分钟 `managed_agent.rate_limit_per_minute`（默认 60），超限 429/12112，带 `Retry-After`。
- 上游 4xx/5xx/超时返回 12106；非法/超限响应返回 12113；出站地址被拒返回 12111。
- 原生工作流的同步调用复用平台程序化运行；需要多次人工输入的流程在同步面返回失败（定时任务走 F077 的 INPUT 处理）。

## 4. 错误码（121 段）

| 码 | HTTP | 含义 |
|---|---|---|
| 12101 | 404 | v1 管理面：不存在或无权限访问 |
| 12102 | 400 | 注册/调用配置非法 |
| 12103 | 409 | 名称或接入地址重复 |
| 12104 | 400 | 来源/协议不支持 |
| 12105 | 400 | 已下线/停用/草稿 |
| 12106 | 502 | 上游调用失败或超时 |
| 12107 | 502 | 凭据缺失或无效 |
| 12108 | 502 | 健康检查失败 |
| 12109 | 400 | 场景/行业分类非法 |
| 12110 | 409 | 仍被启用中的定时任务引用，不可删除 |
| 12111 | 403 | 出站地址被安全策略拒绝 |
| 12112 | 429 | 调用频率超限 |
| 12113 | 502 | 上游响应非法或超限 |
| 12114 | 400 | 调用请求体含白名单外字段 |

另沿用 F076：缺权限位 11807/403、未登记端点 11811/500、调用方指定身份 11817/400、防枚举 11809/404。

## 5. 与定时任务的关系

登记后的智能体可在「智能体纳管 → 详情 → 定时任务」中创建 cron/一次性任务，事件触发仍走 `POST /api/v2/schedule/trigger`（`schedule:trigger` 权限位）。运行身份、失败计数、通知与 F077 一致；外部智能体与原生助手投影由 managed runner 执行，原生工作流投影复用 F077 工作流链路（含 INPUT）。

## 6. 对客差异

1. 外部智能体的调用是**同步**的；平台不提供外部运行结果查询接口（运行留痕在管理端 `managed_agent_run` 与统计中查看）。
2. 原生工作流的同步调用只支持至多一次人工输入；多输入/文件输入流程请使用定时任务或原生工作流接口。
