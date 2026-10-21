# Tasks: 评测可视化对比报告

**关联**: [spec.md](./spec.md) · [design.md](./design.md)
**版本**: v2.8.0
**最后更新**: 2026-10-20

## 状态

| 阶段 | 状态 |
|---|---|
| spec | ✅ 已评审 |
| design | ✅ 已评审 |
| tasks | ✅ 已拆解 |
| 实现 | ✅ T01–T18 完成：后端 124 passed；platform lint 基线不变 / typecheck 全绿；本地真实环境两接口已跑通（报告 JSON + 导出）。2026-10-20 审计修复 4 项（见实际偏差记录），后端 19 passed（本文件） |

## 开发模式

- 后端 **Test-First 配对**：每个纯函数 / service / 端点先写单测任务（红）再实现任务（绿）。
- Wave 内无依赖任务可并行；Wave 间串行。
- 前端以**手动验证**为准（platform 报告页）。
- **复用边界**（design 决策 3）：F066 的 `summarize_performance` / `collect_token_usage`、F051 的 `compare_flow_versions`、F053 的 `_parse_percent` / `_align_questions` **直接 import 复用**；**不改**同事已推送文件。自建 = 跳总行 reader / 结论引擎 / 幻觉率 / 报告组装 / 两版导出布局。
- 新文件 `evaluation_compare_report.py` 承载 Wave 1/2 的后端逻辑，**不碰**老 `get_evaluation_compare` 与 `CompareSheet.tsx`。

---

## Wave 1 — 后端纯逻辑（Test-First 配对，无外部依赖）

### T01 结果 CSV reader 单元测试
- **文件**：`src/backend/test/evaluation/test_eval_compare_reader.py`（新）
- **逻辑**：先写测试——含**末尾总行**的 CSV → 题数/成功数**不含总行**；纯数值列（无失败题）延迟/Token 解析**正确**（design 坑 2）；旧 CSV（无 `exec_status`）→ 成功率 **N/A** 不报错；失败题 → 各指标 N/A 且不入聚合。
- **覆盖 AC**: AC-09, AC-10
- **依赖**: 无

### T02 结果 CSV reader 实现
- **文件**：`src/backend/bisheng/evaluation/domain/services/evaluation_compare_report.py`（新）
- **逻辑**：读 MinIO 结果 CSV（`utf-8-sig`）→ **按 `question` 为空/NaN 剔总行**（**不能只靠 `dropna(how="all")`**，design 坑 3）；延迟 / Token 走**独立数值解析**（design 坑 1/2）；无 `exec_status` 列 → 成功率 N/A。
- **覆盖 AC**: AC-09, AC-10
- **依赖**: T01
- **测试**: T01 全绿

### T03 结论引擎单元测试
- **文件**：`src/backend/test/evaluation/test_eval_compare_conclusion.py`（新）
- **逻辑**：先写测试——优先级链 准确率→幻觉率→成本→延迟；**方向归一**（越低越好）；**分类容差**；打平**下探**；四项全平 `unable`；`analysis` 在**非最高优先级决定**时写明"下探"。
- **覆盖 AC**: AC-03, AC-04, AC-05
- **依赖**: 无

### T04 结论引擎实现
- **文件**：`evaluation_compare_report.py`（新）
- **逻辑**：`conclusion`（`"v1"|"v2"|"unable"`，**中性、无指标名/✅❌**，AC-03）+ `analysis`（模板拼句、含下探、**不调大模型**，AC-05）；优先级链 + 方向归一 + 分类容差（design 决策 4）；**复用** F053 `_parse_percent` / `_align_questions`（仅 import）。
- **覆盖 AC**: AC-03, AC-04, AC-05
- **依赖**: T03
- **测试**: T03 全绿

### T05 对比对象条单元测试
- **文件**: `src/backend/test/evaluation/test_eval_compare_object_bar.py`（新）
- **逻辑**：先写测试——三情形（仅模型变 / 仅提示词变 / 都变→`both`）；两版节点列表齐全；`model_id`→名解析；解析失败降级显示 id。
- **覆盖 AC**: AC-02
- **依赖**: 无

### T06 对比对象条实现
- **文件**: `evaluation_compare_report.py`（新）
- **逻辑**：取两版各自 `flow_version.data['nodes']` 里 `type=='llm'` 节点读 `system_prompt`/`user_prompt`/`model_id`；变更项用 `compare_flow_versions`（**import F051，不改**）的 `node_changes`；`model_id`→`LLMModel.get_model_by_ids` 查名（design 决策 9）。
- **覆盖 AC**: AC-02
- **依赖**: T05
- **测试**: T05 全绿

### T07 幻觉率单元测试
- **文件**: `src/backend/test/evaluation/test_eval_compare_hallucination.py`（新）
- **逻辑**：先写测试——Σ/Σ 聚合（**非逐题均值**，design 坑 4）；`contradicted ⊆ FP` **不重复计数**（坑 5）；无第 4 键（旧裁判输出）→ N/A；默认模板 + 用户 prompt **两路**都拼入第 4 桶指令。
- **覆盖 AC**: AC-11, AC-12
- **依赖**: 无

### T08 幻觉率实现（裁判第 4 桶 + 落 CSV 列）
- **文件**: `src/backend/bisheng/evaluation/domain/services/answer_correctness.py`（改）、`evaluation_service.py`（改 `columns`）
- **逻辑**：
  - `compute_answer_correctness`：**最终裁判模板**（默认 `CORRECTNESS_PROMPT` **与** 用户 `prompt` **两条路**）在**占位符之外固定拼接**第 4 桶指令（AC-12，design 决策 6）；解析新键（`item.get(_KEY_MAP[...], "")` 本容错，坑 6）；多返回 `statements_contradicted_num` / `statements_num_answer` 两组逐题数组。
  - `add_evaluation_task` 的 `columns` 追加这两列（`unit_type=1`，不进既有聚合，design 决策 10）；**不动** F066 的 `PERF_COLUMNS` 逻辑。
- **覆盖 AC**: AC-11, AC-12
- **依赖**: T07
- **测试**: T07 全绿
- **影响范围**（跨 Feature 副作用）：触及**评测执行链**共享文件；仅**纯增量**加桶 + 加列，**不改**既有 TP/FP/FN 口径与公式、**不改** F066 perf 列。

---

## Wave 2 — 后端组装与接口（Test-First 配对）

### T09 报告组装 service 单元测试
- **文件**: `src/backend/test/evaluation/test_eval_compare_report_service.py`（新）
- **逻辑**：先写测试——按 `compare_group_id` 取成员、按 `version` 升序定 v1/v2（AC-01）；`metrics[]` 6 指标不判达标（AC-06）；`per_question[]` 全 6 指标 + 方向翻转（AC-08）；标题统计 / 失败原因（AC-09/10）；跨租户/非属主不可访问（AC-15）。
- **覆盖 AC**: AC-01, AC-06, AC-08, AC-09, AC-10, AC-15
- **依赖**: T02, T04, T06, T08

### T10 报告组装 service 实现
- **文件**: `evaluation_compare_report.py`（新，主组装）
- **逻辑**：`EvaluationRepository.get_compare_group(group_id, user_id)`（**按组取成员**，每组恰 2 条，坑 11）；每版读 CSV（T02）+ `summarize_performance`（**import F066，不改**）+ `version_name`（`FlowVersion.name`）/ 应用名 / 数据集名；组装 `evaluations[2]` / `object_bar`（T06）/ `conclusion`+`analysis`（T04）/ `metrics[]` / `charts` / `per_question[]`（配对 + **全 6 指标**）。**只读**，不新增表/列。
- **覆盖 AC**: AC-01, AC-06, AC-08, AC-09, AC-10, AC-15
- **依赖**: T09
- **测试**: T09 全绿

### T11 报告数据接口 单元测试
- **文件**: `src/backend/test/evaluation/test_eval_compare_api.py`（新）
- **逻辑**：TestClient——happy path；组不存在/非属主 → 404；两版非同一智能体 → 400。
- **覆盖 AC**: AC-01, AC-15
- **依赖**: T10

### T12 报告数据接口 实现
- **文件**: `src/backend/bisheng/evaluation/api/endpoints/evaluation.py`（改）
- **逻辑**: `GET /api/v1/evaluation/compare/{group_id}/report` —— 登录校验 → 调 T10 → `resp_200`；错误沿用模块 `HTTPException` 约定（design §2）。
- **覆盖 AC**: AC-01, AC-15
- **依赖**: T11
- **测试**: T11 全绿

### T13 导出接口 单元测试
- **文件**: `src/backend/test/evaluation/test_eval_compare_export.py`（新）
- **逻辑**：先写测试——xlsx 两 sheet 可 `pandas.read_excel` 回读；含**全 6 指标逐题值**；`format` 非 `xlsx` → 400。
- **覆盖 AC**: AC-13, AC-14
- **依赖**: T10

### T14 导出接口 实现（后端生成 xlsx）
- **文件**: `evaluation/api/endpoints/evaluation.py`（改）、`evaluation_compare_report.py`（改：两版 sheet 生成）
- **逻辑**: `GET /api/v1/evaluation/compare/{group_id}/report/export?format=xlsx` —— 基于**与报告页同一份数据**（T10）生成 xlsx（**复用 F066 openpyxl 写 sheet/单元格 + N/A 口径**；**两版对比布局自建**，design 决策 7）→ 传 MinIO → 返回预签名 `{url, file_name}`；**不随界面所选指标变化**（AC-13）、**不依赖前端截图**（AC-14）。
- **覆盖 AC**: AC-13, AC-14
- **依赖**: T13
- **测试**: T13 全绿

---

## Wave 3 — 前端 Platform（手动验证）

### T15 报告页 组件 + 路由 + 入口 + API + i18n
- **文件**: `src/frontend/platform/src/pages/EvaluationPage/compare/CompareReport.tsx`（新）、`src/routes/index.tsx`（改，注册 `evaluation/compare/:groupId`）、`src/pages/EvaluationPage/index.tsx`（改，入口指向新路由）、`src/controllers/API/evaluate.ts`（改，新增报告数据 API）、`platform/src/locales/{zh-Hans,en,ja}/*`（改，三语文案）
- **逻辑**：对比对象条（多变量标"不可单独归因"，AC-02）/ 一句话结论（中性，AC-03）/ 对比分析（AC-05）/ 指标卡 6 项不判达标（AC-06）/ 逐题明细**下拉切指标**、**更好更差按所选指标方向翻转**（准确率/F1/召回 越高越好，幻觉率/延迟/Token 越低越好，AC-08）/ 标题统计 + 失败原因（AC-09/10）。**新文案三语**；**不复用** `compareBetter`（硬编码"准确性更高"）；**不改** `CompareSheet.tsx`。
- **覆盖 AC**: AC-01, AC-02, AC-03, AC-05, AC-06, AC-08, AC-09, AC-10
- **依赖**: T12
- **手动验证**：`/evaluation` → 某对比组「对比」→ 报告页；切指标看方向翻转；下钻失败题看原因；多变量组看标注。

### T16 图表（分组柱状 + 逐题折线）
- **文件**: `src/frontend/platform/src/pages/EvaluationPage/compare/ReportCharts.tsx`（新）
- **逻辑**：`recharts`（既有依赖，不新增）——分组柱状（准确率/F1/召回 **同 % 共用一根轴**）；逐题折线（横轴题号、两版两条线）；**延迟 / Token 若出图各自单开、独立纵轴**（design 坑 1）。单文件 ≤ 600 行。
- **覆盖 AC**: AC-07
- **依赖**: T15
- **手动验证**：图表渲染正确；两版两色；数据切换后重绘。

### T17 导出按钮
- **文件**: `CompareReport.tsx`（改）、`evaluate.ts`（改）
- **逻辑**：导出按钮 → 调 T14 接口 → 既有 `downloadFile` 走预签名链接。
- **覆盖 AC**: AC-13
- **依赖**: T14, T15
- **手动验证**：点导出 → 下载 xlsx → 核对**全 6 指标逐题值都在**（不随界面当前所选指标变化）。

---

## Wave 4 — 校验

### T18 测试与质量门禁 + 回归 + e2e
- **执行**: `cd src/backend && uv run pytest test/evaluation/`；前端 `cd src/frontend && pnpm lint && pnpm typecheck`（改动文件 0 error）。
- **回归**: **单版本评测（无对比组）行为不变**（AC-16）；跨租户/非属主不可访问（AC-15）。
- **e2e**（`/e2e-test`）：真实跑一次工作流双版本对比评测 → 报告页渲染 / 切指标 / 下钻 → 导出 xlsx 打开正常。
- **覆盖 AC**: AC-16
- **依赖**: T12, T14, T17

---

## 实际偏差记录

> 只留一行指针，论证在 design.md（决策 / 坑）。推翻已 ★ 确认的决策前先停下与用户重新确认。

- T15：`EvaluationPage/index.tsx` 移除旧 `CompareSheet` 挂载与入口 state，入口按钮改为 `navigate(/evaluation/compare/{groupId})`；**`CompareSheet.tsx` 文件本体未改**（保持冻结，仅不再被引用）。
- T15：指标文案前端**按 key 本地化**（`METRIC_I18N`），不使用后端中文 `label`（后端 label 仅作兜底），确保 en/ja 不出现中文。
- T15 补充（用户要求）：对比对象条在 `change_kind === "none"` 时显示黄色警示——「两版模型与提示词均无变化；若只是同一配置重跑，差异属于运行波动，不能作为版本优劣依据」（三语 `reportNoVariableWarning`）。
- T14：导出的成本列标题用「成本」而非「成本(Token)」，与报告页指标卡文案（`reportMetricToken`）区分。
- **T18 本地验证发现并修复 bug**：`CONCLUSION_CHAIN` 首档键写成 `"accuracy"`，而全链路产出的指标键是 `"precision"`（准确率=precision）。键不匹配 → 该档被静默跳过，结论实际上从「幻觉率」档开始判，且 `analysis` 误写"准确率打平后…"。已把链键改为 `"precision"`；单测原先喂的是 `"accuracy"`（自洽，故漏网），已改为 `"precision"` 并在 service 集成测试加断言（analysis 含"准确率"且不含"打平后"）。
- T17（本地验证后按用户要求增强，纯 F090）：`build_analysis` 由一句话扩为四段——结论依据（优先级链/决定指标/容差）、6 项指标逐项方向、数据完整性（逐题点名 N/A 原因：执行失败附原因 / 判分不完整缺哪几项 / 单侧缺失）、跨题提示（指标由不同题目聚合时告警）；前端分析文本加 `whitespace-pre-line`。仍为模板拼句、不调大模型。
- T17 补充（本地真实数据发现）：当某指标的 v0/v1 聚合值来自**完全不同的题目**（两侧题目集合无交集）时，结论引擎直接判 `unable`——即使 token/延迟等低优先级指标有差异，也不允许据此定输赢；分析文案点名不可比的指标并说明「不同题目、不可直接比较」，跨题提示保留。实现：`_disjoint_metric_keys` + `decide_conclusion(..., unusable_metrics)`；三语 `reportConclusionUnable` 文案扩展为「容差内或数据不可比」。
- **T14/T17 导出形态调整（2026-10-19，用户要求）**：导出应是可直接转发的**完整报告**；普通用户只需要 **PDF 与 Word(.docx)**，Excel 不再作为面向用户的导出形态。实现：`build_export_html`（自包含 HTML：内联 CSS + SVG 图表）、`build_export_pdf`（项目既有 Playwright/Chromium，A4）、`build_export_docx`（python-docx + matplotlib 图表）；`EXPORT_CONTENT_TYPES` 支持 `xlsx|html|pdf|docx`，接口默认仍 `xlsx` 以兼容旧调用；前端导出下拉只展示 PDF / Word。**未新增 Python 依赖**（playwright / python-docx / matplotlib 均已在 pyproject）。PDF 运行前提：`playwright install chromium` + 系统库；本机 WSL 缺 `libnspr4/libnss3/libasound2`，已非 root 解包到 `.local-pw-libs/`（gitignore）并在 API 启动时注入 `LD_LIBRARY_PATH`，部署镜像需同样具备。**命名优化（同日）**：下载文件名由 `evaluation_compare_<groupId>` 改为「智能体名 + 评测报告 + 版本对比」，如 `流水分析智能体评测报告_v2-json-extraction_vs_v3.pdf`；非法字符替换为 `_`、截断 120 字符；前端下拉项文案统一为「PDF 文档（.pdf）」「Word 文档（.docx）」，仅 PDF/Word 两项。**分析去重（同日，用户要求）**：对比分析不再逐项重复六个指标的数值/变化量（下方指标卡已展示），改为「一行方向汇总（各版本更优几项、持平几项、缺数据/不可比几项）+ 一行参与结论判定说明」；`结论依据` 仍保留决定指标的数值与容差，`数据完整性`/`跨题提示` 不变。导出 PDF/Word 的分析文本同步精简，指标表仍完整保留。

- **审计修复（2026-10-20，用户拍板）**：
  1. **历史 CSV 状态统计**：`_evaluation_meta` 改用 `compute_success_rate`，无任何 `exec_status` 记录时 `success_count` / `failed_count` 同步为 `null`（前端与 HTML/DOCX 导出显示 N/A），不再由 `summarize_performance` 的 0 默认值伪造成 0% / 全部失败。
  2. **失败题指标**：`_side_entry` 对 `exec_status == failed` 的六项指标统一置 N/A；`build_report_data` 先用 `_successful_rows` 过滤再汇总，失败运行的耗时 / Token 不再进入均值（AC-10）。逐题明细仍保留失败行与原因（配对用全量行）。
  3. **两版就绪门槛（Option B）**：新增 `_ensure_group_ready`，任一版非 `success` 时返回 409 `{code: "compare_not_ready", reason: "running"|"failed", evaluation_id, version}`；前端列表仅在相邻两条均 `success` 时启用「对比」，报告页请求改 `silent` 并按 reason 展示三语文案（`reportNotReadyRunning` / `reportNotReadyFailed`）。原 spec 边界「单侧展示」按用户口径改为「两版都有数据才出报告」。
  4. **文档同步**：spec AC-14 / 范围边界 / 边界情况、design 决策 7 / §4.1–§4.3 / 坑 3·14·15 / §7–§8 与导出接口 `format` 说明同步为 PDF / Word（xlsx / HTML 保留 API 兼容）。
  5. **卡死评测自愈（2026-10-21 本地故障复盘，用户要求）**：评测 98/99 因 09:48 共享 Redis/MySQL 同时抖动——Redis 断开导致第 1 题失败，随后写 `failed` 状态时 MySQL 也不可用，状态永久停在 `running`；且 FastAPI BackgroundTask 串行，98 抛异常后 99 根本没启动。修复：`add_evaluation_task` 在 Redis 进度键上维护 **30 分钟租约**（开跑即写、每题续期）；新增 `sweep_stale_running_evaluations`，在 API 启动 + 每 10 分钟巡检，把「`update_time` 超 30 分钟**且租约已消失**」的 running 评测置 failed（`STALE_RUNNING_DESCRIPTION` 提示重新发起），租约检查时 Redis 不可用则不改任何行。单测覆盖：只扫孤儿、长任务有租约不误杀、恰好卡在阈值不扫、Redis 不可用不动数据、仓储按状态/时间/删除标记筛选。

## 备注：分支

本 feature 的代码直接提交并推送到团队共享分支 `feat/2.7.0/beijing-ns-llm-guardrail`（2026-10-19 确认）。伟华（liweihua）、hei、张波均直接把提交推到该远端同名分支，无 PR / 合并流程；`features/v2.8.0/` 只是特性登记目录的版本号，不代表需要另开 2.8.0 分支。
