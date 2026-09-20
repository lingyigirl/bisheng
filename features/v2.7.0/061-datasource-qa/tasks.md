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
| tasks.md | ✅ 已拆解 | 2026-09-18 拆解完成：**45 个任务 / 10 个 Wave**，66 条 AC 全覆盖（追溯矩阵见文末）。5 条实现前置已全部调研闭环（见下）；拆解前又发现 2 处引擎侧实现缺口（见「拆解期新发现」） |
| 实现 | 🚧 进行中 | **后端 W0~W7 已落地并提交**（T-01..T-29 + T-30，9 个 commit）。**2026-09-20 集中推进**：T-31 对话侧接线（10 commit）、T-35~T-39 平台管理端 UI、T-32（AC-52 真缺陷修复）、T-33（回答侧来源保证）、T-40/T-41 客户端。**核实 T-34 实为已完成**（随 T-31 落地）。**余下**：T-42 工作流节点收敛（含 R4 同步阻塞）、T-43 合规复用（行为验证）、T-44 不变量验证。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |
| e2e-test | 🔲 未开始 | `test/e2e/test_e2e_f061_datasource_qa.py` 已有 7 条（CRUD / 采集 / 删除），覆盖不到对话侧——T-31/T-32/T-33 均已落地，可补对话侧用例 |
| code-review | 🔲 未开始 | — |

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

## 拆解期决策（2026-09-18 用户确认）

design §8 的 4 项开放项 + 拆解粒度，经调研后已定：

| 项 | 决定 | 依据 |
|---|---|---|
| **FGA 模型升级路径**（§8-1，design 称为「本特性最大的部署风险」） | **`scripts/` 运维脚本** | 调研发现 `manager.py` 解析出的 `model_id` **不回写配置** —— 若用 `force_write_model=true`，每次进程启动都会在 OpenFGA 写入一个新模型并堆积。而「alembic 迁移直连 OpenFGA 写模型」这条路被 `alembic/AGENTS.md §5` **明确禁止**（F029 属祖父条款，原文：*do not edit released migrations, but never add new ones like them*）。`scripts/` 已有十几个 `backfill_*_rebac_*` 先例，是既定的一次性运维作业模式 |
| **DB2 驱动**（拆解期新发现） | **加进依赖** | AC-02 明列 DB2，但 `ibm_db` 既不在 `pyproject.toml` 也不在 venv；既有 `_init_sql_address` 的 db2 分支是死代码（try/except 为空 `pass`，从不真正 import） |
| **SQL 生成器**（§8-3） | **不复用 `SqlDatabaseChain`** | 查证：它是全仓零调用方的死代码，且**其自身 docstring 就警告**「may attempt commands like `DROP TABLE` or `INSERT` if appropriately prompted」—— 与 AC-16/AC-17 直接冲突。引入它等于把「可能生成 DROP TABLE」的链放进安全边界内 |
| **助手绑定**（§8-2） | **新增 `AssistantLink.datasource_id` 列** | 复用 `tool_id` 会让数据源继承 `tool` 的授权与管理页口径，与 AC-43（独立受控资源）/ AC-44（独立权限点）冲突 |
| **拆解粒度** | **全量**（含 client SPA） | AC-58/AC-59 覆盖客户端回答区，若留待后续拆解则追溯矩阵不完整 |

> **决策 7 的复核**：用户 2026-09-18 确认**维持方案 C**（订正理由、补判据，不改决策实质）。见 `3b3874ffe`。

### 拆解期新发现（实现时必须处理，design §4.3 未覆盖）

1. **既有 `_init_sql_address` 不支持达梦** —— `workflow/nodes/agent/agent.py:400-435` 只覆盖 mysql / db2 / postgres / gaussdb / oracle 五种，落到 `else: raise ValueError`。**AC-02 要求六种含 DM8**，故 design 说的「抽取复用」不够，必须**补达梦分支**。已验证 `dm+dmPython://` 方言可加载（`registry.load('dm.dmPython')` 通过，默认端口 5236）。
2. **DB2 驱动缺失** —— 见上表。既有代码里的 `ImportError("Please install ibm_db...")` 永远不会触发（`try: pass`），属静默死分支。

### 文档回写（本次拆解一并处理）

- **spec §3 边界情况最后一条**仍写「数据源为达梦 → 库内注释可能无法自动采集（AC-14），界面须明示该限制」—— **已被 2026-09-18 实测推翻**（见 R2：`dmSQLAlchemy` 实现了 `get_table_comment`，列注释亦可采）。该行需改为达梦与其它引擎一致。

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

**另**：有些**合法**语句确实解析失败（Oracle/DM 的 `MODEL` 子句等）。按 AC-20，解析失败**不得拒绝执行**，故它们落到窄校验档 —— 而窄校验若不单独识别 `FOR UPDATE`，则「不可解析语句 + 加锁读取」这一组合**无人拦截**。窄校验档**必须**包含 `FOR UPDATE` / `LOCK IN SHARE MODE` 关键字检查。

> **⚠ 订正（2026-09-18，T-10 实现期）**：本节此前写「`FOR UPDATE SKIP LOCKED` 确实解析失败」——**该结论错误**。探测语句当时写作 `SELECT ... FOR UPDATE SKIP LOCKED`，`...` 不是合法列清单，**是占位符导致的 ParseError**。真实语句可正常解析并由 AST 档拦截。窄校验档的加锁关键字**仍然必需**，只是服务的对象是 `MODEL` 子句那类真正不可解析的语句。

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

## Tasks

> **任务格式**：`**依赖**` / `**改动**`（文件路径）/ `**为什么**`（指向 design §X，**不复制论证**）/ `**验证**`（真实可跑的命令）/ `**覆盖 AC**`。
> **自包含**：实现期不必回读 spec；但设计论证一律指向 design，避免第三处漂移。

### Wave 0 — 基础设施（无测试配对，全部并行）

#### T-01 错误码 117 模块

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/common/errcode/datasource.py`：`Code = 117xx` 系列，覆盖 design §6 列出的十类（连接失败 / 引擎不支持 / 表结构采集失败 / 语句被安全策略拒绝 / 语句执行失败 / 执行超时 / 结果超行数上限 / 数据源不存在或无权限访问 / 数据源名称重复 / 模型槽位未配置）
- 继承 `BaseErrorCode`（同 `common/errcode/sensitive_word.py` 的写法）

**为什么**：design §6 对外契约；模块号 117 已在契约登记（本文件开头「契约登记状态」）。

**验证**：`uv run python -c "from bisheng.common.errcode.datasource import *"` 无 ImportError；`grep -c "117" bisheng/common/errcode/datasource.py` 与 design §6 的条数一致。

**覆盖 AC**: AC-03, AC-06, AC-39

---

#### T-02 四张表的 ORM 模型

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/datasource/domain/models/`：`datasource.py`（`db_datasource`）、`datasource_table.py`（`db_datasource_table`）、`query_log.py`（`db_query_log`）、`query_failure.py`（`db_query_failure`）
- 字段严格按 release-contract 表 1 的登记：`db_datasource` 含租户/名称/引擎/主机·端口·库名·账号/**密码密文**/启用位
- JSON 与长文本字段用 `dialect_helpers`（`JsonType` / `LargeText`）—— **双 DB 约束 C2**

**为什么**：design §4.2 字段约定 + release-contract 表 1（Owner 声明）。

**验证**：`uv run python -c "from bisheng.datasource.domain.models import datasource"` 通过；`uv run python -m pytest test/database/test_alembic_single_head.py -q` 仍单 head。

**覆盖 AC**: AC-04, AC-22, AC-23, AC-41

---

#### T-03 租户白名单登记（**易漏，坑 1**）

**依赖**: T-02

**改动**：
- 在 `bisheng/core/database/tenant_filter.py` 的 `_TENANT_AWARE_MODEL_MODULES` 追加四个模型模块路径

**为什么**：design §5 坑 1 —— 未登记则 `do_orm_execute` 不会注入租户过滤，**四张表全部跨租户可见**。这是静默失败，且既有先例（`knowledgefile` / `flowversion` 曾因此把子租户资源写进 root）。

**验证**：新增 `test/datasource/test_tenant_registration.py`，断言四个模块名在 `_TENANT_AWARE_MODEL_MODULES` 中（**这条测试比读代码可靠** —— 该列表是字符串常量，容易被后续重构切断）。

**覆盖 AC**: AC-50

---

#### T-04 迁移：四张新表 + `AssistantLink.datasource_id`

**依赖**: T-02

**改动**：
- 新增 `src/backend/bisheng/core/database/alembic/versions/v2_7_0_f061_datasource.py`
- `revision = "f061_datasource"`；**`down_revision` 必须用 `uv run alembic heads` 的实际输出**，不得按文件名推断（`alembic/AGENTS.md` §0/§1）
- `upgrade()`：`column_exists` / `table_exists` 守卫下建四张表 + `assistant_link` 加 `datasource_id` 列（可空，无 server_default）
- 命名遵循 §8-4 的结论：`v2_7_0_f061_datasource.py`

**为什么**：design §4.2 的助手绑定行；`alembic/AGENTS.md` §5（**DDL-only，禁数据迁移**）。

**验证**：
```bash
uv run alembic heads                    # 必须恰好一行
uv run alembic upgrade --sql head       # 离线干跑
uv run python -m pytest test/database/test_alembic_single_head.py -q
```

**覆盖 AC**: AC-50, AC-58

---

#### T-05 四张表的 DAO

**依赖**: T-02

**改动**：
- 在 `src/backend/bisheng/datasource/domain/models/` 内为每张表加 `*Dao` 类，方法形如 `acreate` / `aget_by_id` / `aupdate` / `alist`（游标或分页）
- 租户过滤依赖 T-03 的登记；读取路径如遇 Row 元组包装需 `row[0]` 解包（同 F056 踩过的坑，见 058 报告的坑 11）

**为什么**：design §4.3「`datasource/domain/models/` 职责：ORM + DAO，不写业务编排」。

**验证**：`uv run python -m pytest test/datasource/ -q`；DAO 单测 mock session 层（**不要 mock 掉 DAO 本身** —— 058 报告记录过「mock 掉 DAO 等于不测租户隔离」）。

**覆盖 AC**: AC-22, AC-23, AC-35, AC-41

---

### Wave 1 — 权限接入（依赖 W0）

#### T-06 FGA 新增 `db_datasource` 资源类型

**依赖**: 无（与 W0 并行）

**改动**：
- `src/backend/bisheng/core/openfga/authorization_model.py`：在既有 11 个资源类型后追加 `_standard_resource_type('db_datasource')`
- **同步递增 `MODEL_VERSION`**（`:33`，现 `v2.0.2`）

**为什么**：design 决策 10（新增一类受控资源，**与知识库同级**，非其子类型）+ R3 的落点结论。`has_parent` 用默认 `False`，不需要 `parent_types`。

**验证**：`uv run python -c "from bisheng.core.openfga.authorization_model import get_authorization_model as g; m=g(); print([t['type'] for t in m['type_definitions'] if 'datasource' in t['type']])"` 输出包含 `db_datasource`。

**覆盖 AC**: AC-43

---

#### T-07 FGA 模型升级运维脚本（**§8-1 定案**）

**依赖**: T-06

**改动**：
- 新增 `src/backend/scripts/migrate_fga_add_datasource_type.py` + `.sh` 包装（`PYTHONPATH="./"`，遵循 `scripts/AGENTS.md` §1/§2）
- 逻辑：读当前模型 → 比对是否已有 `db_datasource` 类型 → 缺失则写入新模型并**打印新 model_id 供运维填回配置**；已存在则跳过（**幂等**）
- 脚本须能从 `src/backend/` 目录运行

**为什么**：§8-1 用户定案。**不能用 alembic**（`alembic/AGENTS.md §5` 禁止），**不能用 `force_write_model`**（`model_id` 不回写配置 → 每次启动堆积新模型）。

**验证**：`bash scripts/migrate_fga_add_datasource_type.sh --dry-run`（脚本须实现 dry-run）；对着不可达的 OpenFGA 跑应给出可读错误而非堆栈。

**覆盖 AC**: AC-43

---

#### T-08 数据源权限点与资源类型注册

**依赖**: T-06

**改动**：
- 在权限体系的资源类型常量（`VALID_RESOURCE_TYPES` 及同类）中登记 `db_datasource`
- 在 `_MANAGE_PERMISSION_BY_RESOURCE` 类的映射中登记数据源的**独立管理权限点**（AC-44）

**为什么**：design 决策 10 配套；AC-44 要求「与知识库的管理权限相互独立」。

**验证**：`uv run python -m pytest test/permission/ -q -k datasource`；新增断言测试确认权限点与知识库的**不相等**。

**覆盖 AC**: AC-44, AC-66

---

#### T-09 数据源权限模板 + 创建时登记归属

**依赖**: T-08

**改动**：
- 新建数据源权限模板（参照 `permission/domain/knowledge_library_permission_template.py` 的形态）
- 创建路径调用 `PermissionService.authorize()` 写 owner 元组（**C4** 要求）
- 变更路径前置 `PermissionService.check()`

**为什么**：design 决策 10 + v2.5.0 INV-15 的接入要求（创建 `authorize()` / 变更前 `check()`）；AC-66 要求「不得绕过统一权限入口」。

**验证**：单测断言创建后调用了 `authorize()`、变更前调用了 `check()`（**断言调用而非 mock 掉**）；`bash scripts/arch-guard.sh` 无 C4 违规。

**覆盖 AC**: AC-45, AC-46, AC-66

---

### Wave 2 — SQL 校验器（**Test-First，全特性安全边界**）

> ⚠️ **本 Wave 必须先写测试**（红）再写实现（绿）。它是 AC-16/17/20 的唯一平台侧实现，也是 design 决策 8 明确「不承担唯一安全边界」但**仍须正确**的那一层。

#### T-10 `sql_guard.py`：AST 严格档 + 窄校验兜底档

**依赖**: T-01

**改动**：
- 新增 `src/backend/bisheng/datasource/domain/services/sql_guard.py`
- 双层结构：能解析走 AST 档；解析失败（ParseError）退窄校验档并**记录降级**
- 达梦数据源用 `oracle` 方言解析，其余引擎用各自方言（R1 结论）
- 返回值携带：放行/拒绝 + 拒绝原因 + **本次走的是哪一档**（降级可观测，AC-20）
- 新增依赖 `sqlglot` 到 `pyproject.toml`（**30.18.0**，走既有 index）

**为什么**：design 决策 7 方案 C。**决策理由见 design §3 决策 7 的修正版**（达梦不是风险源，风险源是语法噪音）。

**验证**：
```bash
uv run python -m pytest test/datasource/test_sql_guard.py -q
```
测试须覆盖（**这三条是决策 7 表记录的实测绕过面，必须有对应用例**）：

| 用例 | 期望 |
|---|---|
| `SELECT 1; DROP TABLE t` | **拒绝**（`parse_one` 不报错，必须用 `sqlglot.parse()` 判条数） |
| `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` | **拒绝**（根节点是 `Select`，必须遍历全树） |
| `SELECT * FROM t FOR UPDATE` | **拒绝**（锁子句被剥离，必须查 `args['locks']`） |
| `SELECT * FROM t LOCK IN SHARE MODE` / `FOR SHARE` | **拒绝**（同上） |
| 普通 `SELECT` / `WITH ... SELECT` | 放行 |
| `SELECT * FROM t FOR UPDATE SKIP LOCKED` | **拒绝**（AST 档，经 `args['locks']`；非降级） |
| `SELECT ... MODEL ... RULES (...)`（DM/Oracle） | **降级放行**（真 ParseError → 窄校验档） |
| 上述 `MODEL` 语句 + 尾部 `FOR UPDATE` | **拒绝**（窄校验档的关键字检查 —— 见 T-11） |
| `SELECT FROM WHERE`（语法错误） | **放行给数据库**（非缺陷：AC-37 明确「语法错误」属回喂模型重试的失败类型，护栏不是语法检查器） |

**覆盖 AC**: AC-16, AC-17, AC-20

---

#### T-11 窄校验档的关键字集

**依赖**: T-10

**改动**：
- 在 `sql_guard.py` 内实现窄校验：单语句（按 `;` 分隔后须只有一条有效语句）+ 首关键字 `SELECT`/`WITH` + **禁写关键字集**
- **禁写关键字集必须包含 `FOR UPDATE` / `LOCK IN SHARE MODE`**（R1-补 的连带结论）

**为什么**：design 决策 7 方案 B 的落地。**若窄校验不识别加锁读取，「不可解析语句 + 加锁读取」这一组合会被整体放行** —— 因为 AST 档根本没读懂它，而它是 AC-17 明列须拒的。

**验证**：`uv run python -m pytest test/datasource/test_sql_guard.py -q -k narrow`；须有专门用例断言「`MODEL` 语句 + 尾部 `FOR UPDATE`」走窄校验档且**被拒绝**。

**覆盖 AC**: AC-17, AC-20

---

#### T-12 校验器接入执行路径

**依赖**: T-10, T-11, T-05

**改动**：
- 执行器调用 `sql_guard` 前置校验；拒绝时抛 T-01 的「语句被安全策略拒绝」错误码
- 降级发生时写入执行记录的可观测字段

**为什么**：design 决策 7「解析失败 ≠ 拒绝执行」须在调用点体现；AC-20 要求降级被记录。

**验证**：`uv run python -m pytest test/datasource/ -q -k guard`；断言被拒语句**未到达数据库**（用 mock 连接断言 `execute` 未被调用）。

**覆盖 AC**: AC-16, AC-17, AC-20

---

### Wave 3 — 引擎连接层（依赖 W0）

#### T-13 六引擎 URI 拼装（**含补达梦，坑见「拆解期新发现」**）

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/datasource/domain/services/engine_registry.py`（或同层文件）
- 抽取 `workflow/nodes/agent/agent.py:400-435` 的既有拼装逻辑并**补齐两个缺口**：① 新增达梦分支 `dm+dmPython://`（默认端口 5236）；② DB2 分支改为真实可用
- 拒绝 SQLite（AC-06）
- 把既有 `_init_sql_address` 改为委托到新实现（保持兼容，见 T-42）

**为什么**：AC-02 要求六种引擎；「拆解期新发现」记录既有实现只支持五种且 db2 分支是死代码。R1 已验证 `dm+dmPython://` 可加载。

**验证**：`uv run python -m pytest test/datasource/test_engine_registry.py -q`；六种引擎各有用例，且断言**不支持的引擎被拒**（如 `sqlite`）。

**覆盖 AC**: AC-02, AC-06

---

#### T-14 补 DB2 依赖

**依赖**: 无

**改动**：
- `pyproject.toml` 增加 `ibm_db`（及所需的 `ibm_db_sa`）
- 若为平台相关包，参照既有 `dmPython; sys_platform != 'darwin'` 的写法加平台条件

**为什么**：AC-02 明列 DB2，但驱动缺失。见「拆解期决策」表。

**验证**：`uv sync` 成功；`uv run python -c "import ibm_db"` 通过。

**覆盖 AC**: AC-02

---

#### T-15 凭据加密与不回显

**依赖**: T-02

**改动**：
- 复用 `core/config/settings.py` 的 `encrypt()` / `decrypt()`（**不自造**）
- 保存路径：密码加密后落库
- **所有读取路径**（列表 / 详情 / 日志 / 错误消息）不得回显密码

**为什么**：C6（密钥类字段不得明文）+ AC-04。design §4.2 明确「复用既有 Fernet 工具」。

**验证**：
```bash
uv run python -m pytest test/datasource/test_credential.py -q
```
**必须有一条「序列化整个响应对象并断言密码明文不出现」的测试** —— 逐字段断言会漏掉新增字段（F058 曾因错误消息回显模型输出而泄漏 PII）。

**覆盖 AC**: AC-04

---

#### T-16 测试连接（失败零落库）

**依赖**: T-13, T-15

**改动**：
- 新增 `datasource/api/endpoints/` 下的测试连接端点
- 建立连接后立即关闭；**失败路径不产生任何落库写入**（AC-03）
- 失败原因可判定（超时 / 认证失败 / 库不存在 / 网络不可达）

**为什么**：AC-03 明确「失败不产生任何落库写入」—— 这是容易写成「先落库再试连」的地方。

**验证**：`uv run python -m pytest test/datasource/test_test_connection.py -q`；须有断言「测试连接失败后表行数不变」的用例。

**覆盖 AC**: AC-01, AC-03, AC-08

---

### Wave 4 — 表结构采集（依赖 W3）

#### T-17 列出可访问表

**依赖**: T-13

**改动**：
- 用 SQLAlchemy `Inspector` 列出当前账号可访问的表（`get_table_names` / `get_view_names`）
- 不读表内数据行（AC-13）

**为什么**：AC-09；design 决策 5（**不做**运行时反射、**不做**样例值采样）。

**验证**：`uv run python -m pytest test/datasource/test_schema_collect.py -q -k list_tables`。

**覆盖 AC**: AC-09, AC-13

---

#### T-18 结构快照采集（含库内注释）

**依赖**: T-17

**改动**：
- 采集所选表的表名 / 字段名 / 字段类型 / **库内注释**，存入 `structure_snapshot`（`JsonType`）
- 注释经标准反射接口取（`Inspector.get_table_comment` / `get_columns` 的 `comment`）—— **达梦与其它引擎无差别**（R2 实测）

**为什么**：AC-10；R2 已证达梦注释可采，**无需为达梦写降级分支**。

**验证**：`uv run python -m pytest test/datasource/test_schema_collect.py -q -k snapshot`；用例须含「注释为空的表仍采集成功」；**另需一条「修改连接信息 / 表清单后，后续查询立即用新配置、无需重启」的用例**（AC-08 —— 配置解析走进程缓存的实现极易在这里漏失效）。

**覆盖 AC**: AC-08, AC-10, AC-14

---

#### T-19 业务描述维护 + 重采集不覆盖

**依赖**: T-18

**改动**：
- 业务描述字段（`business_desc`）的读写路径
- **重新采集只刷新 `structure_snapshot`，绝不覆盖 `business_desc`**（AC-12）

**为什么**：AC-11 / AC-12；design §4.2 明确标注「人工维护，重采集不覆盖」。这是最容易在实现时被 `update_all` 顺手覆盖的字段。

**验证**：**回归用例**：先填描述 → 触发重采集 → 断言描述仍在、结构已更新。这条必须真实执行重采集逻辑（**不要 mock 掉 DAO** —— 058 报告记录过 mock DAO 导致漏掉真实缺陷）。

**覆盖 AC**: AC-11, AC-12

---

#### T-20 无定时轮询的保证

**依赖**: T-18

**改动**：
- 确认并测试：除管理员显式触发外，无任何 Celery Beat / 定时任务会重采集

**为什么**：AC-15 明确禁止定时轮询。design 决策 5 列了「不做定时采集」。

**验证**：`uv run python -m pytest test/datasource/test_no_polling.py -q`；建议以静态断言实现（扫描 Celery schedule 配置中不含采集任务），比 e2e 更可靠。

**覆盖 AC**: AC-15

---

### Wave 5 — 执行器（依赖 W2、W3）

#### T-21 异步执行器骨架

**依赖**: T-13

**改动**：
- 新增 `src/backend/bisheng/datasource/domain/services/executor.py`
- 全程 async；连接与执行**不得阻塞事件循环**（AC-21）
- 引擎侧驱动须选异步或放入线程池执行 —— **禁止在 async 路径直接调同步驱动**

**为什么**：AC-21「单次查询的耗时不得使同一进程内的其他对话请求排队」；design 决策 12。**R4 记录的既有 `sql_agent` 用同步 `invoke` 正是反面教材**。

**验证**：`uv run python -m pytest test/datasource/test_executor.py -q -k async`；建议加一条「执行期间事件循环仍可调度」的用例（如并发跑一个心跳协程并断言它未被饿死）。

**覆盖 AC**: AC-21

---

#### T-22 只读会话 + 降级记录

**依赖**: T-21

**改动**：
- 执行前尝试开启只读会话（`SET TRANSACTION READ ONLY` 或引擎等价物）
- **不支持只读会话的引擎降级执行，并记录该次降级**（AC-19）

**为什么**：design 决策 8 —— 只读边界是「只读账号（首要）+ 只读会话 + 平台校验（兜底）」。AC-19 的降级必须可观测。

**验证**：`uv run python -m pytest test/datasource/test_executor.py -q -k readonly`；须有「引擎不支持时降级放行且记录」的用例。

**覆盖 AC**: AC-19

---

#### T-23 行数上限与超时（含截断标注）

**依赖**: T-21

**改动**：
- 结果行数上限 + 执行超时约束
- 超限时**截断并在结果上标注已截断**（AC-18）
- 截断结果**复用同一套上限**供持久化（AC-35，不引入第二套裁剪规则）

**为什么**：AC-18 / AC-35；design §4.2 明确「量受执行期行数上限约束，**不二次裁剪**」。

**验证**：`uv run python -m pytest test/datasource/test_executor.py -q -k limit`；须断言截断标志为真且返回行数 == 上限；超时用例断言抛出对应错误码。

**覆盖 AC**: AC-18, AC-35

---

#### T-24 执行记录落库（只存语句与状态）

**依赖**: T-05, T-21

**改动**：
- 每次查询写 `db_query_log`：操作人 / 租户 / 数据源 / 涉及的表 / 执行的语句 / 状态 / 时间
- **不得写入结果集数据**（AC-23 / AC-65）

**为什么**：AC-22 / AC-23；INV-19 ③ 与 INV-11 的留痕边界。design §4.2 标注「只存语句与状态摘要」。

**验证**：**关键用例**：执行一次返回敏感数据的查询，然后断言日志行中**不含**任何结果集内容（序列化整行做子串检查，而非逐字段检查 —— 后者漏新增字段）。

**覆盖 AC**: AC-22, AC-23, AC-65

---

### Wave 6 — 生成与编排（依赖 W4、W5）

#### T-25 独立模型槽位 + 回退

**依赖**: T-01

**改动**：
- SQL 生成使用**独立模型槽位**（`tenant_system_model_config` 机制，同 F058 的 `doc_extract_llm` 用法）
- 槽位未配置时**回退对话模型且不报错**（AC-27）
- 取值须满足 INV-8（本租户内可见且类型匹配）

**为什么**：design 决策 11；AC-27。

**验证**：`uv run python -m pytest test/datasource/test_sql_generation.py -q -k slot`；须有「槽位未配置 → 回退且无异常」与「槽位配置但不可见 → 不采用」两个用例。

**覆盖 AC**: AC-27

---

#### T-26 schema 注入 + 阈值退化

**依赖**: T-18, T-25

**改动**：
- 默认全量注入暴露表的 schema（含表名 / 字段 / 类型 / 业务描述）
- 超阈值时退化为按当前问题**检索相关表**
- 注入形态标记（`full` / `retrieved`）在**执行记录 / 日志 / 响应三处共用同一枚举**（AC-28 的可观测口径）

**为什么**：design 决策 6 + §4.2 的「注入形态标记」行。AC-28 要求两种形态**可被区分观测**。

**验证**：`uv run python -m pytest test/datasource/test_schema_injection.py -q`；须有一条「超阈值时标记为 `retrieved`」的用例。

**覆盖 AC**: AC-28

---

#### T-27 SQL 生成（**不复用 SqlDatabaseChain**）

**依赖**: T-25, T-26

**改动**：
- 自建生成器（**不复用** `bisheng_langchain/sql/base.py:15` 的 `SqlDatabaseChain`）
- 生成的语句**必须过 T-10 的校验器**才进入执行

**为什么**：§8-3 用户定案；`SqlDatabaseChain` 的 docstring 自警可能生成 `DROP TABLE`，与 AC-16/17 冲突。

**验证**：`uv run python -m pytest test/datasource/test_sql_generation.py -q`；须有「生成的语句先过校验器」的接线断言。

**覆盖 AC**: AC-25, AC-26

---

#### T-28 失败回喂重试（≤3 轮）

**依赖**: T-21, T-27

**改动**：
- 执行失败时把数据库错误信息**回喂模型**并允许修正重试
- **次数上限 3 轮**，达到即停（AC-38）
- 重试耗尽后返回**不暴露**内部结构 / 连接信息 / 账号 / 错误堆栈的用户可读说明（AC-39）
- 失败**不得中断同轮对话**：同轮文档知识库有命中则仍正常作答（AC-40）

**为什么**：design 决策 13；AC-37～AC-40。

**验证**：`uv run python -m pytest test/datasource/test_retry.py -q`；须有「第 4 轮不再重试」「错误消息不含连接串 / 账号」两组用例。

**覆盖 AC**: AC-37, AC-38, AC-39, AC-40

---

#### T-29 失败留档 + 管理员失败清单

**依赖**: T-28

**改动**：
- 重试仍失败时写 `db_query_failure`：语句 + 错误类别摘要
- **不存结果集数据、不存用户提问原文**（AC-41 / AC-65）
- 新增失败清单查询端点（供管理员补全表描述）

**为什么**：AC-41 / AC-42；INV-19 ③、INV-11。

**验证**：**关键用例**：构造一次含用户提问原文的失败，断言留档中不含该原文（整行序列化子串检查）。

**覆盖 AC**: AC-41, AC-42, AC-65

---

### Wave 7 — 工具形态 + 对话接入（依赖 W6）

#### T-30 数据源检索工具形态

**依赖**: T-27

**改动**：
- 数据源包装为**可被模型调用的工具**，与文档知识库检索处于**同一调用层**（AC-26）
- 复用既有工具注册机制（F057 已证明 knowledge 工具与 sql_agent 同进 `func_tools` 列表，见 `workflow/nodes/agent/agent.py:266-268`）

**为什么**：design 决策 2；AC-26 要求同层、同轮内模型可自由选择。

**验证**：`uv run python -m pytest test/datasource/test_tool.py -q`；断言工具与知识库工具**在同一个列表中**（而非分属两层）。

**覆盖 AC**: AC-26

---

#### T-31 助手绑定数据源（**对话侧接线**）

**依赖**: T-04, T-30

> **2026-09-20 重述**：本任务原描述只写了「绑定 API + 对话链路读取」，实现期只落了前半边——`build_tools_for_assistant` / `resolve_bound_datasource` 全仓零调用方，管理员也无从写入绑定。**方案以 design.md 决策 15 为准**，下列为其可执行分解。

**改动**：
- **写入侧**：`AssistantLinkDao.update_assistant_datasource`（`database/models/assistant.py:376+`，照 `update_assistant_tool` 形状）；`AssistantUpdateReq.datasource_list`（`api/v1/schemas.py:273+`）；调用分支加进 `api/services/assistant.py:536-541` 那组；读取侧分桶 `assistant.py:307-312` 的 `elif` 链加一支，**必须在 `else: logger.error` 之前**；`AssistantInfo.datasource_list` 返回 id 列表
- **注入侧**：`AssistantAgent.init_tools`（`api/services/assistant_agent.py:254-320`）调用 `build_tools_for_assistant` 并把工具并入 `tools`——五个对话入口全部经过此处
- **锚点侧**：`AssistantCitationToolWrapper` 加数据源分支（`assistant_agent.py:51`）+ `wrap_citation_tool`（`:335`）放行 `DatasourceQueryTool`；`DatasourceQueryTool.last_outcome` 单槽位**改累积列表**（`datasource/domain/langchain/tool.py:73`）；`select_registry_items_for_persistence`（`citation/domain/services/citation_prompt_helper.py:282-294`）对 `CitationType.DATASOURCE` **豁免文本标记过滤**
- **已选择数据源的会话中，模型自主判断是否查询**；**不做强制前置路由判定**（AC-25）

**两个必须盯住的坑**：① 删除条件用 `IS NOT NULL`，**不能用 `datasource_id != 0`**（该列默认 `None`，SQL 中 `NULL != 0` 求值为 NULL，一行都删不掉 → 每次保存追加重复行）；② `elif` 链顺序，漏加会让每行数据源绑定刷一条 error 日志。

**为什么**：design 决策 15（含 A/B/C 三个桥接点的取舍）；AC-24（收窄后仅助手）/ AC-25 / AC-29 / AC-31 / AC-34。

**验证**：`uv run python -m pytest test/datasource/test_assistant_binding.py -q`，须含：未绑定不出现工具 / 绑定后出现 / **重复保存不产生重复行** / outcome→锚点 payload 字段正确 / DATASOURCE 项 survive 标记过滤且 RAG 项仍被过滤。

**覆盖 AC**: AC-24（仅助手，工作台半边见 spec 缺口登记）, AC-25, AC-29, AC-31, AC-34

**T-31 执行记录（2026-09-20 完成）**

实现计划：[plan-t31-chat-wiring.md](./plan-t31-chat-wiring.md)（9 个任务）。10 个 commit，按依赖顺序：

| # | commit | 内容 |
|---|---|---|
| 0 | `aa7a0da65` | `build_tools_for_assistant` 的 `login_user` 契约断言 |
| 1 | `c25fe1b87` + `9159b0499` | `AssistantLinkDao.update_assistant_datasource` + 测试 |
| 2 | `134235b7d` | 请求 / 响应 / 保存分支 / 详情分桶 |
| 3 | `d7ffd00b6` | 注入进 `AssistantAgent.init_tools` |
| 4 | `4962e4fa8` | `outcomes` 改累积列表 |
| 5 | `f47995098` | 锚点桥接（扩 citation wrapper） |
| 6 | `d81d9162a` | 数据源锚点豁免 + **跨轮泄漏修复** |
| — | `02a1995f1` | **citation registry 的 DATASOURCE 分支（阻断性缺陷）** |
| 7 | `01e8ea710` | AC-63 输出围栏锚定 |
| — | `fdef8aa8b` | 断言收紧（消除 vacuous 检查） |

**执行期发现、计划中未预见的两个缺陷**（均已修复并有回归测试）：

1. **跨轮锚点泄漏**（`d81d9162a`）。`AssistantAgent` 在**同一会话内跨轮复用**（`common/chat/client.py:253` 仅在切会话时重建），而 `reset_citation_registry_items` 只清了 collector 与 wrapper 的 `citation_registry_items`，**没清 `DatasourceQueryTool.outcomes`**。于是第 N 轮会重复引用第 1..N-1 轮查过的每一次查询。**该缺陷在本特性之前就存在**（无标记时 `select_registry_items_for_persistence` 返回全部），task 6 的豁免会把它从「纯数据源场景错」扩大到「所有场景错」，故两者必须同轮发布。变异验证：去掉 `inner.outcomes = []` 该测试即红。
2. **citation registry 完全不认 DATASOURCE 类型**（`02a1995f1`）。`citation_registry_service.py` 的 `_group_registry_items` 与 `_load_source_payload` 都写成「RAG 吗？否则 WEB」的二元分支，数据源项落进 WEB 分支，`WebCitationPayloadSchema.model_validate` 抛 `url Field required`。**严重性不在少一条记录**：`save_citations` 在 `common/chat/client.py:461` 被调用，位置在**答案流式发出（:477）之前**，其外层 `except Exception`（:492）会发 system 错误帧并关闭 websocket——即「绑了数据源且模型真的查了库的助手，用户看到的是报错而不是回答」。该雷在 T-31 之前是哑的（无生产者），是 task 5 造出生产者后引爆的，故属本工作流必须收口的部分。

**计划自身的缺陷（执行期由审查发现并修正，记录以免重犯）**：

- 计划里 Task 1 的断言 `str(call_args)` 恒为 `<BinaryExpression object at 0x…>`，**任何实现都无法通过**；改用编译成 SQL 后断言。
- 计划里 Task 3 的测试**打在一个真实 MySQL 上**：`langchain/tool.py` 在 import 期绑定 `DbDatasourceTableDao`，patch `...domain.models.dao.XXX` 打不到实际调用的名字。CI 或任何无中间件的机器上会直接报错。
- 计划里第二个 `AssistantInfo` 站点（`update_assistant` 的返回值）被漏掉——三处不是两处，漏掉会导致「保存后立刻返回空列表」，UI 会清掉管理员刚做的选择。
- 计划里两处 vacuous 断言（wiring 的 substring 检查、模块级计数）在执行期被收紧，并以变异验证确认可区分。

---

#### T-32 未授权数据源不可见 + 授权变更即时生效

**依赖**: T-09, T-31

**改动**：
- 用户可选来源列表按授权集合过滤；**未授权数据源不出现在列表中**
- **直接调用相关接口须被拒绝**（AC-49 的后半句 —— 不能只做列表过滤）
- 授权/取消授权后**在此后的对话轮次中按新集合生效**（AC-52）

**为什么**：AC-49 / AC-52。**列表过滤与接口鉴权是两件事** —— 只做前者等于越权可达。

**验证**：`uv run python -m pytest test/datasource/test_datasource_permission.py -q`；须有一条**绕过列表直连接口**的用例并断言被拒。

**覆盖 AC**: AC-49, AC-52

**2026-09-20 状态核实（本次未实施，记录以便下次直接开工）**：

- **(a) 列表过滤半条 —— 会话侧已有，列表侧无面**。`conversation_service.py:70-101` 的 `authorized_datasource_ids` 按 id 逐个查 `viewer`，供 `build_tools_for_assistant` 过滤模型可用工具（`assistant_agent.py:366`）。但**面向用户的「可选数据源列表」接口不存在**：唯一的列表端点 `GET /api/v1/datasource` 由 `get_tenant_admin_user` 把关且刻意不做 per-user 过滤（`datasources.py:125-134` 明写管理页立场）。`AssistantInfo.datasource_list`（`assistant.py:304-317`）返回**原始绑定 id、未过授权**——能「使用」某助手的人能看到该助手绑定但自己无权使用的数据源 id。是否算 AC-49 违反取决于口径（AC-49 的字面要求是「不出现在该用户**可选的来源列表**中」），**需在 T-40 落地前定夺**。
- **(b) 拒绝半条 —— 代码在，调用方零**。`resolve_bound_datasource`（`conversation_service.py:157-172`）专为 AC-49 后半句而写，但全仓 grep **零调用方、零测试**。当前实际拒绝来自 `can_manage` / 管理员角色校验（`datasources.py:238-245,273-290`），不是 `viewer` 关系。T-32 点名的 `test_datasource_permission.py` 里**没有「绕过列表直连接口」的用例**（其直接调用用例只覆盖 T-09 的 update 路径）。
- **(c) 授权变更即时生效 —— 真缺陷**。`conversation_service.py` 的模块 docstring 自称「AC-52 由「每轮检查而非缓存解析结果」成立」，但**实际是每会话而非每轮**：工具集在 `AssistantAgent.init_tools` 里构建一次（`assistant_agent.py:366-368`），而 agent 在**同一会话内跨轮复用**（`common/chat/client.py:253` 仅在切会话时重建，`reset_citation_registry_items()` 每轮只清引用状态、不清工具）。**后果：会话进行中撤销授权，该会话的后续轮次仍可继续查该数据源**，直到切会话/重连。与 AC-52 字面冲突，且与自身 docstring 相矛盾。修复方向：把工具集的授权过滤挪到每轮（或每轮复检已构建工具的数据源是否仍在授权集内）。



**依赖**: T-30, T-34

**改动**：
- 模型查询数据源后，回答中**明示数据来源**（哪个数据源 / 哪些表）（AC-29）
- 一轮内同时命中文档与数据源时，**一条回答内呈现两类来源并分别标注**（AC-30）
- **未查询数据源的那一轮不得展示数据库相关区域**（不占位、不展示空态）（AC-36）

**为什么**：AC-29 / AC-30 / AC-36。

**验证**：`uv run python -m pytest test/datasource/test_answer_sources.py -q`；AC-36 须有专门用例（这是最容易漏的「无则不展示」约束）。

**覆盖 AC**: AC-29, AC-30, AC-36

---

#### T-34 引用锚点与结果表持久化

**依赖**: T-23, T-30

**改动**：
- 引用锚点格式 `{datasource, tables[], sql}`（AC-31）
- 结果表快照**随回答消息持久化**，供历史重载 / 换端查看时仍可展开（AC-34）
- 持久化复用执行期行数上限（AC-35 由 T-23 保证，此处只消费）

**为什么**：AC-31 / AC-34；design §4.2 的「引用锚点」「结果表快照」两行。

**验证**：`uv run python -m pytest test/datasource/test_citation.py -q`；须有「模拟重新加载会话后锚点与结果表仍可取回」的用例。

**覆盖 AC**: AC-31, AC-34

**2026-09-20 状态核实：已完成（随 T-31 落地，无需再做）**

三条改动逐条复核，均由 T-31 的 10 个 commit 覆盖：

- **(a) 锚点格式** ✅ `DatasourceCitationPayloadSchema`（`datasource/domain/schemas/citation.py:30-42`）＝ `{datasourceId, datasourceName, tables[], statement, columns[], rows[][], truncated}`；`tables` 由 `executor.tables_from_statement` 从语句解析。与 spec 字面 `{datasource, sql}` 仅命名差异（id/name 拆分、`statement`）。由 `AssistantCitationToolWrapper` 的数据源分支自 `QueryOutcome` 构造（`assistant_agent.py:94-102`）。
- **(b) 随消息持久化** ✅ `common/chat/client.py:455-473`:`select_registry_items_for_persistence` → `save_message_citations(message_id=res.id, …)`，DATASOURCE 分组整组保留（`citation_registry_service.py:492-497`），按 `message_id` 存 `citation_type` + `source_payload`；回读走 `_load_source_payload` 的 DATASOURCE 分支 + `to_registry_item`，出口再校验（`citation_resolve_service.py:294-318`），经 `POST /api/v1/citations/resolve` 暴露。JSON 往返测试见 `test_citation_security.py:223-247`。
- **(c) 复用执行期行数上限** ✅ 上限只有一处 `MAX_ROWS = 200`（`executor.py:62`），取数时一次性施加并置 `truncated`；锚点**原样复制** `outcome.rows`，无二次裁剪（`assistant_agent.py:100`）。

**与任务书的两处偏差（均属订正而非降级）**：

1. 点名的 `test/datasource/test_citation.py` **不存在**，覆盖实际落在 `test_assistant_anchor.py` + `test_citation_security.py`。
2. 范围是**助手对话入口**，因为数据源工具目前只接在那里（`assistant_agent.py:389-396`），与 AC-24 已登记的范围收窄一致。

---

### Wave 8 — 平台管理端 UI（⚡ 可与 W6–7 并行，依赖 W4）

> **技术栈**：platform SPA（Zustand + react-query v3 + bs-ui，import 用 `@/`）。
> 参照最近似页面：`src/frontend/platform/src/pages/KnowledgePage/`。
> ⚠️ 与 W9 的 client SPA **技术栈不同、组件不可共享**（design §9 排期警告）。

**W8 执行记录（2026-09-20 完成，T-35~T-39）**

新增文件（均无 `@ts-strict-ignore`，全量 strict 通过）：

| 文件 | 行数 | 内容 |
|---|---|---|
| `pages/DatasourcePage/index.tsx` | 49 | 页面壳：列表 / 失败清单两个 Tab |
| `pages/DatasourcePage/DatasourceListTab.tsx` | 203 | T-35 列表 + T-38 授权入口 + 删除 / 启停 |
| `pages/DatasourcePage/DatasourceFormDialog.tsx` | 295 | T-35 表单（六引擎、测试连接、可写账号警告） |
| `pages/DatasourcePage/DatasourceTablesView.tsx` | 312 | T-36 表勾选与结构、T-37 重采集 |
| `pages/DatasourcePage/TableDescriptionDialog.tsx` | 91 | T-37 业务描述编辑 |
| `pages/DatasourcePage/QueryFailuresTab.tsx` | 150 | T-39 失败清单 |
| `pages/DatasourcePage/useDatasourceQueries.ts` | 63 | 列表状态（两个 Tab 共享，供失败行跳转） |
| `pages/DatasourcePage/datasourceConstants.ts` | 100 | 引擎默认端口 / 标签 / 表单校验 |
| `controllers/API/datasource.ts` | 214 | 全部 10 个后端端点的封装 |

测试（3 个文件 / 10 条，**全部经变异验证**）：`test/datasourcePage.test.tsx`、`test/datasourcePermissionDialog.test.tsx`、`test/datasourceTablesView.test.tsx`。

**入口位置（实现期决策，tasks.md 原未指定）**：**独立页面 `/datasource` + 侧边栏入口**，复用 `permission: 'sys'` 而非新增一级菜单键。理由：① 页面后端全部由 `get_tenant_admin_user` 把关（全局超管或当前租户 Child Admin），与 `isSuperAdmin || isChildAdmin` 同一人群；② 新增菜单键需改动后端 `WebMenuResource` 枚举 + `auth.py` 两个 frozenset + `init_data`、前端角色管理白名单 + 审批页 + 占位页，外加一个角色 backfill——而 `alembic/AGENTS.md §5` 禁止在迁移里做 backfill，需另起 `scripts/` CLI + 启动钩子。先例：`/model/resource`、`/approval` 同样复用既有 permission 自行渲染入口。**用户 2026-09-20 确认此方案**（备选：KnowledgePage 第三个 Tab / SystemPage 新增 Tab / 真·一级菜单键）。

**实现期发现（已处理）**：

1. **AC-46「全员可见」在码基中根本不存在** —— 权限对话框（`bs-comp/permission/`）没有任何 public/everyone 选项，是**结构性满足**而非遗漏。已加断言锁死（变异验证：往 DialogTitle 注入 `Public` 即红）。
2. **AC-47 部门语义：后端不拦，只能靠 UI 不提供** —— `grant_subject_query_service.py:198` 的 `validate_resource_grants` 对**所有**资源类型一视同仁接受 `department` 主体，直接调 API 可把数据源授权给部门。故在 `PermissionDialog` 增加按资源类型过滤 `SUBJECT_TABS_BY_RESOURCE`（仅 `db_datasource` 去掉部门），并把过滤传到 `PermissionListTab` / `PermissionGrantTab`。**KB 等其它资源类型行为不变**（已加回归断言）。⚠️ **残留风险**：绕过 UI 直连 API 仍可授给部门，需后端补拒——已登记为 spec/后端待办。
3. **AC-14 的「不可采」与「无注释」必须区分** —— 采集器对无法上报注释的方言**不写 `comment` 字段**，对有该能力但为空值者写 `""`。UI 据此区分：`undefined/null` 显示「该引擎无法自动采集注释」，`""` 显示空。已加断言（两条列各一，且断言「不可采」文案只出现一次）。
4. **测试自身的一个假绿陷阱**：Radix 把 `DialogContent` portal 到 `document.body` 之外，`render()` 返回的 `container.innerHTML` **长度为 0**。凡以 `container.textContent` 写的「不得出现 X」类断言**恒真**——AC-46 的断言初版即如此，注入 `Public` 后仍全绿，改用 `screen.getByRole('dialog')` 才成立。已存入项目记忆。

**W8 未覆盖 / 遗留**：写操作失败时的前端提示依赖 `captureAndAlertRequestErrorHoc` 的全局 toast（未逐操作定制文案）；无 Playwright 自动化（前端任务仍是手动验证口径，见「开发模式」）。

#### T-35 数据源列表与创建 / 编辑表单

**依赖**: T-16, T-17

**改动**：
- 新增 `src/frontend/platform/src/pages/DatasourcePage/`（列表 + 表单）
- 表单含引擎下拉（六种）、主机 / 端口 / 库名 / 账号 / 密码、**测试连接**按钮
- **无裸连接串（JDBC URL / DSN）输入入口**（AC-07）；**无「保存一段 SQL 作为可查询对象」能力**（AC-07）
- 写权限警告**不阻断保存**（AC-05）
- controller 走 `@/controllers/API/`，**不得直接 `import axios`**（用包装后的 request 模块，同目录既有写法）

**为什么**：AC-01 / AC-05 / AC-07 / AC-57；C7（store 不调 HTTP）。

**验证**：
```bash
cd src/frontend && pnpm typecheck && pnpm lint
```
手动验证：`pnpm start`（platform :3001）→ 建源 → 表单无连接串输入框 → 输入可写账号时出现警告但可保存。

**覆盖 AC**: AC-01, AC-05, AC-07, AC-57

---

#### T-36 表勾选与结构快照展示

**依赖**: T-35, T-18

**改动**：
- 表勾选界面（列当前账号可访问的表）
- 展示采集到的字段名 / 类型 / 库内注释
- **库内注释不可采时该限制须在界面明示，不得静默留空**（AC-14）

**为什么**：AC-09 / AC-14 / AC-57。R2 已证达梦可采，故 AC-14 的明示口径是**兜底**而非达梦专属路径。

**验证**：`pnpm typecheck && pnpm lint`；手动：勾选表 → 保存 → 字段注释正确展示；构造注释为空的情形确认界面有明示。

**覆盖 AC**: AC-09, AC-14, AC-57

---

#### T-37 表描述维护与重新采集触发

**依赖**: T-36, T-19

**改动**：
- 每张已暴露表的业务描述编辑入口（AC-11）
- 重新采集触发按钮，并在界面提示**描述不会被覆盖**（AC-12）

**为什么**：AC-11 / AC-12 / AC-57。

**验证**：`pnpm typecheck && pnpm lint`；手动：填描述 → 重新采集 → 描述仍在。

**覆盖 AC**: AC-11, AC-12, AC-57

---

#### T-38 授权管理入口

**依赖**: T-35, T-09

**改动**：
- 数据源授权管理界面（授权给指定用户 / 用户组）
- **不得出现「全员可见 / 公有」选项**（AC-46）；**不得出现部门归属语义**（AC-47）

**为什么**：AC-45 / AC-46 / AC-47 / AC-48 / AC-57。

**验证**：`pnpm typecheck && pnpm lint`；手动：授权面板**不存在**「全员可见」与部门选择项；**授权粒度到数据源级，界面上不存在表级授权入口**（AC-48 —— 「不提供」类约束必须显式验证，否则漏做了也看不出来）。

**覆盖 AC**: AC-45, AC-46, AC-47, AC-48, AC-57

---

#### T-39 失败提问清单页

**依赖**: T-29, T-35

**改动**：
- 管理员失败清单页（表 / 语句摘要 / 错误类别 / 时间），可跳转到对应表的描述维护

**为什么**：AC-42 / AC-57。

**验证**：`pnpm typecheck && pnpm lint`；手动：造一次失败 → 清单出现该条。

**覆盖 AC**: AC-42, AC-57

---

### Wave 9 — 客户端回答区 + 工作流侧 + 收尾（依赖 W7）

> **技术栈**：client SPA（Recoil + react-query v4 + shadcn，import 用 `~/`）。
> 回答区参照 `src/frontend/client/src/components/Messages/`。
> ⚠️ **与 W8 无共享组件**（design §9）。

#### T-40 客户端数据源选择

**依赖**: T-31

**改动**：
- 客户端会话中可选择数据源（仅显示已授权集合）
- 与文档知识库选择处于**同一交互层**（AC-26 在客户端的体现）

**为什么**：AC-24 / AC-49 / AC-58。

**验证**：`cd src/frontend && pnpm typecheck && pnpm lint`（client 子应用）；手动：`/workspace`（:4001）选择数据源。

**覆盖 AC**: AC-24, AC-49, AC-58

**2026-09-20 完成，口径由「选择」收窄为「展示」（用户确认）**

**为什么改口径**：数据源工具由 `AssistantAgent.init_tools` 从助手的**管理端绑定**一次性注入；而支持**逐轮选择**的链路是 workstation SSE（`use_knowledge_base` / `tools`），那条链路**根本没有数据源工具**，且其数据源半边已在 AC-24 登记为已知缺口（需另立 Feature 走 spec 评审）。即：**没有可被选择的东西**——逐轮选择器背后没有可选的工具集。

**落地**：助手应用会话（`pages/appChat/`）输入框上方一行只读来源，列出该助手绑定且**当前用户已授权**的数据源名。与 T-41 的锚点渲染合起来兑现 AC-58 的「来源可辨」：锚点说明**这一次回答**来自哪里，此行说明**可以来自哪里**。

**后端改动（原计划外）**：`AssistantInfo.datasource_list` 由 `list[int]` 改为解析后的只读模型 `DatasourceRead{id, name, description, engine}`。
- **它不是「多做的」**：同 schema 的 `tool_list` / `flow_list` / `knowledge_list` 三个兄弟**早已是解析后的模型**，平台的保存路径本就把它们 map 回 id；只有 `datasource_list` 是裸 id，是 T-31 引入的不一致。
- **裸 id 根本没法用**：能解析 id→名字的接口 `GET /api/v1/datasource` 由 `get_tenant_admin_user` 把关，**聊天用户无权调用**。留在客户端解析是死路。
- 解析与授权过滤（T-32）放在同一处，两者不会各自漂移。
- 两个 `AssistantInfo` 站点都已更新——**包括 `update_assistant` 的返回值**（T-31 执行记录里那个「漏掉过一次」的站点）。

**实现期发现**：

1. **`DatasourceRead` 必须是窄 schema，不能复用 ORM 行**：`DbDatasource.password` 存 Fernet 密文，INV-19 ① 禁止任何读取路径回显。窄 schema 让这件事变成**结构性**的而非「记得别写错」。变异验证：改成返回 ORM 行 → 6 条用例转红。
2. **三条既有测试被引入「依赖真实 MySQL」**：写入路径解析会先调权限服务（`edit_app`），而这三个测试只 patch 了 DAO、没 patch 权限服务。**与 T-31 计划里那个「测试打在真实 MySQL 上」是同一类错误**，这次由套件直接报错暴露。已补 stub，耗时从 24 秒超时降到 0.2 秒。
3. **客户端 Recoil 冻结**（root AGENTS.md §4 / ledger #5）：新组件不 import recoil，改由父组件（本就持有会话状态）派生后以 props 传入。

**验证**：`test/datasource` 409 passed；client `pnpm typecheck` 6 个既有错误不变、`pnpm lint` 5 个既有错误不变、jest 323 passed（同样的 6 条既有失败）。AC-60：无绑定的助手序列化仍为 `[]`（用例如在）。

---

#### T-41 客户端回答区：来源标注 + 引用锚点 + 展开与下载

**依赖**: T-33, T-34

**改动**：
- 回答中的来源标注（文档 / 数据库分别标注）
- 引用锚点展示（表名 + 语句）
- 语句与结果表**可展开，默认收起**（AC-32）
- 结果表**支持下载**（AC-33）
- 历史会话重载后仍可展开（AC-34 的客户端侧消费）

**为什么**：AC-30 / AC-31 / AC-32 / AC-33 / AC-58。

**验证**：`pnpm typecheck && pnpm lint`；手动：提问触达数据源 → 展开区默认收起 → 展开看到语句与结果表 → 下载 → 刷新页面后仍可展开。

**覆盖 AC**: AC-30, AC-31, AC-32, AC-33, AC-34, AC-58, AC-59

**2026-09-20 完成**（两个 commit：`d8fa3efbe` 识别 + `f1e8f8bb8` 面板与下载）

**核心缺陷是「误分类」而非「缺失功能」**：客户端引用体系只认两类（文档 / 网页），`normalizeCitationType` 把**非 web 一律归为 'rag'**。于是数据源锚点（后端 resolve 接口本来就返回、且已校验）走**文档路径**——badge 显示「文档」、展开器给文档预览，**语句 / 表名 / 结果行无处可渲染**。`citationUtils` 里有一行注释承认这个非 web 默认行为，只是 F061 之前从没有数据源类型流到那里。

**加上了第三类**：`isDatasourceCitation` / `getCitationDataSourceKind`、`getDatasourceAnchor`（含畸形 payload 降级——这是历史重载读回的持久化数据）、`buildDatasourceCsv`、图标与 badge 区分。`bisheng-icons` 全表无数据库字形，按客户端规则回落到 lucide 的 `Database`。

**两个「错了才看得见」的点**：

1. **CSV 的 UTF-8 BOM**。没有它，zh-CN Windows 的 Excel 按 GBK 解码，客户结果表里所有非 ASCII 单元格变乱码。**其他任何查看器都看不出差别**——回归了也毫无征兆，直到客户打开文件。断言因此**读原始字节**（`FileReader.readAsText` 会吃掉 BOM，`Blob.text()` 和 `TextDecoder` 在本项目 jsdom 里都不存在）。
2. **公式注入**。文件内容来自**客户数据库**，单元格天然是不可信输入；开头的 `=`/`+`/`-`/`@` 加单引号前缀，否则 Excel 打开即执行。
3. **结果表列宽 = max(表头, 各行)**，与 `buildDatasourceCsv` 一致；两边算法不同会让下载文件与屏幕显示不一致。NULL 渲染为破折号（空单元格与 `""` 无法区分）。

**AC-36（不查询则完全不渲染）**断言了两次（null 与文档引用各一条）。这是最容易退化的一条：「渲染空盒子」看起来像个刻意设计的状态，且空展开器会告诉用户「查过数据库但没结果」——一个**不同且错误**的陈述。

**实现期我自己的失误（记录以免重犯）**：把 BOM **字面量**写进源码，文件对 git/grep/eslint 变成**二进制**；同一个错误还把 `\x00`–`\x1f` 字面控制字符写进了正则字符类。现已全部改为码点构造，控制字符范围整个删除。

**验证**：client `pnpm typecheck` 6 个既有错误不变、`pnpm lint` 5 个既有错误不变（且 `CitationReferencesDrawer` 比改动前**少 2 条**）、jest 323 passed / 同样的 6 条既有失败。25 条新测试**全部经变异验证**（BOM、AC-32 默认收起、AC-36 不渲染、AC-30 豁免、列宽 —— 各自回退都让特定用例转红）。

**未做（不在本任务范围）**：工作台（workstation）链路的锚点渲染——那条链路没有数据源工具（见 AC-24 缺口登记）。`components/Chat/Messages/Content/` 下的渲染是两条链路共用的，故一旦缺口补上即可复用。

---

#### T-42 工作流 Agent 节点收敛 + 兼容读取

**依赖**: T-13, T-30

**改动**：
- Agent 节点由「内联填写连接信息」改为「**选择一个已建数据源**」（AC-53）
- **兼容读取**既有 `node_params["sql_agent"]` 内联配置，既有流程继续正常运行（AC-54）
- **关闭内联新建入口**，内联配置字段只读（AC-56）
- 既有 `SqlAgentTool.arun` 的同步阻塞一并修正为 async（R4）
- **不新增独立的数据源检索工作流节点**（AC-55）
- 既有 `_init_sql_address` 改为委托 T-13 的新实现（保持对外行为不变）

**为什么**：design 决策 3 / 决策 14；AC-53～AC-56；R4 的处置口径。

**验证**：`uv run python -m pytest test/datasource/test_workflow_compat.py -q`；**回归用例**：既有内联配置的流程输入输出不变（按 R5 的口径，以对外行为为准，不以代码路径是否复用为准）。

**覆盖 AC**: AC-53, AC-54, AC-55, AC-56

---

#### T-43 对话合规约束复用

**依赖**: T-33

**改动**：
- 数据库问答的回答文本**经过既有对话合规链路**（F036/F059 敏感词关卡），不绕过
- 留痕口径不受本特性影响

**为什么**：AC-63；INV-19 ⑤；design §4.3「F036 敏感词关卡 — 复用」。

**验证**：`uv run python -m pytest test/datasource/test_compliance.py -q`；断言数据库来源的回答文本进入了合规检测路径。

**覆盖 AC**: AC-63

---

#### T-44 零回归与不变量验证

**依赖**: T-31, T-42

**改动**：
- 验证「租户未创建任何数据源时全部既有行为与发布前一致」（AC-60）
- 验证数据源**未进入知识库类型枚举 / 未产生向量 / 未建集合或索引**（AC-61）
- 验证**未修改知识库对象、类型枚举、检索链路与授权形状**（AC-64）
- 验证数据源删除后对后续对话立即不可用，但**历史回答 / 锚点 / 执行记录保持可读、不级联删除**（AC-62）

**为什么**：AC-60～AC-64。这几条是「不做破坏」类断言，**容易被默认成「显然成立」而完全不测** —— F058 曾因缺同类测试留下缺口。

**验证**：
```bash
uv run python -m pytest test/datasource/test_invariants.py -q
```
AC-61 / AC-64 建议用静态断言（扫描 `KnowledgeTypeEnum` 无数据源成员）+ 行为断言各一条。
**AC-51** 亦在此处以静态断言覆盖：扫描执行路径确认**不存在**行级条件注入（该 AC 规定平台「不做」某事，行为测试无法证明否定命题）。

**覆盖 AC**: AC-51, AC-60, AC-61, AC-62, AC-64

---

#### T-45 端到端验证

**依赖**: 全部

**改动**：
- 新增 `test/e2e/test_e2e_f061_datasource_qa.py`
- 覆盖：建源 → 采集 → 描述 → 对话触达 → 引用锚点 → 失败重试 → 失败清单 → 授权隔离

**为什么**：SDD 流程步骤 7（强制）；058 的教训是「租户隔离、事务提交、配置解析三类边界 mock 掉 DAO 等于不测」，故 e2e 须走**真栈 + 真实数据形态**。

**验证**：`uv run python -m pytest test/e2e/test_e2e_f061_datasource_qa.py -q`（需中间件，CI 跑）。

**覆盖 AC**: 全量回扫（见下「AC 追溯矩阵」）

---

## AC 追溯矩阵

> **验收线：66 条 AC 必须全部出现在下表。** F058 曾声称「46 条 AC 全覆盖」，经 code review 核实**不属实**（7 条无测试），故本表是拆解后必须自检的第一项。

| AC | 内容摘要 | 任务 |
|---|---|---|
| AC-01 | 创建数据源采集连接信息 + 测试连接 | T-16, T-35 |
| AC-02 | 支持六种引擎（含达梦 DM8） | T-13, T-14 |
| AC-03 | 测试连接返回可判定结果，失败零落库 | T-16 |
| AC-04 | 凭据加密，任何读取不回显密码 | T-02, T-15 |
| AC-05 | 可写账号给出警告但不阻断保存 | T-35 |
| AC-06 | 拒绝 SQLite | T-13 |
| AC-07 | 无「保存 SQL 作为可查询对象」、无裸连接串入口 | T-35 |
| AC-08 | 修改连接信息 / 表清单立即生效 | T-16, T-18 |
| AC-09 | 列出可访问表供勾选 | T-17, T-36 |
| AC-10 | 采集并持久化表名 / 字段 / 类型 / 库内注释快照 | T-18 |
| AC-11 | 每张暴露表可维护业务描述 | T-19, T-37 |
| AC-12 | 重采集刷新快照但不覆盖业务描述 | T-19, T-37 |
| AC-13 | 不读取表内真实数据行 | T-17 |
| AC-14 | 注释不可采时建源仍成功 + 界面明示 | T-18, T-36 |
| AC-15 | 无定时轮询重采集 | T-20 |
| AC-16 | 仅放行单条 SELECT，多语句 / 非查询拒绝 | T-10, T-12 |
| AC-17 | 拒绝写操作 / 结构变更 / 授权变更 / 加锁读取 | T-10, T-11, T-12 |
| AC-18 | 行数上限与超时，超限截断并标注 | T-23 |
| AC-19 | 只读会话执行，不支持则降级并记录 | T-22 |
| AC-20 | 解析失败退窄校验，不因解析失败拒绝执行 | T-10, T-11, T-12 |
| AC-21 | 查询不阻塞对话主链路 | T-21 |
| AC-22 | 留存执行记录（操作人 / 租户 / 数据源 / 表 / 语句 / 状态 / 时间） | T-02, T-05, T-24 |
| AC-23 | 执行记录不含结果集数据 | T-02, T-05, T-24 |
| AC-24 | 数据源可配置为助手的可用来源（工作台半边已收窄，见 spec 缺口登记） | T-31, T-40 |
| AC-25 | 模型自主判断是否查询，无强制前置路由 | T-27, T-31 |
| AC-26 | 以工具形态与知识库检索同层 | T-27, T-30 |
| AC-27 | SQL 生成用独立模型槽位，未配置则回退不报错 | T-25 |
| AC-28 | 表描述超限退化检索，两形态可区分观测 | T-26 |
| AC-29 | 回答明示数据来源 | T-31（锚点落库）, T-33 |
| AC-30 | 一轮内两类来源并存并分别标注 | T-33, T-41 |
| AC-31 | 引用锚点含表名与本次语句 | T-31（构造 payload）, T-34, T-41 |
| AC-32 | 展开展示语句与结果表，默认收起 | T-41 |
| AC-33 | 结果表支持下载 | T-41 |
| AC-34 | 历史重载 / 换端仍可展开 | T-31（落库不丢）, T-34, T-41 |
| AC-35 | 结果表落库复用执行期行数上限 | T-05, T-23 |
| AC-36 | 未查询数据源时不展示相关区域 | T-33 |
| AC-37 | 失败回喂模型允许修正重试 | T-28 |
| AC-38 | 重试次数上限 3 轮 | T-28 |
| AC-39 | 重试仍失败返回可理解说明，不暴露内部信息 | T-01, T-28 |
| AC-40 | 失败不中断同轮其他能力 | T-28 |
| AC-41 | 失败留档仅存语句与错误类别摘要 | T-02, T-05, T-29 |
| AC-42 | 管理员可查看失败清单 | T-29, T-39 |
| AC-43 | 数据源作为新受控资源类型，与知识库同级 | T-06, T-07 |
| AC-44 | 数据源有独立权限点 | T-08 |
| AC-45 | 默认仅创建者可见，支持显式授权 | T-09, T-38 |
| AC-46 | 不提供全员可见 / 公有选项 | T-09, T-38 |
| AC-47 | 不参与跨租户共享，无部门归属语义 | T-38 |
| AC-48 | 授权粒度到数据源级，无表级授权 | T-38 |
| AC-49 | 未授权不出现于来源列表 + 直连接口被拒 | T-32, T-40 |
| AC-50 | 按租户隔离 | T-03, T-04 |
| AC-51 | 平台不施加行级过滤（显式接受的风险） | **刻意不测**（见下），静态断言归 T-44 |
| AC-52 | 授权变更在此后对话轮次生效 | T-32 |
| AC-53 | Agent 节点改为选择已建数据源 | T-42 |
| AC-54 | 既有内联配置兼容读取，流程不中断 | T-42 |
| AC-55 | 不新增独立数据源检索节点 | T-42 |
| AC-56 | 不允许经内联新建连接 | T-42 |
| AC-57 | 平台侧提供完整管理能力 | T-35, T-36, T-37, T-38, T-39 |
| AC-58 | 客户端提供选择 / 标注 / 锚点 / 展开下载 | T-04, T-40, T-41 |
| AC-59 | 平台侧与客户端能力等价 | T-41 |
| AC-60 | 未建数据源时既有行为完全一致 | T-44 |
| AC-61 | 不进知识库类型枚举、不产生向量 | T-44 |
| AC-62 | 删除后立即不可用，历史保持可读 | T-44 |
| AC-63 | 回答文本遵守同一套对话合规约束 | T-43（行为验证，待做）, T-31（结构性锚定已完成 `01e8ea710`） |
| AC-64 | 不修改知识库任何既有行为 | T-44 |
| AC-65 | 执行记录与失败留档不存结果集 / 提问原文 | T-24, T-29 |
| AC-66 | 创建与变更统一经权限入口 | T-08, T-09 |

### 刻意不测的 AC（非缺口，需在 code review 时确认口径）

- **AC-51**（平台不施加行级过滤）是一条**否定性/声险性** AC —— 它规定平台**不做**某件事。无法用「断言不存在」的行为测试有意义地覆盖（任何测试都无法证明过滤逻辑不存在）。它在 release-contract INV-19 ④ 已显式登记为**经确认接受的风险**，缓解手段是授权治理而非技术强制。**建议**：以「代码中不存在行级条件注入」的静态断言覆盖，不写行为测试。

---

## 实际偏差记录

> 实现期发现的与 design/spec 的偏差记在此处，并回写对应文档（`docs/SDD-Guide.md` §7）。

### 文档层遗留（非本特性实现偏差，但涉及同一批改动）

1. **F058 迁移文件名仍带 v2.8.0 前缀**：`src/backend/bisheng/core/database/alembic/versions/v2_8_0_f058_doc_extract.py`。
   F058 已由 v2.8.0 移入 v2.7.0，但该文件名未随之重命名。**迁移链本身不受影响**——其 `revision = "f058_doc_extract"`、`down_revision = "f059_hit_log_source"`，与其他迁移的引用关系不依赖文件名。
   是否重命名待用户决定（重命名会与既有 git 历史冲突；不重命名则保留一处易误导的命名）。**在决定前，任何按文件名推断版本归属的做法都会出错。**
2. **F058 design.md §2 的 C5 落点仍写「错误码模块 114」**（`features/v2.7.0/058-doc-extract-template/design.md:35`），而实际与契约均为 **116**。该文件的变更历史（`:487`）已记录 114→116 的改判，但正文未同步。属既有文档漂移，非本次搬迁引入。
