# Design: 智能体版本对比评测（同一数据集 × 双版本）

> **本文档定位 — 现状快照（Why this How）**。spec.md 回答做什么，本文回答为什么这么实现。

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-05

---

## 1. 目标与非目标

- **目标**：评测功能支持「一键双版本对比」——选择智能体 + 两个版本 + 一份数据集（可复用历史评测的数据集），自动跑两份评测，并给出总分差值 + 逐题变好/变差的对比视图。
- **非目标**：N 版本对比、新评估指标、历史评测回填对比组、对比结果导出。

---

## 2. 关键约束

- 遵循 `docs/constitution.md` C1–C7。
- **单版本评测行为零变化**（AC-04）：创建端点的既有参数与行为完全保留，新参数可选。
- **评估执行链不改**：两份评测复用既有 `add_evaluation_task`（按版本执行评测的能力已存在——`evaluation.version` + `FlowVersionDao.get_version_by_id`）。
- **错误码**：不新增错误码（两版本相同 → HTTPException 400，与既有窗口/参数错误风格一致）。
- **时区/时钟**：本 feature 不写时间戳（复用既有 create_time）。

---

## 3. 方案对比与选定

### 决策 1：对比组的数据模型

- **备选**：
  - A. `evaluation` 表加可空列 `compare_group_id`（同组两条评测共享一个 uuid）
  - B. 独立 `evaluation_compare_group` 表
- **选定**：A
- **原因**：对比组只是"两条评测的分组关系"，没有独立生命周期与属性（组状态 = 两条子评测状态的投影）；B 增加联表与级联删除复杂度，收益为零。可空列对既有 30 行数据无影响。既有表结构变更 → 需 Alembic revision（create_all 不改造已存在表）。
- **何时该重新考虑**：需要 N 版本对比（组内任意条数 + 组级属性如"基准版本"）时，升级为 B 或组表 + 组内顺序列。

### 决策 2：数据集复用的机制

- **备选**：
  - A. 复制引用——新评测的 `file_path` 直接指向历史评测的 MinIO 对象（同一对象键）
  - B. 复制文件——把历史数据集对象复制成新对象再引用
- **选定**：A
- **原因**：评测数据集是不可变文件（上传后不被修改），共享引用零存储成本且口径天然一致（同一份题目与标准答案）；历史评测删除是逻辑删除（`is_delete`），对象保留，新评测不受影响（spec 边界）。B 徒增存储与无谓的一致性面。
- **何时该重新考虑**：数据集支持编辑/版本化时（那时数据集应成为一等对象，另起 feature）。

### 决策 3：逐题匹配策略

- **备选**：
  - A. 按行序对齐（同数据集 → 顺序必然一致），题目文本不一致时降级为文本匹配
  - B. 纯文本匹配
- **选定**：A（先序对齐，文本校验，失败降级文本匹配，再失败标注未匹配）
- **原因**：正常路径下两份评测来自同一数据集 → 行序一致，O(n) 对齐；文本匹配是兜底（spec 要求题目集不一致时按文本匹配）。文本相同但顺序错位时 B 也能兜住，A 校验失败后走 B 逻辑。
- **何时该重新考虑**：数据集允许自定义乱序/抽样评测时，直接以文本匹配为主。

### 决策 4：对比计算的口径

- **备选**：
  - A. 服务端计算对比（读两份结果 CSV，输出总分差值 + 逐题表 + 结论）
  - B. 前端拉两份原始数据自行对比
- **选定**：A
- **原因**：结论规则（F1 → 召回 → 精确，容差 0.01）与 N/A 处理（不参与差值）是业务口径，放服务端保证一致性与可测试性；前端只渲染。结果 CSV 的百分数字符串解析（"48.49%" → 0.4849，N/A → 排除）也在服务端统一处理。
- **何时该重新考虑**：对比规则需要用户自定义（权重、指标选择）时，把规则下放为前端参数 + 服务端通用计算。

### 决策 5：创建接口的扩展形态

- **备选**：
  - A. 扩展现有 `POST /api/v1/evaluation`：新增可选参数 `compare_version`（第二版本）与 `reuse_dataset_id`（复用历史评测 id，此时 `file` 可选）
  - B. 新端点 `POST /api/v1/evaluation/compare`
- **选定**：A
- **原因**：对比评测与普通评测共用 95% 的创建逻辑（上传/解析/任务派发），B 会复制整段逻辑并造成两条创建路径漂移；A 在既有端点内分支（插入两条记录），单版本调用完全不受影响。`file` 参数从必填变"未提供 reuse 时必填"——兼容既有调用。
- **何时该重新考虑**：对比评测参数膨胀到 5+ 个专有字段时，拆独立端点 + 共享内部 service。

### 决策 6：前端对比视图形态

- **备选**：
  - A. 列表内对比组卡片（两行合并为一个分组行 + 「对比」按钮）→ 右侧 Sheet 对比视图（复用 F052 的 Sheet 模式）
  - B. 独立对比页面/路由
- **选定**：A
- **原因**：对比是评测列表的衍生操作，Sheet 轻量、不打断列表上下文；列表分组合并实现为渲染层重组（同 `compare_group_id` 相邻合并），不改列表 API 分页语义。总分卡片 + 逐题表（指标切换 Tab：F1/召回/精确）+ 变好/变差/持平徽标。
- **何时该重新考虑**：对比内容需要深链分享/独立 URL 时，改为路由页面。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
创建对比评测:
  POST /api/v1/evaluation {file?|reuse_dataset_id, exec_type, unique_id, version, compare_version?}
    ├─ 校验: compare_version 存在且 != version（相同 → 400）；reuse_dataset_id → 校验归属当前用户并取其 file_path
    ├─ 上传/复用数据集 → 得到 file_path
    ├─ 插入两条 evaluation（同一 compare_group_id = uuid4，各自的 version）
    └─ background_tasks 各派发一次既有 add_evaluation_task（按版本执行，互不影响）

对比查询:
  GET /api/v1/evaluation/compare/{group_id}
    ├─ 校验组属于当前用户 → 读两条 evaluation
    ├─ 各自 result CSV（MinIO result_file_path；未完成/失败 → 状态透出）
    ├─ 逐题对齐（行序 → 文本校验 → 文本匹配兜底）
    └─ 总分差值 + 逐题表 + 结论（F1 → 召回 → 精确，容差 0.01）
```

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `evaluation.compare_group_id` | str uuid, 可空, index | 同组两条评测共享；单版本评测为 NULL | 列表分组渲染 + 对比端点 |
| 创建端点新参数 | `compare_version: int\|str\|None`、`reuse_dataset_id: int\|None` | `file` 在提供 `reuse_dataset_id` 时可选 | 前端创建弹窗 |
| 对比响应 | `{group_id, evaluations: [{id, version, status, result_score, description}], comparison: {totals: {f1/recall/precision: {v1, v2, delta}}, conclusion, per_question: [{question, v1: {f1,recall,precision}, v2: {...}, verdict: better/worse/same/na}]}}` | 差值 = v2 - v1（正数 = 版本 2 更好）；N/A 题 verdict=na 且不计入总分差值 | 前端对比 Sheet |
| 结论规则 | F1 优先 → 召回 → 精确；差值绝对值 > 0.01 才计为差异；双 N/A → unable | 服务端固定口径 | 前端结论高亮 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `evaluation/api/endpoints/evaluation.py`（改） | 创建端点扩展：解析新参数、校验、双条插入、派发两任务 | 不改执行链、不改单版本分支 |
| `evaluation/domain/services/evaluation_service.py`（改） | 新增对比 service：组归属校验、CSV 读取解析、逐题对齐、差值/结论计算 | 不写评测结果（只读） |
| `evaluation/domain/models/evaluation.py`（改） | `compare_group_id` 字段 | — |
| `core/database/alembic/versions/`（新 revision） | `evaluation` 表加 `compare_group_id` 列（DM8 兼容写法） | 无数据迁移（不回填历史） |
| `platform/src/pages/EvaluationPage/EvaluationCreate.tsx`（改） | 创建弹窗：双版本选择 + 数据集来源切换（上传/复用历史评测下拉） | — |
| `platform/src/pages/EvaluationPage/index.tsx`（改） | 列表对比组渲染 + 对比入口 | — |
| `platform/src/pages/EvaluationPage/compare/CompareSheet.tsx`（新） | 对比视图：总分卡片 + 逐题表（指标 Tab）+ 结论 | — |
| `platform/src/controllers/API/evaluation.ts`（改） | 创建参数扩展 + `getEvaluationCompareApi` | — |

### 4.4 前端交互

- 创建弹窗：版本选择区在既有单选基础上支持「添加第二版本」→ 版本 B 下拉（同一版本列表，排除版本 A）；数据集来源 SegmentedControl（上传 CSV / 复用历史评测——下拉展示"文件名 + 版本名 + 时间"）。
- 列表：遍历时把同 `compare_group_id` 的两行合并为一个分组行（展示两版本名 + 各自分数摘要），行尾「对比」按钮打开 Sheet；单版本行渲染不变。组内一条被删除后列表只剩一条 → 自然按普通行渲染（AC-06 无需额外状态处理）。
- 对比 Sheet：顶部两版本分数卡（三指标并排，差值红色/绿色标注）+ 结论条（如「版本 1.6 准确性更高（F1 +12.34）」或「无法比较」）+ 逐题表（指标 Tab 切换；每题行：题目文本截断、V1 分数、V2 分数、变好/变差/持平徽标；N/A 灰色标注）。
- 两版本任一未完成/失败：对比 Sheet 顶部状态条（进行中/失败原因），总分卡仅渲染已完成版本（AC-10）。

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `evaluation` 是既有表——加列必须走 Alembic revision，`create_all` 不会改已存在的表 | 新环境表缺列、运行时报错 | revision 只加列（可空、无数据迁移），DM8 兼容 |
| 2 | 结果 CSV 的百分比是字符串（`"48.49%"`），N/A 是字符串 `"N/A"`（2026-09-05 清洗后），历史 CSV 里还有 `"nan%"` 残留 | 直接 float() 解析崩溃或把 nan 当 0 | 对比服务统一解析：`"N/A"`/`"nan%"`/空 → 排除；否则 strip `%` 转 float |
| 3 | CSV 的 question 列可能含 NaN/空行（历史评测数据）；`str(float('nan'))` 会得到字面量 `"nan"` 而被当成题目 | 逐题匹配错位、出现名为 "nan" 的假题目行 | 对齐前先 `isinstance(q, str)` 再 strip 判空（实现于 `_align_questions`） |
| 4 | 两份评测共用同一 MinIO 对象——删除历史评测是逻辑删除，对象保留 | 误以为复用关系会因删除而断链 | 复用即复制引用，无反向依赖；实现不查原评测存在性（仅创建时校验归属） |
| 5 | 对比组两条评测的任务是独立的两个 background_tasks——完成时间不同 | 对比视图在一条未完成时误报错 | 对比端点按状态分支：完成才读 CSV，未完成透出状态（AC-10） |
| 6 | 前端列表分组要在**渲染层**做（同组相邻合并），不能改列表 API 的分页/排序语义 | 分页错乱、组被切页拆散 | 渲染层重组 + 备注：同组两条相邻（创建时同秒插入） |
| 7 | `FlowVersionDao.get_version_by_id` 执行的是版本快照——评测期间改版本不影响已创建评测 | 误担心对比口径漂移 | 既有行为，无需处理（spec 边界已确认） |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `POST /api/v1/evaluation` 扩展参数（`compare_version` / `reuse_dataset_id`，均可选，向后兼容） | HTTP API | 评测创建弹窗 |
| `GET /api/v1/evaluation/compare/{group_id}` | HTTP API | 评测列表对比入口 |
| `evaluation.compare_group_id` 列 | MySQL 表 | 本 Feature 列表/对比 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `add_evaluation_task`（既有评测执行链，按版本执行） | 内部函数 | 执行链改动需同步（本 feature 不改它） |
| 版本列表 API（`get_version_list_by_flow`） | HTTP API | 前端第二版本下拉数据源 |
| MinIO 对象存储（数据集与结果 CSV） | 对象存储 | 结果 CSV 列名变化会破坏解析（坑 2/3） |
| Alembic 迁移机制 | 内部机制 | revision 命名/依赖链遵循 `alembic/AGENTS.md` |

---

## 7. 测试与可观测

- **单元**（pytest，`test/evaluation/`）：对比计算（差值符号、结论优先级与容差、N/A 排除、双 N/A → unable）、CSV 解析（`"48.49%"`/`"N/A"`/`"nan%"`/空）、逐题对齐（行序、文本兜底、空题跳过）、创建校验（版本相同 400、reuse 归属校验）。
- **e2e**：创建双版本对比组（复用数据集）→ 轮询至完成 → 对比端点返回总分/逐题/结论；单版本创建回归（行为不变）。
- **手动验证**：会议纪要 flow（c6c9b2c3）真实两个版本 + 同一 CSV → 对比 Sheet 分数卡/差值/逐题徽标正确。
- **可观测**：对比端点 CSV 解析失败日志（不静默吞）；创建双条插入用同一 session 事务（一条失败则回滚，不留孤儿组）。

---

## 8. 后续改进 / 不打算做的事

- N 版本对比与组级"基准版本"概念（决策 1 触发条件）。
- 数据集成为一等对象（可命名/复用/编辑）——当前复用是"引用历史评测的文件"。
- 对比报告导出。
- 新评估指标（语义一致性等）——对比层天然支持指标扩展，但指标本身另起 feature。
