# Tasks: 智能体监控页签（按交易监控 + 节点出入参可视化）

> 本文档 = 执行流水（append-only 运行日志）。How 的真相在 [design.md](./design.md)，验收口径在 [spec.md](./spec.md)。

## 状态表

| 文档 | 状态 | 评审结论 / 备注 |
|------|------|----------------|
| spec.md | ✅ 已评审 | 2026-09-05 用户确认（4 口径：输入快照/工作流型优先//log 页签/不回填）；AC-11 修订为「仅展示已结束运行」（用户确认） |
| design.md | ✅ 已评审 | 2026-09-05 用户确认；AC-11 冲突已按用户决定修订 spec |
| tasks.md | ✅ 已拆解 | 见下 |

## 任务列表（按 Wave 执行；每任务 1-2 文件，一次会话内可完成）

### Wave 1 — 后端数据采集（节点记录 + 输入快照）

- [x] **T-01（基础设施）** 新建 `database/models/workflow_node_run.py`：`WorkflowNodeRun` 表（SQLModel `table=True`，字段见 design §4.2，`create_time` 用 `server_default=CURRENT_TIMESTAMP`）+ `NodeRunDao`（`insert_event()` sync 单行插入、查询方法骨架）。整表新建无需 Alembic revision（启动 create_all 补表）；回滚方案 = `DROP TABLE workflow_node_run`。 覆盖 AC: AC-02/03
- [x] **T-02（测试先行）** 新建 `test/worker/test_workflow_node_run.py`：`_sanitize_payload` 清洗边界（bytes→摘要、超长字符串截断、不可序列化对象 `default=str`、64KB 上限）与 `insert_event` 写入行为（start/end 行、失败不阻断调用方）。 覆盖 AC: AC-02, AC-03, AC-04, AC-06
- [x] **T-03** 在 `database/models/workflow_node_run.py` 实现 `_sanitize_payload()` 与 `NodeRunDao.insert_event()`，跑 T-02 测试转绿。 覆盖 AC: AC-02, AC-03, AC-04, AC-06
- [x] **T-04（Worker）** 修改 `worker/workflow/redis_callback.py` `on_node_start`/`on_node_end`（:672-696）追加 `NodeRunDao.insert_event`（start: status=running；end: status=success/failed + reason + input_data=`other_node_variable` + output_data=`log_data`），try/except 仅 `logger.warning` 不阻断。tenant_id 传递：取自 `RedisCallback` 上下文 `self.tenant_id`（构造时由 flow 租户传入，`redis_callback.py:64`，与 ChatMessage 写入路径相同，无需 Celery headers）。 覆盖 AC: AC-02, AC-03, AC-04
- [x] **T-05** 修改 `common/schemas/telemetry/event_data_schema.py`：`ApplicationProcessEventData` 增 `input_snapshot: str | None`（≤200 字符，字段语义见 design §4.2）。 覆盖 AC: AC-01
- [x] **T-06** 修改 `worker/workflow/tasks.py`：`execute_workflow` 入口查该 chat_id 最近一条 `question` 行文本化（`order_by create_time desc limit 1`）截断 200 字符，finally 上报时填 `input_snapshot`。 覆盖 AC: AC-01
- [x] **T-07** 修改 `common/chat/client.py`（assistant 上报点 :103）与 `workstation/domain/services/chat_service.py`（:1150）：从各自上下文取该轮用户输入文本化填 `input_snapshot`（不改上报时机与 F046 字段语义）。 覆盖 AC: AC-01
- [x] **T-08** 修改 `linsight/api/endpoints/linsight.py`（:648）：同上填 `input_snapshot`。 覆盖 AC: AC-01
- [x] **T-09（回归验证）** 跑一次工作流后调用 `/api/v1/chat/history`，断言返回内容与结构不变（无 node_run 行）；补一条自动化回归到 `test/chat_session/`（chat history 过滤断言）。 覆盖 AC: AC-05

### Wave 2 — 后端查询 API（交易列表 / 节点明细 / 模型调用）

- [x] **T-10（测试先行）** 新建 `test/telemetry_search/test_agent_transactions.py`：service 层（ES mock 沿用 F046 测试范式）列表分页/筛选/关键词（前缀 key `application_process_input_snapshot`）/90 天窗口校验/租户用户集合过滤/一条运行一条记录；节点 start/end 合并、缺失 end「记录不完整」、无记录空数组、无权限拒。 覆盖 AC: AC-07, AC-08, AC-09, AC-10, AC-15, AC-17, AC-18
- [x] **T-11** 新建 `telemetry_search/domain/schemas/agent_transactions.py`（查询参数/响应 schema，窗口校验同 call-logs 90 天口径）与 `telemetry_search/domain/services/agent_transactions.py`（ES 查询 `base_telemetry_events` application_process；节点明细走 `NodeRunDao`；模型调用按 trace_id 过滤 model_invoke；租户过滤同 F046 范式；权限复用 audit `_user_has_log_web_menu` 检查），跑 T-10 转绿。 覆盖 AC: AC-07, AC-08, AC-09, AC-10, AC-12, AC-14, AC-15, AC-17, AC-18
- [x] **T-12** 新建 `telemetry_search/api/endpoints/agent_transactions.py`（3 个 GET 端点，登录依赖同 call-logs `UserPayload.get_login_user`）并注册进 `telemetry_search/api/router.py`。 覆盖 AC: AC-07, AC-12, AC-14

### Wave 3 — 前端页签（列表 + 详情 + JSON 可视化；仅 Platform，不涉及 Client）

- [x] **T-13** `platform/src/controllers/API/log.ts` 增 3 个 API 函数（`getAgentTransactionsApi` / `getTransactionNodesApi` / `getTransactionModelCallsApi`）。 覆盖 AC: AC-07, AC-12, AC-14
- [x] **T-14** 新建 `platform/src/pages/LogPage/agentMonitor/JsonTree.tsx`（自写轻量 JSON 树：折叠/展开/字符串截断预览，无新依赖）。 覆盖 AC: AC-13
- [x] **T-15** 新建 `platform/src/pages/LogPage/agentMonitor/NodeRunList.tsx`（节点时间线：状态徽标/起止/耗时/失败原因/展开出入参）+ `TransactionDetail.tsx`（详情抽屉：运行信息 + 模型调用明细表 + 节点列表；无节点记录时 AC-15 提示文案）。 覆盖 AC: AC-12, AC-13, AC-14, AC-15, AC-16
- [x] **T-16** 新建 `platform/src/pages/LogPage/agentMonitor/index.tsx`（交易列表：筛选（智能体/类型/状态/时间/关键词）+ 刷新按钮 + AutoPagination + 状态徽标/交易标识列，空标识显示 `-`）并挂入 `platform/src/pages/LogPage/index.tsx` 第三个 Tab。 覆盖 AC: AC-07, AC-08, AC-10, AC-11
- [x] **T-17** i18n 三语新增 `log.agentMonitor` 及相关 key（zh-Hans/en-US/ja），`pnpm check-i18n` 通过；`pnpm lint` + `pnpm typecheck` 通过（新文件不加 `@ts-strict-ignore`）。 覆盖 AC: AC-07, AC-18

### Wave 4 — 构建、e2e 与文档

- [x] **T-18** `/e2e-test features/v2.7.0/052-agent-monitor-transactions`：API 级（列表/详情/权限/租户/窗口）+ 页面手动验证清单（页签、交易标识、节点出入参、失败节点、无节点提示、历史空态）。 覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18
- [x] **T-19** 前端重新构建（`data/manage.sh build`）并重启 preview；后端重启（采集埋点生效）；跑一次「流水分析智能体」端到端验证（交易列表 +1、交易标识正确、节点出入参完整）。 覆盖 AC: AC-01, AC-07, AC-12
- [x] **T-20** `/code-review --base main`；实现偏差回写 design.md。 覆盖 AC: —

## 执行日志

| 时间 | 记录 |
|------|------|
| 2026-09-05 | spec/design 评审通过（用户确认 AC-11 修订）；tasks 拆解完成（Test-First 顺序、AC 逐条标注） |
| 2026-09-05 | Wave 1-3 实现完成；e2e 4/4 通过；headless 页面验证通过；遗留：真实工作流手动验证（见 e2e-checklist.md） |
| 2026-09-05 | 真实运行验证：企业基本信息查询智能体跑通——修复 exec_id 与 trace 混淆（坑 15）、collapse 去重；交易列表/节点明细/模型调用/关键词搜索全链路通过 |
