# Tasks: 数据库知识问答（数据源接入 · NL2SQL · 对话侧混合来源）

**关联规格**: [spec.md](./spec.md)（66 条 AC）
**版本**: v2.7.0
**所属 Feature**: F061-datasource-qa

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-17 用户确认。首轮审查 2 CONFLICT 已消除（契约登记完成 + AC-41 收窄遵守 INV-11）；3 条 low ISSUE 经用户确认保留原样（AC-01/AC-10/AC-22 字段枚举、AC-39 意图声明、AC-21 并发约束） |
| design.md | ✅ 已评审 | 2026-09-17 用户确认。首轮 `/sdd-review design` 0 high / 3 medium / 3 low，**全部 6 条已修订**：① §9 补平台管理端 UI 步骤（spec AC-57，原缺失整块）；② 决策 6 补 AC-28 的「可观测口径」（注入形态标记三处共用）；③ §7 补可操作验证路径（uvicorn / pnpm start / :4001 等真实命令）；④ §4.2 补结果表快照与下载（AC-33）；⑤ §4.2 绑定行改为定稿表述 + §8 推荐项；⑥ §8 补 F058 既有文档漂移记录。**Constitution Check PASS**（C1–C7 无违反） |
| tasks.md | 🔲 骨架 | 本文件当前**仅为状态骨架**，任务拆解在 design.md 评审通过后进行 |
| 实现 | 🔲 未开始 | 0 / N 完成。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 契约登记状态（已完成，非任务）

> 本特性的版本契约登记已先行完成（2026-09-17），记录于此以免重复登记：

| 登记项 | 位置 | 内容 |
|---|---|---|
| 领域对象（7 行） | `../release-contract.md` 表 1 | `db_datasource` / `db_datasource_table` / `db_query_log` / `db_query_failure` / AuthorizationModel 新增数据源资源类型 / Agent 节点数据源配置 / 数据源检索工具 |
| 不变量 | `../release-contract.md` 表 2 | **INV-19**（数据源查询的安全与留痕边界，5 条；**不放宽任何既有不变量**） |
| 跨版本依赖 | `../release-contract.md` 表 2 注 | 消费 v2.5.0 **F004** 的 `AuthorizationModel` + 按 v2.5.0 **INV-15** 接入权限；**不修改 v2.5.0 任何既有文本** |
| Feature 依赖图 | `../release-contract.md` 表 3 | F061 全量依赖（跨版本 F004/F005 + 本版本 F056/F059 + 既有平台机制） |
| 错误码模块 | `../release-contract.md` 错误码表 | **117**（`common/errcode/datasource.py`）；分配前已扫三来源确认全空 |
| F058 版本归属调整 | ✅ **已完成**（2026-09-17，与本特性无关） | F058 由 v2.8.0 调整至 v2.7.0：目录已 `git mv`，表1 两行 / 表2 INV-12~15 / 表3 / 错误码 116 均已移入本版本契约，v2.8.0 相应移除。**唯一遗留**：迁移文件名 `v2_8_0_f058_doc_extract.py` 仍带 v2 前缀（详见本文件「实际偏差记录」） |

---

## 待办：design.md 评审后补齐

本文件的任务拆解（Wave 划分、Test-First 配对、`覆盖 AC` 追溯、文件清单）在 design.md 评审通过后编写。
届时须保证：**spec 的 66 条 AC 全部被至少一个任务的 `覆盖 AC:` 追溯**。

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**后端 Test-First（务实版）**：
- 理想流程：先写测试（红），再写实现（绿）
- 测试基础薄弱时，首个测试任务一并搭 pytest 基础设施（conftest.py、db fixture、mock helpers）
- 测试编写成本极高（如需完整 Milvus/ES mock）可标注 `**测试降级**: 手动验证 + TODO`，并在「实际偏差记录」说明
- 中间件 / DM8 / e2e 测试在 **CI** 跑，不依赖本地（见 `docs/SDD-Guide.md` §6）

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（`docs/SDD-Guide.md` §6，🚧 规划中）—— 落地后前端任务改写自动化断言。

**自包含任务**：每个任务内联文件、逻辑、AC 覆盖，实现期不必回读 spec。但**设计论证（为什么这么做）指向 design §X，不复制**（避免第三处漂移）。

---

## 已知实现前置（design 阶段须先解决）

> 以下为本特性在 spec 评审中暴露的、**必须在 design 阶段给出结论**的事项，不在此处展开方案：

1. **`sqlglot` 不在依赖中**（`pyproject.toml` 无、代码无人用、venv 未装）——需决定引入方式；且其**无达梦方言**，需给出达梦下的解析策略（对应 AC-20 的降级口径）。
2. **达梦库内注释可否自动采集需实测**——若不可，则对应 AC-14 的界面明示口径成为达梦数据源的必经路径。
3. **F004 授权模型的新增类型定义**——需确定新增资源类型的 FGA type definition 与关系集合，并按 INV-15 接入（对应 AC-66 / C4）。
4. **既有 `sql_agent` 的同步阻塞**（`bisheng_langchain/gpts/tools/sql_agent/tool.py:75-76` 的 `arun` 直接委托同步 `run`）——对应 AC-21，重写时须一并处理。
5. **既有内联 `sql_agent` 配置的兼容读取**——对应 AC-54 / AC-56，需确定兼容读取的边界（读旧、不新建）。

---

## 实际偏差记录

> 实现期发现的与 design/spec 的偏差记在此处，并回写对应文档（`docs/SDD-Guide.md` §7）。

### 文档层遗留（非本特性实现偏差，但涉及同一批改动）

1. **F058 迁移文件名仍带 v2.8.0 前缀**：`src/backend/bisheng/core/database/alembic/versions/v2_8_0_f058_doc_extract.py`。
   F058 已由 v2.8.0 移入 v2.7.0，但该文件名未随之重命名。**迁移链本身不受影响**——其 `revision = "f058_doc_extract"`、`down_revision = "f059_hit_log_source"`，与其他迁移的引用关系不依赖文件名。
   是否重命名待用户决定（重命名会与既有 git 历史冲突；不重命名则保留一处易误导的命名）。**在决定前，任何按文件名推断版本归属的做法都会出错。**
2. **F058 design.md §2 的 C5 落点仍写「错误码模块 114」**（`features/v2.7.0/058-doc-extract-template/design.md:35`），而实际与契约均为 **116**。该文件的变更历史（`:487`）已记录 114→116 的改判，但正文未同步。属既有文档漂移，非本次搬迁引入。
