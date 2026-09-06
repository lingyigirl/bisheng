# Tasks: 评测支持表单型工作流（form_input 智能体的版本对比评测）

> 本文档 = 执行流水（append-only 运行日志）。How 的真相在 [design.md](./design.md)，验收口径在 [spec.md](./spec.md)。

## 状态表

| 文档 | 状态 | 评审结论 / 备注 |
|------|------|----------------|
| spec.md | ✅ 已评审 | 2026-09-05 用户确认（form_values JSON 列/对象路径+运行时签名/ground_truth 必填 question 可选） |
| design.md | ✅ 已评审 | 2026-09-05 用户确认 |
| tasks.md | ✅ 已拆解 | 见下 |

## 任务列表（按 Wave 执行；每任务 1-2 文件，一次会话内可完成）

### Wave 1 — 数据集解析与校验（后端）

- [x] **T-01（测试先行）** 新建 `test/evaluation/test_eval_form_dataset.py`：`parse_csv` 扩展——form_values 列存在时逐行 JSON 校验（非法 JSON → 报错带行号）；question 列缺失 → 每行补空字符串；ground_truth 缺失 → 拒绝。 覆盖 AC: AC-01, AC-10
- [x] **T-02** `evaluation/domain/services/evaluation_service.py` 的 `parse_csv` 扩展 + 创建端点 CSV 校验改为 ground_truth 必填（question 可选），跑 T-01 转绿。 覆盖 AC: AC-01, AC-10

### Wave 2 — 表单执行分支与失败隔离（后端）

- [x] **T-03（测试先行）** 新建 `test/evaluation/test_eval_form_payload.py`：payload 构建——label→运行时 key 映射；label 不存在/重复 → 失败信号；schema 全部项填值（可选文本空串）；文件路径转预签名（mock minio：`tmp-dir/` 前缀走 tmp 桶、`://` 原样、普通路径默认桶）；字符串自动转数组。 覆盖 AC: AC-02, AC-03, AC-04, AC-05
- [x] **T-04** `evaluation/domain/services/evaluation_service.py`：`execute_workflow_get_answer` 新增 form_input 分支（映射/签名/提交/continue/复用答案采集）+ workflow 分支逐题 try/except 隔离 + 失败行注入结果表（answer=`[执行失败] 原因`、指标 N/A、合计行之前）；全部失败 → 任务失败；跑 T-03 转绿。 覆盖 AC: AC-03, AC-04, AC-05, AC-07, AC-08, AC-09
- [x] **T-05（回归）** 问答型数据集评测走原路径（单版本 + 对比组各跑一次回归断言行为不变）；权限/租户口径不变。 覆盖 AC: AC-06, AC-12, AC-13

### Wave 3 — 前端模板

- [x] **T-06** `platform/public/dataset.csv` 下载模板加 `form_values` 列（含注释示例）。 覆盖 AC: AC-01（数据集作者体验）

### Wave 4 — 构建、e2e 与文档

- [x] **T-07** `/e2e-test features/v2.7.0/054-eval-form-workflows`：API 级——流水分析智能体（v28）+ 含 form_values 的数据集（引用 tmp-dir 既有流水文件）跑真实评测至 success；单题坏路径（文件对象不存在）→ 该题 `[执行失败]` 其余正常；双版本对比评测（F053 流程）对表单型可用；页面手动验证清单。 覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13
- [x] **T-08** 前端重新构建（模板生效）+ 后端重启；headless 验证评测列表与对比视图对表单型评测正常。 覆盖 AC: AC-11
- [x] **T-09** `/code-review --base main`；实现偏差回写 design.md。 覆盖 AC: —
- [x] **T-10（追加需求 2026-09-06）** 结果 CSV 展示测试案例：`evaluation_service.py` 新增 `format_form_values()`/`_overlay_form_question()`（表单行 question 列填可读摘要，评分输入不变）+ 失败行同规则 + 结果 CSV 改 `utf-8-sig`（compare 读取同步 `utf-8-sig`，兼容旧文件）；新增 `test/evaluation/test_eval_form_result_csv.py`（7 测试）；spec §3/PRD、design 决策 7 + §8 回写。 覆盖 AC: 追加需求（下载文件展示测试案例及测试值）

## 执行日志

| 时间 | 记录 |
|------|------|
| 2026-09-05 | spec/design 评审通过（用户确认）；tasks 拆解完成 |
| 2026-09-05 | 实现完成：evaluation 36 测试全绿（+13 新测试）；真实 e2e——流水智能体 v28/v34 双版本表单评测均 success（此前报 Only Q&A）、坏题隔离（[执行失败] 行注入 + N/A 不参与聚合）、F053 对比联动验证通过；code-review 7 维度 PASS（B007 为文件既有 lint 债，未动）；评判 LLM 对长报告答案产出 N/A 属既有评判层行为（design 坑 8 已记录） |
| 2026-09-06 | T-10 完成：结果 CSV 表单行 question 列填案例摘要（用户选定方案 A，design 决策 7；顺带修复表单型对比逐题配对）；evaluation 43 测试全绿（+7 新测试）；BOM 往返验证通过 |
| 2026-09-06 | 真实 e2e 验证（流水智能体 v28/v34 双版本表单评测）：首轮 3/4 题失败暴露两个 bug——① `_overlay_form_question` 的 strict zip 把合计行计入（ValueError）；② 既有潜伏 bug `find_task_node_sync` 调 async `update_alive_nodes` 未 await → 冷启动 API 注册表空 → 工作流落到 default 队列（坑 9）。两处修复后重跑：43/44 均 success，结果 CSV question 列摘要正确、F053 对比逐题配对/总分/结论（v2 更好）全部生效 |
| 2026-09-06 | 追加：评测默认指令改中文版（defaultPrompt.ts，zh-Hans 中文模板 + 4 中文示例、en-US/ja 保持原英文模板；按界面语言选择；LangChain 渲染验证通过；platform 已重新构建）。中文模板真实验证时暴露坑 10（reuse 解包顺序错误）→ 修复端点后重验 |
