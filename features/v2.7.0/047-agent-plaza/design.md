# Design: 智能体广场（F047）

> **本文档定位 — 现状快照（Why this How）**。spec 答 What，本文答 Why/How，tasks.md 是流水账。
> 实现变化 → 覆盖更新本文档（只留今天的状态）；推翻 ★ 已确认决策 → 停下与用户重新确认。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md) · [discovery.md](./discovery.md)
**版本**: v2.7.0
**最后更新**: 2026-09-01

---

## 1. 目标与非目标

- **目标**：客户端工作台提供一个「智能体广场」——终端用户一站式发现（搜索/多维筛选）、了解（预览+访问统计）、使用（进入会话）、留存（收藏/最近使用）已上线的智能体应用。
- **非目标**：
  - 不做上架/下架管理（D1 决策：沿用 ONLINE+view_app 口径，无 on_public）
  - 不做 App Center（/apps）改造；不碰应用内会话历史
  - 不做排行榜区块、不做平台侧管理 UI
  - 不改权限模型（不新增权限位）

## 2. 关键约束

> 全局铁律遵循 `docs/constitution.md` C1–C7；版本契约 `features/v2.7.0/release-contract.md`（F047 已登记 user_app_favorite 与统计只读端点）。

- **统计延迟 ≤ 5 分钟**：数据源 `mid_agent_run_dtl`（F046）由 beat 每 5 分钟增量同步；广场统计与排序共用该口径（AC-21）。
- **遥测无 tenant_id**：`application_process` 事件与 mid 表都不带租户字段（F046 call-logs 服务通过「租户用户 id 列表 + user_context.user_id terms 过滤」实现租户隔离，见 `telemetry_search/domain/services/call_logs.py`）。广场统计取**全局口径**（跨租户聚合），理由见决策 2。
- **可见应用集规模**：广场按 view_app 过滤后单租户应用数通常在百级以内 → 「全量可见 id + 单次 ES 聚合 + 内存排序分页」可行（决策 3）。
- **前端技术栈冻结**：client 端 Recoil 冻结、react-query v4 为唯一新数据获取方式；UI 用 shadcn 已有组件；i18n 三语同 PR；文件 ≤ 600 行。
- **错误码归属**：收藏/广场新错误码定义于 `common/errcode/workstation.py`（workstation 段 120xx，C5）；spec 不维护错误码详表，AC 中按「可观测行为」引用。

## 3. 方案对比与选定

### 决策 1：广场列表接口 — 新增专用端点 vs 扩展现有 /chat/online

- **备选**：
  - A. 扩展 `GET /api/v1/chat/online`（加 `tag_ids` 多选、`sort_by=visit_count`）— 少一个新端点；但给共享端点加广场特有语义（访问量排序需 ES join），回归面大（explore、App switcher、推荐选择器都依赖它），且 chat 模块要懂统计。
  - B. 新增 `GET /api/v1/workstation/app/plaza` — 广场语义内聚在 workstation 模块（常用/历史/置顶都在此），复用 `WorkFlowService.get_all_flows` 的权限过滤与分页骨架；`/chat/online` 零改动。
- **选定**：B
- **原因**：收藏/最近使用/统计都在 workstation 模块，广场入口收口到这里职责最清晰；共享端点零回归。成本是 ~100 行新端点代码，复用 get_all_flows 后增量可控。
- **何时该重新考虑**：其他端也需要「访问量排序」这类广场能力时，抽公共 service 而不是扩 /chat/online。

### 决策 2：统计实现 — ES 直查 mid_agent_run_dtl vs 复用 dashboard 查询引擎

- **备选**：
  - A. ES 直查 `mid_agent_run_dtl`（terms agg by app_id + 状态过滤），仿 F046 call-logs 服务的查询模式 — 简单直接、口径与 F046 数据集一致。
  - B. 复用 telemetry_search 的 dashboard 组件查询引擎（component data_config）— 通用但把广场耦合到平台看板配置体系，需要 dataset_code + data_config 模板，过重。
- **选定**：A
- **原因**：统计口径 = F046 的 run_count/成功率/avg_process_time 定义，直接对该索引聚合即得到一致结果；无 dashboard 配置依赖。
- **租户口径**：统计为**全局聚合**（不按租户用户过滤）。理由：应用本身租户隔离，跨租户访问几乎不存在；且 mid 表无 tenant_id，按 user 过滤会漏掉访客/分享链接产生的访问，口径反而失真。与平台看板（F046）口径一致。
- **何时该重新考虑**：出现跨租户访问统计污染诉求（客户明确要租户内口径）时，改用 call-logs 式 user 过滤。

### 决策 3：「最多访问」排序 — 全量可见 id 一次聚合 vs 每页查

- **备选**：
  - A. 列表服务先取全部可见应用 id（`get_all_flows(skip_pagination=True)`），一次 ES terms agg 拿到每个 app 的近 7 天访问量，Python 内存按访问量排序后分页 — 正确且一次聚合。
  - B. 每页只对当页应用查统计再排序 — 排序域只有一页，错误；或 ES 直接排序分页 — 会破坏 view_app 权限过滤（ES 不知道权限）。
- **选定**：A
- **原因**：权限过滤只能在后端 MySQL 侧做（ReBAC），排序键在 ES —— 必须「先取全量可见 id → ES 聚合 → 内存排序 → 分页」。可见集百级，单次聚合毫秒级。
- **何时该重新考虑**：可见应用数过万且访问量排序成热点时，把访问量快照同步到 MySQL 列（加缓存表），不再实时聚合。

### 决策 4：收藏存储 — 新表 user_app_favorite vs 复用 UserLink

- **备选**：
  - A. 新表 `user_app_favorite`（user_id + app_id + app_type + tenant_id，唯一约束防重，仿 `user_link.py` 模式 + 补 tenant_id）— 语义独立、幂等由唯一约束保证。
  - B. 复用 `UserLink`（type=used_app_pin 同款机制）— 与「置顶」混在一条表里，且 UserLink 无 tenant_id，语义与排查都会互相污染。
- **选定**：A
- **原因**：收藏是用户主动、跨端可见的语义，与自动「常用」、手动「置顶」并列但不混存；C3 要求租户隔离（UserLink 缺 tenant_id 是既有债，不学）。
- **何时该重新考虑**：出现更多「用户对应用的关系」类型时，评估统一成 user_app_relation(type) 表。

### 决策 5：预览抽屉数据 — 复用既有详情接口 vs 新增详情端点

- **备选**：
  - A. 复用 `getFlowApi` / `getAssistantDetailApi`（chat 入口同款）+ 新增统计端点 — 详情字段零新契约，抽屉只需补统计。
  - B. 新增 plaza 专用详情端点 — 重复字段组装，无收益。
- **选定**：A
- **原因**：应用详情（名称/描述/标签/图标/版本）已有成熟返回结构；统计单独走只读聚合端点。
- **何时该重新考虑**：详情需返回广场特有字段（如运营语）时再加。

### 决策 6：前端数据层 — react-query vs Recoil

- **备选**：
  - A. react-query v4：列表 `useInfiniteQuery`、收藏 `useMutation` + invalidation、统计随列表响应返回 — 符合 client 端冻结 Recoil 的规则，缓存/重试/竞态免费。
  - B. 新增 Recoil 原子/selector 管理广场状态 — 违反冻结规则（新代码禁用），且要手写请求状态。
- **选定**：A
- **原因**：规则约束 + 收藏状态需要在卡片/抽屉/收藏区块三处一致，react-query cache key 统一天然满足。

### 决策 7：标签多选语义 — OR（任一选中标签）

- **备选**：
  - A. OR：应用命中任一选中标签即入列表 — 与后端 `TagDao.get_resources_by_tags` 的 `tag_id.in_(...)` 原生语义一致（资源并集），改动最小。
  - B. AND：应用必须同时具有所有选中标签 — 筛选更严，但后端要按 TagLink 分组求交集，且典型广场交互（同类标签多选）预期是并集。
- **选定**：A
- **原因**：AC-06 已按 OR 语义验收（★ 已确认）；后端原生支持。
- **何时该重新考虑**：用户反馈「多选反而找不到」时再评估 AND 或分组逻辑。

## 4. 系统现状（接手必读）

### 4.1 数据流

**广场页加载**：
`client /apps/explore（改造）` → `GET /api/v1/workstation/app/plaza`（keyword/flow_types/tag_ids/sort_by/page/limit）→ `WorkStationService.plaza_list()`：`get_all_flows`（view_app 过滤 + 分页骨架，`api/services/workflow.py`）→ ES `mid_agent_run_dtl` 聚合（近 7 天访问量）→ 按 sort_by 排序分页 → `add_extra_field` 富化（user_name/logo/tags）→ 响应含每应用 stats。

**预览**：点卡片 → 抽屉 → `getFlowApi`/`getAssistantDetailApi`（现有，`client/src/api/apps.ts:31/56`）+ `GET /api/v1/workstation/app/stats/{app_id}`（只读聚合：7 天/累计访问量、成功率、平均响应时间）→ 「开始对话」→ 现有会话进入流程（`use_app` 预检在 appChat init 已有）。

**收藏**：`POST/DELETE /api/v1/workstation/app/favorite`（body: app_id, app_type）→ `FavoriteRepository`（user_id+app_id+app_type 唯一）→ 幂等；`GET /api/v1/workstation/app/favorites` → 收藏 id 列表 → 复用 `get_all_flows` 富化 + 状态过滤（已下架/不可见不返回，AC-16）。

**最近使用**：复用 `GET /api/v1/workstation/app/used`（`MessageSessionDao.get_user_used_apps`，pinned-first），取前 10。

**统计数据链路**（F046 已建）：应用运行结束 → `application_process` 事件写 `base_telemetry_events` → beat 每 5 分钟同步 `mid_agent_run_dtl`（`worker/telemetry/mid_table.py`）→ 广场只读聚合。

### 4.2 对外契约（API）

| 接口 | 形式 | 说明 |
|---|---|---|
| `GET /api/v1/workstation/app/plaza` | query: keyword, flow_types(list), tag_ids(list), sort_by(recent/update_time/visit_count), page, limit | 广场列表；返回 `{data:[{app 富化字段 + stats:{recent_7d_visits, total_visits, success_rate, avg_process_time}}], total}` |
| `GET /api/v1/workstation/app/stats/{app_id}` | path + query: days=7 | 单应用统计（预览抽屉用），同口径 |
| `POST /api/v1/workstation/app/favorite` | body: {app_id, app_type} | 收藏（幂等） |
| `DELETE /api/v1/workstation/app/favorite` | body: {app_id, app_type} | 取消收藏（幂等） |
| `GET /api/v1/workstation/app/favorites` | query: page, limit | 我的收藏列表（含应用富化，过滤不可见） |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `workstation/api/endpoints/plaza.py`（新，独立 router） | 上述 5 个端点入口（不扩展 apps.py：共享落地页端点零回归面、测试可独立挂载） | 不写 ORM/ES 查询 |
| `workstation/domain/services/plaza_service.py`（新） | 列表编排（权限→统计→排序→分页→富化）、收藏业务、统计聚合 | 不直接操作 session/ORM（走 repository/service） |
| `workstation/domain/models/user_app_favorite.py`（新） | SQLModel 表（唯一约束） | 无业务逻辑 |
| `workstation/domain/repositories/favorite_repository.py`（新） | 收藏 CRUD | 不做权限判断（上层做） |
| `workstation/domain/services/plaza_stats.py`（新，或并入 plaza_service） | ES 聚合查询（terms agg / 单应用过滤聚合） | 不写遥测 |
| `client/src/pages/apps/explore.tsx`（改造）+ `components/plaza/`（新） | 广场 UI：筛选条、预览抽屉、收藏/最近使用区块、卡片 badge | 不发 HTTP（走 `src/api/apps.ts`） |
| `client/src/api/apps.ts`（扩展） | 上述 5 接口的封装 | — |

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | 遥测事件与 mid 表**无 tenant_id**，统计是全局口径；应用租户隔离靠 view_app 权限过滤（MySQL 侧） | 想把统计做成租户内口径时发现无字段可过滤；或误以为统计会泄露跨租户数据 | 决策 2；plaza_stats 聚合不含租户过滤 |
| 2 | viewer ≠ use_app：广场能看到的应用（view_app）点「开始对话」可能被 use_app 预检拦下 | 以为列表可见=能进会话，测试时困惑 | 沿用 appChat init 的既有 checkPermission 流程，AC-10 明确不新增分支 |
| 3 | `/app/used` 返回 pinned-first 且含已下架过滤前的原始最近使用（按 flow_id 去重） | 「最近使用」区块直接展示可能混入置顶语义，展示口径要重新定（只取 top10 按时间倒序） | plaza_service 取 used 结果前 10，忽略 pin 排序 |
| 4 | `sort_by=visit_count` 排序必须「全量可见 id → ES 聚合 → 内存排序」，不能 ES 直接排序分页 | ES 排序会绕过 view_app 权限过滤，泄露不可见应用 | 决策 3 |
| 5 | SQLAlchemy 普通 JSON 列原地改 dict 不触发 UPDATE（本次会话在看板布局上刚踩过） | 收藏/配置类 JSON 字段更新静默丢失 | 收藏表不用 JSON 列；如未来加 JSON 字段，赋值新对象 |
| 6 | client i18n `useLocalize()` 返回函数身份不稳定（CLAUDE.md pitfall） | useEffect 依赖 t 导致循环请求 | 广场组件依赖数组不用 t，或 useCallback 包一层 |
| 7 | 新应用 7 天内无访问 → 聚合结果缺 key，排序时按 0 处理排最后 | 新应用在「最多访问」排序下消失或排序异常 | plaza_service 对缺失 app_id 补 0 |
| 8 | **应用的 id 类型不统一**：工作流 id 是 UUID 字符串，助手 id 是 int（e2e 实测抓到） | 收藏表 app_id 用 Integer 会存不下工作流，收藏直接失败 | user_app_favorite.app_id 统一存 **VARCHAR(64)**；repository 入参 int\|str 归一为 str；前端/API 全程按字符串透传 |

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `/api/v1/workstation/app/plaza` 及上述 4 端点 | HTTP API | client 前端（广场页） |
| `user_app_favorite` 表 | 数据 | 仅本 Feature 写（release-contract 表 1 已登记）；未来 App Center 收藏改造可读 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `mid_agent_run_dtl` 中间表（F046） | ES 索引，5 分钟同步 | 同步中断时统计滞后（AC-21 已按 ≤5 分钟承诺）；F046 改口径时需联动 |
| `WorkFlowService.get_all_flows` | 内部 Python API（权限过滤+分页骨架） | 其签名/返回结构变化会影响 plaza_list |
| `MessageSessionDao.get_user_used_apps` + `/workstation/app/used` | 内部 API / HTTP | pinned-first 语义（坑 #3） |
| `TagDao.get_resources_by_tags` | 内部 Python API | 多 tag `in_` 语义 = OR（决策 7 依赖此语义） |
| `getFlowApi` / `getAssistantDetailApi` 返回结构 | 隐式数据契约（详情字段） | 详情字段变化影响预览抽屉渲染 |

## 7. 测试与可观测

- **后端单测**（`test/workstation/`）：收藏 CRUD 幂等与隔离（跨用户/跨租户）、plaza 列表筛选组合与排序、统计聚合口径（构造 mid 表数据）、幽灵应用过滤（AC-16）。
- **前端组件测试**（Vitest）：筛选条交互（类型/标签多选/排序联动）、预览抽屉状态、收藏按钮乐观更新。
- **e2e**（`/e2e-test features/v2.7.0/047-agent-plaza`）：API 链路（登录 → 收藏 → 列表/收藏区/最近使用可见性 → 统计字段存在）+ 页面手动验证清单（空态、无权限应用不展示、抽屉加载失败重试）。
- **手动验证**：`https://172.19.70.141:4001/workspace/apps/explore`，用 admin + 普通用户各跑一遍；验证统计数字与平台看板 F046 指标一致。
- **日志**：plaza 列表/统计查询的耗时日志（loguru），统计聚合失败降级为空统计（不阻塞列表）。

## 8. 后续改进 / 不打算做的事

- 访问量快照入 MySQL（应用数规模大时替换实时聚合，见决策 3 触发条件）
- App Center 收藏/统计改造（另起 feature）
- 上架开关、排行榜区块（spec 排除项）

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-01 | 初版 | spec 评审通过后 design 编写 |
