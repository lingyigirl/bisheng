# F076 开放 API 鉴权 — 改动清单与 API Key 使用说明

> 面向：**对接方开发**（拿密钥调 `/api/v2`）与**平台运维**（签发/吊销/排障）
> 代码锚点：`feat/2.7.0/beijing-ns-076-wip`（Wave 1–3 + 控制台 + 审计 T-27 + e2e T-29 + 真库演练 T-30 已完成）
> 配套文档：`spec.md`（验收标准）、`design.md`（实现决策）、`smoke-report-2026-10-15.md`（真机冒烟）、`docs/v2.7.0-manual-2026-10-19/report.md`（2026-10-19/20 手工验证与修复复验；**本地证据，不入库**）

---

## 一、这套东西解决什么问题

`/api/v2` 开放接口此前**没有任何鉴权**——身份固定为数据库里配置的"默认操作员"（部署侧只能靠网络隔离降低暴露面）。F076 起改为密钥鉴权：

- 每次调用必须携带**服务账号密钥**（`Authorization: Bearer bs-sak-…`），密钥归属明确、权限可裁剪、可过期、可吊销；
- 调用以该服务账号**专用身份**执行，可见范围 = 该身份被授予的资源，**永不继承管理员特权**；
- 调用方可指定**本轮启用哪些工具**（`tools[]`），也可查询应用的可用工具清单；
- 支持**灰色切换**：总闸关闭时行为与升级前逐字一致，存量对接不受影响。

## 二、改动清单（改了什么 → 体现在哪）

### 2.1 数据模型（3 张新表 + 2026-10-22 增量两列）

| 表                          | 用途                 | 关键字段                                                                                                                                                           |
| --------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `service_account`         | 一个对接系统一个账号 | `name`(租户内唯一)/`description`/`resource_owner_user_id`(专用身份)/`disabled_at`/`deleted_at`(软删)                                                     |
| `api_credential`          | 密钥                 | `service_account_id`/`key_prefix`(`bs-sak-`)/`last4`/`token_hash`(SHA-256，**校验唯一依据**)/`token_ciphertext`(Fernet 密文，**仅管理员查看**)/`scopes`/**`allowed_apps`**(每 Key 智能体白名单)/`expires_at`/`revoked_at`/`last_used_at` |
| `open_api_tenant_setting` | 租户级开关           | 每租户一行 `enforce_keys`；**缺行 = 未启用**                                                                                                               |

- 三张表由 SQLModel `create_all` 建；2026-10-22 增量为 `api_credential` 追加两列，走 Alembic revision `f076_api_key_reveal_allowlist`（升级随启动执行）。
- `allowed_apps`：`NULL` = 不限制（沿用账号资源授权）；`[]` = 不调用任何智能体；非空 = 只允许列表内的助手/工作流/纳管智能体。
- 回滚：只回退代码、保留可空新列；**不要** `DROP COLUMN allowed_apps`（会把权限放宽回账号级）。

### 2.2 鉴权链路（体现在这些位置）

| 位置                                                                                               | 作用                                                                                                           |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `bisheng/api/router.py`（`router_rpc`，挂 `dependencies=[Depends(verify_open_api_access)]`） | `/api/v2` 全量路由级鉴权                                                                                     |
| `bisheng/open_api/api/dependencies.py`                                                           | 校验顺序：**总闸 → 凭据 → 账号活性 → 端点权限位 → 身份输入 → owner 装载与防提权 → 租户上下文重建** |
| `bisheng/open_api/domain/scopes.py`                                                              | 端点 ↔ 12 项权限位的硬编码注册表；**漏标的端点会 500/11811**（fail-closed，守卫测试锁死）       |
| `bisheng/main.py`（`handle_open_api_error`）                                                   | 118xx → **真实 HTTP 状态**（不再是平台惯例的 200+信封）                                                 |
| `bisheng/open_endpoints/domain/utils.py`                                                         | 身份助手接缝：运行期把默认操作员替换为服务账号身份（44 个既有端点零改造）                                      |

### 2.2a 2026-10-22 安全收口（权限位拆分与任务归属）

- **ASR/TTS 拆出 `llm:workbench`**：不再由 `assistant:invoke` 覆盖。存量 Key 若用 ASR/TTS，需在密钥编辑中补该位，否则 403/11807。
- **`download_statistic` 拆出 `diagnostics:read`**：不再由 `knowledge:read` 覆盖；该端点默认不建议签发。存量 Key 需显式补位。
- **定时任务触发要求任务归属一致**：`/api/v2/schedule/trigger` 只允许触发**密钥所属服务账号**名下的任务；跨账号任务一律 404/11809（与不存在不可区分）。
- **知识库仍为账号级**：`knowledge:read/write` 覆盖该账号可见的**全部**知识库/空间；每 Key 智能体白名单不适用于知识库。按知识库隔离请拆**服务账号**，不要只拆 Key。

### 2.3 错误码（118 段，23 个；后端三语文案已就绪）

| 码    | HTTP          | 含义                                                                         | 对接方动作                               |
| ----- | ------------- | ---------------------------------------------------------------------------- | ---------------------------------------- |
| 11801 | 401           | 缺失/格式非法密钥                                                            | 检查 `Authorization: Bearer bs-sak-…` |
| 11802 | 401           | 密钥无效（查不到）                                                           | 核对密钥是否被清理/写错                  |
| 11803 | 401           | 密钥过期                                                                     | 换新密钥                                 |
| 11804 | 401           | 密钥已吊销                                                                   | 联系管理员确认吊销原因                   |
| 11805 | 401           | 服务账号停用/专用身份不可用                                                  | 联系管理员                               |
| 11806 | 403           | 租户停用                                                                     | 联系管理员                               |
| 11807 | 403           | 缺权限位（`data.required` 回带）                                           | 让管理员给密钥勾上该权限位               |
| 11808 | 403           | 资源已知可见但操作档位不足                                                   | 提升授权档位                             |
| 11809 | 404           | 资源不存在**或**不在授权范围（防枚举，两者不可区分）                   | 确认资源已授权给该账号                   |
| 11810 | 400           | 指定的资源所有者是管理员（防提权）                                           | 换非管理员用户                           |
| 11811 | 500           | 端点漏标权限位（平台配置错误）                                               | 报障（服务端日志可见）                   |
| 11812 | 503           | 鉴权依赖不可用（fail-closed）                                                | 稍后重试                                 |
| 11813 | 400           | 服务账号重名                                                                 | 换名                                     |
| 11814 | 503           | 授权服务不可用                                                               | 稍后重试                                 |
| 11815 | 403           | `tools[]` 指定了未绑定/未启用的工具（`data.tool_key` 回带）              | 见 §3.4                                 |
| 11816 | 403           | 服务账号 owner 事后被提权（运行时防提权）                                    | 报障：该账号已停用                       |
| 11817 | 400           | 请求里出现裸 `user_id`（身份不可由调用方指定）                             | 去掉该参数                               |
| 11818 | 400           | `tools[]` 在本次调用形态下无法生效（如工作流续跑）                         | 见 §3.4                                 |
| 11819 | 400（管理面） | 签发/编辑密钥时指定了未登记的权限位                                          | 从目录端点取合法 code                    |
| 11820 | 400（管理面） | 资源授权代管时资源类型/档位不在白名单（四类 × viewer/editor/manager）       | 从白名单里选                             |
| 11821 | 400（管理面） | 服务账号的专用身份被拿到用户侧人工管理（改角色/用户组/密码、启停、加入部门） | 到「系统 → 服务账号」里操作             |
| 11822 | 400（管理面） | 签发/编辑密钥时指定的智能体不可用或不在账号授权范围 | 先在「资源授权」中授予该资源             |
| 11823 | 400（管理面） | 该密钥不可查看明文（存量密钥无密文或密文损坏） | 重新签发一把新密钥                       |

### 2.4 管理面 API（挂在 `/api/v1`，权限=超管或当前租户管理员；跨租户一律 404）

```
GET    /api/v1/service-accounts                     列表(keyword/page/page_size,含密钥数与最近使用)
POST   /api/v1/service-accounts                     建号(同事务自动创建专用身份 svc-<名称>)
GET    /api/v1/service-accounts/{id}                详情
PATCH  /api/v1/service-accounts/{id}                改名/描述/换 owner(换 owner 校验非管理员)
POST   /api/v1/service-accounts/{id}/enable|disable 启停(停用即吊销其全部密钥)
DELETE /api/v1/service-accounts/{id}                软删(吊销密钥+停用身份+名字不复用)

GET    /api/v1/service-accounts/{id}/keys           密钥列表(掩码+白名单+revealable+状态)
POST   /api/v1/service-accounts/{id}/keys           签发(可带 allowed_apps;响应含明文)
PATCH  /api/v1/service-accounts/{id}/keys/{key_id}  改名/权限位/智能体白名单/有效期(立即生效)
POST   /api/v1/service-accounts/{id}/keys/{key_id}/reveal   管理员查看明文(写审计;存量密钥 11823)
POST   /api/v1/service-accounts/{id}/keys/{key_id}/revoke   单把吊销
POST   /api/v1/service-accounts/{id}/keys/revoke-all        全量吊销（轮换收尾）
GET    /api/v1/open-api/scopes                      可签发权限位目录(12 项)

GET    /api/v1/service-accounts/{id}/assignable-agents      可加入白名单的智能体(name/level/invocable)
GET    /api/v1/service-accounts/{id}/resource-grants        该账号可见的资源·档位·来源
       └ 行形状 {resource_type, resource_id, level, source};source=direct(代管直接授予,可撤销)
         /inherited(部门/租户等继承路径,此处不可撤销);level 为 null 表示非代管档位(如 owner)
POST   /api/v1/service-accounts/{id}/resource-grants:mutate 授予/撤销资源(viewer|editor|manager)
       └ 撤销只对 source=direct 生效:对继承来的可见性撤销 → 404/11809(没有可直接撤的元组)

GET    /api/v1/open-api/settings                    本租户强制开关(回显部署级/生效态)
PUT    /api/v1/open-api/settings                    切换本租户开关(立即生效)
```

### 2.5 工具参数（`tools[]`）

- 助手与工作流的开放调用支持可选 `tools`：**白名单语义**——列出即"本轮启用"，未列出的一律不启用；**不传 = 完全按应用默认配置**（与升级前一致）。
- 条目形状与平台日间模式一致：`{"id": <工具ID>, "tool_key": "<工具key>", "type": "tool"}`。
- 可用工具清单：助手详情 `GET /api/v2/assistant/info/{id}` 的 `tool_list`；工作流详情 `GET /api/v2/flows/{id}` 的 `tools`（新增字段）。
- 未绑定/未知工具 → **403/11815**（不静默忽略）；工作流**续跑**（`session_id` 处于等待输入/运行中）时传 `tools[]` → **400/11818**（该形态下无法生效，显式拒绝）。

### 2.5a 用户管理里的专用身份（运维须知）

建服务账号时平台会同步创建一个专用身份 `svc-<账号名>`（无角色、无资源授权、不可登录）。它在
「用户管理」列表里**可见**并带「服务账号专用」标注，且角色/密码/启停三个动作全部锁定——刻意为之：

- 改它的角色或用户组 = **给服务账号提权**（违背"零角色零授权"的最小权限前提）；
- 停用它 = 该账号全部调用立刻返回 401/11805；
- 改它的密码没有业务意义（口令由平台随机生成，登录口径按外部身份匹配，它本身登不进来）。

后端在四个入口（改角色、重置密码、启停、用户组替换）与部门成员入口一律返回 **400/11821**；
删除服务账号时该身份会被自动停用（软删、名字不复用）。需要换身份请用服务账号页的"换 owner"。

### 2.6 尚未落地（排期中，对接前先确认）

| 项                             | 状态                                                                                                                                                                      |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 调用审计落库                   | **已可用**（T-27）：被鉴权的调用一条一行（掩码/账号/权限位/HTTP 状态/业务码/耗时/IP），管理动作含操作者与前后取值。**不在平台审计页显示**（只落库取证，见下） |
| 平台端「系统 → 服务账号」页面 | **已可用**（T-23 列表/创建、T-24 概览+密钥、T-24a 资源授权；超管/租户管理员可见）。下方 API 仍是唯一的脚本化途径                                                    |
| WebSocket 会话内密钥重验       | 二期（吊销不影响已建立的连接）                                                                                                                                            |

### 2.7 调用审计怎么查（运维/合规）

审计写在既有 `auditlog` 表，动作名 `open_api.call`（逐调用）与 `open_api.service_account.*` /
`open_api.api_key.*` / `open_api.settings.update`（管理动作）。**刻意不进平台「审计日志」页的可见白名单**——
那里只展示租户/模型/审批等面向业务运营的动作，开放 API 的调用明细属于取证数据，按需从库里查：

```sql
select create_time, action, target_id, operator_id, ip_address, metadata
from auditlog where action = 'open_api.call' order by create_time desc limit 20;
```

`metadata` 里是掩码、权限位、HTTP 状态、业务码、耗时与端点；**永不含密钥明文或摘要**。
两类特别说明：① 凭据本身无效的尝试（401/11801、11802）**不写审计**——没有可归因身份，避免被扫描灌表，
它们只在服务端日志里；② WebSocket 会话记 `method=WS` 与会话时长，拿不到关闭码（连接由会话管理器关闭）。
写入是 best-effort：队列满或落库失败都不影响调用结果，最多丢个别记录。

## 三、如何使用 API Key

### 3.0 端到端流程与角色（谁做什么）

**当前形态**：平台内**没有"申请—审批"工单**——密钥由**管理员直接签发**，角色只有两个：

| 角色                                         | 能做什么                                    | 入口                                              |
| -------------------------------------------- | ------------------------------------------- | ------------------------------------------------- |
| **平台管理员**（超管或当前租户管理员） | 建服务账号、签发/吊销密钥、授予资源、查审计 | 控制台「系统 → 服务账号」（或 §2.4 的管理 API） |
| **对接方**（行内系统开发）             | 持密钥调 `/api/v2`；用 whoami 自检        | 无控制台入口（只持密钥）                          |

**标准流程（四步）**：

1. **对接方提需求**：告知平台管理员——对接系统名、要调用哪些应用/知识库（可照 §3.3 的接口清单提）。
2. **管理员建号**：控制台「系统 → 服务账号」→「创建服务账号」。名称建议直接写对接系统名；平台**自动**为它建专用身份（`svc-*`，零角色零权限，无需手工指定用户）。
3. **管理员签发密钥**：进入该账号 →「密钥」Tab →「签发密钥」→ 勾权限位、按需选择**可调用智能体**、设有效期 → 确认后弹窗显示明文（可复制；必须勾选"已保存"才能关闭）。此后可在密钥列表点「查看」再次获取明文（每次查看写审计；升级前签发的存量密钥无查看密文，需重新签发）。
4. **管理员授予资源**：同一详情页的「资源授权」Tab 里，把该账号可访问的应用/知识库授予它（**默认可见范围为空，不授予就调不通**；工作流建议授"可编辑"档及以上，见下）。
5. **线下交付密钥**：经行内既有安全渠道交给对接方。**不要**用邮件/IM 明文群发——密钥等同口令，一旦泄露等于把该账号的权限交出去。

> **若行内要求"申请—审批"链路**：属**新增需求**（本期未做）。可选方向：接平台既有审批中心出工单，或把"签发"动作纳入运维工单流程——需另行评审后再定。
>
> **轮换**：密钥支持多把并存与有效期；建议按"一系统一账号"分环境（生产/测试分开），到期前用「签发新密钥 → 对接方切换 → 吊销旧密钥」完成轮换。

### 3.1 前置：管理员开启强制鉴权

两个开关**同时打开**才强制（部署级 ∧ 租户级）：

1. **部署级**（配置文件，改后需重启后端）：
   ```yaml
   open_api:
     enforce_keys: true            # 默认 false；false 时完全旧行为（连无效密钥也忽略）
     credential_cache_ttl_seconds: 3   # 吊销生效上界，≤5
   ```
2. **租户级**：`PUT /api/v1/open-api/settings {"enforce_keys": true}`（**缺行=未启用**；写后立即生效）。
   > **灰度说明**：租户级开关只影响**匿名请求**判定（且只看默认租户那一行），带密钥的调用一开总闸就强制——上线顺序是"预发全量验证 → 生产一次性切换"（详见迁移指引 §2）。`GET` 同一路径会回显 `deployment_enforced` 与 `effective`，用来解释"我开了为什么没生效"——强制 = 两者**同时**为真。
   >

**免登录面**（平台自己的分享页依赖的 8 个端点）由 `enable_guest_access` 单独控制：开着时这些端点匿名可用；关掉则与其余端点一视同仁。

### 3.2 建服务账号并签发密钥（管理员，curl）

```bash
# 1) 建服务账号(自动创建专用身份 svc-对接系统,无任何权限)
curl -X POST http://<host>:7860/api/v1/service-accounts \
  -H "Authorization: Bearer <管理员JWT>" -H 'Content-Type: application/json' \
  -d '{"name":"信贷系统","description":"信贷系统对接"}'
# → {"id":1,...,"resource_owner_user_id":95,"enabled":true,"key_count":0}

# 2) 签发密钥(可带 allowed_apps 限定可调用的智能体;null/不传=不限制,[]=全拒绝)
curl -X POST http://<host>:7860/api/v1/service-accounts/1/keys \
  -H "Authorization: Bearer <管理员JWT>" -H 'Content-Type: application/json' \
  -d '{"name":"生产","scopes":["assistant:invoke","workflow:invoke","knowledge:read"],
       "allowed_apps":[{"type":"workflow","id":"<工作流ID>"}],"expires_at":null}'
# → {"id":1,"key_mask":"bs-sak-********AbCd","plaintext":"bs-sak-<43字符>","scopes":[...],
#    "allowed_apps":[{"type":"workflow","id":"..."}],"expires_at":null}

# 2b) 以后查看明文(管理员;写审计;存量密钥返回 400/11823)
curl -X POST -H "Authorization: Bearer <管理员JWT>" \
  http://<host>:7860/api/v1/service-accounts/1/keys/1/reveal
# → {"id":1,"key_mask":"bs-sak-********AbCd","plaintext":"bs-sak-<43字符>",...}

# 2c) 可分配智能体目录(密钥编辑器的选项来源)
curl -H "Authorization: Bearer <管理员JWT>" \
  http://<host>:7860/api/v1/service-accounts/1/assignable-agents
# → {"data":[{"type":"workflow","id":"...","name":"报销流程","level":"editor","invocable":true}],"total":1}
```

**把资源授予这个账号**（服务账号的可见范围 = 专用身份 `svc-对接系统` 在平台权限体系里的授权）：

```bash
# 查看该账号现有资源与档位
curl -H "Authorization: Bearer <管理员JWT>" \
  http://<host>:7860/api/v1/service-accounts/1/resource-grants

# 授予(资源类型:knowledge_library|knowledge_space|assistant|workflow;档位:viewer|editor|manager)
curl -X POST http://<host>:7860/api/v1/service-accounts/1/resource-grants:mutate \
  -H "Authorization: Bearer <管理员JWT>" -H 'Content-Type: application/json' \
  -d '{"grants":[{"resource_type":"assistant","resource_id":"<助手ID>","level":"viewer"}],
       "revokes":[]}'
```

> 代管权限受两道闸保护:执行的管理员**必须自己对目标资源有 manager 权限**,否则 403/11808;资源类型与档位在白名单外为 400/11820。

### 3.3 对接方调用（curl）

```bash
KEY='bs-sak-<43字符>'; BASE=http://<host>:7860

# 自检(确认密钥是谁、能干什么、何时到期;无需权限位)
curl -H "Authorization: Bearer $KEY" $BASE/api/v2/auth/whoami
# → {"account_id":1,"account_name":"信贷系统","key_mask":"bs-sak-********AbCd",
#    "scopes":[...],"allowed_apps":null,"expires_at":null,"tenant_id":1}

# 调智能体应用(助手)
curl -X POST $BASE/api/v2/assistant/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<助手UUID>","messages":[{"role":"user","content":"你好"}],"stream":false}'

# 指定本轮启用的工具(白名单;可用清单见 assistant/info 的 tool_list)
curl -X POST $BASE/api/v2/assistant/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"<助手UUID>","messages":[{"role":"user","content":"查一下最新公告"}],
       "tools":[{"id":3,"tool_key":"web_search","type":"tool"}]}'
```

> 注意：**不要**再传 `user_id` 之类的身份参数（400/11817）；如需"代表某员工执行"，属受控代表调用，二期提供。

**三层权限同时满足才放行**：

1. **密钥权限位**（`scopes`）：能调哪一类端点（如 `workflow:invoke`）；
2. **每 Key 智能体白名单**（`allowed_apps`）：能调其中的哪些具体智能体（`null`=不限制，`[]`=全拒绝）；
3. **服务账号资源授权**：这个账号身份对目标资源有足够档位。

同一账号下的不同 Key 可以设置不同的 `allowed_apps`，因此"Key1 只能调工作流 A、Key2 只能调工作流 B"是支持的。白名单未命中与资源不可见同码 `404 / 11809`。

**怎么知道调哪个地址、哪个接口、什么方法？**

- **地址（IP/域名 + 端口）**：由行内运维提供——要么后端直连地址（如 `http://<行内后端IP>:7860`），要么商业版网关地址。密钥里**不含**地址，同一把密钥在不同环境用的是不同地址。
- **接口与方法**：`/api/v2` 的鉴权与错误码见 [docs/api/open-api-auth.md](../../../docs/api/open-api-auth.md)；具体接口（路径 + 方法 + 入参/出参 + 示例）见 [docs/api/filelib-retrieve.md](../../../docs/api/filelib-retrieve.md)（纯检索）与 [docs/api/知识库接口文档.md](../../../docs/api/知识库接口文档.md)（知识空间/知识库）。方法以接口文档为准（检索/对话是 `POST`，会话历史是 `GET`）。
- **权限位 → 能调哪些端点**：签发页的权限位选项即目录（也可 `GET /api/v1/open-api/scopes` 取）；缺失时返回 403/11807 并回带 `data.required`，让管理员把该位补进密钥即可（≤5 秒生效）。
- **联调第一步**：调 `GET /api/v2/auth/whoami`，它会回显"你是谁、有哪些权限位、密钥何时到期"——比对着报错猜快得多。

### 3.4 失败自查顺序

1. **401/11801** → 头没带或格式不对（必须是 `Bearer bs-sak-` + 43 字符）；
2. **401/11802/11804/11803** → 密钥不存在/被吊销/过期 —— 找管理员在密钥列表核对 `status`；
3. **403/11807** → 报错体 `data.required` 直接告诉你缺哪个权限位；
4. **403/11815** → 工具不在该应用绑定清单里：先 `assistant/info` 或 `flows/{id}` 拿清单；
5. **404/11809** → 资源没授权给这个账号（或确实不存在）；（AC-23 的 404/11809 防枚举已收口并由 e2e 覆盖；对接方仍应把 403 与 404 都当作"未授权/不可见"处理）
6. **400/11818** → 工作流续跑时不要传 `tools[]`（本轮工具只在**新会话**生效）。

### 3.5 密钥生命周期实践

- **轮换**：先签新密钥 → 观察一段时间（`last_used_at` 可判旧密钥是否仍在用）→ 再吊销旧密钥；同账号**允许多把并存**。白名单是**每把 Key 独立**的，签发新 Key 时记得复制旧 Key 的 `allowed_apps`。
- **白名单调整**：编辑密钥的 `allowed_apps` 后 ≤5 秒生效（清缓存 + TTL 兜底）；新增条目要求资源对该账号可见，否则 400/11822。
- **吊销**：单把用 `/keys/{key_id}/revoke`，收尾用 `/keys/revoke-all`；**吊销即时生效**（清缓存，最坏 3 秒）。
- **过期**：`expires_at` 不传 = 不过期（推荐给长期对接）；给临时对接设 90/180 天。
- **应急**：停用服务账号（`disable`）会一次吊销其全部密钥；总闸关掉 `enforce_keys=false` 改回配置并**重启**即回到旧行为（不是热生效，重启窗口按常规运维流程评估）。

### 3.6 管理员排障速查

- 服务端日志：`data/logs/backend.log`，搜 `open_api:` 前缀（建号/签发/吊销/代管变更/提权拒绝都有记录）；
- 调用方报"没有权限"：先看对方密钥的 `scopes`（目录端点对码），再看 `svc-*` 用户在资源上的授权；
- 报 11811（500）：说明有人加了 `/api/v2` 端点但漏标权限位——查 `open_api/domain/scopes.py` 注册表。
