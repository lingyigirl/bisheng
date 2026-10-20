# 开放 API 接入指南

本文档供**行内对接系统的开发人员**使用：通过 API Key 调用本平台的智能体应用、工作流与知识库接口。

文中出现的 `{{base_url}}` 会在查看与导出时自动替换为页面顶部的「接入地址」；示例中的密钥均为占位符，请以管理员实际交付的为准。

## 一、三步开始

1. **自检**：拿到密钥后先调 `GET {{base_url}}/api/v2/auth/whoami`，确认身份、权限位与有效期（见第五节）。
2. **调用**：在请求头携带 `Authorization: Bearer <密钥>`，调用第八节的接口。
3. **排障**：失败先查第十节错误码表，再按第十四节的顺序自查。

## 二、前置条件：平台已开启强制鉴权

只有**部署级**与**租户级**两个开关同时打开时，密钥鉴权才真正生效（`enforce_keys`）。**未开启时密钥会被静默忽略**：不带密钥的调用照样可用，带了密钥也不会按密钥所属身份与权限位执行。

- 两个开关由平台管理员/运维开启，状态请与管理员确认，不要以本文档为准。
- 一个快速的现场判断：用一个**明显错误**的密钥调用 `whoami`。若仍返回业务结果而不是 `401 / 11802`，说明强制鉴权未生效（见第十三节示例 6）。

## 三、密钥怎么来、怎么保管

- 密钥由**平台管理员**在控制台「系统 → 服务账号」中建号并签发，经行内安全渠道线下交付；平台不提供自助申请与工单流程。
- **密钥格式**：`bs-sak-` 开头 + 43 个随机字符，共 50 个字符。
- **明文只在签发时显示一次**：平台只保存不可逆摘要，此后任何接口、界面、日志、审计都无法取回明文；遗失只能重新签发。
- 平台列表与审计中密钥显示为掩码（如 `bs-sak-********AbCd`），用于对账与轮换识别。
- 建议**一个对接系统（一个环境）一个服务账号**：生产与测试分开，便于单独吊销、单独审计。

## 四、怎么携带密钥

```http
Authorization: Bearer bs-sak-<43 个字符>
```

- **WebSocket** 同样在**握手请求头**中携带，不使用查询参数。
- **禁止**把密钥放进 URL 查询串（`?api_key=...` 之类）：请求行会被访问日志原样记录，等同于泄露；平台也不接受这种形式。
- **不要**在请求中传 `user_id` 等身份参数：调用身份完全由密钥决定，传了会被拒绝（`400 / 11817`）。
- 调用**不代表**任何具体用户：密钥对应平台为该服务账号自动创建的专用身份（`svc-*`，零角色零权限），**不继承任何管理员权限**。

## 五、先自检：这个密钥能干什么

```bash
curl -H "Authorization: Bearer $KEY" {{base_url}}/api/v2/auth/whoami
```

返回该密钥的账号名、掩码、权限位与有效期，例如：

```json
{
  "account_id": 1,
  "account_name": "信贷系统",
  "key_mask": "bs-sak-********AbCd",
  "scopes": ["assistant:invoke", "knowledge:read"],
  "expires_at": null,
  "tenant_id": 1
}
```

该端点**不需要任何权限位**，是联调第一步——比对着报错猜快得多。

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
| `delegate` | 预留位：本期可签发但**不产生任何生效行为** |
| `schedule:trigger` | 触发定时任务（事件触发端点 `POST /api/v2/schedule/trigger`） |
| `managed_agent:invoke` | 调用已纳管的第三方智能体（`POST /api/v2/managed-agent/invoke`） |

- **隐含规则**：授予 `knowledge:write` 即同时具备 `knowledge:read`。
- 权限位在签发/编辑密钥时由管理员选择；需要补位时联系管理员（编辑后 ≤5 秒生效）。

## 七、资源可见范围

- 密钥能访问哪些**应用、工作流、知识库**，取决于该服务账号的**资源授权**：默认**为空**，需要管理员在服务账号详情页「资源授权」中授予（档位：`viewer` / `editor` / `manager`）。
- 资源不在授权范围、或资源不存在，**都返回 `404 / 11809`**（防枚举，两者不可区分）。
- **实现建议**：把 `403` 与 `404` 都当作"未授权/不可见"处理，据此做降级提示，不要用来判断资源是否存在。

## 八、常用端点速查

| 能力 | 方法与路径 | 需要的权限位 |
|---|---|---|
| 凭据自检 | `GET /api/v2/auth/whoami` | 无（仅需密钥） |
| 助手对话（含流式） | `POST /api/v2/assistant/chat/completions` | `assistant:invoke` |
| 助手信息 | `GET /api/v2/assistant/info/{assistant_id}` | `assistant:read` |
| 助手列表 | `GET /api/v2/assistant/list` | `assistant:read` |
| 助手对话（WebSocket） | `WS /api/v2/assistant/chat/{assistant_id}` | `assistant:invoke` |
| 工作流调用 / 停止 | `POST /api/v2/workflow/invoke` · `POST /api/v2/workflow/stop` | `workflow:invoke` |
| 工作流详情 | `GET /api/v2/flows/{flow_id}` | `workflow:read` |
| 工作流会话（WebSocket） | `WS /api/v2/workflow/chat/{workflow_id}` | `workflow:invoke` |
| 知识库检索 | `POST /api/v2/filelib/retrieve` | `knowledge:read` |
| 知识库 / 文件列表 | `GET /api/v2/filelib/` · `GET /api/v2/filelib/file/list` | `knowledge:read` |
| 会话历史 | `GET /api/v2/chat/history` | `chat:invoke` |
| 引用详情 | `GET /api/v2/citation/{citation_id}` | `knowledge:read` |

以上路径前都需拼上接入地址，例如 `{{base_url}}/api/v2/auth/whoami`。表中未列出的端点同样遵循同一套鉴权与错误码规则；完整接口契约（入参、出参、返回示例）以随交付的接口文档为准。

## 九、工具参数 tools[]（可选）

助手与工作流的开放调用支持可选 `tools`，语义是**白名单**——列出即"本轮启用"，未列出的一律不启用；**不传**则完全按应用默认配置。

- 条目形状：`{"id": <工具ID>, "tool_key": "<工具key>", "type": "tool"}`。
- 可用清单：助手详情 `GET /api/v2/assistant/info/{assistant_id}` 的 `tool_list`；工作流详情 `GET /api/v2/flows/{flow_id}` 的 `tools`。
- 指定了未绑定/未知的工具 → `403 / 11815`（不会静默忽略）；工作流**续跑**形态下传 `tools[]` → `400 / 11818`（该形态下无法生效，显式拒绝）。

## 十、错误码

鉴权与授权失败返回**真实 HTTP 状态码**（401/403/404/400/500/503），响应体为统一信封：

```json
{
  "status_code": 11807,
  "status_message": "Missing the scope required by this endpoint",
  "data": { "exception": "...", "required": "knowledge:read" }
}
```

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

# 1) 自检：确认身份 / 权限位 / 有效期
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

# 5) 工作流调用（非流式；stream 默认 true 为 SSE 流式）
curl -s -X POST "$BASE/api/v2/workflow/invoke" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<工作流ID>","input":{"<入参名>":"<入参值>"},"stream":false}'
# → {"session_id":"<会话ID>","events":[...]}；session_id 用于续跑与停止

# 6) 用错误密钥验证强制鉴权是否生效：期望 401 / 11802，若返回业务结果说明未开启
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer bs-sak-0000000000000000000000000000000000000000000" \
  "$BASE/api/v2/auth/whoami"

# 7) 知识库检索（无 LLM 生成，直接返回命中的文本片段）
curl -s -X POST "$BASE/api/v2/filelib/retrieve" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"query":"<检索问题>","knowledge_base_ids":[<知识库ID>],"top_k":5}'
```

工作流停止（用调用返回的 `session_id`）：

```bash
curl -s -X POST "$BASE/api/v2/workflow/stop" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<工作流ID>","session_id":"<会话ID>"}'
```

WebSocket（密钥放握手头，可用任意 WS 客户端；下面以 wscat 为例）：

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
8. 一切正常但结果为空 → 检查调用身份对该资源是否只是 `viewer` 档位、以及资源本身的数据状态。

## 十五、密钥轮换建议

- **轮换**：先签发新密钥 → 观察一段时间（管理端可看旧密钥 `last_used_at` 是否仍在用）→ 再吊销旧密钥；同一账号允许多把密钥并存。
- **过期**：签发时可不设有效期（`expires_at: null`，适合长期对接），临时对接建议设 90/180 天。
- **应急**：确认密钥泄露时，立即联系管理员吊销该密钥（≤5 秒生效）或停用整个服务账号（一次吊销其全部密钥）。

## 十六、更多接口文档

本文档覆盖鉴权、错误码与常用端点。各接口的完整入参、出参与返回示例，以随交付的接口文档为准；接入地址以本页顶部「接入地址」为准。
