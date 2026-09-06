# Tasks: 风险合规知识库（055-risk-compliance-knowledge）

> 本文档 = 执行流水（append-only 运行日志）。How 的真相在 [design.md](./design.md)，验收口径在 [spec.md](./spec.md)。

## 状态表

| 文档 | 状态 | 评审结论 / 备注 |
|------|------|----------------|
| spec.md | ✅ 已评审 | 2026-09-06 用户确认（18 AC，1 项 low 已修：contract 补登检索注入归属行） |
| design.md | ✅ 已评审 | 2026-09-06 用户确认（8 项决策；§7 补手动验证 URL） |
| tasks.md | ✅ 已拆解 | 见下 |

## 任务列表（按 Wave 执行；每任务 1-2 文件，一次会话内可完成）

### Wave 1 — 数据模型与维护 API（后端，可并行）

- [ ] **T-01（测试先行）** 新建 `test/knowledge/test_risk_compliance_model.py`：`Knowledge.weight` 默认 1.0、`Knowledge.priority` 默认 0、`KnowledgeFile.effective_date/expire_date` 可空（null=无限制）；预设分类标签初始化幂等——首次建库创建「行内制度/监管法规/行业分析/历史案例」4 标签（business_type=knowledge、business_id=KB id），再次初始化不重复建。 覆盖 AC: AC-01, AC-06
- [ ] **T-02** `knowledge/domain/models/knowledge.py` + `knowledge_file.py` 加字段（SQLModel 定义 + schema 透出），跑 T-01 转绿。 覆盖 AC: AC-01, AC-06 依赖: T-01
- [ ] **T-03** `knowledge/domain/services/knowledge_service.py` 新增预设分类标签初始化方法（幂等，创建知识库时调用）。 覆盖 AC: AC-01 依赖: T-02
- [ ] **T-04（基础设施）** Alembic revision：knowledge 表加 weight/priority 列（server_default 1/0）、knowledgefile 表加 effective_date/expire_date 列（可空）；含 downgrade 回滚。注意：create_all 不改既有表，必须走 revision（`backend/CLAUDE.md` 迁移规则）。 覆盖 AC: — 依赖: T-02
- [ ] **T-05** 知识库编辑端点与文档编辑端点透出 weight/priority/effective_date/expire_date（`api/endpoints/knowledge.py` + 对应 schema）。 覆盖 AC: AC-05, AC-08, AC-09, AC-10 依赖: T-02, T-04

### Wave 2 — 检索注入（后端，依赖 Wave 1 模型）

- [ ] **T-06（测试先行）** 新建 `test/knowledge/test_risk_compliance_retrieval.py`：时效过滤构造——null 容忍（字段缺失不排除）、生效区间边界（等于生效日/失效日）、未来生效排除；`today` 动态表达式解析；标签过滤构造（tag_ids→file_ids→document_id expr，与 metadata 条件 AND 组合）；权重/优先级排序（weight=0 不召回、优先组先召回不足补普通组、未配置=既有行为）；top_k 与 ef 联动、阈值过滤位置（RRF 融合后）。 覆盖 AC: AC-03, AC-05, AC-06, AC-07, AC-09, AC-10, AC-11, AC-12, AC-13
- [ ] **T-07** `workflow/common/knowledge.py` 实现注入：RagUtils 新参数 tags/top_k/score_threshold；时效过滤默认注入（null 容忍，design 决策 2）；权重/优先级后处理（design 决策 3）；metadata_filter 支持 `today` 表达式；跑 T-06 转绿。 覆盖 AC: AC-03, AC-05, AC-06, AC-07, AC-09, AC-10, AC-11, AC-12, AC-13 依赖: T-05, T-06
- [ ] **T-08** `knowledge/domain/services/space_flow_retrieval.py` 同步注入（知识空间路径，design 坑 3：双链路必须同改）。 覆盖 AC: AC-03, AC-05, AC-06, AC-07, AC-09, AC-10, AC-12 依赖: T-07
- [ ] **T-09（测试先行）** 新建 `test/knowledge/test_hit_test_endpoint.py`：命中测试端点——返回命中片段/文档/分数/失效排除统计；与工作流检索同一构建函数（同输入同输出）。 覆盖 AC: AC-14
- [ ] **T-10** 命中测试端点实现：`api/endpoints/knowledge.py` 新增 `POST /knowledge/{id}/hit_test`（只读，复用检索构建，返回诊断信息）。 覆盖 AC: AC-14 依赖: T-07, T-09
- [ ] **T-11** 删除标签级联解除文档关联：`knowledge_service.delete_knowledge_tag` 删除标签时同步删除该标签的全部 TagLink；先补测试再改实现（测试并入 T-11 内，文件 `test/knowledge/test_tag_delete_cascade.py`）。 覆盖 AC: AC-04 依赖: T-03

### Wave 3 — 前端 Platform（依赖 Wave 1/2 API）

- [ ] **T-12** 标签中心页：新建 `pages/KnowledgePage/components/tags/TagCenterDialog.tsx`（建/改/删标签，复用 `controllers/API/knowledgeTags.ts` 既有 CRUD），知识库详情页入口。 覆盖 AC: AC-01, AC-04 依赖: T-05
- [ ] **T-13** 时效/权重编辑 UI：知识库编辑弹窗扩展（weight/priority）、文档元数据弹窗扩展（effective_date/expire_date）、Files.tsx 失效/未生效标识（列表行状态，AC-07）。 覆盖 AC: AC-05, AC-07, AC-09, AC-10 依赖: T-05
- [ ] **T-14** 命中测试弹窗：知识库详情页入口 + `controllers/API/knowledge.ts` 新增 hit_test 调用，展示命中列表与排除统计。 覆盖 AC: AC-14 依赖: T-10
- [ ] **T-15** 工作流检索节点参数前端：`RetrievalWeightSlider.tsx` 扩展 tags 选择、top_k、score_threshold；`controllers/API/workflow.ts` 节点参数模板同步。 覆盖 AC: AC-03, AC-12 依赖: T-07
- [ ] **T-16** i18n：新增文案三语（zh-Hans/en-US/ja，knowledge 命名空间）；顺带清理 `KnowledgeTagSelect.tsx` 既有硬编码中文（改文件即清债，根目录 i18n 规则）。 覆盖 AC: — 依赖: T-12, T-13, T-14, T-15

### Wave 4 — 回归、e2e 与收尾

- [ ] **T-17（回归）** 未配置新参数的既有工作流/问答路径行为不变（单测）；标签 bug 回归（新建标签→保存→列表立即显示，AC-02）；权限与租户口径（新字段维护受既有写权限约束、租户隔离）。 覆盖 AC: AC-02, AC-11, AC-13, AC-17, AC-18
- [ ] **T-18** `/e2e-test features/v2.7.0/055-risk-compliance-knowledge`：API 级——真实知识库（或新建测试库）上传→预设分类标签初始化→打标→设生效/失效日期→工作流检索只召回生效文档（含未来生效/已失效排除）→多库权重与优先级排序→命中测试与工作流结果一致→双路径（文档库 + 知识空间）各跑一遍；MCP 分流口径验证（AC-15：实时数据不进知识库检索；AC-16：静态资料入库同口径）。页面手动验证清单。 覆盖 AC: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18
- [ ] **T-19** `/code-review --base main`；实现偏差回写 design.md（决策/坑）；tasks 状态表更新。 覆盖 AC: —

## 实际偏差记录

- （实现期间按 design.md 顶部调整原则回写）

## 执行日志

| 时间 | 记录 |
|------|------|
| 2026-09-06 | spec 评审通过（用户确认；low 项已修——contract 补登检索注入归属行）；design 评审通过（用户确认 8 项决策）；tasks 拆解完成 |
