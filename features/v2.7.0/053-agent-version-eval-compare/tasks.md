# Tasks: 智能体版本对比评测（同一数据集 × 双版本）

> 本文档 = 执行流水（append-only 运行日志）。How 的真相在 [design.md](./design.md)，验收口径在 [spec.md](./spec.md)。

## 状态表

| 文档 | 状态 | 评审结论 / 备注 |
|------|------|----------------|
| spec.md | ✅ 已评审 | 2026-09-05 用户确认（一键双版本/总分+逐题/数据集复用） |
| design.md | ✅ 已评审 | 2026-09-05 用户确认 |
| tasks.md | ✅ 已拆解 | 见下 |

## 任务列表（按 Wave 执行；每任务 1-2 文件，一次会话内可完成）

### Wave 1 — 后端数据模型与创建扩展

- [x] **T-01（基础设施）** Alembic revision：`evaluation` 表加 `compare_group_id` 列（varchar(36) 可空 + index，DM8 兼容写法，无数据迁移——不回填历史）。回滚方案 = revision downgrade（DROP COLUMN）。 覆盖 AC: AC-01
- [x] **T-02** `evaluation/domain/models/evaluation.py`：`EvaluationBase` 加 `compare_group_id: str | None = Field(default=None, index=True)`。 覆盖 AC: AC-01
- [x] **T-03（测试先行）** 新建 `test/evaluation/test_eval_compare_create.py`：创建参数校验（两版本相同 → 400；`reuse_dataset_id` 归属非当前用户 → 拒绝；未提供 reuse 且无 file → 400；提供 reuse 且无 file → 允许）。 覆盖 AC: AC-02, AC-03
- [x] **T-04** `evaluation/api/endpoints/evaluation.py` 创建端点扩展：新参数 `compare_version`/`reuse_dataset_id`（file 在 reuse 时可选）；校验逻辑（同 T-03 口径）；同事务插入两条评测（同 `compare_group_id`）；`background_tasks` 派发两个既有评测任务。 覆盖 AC: AC-01, AC-02, AC-03
- [x] **T-05（回归）** 单版本创建行为不变：既有参数调用走原分支（一条记录、group_id 为 NULL）。 覆盖 AC: AC-04

### Wave 2 — 对比服务与端点

- [x] **T-06（测试先行）** 新建 `test/evaluation/test_eval_compare_service.py`：CSV 解析（`"48.49%"` / `"N/A"` / `"nan%"` / 空 → 对应 float/排除）；逐题对齐（行序、文本兜底、空题跳过）；差值符号（v2-v1）；结论优先级（F1→召回→精确，容差 0.01）；N/A 题不计差值；双 N/A → unable；单边未完成/失败透出状态。 覆盖 AC: AC-07, AC-08, AC-09, AC-10, AC-11
- [x] **T-07** `evaluation/domain/services/evaluation_service.py` 新增对比方法（组归属校验、读两条评测 + 结果 CSV、对齐、差值/结论计算）+ 端点 `GET /api/v1/evaluation/compare/{group_id}`。 覆盖 AC: AC-07, AC-08, AC-09, AC-10, AC-11
- [x] **T-08（权限测试）** 对比端点：他人组 → 拒绝；登录态要求；租户口径与列表一致。 覆盖 AC: AC-12, AC-13

### Wave 3 — 前端（仅 Platform）

- [x] **T-09** `platform/src/controllers/API/evaluation.ts`：创建参数扩展（`compareVersion`/`reuseDatasetId`）+ `getEvaluationCompareApi`。 覆盖 AC: AC-01, AC-07
- [x] **T-10** `platform/src/pages/EvaluationPage/EvaluationCreate.tsx`：版本选择区支持「添加第二版本」（第二版本下拉排除已选）；数据集来源切换（上传 CSV / 复用历史评测下拉：文件名+版本名+时间）。 覆盖 AC: AC-01, AC-03
- [x] **T-11** `platform/src/pages/EvaluationPage/index.tsx`（列表渲染层把同 `compare_group_id` 两行合并为分组行 + 「对比」按钮）+ 新建 `platform/src/pages/EvaluationPage/compare/CompareSheet.tsx`（总分卡片三指标并排+差值红绿标注 + 结论条 + 逐题表（指标 Tab 切换、变好/变差/持平徽标、N/A 灰标）+ 单边状态条）。 覆盖 AC: AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11
- [x] **T-12** i18n 三语新增 evaluation.compare* 相关 key；`pnpm check-i18n` + `pnpm lint` + `pnpm typecheck` 通过（新文件不加 `@ts-strict-ignore`）。 覆盖 AC: AC-05, AC-07

### Wave 4 — 构建、e2e 与文档

- [x] **T-13** `/e2e-test features/v2.7.0/053-agent-version-eval-compare`：API 级（创建双版本对比组（复用数据集）→ 轮询至完成 → 对比端点总分/逐题/结论；单版本回归；权限拒绝）+ 页面手动验证清单（弹窗双版本/复用下拉、列表分组、对比 Sheet、N/A 与失败状态）。 覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13
- [x] **T-14** 前端重新构建（`manage.sh build`）；后端重启 + `alembic upgrade head`（T-01 revision 应用）；headless 验证对比视图。 覆盖 AC: AC-05, AC-07
- [x] **T-15** `/code-review --base main`；实现偏差回写 design.md。 覆盖 AC: —

## 执行日志

| 时间 | 记录 |
|------|------|
| 2026-09-05 | spec/design 评审通过（用户确认）；tasks 拆解完成 |
| 2026-09-05 | 实现完成：evaluation 23 测试全绿；API e2e（创建对比组/相同版本 400/失败状态透出/权限）+ headless UI（分组行/对比 Sheet/结论/逐题徽标）验证通过；修复 NaN 题目对齐坑（isinstance str 前置）；遗留：演示数据为合成组（真实 Q&A 双版本评测待有版本后跑） |
