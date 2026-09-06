# Design: 智能体运行状态监控（监控看板 + 调用日志）

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-01

---

## 1. 目标与非目标

- **目标**：在平台首页统计看板上提供智能体（工作流/助手/灵思）的准实时运行监控（调用量/成功率/响应时间）与调用日志查询（智能体运行 + 模型调用）。
- **非目标**：告警通知、数据导出、自定义看板编辑器、知识库/数据集资源监控、跨租户汇总。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7（分层/双DB/多租户/权限/错误码）。
- **上游数据格式依赖**：遥测事件写入 ES `base_telemetry_events`，`event_data` 内所有 key 带事件名前缀（如 `application_process_app_id`）——中间表同步与日志查询必须按前缀取字段。
- **看板查询引擎只读 `mid_*` 中间表**（telemetry_search 的 DataQueryService/SearchEngineService 不接受原始事件索引）。
- **同步延迟约束**：5 分钟准实时（用户确认口径），远高于既有 30 分钟增量表的频率要求——同步任务必须增量且幂等。
- **已有环境兼容**：看板组件与数据集有 DB 种子 + `init_dataset.py` 两处，已有部署环境需要手动补 DB 行（运维脚本）。

---

## 3. 方案对比与选定

### 决策 1：智能体运行「成功率」的数据源

- **备选**：
  - A. 给 `application_process` 遥测事件补充 `status` 字段（各上报点 finally 处写入 success/failed）
  - B. 从同 trace 的 `model_invoke.status` 推断（有失败模型调用即算失败）
- **选定**：A
- **原因**：B 无法覆盖无模型调用的运行（纯工具链/空跑）、且跨事件关联查询复杂；A 是一处 schema 字段 + 各上报点 finally 已知道成败（异常变量在 finally 可见），改动小而语义准。
- **何时该重新考虑**：上报点增加过多、或希望统计「部分成功」等更细语义时。

### 决策 2：监控中间表的形态（同步时聚合 vs 查询时聚合）

- **备选**：
  - A. `mid_agent_run_dtl` 存**原始明细行**（一次运行一行），指标在数据集 `schema_config` 里用 VALUE_COUNT/AVG/MAX + filter + DIVIDE 公式在**查询时**聚合（完全复用 mid_tool_call_dtl/mid_model_call_dtl 的既有范式）
  - B. 同步时按 智能体×时间桶 read-modify-write 聚合（写桶文档）
- **选定**：A
- **原因**：B 需要读改写 + 重算窗口，且查询引擎已支持全套聚合 DSL（调研确认 init_dataset.py 中 tool_call_success_rate 的 filter+DIVIDE 范式现成）；A 的同步任务只是「事件 → 行」搬运，与 `sync_mid_user_interact_dtl` 同构，幂等靠 es_id。
- **何时该重新考虑**：明细行体积过大（> 数千万/年）影响 ES 与查询性能时，改 B 或加生命周期策略。

### 决策 3：调用日志的数据源

- **备选**：
  - A. 调用日志**直查原始事件索引** `base_telemetry_events`（新增独立只读端点，不走看板 component/query 引擎）
  - B. 调用日志也进中间表、走看板引擎
- **选定**：A
- **原因**：日志明细进中间表 = 数据翻倍存储 + 5 分钟同步延迟 + 看板引擎的指标/维度模型不适配分页列表；原始事件实时可查，模型调用记录（model_invoke）本就在原始索引中。中间表只承担聚合指标。
- **何时该重新考虑**：需要跨天保留策略、或 ES 原始索引被独立 statistics 集群隔离且网络受限时。

### 决策 4：前端集成形态

- **备选**：
  - A. 扩展现有预置统计看板（dashboard id=10）：新增 4 个指标卡 + 2 个图表 + 1 个「调用日志」表格组件（新组件类型 `table`，直连调用日志 API）
  - B. 独立监控页面 / 第二看板
- **选定**：A（用户已确认）
- **原因**：首页落地即 `/dashboard`（board 权限首位路由），扩展现有看板零新增入口；看板网格/时间筛选联动/图表渲染全复用。`table` 组件类型是新增的最小载体。
- **何时该重新考虑**：监控组件过多挤压统计看板空间时，拆第二看板。

### 决策 5：租户隔离口径

- **备选**：
  - A. 调用日志与中间表查询按「当前租户用户集合」过滤（MySQL 查租户全部用户 id → ES terms 过滤，用户集合缓存）
  - B. 事件写入时补 tenant_id 字段（历史事件无法回填）
- **选定**：A（中间表沿用 mid_* 既有隔离机制；调用日志 API 用用户集合过滤）
- **原因**：事件层无 tenant_id（user_context 只有 user_id）；B 对历史数据无效且要改所有写入点。A 与看板既有口径一致。
- **何时该重新考虑**：租户用户量过大（terms 过滤超限）时改 B 并接受历史不回填。

### 决策 6：数据新鲜度

- **备选**：
  - A. beat 每 5 分钟增量同步（沿用中间表游标范式）
  - B. 与既有统计看板一致每 30 分钟
  - C. 查询链路直查原始事件索引（完全实时）
- **选定**：A
- **原因**：用户确认的准实时口径；B 监控意义弱；C 需改造查询引擎（看板引擎只读 mid_*，改造面大——见决策 3）。
- **何时该重新考虑**：需要秒级实时时，评估 C 的查询引擎改造成本。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**遥测写入**：运行结束点 → `telemetry_service.log_event_sync`（`common/services/telemetry/telemetry_service.py:334`，ThreadPool + copy_context）→ ES `base_telemetry_events`（mapping `INDEX_MAPPING` L29-62；`event_data` key 前缀化在 `base_telemetry_schema.py:50-65`）。

**事件类型（本功能相关）**：
- `application_process`（`ApplicationProcessEventData`，`common/schemas/telemetry/event_data_schema.py:145`）：app_id/app_name/app_type/chat_id/start_time/end_time/**process_time(ms)**；上报点：`worker/workflow/tasks.py:108/159`（工作流 Celery finally）、`common/chat/client.py:103`（助手）、`open_endpoints/api/endpoints/assistant.py:220`、`workstation/domain/services/chat_service.py:1150`、`linsight/api/endpoints/linsight.py:648`。**无 status**。
- `model_invoke`（`ModelInvokeEventData` L103）：model_id/name/type、app_id/name/type、start/end/**first_token_cost_time**、**status**、token 数；上报点：`llm/domain/utils.py:213 upload_telemetry_log`（LLM 调用 wrapper finally）。

**中间表范式**：`worker/telemetry/mid_table.py:335 sync_mid_user_interact_dtl` = 「ES 原始事件 → mid_* 行」现成范式（增量游标 `get_latest_record_time_sync`、bulk es_id 幂等、`_cross_tenant`）。表基类 `telemetry/domain/mid_table/base.py:BaseMidTable`（公共 mapping：user 维度 + timestamp，ngram 分析器）。

**看板查询链**：前端 `ChartContainer`（`platform/src/pages/Dashboard/components/charts/ChartContainer.tsx`）→ `POST /api/v1/telemetry/dashboard/component/query`（`telemetry_search/api/endpoints/dashboard.py:89`）→ `DataQueryService.query_telemetry_data`（读 MySQL `dashboard_dataset.schema_config`）→ `SearchEngineService` 构造聚合 DSL 查 `mid_*` → 二维数组返回。数据集声明种子：`telemetry_search/domain/init_dataset.py`（`DASHBOARD_DATASET`，10 个数据集；metric 的 filter+VALUE_COUNT+DIVIDE 范式见 `mid_tool_call_dtl` L312-377）。

**看板渲染**：`dashboard`（id=10 预置统计看板，seed SQL `init_dataset.py:823`）→ `dashboard_component`（type: metric/query/bar/stacked-bar/area/pie/donut/line/…）→ `EditorCanvas` react-grid-layout → `ComponentWrapper` 分发 → `BaseChart`（ECharts）或 `MetricCard`。首页落地：`platform/src/routes/index.tsx` board 权限首位 → `/dashboard`。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `mid_agent_run_dtl` 行 | ES doc：`event_id`(es_id)、`app_id`、`app_name`、`app_type`、`status`(success/failed)、`process_time`(int ms)、`user_id/user_name/user_group_infos/user_department_infos`、`timestamp` | 一次智能体运行一行；es_id=event_id 幂等 | 看板数据集聚合 |
| `application_process.status` | `"success" \| "failed"` | 新增事件字段（历史事件无此字段） | 成功率统计 |
| 调用日志 API 响应 | `{records:[{type:"agent_run"/"model_call", …}], total, page, page_size}` | agent_run 含 app 名/类型/用户/状态/耗时/时间；model_call 含模型名/状态/耗时/token/时间 | 首页调用日志表格组件 |
| `dashboard_component` 新行（type=`table`） | data_config 含 `linkedComponentIds`(时间筛选联动) | 调用日志表格组件 | 看板渲染 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `worker/telemetry/mid_table.py`（新增 sync 任务） | 事件 → mid_agent_run_dtl 行搬运 | 不做聚合（查询时聚合） |
| `telemetry_search/api/endpoints/`（新增 call-logs 端点） | 调用日志只读查询（原始事件索引） | 不写事件、不管理看板组件 |
| `telemetry_search/domain/init_dataset.py` | 数据集/看板种子声明 | 已部署环境的 DB 行由运维脚本补 |
| `platform/src/pages/Dashboard/components/`（新增 CallLogTable 组件） | 表格渲染 + 分页筛选 | 不参与图表/指标卡渲染 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | ES 事件 `event_data` 的 key 带事件名前缀（`application_process_app_id`） | 同步/查询取错字段，全空数据 | 同步任务按前缀取字段（仿 mid_table.py:377-380） |
| 2 | `application_process` 历史事件无 status | 成功率分母口径错（历史被当失败或剔除） | 成功率公式分母 = status 存在的记录数；spec 边界已定 |
| 3 | 看板数据集/组件种子只在**空库启动**时写入；已有环境必须手动补 DB（`dashboard_dataset`/`dashboard_component`） | 新指标卡不渲染、组件 404 | 提供运维脚本 + 文档步骤 |
| 4 | 中间表增量游标与 beat TZ：worker 进程 UTC vs MySQL CST | 游标错位导致漏同步/重复 | 沿用 `_db_now()`（mid_table.py 既有做法） |
| 5 | 稀疏折线图表（≤3 点）不显示 symbol 不可见；整数指标 yAxis 需 minInterval:1 | 图表空白/刻度重复 | BaseChart 既有处理，新图表沿用 |
| 6 | `wrapper_bisheng_model_limit_check` 的 telemetry 上报在 finally（含失败路径），但 **workflow 的 APPLICATION_PROCESS 上报点在 Celery 任务 finally**——补 status 时要区分「任务本身失败」与「正常结束」 | status 恒 success，成功率恒 100% | tasks 任务里逐个上报点核对异常变量 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `GET /api/v1/telemetry/call-logs`（分页/筛选参数：type/app_id/model/status/start/end） | HTTP API | platform 首页调用日志组件 |
| `mid_agent_run_dtl` 索引 + `dashboard_dataset` 声明（dataset_code=`mid_agent_run_dtl`） | ES 索引 + DB 种子 | 看板查询引擎 |
| `application_process` 事件新增 `status` 字段 | 遥测事件 schema | 中间表同步、未来告警 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `BaseTelemetryService.log_event_sync` | 内部 API | 事件 schema 变更需与 telemetry 模块一致 |
| `BaseMidTable` 基类与 beat 调度 | 内部基类 + settings | beat 重启才生效；调度冲突 |
| 看板引擎（DataQueryService/SearchEngineService） | 内部服务 | 只读 mid_*；新数据集字段必须匹配索引 mapping |
| `dashboard_component`/`dashboard_dataset` DB 行 | 数据库种子 | 已有环境需手动补行 |

---

## 7. 测试与可观测

- **单元/集成**（pytest，`test/telemetry_search/`、`test/worker/`）：同步任务（事件→行映射、幂等、游标续传）、数据集聚合查询（成功率/avg/max 口径）、调用日志 API（筛选/分页/租户隔离）。
- **e2e**（headless，首页）：预置看板渲染 4 指标卡 + 图表 + 调用日志表格；时间筛选联动；无数据空态。
- **手动验证**：跑一次会议纪要工作流 → 5 分钟内首页指标卡数字 +1、成功率反映结果；调用日志可见该次运行与内部模型调用明细。
- **可观测**：beat 任务日志（同步量/游标）、调用日志 API 的 ES 查询耗时；看板查询错误沿用 dashboard 组件错误态。

---

## 8. 后续改进 / 不打算做的事

- 告警/通知、监控数据导出（spec 已排除，另起 feature）。
- 明细行生命周期策略（ILM/按年滚动）——数据量上量后再做。
- 应用删除后名称快照：日志查询时 event 自带 app_name 快照，无需额外处理；看板维度仅统计现存应用（沿用 mid_app_increment 口径）。
- 触发条件：年明细 > 千万级 或 ES 存储压力时，重新评估决策 2。
