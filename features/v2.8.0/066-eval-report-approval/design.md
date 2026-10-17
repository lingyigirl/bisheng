# Design: 评测报告与上线审批参考

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.8.0
**最后更新**: 2026-10-14

---

## 1. 目标与非目标

- **目标**：评测执行期实测采集性能指标；评测模块提供按需生成的 xlsx 报告（效果 + 性能）；智能体发布审批详情中注入该应用该版本最近一次成功评测的简要指标与报告下载链接，作为审批参考。
- **非目标**：硬门禁拦截、自动阈值判定、历史评测回填性能、多报告对比、P99 等扩展分位、其它审批场景。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7。
- **既有评测行为零回归**：结果 CSV 追加列须**纯增量**（旧文件缺列即 N/A）；F053 对比解析（读 `question`/`F1`/`recall`/`precision` 列）与 F054 失败行注入逻辑 SHALL NOT 改变语义。
- **无需数据库迁移**：本 feature **不新增表、不新增列**。逐题性能进结果 CSV；汇总指标在生成报告时现算。
- **评测主流程不可被性能采集阻断**：遥测/ES 不可达时降级 N/A（AC-03）。
- **错误码**：评测模块历史实现（含 F053 的 `get_evaluation_compare`）统一使用 `HTTPException` + detail，而非 `BaseErrorCode`。本 feature **沿用模块既有约定**，不新引入 MMM 段（避免为一个模块级错误码触发 `errcode/README.md` + 前端 `api_errors` 生成链的连锁改动）。此为有意偏离，若后续要统一，另起 feature 一次性收口。
- **无新增第三方依赖**：xlsx 用已在 `pyproject.toml` 声明的 `openpyxl>=3.1.5` + `pandas>=2.3.3`。

---

## 3. 方案对比与选定

### 决策 1：性能指标的采集方式

- **备选**：
  - A. 评测执行期**实测采集**（耗时在评测链内计时；Token 走遥测按题归集）
  - B. 复用线上遥测 ES 聚合（按 app_id + 时间窗统计）
- **选定**：A
- **原因**：门禁/验收场景要求的是**上线前**该版本的数据；B 只有旧版本线上数据，且新版本上线前无数据。B 还有归因缺陷：遥测事件无版本/评测维度，助手评测根本不产 `APPLICATION_PROCESS`（`AssistantAgent.run` 不经 `ChatClient`，见 §5 坑 2）。
- **何时该重新考虑**：需要长期线上质量趋势（非单次评测）时，B 作为补充视图另起 feature。

### 决策 2：Token 用量的归集路径

- **备选**：
  - A. 进程内 ContextVar 累加器（挂在 LLM wrapper 上）
  - B. 扩展工作流引擎的 Redis 事件通道，回传 usage
  - C. 按**每题 trace_id** 查统计 ES 的 `model_invoke` 事件求和
- **选定**：C
- **原因**：
  - A 不可行——工作流评测跑在**独立 Celery worker 进程**（`execute_workflow.apply_async`），ContextVar 不跨进程；助手评测虽在 API 进程，但两条路径无法统一。
  - B 可靠但需动工作流引擎（`WorkflowEventType` 枚举 + `event.py` Data 模型 + `LLMNodeCallbackHandler` 触发 + `RedisCallback.on_*` + 消费），回归面大（F054 已证明引擎路径易碎，见 §5 坑 1）。
  - C 的**归因键现成**：`execute_workflow_get_answer` 每道题独立 `generate_uuid()` 生成 run `unique_id`（`evaluation_service.py:355`），该值在 worker 内被 `trace_id_var.set(unique_id)`（`worker/workflow/tasks.py:103`），而 `model_invoke` 遥测事件的 `trace_id` 就取自同一 ContextVar（`llm/domain/utils.py:216`）。事件已带 `input_token/output_token/total_token/first_token_cost_time`。助手路径补一个 per-question trace_id 即可复用同一机制。
- **局限（已知并接受）**：遥测写入是 fire-and-forget，存在**索引延迟**；采集实现带短重试，仍取不到则记 N/A（AC-03/AC-08）。
- **何时该重新考虑**：要求 Token 100% 齐全、或评测规模大到 ES 查询成为瓶颈时，升级为 B。

### 决策 3：报告生成时机

- **备选**：
  - A. **按需生成**（点下载时现算 + 生成 xlsx + 传 MinIO + 返回预签名链接）
  - B. 评测完成时预生成落库（新增 `report_file_path` 列）
- **选定**：A（用户已确认）
- **原因**：无 DB 迁移、无历史数据回填问题、报告格式迭代不影响存量；报告体积小、生成频率低（仅下载时）。
- **何时该重新考虑**：报告需要作为不可变审计凭证长期留存、或需要批量导出时，改为 B。

### 决策 4：逐题性能数据的落点

- **备选**：
  - A. 追加为**结果 CSV 的新列**
  - B. 新增 DB 列/表
- **选定**：A
- **原因**：结果 CSV 已是评测的逐题明细载体，F053 对比、下载功能都基于它；追加列纯增量、零迁移。汇总指标在报告生成时从逐题数据现算，避免"聚合值随口径变化而失真"。
- **何时该重新考虑**：需要按性能维度做结构化查询/排序时，改为 B。

### 决策 5：审批详情的注入点

- **备选**：
  - A. 扩展 `AgentPublishApprovalHandler.build_detail`（提交时写 `detail_snapshot`）
  - B. 在 `ApprovalCenterService.get_task_detail` **动态注入**一个新字段
- **选定**：B
- **原因**：`build_detail` 只在**提交审批时执行一次并冻结**（`approval_gate.py:106-107`），而评测报告是提交后甚至审批期间才产生的数据，冻结值会过期。`get_task_detail` 是详情组装的唯一入口（`approval_center_service.py:99-203`），已有按 `scenario_code` 特判的先例（`menu_access_request` 的 `grant_revoked`，同文件 :151-156）。
- **何时该重新考虑**：报告需要固化进审批快照留痕时，改为提交时快照 + 详情透传。

### 决策 6：报告下载的访问控制

- **备选**：
  - A. 新增带授权校验的下载端点（校验"调用者是评测属主 **或** 持有引用该评测的审批任务"）
  - B. 详情接口/评测接口直接返回 **MinIO 预签名 URL**
- **选定**：B
- **原因**：审批人不是评测属主，A 需要一套跨模块的"审批引用"反查授权，复杂且易错。B 复用既有 `get_share_link_sync` 能力，链接本身**有时效**，且仅在**已鉴权的详情接口/属主接口**响应中下发（未鉴权拿不到链接），风险可控。报告定位是"参考"，敏感度低。
- **何时该重新考虑**：报告含敏感数据、或需要下载审计时，改为 A + 下载流水。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
评测执行（改造后）:
  add_evaluation_task(evaluation_id)
    ├─ 读数据集 CSV → parse_csv
    ├─ 逐题执行:
    │   ├─ t0 = perf_counter()
    │   ├─ workflow: execute_workflow_get_answer(...)  ← 返回该题 run unique_id（新增返回值）
    │   │   assistant: trace_id_var.set(f"eval-{eid}-{i}") → await agent.run(q)
    │   ├─ duration_ms = (perf_counter() - t0) * 1000
    │   └─ tokens = collect_token_usage(trace_id)   ← 查统计 ES（降级 → None）
    ├─ compute_answer_correctness(...)  ← 不变
    ├─ 组装逐题表: 既有列 + duration_ms / input_token / output_token / exec_status
    ├─ 上传结果 CSV（MinIO evaluation/result/*.csv，utf-8-sig）← 结构向后兼容
    └─ 更新 evaluation（result_score / status / result_file_path）← 不变

报告下载（按需）:
  GET /api/v1/evaluation/{id}/report/download
    ├─ 校验: 存在 && 属主 && status=success && result_file_path
    ├─ 读结果 CSV → 逐题 DataFrame
    ├─ 聚合: 效果总分（取 CSV 合计行 / 或重算） + 性能（成功率、avg/max/P95、token 总量、token/s、avg TTFT）
    ├─ 生成 xlsx（汇总 sheet + 逐题 sheet，openpyxl）
    ├─ 传 MinIO evaluation/report/{uuid}.xlsx
    └─ 返回 { url: <预签名> }

审批详情（注入）:
  GET /api/v1/approval/my-tasks/{task_id}
    └─ ApprovalCenterService.get_task_detail
        └─ if scenario_code == 'agent_publish_request':
             app_id = payload_snapshot['app_id']; version_id = payload_snapshot.get('version_id')
             评测 = EvaluationRepository.get_latest_success_by_app_version(unique_id=app_id, version=version_id)
             → evaluation_report = {
                 available, evaluation_id, evaluated_at, data_set, question_count,
                 f1/recall/precision, success_rate, avg_duration_ms, token_rate, download_url
               } | { available: False }
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| 结果 CSV 新列 | `duration_ms`(int) / `input_token`(int) / `output_token`(int) / `first_token_ms`(int) / `exec_status`(str) | 逐题性能；缺失为 `N/A`（字符串，沿用既有 N/A 口径）。`first_token_ms` 支撑汇总页「平均首 token 延迟」(AC-06) | 报告生成、对比兼容 |
| 每题 run trace_id（工作流） | `unique_id = generate_uuid()`，评测链内部变量 | 用于查遥测；**不落库**，运行期使用后丢弃 | Token 采集 |
| 每题 trace_id（助手） | `f"eval-{evaluation_id}-{question_index}"` | 写入 `trace_id_var`，使 `model_invoke` 事件可按题归因 | Token 采集 |
| 遥测事件 | ES `base_telemetry_events`，`event_type=model_invoke`，`event_data` 键前缀 `model_invoke_` | `*_input_token` / `*_output_token` / `*_total_token` / `*_first_token_cost_time` / `*_start_time` / `*_end_time` | Token 采集 |
| `evaluation_report`（审批详情响应新字段） | dict | 见 §4.3 接口文档 | 审批详情前端 |
| 报告 xlsx | 两个 sheet：`汇总` / `逐题明细` | MinIO `evaluation/report/{uuid}.xlsx` | 下载用户 / 审批人 |

### 4.3 接口文档

#### 4.3.1 下载评测报告（新增）

`GET /api/v1/evaluation/{evaluation_id}/report/download`

- 认证：登录用户；**仅评测属主**（AC-14）
- 入参：路径参数 `evaluation_id: int`
- 出参：`{ status_code: 200, status_message: "success", data: { url: "<MinIO 预签名链接，有效期 7 天>", file_name: "evaluation_report_{id}.xlsx" } }`
  - 预签名链接保留 host（`clear_host=False`）：平台端用 `checkSassUrl`、client 端用 `__APP_ENV__.BASE_URL` 归一化，两端同一套下载路径。
- 错误：
  | 场景 | HTTP | detail |
  |---|---|---|
  | 评测不存在 / 非属主 | 404 | `Evaluation not found` |
  | 评测未成功完成 | 400 | `Evaluation not finished or failed` |
  | 结果文件缺失 | 400 | `Evaluation result file not found` |

#### 4.3.2 审批任务详情（既有接口扩展）

`GET /api/v1/approval/my-tasks/{task_id}` — 响应新增字段：

```jsonc
{
  // ... 既有字段不变 ...
  "evaluation_report": {
    "available": true,
    "evaluation_id": 123,
    "evaluated_at": "2026-10-14 10:20:00",
    "data_set": "bankflow_testset.csv",
    "question_count": 12,
    "f1": "62.35%",
    "recall": "70.10%",
    "precision": "58.20%",
    "success_rate": "91.67%",
    "avg_duration_ms": 3210,
    "token_rate": 42.7,          // token/s，N/A 时为 null
    "download_url": "http://.../presigned"
  }
  // 无成功评测时：
  // "evaluation_report": { "available": false }
}
```
> 仅当 `scenario_code == "agent_publish_request"` 时该字段存在；其它场景不注入（保持既有响应形状）。

### 4.4 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `evaluation/domain/services/evaluation_service.py`（改） | 逐题耗时/成功标记采集；调用 Token 采集；扩展结果 CSV 列 | 不改评分逻辑、不改 F053/F054 语义 |
| `evaluation/domain/services/evaluation_metrics.py`（新） | `collect_token_usage(trace_id)` 查 ES + 解析；`summarize_performance(rows)` 聚合（成功率/avg/max/P95/token-rate） | 不写存储 |
| `evaluation/domain/services/evaluation_report.py`（新） | 读结果 CSV → 构建两个 sheet → xlsx bytes → 上传 MinIO；`build_report_payload(evaluation)` 生成审批用简要块 | 不校验权限（由调用方校验） |
| `evaluation/api/endpoints/evaluation.py`（改） | 新增报告下载端点 | — |
| `evaluation/domain/repositories/evaluation_repository.py`（改） | `get_latest_success_by_app_version`（租户内、不限定 owner） | — |
| `approval/domain/services/approval_center_service.py`（改） | `get_task_detail` 注入 `evaluation_report` | 不改审批流转 |
| `platform/src/pages/EvaluationPage/index.tsx`（改） | 列表新增「下载评测报告」 | — |
| `platform/src/controllers/API/evaluate.ts`（改） | 新增报告下载 API | — |
| `client/src/components/approval/ApprovalCenterDialog.tsx`（改） | `TaskDetailPanel` 新增评测报告区块（含下载） | 不改审批流转 |
| `client/src/api/approval.ts`（改） | `ApprovalTaskDetail` 新增 `evaluation_report` 类型 | — |

### 4.5 前端交互

- **评测列表**（platform）：每行操作区在「下载」旁增加「下载报告」，点击调报告接口 → 用既有 `downloadFile` 走预签名链接下载。
- **审批详情**（client，`TaskDetailPanel`）：在「业务内容」与「申请理由」之间插入**评测报告区块**：
  - 有报告：标题 + 关键指标网格（F1/召回/精确、成功率、平均耗时、Token 速率、评测时间）+ 「下载完整报告」按钮。
  - 无报告：`暂无评测报告`（灰色提示），**不影响同意/拒绝按钮**（AC-12）。
  - 注意：现有业务内容渲染一律 `String(v)`，**不产生链接**；本区块需独立渲染分支输出下载按钮（复用 `~/utils` 的 `downloadFile`）。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | 工作流评测执行在**独立 Celery worker 进程**（`execute_workflow.apply_async`），评测发起方只在 API 进程监听 Redis 事件 | 以为能用进程内 ContextVar/回调统一采集 Token → 工作流路径永远采不到 | 决策 2 选 C（ES 按 trace_id） |
| 2 | 助手评测**完全不产 `APPLICATION_PROCESS`** 遥测（`AssistantAgent.run` 不经 `ChatClient`），且 `llm_token_log`/`llm_call_log` 无 app_id 列 | 以为"线上遥测能覆盖助手评测" → 助手成功率/耗时永远为空 | 决策 1 选 A（实测）；耗时直接 in-process 计时 |
| 3 | `model_invoke` 事件 `event_data` 的键名带**事件名前缀**（`model_invoke_input_token`），且 mapping 为 `dynamic:true` | 按 `input_token` 直查为 0 | `evaluation_metrics.py` 统一加前缀 |
| 4 | 遥测写入是 **fire-and-forget**（`asyncio.create_task` / 线程池），查 ES 存在**索引延迟** | 刚跑完立刻查 → 查不到 → Token 全 N/A | 采集带短重试；仍失败记 N/A（AC-03） |
| 5 | 结果 CSV 的 `build_result_table` 会把 `unit_type != 1` 的列在合计行**求和**（`unit_type==3` 才求平均） | 直接把耗时/Token 塞进现有列 → 合计行把耗时求和，语义错 | 新列不进既有 `columns` 聚合口径；汇总由报告模块现算 |
| 6 | 审批 `detail_snapshot` 在**提交时冻结**（`build_detail` 只执行一次） | 把报告写进 `build_detail` → 审批期间报告不更新 | 决策 5 选 B（详情动态注入） |
| 7 | 审批 payload **没有应用名**，只有 `app_id` / `version_id` / `app_type`；应用名在 `business_name` | 试图从 payload 取名字失败 | 报告块基础信息用 `instance.business_name` |
| 8 | 审批详情前端对 `detail_snapshot` 的每个 value 一律 `String(v)`，无链接/按钮能力（`build_business_link` 是死代码） | 把 `download_url` 塞进 `detail_snapshot` → 只显示成一长串文本 | 独立 `evaluation_report` 字段 + 专用渲染分支 |
| 9 | 助手无版本：`evaluation.version` 为 `None`/0，审批 payload `version_id` 为空 | 按 version 精确匹配 → 助手永远匹配不到 | 决策 5 的按 `app_type` 分支：助手只按 `app_id` 匹配 |
| 10 | 评测表 `evaluation` 的列表查询**排除** `exec_type=flow`（`get_my_evaluations`），但 flow 评测已废弃 | — | 无需处理，匹配时按 app_type ∈ {workflow, assistant} |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `GET /api/v1/evaluation/{id}/report/download` | HTTP API | 评测列表页 |
| 结果 CSV 追加列（`duration_ms` / `input_token` / `output_token` / `exec_status`） | 文件格式（纯增量） | 报告生成、下载用户 |
| 审批详情 `evaluation_report` 字段 | HTTP API 响应字段 | 审批详情前端 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| 统计 ES `base_telemetry_events` + `model_invoke` 事件 schema | ES 索引/字段 | 字段名/前缀变化 → Token 采集失效（降级 N/A，不崩） |
| `trace_id_var` 与 worker 的 trace_id 设置点（`worker/workflow/tasks.py`） | 内部机制 | 引擎改动若移除 trace 设置 → 工作流 Token 归因失效 |
| `RedisCallback` / `execute_workflow_get_answer` 的 run unique_id | 内部函数 | 需返回 unique_id（本 feature 增加返回值） |
| 审批 `approval_instance`（`payload_snapshot` / `detail_snapshot` / `business_name`） | DB 表 | payload key 变化需同步 |
| MinIO `put_object_sync` / `get_share_link_sync` | 内部 API | — |
| `openpyxl`（xlsx 引擎） | 依赖 | 固定 `>=3.1.5`（已锁 3.1.5） |

---

## 7. 测试与可观测

- **单元**（pytest，`test/evaluation/`）：
  - 性能聚合：成功率含失败题、avg/max/P95 计算、Token 速率（总量/总耗时）、全 N/A → N/A
  - Token 采集：mock ES 返回 → 求和正确；ES 抛错 → 返回 None（不抛出）
  - 结果 CSV：新列存在、失败题新列为 N/A、旧 CSV（无新列）读取不报错
  - 报告生成：两个 sheet 名称与表头、xlsx 可被 `pandas.read_excel` 回读、N/A 单元格
  - 审批注入：`agent_publish_request` 有/无成功评测两种情况；非该场景不注入；助手按 app_id 匹配
- **e2e**：真实跑一次工作流评测 → 结果 CSV 含性能列 → 下载报告 → xlsx 打开正常；创建一个发布审批 → 审批详情返回 `evaluation_report`。
- **手动验证**：评测列表点「下载报告」；审批中心待办详情查看报告区块与下载。
- **可观测**：Token 采集失败与重试记 `logger.warning`（带 evaluation_id / trace_id）；报告生成失败记 `logger.exception`。

---

## 8. 后续改进 / 不打算做的事

- 硬门禁（报告合格才放行）——需要阈值配置与放行策略，另起 feature。
- 报告持久化留痕 + 下载审计（决策 3/6 的升级触发条件）。
- P99 / 分位数可配置、性能趋势对比（决策 1 的 B 方案）。
- 工作流引擎 Token 事件回传（决策 2 的 B 方案）——若要求 Token 100% 齐全。
- 其它审批场景的报告注入。

---

## 修订历史

| 日期 | 变更 |
|---|---|
| 2026-10-14 | 初稿（spec/design/tasks 同期创建） |
