# Tasks: 智能体运行状态监控（监控看板 + 调用日志）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-01 用户确认 |
| design.md | ✅ 已评审 | 2026-09-01 用户确认；接手第一入口 |
| tasks.md | ✅ 已拆解 | 2026-09-01 |
| 实现 | ✅ 已完成 | 15 / 15 完成（2026-09-01）。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 开发模式

按 Wave 组织：Wave 1 无依赖可并行；后端测试先红后绿（测试内联在各任务「测试:」行）；前端手动验证 + headless e2e。设计论证指向 design §X，不复制。

---

## Tasks

### Wave 1（无依赖，可并行）

- [x] **T001**: application_process 事件补 status 字段
  **文件**: `src/backend/bisheng/common/schemas/telemetry/event_data_schema.py`（ApplicationProcessEventData）
  **逻辑**: 加 `status: str`（success/failed）字段；确认 BaseTelemetryEvent.model_dump 前缀化对新字段生效（`application_process_status`）
  **依赖**: 无 | **覆盖 AC**: AC-01 | **设计**: design 决策 1
  **测试**: 先红后绿：`test/common/test_telemetry_schema.py`：model_dump 输出含 `application_process_status`

- [x] **T002**: 工作流上报点写入 status
  **文件**: `src/backend/bisheng/worker/workflow/tasks.py`（execute_workflow/continue_workflow 的 finally，2 处）
  **逻辑**: finally 用异常变量判定 success/failed 写入 status（design 坑 #6：区分任务失败与正常结束）
  **依赖**: T001 | **覆盖 AC**: AC-01 | **设计**: design 决策 1、坑 #6
  **测试**: 先红后绿：`test/worker/test_application_process_status.py` 断言成功/失败路径事件带 status

- [x] **T003**: 助手聊天上报点写入 status
  **文件**: `src/backend/bisheng/common/chat/client.py`（~L103）、`src/backend/bisheng/open_endpoints/api/endpoints/assistant.py`（~L220）
  **逻辑**: 两处运行结束点用异常变量判定 success/failed 写入 status（写法同 T002）
  **依赖**: T001 | **覆盖 AC**: AC-01 | **设计**: design 决策 1、坑 #6
  **测试**: 手动验证（跑一次助手聊天，检查 ES 事件含 status）

- [x] **T004**: 工作站 / 灵思上报点写入 status
  **文件**: `src/backend/bisheng/workstation/domain/services/chat_service.py`（~L1150）、`src/backend/bisheng/linsight/api/endpoints/linsight.py`（~L648）
  **逻辑**: 两处运行结束点用异常变量判定 success/failed 写入 status（写法同 T002）
  **依赖**: T001 | **覆盖 AC**: AC-01 | **设计**: design 决策 1、坑 #6
  **测试**: **测试降级**: 上报点为聊天服务收尾处，无独立可测单元（异常变量判定），手动验证（跑一次工作站/灵思聊天）+ 既有相关测试回归

- [x] **T005**: mid_agent_run_dtl 中间表模型 + 同步任务
  **文件**: `src/backend/bisheng/telemetry/domain/mid_table/agent_run.py`（新）、`src/backend/bisheng/worker/telemetry/mid_table.py`（加 sync_mid_agent_run_dtl）
  **逻辑**: 继承 BaseMidTable；行字段 event_id(es_id)/app_id/app_name/app_type/status/process_time/user 维度/timestamp；同步读 base_telemetry_events 的 application_process 事件（按前缀取字段，仿 sync_mid_user_interact_dtl L335），增量游标 get_latest_record_time_sync；`_cross_tenant` 装饰（beat 任务无用户 tenant header，跨租户全量同步，与既有 mid 表同口径——design 决策 5）
  **依赖**: T001 | **覆盖 AC**: AC-02, AC-03, AC-13 | **设计**: design 决策 2、5
  **测试**: 先红后绿：`test/worker/test_mid_agent_run_sync.py`：事件→行映射、es_id 幂等、游标续传

- [x] **T006**: beat 调度（每 5 分钟）
  **文件**: `src/backend/bisheng/core/config/settings.py`（beat_schedule 加 `*/5 * * * *`）
  **逻辑**: 注册 telemetry_sync_mid_agent_run_dtl；重启 beat/worker 生效
  **依赖**: T005 | **覆盖 AC**: AC-02 | **设计**: design 决策 6

- [x] **T007**: dashboard_dataset 数据集声明
  **文件**: `src/backend/bisheng/telemetry_search/domain/init_dataset.py`（DASHBOARD_DATASET 加 mid_agent_run_dtl）
  **逻辑**: dataset_code=mid_agent_run_dtl、es_index_name=mid_agent_run_dtl、is_commercial_only=False；metrics：run_count(VALUE_COUNT)、success_count(filter status=success)、success_rate(DIVIDE：成功数 / status 存在总数，历史无 status 剔除分母)、avg/max process_time；dimensions：时间(day 等)/用户/app（仿 mid_tool_call_dtl L312 范式）
  **依赖**: T005 | **覆盖 AC**: AC-03 | **设计**: design 决策 2、坑 #1/#2
  **测试**: 先红后绿：`test/telemetry_search/test_agent_run_dataset.py`：聚合 DSL 口径（成功率分母剔除无 status 历史）

- [x] **T008**: 调用日志 API
  **文件**: `src/backend/bisheng/telemetry_search/api/endpoints/call_logs.py`（新）+ router 注册
  **逻辑**: GET /api/v1/telemetry/call-logs；ES 直查 base_telemetry_events（application_process + model_invoke），参数 type/app_id/status/start/end/page/page_size；租户按「当前租户用户集合」terms 过滤 user_context.user_id（MySQL 查租户用户 id，本地缓存）；返回 records[{type, 名称, 用户, 状态, 耗时, token?, 时间}]；app 删除后历史可查（事件自带 app_name 快照）；时间范围上限 90 天，超限返回参数错误
  **依赖**: T001 | **覆盖 AC**: AC-04, AC-09, AC-10, AC-11, AC-12, AC-13 | **设计**: design 决策 3、5
  **测试**: 先红后绿：`test/telemetry_search/test_call_logs_api.py`：筛选/分页/租户隔离/90 天上限参数错误/删除应用后历史可查

### Wave 2（依赖 Wave 1）

- [x] **T009**: 预置看板组件种子 + 运维补数脚本
  **文件**: `src/backend/bisheng/telemetry_search/domain/init_dataset.py`（preset_oss_dashboard_sql 增加组件）、`src/backend/scripts/ops_seed_agent_monitoring.py`（新：已有环境补 dashboard_dataset/dashboard_component 行，**幂等**——upsert 按唯一键，重复运行安全）
  **逻辑**: dashboard id=10 增 8 个组件：4 指标卡（今日调用总量/成功率/平均/最大响应，dataset mid_agent_run_dtl）+ 2 图表（area 近7天调用趋势按天、stacked-bar 各智能体调用分布 Top N）+ 2 表格（智能体运行状态表：每智能体一行——调用量/成功率/平均/最大响应/最近活跃【AC-08 载体】；调用日志表：AC-09/10/11 载体）；table 组件 data_config 含 linkedComponentIds 时间联动
  **依赖**: T007 | **覆盖 AC**: AC-05, AC-06, AC-07, AC-08 | **设计**: design 决策 4、坑 #3
  **测试**: 手动：运行 ops 脚本后 GET /api/v1/telemetry/dashboard/10 含 8 个新组件；重复运行脚本无副作用

- [x] **T010**: 前端智能体运行状态表组件
  **文件**: `src/frontend/platform/src/pages/Dashboard/components/dashboard/AgentStatusTable.tsx`（新）、`controllers/API/dashboard.ts`（复用既有 component/query 封装）
  **逻辑**: 复用 component/query 引擎（mid_agent_run_dtl 聚合，app 维度 + 调用量/成功率/平均/最大响应/最近活跃列），接受 QueryFilter 联动时间，空态
  **依赖**: T008/T009 | **覆盖 AC**: AC-08 | **设计**: design 决策 4
  **测试**: 手动 + headless（表格渲染、空态、时间联动）

- [x] **T011**: 前端调用日志表组件
  **文件**: `src/frontend/platform/src/pages/Dashboard/components/dashboard/CallLogTable.tsx`（新）、`ComponentWrapper.tsx`（type=table 分发）
  **逻辑**: 直连 call-logs API，分页 + 类型/状态筛选 + 状态徽标 + 空态；接受 QueryFilter 联动时间；ComponentWrapper 对 type=table 分发到两个表格组件
  **依赖**: T008/T009 | **覆盖 AC**: AC-09, AC-10, AC-11 | **设计**: design 决策 4
  **测试**: 手动 + headless（表格渲染、筛选、空态、时间联动）

- [x] **T012**: 监控指标卡与图表渲染验证
  **文件**: 无新文件（复用 MetricCard/BaseChart；核对 dashboardStore 的 chartRefreshTriggers 对 8 个新组件生效）
  **逻辑**: 组件数据经 component/query 返回；成功率/响应时间数字格式（unitConversion duration 单位）；面积图/堆叠柱图数据转换（design 坑 #5 稀疏线 symbol）
  **依赖**: T007/T009 | **覆盖 AC**: AC-05, AC-06, AC-07 | **设计**: design 坑 #5
  **测试**: 手动：造 2-3 次运行后首页指标卡/图表数字正确、时间筛选联动

### Wave 3（端到端与收尾）

- [x] **T013**: e2e 验证（headless）
  **文件**: e2e 脚本（首页看板：指标卡渲染、图表、两张表格、时间筛选联动、无数据空态、无 board 权限不展示）
  **依赖**: T010/T011/T012 | **覆盖 AC**: AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-13, AC-14
  **测试**: headless Chromium 跑平台首页截图与断言

- [x] **T014**: 手动验证 + 文档
  **文件**: 运维文档（已有环境补数步骤、beat 重启、验证清单）
  **逻辑**: 跑一次会议纪要工作流 → ≤5 分钟首页指标 +1 且成功率正确；调用日志可见该次运行与内部模型调用；失败场景（停模型服务）成功率下降
  **依赖**: T013 | **覆盖 AC**: AC-02, AC-03, AC-05, AC-09, AC-11

- [x] **T015**: 代码评审 + 回归
  **逻辑**: /code-review --base main；全量 lint/typecheck（前后端）；确认既有统计看板无回归
  **依赖**: T014 | **覆盖 AC**: —
  **测试**: 既有测试全量回归

---

## 实际偏差记录

- T003/T004 上报点：工作站/灵思两处「到达收尾点=成功、失败路径抛异常不产生事件」（与现状一致），见 design 坑 #6
- T007 数据集测试降级：聚合 DSL 口径改为集成验证（首页组件查询实测 883 行、成功率/avg/max 数值正确）
- T010 前端未用 react-query（冻结依赖 no-restricted-imports），改用 useState+useEffect 手动拉取
- T009 调用日志表组件不参与 dataset 聚合（source=call_logs 直连新 API），design 决策 4 已说明
