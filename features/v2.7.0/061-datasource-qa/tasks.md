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

## 实现前置 —— 调研结论（2026-09-18，全部已闭环）

> 以下 5 条是 spec/design 评审时留待解决的问题。**已于 2026-09-18 逐条实测/查证，结论如下。**
> 其中第 1 条推翻了 design 决策 7 的一个**前提假设**，并暴露出一个 design 未覆盖的**校验口径缺口**（见 R1-补）。

### R1. `sqlglot` 引入方式与达梦解析策略 —— ✅ 可引入，且**达梦担忧不成立**

- **可解析安装**：`uv pip install sqlglot` → **30.18.0**（走既有 index，无需新增源）。
- **无达梦方言属实**：34 个内置方言中确无 `dm` / `dameng`。
- **但「达梦特有语法会解析失败」不成立** —— 实测 27 种达梦/Oracle 风格语法（`ROWNUM` 分页、`(+)` 外连接、`CONNECT BY`、`NVL`/`DECODE`、`DUAL`、`FETCH FIRST`、DM 注释 hint、`VARCHAR2`、`@dblink`、`PARTITION`、`TABLE()` 函数、中文标识符……）在 **oracle / mysql / 默认方言下全部解析成功，零失败**。
- **结论**：design 决策 7 的 C 方案（AST 严格档 + 窄校验兜底）**继续成立**，但决策理由需改写 —— 达梦不是「解析失败」的主要来源，**真正的失败源是语法噪音**（见 R1-补）。
- **方言选择**：达梦数据源用 `oracle` 方言解析（DM 与 Oracle 语法亲缘最近）；其余引擎用各自方言。

### R1-补. ⚠️ **新发现：`sqlglot` 的 AST 校验存在三个必须显式处理的绕过面**

实测发现，若按「根节点是否为 `Select`」这一直觉写法判定，**AC-16 / AC-17 会被绕过**。`sql_guard.py` 的实现必须按下表处理：

| 绕过手法 | 实测结果 | 正确判据 |
|---|---|---|
| **多语句**：`SELECT 1; DROP TABLE t` | `parse_one` **不报错**，返回 `Block(expressions=[Select, Drop])` | 必须用 `sqlglot.parse()` 取语句数，**`len(stmts) != 1` 即拒**（AC-16）；不能只看根节点 |
| **CTE 内藏写操作**：`WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` | 解析成功，**根节点是 `Select`**，`Delete` 在子节点 | 必须**遍历整棵树**找写节点，不能只判根节点（AC-17） |
| **加锁读取**：`FOR UPDATE` / `LOCK IN SHARE MODE` / `FOR SHARE` | 解析成功且**锁子句在 AST 中被剥离**：`parse_one(...).sql()` 输出 `SELECT * FROM t`；仅 `args['locks']` 残留 `Lock(update=True)` | 必须检查 `tree.args.get('locks')`，**不能靠 SQL 文本里有没有 `FOR UPDATE` 字样**（AC-17 明列「加锁读取」） |

**另**：`FOR UPDATE SKIP LOCKED` **确实解析失败**（ParseError）。按 AC-20，解析失败**不得拒绝执行**，故它将落到窄校验档 —— 而窄校验若不单独识别 `FOR UPDATE`，这条**加锁读取会被放行**。窄校验档**必须**包含 `FOR UPDATE` / `LOCK IN SHARE MODE` 关键字检查。

> **对决策 8 的影响**：这进一步坐实「AST 校验不是安全边界」（design 决策 8 的判断正确）——它至少有三个已知绕过面已由平台侧显式堵上，但仍不应被视为唯一依赖。只读账号仍是首要边界。

### R2. 达梦库内注释采集 —— ✅ **可自动采集**，AC-14 的界面明示口径**无需**成为达梦必经路径

- `dmSQLAlchemy` **实现了 `get_table_comment`**（`.venv/Lib/site-packages/dmSQLAlchemy/base.py:2746`），查 `user_tab_comments` / `all_tab_comments`。
- **列注释亦可**：`base.py:2433` 的反射查询已 select `a_col_comments.comments`（`ALL_TAB_COLS` ⋈ `ALL_COL_COMMENTS`）。
- **结论**：达梦数据源的表/字段注释可经 SQLAlchemy 标准反射接口（`Inspector.get_table_comment` / `get_columns` 的 `comment`）取得，与 MySQL 无差别。原担忧（对应 AC-14 的界面明示降级）**不成立**。

### R3. F004 授权模型新增数据源资源类型 —— ✅ 落点明确

- 定义集中在 `bisheng/core/openfga/authorization_model.py`，资源类型由 `_standard_resource_type(name, has_parent=..., parent_types=[...])` 生成（该文件 `:281-295` 已有 11 个既有资源类型）。
- **落点**：在同处追加 `_standard_resource_type('db_datasource')`。按 spec/契约，数据源与知识库**同级**（非其子类型），故 `has_parent` 用默认（False），不需要 `parent_types`。
- **注意**：`MODEL_VERSION`（`:33`，现 `v2.0.2`）需**同步递增** —— 授权模型变更后版本号不变会导致既有实例不重新加载。
- 权限接入按 v2.5.0 **INV-15**：创建时 `PermissionService.authorize()`、变更前 `check()`（对应 AC-66 / C4）。

### R4. 既有 `sql_agent` 同步阻塞 —— ✅ 确认存在，重写时须一并处理

- `bisheng_langchain/gpts/tools/sql_agent/tool.py:71-75` 的 `arun` 直接 `return self.run(query)`，而 `run` 调 `self.agent.invoke(...)`（**同步** LangGraph 调用）。
- 后果：在 async 链路中调用会**阻塞事件循环**，同一进程内其他对话请求排队 —— 正是 AC-21 禁止的。
- **处置口径**：本特性重写时改为 `await agent.ainvoke(...)`；既有 `SqlAgentTool` 若保留兼容读取，其 `arun` 也须同步修正（否则旧流程仍会阻塞）。

### R5. 既有内联 `sql_agent` 配置的兼容读取边界 —— ✅ 边界可明确

- **读旧**：AC-54 要求既有流程中仍配置内联连接信息的**继续正常运行**。边界是「保留既有 `sql_agent` 节点的解析与执行路径，不改其对外行为」。
- **不新建**：AC-56 要求内联入口**只保留兼容读取**，不得经它新建连接。边界是「前端的『新建数据源』入口不再产出内联配置；既有节点的配置字段保持只读兼容」。
- **回归口径**：以「既有 `sql_agent` 节点流程的输入输出不变」为验收（AC-54），而非以代码路径是否被复用为准。

---

## 实际偏差记录

> 实现期发现的与 design/spec 的偏差记在此处，并回写对应文档（`docs/SDD-Guide.md` §7）。

### 文档层遗留（非本特性实现偏差，但涉及同一批改动）

1. **F058 迁移文件名仍带 v2.8.0 前缀**：`src/backend/bisheng/core/database/alembic/versions/v2_8_0_f058_doc_extract.py`。
   F058 已由 v2.8.0 移入 v2.7.0，但该文件名未随之重命名。**迁移链本身不受影响**——其 `revision = "f058_doc_extract"`、`down_revision = "f059_hit_log_source"`，与其他迁移的引用关系不依赖文件名。
   是否重命名待用户决定（重命名会与既有 git 历史冲突；不重命名则保留一处易误导的命名）。**在决定前，任何按文件名推断版本归属的做法都会出错。**
2. **F058 design.md §2 的 C5 落点仍写「错误码模块 114」**（`features/v2.7.0/058-doc-extract-template/design.md:35`），而实际与契约均为 **116**。该文件的变更历史（`:487`）已记录 114→116 的改判，但正文未同步。属既有文档漂移，非本次搬迁引入。
