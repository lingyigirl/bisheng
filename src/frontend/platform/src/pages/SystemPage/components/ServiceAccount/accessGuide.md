# 开放 API 接入指南

本文档供**行内对接系统的开发人员**使用：通过 API Key 调用本平台的智能体应用、工作流、知识库与已纳管智能体接口。

文中出现的 `{{base_url}}` / `{{ws_base_url}}` 会在查看与导出时自动替换为页面顶部的「接入地址」。该地址默认可能来自平台配置、自动探测或控制台地址，**不一定是正确的对接地址**，交付前请按第二节确认。示例中的密钥均为占位符，请以管理员实际交付的为准。

## 一、三步开始

1. **确认地址**：向平台运维确认对接系统应使用的 API 根地址，并按 2.1 验证（不要默认使用控制台地址）。
2. **自检**：`GET {{base_url}}/api/v2/auth/whoami`，确认身份、权限位与有效期（见第五节）。
3. **调用**：请求头携带 `Authorization: Bearer <密钥>`，按第八节调用；失败先查第十节错误码，再按第十四节自查。

## 二、接入地址与强制鉴权

### 2.1 接入地址

接入地址 = **对接系统实际访问、能到达 `/api/v2` 的根地址**：

- 内网直连后端：`http://<后端IP>:7860`；
- 经 API 网关/反向代理：`https://<网关域名>[:端口][/前缀]`（由运维提供）；
- 生产必须 HTTPS；WebSocket 对应 `wss://`。

页面顶部地址的来源有三种：**平台配置**（运维在 `config.yaml` 的 `open_api.public_base_url` 设置，最权威）、**自动探测**（平台探测本机 IP 与监听端口，仅在开发环境作为默认值）、**控制台地址**（默认值）。前端开发服务器地址只在开发代理下可用，**不可作为交付地址**。

- 浏览器直连后端时，需要运维把对接系统的源加入后端 CORS 白名单；服务端到服务端调用不受此限制。
- 验证地址（不带密钥）：

  ```bash
  curl -i {{base_url}}/api/v2/auth/whoami
  ```

  预期 **HTTP 401 + 业务码 11801**（能到达且开放 API 已挂载）。连接失败、404 或返回 HTML 页面，说明地址不对。
- 还要从**对接系统所在网段**验证防火墙、白名单与 TLS 证书；WebSocket 需能完成握手。

### 2.2 强制鉴权开关

- **部署级总闸** `open_api.enforce_keys` 关闭时：`/api/v2` 回到旧行为，`Authorization` 被**完全忽略**（连无效密钥也不会被拦）。
- 总闸开启、请求**不带** `Authorization` 时：
  - 免登录面（见下）在 `default_operator.enable_guest_access` 开启时放行；
  - 其余端点按**默认租户**的租户开关判定：开启 → `401 / 11801`；关闭 → 旧行为（分租户灰度）。
- 总闸开启、请求**带** `Authorization` 时：**始终校验**，身份与权限位一律按密钥执行，**与租户开关无关**。
- 免登录面共 8 个端点：`POST /api/v2/assistant/chat/completions`、`GET /api/v2/assistant/info/{id}`、`GET /api/v2/assistant/list`、`WS /api/v2/assistant/chat/{id}`、`WS /api/v2/workflow/chat/{id}`、`POST /api/v2/chat/gen_title`、`GET /api/v2/chat/history`、`GET /api/v2/flows/{id}`。
- **例外**：`POST /api/v2/schedule/trigger` 与 `POST /api/v2/managed-agent/invoke` 无论总闸是否开启都要求有效密钥（总闸关闭时返回 `401 / 11801`）。
- 判断总闸（**不要用 whoami**，总闸关闭时它同样返回 `401 / 11801`，与"密钥格式错"无法区分）：

  ```bash
  # 用一把格式合法但不存在的密钥调用非免登录端点
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H 'Authorization: Bearer bs-sak-0000000000000000000000000000000000000000000' \
    '{{base_url}}/api/v2/filelib/'
  # 总闸开启：401（业务码 11802）；总闸关闭：旧行为（通常 200 / 业务结果）
  ```

## 三、密钥怎么来、怎么保管

- 密钥由**平台管理员**在控制台「系统 → 服务账号」中建号并签发，经行内安全渠道线下交付；平台不提供自助申请与工单流程。
- **密钥格式**：`bs-sak-` 开头 + 43 个随机字符，共 50 个字符。
- **明文只在签发时显示一次**：平台只保存不可逆摘要，此后任何接口、界面、日志、审计都无法取回明文；遗失只能重新签发。
- 平台列表与审计中密钥显示为掩码（如 `bs-sak-********AbCd`），用于对账与轮换识别。
- 建议**一个对接系统（一个环境）一个服务账号**：生产与测试分开，便于单独吊销、单独审计。
- 密钥放在环境变量或密钥管理系统中，**不要入库、不要提交代码库、不要打进镜像**。

## 四、怎么携带密钥

```http
Authorization: Bearer bs-sak-<43 个字符>
```

- **WebSocket** 同样在**握手请求头**中携带，不使用查询参数。
- **禁止**把密钥放进 URL 查询串（`?api_key=...` 之类）：请求行会被访问日志原样记录，等同于泄露；平台也不接受这种形式。
- **不要**在请求中传 `user_id` 等身份参数：调用身份完全由密钥决定，传了会被拒绝（`400 / 11817`）。
- 调用**不代表**任何具体用户：密钥对应平台为该服务账号自动创建的专用身份（`svc-*`，零角色零权限），**不继承任何管理员权限**。
- `Authorization` 只在总闸开启时生效；两个新增端点（见 2.2）例外——它们始终要求密钥。

## 五、先自检：这个密钥能干什么

```bash
curl -H "Authorization: Bearer $KEY" {{base_url}}/api/v2/auth/whoami
```

返回为统一信封，`data` 中包含账号名、掩码、权限位与有效期：

```json
{
  "status_code": 200,
  "status_message": "SUCCESS",
  "data": {
    "account_id": 1,
    "account_name": "信贷系统",
    "key_mask": "bs-sak-********AbCd",
    "scopes": ["assistant:invoke", "knowledge:read"],
    "expires_at": null,
    "tenant_id": 1
  }
}
```

该端点**不需要任何权限位**，是联调第一步。注意：返回 200 同时说明"总闸已开启 + 密钥有效"；总闸关闭时它返回 `401 / 11801`。

## 六、权限位（可签发 10 项）

每个密钥携带一组权限位，**按端点判定**：调用某端点需要它登记的权限位，缺位返回 `403 / 11807`，响应体 `data.required` 回带缺少的 code。

| code | 覆盖的能力（摘要） |
|---|---|
| `workflow:invoke` | 工作流调用/停止、工作流会话（WebSocket） |
| `workflow:read` | 工作流详情 |
| `assistant:invoke` | 助手对话（含流式与 WebSocket）、语音转写/合成 |
| `assistant:read` | 助手信息与列表 |
| `chat:invoke` | 会话管理（历史、标题、点赞、标记、评论、消息同步） |
| `knowledge:read` | 知识库与文件读取、检索、引用详情 |
| `knowledge:write` | 知识库与文件的增删改、问答与元数据写入 |
| `schedule:trigger` | 事件触发 `POST /api/v2/schedule/trigger` |
| `managed_agent:invoke` | 调用已纳管的智能体（本平台投影或第三方）`POST /api/v2/managed-agent/invoke` |
| `delegate` | 预留位：本期可签发但**不产生任何生效行为** |

- **隐含规则**：授予 `knowledge:write` 即同时具备 `knowledge:read`。
- 权限位决定"能调哪类端点"，**资源授权**（第七节）决定"能调哪个资源"——两者都要满足。
- 权限位在签发/编辑密钥时由管理员选择；需要补位时联系管理员（编辑后 ≤5 秒生效）。

## 七、资源可见范围

- 能访问哪些资源，取决于该服务账号的**资源授权**：默认**为空**，需要管理员在服务账号详情页「资源授权」中授予。
- 可授权的资源类型：知识库（`knowledge_library`）、知识空间（`knowledge_space`）、助手（`assistant`）、工作流（`workflow`）、纳管智能体（`managed_agent`）。
- 档位：`viewer`（只读）/ `editor`（读写/调用）/ `manager`（管理，不含 `owner`）。
- 调用类端点的档位要求：
  - 工作流调用/停止、会话管理、事件触发：需要 `use_app`，即 **editor 及以上**；
  - 助手对话：`view_app` 或 `use_app` 任一即可，**viewer 也够**；
  - 纳管智能体调用：需要 **editor**；
  - 只读的列表与详情：viewer 即可。
- 资源不在授权范围、或资源不存在，**都返回 `404 / 11809`**（防枚举，两者不可区分）；资源可见但档位不足返回 `403 / 11808`。
- **实现建议**：把 `403` 与 `404` 都当作"未授权/不可见"处理，据此做降级提示，不要用来判断资源是否存在。

## 八、常用端点速查

| 能力 | 方法与路径 | 权限位 | 关键参数 |
|---|---|---|---|
| 凭据自检 | `GET /api/v2/auth/whoami` | 无（仅需密钥） | — |
| 助手对话（含流式） | `POST /api/v2/assistant/chat/completions` | `assistant:invoke` | `model`（助手ID）、`messages`、`stream`（默认 false）、`temperature`、`tools?` |
| 助手信息 / 列表 | `GET /api/v2/assistant/info/{assistant_id}` · `GET /api/v2/assistant/list` | `assistant:read` | 列表 `page`、`limit`、`name?`、`tag_id?`、`status?` |
| 助手对话（WebSocket） | `WS /api/v2/assistant/chat/{assistant_id}` | `assistant:invoke` | `chat_id?` |
| 工作流调用 / 停止 | `POST /api/v2/workflow/invoke` · `POST /api/v2/workflow/stop` | `workflow:invoke` | `workflow_id`、`input`、`stream`（默认 **true**）、`session_id?`、`message_id?`、`override?`、`tools?` |
| 工作流详情 | `GET /api/v2/flows/{flow_id}` | `workflow:read` | 返回 `data.tools` |
| 工作流会话（WebSocket） | `WS /api/v2/workflow/chat/{workflow_id}` | `workflow:invoke` | `chat_id?` |
| 知识库 / 知识空间列表 | `GET /api/v2/filelib/` | `knowledge:read` | `type`（0 文档库、1 问答库、3 知识空间；2 不支持）、`name?`、`page_size`、`cursor?` |
| 文件列表 | `GET /api/v2/filelib/file/list` | `knowledge:read` | **`knowledge_id`（必填）**、`parent_id?`、`keyword?`、`page_size`、`cursor?` |
| 知识库检索 | `POST /api/v2/filelib/retrieve` | `knowledge:read` | `query`、`knowledge_base_ids`、`top_k`（1–200，默认 10） |
| 会话历史 | `GET /api/v2/chat/history` | `chat:invoke` | **`chat_id`、`flow_id`（均必填）**、`id?`、`page_size?` |
| 会话其他 | `POST /api/v2/chat/gen_title` · `liked` · `solved` · `comment` · `sync/messages` | `chat:invoke` | 见 openapi.json |
| 引用详情 | `GET /api/v2/citation/{citation_id}` | `knowledge:read` | — |
| 事件触发 | `POST /api/v2/schedule/trigger` | `schedule:trigger` | 见 8.1 |
| 纳管智能体调用 | `POST /api/v2/managed-agent/invoke` | `managed_agent:invoke` | 见 8.2 |

以上路径前都需拼上接入地址。表中未列出的端点同样遵循同一套鉴权与错误码规则；完整入参/出参与返回示例见 `GET {{base_url}}/docs`（Swagger UI）或 `GET {{base_url}}/openapi.json`（机器可读，**以它为准**）。

### 8.1 事件触发（`schedule:trigger`）

```bash
TS=$(date +%s); NONCE=$(uuidgen)
curl -s -X POST "$BASE/api/v2/schedule/trigger" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H "X-Bs-Timestamp: $TS" -H "X-Bs-Nonce: $NONCE" \
  -d '{"task_id":123}'
# → {"status_code":200,"status_message":"SUCCESS","data":{"task_id":123,"run_trace_id":"..."}}
```

- 必带两个头：`X-Bs-Timestamp`（epoch 秒，允许 ±300 秒）与 `X-Bs-Nonce`（一次性，窗口内重复即拒）。
- 请求体只接受 `task_id`（整数，必填）与可选的 `inputs`；**`inputs` 当前不生效**——运行时入参取自任务本身的预设配置；多余字段直接拒绝。
- 任务必须处于启用状态，且密钥身份对任务目标应用有 `use_app` 权限；否则一律 `404 / 11809`（不区分不存在、停用与越权）。
- 受理即返回，不等运行完成；运行结果在平台任务列表与监控中查看。
- 该端点无论总闸是否开启都要求密钥。

### 8.2 纳管智能体调用（`managed_agent:invoke`）

```bash
curl -s -X POST "$BASE/api/v2/managed-agent/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"agent_id":29,"input":{"query":"你好"},"session_id":"<可选>"}'
# → {"status_code":200,"status_message":"SUCCESS",
#    "data":{"run_id":"...","output":"...","usage":{...}}}
```

- 请求体只接受 `agent_id`（整数，必填）、`input`（必填）与可选的 `session_id`；多余字段或类型错误直接拒绝。
- 智能体必须已上线，且密钥身份对其有 **editor** 授权；未上线、未授权都按不可见处理。
- 出站地址受平台 SSRF / allowlist 策略约束；上游失败、凭据错误、响应非法分别返回对应的 502 系错误码（见 10.3）。
- 频控 60 次/分钟（租户 + 智能体），超限返回 429 并带 `Retry-After`；健康检查不计入。
- 部署关闭「智能体纳管」开关时该路由不存在，返回**普通 404**（无统一信封）。
- 该端点同样无论总闸是否开启都要求密钥。

## 九、工具参数 tools[]（可选）

助手与工作流的开放调用支持可选 `tools`，语义是**白名单**——列出即"本轮启用"，未列出的一律不启用；**不传**则完全按应用默认配置。

- 条目形状：`{"id": <工具ID>, "tool_key": "<工具key>", "type": "tool"}`；`id` 与 `tool_key` **至少给一个**（优先按 `tool_key` 匹配）。
- 可用清单：助手详情 `GET /api/v2/assistant/info/{assistant_id}` 的 `tool_list`；工作流详情 `GET /api/v2/flows/{flow_id}` 的 `tools`。
- 指定了未绑定/未知的工具 → `403 / 11815`（不会静默忽略）；工作流**续跑**形态下传 `tools[]` → `400 / 11818`（该形态下无法生效，显式拒绝）。

## 十、错误码

鉴权与授权类错误（118xx）返回**真实 HTTP 状态码**（401/403/404/400/500/503），响应体为统一信封：

```json
{
  "status_code": 11807,
  "status_message": "Missing the scope required by this endpoint",
  "data": { "exception": "...", "required": "knowledge:read" }
}
```

- **两个例外，排障时不要按 HTTP 状态判断**：
  - 请求参数缺失或类型错误：HTTP **200**，响应体 `status_code: 422`；
  - 工作流内部错误（如续跑缺 `message_id`）：HTTP **200**，响应体 `status_code: 500`。
- 部署关闭「智能体纳管」开关时，`POST /api/v2/managed-agent/invoke` 返回**普通 404**（FastAPI 默认响应体，无统一信封）。

### 10.1 鉴权与授权（118xx）

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 11801 | 401 | 缺失或格式非法的密钥 | 检查 `Authorization: Bearer bs-sak-…` |
| 11802 | 401 | 密钥无效（查不到） | 核对密钥是否正确、是否已被清理 |
| 11803 | 401 | 密钥过期 | 联系管理员换新密钥 |
| 11804 | 401 | 密钥已吊销 | 联系管理员确认吊销原因 |
| 11805 | 401 | 服务账号停用 / 专用身份不可用 | 联系管理员 |
| 11806 | 403 | 租户停用 | 联系管理员 |
| 11807 | 403 | 缺权限位（`data.required` 回带） | 让管理员把该权限位补进密钥 |
| 11808 | 403 | 资源可见但授权档位不足 | 让管理员提升授权档位 |
| 11809 | 404 | 资源不存在**或**不在授权范围（防枚举） | 确认资源已授权给该账号 |
| 11810 | 400 | 指定的资源所有者是管理员（防提权） | 换非管理员用户 |
| 11811 | 500 | 端点漏标权限位（平台配置错误） | 报障（服务端日志可见） |
| 11812 | 503 | 鉴权依赖不可用（fail-closed） | 稍后重试 |
| 11813 | 400 | 服务账号重名 | 换名 |
| 11814 | 503 | 授权服务不可用 | 稍后重试 |
| 11815 | 403 | `tools[]` 指定了未绑定/未启用的工具（`data.tool_key` 回带） | 见第九节 |
| 11816 | 403 | 服务账号 owner 事后被提权（运行时防提权） | 报障：该账号已停用 |
| 11817 | 400 | 请求里出现裸 `user_id`（身份不可由调用方指定） | 去掉该参数 |
| 11818 | 400 | `tools[]` 在本次调用形态下无法生效（如工作流续跑） | 见第九节 |
| 11819 | 400 | 签发/编辑密钥时指定了未登记的权限位 | 从目录端点取合法 code |
| 11820 | 400 | 授权代管时资源类型/档位不在白名单 | 见第七节 |
| 11821 | 400 | 该用户是服务账号专用身份，不允许在用户侧管理 | 到「服务账号」页操作 |

### 10.2 事件触发（119xx）

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 11907 | 409 | 目标应用不可用（下线/不可调度） | 联系管理员恢复应用 |
| 11910 | 409 | 该任务已有运行在进行 | 等本次运行结束后再触发 |
| 11911 | 429 | 任务级触发频控（默认 10 次/分钟/任务） | 降低频率后重试；响应**不带** `Retry-After` |
| 11912 | 400 | 时间戳超窗/缺失，或 nonce 重放 | 校准系统时钟，每次使用新的 nonce |
| 11916 | 400 | 触发载荷含多余字段 | 只传 `task_id` 与 `inputs` |

### 10.3 纳管智能体调用（121xx）

| 码 | HTTP | 含义 | 对接方动作 |
|---|---|---|---|
| 12105 | 400 | 智能体未上线（草稿/下线/停用） | 联系管理员上线 |
| 12106 | 502 | 上游调用失败或超时 | 稍后重试；持续失败联系管理员 |
| 12107 | 502 | 上游凭据缺失/无效/无法解密 | 联系管理员检查凭据 |
| 12111 | 403 | 出站地址被 SSRF / allowlist 拒绝 | 联系管理员调整出站白名单 |
| 12112 | 429 | 调用频控（默认 60 次/分钟） | 按响应头 `Retry-After` 退避重试 |
| 12113 | 502 | 上游响应非法或超限 | 联系管理员核对上游协议 |
| 12114 | 400 | 调用载荷含非法字段或类型错误 | 见 8.2 |

> 事件触发与纳管调用同样会返回 `11801`（缺密钥）、`11807`（缺权限位）、`11809`（不可见/不存在）、`11812`（依赖不可用）、`11817`（裸 `user_id`）——按 10.1 处理。

## 十一、吊销、过期与停用

- 吊销、编辑、停用后 **≤5 秒**内新调用即被拒（平台侧有短 TTL 缓存）。
- **已建立的 WebSocket 会话一期不主动中断**：进行中的会话继续跑完，之后的新调用按新状态判定。
- 服务账号被停用 = 其**全部密钥立即吊销**（不可逆，重新启用不会恢复它们）。

## 十二、审计

- 每次被鉴权的调用都会留一条审计（密钥掩码、服务账号、权限位、HTTP 状态、业务码、耗时、来源 IP）；管理动作另有前后取值。
- 审计**不含密钥明文或摘要**；凭据本身无效的尝试（11801 / 11802）不写审计，只出现在服务端日志。
- 审计保留建议 **≥ 6 个月**；需要取证时由平台运维查询。

## 十三、调用示例（curl）

```bash
KEY='bs-sak-<43 个字符>'
BASE='{{base_url}}'

# 1) 自检：确认身份 / 权限位 / 有效期（返回统一信封）
curl -s -H "Authorization: Bearer $KEY" "$BASE/api/v2/auth/whoami"

# 2) 助手对话（非流式）
curl -s -X POST "$BASE/api/v2/assistant/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<助手ID>","messages":[{"role":"user","content":"你好"}],"stream":false}'

# 3) 助手对话（流式：stream=true，响应为 SSE）
curl -s -N -X POST "$BASE/api/v2/assistant/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<助手ID>","messages":[{"role":"user","content":"你好"}],"stream":true}'

# 4) 助手对话：指定本轮启用的工具（白名单，清单见 assistant/info 的 tool_list）
curl -s -X POST "$BASE/api/v2/assistant/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<助手ID>","messages":[{"role":"user","content":"查一下最新公告"}],
       "stream":false,
       "tools":[{"id":3,"tool_key":"web_search","type":"tool"}]}'

# 5) 工作流调用（非流式：必须显式传 stream=false；默认 true 为 SSE 流式）
curl -s -X POST "$BASE/api/v2/workflow/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<工作流ID>","input":{"<入参名>":"<入参值>"},"stream":false}'
# → {"status_code":200,"status_message":"SUCCESS",
#    "data":{"session_id":"<会话ID>","events":[...]}}

# 5b) 工作流等待用户输入（INPUT）后续跑：沿用同一 session_id，
#     必须同时传 input 与 message_id，缺任一项返回 HTTP 200 + status_code 500
curl -s -X POST "$BASE/api/v2/workflow/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<工作流ID>","session_id":"<会话ID>",
       "message_id":"<上轮消息ID>","input":{"<入参名>":"<入参值>"},"stream":false}'

# 6) 判断部署总闸是否开启（不要用 whoami：总闸关闭时它同样返回 401/11801）
#    用一把"格式合法但不存在"的密钥调用非免登录端点
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer bs-sak-0000000000000000000000000000000000000000000" \
  "$BASE/api/v2/filelib/"
# 总闸开启：401（业务码 11802）；总闸关闭：旧行为（通常 200 / 业务结果）

# 7) 知识库检索（无 LLM 生成，直接返回命中的文本片段）
curl -s -X POST "$BASE/api/v2/filelib/retrieve" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"<检索问题>","knowledge_base_ids":[<知识库ID>],"top_k":5}'

# 8) 事件触发（完整说明见 8.1）
TS=$(date +%s); NONCE=$(uuidgen)
curl -s -X POST "$BASE/api/v2/schedule/trigger" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H "X-Bs-Timestamp: $TS" -H "X-Bs-Nonce: $NONCE" \
  -d '{"task_id":123}'

# 9) 纳管智能体调用（完整说明见 8.2）
curl -s -X POST "$BASE/api/v2/managed-agent/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"agent_id":29,"input":{"query":"你好"}}'
```

工作流停止（用调用返回的 `session_id`）：

```bash
curl -s -X POST "$BASE/api/v2/workflow/stop" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<工作流ID>","session_id":"<会话ID>"}'
```

WebSocket（密钥放握手头，可用任意 WS 客户端；下面以 wscat 为例；HTTPS 环境把 `ws://` 换成 `wss://`）：

```bash
wscat -c '{{ws_base_url}}/api/v2/assistant/chat/<助手ID>' \
  -H 'Authorization: Bearer bs-sak-<43 个字符>'
```

## 十四、联调自查顺序

1. **401 / 11801** → 请求头没带或格式不对（必须是 `Bearer bs-sak-` + 43 字符）。
2. **401 / 11802 / 11803 / 11804** → 密钥不存在 / 过期 / 已吊销，找管理员在密钥列表核对状态。
3. **403 / 11807** → 响应体 `data.required` 直接告诉你缺哪个权限位。
4. **403 / 11815** → 工具不在该应用的绑定清单里：先取 `tool_list` / `tools` 清单再传。
5. **404 / 11809** → 资源没有授权给这个账号（或确实不存在）——两者不可区分，属预期设计。
6. **400 / 11817** → 请求里出现了 `user_id` 之类的身份参数，去掉。
7. **400 / 11818** → 工作流续跑时不要传 `tools[]`（本轮工具只在新会话生效）。
8. **409 / 11910、429 / 11911、400 / 11912** → 事件触发：运行未结束、触发频控、时间戳超窗或 nonce 重放。
9. **400 / 12105、502 / 12106 / 12107 / 12113、403 / 12111、429 / 12112** → 纳管智能体：未上线、上游失败/凭据/响应非法、出站被拒、频控。
10. **HTTP 200 但 `status_code` 为 422** → 缺必填参数或类型错误；**HTTP 200 但 `status_code` 为 500** → 工作流内部错误（如续跑缺 `message_id`）。
11. 一切正常但结果为空 → 检查调用身份对该资源的授权档位、以及资源本身的数据状态。

## 十五、密钥轮换建议

- **轮换**：先签发新密钥 → 观察一段时间（管理端可看旧密钥 `last_used_at` 是否仍在用）→ 再吊销旧密钥；同一账号允许多把密钥并存。
- **过期**：签发时可不设有效期（`expires_at: null`，适合长期对接），临时对接建议设 90/180 天。
- **应急**：确认密钥泄露时，立即联系管理员吊销该密钥（≤5 秒生效）或停用整个服务账号（一次吊销其全部密钥）。

## 十六、更多接口文档

- Swagger UI：`GET {{base_url}}/docs`
- OpenAPI JSON：`GET {{base_url}}/openapi.json`（含全部 `/api/v2` 路由与 schema，**以它为准**）
- ReDoc：`GET {{base_url}}/redoc`
- 以上三个地址都在应用根下（不带 `/api/v2` 前缀），生产环境同样可访问。
- 接入地址以本页顶部「接入地址」为准；如不确定，按第二节方法向运维确认并验证。
