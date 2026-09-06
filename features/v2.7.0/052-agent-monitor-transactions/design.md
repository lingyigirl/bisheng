# Design: 智能体监控页签（按交易监控 + 节点出入参可视化）

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-05

---

## 1. 目标与非目标

- **目标**：在 /log 页新增「智能体监控」页签——按交易（一次运行 = 一条交易）监控智能体调用，交易列表携带**输入快照**作为交易标识；交易详情展示节点执行视图（状态/起止/耗时/失败原因）与节点**输入/输出参数的结构化可视化**，以及运行内模型调用明细。
- **非目标**：灵思智能体节点详情（交易列表可见）、历史节点明细回填、结构化业务字段抽取、告警/导出、自动轮询（手动刷新）。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7。
- **纯 What 与 How 的边界**：spec.md 已定验收口径；本文只做实现决策。
- **不修改 F046 的任何领域对象**（release-contract 表 1）：F046 的 call-logs 端点、`mid_agent_run_dtl`、`application_process.status` 均不动；F052 只读其事件与租户过滤范式。
- **既有聊天历史链路零扰动**：节点执行记录独立存储，`/api/v1/chat/history` 等接口返回不变（AC-05）。
- **时区口径**：所有新增的时间字段用 DB 时钟（`server_default=CURRENT_TIMESTAMP`），**禁止** Python `datetime.now()` 写时间戳（本机曾因 worker TZ=UTC vs MySQL CST 差 8 小时——见 2026-09-05 排查记录）。

---

## 3. 方案对比与选定

### 决策 1：节点执行记录的持久化位置

- **备选**：
  - A. 新建独立表 `workflow_node_run`（MySQL）
  - B. 写入 `chatmessage`（category=`node_run`，沿用既有表与历史接口）
  - C. 写入 ES 新索引
- **选定**：A
- **原因**：B 会让既有聊天历史接口（`/api/v1/chat/history`）向所有旧前端客户端返回 node_run 行，历史回放出现节点气泡噪音，且旧客户端过滤列表虽含 node_run 但其渲染按"运行中占位、结束时删除"设计，历史回放语义不符——违反 AC-05；C 对"按一次运行查节点明细"无必要（无跨运行节点级聚合需求），且 ES 写放大；A 一张窄表 + 独立只读 API，隔离干净，C3 租户过滤与审计表同范式。
- **何时该重新考虑**：需要跨运行做节点级聚合分析（如某节点平均耗时统计）时，评估 C 或从 A 同步中间表。

### 决策 2：节点记录的行形态（start/end 两行 vs 合并一行）

- **备选**：
  - A. 每个节点事件一行（start 一行 + end 一行，`event_type` 区分），读取时按 `unique_id + node_id` 合并
  - B. 内存中暂存 start，end 时合并写一行
- **选定**：A
- **原因**：写入点 `RedisCallback.on_node_start/on_node_end`（`worker/workflow/redis_callback.py:672-696`）是无状态回调，B 需要跨回调缓存（进程内 dict，worker 多线程/重启丢数据）；A 与实时 WS 协议（start/end 两条消息）同构，写入零状态、幂等心智简单；进程中断时已有 start 行、无 end 行 → 详情页显示"运行中（记录不完整）"，恰好满足 spec AC 的边界定义。
- **何时该重新考虑**：行数成为存储瓶颈（每次运行 2N 行）时改 B 或批量写。

### 决策 3：交易标识（输入快照）的采集点与形式

- **备选**：
  - A. 扩展 `ApplicationProcessEventData` 增加 `input_snapshot` 字段，各 `application_process` 上报点采集"该轮最近一条用户消息"文本化后写入
  - B. 交易列表查询时从 `chatmessage.question` 行 join/回查拼装
- **选定**：A
- **原因**：B 让列表查询跨 ES+MySQL 且需按时间配对（多轮会话的 question 行 ↔ 每次运行），脆弱且慢；A 与 F046 加 `status` 字段同构（同 schema、同上报点：`worker/workflow/tasks.py:111`、`common/chat/client.py:103`、`workstation/domain/services/chat_service.py:1150`、`linsight/api/endpoints/linsight.py:648`），一次写入、查询只查 ES。workflow 上报点无法直接拿到输入文本（输入来自 Redis 事件流），在 `execute_workflow` 入口同步查该 chat_id 最近一条 `question` 消息文本（`ChatMessageDao`，一条主键/索引查询），截断 200 字符；其余上报点在上下文里已有用户输入文本。
- **何时该重新考虑**：需要结构化业务字段（统一社会信用代码等）时，另起 feature 在智能体配置里声明字段并从输入里结构化抽取。

### 决策 4：查询 API 形态

- **备选**：
  - A. 扩展 F046 的 `/api/v1/telemetry/call-logs`（加 input_snapshot/trace 维度）
  - B. F052 新增独立只读端点（交易列表 / 节点明细 / 模型调用明细），复用 F046 的租户过滤 helper
- **选定**：B
- **原因**：release-contract 表 1 规定 F046 拥有 call-logs 端点，F052 不得修改其契约；B 不碰 F046，且交易语义（一次运行一条）与 call-logs（agent_run/model_call 混合）不同。租户过滤沿用 F046 同款范式（`_tenant_user_ids()` 缓存 + `user_context.user_id` terms，`telemetry_search/domain/services/call_logs.py:27-45`）。**已落地**：直接 import 该 helper（同包内读用，未改 F046 文件）。
- **何时该重新考虑**：F046 演进出"运行详情"语义并主动合并两个端点时。

### 决策 5：节点明细的租户口径

- **备选**：
  - A. `workflow_node_run` 查询按 `tenant_id == 当前租户`（表内列，C3 自然过滤）
  - B. 按 `user_id IN 租户用户集合`（与 F046 ES 口径完全一致）
- **选定**：B（写冗余 tenant_id 列供 C3 审计与未来使用，查询用 user_id 集合）
- **原因**：F046 口径是"租户用户集合"（ES 事件无 tenant_id）；用户在租户间转移后仍应看到自己历史运行的节点明细（AC-17 与 F046 口径一致）。实现上仿 `AuditLogDao`：`bypass_tenant_filter()` + 显式 `user_id IN (...)` 谓词（否则 C3 auto-listener 会注入 `tenant_id = ctx` 造成双重过滤/漏数据）。
- **何时该重新考虑**：租户用户量过大导致 IN 列表超限时，退回 A 并接受 transfer 后历史不可见的语义变化。

### 决策 6：前端节点视图形态（列表时间线 vs DAG 拓扑）

- **备选**：
  - A. 节点执行视图 = 记录驱动的时间线列表（节点卡片：名称/状态/起止/耗时/失败原因，点击展开出入参 JSON 树）
  - B. 按 flow 当前版本渲染 DAG 拓扑（@xyflow/react），节点着色状态
- **选定**：A（本期），DAG 作为后续增强记录在案
- **原因**：B 的拓扑来自 flow **当前**版本，运行后智能体被修改/删除时拓扑与历史运行错位（AC-16 要求历史交易视图不因版本变化错位）；A 完全由持久化记录驱动，天然满足 AC-16，且不用引入拓扑渲染成本；出入参的"可视化"由自写轻量 JSON 树组件承担（**前端规则禁止引入新 UI 库**，不引 react-json-view 等）。
- **何时该重新考虑**：flow 版本快照机制（flow_version）支持按运行时刻取拓扑时，加 DAG 视图作为节点视图的切换形态。

### 决策 7：节点出入参的清洗与体积限制

- **备选**：
  - A. 序列化前清洗：剔除二进制/超长字段，`input_data`/`output_data` 各截断 64KB（AC-06）
  - B. 原样序列化
- **选定**：A
- **原因**：`input_data` 来源 `self.other_node_variable`（`workflow/nodes/base.py:247`），可能含文件二进制/Base64/超大列表；B 会把数 MB 数据灌进 MySQL 且无价值。清洗函数 `_sanitize_payload()`：递归处理 dict/list/str，str > 8KB 截断、dict 保留 key 结构、bytes 转 `"<bytes N>"` 摘要；文件类值保留文件名/路径引用。
- **何时该重新考虑**：需要完整大对象回放时（走对象存储引用方案，另起 feature）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
工作流运行中:
  nodes/base.py:220 on_node_start ─┐
  nodes/base.py:236-247 on_node_end ─┤→ RedisCallback(worker/workflow/redis_callback.py:672-696)
                                      │     ├─ 既有: send_chat_response → Redis workflow:{unique_id}:event (WS 实时)
                                      │     └─ 新增: NodeRunDao.insert (MySQL workflow_node_run, try/except 不阻断)
运行结束:
  worker/workflow/tasks.py:111 finally ─→ telemetry_service.log_event_sync
                                      │     └─ ES base_telemetry_events (application_process, 新增 input_snapshot)
监控查询:
  /log 智能体监控页签 ─→ GET /api/v1/telemetry/agent-transactions ─→ ES base_telemetry_events (application_process, collapse trace_id 去重)
                     ─→ GET /api/v1/telemetry/agent-transactions/{trace_id}/nodes ─→ MySQL workflow_node_run
                     ─→ GET /api/v1/telemetry/agent-transactions/{trace_id}/model-calls ─→ ES (model_invoke, trace_id 过滤)
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `workflow_node_run` 表 | 见下 | 每节点事件一行（start/end 两行合并成节点记录） | 交易详情 API |
| `workflow_node_run.unique_id` | str, index | = 运行 trace_id（`RedisCallback.unique_id` = `{chat_id}_async_task_id`），贯穿节点行与 ES 事件 | 详情关联 |
| `workflow_node_run.exec_id` | str, index | = 单次节点执行的 uuid（`NodeStartData.unique_id`）——**不是** trace；用于 start/end 精确配对（坑 15） | 详情合并 |
| `workflow_node_run.event_type` | `start` / `end` | start 行: status=running；end 行: success/failed + reason + input_data + output_data | 详情合并 |
| `application_process.input_snapshot` | str ≤200 | 该轮用户输入的文本化快照（问题文本；表单类为 `label: value` 列表，与 chatmessage question 行同构） | 交易列表"交易标识"列 + 关键词筛选 |
| 交易列表 API 响应 | `{records:[{trace_id, app_id, app_name, app_type, chat_id, user_id, user_name, status, process_time, input_snapshot, start_time, end_time}], total, page, page_size}` | 只读 `application_process` 事件；关键词筛选用 ES match 于 `event_data.application_process_input_snapshot`（前缀化 key，见坑 1） | 前端页签列表 |
| 节点明细 API 响应 | `{run: {trace_id, chat_id, app_name, ...}, nodes: [{node_id, node_name, status, start_time, end_time, duration, reason, input_data, output_data}]}` | 无 start 行的节点单独成条（"运行中/记录不完整"）；无任何节点行 → 前端提示 AC-15 | 前端详情 |

`workflow_node_run` 表结构（SQLModel `table=True`，启动 create_all 补表，无需 Alembic revision——整表新建规则见 backend AGENTS.md）：

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| unique_id | str, index | 运行 trace_id |
| chat_id | str, index | 会话 |
| flow_id | str, index | 智能体 |
| node_id | str | 节点 ID（flow 内稳定） |
| node_name | str | 节点名称快照 |
| event_type | str | start / end |
| status | str | running / success / failed |
| reason | str, nullable | 失败原因（end 行） |
| input_data | JsonType, nullable | 节点输入（end 行，清洗后 ≤64KB） |
| output_data | JsonType, nullable | 节点输出（end 行，清洗后 ≤64KB） |
| user_id | int, index | 运行用户（租户口径用） |
| tenant_id | int | 冗余（C3 审计/未来用；查询不走它） |
| create_time | datetime, server_default CURRENT_TIMESTAMP | **DB 时钟**（坑 2） |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `database/models/workflow_node_run.py`（新） | 表 + `NodeRunDao.insert_event()`（sync）+ `_sanitize_payload()` 清洗 + 查询方法（bypass + user_id 集合） | 不碰 chatmessage；不做聚合 |
| `worker/workflow/redis_callback.py`（改） | `on_node_start/on_node_end` 内追加 `NodeRunDao.insert_event`（try/except，失败仅 logger.warning——AC-04） | 不改变既有 send_chat_response 行为 |
| `common/schemas/telemetry/event_data_schema.py`（改） | `ApplicationProcessEventData` + `input_snapshot: str \| None` | 不改 status 等 F046 字段语义 |
| 4 个 application_process 上报点（改） | 采集输入快照：workflow 在 `execute_workflow`/`continue_workflow` 的 **finally 上报处**查 chat_id 最近 question 行文本化（本轮 question 行由输入节点回调在运行中写入，入口时查会拿到上一轮——见坑 11）；assistant/workstation/linsight 从上报点上下文取用户输入文本化 | 不改上报时机 |
| `telemetry_search/api/endpoints/agent_transactions.py`（新） | 3 个只读端点（列表/节点/模型调用），登录依赖同 call-logs（`UserPayload.get_login_user`），窗口校验同 call-logs（90 天、HTTPException 400，不新增错误码） | 不写事件、不管理看板 |
| `telemetry_search/domain/services/agent_transactions.py`（新） | ES 查询（租户过滤同 F046 范式）+ 节点合并逻辑 + 权限（`_user_has_log_web_menu` 复用 audit 同款检查） | — |
| `telemetry_search/domain/schemas/agent_transactions.py`（新） | 查询参数/响应 schema | — |
| `platform/src/pages/LogPage/agentMonitor/`（新目录） | index（交易列表+筛选）、TransactionDetail（详情抽屉）、NodeRunList、JsonTree（自写轻量 JSON 树） | 不引新 UI 库 |
| `platform/src/controllers/API/log.ts`（改） | 3 个 API 函数 | — |
| `platform/src/pages/LogPage/index.tsx`（改） | 第三个 Tab「智能体监控」 | 权限沿用 log 路由 |

### 4.4 前端交互

- 页签：LogPage Tabs 加 `agentMonitor`（i18n key `log.agentMonitor`，三语同 PR）。
- 列表：表格列（交易标识/智能体/类型/用户/状态/耗时/时间）+ 筛选（智能体下拉=FilterByApp 复用、状态 Select、时间 FilterByDate 复用、关键词 Input）+ AutoPagination；"刷新"按钮手动 refetch（AC-11）。
- 详情：点击行 → 抽屉（TransactionDetail）：头部运行信息 + 模型调用明细表 + 节点时间线（NodeRunList，状态徽标 + 失败原因），节点展开 → JsonTree 渲染 input_data/output_data。
- JsonTree：自写递归组件（折叠/展开、key 着色、字符串截断预览），~150 行内，无新依赖。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | ES 事件 `event_data` key 带事件名前缀：`application_process_input_snapshot`（不是 `input_snapshot`） | 查询/展示取错字段全空 | 列表查询与关键词筛选按前缀 key 构造（仿 call_logs.py:56-79） |
| 2 | 新表 create_time 若用 Python `datetime.now()`，在 TZ 不统一的环境会差 8 小时（本机 2026-09-05 已把宿主改为 Asia/Shanghai，但原则不依赖它） | 节点时间线整体偏移、跨天错位 | `server_default=CURRENT_TIMESTAMP`；时长由 start/end 行 create_time 差计算 |
| 3 | NodeStart/End 事件本身不带时间戳，MySQL create_time 是秒级 | 亚秒级节点耗时显示 0s | 正常现象，前端显示 `>=1s` 才有意义，不伪造精度 |
| 4 | output 节点成功时不发 end 事件（`nodes/base.py:237` 逻辑） | 节点视图出现只有 start 的节点 | 详情按"运行中/记录不完整"渲染，不做特殊补齐 |
| 5 | C3 tenant auto-listener 对 SELECT 注入 `tenant_id = ctx`：查询按 user_id 集合过滤时必须 `bypass_tenant_filter()`（审计 DAO 同款理由） | 超管看全量时被过滤、scoped 用户双重过滤 | `workflow_node_run` 查询方法内 bypass + 显式谓词 |
| 6 | 多轮会话同一 chat_id：question 行有多条，快照必须取**该轮最近**一条（execute_workflow 入口查 `order_by create_time desc limit 1`） | 交易标识显示上一轮内容 | 采集点实现 |
| 7 | 历史 application_process 事件无 `input_snapshot`（与 F046 status 同理） | 交易标识列空 | 前端显示 `-`；不做回填 |
| 8 | 节点落库在 worker 热路径（每节点 2 次同步 insert）；失败必须静默（AC-04） | 一次 DB 抖动把整个运行打挂 | try/except + logger.warning；insert 单行轻量 |
| 9 | `input_data` 来自 `other_node_variable`，可能含循环引用或不可 JSON 化对象 | 序列化崩溃 | `_sanitize_payload` 用 `default=str` 兜底 + 结构递归截断 |
| 10 | 详情页无节点记录时（上线前历史）需要明确提示而非空白（AC-15） | 用户误以为功能坏 | 前端空态文案 + 后端返回空 nodes 数组 |
| 11 | 同一轮带表单的运行会写**两条** application_process 事件（`execute_workflow` 到输入节点 + `continue_workflow` 完成剩余，同一 trace_id） | 交易列表一条交易出现两行，违反 AC-09 | 列表查询 ES `collapse: {"field": "trace_id"}`（保留时间最新的一条；total 自动变为去重计数） |
| 12 | ES `base_telemetry_events.timestamp` 的 mapping 是 `epoch_second`（秒，不是毫秒）；但数值 range 查询传毫秒也能命中（ES 数字自动按毫秒识别） | 前端直接用 `new Date(time)` 渲染出 1970 年；range 查询语义混淆 | 前端统一 `time * 1000`；range 查询沿用 F046 的 `*1000` 写法（实测可命中） |
| 13 | 平台的业务错误统一转成 HTTP 200 + 响应体 `status_code`（前端拦截器处理）——HTTPException 也不例外 | e2e 断言 403/400 的 HTTP 状态码会误判 | 权限拒绝断言 `status_code == 403`（body）、窗口超限断言 `status_code == 400`（body） |
| 14 | 输入快照可能包含 MinIO presigned URL（带签名的临时凭证参数）——与 chatmessage question 行的既有存储行为一致，URL 本身有时效 | 快照被误当长期凭证 | 沿用既有行为不做额外脱敏；若收紧需同时改 question 行（另起 feature） |
| 15 | `NodeStartData.unique_id` 是**单次节点执行的 uuid**（`nodes/base.py:218` 的 `exec_id`），不是运行 trace——trace 在 `RedisCallback.unique_id`。首版实现误用它做行键，节点行挂在错误的键下（监控查不到） | 详情页节点永远为空且难排查 | 表内 `exec_id` 列存执行 uuid（start/end 配对键）、`unique_id` 列存运行 trace；写入点两者分开传入（2026-09-05 实测跑通后修复） |
| 16 | 节点行按 `create_time`（秒级）+ uuid PK 排序——同一秒内多个节点的先后顺序不保证与真实执行顺序一致 | 节点时间线偶发顺序颠倒（秒级精度下的已知限制） | 前端按返回顺序展示；如需严格顺序需单调自增主键（后续） |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `GET /api/v1/telemetry/agent-transactions`（page/page_size/app_id/app_type/status/start_time/end_time/keyword） | HTTP API | /log 智能体监控页签 |
| `GET /api/v1/telemetry/agent-transactions/{trace_id}/nodes` | HTTP API | 同上 |
| `GET /api/v1/telemetry/agent-transactions/{trace_id}/model-calls` | HTTP API | 同上 |
| `application_process` 事件新增 `input_snapshot` 字段 | 遥测事件 schema | 未来告警/审计 |
| `workflow_node_run` 表 | MySQL 表 | 仅本 Feature 读写（写：worker 回调；读：详情 API） |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| F046 `CallLogsService._tenant_user_ids()` / 登录依赖范式 | 内部 service/helper | 若 F046 重构 helper 签名需跟进；已按"读取"口径调用 |
| `RedisCallback` 上下文（`self.unique_id/chat_id/user_id/tenant_id/workflow_id`，`redis_callback.py:64-70`） | 内部对象 | 均已存在，无需改动其构造 |
| `ChatMessageDao`（取该轮 question 文本） | 内部 DAO | 多轮会话取最近一条需按 create_time 排序 |
| ES `base_telemetry_events` mapping（dynamic） | ES 索引 | `input_snapshot` 走 event_data 前缀化自动映射，无需改 mapping |

---

## 7. 测试与可观测

- **单元/集成**（pytest，`test/worker/`、`test/telemetry_search/`）：
  - `_sanitize_payload`：二进制/超长/不可 JSON 化对象清洗与 64KB 截断（AC-06）
  - `NodeRunDao.insert_event`：start/end 写入、幂等、失败不影响调用方（AC-02/03/04）
  - 节点明细 API：start/end 合并、缺失 end 的"记录不完整"、user_id 租户集合过滤、无记录空数组（AC-12/15/17）
  - 交易列表 API：input_snapshot 返回、关键词筛选（前缀化 key）、90 天窗口校验、一条运行一条记录（AC-07/08/09/10）
  - 聊天历史接口回归：新增采集后 `/api/v1/chat/history` 返回内容与结构不变（AC-05）
- **e2e**（headless，/log 页）：页签可见（log 权限）；跑一次工作流后交易列表出现该笔交易（含交易标识）；详情节点视图状态/出入参/失败原因正确；无权限账号不显示页签（AC-18）。
- **手动验证**：跑一次「流水分析智能体」→ 监控页签交易列表 +1、交易标识 = 本轮输入快照；详情可见每个节点（输入节点/LLM 节点/报告节点）的出入参；中途停掉 worker 再查看"运行中/记录不完整"节点。
- **可观测**：worker 日志（节点落库失败 warning 计数）、详情 API 的 ES/MySQL 查询耗时；前端沿用表格 loading/错误态。

---

## 8. 后续改进 / 不打算做的事

- DAG 拓扑视图（依赖 flow 运行时刻版本快照，见决策 6）。
- 灵思智能体节点详情（执行体系不同，另起 feature）。
- 结构化业务字段抽取（另起 feature）。
- 节点记录保留策略（与 chatmessage 同生命周期，无特殊清理）；数据量上量后再评估归档。
