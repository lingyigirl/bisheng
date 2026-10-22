# 开放 API 鉴权（`/api/v2`）— 密钥、权限位与错误码

> 面向：**对接方开发**（携带密钥调用 `/api/v2`）与**平台运维**（签发/吊销/排障）
> 适用范围：`/api/v2` 的全部端点（HTTP 与 WebSocket）
> 平台内查看与导出：控制台「系统 → 服务账号 → 接入指南」提供对接方直接可用的接入说明（接入地址按环境实时替换、curl 示例、权限位与错误码全表），可复制全文或导出 Markdown。两份文档同步维护，均覆盖 118xx / 119xx / 121xx
> 配套：[`开放API密钥鉴权迁移指引.md`](../开放API密钥鉴权迁移指引.md)（存量调用方切换与回退）、[`filelib-retrieve.md`](filelib-retrieve.md)（纯检索接口参考）、[`知识库接口文档.md`](知识库接口文档.md)（知识空间/知识库接口）、[`scheduled-task-trigger.md`](scheduled-task-trigger.md)（事件触发端点）、[`managed-agents.md`](managed-agents.md)（纳管智能体调用）

---

## 1. 怎么拿到密钥

平台管理员在「系统 → 服务账号」里操作（也可用下列管理 API，均在 `/api/v1`，权限=超管或当前租户管理员）：

```
POST   /api/v1/service-accounts                     建服务账号(同事务自动创建专用身份 svc-*,零角色零授权)
POST   /api/v1/service-accounts/{id}/keys           签发密钥(可带 allowed_apps;响应含明文)
PATCH  /api/v1/service-accounts/{id}/keys/{key_id}  改名称/权限位/智能体白名单/有效期(立即生效)
POST   /api/v1/service-accounts/{id}/keys/{key_id}/reveal   管理员查看明文(写审计;存量密钥 11823)
POST   /api/v1/service-accounts/{id}/keys/{key_id}/revoke   单把吊销
POST   /api/v1/service-accounts/{id}/keys/revoke-all        全量吊销(轮换收尾)
GET    /api/v1/open-api/scopes                      可签发权限位目录(10 项)
GET    /api/v1/service-accounts/{id}/assignable-agents      可加入白名单的智能体目录
```

- **密钥格式**：`bs-sak-` + 43 字符随机串，共 50 字符。
- **明文可由管理员查看**：签发响应返回明文；此后可调用 `POST .../keys/{key_id}/reveal` 再次获取（仅租户管理员，每次写审计）。平台校验只依赖不可逆摘要，查看用密文独立加密存储；明文不进入日志/审计/列表/缓存。
- **存量密钥**：本次升级前签发的密钥没有查看密文，reveal 返回 `400 / 11823`，需重新签发。
- **掩码**：列表与审计中一律显示为 `bs-sak-********` + 明文末四位（例：`bs-sak-********AbCd`），用于对账与轮换识别。
- **停用服务账号 = 立即吊销其名下全部密钥**（不可逆，重新启用不会恢复）；删除账号同理。建议按系统/环境分开建账号（例：`信贷系统-生产`、`信贷系统-测试`）。

## 2. 怎么携带

```http
Authorization: Bearer bs-sak-<43 字符>
```

- **WebSocket** 同样在**握手请求头**里携带；不要用查询参数。
- **禁止把密钥放进 URL 查询串**（`?api_key=` 之类）：请求行会被多处访问日志原样记录，密钥等于泄露。平台不接受该形式。

## 3. 调用身份怎么算

- 调用身份 = 该密钥所属**服务账号的专用身份**（`svc-*`，平台自动创建，零角色零资源授权）。**不继承任何管理员特权。**
- **请求里的 `user_id` 等身份参数一律不予采用**：query/form 形态出现即被拒（`400 / 11817`）；出现在请求体里时也不会生效——调用永远按密钥所属身份执行，不会"代表"某个用户。
- 自检端点：`GET /api/v2/auth/whoami` 回显当前身份（账号、掩码、权限位、智能体白名单、有效期），**不需要权限位**，适合联调第一步。

## 4. 权限位（scope）

每个密钥携带一组权限位，**端点级**判定：调用某端点需要它登记的权限位，缺位即 `403 / 11807`（响应 `data.required` 回带缺少的 code）。

| code | 覆盖的能力（摘要） |
|---|---|
| `workflow:invoke` | 工作流调用/停止、工作流会话（WS） |
| `workflow:read` | 工作流详情 |
| `assistant:invoke` | 助手对话（含流式与 WS）、语音转写/合成 |
| `assistant:read` | 助手信息与列表 |
| `chat:invoke` | 会话管理（历史、标题、点赞、标记、评论、消息同步） |
| `knowledge:read` | 知识库与文件读取、检索（`/filelib/retrieve` 等）、引用详情 |
| `knowledge:write` | 知识库与文件的增删改、问答与元数据写入 |
| `delegate` | 预留位：本期可签发但**不产生任何生效行为** |
| `schedule:trigger` | 定时任务的事件触发端点（`POST /api/v2/schedule/trigger`） |
| `managed_agent:invoke` | 纳管智能体调用端点（`POST /api/v2/managed-agent/invoke`） |

- 目录端点 `GET /api/v1/open-api/scopes` 返回可签发清单（`code`/`group`/`endpoint_count`），以它为准。
- **隐含规则**：授予 `knowledge:write` 即同时具备 `knowledge:read`。
- 签发时未登记的 code 一律拒绝（`400 / 11819`）。

## 4.1 每 Key 智能体白名单（`allowed_apps`）

权限位是**功能维度**（能调哪类端点），白名单是**具体对象维度**（能调哪些智能体）：

- `allowed_apps` 为 `null`/不传 = 不限制，沿用账号资源授权；
- `[]` = 不调用任何智能体（只读/知识库调用不受影响）；
- 非空数组 = 只能调用其中的助手/工作流/纳管智能体：`[{"type":"assistant|workflow|managed_agent","id":"..."}]`。

判定顺序：**权限位 → 白名单 → 资源授权**，三者同时满足才放行。白名单未命中与资源不可见同码 `404 / 11809`。同一服务账号下的不同密钥可以有不同白名单（例如 Key1 只允许工作流 A，Key2 只允许工作流 B）。

- `schedule:trigger` 是间接调用：事件触发时会按任务的目标应用再判一次白名单。
- `managed_agent` 条目对应纳管注册表 id（不是 `native_app_id`）。
- 白名单只收窄、不扩权；编辑后 ≤5 秒生效。

## 5. 资源可见范围

- 可见范围 = **该专用身份在平台权限体系中的资源授权**。默认**为空**。
- 可授权的资源类型：`knowledge_library`（知识库）、`knowledge_space`（知识空间）、`assistant`（助手）、`workflow`（工作流）、`managed_agent`（纳管智能体）。
- 管理员在服务账号详情页的「资源授权」里代管这些授权（也可用 `POST /api/v1/service-accounts/{id}/resource-grants:mutate`）；授权有档位：`viewer` / `editor` / `manager`（不含 `owner`）。
- 调用类端点的档位要求：
  - 工作流调用/停止、助手对话（HTTP 与 WebSocket）、`/api/v2/chat/*`、`schedule/trigger`：需要 `use_app`，即 **editor 及以上**；
  - `managed-agent/invoke`：需要 **editor**；
  - 知识库检索 `POST /api/v2/filelib/retrieve`：文档知识库需要 `use_kb`（系统默认 viewer 档同时含 `view_kb` + `use_kb`）；知识空间（type=3）沿用视图权限，不适用 `use_kb`；
  - 问答行查询 `POST /api/v2/filelib/query_qa`：只返回该身份有 `view_kb` 的知识库下的记录；
  - 只读的列表与详情（含助手详情/列表）：viewer 即可。
- **对接方应把 `403` 与 `404` 都当作"未授权/不可见"处理**：资源不在授权范围、或本就不可见，都按"不能访问"实现降级（不要据此区分资源存在性）。档位不足是 `403 / 11808`（资源可见），不可见是 `404 / 11809`。

## 6. 错误码与 HTTP 状态

鉴权与授权失败走**真实 HTTP 状态码**（401/403/404/400/500/503），响应体仍是统一信封：

```json
{"status_code": 11807, "status_message": "Missing the scope required by this endpoint",
 "data": {"exception": "...", "required": "knowledge:read"}}
```

- **两个例外，不要按 HTTP 状态判断**：请求参数缺失/类型错误返回 HTTP **200** + `status_code: 422`；工作流内部错误（如续跑缺 `message_id`）返回 HTTP **200** + `status_code: 500`。
- `managed_agent.enabled=false` 时 `POST /api/v2/managed-agent/invoke` 路由不存在，返回**普通 404**（无统一信封）。

### 6.1 鉴权与授权（118xx）

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 11801 | 401 | 缺失/格式非法密钥 | 检查 `Authorization: Bearer bs-sak-…` |
| 11802 | 401 | 密钥无效（查不到） | 核对密钥是否正确/是否被清理 |
| 11803 | 401 | 密钥过期 | 换新密钥 |
| 11804 | 401 | 密钥已吊销 | 联系管理员确认吊销原因 |
| 11805 | 401 | 服务账号停用/专用身份不可用 | 联系管理员 |
| 11806 | 403 | 租户停用 | 联系管理员 |
| 11807 | 403 | 缺权限位（`data.required` 回带） | 让管理员给密钥补上该权限位 |
| 11808 | 403 | 资源可见但档位不足 | 提升授权档位 |
| 11809 | 404 | 资源不存在**或**不在授权范围（防枚举，两者不可区分） | 确认资源已授权给该账号 |
| 11810 | 400 | 指定的资源所有者是管理员（防提权） | 换非管理员用户 |
| 11811 | 500 | 端点漏标权限位（平台配置错误） | 报障（服务端日志可见） |
| 11812 | 503 | 鉴权依赖不可用（fail-closed） | 稍后重试 |
| 11813 | 400 | 服务账号重名 | 换名 |
| 11814 | 503 | 授权服务不可用 | 稍后重试 |
| 11815 | 403 | `tools[]` 指定了未绑定/未启用的工具（`data.tool_key` 回带） | 见 §7 |
| 11816 | 403 | 服务账号 owner 事后被提权（运行时防提权） | 报障：该账号已停用 |
| 11817 | 400 | 请求里出现裸 `user_id`（身份不可由调用方指定） | 去掉该参数 |
| 11818 | 400 | `tools[]` 在本次调用形态下无法生效（如工作流续跑） | 见 §7 |
| 11819 | 400 | 签发/编辑密钥时指定了未登记的权限位 | 从目录端点取合法 code |
| 11820 | 400 | 授权代管时资源类型/档位不在白名单 | 见第 5 节 |
| 11821 | 400 | 该用户是服务账号专用身份，不允许在用户侧人工管理 | 到「服务账号」页操作 |
| 11822 | 400（管理面） | 签发/编辑密钥时指定的智能体不可用或不在账号授权范围 | 先在「资源授权」中授予 |
| 11823 | 400（管理面） | 该密钥不可查看明文（存量密钥无密文或密文损坏） | 重新签发一把新密钥 |

### 6.2 事件触发（119xx）

仅 `POST /api/v2/schedule/trigger` 产生；完整说明见 [`scheduled-task-trigger.md`](scheduled-task-trigger.md)。

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 11907 | 409 | 目标应用不可用（下线/不可调度） | 联系管理员恢复应用 |
| 11910 | 409 | 该任务已有运行在进行 | 等本次运行结束后再触发 |
| 11911 | 429 | 任务级触发频控（默认 10 次/分钟/任务） | 降低频率后重试；响应**不带** `Retry-After` |
| 11912 | 400 | 时间戳超窗/缺失，或 nonce 重放 | 校准系统时钟，每次使用新的 nonce |
| 11916 | 400 | 触发载荷含多余字段 | 只传 `task_id` 与 `inputs` |

> 事件触发同样会返回 `11801`（缺密钥）、`11807`（缺权限位）、`11809`（任务不可见/不存在）、`11812`（Redis 不可用，防重放 fail-closed）、`11817`（裸 `user_id`），按 6.1 处理。

### 6.3 纳管智能体调用（121xx）

仅 `POST /api/v2/managed-agent/invoke` 产生；完整说明见 [`managed-agents.md`](managed-agents.md)。

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 12105 | 400 | 智能体未上线（草稿/下线/停用） | 联系管理员上线 |
| 12106 | 502 | 上游调用失败或超时 | 稍后重试；持续失败联系管理员 |
| 12107 | 502 | 上游凭据缺失/无效/无法解密 | 联系管理员检查凭据 |
| 12111 | 403 | 出站地址被 SSRF / allowlist 拒绝 | 联系管理员调整出站白名单 |
| 12112 | 429 | 调用频控（默认 60 次/分钟） | 按响应头 `Retry-After` 退避重试 |
| 12113 | 502 | 上游响应非法或超限 | 联系管理员核对上游协议 |
| 12114 | 400 | 调用载荷含非法字段或类型错误 | 见 [`managed-agents.md`](managed-agents.md) |

> 纳管调用同样会返回 `11801`、`11807`、`11809`、`11812`、`11817`，按 6.1 处理。

三语文案（中/英/日）由平台统一维护，错误码含义与文案同源。

## 7. 工具参数 `tools[]`

助手与工作流的开放调用支持可选 `tools`：**白名单语义**——列出即"本轮启用"，未列出的一律不启用；**不传 = 完全按应用默认配置**（与升级前一致）。

- 条目形状：`{"id": <工具ID>, "tool_key": "<工具key>", "type": "tool"}`。
- 可用清单：助手详情 `GET /api/v2/assistant/info/{id}` 的 `tool_list`；工作流详情 `GET /api/v2/flows/{id}` 的 `tools`。
- 未绑定/未知工具 → `403 / 11815`（不静默忽略）；工作流**续跑**时传 `tools[]` → `400 / 11818`（该形态下无法生效，显式拒绝）。

## 8. 吊销、过期与停用的即时性

- 吊销/编辑/停用后 **≤5 秒**内新调用即被拒（平台侧有短 TTL 缓存）。
- **已经建立的 WebSocket 会话一期不主动中断**：当前执行中的会话继续跑完，之后的新调用按新状态判定。

## 9. 审计与日志

- 每次被鉴权的调用都会留一条审计（密钥掩码、服务账号、权限位、HTTP 状态、业务码、耗时、来源 IP），管理动作另有前后取值。
- 审计**不含密钥明文、密文或摘要**；管理员每次查看明文另写一条 `open_api.api_key.reveal`（只记掩码/账号/操作者）。
- 审计不在平台「审计日志」页展示（取证数据），需要时由运维按 `auditlog.action = 'open_api.call'` 查询；对客口径建议保留 **≥ 6 个月**。

## 10. 常见联调问题

| 现象 | 排查 |
|---|---|
| 401 / 11801 | 请求头是否 `Authorization: Bearer bs-sak-…`（不要把密钥写进 URL） |
| 403 / 11807 | 看响应 `data.required`，让管理员把该权限位加进密钥（`PATCH .../keys/{key_id}`） |
| 404 / 11809 | 资源未授权给该账号：在服务账号页「资源授权」里授予，或核对资源 ID |
| 401 / 11805 | 服务账号被停用；确认后在服务账号页启用（密钥需重新签发） |
| 同一个密钥昨天能用今天不行 | 检查有效期（列表可见"有效期至"）与是否被 `revoke-all` |
| 409 / 11910、429 / 11911、400 / 11912 | 事件触发：任务运行未结束、触发频控、时间戳超窗或 nonce 重放（11911 **不带** `Retry-After`） |
| 400 / 12105、502 / 12106 / 12107 / 12113、403 / 12111、429 / 12112 | 纳管调用：智能体未上线、上游失败/凭据/响应非法、出站被拒、频控 |
| HTTP 200 但 `status_code` 是 422 | 缺必填参数或类型错误（平台统一在响应体里回业务码） |

## 11. 事件触发端点（`POST /api/v2/schedule/trigger`）

- **鉴权**：必须携带密钥（权限位 `schedule:trigger`）；即使部署总闸 `open_api.enforce_keys` 关闭，本端点**仍要求有效密钥**（否则 `401 / 11801`）。
- **请求头**：`X-Bs-Timestamp`（epoch 秒，允许 ±300 秒）与 `X-Bs-Nonce`（一次性，窗口内重复即拒）。
- **请求体**：`{"task_id": <int>, "inputs": {...}?}`；`inputs` **被接收但当前不生效**，运行时入参取自任务预设；多余字段 `400 / 11916`。
- **资源要求**：密钥身份需对任务目标应用有 `use_app`；否则 `404 / 11809`（不区分不存在、停用与越权）。
- **成功响应**：`{"status_code":200,"status_message":"SUCCESS","data":{"task_id":<id>,"run_trace_id":"..."}}`，受理即返回。
- **错误**：见 6.2。

```bash
TS=$(date +%s); NONCE=$(uuidgen)
curl -s -X POST "$BASE/api/v2/schedule/trigger" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H "X-Bs-Timestamp: $TS" -H "X-Bs-Nonce: $NONCE" \
  -d '{"task_id":123}'
```

## 12. 纳管智能体调用端点（`POST /api/v2/managed-agent/invoke`）

- **鉴权**：必须携带密钥（权限位 `managed_agent:invoke`）；总闸关闭时同样要求有效密钥。
- **请求体**：`{"agent_id": <int>, "input": <any>, "session_id": "<str>"?}`；多余字段或类型错误 `400 / 12114`。
- **资源要求**：智能体须已上线，且密钥身份对其有 **editor** 授权；否则 `404 / 11809`，未上线 `400 / 12105`。
- **成功响应**：`{"status_code":200,"status_message":"SUCCESS","data":{"run_id":"...","output":"...","usage":{...}}}`。
- **频控**：60 次/分钟（租户+智能体），`429 / 12112` 带 `Retry-After`；健康检查不计入。
- **功能开关**：`managed_agent.enabled=false` 时路由不挂载，返回**普通 404**。
- **错误**：见 6.3。

```bash
curl -s -X POST "$BASE/api/v2/managed-agent/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"agent_id":29,"input":{"query":"你好"}}'
```
