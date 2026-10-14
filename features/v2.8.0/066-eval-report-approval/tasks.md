# Tasks: 评测报告与上线审批参考

**关联**: [spec.md](./spec.md) · [design.md](./design.md)
**版本**: v2.8.0
**最后更新**: 2026-10-14

## 状态

| 阶段 | 状态 |
|---|---|
| spec | ✅ |
| design | ✅ |
| tasks | ✅ |
| 实现 | ✅ |

## 开发模式

- 后端 **Test-First**（务实版）：先写 `test/evaluation/` 下单测，再实现；无中间件的纯函数优先。
- Wave 内无依赖任务可并行；Wave 间串行。
- 前端改动以手动验证为准（platform 评测列表 / client 审批详情）。

---

## Wave 1 — 基础设施（后端纯逻辑，无外部依赖）

### T01 性能指标采集模块
- **文件**：`src/backend/bisheng/evaluation/domain/services/evaluation_metrics.py`（新）
- **逻辑**：
  - `collect_token_usage(trace_id: str) -> dict | None`：查统计 ES `base_telemetry_events`（`event_type=model_invoke` + `trace_id`），用 `aggs` 求和 `event_data.model_invoke_{input,output,total}_token`、平均 `..._first_token_cost_time`；带短重试（索引延迟）；ES 异常/无命中 → `None`。
  - `summarize_performance(rows: list[dict]) -> dict`：成功率（含失败题）、avg/max/P95 耗时、Token 总量、Token 速率（总量 / 总耗时秒）、平均首 token 延迟；无有效样本 → N/A。
- **覆盖 AC**: AC-01/02/03/08
- **依赖**: 无
- **手动验证**: 单测 mock ES 返回与异常路径。

### T02 xlsx 报告生成模块
- **文件**：`src/backend/bisheng/evaluation/domain/services/evaluation_report.py`（新）
- **逻辑**：
  - `build_report_workbook(evaluation, rows) -> bytes`：`pd.ExcelWriter(engine="openpyxl")` 写两个 sheet（`汇总` / `逐题明细`）。
  - `generate_report_download_url(evaluation) -> str`：读结果 CSV → 生成 → 传 MinIO `evaluation/report/{uuid}.xlsx` → `get_share_link_sync`。
  - `build_report_summary(evaluation) -> dict`：审批详情用的简要块（`available` / 指标 / 下载链接）。
- **覆盖 AC**: AC-05/06/07/08
- **依赖**: T01（`summarize_performance`）
- **手动验证**: 单测生成 xlsx 后用 `pandas.read_excel` 回读校验 sheet 名与表头。

---

## Wave 2 — 后端执行链与接口

### T03 评测执行期性能采集接入
- **文件**：`src/backend/bisheng/evaluation/domain/services/evaluation_service.py`（改）
- **逻辑**：
  - `execute_workflow_get_answer` 返回 `(answer, trace_id)`（内部 `unique_id` 上抛）。
  - workflow 分支：每题 `perf_counter` 计时 + `collect_token_usage(trace_id)`；失败题同样计时。
  - assistant 分支：每题 `trace_id_var.set(f"eval-{eid}-{i}")`（finally reset）+ 计时 + `collect_token_usage`。
  - 逐题性能写入行 dict；结果表追加 `duration_ms` / `input_token` / `output_token` / `exec_status` 列（`unit_type=1`，不进既有聚合口径）；失败行同样带 `exec_status=failed`。
- **覆盖 AC**: AC-01/02/03/04
- **依赖**: T01
- **手动验证**: 单测校验新列与旧 CSV 兼容；真实跑一次评测观察结果 CSV。

### T04 报告下载接口
- **文件**：`src/backend/bisheng/evaluation/api/endpoints/evaluation.py`（改）
- **逻辑**：`GET /evaluation/{evaluation_id}/report/download` —— 属主校验 + `status=success` + `result_file_path` 校验（`HTTPException`，沿用模块既有约定）→ 调 `generate_report_download_url` → `resp_200({"url": ...})`。
- **覆盖 AC**: AC-09/14
- **依赖**: T02

### T05 仓储：按应用+版本取最近成功评测
- **文件**：`src/backend/bisheng/evaluation/domain/repositories/evaluation_repository.py`（改）
- **逻辑**：`get_latest_success_by_app_version(unique_id, version=None)` —— `is_delete=0` + `status=success` + `unique_id` 匹配（version 非空时按 version）→ `update_time desc` 取第一条；租户过滤由全局 Session 事件注入。
- **覆盖 AC**: AC-10/13/16
- **依赖**: 无

### T06 审批详情注入评测报告
- **文件**：`src/backend/bisheng/approval/domain/services/approval_center_service.py`（改）
- **逻辑**：`get_task_detail` 中，`scenario_code == "agent_publish_request"` 时按 `payload_snapshot` 的 `app_id`/`version_id`（助手仅 `app_id`）取最近成功评测 → `evaluation_report = build_report_summary(...)`（无 → `{"available": False}`）注入响应。
- **覆盖 AC**: AC-10/11/12/15
- **依赖**: T02、T05
- **手动验证**: 构造/复用发布审批，观察详情响应含 `evaluation_report`。

---

## Wave 3 — 前端

### T07 评测列表「下载评测报告」
- **文件**：`src/frontend/platform/src/controllers/API/evaluate.ts`（改）、`src/frontend/platform/src/pages/EvaluationPage/index.tsx`（改）
- **逻辑**：新增 `getEvaluationReportApi(id)`；列表操作区在「下载」旁增加「下载报告」（`status=success` 时可用），调接口取 url 后 `downloadFile`。
- **覆盖 AC**: AC-05
- **依赖**: T04

### T08 审批详情评测报告区块
- **文件**：`src/frontend/client/src/api/approval.ts`（改）、`src/frontend/client/src/components/approval/ApprovalCenterDialog.tsx`（改）、`src/frontend/client/src/locales/{zh-Hans,en,ja}/translation.json`（改）
- **逻辑**：`ApprovalTaskDetail` 增加 `evaluation_report?` 类型；`TaskDetailPanel` 在业务内容与申请理由之间渲染报告区块（有 → 指标网格 + 下载按钮；无 → 暂无评测报告提示）；三语补 `com_approval_eval_report_*` key。
- **覆盖 AC**: AC-10/11/12
- **依赖**: T06

---

## Wave 4 — 校验

### T09 测试与质量门禁
- **文件**：`src/backend/test/evaluation/test_eval_report.py`、`test_eval_perf_metrics.py`（新）
- **逻辑**：覆盖 T01/T02 纯函数、新列兼容、审批注入分支。
- **执行**：`cd src/backend && uv run pytest test/evaluation/`
- **前端**：`cd src/frontend && pnpm lint && pnpm typecheck`

---

## 实际偏差记录

| # | 偏差 | 原因 / 落档 |
|---|---|---|
| 1 | 逐题性能列由 4 列增至 5 列：新增 `first_token_ms` | AC-06 要求「平均首 token 延迟」，遥测 `first_token_cost_time` 需落到逐题数据；已回写 design.md §4.2 |
| 2 | 报告下载接口出参含 `file_name`（原文档仅 `url`） | 下载文件名由后端给出，避免前端硬编码；已回写 design.md §4.3.1 |
| 3 | 预签名链接用 `clear_host=False`（绝对 URL） | 审批页（client）不在评测模块内，无法用模块既有的相对链接约定；绝对链接两端各自归一化，已回写 design.md §4.3.1 |
| 4 | 顺带把 `app_name`/`app_type` 加入前端 `localizeFieldKey` 映射 | 发布审批的业务内容此前显示原始英文 key；属本 feature 触及面，低风险 |
| 5 | 未给报告接口新增错误码，沿用 `HTTPException` | 评测模块既有约定（含 F053 的 `get_evaluation_compare`）；引入新 MMM 段会触发 errcode README + 前端 `api_errors` 生成链，已记于 design.md §2 |

## 验证结果

- 后端单测：`cd src/backend && ./.venv/Scripts/python.exe -m pytest test/evaluation/ -q` → **70 passed**（含新增 `test_eval_perf_metrics.py` / `test_eval_report.py`）
- 前端类型检查：`pnpm --filter bisheng typecheck` → 通过；`pnpm --filter bishengchat typecheck` → 失败 6 项，**全部为既有问题**（`src/components/Linsight/Execution/*`，本 feature 未触及）
- 前端 lint：改动的 4 个文件（platform 2 + client 2）→ **0 error**；全量 lint 的既有失败项（platform 54 / client 5）均为本 feature 未触及的文件
- 无数据库迁移：本 feature 不新增表/列

## 端到端验证步骤（人工）

1. 后端：`cd src/backend && uv run python -m bisheng.server`（或按 `docs/本地后端启动-Windows.md`）；前端：`pnpm dev:platform` + `pnpm dev:client`
2. 评测模块新建一次评测（工作流类型，需已配置评测模型）→ 等待完成 → 下载结果 CSV，确认含 `duration_ms`/`input_token`/`output_token`/`first_token_ms`/`exec_status` 列
3. 评测列表点「下载评测报告」→ 检查 xlsx 两个 sheet 与 N/A 单元格
4. 对同一应用同一版本发起「上线」→ 走审批 → 以审批人身份打开「审批中心 → 我的审批 → 待我处理」→ 查看评测报告区块、点「下载完整报告」
5. 反向验证：对**无成功评测**的应用发起上线审批 → 详情显示「暂无评测报告」且同意/拒绝按钮可用
