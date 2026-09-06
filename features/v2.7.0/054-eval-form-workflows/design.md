# Design: 评测支持表单型工作流（form_input 智能体的版本对比评测）

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-05

---

## 1. 目标与非目标

- **目标**：评测执行链支持表单型（form_input）工作流——数据集用 `form_values` 列（按表单项 label 引用）提供表单值，执行时提交表单并继续，采集输出为答案；单题失败隔离；与 F053 对比评测联动。
- **非目标**：助手/灵思表单型评测、数据集文件上传管理界面、label 重复自动消歧、图片/音频专属输入类型。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7。
- **问答型路径零变化**（AC-06）：无 `form_values` 的数据集走既有代码分支，行为与现在完全一致。
- **结果 CSV 结构不变**（spec §3）：不新增列——单题失败原因写入该行 `answer` 列（`[执行失败] <原因>`），指标填 `N/A`。表单型行的 `question` 列填案例摘要（决策 7），仅展示用途，评分输入不变。
- **不改 F053 的任何契约**：对比端点/对比组机制照常工作（表单型评测产出的结果 CSV 与问答型同构）。
- 错误码不新增（创建校验沿用既有 UploadFileExtError 风格；执行期单题失败按 N/A 口径记录）。

---

## 3. 方案对比与选定

### 决策 1：表单值的映射键（label vs 流程定义 key）

- **备选**：
  - A. 数据集按**表单项 label** 引用（`"银行流水文件（PDF/Excel/图片，可多份）"`），运行时由 input 事件的 `input_schema` 建 label → 运行时 key 映射
  - B. 数据集按流程定义的表单项 key 引用（`bank_flow_files`），运行时由流程定义与运行时 schema 关联
- **选定**：A（用户已确认）
- **原因**：运行时 schema 的 key 每次会话随机生成（`file_<uuid>`，2026-09-05 批量评测脚本踩坑实证），流程定义 key 与运行时 key 之间没有现成关联字段；label（表单项显示文字）在运行时 schema 的 `value` 字段中直接可得，是唯一稳定的关联键。数据作者在客户端看到的就是 label。
- **何时该重新考虑**：运行时 schema 未来携带流程定义 key 时，改 B（更抗 label 文案变更）。

### 决策 2：文件引用的签名时机与路径约定

- **备选**：
  - A. 数据集存对象路径，执行时逐题生成预签名 URL（用户已确认）
  - B. 数据集直接存 URL
- **选定**：A。路径约定：以 `tmp-dir/` 开头的路径按临时桶（bucket=`tmp-dir`）处理，其余路径按默认桶处理；完整 URL（含 `://`）原样使用（spec §3）。对象不存在 → 该题失败并记录原因，不整体失败。
- **何时该重新考虑**：对象存储桶增多时，把 bucket 解析做成显式映射表。

### 决策 3：执行接法（execute → input → 提交 → continue）

- **备选**：
  - A. 在既有 `execute_workflow_get_answer` 内扩展 form_input 分支：构建 payload → `set_user_input` → `INPUT_OVER` → `continue_workflow` → 复用既有答案采集
  - B. 新写一个表单型执行函数
- **选定**：A
- **原因**：对话型与表单型在执行前（启动 execute、收集事件）与执行后（continue、采集答案）完全相同，只有"输入状态后的提交"不同——A 只改一个分支，答案采集逻辑复用，避免两条执行路径漂移。
- **何时该重新考虑**：表单型需要特殊的前处理（如批量预热）时拆函数。

### 决策 4：单题失败隔离的边界

- **备选**：
  - A. 仅工作流分支（exec_type=workflow/flow）逐题 try/except 隔离；assistant 分支保持现状
  - B. 所有 exec_type 逐题隔离
- **选定**：A
- **原因**：本 feature 的范围是表单型工作流；assistant 执行链不在范围内，动它等于扩大回归面。隔离后的失败题：`answer = "[执行失败] <原因截断>"`，评分指标 N/A（不参与聚合——从 LLM 评分批次剔除，结果行单独注入）。
- **何时该重新考虑**：assistant 评测出现同类痛点时，把隔离逻辑抽成通用装饰器覆盖全 exec_type。

### 决策 5：失败题在结果 CSV 中的表达

- **备选**：
  - A. 失败行保留全部列：question/ground_truth 照填，answer 填 `[执行失败] 原因`，三项指标与语句计数填 `N/A`，行位置插入在正常行之后、合计行之前
  - B. 结果 CSV 加一列 `status`
- **选定**：A
- **原因**：spec §3 锁定"结果 CSV 结构不变"；A 在不加列的前提下保留失败信息与可读性，且合计行只聚合正常行（失败行 N/A 天然被既有聚合排除——2026-09-05 的 NaN 清洗口径复用）。
- **何时该重新考虑**：需要结构化查询失败原因时，加列并升级对比视图。

### 决策 6：form_values 校验时机

- **备选**：
  - A. 创建评测时全校验（parse_csv 阶段解析每行 JSON，错误带行号）——执行期直接使用
  - B. 执行期逐题解析
- **选定**：A
- **原因**：AC-01 要求创建时返回参数错误并指明行号（快失败）；B 会把数据错误推迟到跑分阶段且难报行号。执行期仍需容错（对象缺失、label 不匹配属运行时问题，按 AC-03/04 单题失败）。
- **何时该重新考虑**：数据集超大型（上万行）解析开销显著时，改为执行期惰性解析 + 首行抽查。

### 决策 7：结果 CSV 展示测试案例（2026-09-06 用户确认）

- **备选**：
  - A. 表单型行的 `question` 列填可读摘要（`label: 值; label: 值`，列表逗号连接、空值跳过）——不加列，评分输入仍为空 question（纯展示）
  - B. 新增 `form_values` 列存 JSON 原文
  - C. 结果文件改 xlsx 多 sheet（汇总 + 明细）
- **选定**：A
- **原因**：下载的结果文件要能看出每行对应哪个测试案例（原 spec 锁定「表单值不写入结果 CSV」，但表单型行 question 为空，下载后无法识别案例归属）。A 不加列、Excel 里直观，且**顺带修复 F053 对比对表单型的逐题配对**——此前两侧 question 全空 → 配对为空 → 对比视图逐题表空、总分 N/A、结论「无法判定」；填摘要后 `_align_questions` 文本匹配生效，逐题表/总分/结论均可用。评分语义不变（judge 仍收空 question），不影响既有分数。C 改动面大（生成格式切换 + 对比读文件兼容 + 前端文件名），收益不抵风险。
- **实现**：`format_form_values()` 摘要函数；`build_result_table` 之后 `_overlay_form_question(row_list, csv_data)` 覆盖（仅 question 为空的表单行，**显式排除末尾合计行**——`row_list` 比 `csv_data` 多一行）；`_inject_failed_rows` 失败行同规则；结果 CSV 改 `utf-8-sig` 写入、对比读取改 `utf-8-sig`（向后兼容旧无 BOM 文件），保证 Excel/WPS 打开中文不乱码。
- **何时该重新考虑**：需要结构化查询表单值（如按字段过滤失败题）时，升级为 B 或 C。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
创建评测（含 form_values 列的数据集）:
  POST /api/v1/evaluation
    └─ parse_csv: 逐行校验 form_values 为合法 JSON（错误带行号，AC-01）——question 列可选、ground_truth 必填

执行（workflow 分支，逐题循环，单题 try/except 隔离）:
  execute_workflow_get_answer(version 快照, evaluation, 行数据)
    ├─ execute_workflow → 收集事件至 break
    ├─ status == INPUT:
    │   ├─ dialog_input → 既有分支（question 提交）【零变化】
    │   └─ form_input → 构建 payload：
    │       1. input 事件 input_schema.value[] → {label: 运行时 key} 映射（label 重复 → 该题失败）
    │       2. form_values 逐项: 文本 → 字符串; 文件 → 对象路径转预签名 URL（tmp-dir/ 前缀 → tmp 桶; :// → 原样）
    │       3. 为 schema 中全部项填值（缺失可选文本 → 空串; 缺失必填文件 → 该题失败）
    │       → set_user_input({node_id: payload}) → INPUT_OVER → continue_workflow
    └─ 复用既有答案采集（输出事件文本 → answer）

评分:
  正常题 → compute_answer_correctness（与现在一致）
  失败题 → 不进入评分批次；结果行单独注入（answer=[执行失败] 原因, 指标 N/A），合计行只聚合正常行
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| 数据集 `form_values` 列 | JSON 字符串：`{"<label>": <值>}` | 文件值 = `["tmp-dir/bankflow_2026.csv"]`（数组或单字符串）；文本值 = 字符串；列可选（无列 = 问答型行为） | 创建校验 + 执行期提交 |
| label → 运行时 key 映射 | `{label: key}` | 从 input 事件 `input_schema.value[*]` 的 `value`（label）与 `key` 构建；同一 label 出现两次 → 该题失败 | 表单提交构建 |
| 失败题结果行 | 既有列结构 | `answer = "[执行失败] <原因≤200字>"`；`recall/precision/F1` 与语句计数 = `N/A` | 结果 CSV + 评分聚合（自动排除） |
| 预签名 URL | 临时，不入库不入结果 | 每行执行时按需生成（`get_share_link_sync`） | 表单提交 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `evaluation/domain/services/evaluation_service.py`（改） | `parse_csv` 扩展（form_values 校验、question 可选）；`execute_workflow_get_answer` 表单分支（映射/签名/提交）；workflow 分支逐题隔离；失败行注入 | 不改问答型分支行为、不改 assistant 分支 |
| 创建端点（改） | CSV 校验规则：ground_truth 必填（替代 question 必填） | — |
| `platform/public/dataset.csv`（改） | 下载模板加 `form_values` 列说明 | — |

### 4.4 前端交互

- 无新增交互。评测创建页与对比视图直接可用（表单型评测产出的结果 CSV 与问答型同构）。
- 唯一前端变更：下载模板 CSV 增加 `form_values` 列头（含示例注释行）。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | 运行时表单的字段 key 每次会话随机（`file_<uuid>`），与流程定义 key 无关联字段 | 按流程 key 提交必失败（2026-09-05 批量评测实证） | 用 input 事件 schema 的 label 做映射（决策 1） |
| 2 | `set_user_input` 的校验要求 schema 中**全部**表单项都出现在提交 payload 里（缺项报 "key not found"） | 只填数据集中出现的项 → 整题报错 | payload 构建时遍历 schema 全部项：可选文本缺省空串、文件缺失则判该题失败 |
| 3 | output 节点成功时不发 end 事件（引擎既有行为） | 答案采集等待 end 而超时/漏采 | 复用既有对话分支的答案采集逻辑（已验证可工作），不新写采集 |
| 4 | `form_values` 里的对象路径需要区分桶：`tmp-dir/` 前缀是临时桶，其他路径是默认桶 | 预签名 404 | 路径约定写进签名 helper（决策 2） |
| 5 | 失败题若不从评分批次剔除，空答案会被评判 LLM 打 0 分而不是 N/A | 失败题拉低总分、违背 AC-09 | 评分批次只含正常题；失败行单独注入（决策 5） |
| 6 | 结果 CSV 的合计行由 `build_result_table` 生成——注入失败行必须在其**之前**，且合计行聚合不受影响（N/A 自动排除，复用 NaN 清洗口径） | 合计行位置错乱或把 N/A 当 0 | 注入顺序：正常行 → 失败行 → 合计行 |
| 7 | 创建校验要求 `question` 列（现行为）——表单型数据集可能没有 question 列 | 合法数据集被拒 | 校验改为 ground_truth 必填、question 可选（缺失时补空字符串列） |
| 8 | 表单型智能体的答案常为长报告（数千字）——评判 LLM 对超长答案的语句提取可能解析失败 | 该题分数为 N/A（评判失败，非执行失败）——既有 NaN 清洗口径兜底，属评判层问题而非本 Feature | 无需处理；如要提升覆盖率，另起 feature 优化评判提示词/分段评判 |
| 9 | `find_task_node_sync` 原实现调用 async 的 `update_alive_nodes` 而未 await（协程从未执行）——冷启动且无聊天流量的 API 进程中状态化 worker 注册表永远为空 → 工作流任务落到 **default 队列**（default worker 100 线程同时跑遥测任务，工作流执行受干扰/行为异常）；且注册表一旦被聊天路径捂热，execute 与 continue 可能派发到不同 worker → "workflow object not found" | 评测批量失败（2026-09-06 实证：3/4 题报 Only Q&A）；API 重启后首批评测必中 | 修复：改为 `update_alive_nodes_sync()`（本 Feature 附带修复，`worker/utils/stateful_worker.py`）；修复后重新验证通过 |
| 10 | 创建端点的数据集复用分支解包顺序错误：`resolve_compare_params` 按契约返回 `(file_path, file_name)`，端点却按 `file_name, file_path` 解包 → 新评测的 file_path 存成文件名、读取数据集时 MinIO NoSuchKey（2026-09-06 实证：reuse_dataset_id 创建即失败） | 复用历史数据集（F053 流程）的评测全部失败 | 修复：端点改为 `file_path, file_name = reused`（`evaluation/api/endpoints/evaluation.py`）；单测 `test_reuse_dataset_owned_by_user` 顺序断言不变 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| 评测数据集 `form_values` 列语义（label 引用 + 对象路径） | CSV 约定 | 数据集作者 |
| 表单型评测的结果 CSV（与问答型同构 + 失败行 `[执行失败]` 约定；表单行 question 列填案例摘要——决策 7；utf-8-sig BOM） | 文件格式 | F053 对比视图、下载用户 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| 工作流引擎输入状态协议（`input_schema` 结构、`set_user_input`/`INPUT_OVER` 协议） | 引擎 API | schema 结构变化需同步（引擎改动有测试覆盖） |
| `RedisCallback`/`execute_workflow`/`continue_workflow`（worker 队列派发） | 内部函数 | 既有，不改 |
| MinIO `get_share_link_sync` | 内部 API | 桶约定变化需同步（决策 2） |
| F053 对比端点 | HTTP API | 只读，结果 CSV 结构不变即兼容 |

---

## 7. 测试与可观测

- **单元**（pytest，`test/evaluation/`）：
  - `parse_csv`：form_values 合法/非法 JSON（带行号）、question 列缺失补空、ground_truth 缺失拒绝
  - label→key 映射与 payload 构建：全项填值、可选文本空串、文件路径转签名（mock minio）、label 不存在/重复、必填文件缺失
  - 失败行注入：位置（合计行之前）、N/A 聚合不受影响
- **e2e**：用流水分析智能体（表单型，v28/v34）+ 含 form_values 的数据集跑一次真实评测 → 状态 success、结果 CSV 含答案；F053 对比流程对表单型可用（AC-11）；单题坏数据（文件路径不存在）→ 该题 `[执行失败]`、其余正常。
- **手动验证**：评测列表查看表单型评测分数卡；下载结果 CSV 检查失败行格式与 question 列案例摘要。
- **可观测**：单题失败日志（logger.warning 带题目序号与原因）；预签名失败对象名记录。

---

## 8. 后续改进 / 不打算做的事

- 数据集文件上传管理界面（对象路径手填 → 可视化选择，另起 feature）。
- ~~表单值在结果 CSV 中的展示列~~（2026-09-06 已做：决策 7 摘要方案）。
- 结果 CSV 的 xlsx 多 sheet 形态（决策 7 备选 C；需要结构化明细时再考虑）。
- 助手/灵思表单型评测（另起 feature）。
- label 文案变更的兼容（数据集引用旧 label 会失败——错误信息里给出当前表单的全部 label，帮助作者修正）。
