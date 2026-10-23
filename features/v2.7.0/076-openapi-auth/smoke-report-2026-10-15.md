# F076 开放 API 鉴权 — 真机冒烟报告（2026-10-15）

> 被测代码：`feat/2.7.0/beijing-ns-076-wip` @ `eae4cadae`（Wave 1 完成态 + 本报告附带的 11807 修复）
> 环境：本机源码栈（`data/bisheng-dev.sh` 管理），后端 `:7860`，配置 `config.celery-private.yaml`
> 口径：**真机 HTTP 冒烟**（非单测替代）——验证鉴权内核在真实进程/中间件/数据库下的端到端行为
> 执行方式：三次重启（总闸关 → 总闸开 → 恢复），冒烟数据用产品自身服务创建、**用后即清**，环境已恢复原状

---

## 0. 结论

**10/10 用例全部符合设计**；发现并当场修复 1 处契约缺口（11807 未回带 `data.required`，已复验）。`/api/v2` 的密钥鉴权链路在真实环境下端到端可用，且**总闸关闭时的行为与升级前逐字一致**（AC-21 回归干净）。

## 1. 轮次 1：总闸关闭（回归验证，AC-21）

用**新代码**+**原配置**重启后：

| # | 请求 | 期望 | 实测 |
|---|---|---|---|
| R1-1 | 无凭据 `GET /api/v2/assistant/list` | 与升级前一致 | **HTTP 200 / status_code 200** ——与改动前基线（本次冒烟前采集：同为 200/200）逐字一致 |
| R1-2 | 带**乱造密钥** `GET /api/v2/assistant/list` | 总闸关 = 连无效密钥也忽略 | **HTTP 200 / 200** |
| R1-3 | 无密钥 `GET /api/v2/auth/whoami` | 真 HTTP 401 + 11801 | **HTTP 401** + `{"status_code":11801,...}` |

> R1-3 同时验证了 T-13 的"真 HTTP 状态"处理器在真实应用中生效（平台惯例是 200+信封）。

## 2. 轮次 2：总闸打开 + 租户开关打开

部署级开关经 `config.f076-smoke.yaml`（**原配置的临时副本**，原文件未改动）注入；租户级开关与服务账号/密钥经产品自身服务创建（`tenant_setting_service.upsert_policy` / `service_account_service.create_service_account` / `credential_service.issue_credential`——管理面 API 属 Wave 3，尚未提供）。

| # | 请求 | 期望 | 实测 |
|---|---|---|---|
| R2-1 | 无凭据 `POST /api/v2/filelib/retrieve`（非 guest 端点） | 401 / 11801 | **HTTP 401** / 11801 |
| R2-2 | 无凭据 `GET /api/v2/assistant/list`（guest 端点 + 免登录开关默认开） | 200（guest 豁免） | **HTTP 200 / 200** |
| R2-3 | **带密钥** `GET /api/v2/assistant/list` | 按服务账号身份执行 | **HTTP 200 / 200** |
| R2-4 | 带密钥 `GET /api/v2/auth/whoami` | 回显自描述信息 | **HTTP 200** + `data={account_id:1, account_name:"F076冒烟", key_mask:"bs-sak-********Eo1g", scopes:[7 项], expires_at:null, tenant_id:1}` |
| R2-5 | 带密钥 `POST /api/v2/workflow/invoke`，`tools=[{"tool_key":"ghost_tool"}]` | 403 / 11815 且回带 tool_key | **HTTP 403** / 11815，`data.tool_key="ghost_tool"` |
| R2-6 | 带密钥 `GET /api/v2/filelib/?user_id=1` | 400 / 11817 | **HTTP 400** / 11817 |
| R2-7 | `Authorization: Bearer nope` | 401 / 11801（格式） | **HTTP 401** / 11801 |
| R2-8 | 格式合法但未签发的密钥 | 401 / 11802 | **HTTP 401** / 11802 |
| R2-9 | 仅授 `assistant:read` 的密钥调 `POST /api/v2/workflow/invoke` | 403 / 11807 + `data.required` | 首测 **HTTP 403 / 11807（缺 required → 见 §3）**；修复后复验 `data.required="workflow:invoke"` ✓ |
| R2-10 | **吊销密钥后同秒**再调 | 401 / 11804（吊销即时生效） | **HTTP 401** / 11804（跨进程清 Redis 缓存生效） |

**关键证据（原文摘录）**：

```json
// R2-4 whoami
{"status_code":200,"data":{"account_id":1,"account_name":"F076冒烟","key_mask":"bs-sak-********Eo1g",
 "scopes":["assistant:invoke","assistant:read","workflow:invoke","workflow:read","chat:invoke","knowledge:read","knowledge:write"],
 "expires_at":null,"tenant_id":1}}

// R2-5 tools 越权
{"status_code":11815,"status_message":"The specified tool is unavailable or not bound to this application","data":{"tool_key":"ghost_tool"}}

// R2-9 权限位不足（修复后）
{"status_code":11807,"data":{"required":"workflow:invoke"}}
```

## 3. 冒烟发现（1 处，已修复）

**11807 未回带 `data.required`**：design §4.2 约定"缺权限位时回带缺失的权限位，对接方据此自助补齐"，实现里漏了 kwargs。修复 `open_api/api/dependencies.py`（一行）+ 重启复验通过 → 提交 `eae4cadae`。这是**只有真机冒烟才能暴露**的一类问题（单测只断言了异常类型与状态码，没人断言 data 形状）。

## 4. 未覆盖的边界（如实列出）

| 项 | 说明 |
|---|---|
| 工具白名单的**执行态** | R2-5 验证到"校验与拒绝"；"选中工具后在工作流/助手内真正启用"需跑真实 LLM 应用，留待 `/e2e-test`（T-29） |
| guest 开关**关闭**路径 | 单测覆盖（T-14 三条用例）；真机改 `enable_guest_access` 需要动你的 DB 配置，未做 |
| WebSocket 握手 | 单元测试覆盖 1008 语义；真机探针在 T-07 用最小应用验证过（握手 403、无 traceback），本次未对全量应用重跑 |
| 调用审计 | T-27 未实现（Wave 4） |
| 管理面 API | T-20~T-22 未实现（Wave 3）——本轮的服务账号/密钥由服务层直建 |
| 多租户隔离 | 单测覆盖（跨租户 404/不可见）；真机为单租户环境 |

## 5. 环境处置（已恢复）

- 三次重启均经 `data/bisheng-dev.sh`（总闸关 → 总闸开 → 关），**当前进程即原始配置**（PID 585250，health 200）；
- 冒烟配置副本 `config.f076-smoke.yaml` **已删除**；`config.celery-private.yaml` 全程未改动（`git status` 可证）；
- 冒烟数据**已硬清理**：服务账号 ×1、密钥 ×2、租户策略行 ×1、专用身份（user 95 + user_tenant）——恢复到冒烟前状态；Redis `oapi:*` 缓存键已清空；
- 恢复验证：无凭据 `GET /api/v2/assistant/list` → HTTP 200/200，与冒烟前基线一致。
