# Tasks: 智能体监测导出微调数据集

**关联**: [spec.md](./spec.md) · [design.md](./design.md)
**版本**: v2.8.0
**最后更新**: 2026-10-20

## 状态

| 阶段 | 状态 |
|---|---|
| spec | ✅ 2026-10-20 用户确认 |
| design | ✅ 2026-10-20 用户确认（新增 §12 微调链路研究结论） |
| tasks | ✅ 2026-10-20 用户确认 |
| 实现 | ✅ 代码完成：后端 36 项单测通过；前端 ESLint / check-i18n 通过；真实数据导出 47 条格式正确 |

> 说明：实现先于 spec/design ★ 确认完成；口径已确认，按 design §10 决策表执行。

---

## Wave 1 — 后端（Test-First）

### T01 导出 schema 与文案
- **文件**：`src/backend/bisheng/telemetry_search/domain/schemas/agent_transactions.py`
- **内容**：`ExportFeedback = all | liked | exclude_disliked`；`AgentTransactionExportQuery`（feedback 默认 `exclude_disliked`，limit 默认/上限 5000）。
- **覆盖 AC**: AC-05, AC-07
- **状态**: ✅

### T02 问答配对与样本构建单元测试
- **文件**：`src/backend/test/telemetry_search/test_agent_transactions.py`
- **内容**：文本解包（问题 5 种 / 答案 6 种）；当轮优先、缺失回退、忽略下一轮（含答案 slack 窗口内）；反馈三模式；去重；不完整样本跳过；超限拒绝（含额外命中不可映射）。
- **覆盖 AC**: AC-03, AC-04, AC-05, AC-11, AC-12, AC-13
- **状态**: ✅（36 passed）

### T03 后端导出服务
- **文件**：`src/backend/bisheng/telemetry_search/domain/services/agent_transactions.py`
- **内容**：`_map_export_trace` / `_load_chat_rows` / `_pick_qa` / `build_finetune_samples` / `_query_export_transactions` / `AgentTransactionsService.export_finetune_dataset`。
- **覆盖 AC**: AC-03 ~ AC-07, AC-10, AC-11, AC-12
- **依赖**: T02
- **状态**: ✅

### T04 导出端点
- **文件**：`src/backend/bisheng/telemetry_search/api/endpoints/agent_transactions.py`
- **内容**：`GET /export`，复用筛选参数 + `feedback` / `limit`，返回 JSON 附件；权限与时间窗在 service 内复用。
- **覆盖 AC**: AC-01, AC-06, AC-07, AC-08, AC-09
- **依赖**: T03
- **状态**: ✅

---

## Wave 2 — 前端

### T05 API 封装
- **文件**：`src/frontend/platform/src/controllers/API/log.ts`
- **内容**：`exportAgentTransactionsApi`（blob + silent）、`ExportFeedback` 类型。
- **覆盖 AC**: AC-03, AC-13
- **状态**: ✅

### T06 监测页入口
- **文件**：`src/frontend/platform/src/pages/LogPage/agentMonitor/index.tsx`
- **内容**：反馈筛选下拉（默认排除差评）、导出按钮、确认框（敏感数据提示）、Blob 下载、Blob 错误体解析。
- **覆盖 AC**: AC-01, AC-02, AC-05, AC-13
- **依赖**: T05
- **状态**: ✅

### T07 三语文案
- **文件**：`src/frontend/platform/public/locales/{zh-Hans,en-US,ja}/bs.json`
- **内容**：`monitorFeedback*` / `monitorExport*` 共 9 个 key，三语同 PR。
- **覆盖 AC**: AC-01, AC-02
- **依赖**: T06
- **状态**: ✅

---

## Wave 3 — 验证与交付

### T08 后端测试
- `uv run pytest test/telemetry_search/test_agent_transactions.py -q`
- **状态**: ✅ 36 passed

### T09 前端门禁
- `cd src/frontend && pnpm typecheck`
- `cd src/frontend && pnpm check-i18n`
- 对改动文件跑 ESLint（分支存在既有 lint 债务，需区分新增问题）
- **状态**: ✅ ESLint（含历史豁免）与 `check-i18n` 通过；`typecheck` 因本机 WSL 内存被杀，IDE 诊断确认改动文件无新增类型错误

### T10 本地联调
- 起后端 + platform，用近 7 天真实数据导出：
  - 三种反馈模式各导一次，核对条数；
  - 抽查 3 条问答与原始会话一致；
  - 把文件上传到微调指令集校验入口，确认格式通过；
  - 构造超限（>5000）或临时调小 limit 验证 400 提示。
- **状态**: 🟡 已用真实数据导出（全部反馈 47 条，字段与模板一致、无空值/重复）；超限 400 与微调页上传待验

### T11 提交
- 后端 + 前端 + feature 文档同一分支提交；用户确认后再推送。
- **状态**: ✅ 2026-10-20 已提交 `0aa3a1b44` 并推送 `feat/2.7.0/beijing-ns-llm-guardrail`；评审后修复（下一轮答案截止 / 超限按原始命中计数）另随 F090 审计修复一并提交
