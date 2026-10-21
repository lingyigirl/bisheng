# Design: 评测可视化对比报告

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。
>
> 调整原则见 `docs/SDD-Guide.md` §3-§4：实现变了就覆盖本文档（只留今天的状态）；推翻已 ★ 确认的决策要停下重确认，纯实现细节直接改。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.8.0
**最后更新**: 2026-10-20

---

## 1. 目标与非目标

- **目标**：在**智能体（工作流）双版本对比组**上，提供一个**可视化对比报告页**（对比对象条 / 一句话结论 / 对比分析 / 指标卡 / 图表 / 逐题明细可下钻可切指标），并支持**把报告全量导出**（页面提供 PDF / Word；xlsx / HTML 保留 API 兼容）。同时补齐报告所需指标：**幻觉率**（内容级）、延迟 / Token（读 F066 已采集的 CSV 列）。
- **非目标**（详见 spec §范围边界）：N 路(>2)对比、按场景/题型拆分、LLM 自由生成的"建议"、**达标线/是否通过判定（归 6.4）**、faithfulness 型幻觉率、助手评测、Excel/HTML 页面入口、**修旧对比抽屉 CompareSheet 的 v1/v2 标签 bug**、雷达/箱线图。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7。
- **只读，无 DB 迁移**：**不新增表、不新增列、不改既有写入行为**（release-contract 表1 已登记 F090 为"非 Owner"）。报告数据在**生成时现算**，来源为既有 `evaluation` / `flow_version` / 结果 CSV。
- **不改同事已推送的代码（硬约束）**：伟华 F066（`features/v2.8.0/066-*`，结果 CSV 新列 + xlsx 报告）**已写好并推送，只读复用其能力，绝不修改其文件**。老抽屉 `CompareSheet.tsx` 与其后端 `get_evaluation_compare` 亦**冻结不动**（其 v1/v2 错位 bug 不在本 feature 修，spec 明确排除）。
- **复用边界（2026-10-16 定，勿过度纠偏）**：
  - **允许** —— 指标重合处**直接 import 复用其纯函数 / 纯逻辑**（`summarize_performance`、`collect_token_usage`、`_parse_percent`、`_align_questions`、`compare_flow_versions`、xlsx 生成函数）。
  - **禁止** —— **修改**上述文件。
  - **自建** —— 他**没做或做错**的部分：**跳末尾总行的 CSV reader**、**结论引擎**、**幻觉率**。
- **既有评测行为零回归**：新增裁判第 4 桶、CSV 读取、报告接口均**纯增量**；单版本评测（无对比组）行为不变（spec AC-16）。
- **多租户**：新接口的读查询走既有 repository（自动注入 `tenant_id`），并在属主维度过滤（spec AC-15）。
- **错误码**：评测模块历史实现（含 `get_evaluation_compare`）统一用 `HTTPException` + detail，不引入 MMM 段。本 feature **沿用模块既有约定**（与 F066 决策一致），复用既有 HTTP 4xx（新增 409 就绪错误，detail 带 machine-readable `code`）。
- **无新增第三方依赖**：图表用已在 `platform/package.json:62` 声明的 `recharts ^2.12.7`；xlsx 用已声明的 `openpyxl` + `pandas`。

---

## 3. 方案对比与选定

### 决策 1：报告落点 —— 新报告页（新路由） vs 扩老抽屉

- **备选**：
  - A. **新增报告页（新路由 `evaluation/compare/:groupId`）** —— 入口仍是列表「对比」按钮。
  - B. 把老抽屉 `CompareSheet.tsx` 直接扩成报告页（加图表 / 幻觉率 / 导出）。
- **选定**：A。
- **原因**：① 老抽屉有**已定位的 v1/v2 版本错位 bug**（前端按列表序贴标签、后端按 version 升序，两边下标相反，见 §5 坑 11）——扩它就必须先修它，而 spec 明确**不修**；② 报告页布局/交互是全新形态（图表 + 切指标 + 导出），与"侧滑抽屉看数字"差异大；③ 新页可**按 `compare_group_id` 独立取成员**，不依赖列表相邻行。
- **何时该重新考虑**：老抽屉 bug 被单独修复、且产品要求"对比只有一个入口形态"时，可合并。

### 决策 2：报告数据来源 —— 新增只读"报告接口" vs 复用老 `get_evaluation_compare`

- **备选**：
  - A. **新增 `GET /api/v1/evaluation/compare/{group_id}/report`**，返回前端渲染 + 导出**共用的同一份数据**。
  - B. 直接扩老接口 `get_evaluation_compare` 的返回字段。
- **选定**：A。
- **原因**：① 老接口返回**缺** `version_name` / 应用名 / 数据集名，且 `per_question` **只有 f1/recall/precision** 三指标（无幻觉率/延迟/Token），扩它等于重写，还会牵动冻结的老抽屉；② 新接口可**一次组装全量**（含导出需要的每个指标的逐题值），实现 spec AC-13「导出不随界面所选指标变化」；③ **一个数据源 → 前端可视化 + 后端导出** 两个薄消费方（§9 一引擎两视图）。
- **何时该重新考虑**：报告字段稳定且要被第三个消费方复用时，抽出公共 schema。
- **接口形态**：见 §4.2 / §4.3。

### 决策 3：复用 vs 自建的分界

- **备选**：
  - A. 指标重合处全部重写（"独立自建"的过度解读）。
  - B. 重合处 import 复用；只自建他没做/做错的部分。
- **选定**：B（2026-10-16 晓晨澄清）。
- **原因**：目的是**不修改同事文件**（避免回归、责任不清），**不是**拒绝复用、更不是重造轮子。复用比重写更省、口径更一致。
- **具体分界**：

  | 能力 | 归属 | 做法 |
  |---|---|---|
  | 性能汇总（成功率 / avg·max·P95 耗时 / token 量·速率 / 首 token） | F066 `evaluation_metrics.summarize_performance` | **import 复用**（重合口径） |
  | Token 归集（按题 trace_id 查 ES） | F066 `evaluation_metrics.collect_token_usage` | **import 复用** |
  | 百分比解析、逐题配对 | F053 `_parse_percent` / `_align_questions` | **import 复用**（但延迟/Tokend 走独立数值解析，见坑 2） |
  | 版本节点/边 diff | F051 `common/utils/flow_version_diff.compare_flow_versions` | **import 复用**（纯函数，见决策 9） |
  | 结果 CSV **读取** | —— | **自建**：必须**跳过末尾总行**（他靠 `dropna(how="all")` 剔不掉，见坑 3） |
  | **结论引擎** | —— | **自建**：优先级链 + 方向归一 + 分类容差，与 F053 的"三指标同向"链不同（见决策 4） |
  | **幻觉率** | —— | **自建**：内容级 Σ/Σ（见决策 5/6） |
  | xlsx 生成 | F066 `evaluation_report` 的 openpyxl 做法 | **复用其写 sheet/单元格的做法与 N/A 口径**；两版对比的 sheet 布局**自建**（`build_report_bytes` 是单评测结构，不能直接套，见决策 7） |

- **何时该重新考虑**：若伟华后续重构了这些函数签名，改为自建或适配。

### 决策 4：结论规则升级 —— 优先级链 + 方向归一 + 分类容差

- **备选**：
  - A. 沿用 F053 `_build_comparison` 的结论：三指标 `f1→recall→precision` **同向**（都是越高越好）、单容差 `0.01`。
  - B. **新结论引擎**：优先级链 **准确率 → 幻觉率 → 成本(Token) → 延迟**；对"越低越好"的指标**先方向归一**再比；容差**按指标类型**分别取值。
- **选定**：B。
- **原因**：spec AC-04 明确要求方向归一（幻觉率/成本/延迟是**越低越好**）与**分类容差**（百分比类与 秒/个 类量纲不同，一个 1% 的绝对容差对 3.2 秒的延迟没有意义）。F053 的链**没有**幻觉率/成本/延迟，也**没有**方向翻转 → 不能沿用，只能**复用其底层原子**（`_parse_percent`、`_align_questions`）后**自建链**。
- **口径**：某优先级"差值超容差"即判优；打平（未超）则**下探**下一优先级；四项全平 → 结论「无超容差差异」（前端文案 `compareUnable`）。**准确率 = precision**（全项目统一，不改名，见记忆）。
- **对比分析节（`analysis`，spec AC-05）**：与结论**同源生成**——由**差值与逐题胜负按固定模板**拼句，**当结论由非最高优先级指标决定时，模板里写明"下探"情形**（如"准确率打平，幻觉率低 0.03"）。**不调大模型**（防幻觉、可复现）。生成落点与结论引擎同一函数。
- **何时该重新考虑**：容差需要按数据集/业务自定义阈值时，改为可配置。

### 决策 5：幻觉率口径 —— 内容级 Σ/Σ

- **备选**：
  - A. 逐题求均值（与现有 totals 一致的路径）。
  - B. **内容级**：`幻觉率 = Σ矛盾陈述数 ÷ Σ总陈述数`（跨题按总陈述数加权）。
- **选定**：B（2026-10-15 定）。
- **原因**：题目长短不一，逐题均值会让"一句话的题"和"十句话的题"等权，失真；内容级按实际陈述数加权更真实。**代价（已知并接受）**：不做 faithfulness（挂检索上下文，逐次不可复现）。
- **何时该重新考虑**：需要与外部评测口径对齐时。

### 决策 6：幻觉桶怎么进裁判 —— 方案 A（固定拼接、保留自定义）

- **背景**：裁判提示词是**建评测表单里用户可编辑的字段**（必带 `{question}/{answer}/{ground_truth}` 占位符，见 `evaluation/api/endpoints/evaluation.py`）。
- **备选**：
  - A. **幻觉判定指令由系统固定拼接在占位符之外**（默认模板 + 用户自填模板**两条路都拼**）→ 用户删不掉 → 幻觉率**恒可算**。
  - B. 强制用新版默认模板 → 用户不能自定义裁判。
- **选定**：A（2026-10-16 晓晨拍板："源码给了可以改的空间，我们就改保留"）。
- **原因**：保留用户自定义空间（有用户会用自定义裁判），同时保证幻觉率恒可算。
- **落地**：第 4 桶（"与标准答案**矛盾的陈述**"）加进 `answer_correctness` 的**最终裁判模板**——默认 `CORRECTNESS_PROMPT` **与** 用户 `prompt` **两条路都在占位符外拼接**同一段指令。拼装点见 §4.1。
- **反直觉点**：`compute_answer_correctness` 用 `item.get(_KEY_MAP[...], "")` 取桶 → **加第 4 键不会破坏解析**（对多出的键容错）；只要提示词里让裁判多返回一个键即可。
- **何时该重新考虑**：若改为"幻觉率 N/A 也可接受"，可退回 B。

### 决策 7：导出 —— 后端生成；页面 PDF / Word，xlsx / HTML 保留 API 兼容

- **备选**：
  - A. 前端截图/打印转 PDF（所见即所得）。
  - B. **后端按与报告页同一份数据生成**文件。
- **选定**：B（spec AC-14 硬要求）。
- **原因**：前端截图对**中文、字体、分页**不友好（信创浏览器尤甚）→ 输出不稳；后端生成可复现、可全量。
- **格式（2026-10-19 用户调整，2026-10-20 落档）**：面向用户的导出是**可直接转发的完整报告**，页面只提供 **PDF / Word(.docx)**：
  - `build_export_pdf`：复用项目既有 Playwright/Chromium 渲染 `build_export_html` 的自包含 HTML（内联 CSS + SVG 图表），A4；
  - `build_export_docx`：python-docx + matplotlib 图表；
  - `build_export_xlsx` 与 `build_export_html` **保留**（`format` 支持 `xlsx | html | pdf | docx`，默认仍 `xlsx` 兼容旧调用），页面不暴露。
- **口径**：导出 = **全量**（每个指标的逐题值都在），**不随界面当前所选指标变化**（spec AC-13）。PDF 运行前提：`playwright install chromium` + 系统库（部署镜像同样需要）。
- **何时该重新考虑**：要求"像素级还原界面"时才考虑 A；若确认只需一种格式，可删除其余生成器。

### 决策 8：图表库 —— recharts（既有依赖）

- **备选**：A. `recharts`（`platform` 已声明 `^2.12.7`）；B. 引入新图表库。
- **选定**：A。
- **原因**：前端规范**禁止引入新的 UI/状态库**；recharts 已覆盖分组柱状 + 折线需求。
- **口径**：分组柱状图（准确率/F1/召回，同 % 量纲共用一根轴）；逐题折线图（横轴题号、两版两条线）；**延迟 / Token 若出图各自单开图、独立纵轴**（异量纲，见坑 1）。

### 决策 9：对比对象条 —— 用 F051 纯函数 diff + 模型名解析

- **背景**：报告须展示两版**各自的全部模型 + 全部提示词节点 + 变更项**（spec AC-02）。工作流**没有独立的"提示词节点"**：提示词内嵌在 **LLM 节点**的 `system_prompt/user_prompt`，模型是 LLM 节点的 `model_id`（整数）。
- **备选**：
  - A. 手写解析 `flow_version.data['nodes']`，逐节点比。
  - B. **复用 F051 `compare_flow_versions(v1.data, v2.data)`**（纯函数，`common/utils/`，非同事专属模块）取 `node_changes`，再筛 LLM 节点。
- **选定**：B。
- **原因**：`compare_flow_versions` 已按业务口径做归一（节点按 `id` 配对、只比 `node.data`、`group_params/params` 按 `key` 对齐、忽略 `position`/`viewport`），返回 `node_changes[{node_id,node_name,node_type,change,fields}]`——**正好是"哪些节点、哪里变了"**。
- **口径**：
  - **全部模型 / 全部提示词节点** = 从**两版各自** `data['nodes']` 取 `type == 'llm'` 的节点，读 `group_params[].params` 里的 `system_prompt/user_prompt/model_id`。
  - **变更项** = `compare_flow_versions` 的 `node_changes`（筛 `node_type=='llm'`，`fields` 里路径含 `model_id`/`system_prompt`/`user_prompt`）。
  - **模型名**：`model_id`（整数）→ `LLMModel.get_model_by_ids([...])` 查 `.name`（`llm/domain/models/llm_server.py:329`）。
  - **情形判定**：仅模型变 → 「换模型对比」；仅提示词变 → 「换提示词对比」；两者都变 → 「模型 + 提示词 均变更」，标 **「多变量 · 不可单独归因」**（spec AC-02；注意口径："不可单独归因" ≠ "比不出来"）。
- **何时该重新考虑**：节点结构大改（如引入独立提示词节点）时。

### 决策 10：逐题幻觉数的落点 —— 结果 CSV 新增列

- **背景**："矛盾"是**裁判（评测执行期）**才产出的新判断，报告期**无法再算**（不重跑裁判）。要进报告 + 导出，必须先**持久化逐题幻觉数**。
- **备选**：
  - A. **结果 CSV 新增列**（与 F066 的 perf 列同一套路：`unit_type=1` 文本型，不进既有聚合口径），存**逐题原始计数**。
  - B. 塞进 `evaluation.result_score`（JsonType 汇总列）。
- **选定**：A。
- **原因**：① 报告要**逐题**幻觉率（§4.5 切指标要下钻到题），`result_score` 只有汇总、放不下逐题；② Σ/Σ 聚合需要**原始分子分母**（矛盾陈述数 / 总陈述数），只存率无法加权——**必须存计数**；③ 与 F066 perf 列同构，读取/展示一致。
- **列**：新增 `statements_contradicted_num`（矛盾陈述数，⊆ 多答）与 `statements_num_answer`（本题总陈述数 = overlap+answer_only）；报告中 幻觉率 = Σ`contradicted` ÷ Σ`total`。
- **代价（需晓晨知悉）**：这要在**评测执行链**里加列——`answer_correctness.compute_answer_correctness` 多返回两组数组，`evaluation_service.add_evaluation_task` 的 `columns` 列表多两项。这是**6.2 自己新能力所必需的扩展**（不是改伟华的评分逻辑）；若晓晨认为触碰该文件越界，可退回 B（但逐题幻觉率将缺失）。
- **何时该重新考虑**：幻觉判定改为可重放（如落裁判原始输出）时，可后算而不落列。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
报告数据（新增，只读）:
  GET /api/v1/evaluation/compare/{group_id}/report
    ├─ 校验: 当前用户可访问该 compare_group（租户 + 属主/组内）
    ├─ EvaluationRepository.get_compare_group(group_id, user_id)   ← 复用 F053，每组恰 2 条
    ├─ 就绪校验: 两版 status 均须 success，否则 409 + {code: compare_not_ready, reason: running|failed, evaluation_id, version}
    ├─ 对每版（按 version 升序 → v1=旧 / v2=新）:
    │   ├─ 读结果 CSV: <自建 reader> 跳末尾总行 → 逐题行
    │   │     （复用 summarize_performance 汇总；collect_token_usage 已由 F066 采集进 CSV 列，不再查 ES）
    │   │     无 exec_status 列（历史 CSV）→ 成功率/成功数/失败数 = N/A，不记 0%
    │   │     exec_status == failed 的题 → 六项指标全部 N/A，且不进任何均值/聚合
    │   └─ flow_version.data → 取 LLM 节点（system_prompt/user_prompt/model_id）
    ├─ 结论引擎（自建）: 准确率→幻觉率→成本(Token)→延迟；方向归一；分类容差 → conclusion
    ├─ 对比对象条: compare_flow_versions(v1.data, v2.data) → node_changes（筛 LLM）；model_id→名
    ├─ 逐题明细: 配对(F053 _align_questions) × 全指标（准确率/F1/召回/幻觉率/延迟/Token）
    └─ 返回 { group_id, evaluations[v1,v2], object_bar, conclusion, analysis, metrics, charts, per_question }

报告导出（新增，后端按同一份数据生成）:
  GET /api/v1/evaluation/compare/{group_id}/report/export?format=pdf|docx|xlsx|html
    ├─ 同上组装同一份报告数据（含 409 就绪校验）
    ├─ pdf / docx: 页面入口；xlsx / html: 保留的 API 兼容格式
    └─ 返回预签名下载链接（不依赖前端截图）

裁判（评测执行期，改）:
  compute_answer_correctness(...)  ← 最终裁判模板 = 默认模板 或 用户 prompt
    ├─ 【新增】在 {question}/{answer}/{ground_truth} 占位符之外，固定拼接第 4 桶
    │    "与标准答案矛盾的陈述" 指令 → 裁判多返一个键 → 幻觉数（contradicted ⊆ FP）
    └─ 【新增】多返回两组逐题数组：statements_contradicted_num / statements_num_answer
  add_evaluation_task(...)  ← 结果 CSV columns 追加这两列（决策 10）
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| 报告数据 `evaluations[]` | `{ id, version, version_name, status, result_score, description, question_count, success_count, failed_count, success_rate }` | 两版元信息，**按 version 升序**（v1=旧/v2=新）。`version_name` 走 `FlowVersion.name`（列表接口同源，`evaluation_service.py:235`）。三个成功/失败计数字段在历史 CSV（无 `exec_status`）下为 `null`（前端显示 N/A） | 报告页头 / 对比对象条 |
| `object_bar` | `{ app_name, data_set, models: {v1:[], v2:[]}, prompt_nodes: {v1:[], v2:[]}, change_kind: "model"\|"prompt"\|"both"` }` | 对比对象条；`change_kind=='both'` 时前端标"多变量" | 报告页头 |
| `conclusion` | `"v1" \| "v2" \| "unable"` | 只方向，**不含指标名/✅❌**（spec AC-03） | 一句话结论 |
| `metrics[]`（指标卡） | `{ key, label, v1, v2, delta, unit }` | 准确率/F1/召回/幻觉率/延迟/成本；**不判达标** | 指标卡 |
| `per_question[]` | `{ question, failed?, fail_reason?, v1:{...}, v2:{...} }`，值含 **全 6 指标** | 逐题**全指标**（界面一次显一个，导出全给） | 逐题明细 / 导出 |
| 结果 CSV 新列（F066） | `duration_ms / input_token / output_token / first_token_ms / exec_status` | **F066 已写**；缺失为 `N/A` | 延迟/成本/成功率/失败原因 |
| 结果 CSV 新列（**F090**） | `statements_contradicted_num`（矛盾陈述数）/ `statements_num_answer`（本题总陈述数） | **本 feature 新增**（决策 10）；`unit_type=1` 文本型，不进既有聚合 | 幻觉率（报告 + 导出） |
| 导出预签名链接 | `{ url, file_name }` | 复用 F066 的 MinIO `get_share_link_sync` | 报告页导出按钮 |

> 内部结构（`node_changes` 原始 diff 等）不在此列，看代码即可。

### 4.3 接口文档（新增）

#### 4.3.1 报告数据

`GET /api/v1/evaluation/compare/{group_id}/report`

- 认证：登录用户；**租户内 + 可访问该对比组**（AC-15）
- 入参：路径参数 `group_id: str`
- 出参：`{ status_code: 200, data: { group_id, evaluations[2], object_bar, conclusion, analysis, metrics[], charts{...}, per_question[] } }`
- 错误：

  | 场景 | HTTP | detail |
  |---|---|---|
  | 对比组不存在 / 不属本用户 | 404 | `compare group not found` |
  | 组内两版非同一智能体 | 400 | `compare group agents mismatch` |
  | 任一版未执行完成 | 409 | `{code: "compare_not_ready", reason: "running"\|"failed", evaluation_id, version}`（两版都成功才可出报告） |
  | 结果文件不可读 | 400 | `result csv unreadable`（对外可辨识，不静默成功） |

#### 4.3.2 报告导出

`GET /api/v1/evaluation/compare/{group_id}/report/export`

- 入参：路径参数 `group_id`；查询参数 `format`（`pdf | docx | xlsx | html`，默认 `xlsx`；页面只调用 `pdf` / `docx`）
- 出参：`{ status_code: 200, data: { url, file_name } }`（预签名）
- 错误：同上（含 409 就绪校验）；`format` 非支持值 → 400。

### 4.4 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `evaluation/domain/services/evaluation_compare_report.py`（**新**） | 组装报告数据：读 CSV（跳总行）→ 汇总 → 结论引擎 → 对比对象条 → 逐题全指标 | 不碰老 `get_evaluation_compare`；不改 F066 文件 |
| `evaluation/domain/services/evaluation_report.py`（**F066，只读复用**） | `build_report_bytes` / `_resolve_app_meta` 等 xlsx + 元信息解析 | **不改**其任何行 |
| `evaluation/domain/services/evaluation_metrics.py`（**F066，只读复用**） | `summarize_performance` / `collect_token_usage` | **不改** |
| `evaluation/domain/services/answer_correctness.py`（**改**） | 裁判模板**新增第 4 桶**（占位符外固定拼接）；解析新键 | 不改 TP/FP/FN 既有口径与公式 |
| `evaluation/api/endpoints/evaluation.py`（**改**） | 新增报告数据 + 导出两个端点 | 不改既有端点 |
| `evaluation/domain/services/evaluation_service.py`（**改**） | ①报告编排入口（或薄封装）；②`add_evaluation_task` 的结果 CSV `columns` **追加幻觉两列**（决策 10） | **不动** `_build_comparison` / `get_evaluation_compare`（老抽屉冻结）；不动 F066 的 perf 列逻辑 |
| `common/utils/flow_version_diff.py`（**F051，只读复用**） | `compare_flow_versions` 节点 diff | **不改** |
| `platform/src/pages/EvaluationPage/compare/CompareReport.tsx`（**新**） | 报告页（图表 + 指标卡 + 逐题 + 导出） | 不改老 `CompareSheet.tsx` |
| `platform/src/pages/EvaluationPage/index.tsx`（**改**） | 「对比」入口指向新报告路由 | 不动老抽屉挂载逻辑（可并存/替换） |
| `platform/src/routes/index.tsx`（**改**） | 注册新路由 `evaluation/compare/:groupId` | — |
| `platform/src/controllers/API/evaluate.ts`（**改**） | 新增报告数据 / 导出 API | 不动 `getEvaluationCompareApi` |

### 4.5 前端交互

- **入口**：评测列表「对比」按钮 → 打开**新报告页**（新路由），参数为 `compare_group_id`（**不按列表相邻行推成员**）。列表侧只在**相邻两条对比组成员均 `success`** 时启用按钮，否则置灰并提示；报告页对 409 的 `reason` 分别展示「运行中」「失败」文案（请求走 `silent`，页面读 `detail`）。
- **报告页结构**（自上而下）：对比对象条 → 一句话结论 → 对比分析 → 指标卡 → 图表（分组柱状 + 逐题折线）→ 逐题明细（**下拉切指标**，判据 = 该指标有逐题值；**更好/更差按所选指标方向翻转**：准确率/F1/召回 越高越好，幻觉率/延迟/Token 越低越好）→ 导出按钮。
- **逐题标题**：`共 N 题 · 成功 ×% · 失败 M`；历史 CSV 无状态时成功率 / 失败数显示 **N/A**；失败题在「结果」列标**失败原因**，其各指标 **N/A** 且不进聚合。
- **导出**：调后端导出接口 → 用既有 `downloadFile` 走预签名链接。
- **i18n**：新增文案三语同 PR；`compareBetter`（`bs.json:824`）当前硬编码"准确性更高"，报告页**不得**复用它（结论中性、无指标名），另起 key。

---

## 5. 已知坑 / 反直觉事实

> 来自需求分析 §14「实现必查清单」，逐条核过代码。**不知道会怎样**列在右。

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | 分组柱状 3 指标（准确率/F1/召回）同为 % 可共用一根 y 轴；**幻觉率同为 %，不进该图的原因是"方向"（柱越高越差）+ 清爽，不是量纲**；**延迟(秒)/Token(个) 才是异量纲** | 把延迟 3.2 秒画进 0–100% 的轴 → 柱子贴地 → 被误读成"该指标差" | 延迟/Token 出图各自单开、独立纵轴 |
| 2 | `_parse_percent` 对**纯数值列**（`np.int64`）`isinstance(x,int)` 为 False → 走字符串分支 → `"1234"` ÷100 = **12.34**；列里**一旦混入 `"N/A"`** 变 float → 返回 **1234.0（对）** | 延迟/Token 用 `_parse_percent` → **全成功时**乱、有失败题时反而对（值随失败题有无翻转） | 延迟/Token **必须用独立数值解析**，不与百分比解析共用 |
| 3 | 结果 CSV 有**末尾总行**（`build_result_table` 追加），其 `recall/precision/F1` 是**非空字符串** → `dropna(how="all")` **剔不掉**（F066 现有 bug，已复现：2 题报成 3 题 / 66.67%） | 题目总数多 1、成功率偏低；老 CSV（无 `exec_status`）成功率显示成 0% | **自建 reader** 按 `question` 为空/NaN 剔总行；**`_evaluation_meta`** 用 `compute_success_rate` 决定成功率/成功数/失败数——无任何状态记录时三项均为 `None`（N/A），**不得**用 `summarize_performance` 的 `success_count=0` 默认值。此 reader bug 只**列出**给晓晨，是否转告伟华由他定 |
| 4 | 幻觉率是**内容级 Σ/Σ**（按总陈述数加权），**不是逐题均值** | 沿用 totals 的均值路径 → 长短题等权、失真 | 幻觉率**单独按 Σ/Σ 算**，不走均值路径 |
| 5 | "与标准答案**矛盾**"的陈述很可能**同时落在"多答"（FP）清单里** | 分子/分母都虚（重复计数） | 把"矛盾"做成 **FP 的子类**（`contradicted ⊆ FP`）；总陈述数 = `TP + FP` |
| 6 | `compute_answer_correctness` 用 `item.get(_KEY_MAP[...], "")` 取桶，**对多出的键容错** | 以为加第 4 桶会破坏解析，从而去改解析 → 多余改动 | 只**加提示词指令 + 加一个键**，解析不动 |
| 7 | 老对比接口 `get_evaluation_compare` 返回 `evaluations` **无 `version_name`/应用名/数据集名**；`per_question` **只有 3 指标** | 直接拿去渲染报告 → 头部缺信息、切指标无数据 | 新增报告接口补齐（决策 2） |
| 8 | F053 结论链是 `f1→recall→precision` **同向**、**单容差 0.01**，**无**幻觉率/成本/延迟、**无**方向翻转 | 以为能直接复用 `_build_comparison` 当结论 | 复用其**原子**（`_parse_percent`/`_align_questions`），**自建链**（决策 4） |
| 9 | 工作流**没有独立提示词节点**；提示词内嵌 LLM 节点 `system_prompt/user_prompt`，模型是 LLM 节点 `model_id`（整数） | 去找"提示词节点"表 → 找不到 | 取 `data['nodes']` 里 `type=='llm'` 节点；`model_id` 需 `LLMModel` 查名（决策 9） |
| 10 | 两版**共用同一份数据集 + 同一评测指令**（`create_evaluation` 一次建两行，共用 `file_path` / `prompt`）；对比校验只查**应用**（`unique_id`） | 以为要校验"两版是否同数据集/同指令" → 做多余校验 | **不需要**该校验；「对比对象」条只列**会变的**（模型/提示词） |
| 11 | 老抽屉 `CompareSheet` 有 **v1/v2 标签错位** bug（前端按列表序贴、后端按 version 升序） | 扩它 → 把错位带进新报告 | **不改老抽屉**；新页按 `compare_group_id` 取成员、按 version 升序贴标签（v1=旧/v2=新） |
| 12 | 配对须按**唯一标识**而非题面文本（题面可能重复）；系统追加的**汇总行不算一道题**；表单型题无题面时用**表单摘要**当题面 | 取错题 / 题数虚高 / 题面空白 | `_align_questions` 现有文本配对（同数据集走位置快路径）；reader 跳总行；表单摘要兜底 |
| 13 | 幻觉数在**评测执行期**由裁判产出，报告期**不重跑裁判** → 必须先落**逐题原始计数**（只想存率会丢 Σ/Σ 加权） | 报告期算不出逐题幻觉率；Σ/Σ 退化成逐题均值 | 结果 CSV 新增 `statements_contradicted_num`/`statements_num_answer`（决策 10） |
| 14 | `summarize_performance` **不过滤失败题**：F066 `_inject_failed_rows` 仍写入失败运行的 `duration_ms`/token，直接汇总会把失败路径的耗时/Token 混进延迟/成本 | 失败题越多，延迟/成本聚合越失真，且与「失败题不计入」矛盾（AC-10） | 报告组装先 `_successful_rows` 过滤再汇总；逐题 `_side_entry` 对 failed 行六项统一置 N/A（失败原因仍展示） |
| 15 | 单侧未完成时 `result_file_path` 为空或 CSV 读不到 → 原先直接 400；若改为静默只展示单侧，又会让用户在半个数据集上判优劣 | 运行中的对比组点进来只有笼统报错；单侧展示则产出不可信的「对比」结论 | `_ensure_group_ready` 在读取 CSV 前返回 **409** + `{code, reason, evaluation_id, version}`；前端列表禁用入口、报告页按 reason 分文案提示（2026-10-20 用户拍板「两版都有数据才出报告」） |
| 16 | 评测执行是 **API 进程内的 FastAPI BackgroundTask**，进程重启/依赖抖动会把它变成孤儿；失败分支写库时若 MySQL 同时不可用，连 `failed` 都写不进去 → 行永久停在 `running`。**只看"启动时扫描"不够**：本次故障的进程一直活着（4 小时），根本没重启 | 列表永远 "进行中 0%"、对比入口永远置灰，且无法自愈 | 执行器在 Redis 进度键上维护 **30 分钟租约**（开跑即写、每题续期）；API 启动 + 每 10 分钟巡检：`update_time` 超 30 分钟 **且租约已消失**才置 failed。租约检查时 Redis 不可用则**不动任何行**，避免把长任务误判为孤儿（题目再多，只要单题在租约期内完成就不会被扫掉） |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `GET /api/v1/evaluation/compare/{group_id}/report` | HTTP API | platform 报告页 |
| 报告就绪契约：两版均 `success` 才返回报告，否则 **409** `{code: "compare_not_ready", reason, evaluation_id, version}` | HTTP 错误契约 | platform 列表入口 / 报告页（据此展示具体原因） |
| `GET /api/v1/evaluation/compare/{group_id}/report/export` | HTTP API | platform 报告页导出（页面用 pdf / docx） |
| 裁判模板第 4 桶（幻觉指令固定拼接） | 行为契约 | 评测执行链；影响幻觉率可得性 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| F066 结果 CSV 新列（`duration_ms`/`input_token`/`output_token`/`first_token_ms`/`exec_status`） | 文件格式 | 列名/语义若变 → 延迟/成本/失败原因失效（降级 N/A，不崩） |
| F066 `evaluation_metrics.summarize_performance` / `collect_token_usage` | 内部 Python API（**复用，不改**） | 签名重构 → 需适配 |
| F051 `common/utils/flow_version_diff.compare_flow_versions` | 纯函数（**复用，不改**） | 归一规则变 → 对比对象条口径变 |
| `flow_version.data` 结构（`nodes[].data{type,name,group_params}`） | DB JSON 结构 | 节点结构大改 → 提取失败 |
| `LLMModel.get_model_by_ids`（id→名） | 内部 API | — |
| `evaluation.compare_group_id` / `version` / `result_file_path` | DB 列（**只读**） | — |
| `recharts ^2.12.7` / `openpyxl` / `pandas` | 依赖 | 已锁版本，无新增 |

---

## 7. 测试与可观测

- **单元**（pytest，`test/evaluation/`；新测试放 `test/<module>/`）：
  - **reader**：含总行的 CSV → 题数/成功数正确（**不**把总行当题）；旧 CSV（无 `exec_status`）→ 成功率 / 成功数 / 失败数均 N/A 不报错；失败题 → 六项指标 N/A 且不进聚合（含延迟 / Token）；失败题仍出现在逐题明细并带原因。
  - **就绪校验**：任一版 running / failed → 409 + `compare_not_ready`（reason 可区分），且不读取结果文件；两版 success → 正常组装。
  - **结论引擎**：方向归一（幻觉率/成本/延迟越低越好）；分类容差；高优先级打平 → 下探；四项全平 → `unable`；非最高优先级决定 → 「下探」情形在 `analysis` 里写明。
  - **幻觉率**：Σ/Σ（非均值）；`contradicted ⊆ FP` 不重复计数；无第 4 键（旧裁判输出）→ N/A。
  - **对比对象条**：仅模型变 / 仅提示词变 / 都变（多变量）三情形；`model_id`→名解析。
  - **导出**：xlsx 两 sheet 可被 `pandas.read_excel` 回读；html / docx / pdf 生成不报错；含**全 6 指标逐题值**；导出结果**不随**界面临时所选指标变化。
  - **多租户/回归**：跨租户/非属主 → 404/不可访问（AC-15）；单版本评测行为不变（AC-16）。
- **e2e**：真实跑一次工作流双版本对比评测 → 打开报告页（图表渲染、切指标翻转、下钻）→ 导出 PDF / Word 打开正常（`/e2e-test`）。
- **手动验证**：本地起前后端——后端 `cd src/backend && export config=config.yaml && uv run uvicorn bisheng.main:app --port 7860`，前端 platform `cd src/frontend/platform && pnpm dev`（:3001）；登录后在 `/evaluation` 点某对比组「对比」→ 报告页；导出按钮下载 PDF / Word；再挑一个运行中 / 失败的对比组确认入口置灰与报本文案。**跑通再推分支**（记忆 `feedback_local_verify_before_push`）。
- **可观测**：CSV 读取 / 报告生成失败记 `logger.exception`；模型名解析失败降级显示 `model_id` 原值并 `logger.warning`。

---

## 8. 后续改进 / 不打算做的事

- N 路(>2)对比（把"固定 2 条"放开即可，计算逻辑可复用）。
- 按场景/题型拆分（数据集无"场景"列）。
- 导出引擎与 F066 是否合并（待与伟华协商）；页面是否需要恢复 Excel / HTML 入口。
- faithfulness 型幻觉率（挂检索上下文）。
- 雷达 / 箱线图。
- 修复老抽屉 `CompareSheet` 的 v1/v2 错位 bug（不在本 feature）。

---

## 修订历史

| 日期 | 变更 |
|---|---|
| 2026-10-16 | 初稿（spec 同期创建）；复用边界按晓晨澄清更正为"不改文件、重合能力可复用" |
| 2026-10-16 | /sdd-review 后补：决策 10（逐题幻觉数落结果 CSV 列）+ 坑 13；导出 xlsx 复用口径澄清（build_report_bytes 是单评测结构）；对比分析（analysis）生成口径 |
| 2026-10-20 | 审计修复落档：①历史 CSV 成功率 / 成功数 / 失败数改为 N/A（坑 3）；②失败题六项指标 N/A 且不进聚合（坑 14）；③两版都成功才出报告，单侧未完成返回 409 `compare_not_ready` 并区分 running/failed（坑 15）；④导出形态按 2026-10-19 调整同步为页面 PDF / Word、xlsx / HTML 保留 API 兼容（决策 7 / §4.3.2 / §8） |
| 2026-10-21 | 本地故障复盘加固（评测 98/99 卡 4 小时）：执行器增加 Redis 租约（30min，开跑即写、每题续期）；API 启动 + 每 10min 巡检把「超 30min 未更新且租约消失」的 running 评测置 failed（坑 16） |
