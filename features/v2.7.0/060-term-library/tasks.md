# Tasks: 术语库（租户术语词条 + 运行时对话注入）

**关联规格**: [spec.md](./spec.md)（32 条 AC）
**设计**: [design.md](./design.md)（7 条决策 + 11 条坑）
**版本**: v2.7.0（**2026-09-21 由 v2.8.0 移入**）
**所属 Feature**: F060-term-library

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-21 用户确认。`/sdd-review spec` 14 项检查 13 项通过；唯一未通过项（权限口径缺 AC）已补为 AC-16, AC-17, AC-18, AC-19，另 1 medium（§4 How 泄漏）+ 2 low 已处理/留待 design |
| design.md | ✅ 已评审 | 2026-09-21 `/sdd-review design` 24 项：**5 条 findings 全部已修**（3 high：§7 测试与可观测 / §8 后续改进 / Constitution Check 三处整块缺失；1 medium：无可操作验证路径；1 medium：「租户白名单」表述不准确）。**Constitution Check PASS**（C1–C7 逐条结论见 design §2） |
| tasks.md | ✅ 已拆解 | 2026-09-21 拆解完成：**20 个任务 / 7 个 Wave**，32 条 AC 全覆盖（追溯矩阵见文末）。`/sdd-review tasks` 21 项检查：**2 条已修**（T-04 补回滚说明；AC 范围写法归一为逐条列举） |
| 实现 | 🟡 **T-01 ~ T-18 完成** | 2026-09-21。**T-19 / T-20 见下**。逐任务偏差记录见文末「实际偏差记录」 |
| e2e-test | 🟡 **已编写，未跑通** | `test/e2e/test_e2e_f060_term_library.py` 已写并 lint 通过；**本机 admin 口令未知（`admin123` / `Bisheng@top1` 均返回 10600），一次都没在真后端上跑过**。人工清单：[e2e-checklist.md](./e2e-checklist.md) |
| code-review | 🔲 未开始 | — |

### 任务完成明细

| Wave | 任务 | 状态 | 主要产出 |
|---|---|---|---|
| 0 | T-01 错误码 115 | ✅ | `common/errcode/term_library.py`（先前已提交） |
| 0 | T-02 词条表 ORM | ✅ | `domain/models/term.py`（+ `MAX_STANDARD_TERM_LENGTH`） |
| 0 | T-03 租户过滤登记 | ✅ | 先前已提交 |
| 0 | T-04 迁移 | ✅ | 先前已提交 |
| 0 | T-05 共享解析抽取 | ✅ | 先前已提交；**T-09 未再改动该文件** |
| 1 | T-06 `build_term_block` | ✅ | 先前已提交 |
| 1 | T-07 降级 + callable 契约 | ✅ | 先前已提交 |
| 1 | **T-08 词条 CRUD 与校验** | ✅ | 先前已提交 |
| 1 | **T-09 批量导入** | ✅ | `domain/services/term_importer.py` + `test_term_importer.py`（26 用例 / 8 变异） |
| 2 | **T-10 术语库 API 与门控** | ✅ | `api/endpoints/terms.py` + `api/router.py` + 注册（8 用例 / 4 变异） |
| 2 | **T-11 错误码三语** | ✅ | `packages/locales` 11501~11506 三语 + 生成产物；`pnpm check-i18n` **OK** |
| 3 | **T-12/13/14 三处注入落点** | ✅ | `domain/services/prompt.py`（新）+ 3 文件 5 个调用点；`test_injection_sites.py`（23 用例 / 变异覆盖 5 个落点） |
| 4 | **T-15 不变量与合规** | ✅ | `test_invariants.py`（12 用例 / 6 变异） |
| 5 | **T-16/17/18 平台管理页** | ✅ | `TermLibraryTab.tsx`（新）+ `controllers/API/termLibrary.ts`（新）+ 第三个 Tab；typecheck 全绿 |
| 6 | T-19 e2e | 🟡 | 已写 **未跑**（缺口令） |
| 6 | T-20 文档回写 | ✅ | design §9 定稿 + §10 偏差；本文件 |

---

## 契约登记状态（已完成，非任务）

> 本特性的版本契约登记已先行完成（2026-09-17），记录于此以免重复登记：

| 登记项 | 位置 | 内容 |
|---|---|---|
| 领域对象（2 行） | `../release-contract.md` 表 1 | `term_library_term`（租户/标准词/别名集/释义/归属部门）；「对话提示词的术语注入」（运行时行为，**不落库**） |
| 不变量 | `../release-contract.md` 表 2 | **INV-16**（按租户隔离）、**INV-17**（部门归属不是授权边界、不限定注入范围）、**INV-18**（注入是运行时只读行为） |
| Feature 依赖图 | `../release-contract.md` 表 3 | F056（钩子模式）+ F059（自动机 / 缓存 / 解析器）+ F057（部门子树展开） |
| 错误码模块 | `../release-contract.md` 错误码表 | **115**（`common/errcode/term_library.py`） |
| **契约订正** | ✅ **已完成**（2026-09-21，设计期） | 两处过期前提已回写：① F059 解析器**已落盘**（原写「尚未落盘，F060 自建」）；② 「复用 `guardrail.py` 经 `dispatch_client` 注入」**不是提示词注入点**（那是前后过滤器） |

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**后端 Test-First（务实版）**：
- 理想流程：先写测试（红），再写实现（绿）
- 测试基础薄弱时，首个测试任务一并搭 pytest 基础设施
- 中间件 / e2e 测试在 **CI** 跑，不依赖本地（见 `docs/SDD-Guide.md` §6）

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（`docs/SDD-Guide.md` §6，🚧 规划中）—— 落地后前端任务改写自动化断言。

**自包含任务**：每个任务内联文件、逻辑、AC 覆盖，实现期不必回读 spec。但**设计论证（为什么这么做）指向 design §X，不复制**（避免第三处漂移）。

**⚠ 本特性的三条实现铁律**（出自 design §5，每个相关任务都会再点一次）：
1. **注入路径不得读 `department_id`**（INV-17，design §2.1）——F057 的方向与本特性相反，不可类推。
2. **注入内容不得写入任何持久化配置**（INV-18）——不改 `assistant.prompt` / 应用配置 / 工作流提示词。
3. **新表光有 `tenant_id` 列不够**，必须登记 `_TENANT_AWARE_MODEL_MODULES`（design §2 C3 / 坑 10），否则租户过滤**静默失效**。

---

## Tasks

> **任务格式**：`**依赖**` / `**改动**`（文件路径）/ `**为什么**`（指向 design §X，**不复制论证**）/ `**验证**`（真实可跑的命令）/ `**覆盖 AC**`。
> **自包含**：实现期不必回读 spec；但设计论证一律指向 design，避免第三处漂移。

### Wave 0 — 基础设施（无测试配对，全部并行）

#### T-01 错误码 115 模块

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/common/errcode/term_library.py`：`Code = 115xx` 系列，覆盖契约登记的六类——**11501** 词条不存在或无权限访问 / **11502** 词条定义非法（标准词为空 / 释义为空 / 释义超长 / 别名非法）/ **11503** 词条重复（标准词或别名与同租户既有冲突）/ **11504** 库规模超限 / **11505** 导入文件无法解析（空文件 / 格式不支持 / 无有效词条）/ **11506** 归属部门不存在或已归档
- 继承 `BaseErrorCode`（同 `common/errcode/sensitive_word.py` 的写法）

**为什么**：design §6 对外契约；模块号 115 已在契约登记（本文件开头「契约登记状态」）。

**验证**：`uv run python -c "from bisheng.common.errcode.term_library import *"` 无 ImportError；错误码取值与契约错误码表逐条一致。

**覆盖 AC**: AC-02, AC-03, AC-04, AC-06, AC-09

---

#### T-02 词条表 ORM 模型

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/term_library/domain/models/term.py`：表 `term_library_term`，字段严格按契约表 1 登记——`id` / `tenant_id` / `standard_term` / `aliases` / `definition` / `department_id`（可空）/ `create_user_id` / `create_time` / `update_time`
- `aliases` 用 `dialect_helpers.JsonType`（JSON 数组）、`definition` 用 `LargeText` —— **双 DB 约束 C2**（design §2）
- `department_id` 的注释须写明**它是展示元数据、不参与鉴权与注入范围**（INV-17）

**为什么**：design §4.2 字段约定 + release-contract 表 1（Owner 声明）。

**验证**：`uv run python -c "from bisheng.term_library.domain.models.term import TermLibraryTerm"` 通过；`uv run python -m pytest test/database/test_alembic_single_head.py -q` 仍单 head。

**覆盖 AC**: AC-01, AC-15

---

#### T-03 租户过滤登记（**易漏，与 F061 T-03 同构**）

**依赖**: T-02

**改动**：
- 在 `src/backend/bisheng/core/database/tenant_filter.py` 的 `_TENANT_AWARE_MODEL_MODULES` 追加 `bisheng.term_library.domain.models.term`

**为什么**：design §2 约束 C3 + §5 坑 10。

> **⚠ 这是本特性最容易漏、且后果最隐蔽的一步**：租户过滤是**启动时按 `SQLModel.metadata` 自动发现**的（`_discover_tenant_aware_tables` 只认「有 `tenant_id` 列」），而 `metadata` 只有在模型模块**被 import 过**之后才填得满。这个清单就是「保证 import」用的。
> **漏了的后果**：表能读写、接口正常、**测试全绿**——只是**没有租户过滤**，A 租户的术语在 B 租户可见/可注入。**不报任何错**。F061 把这条单列为一个任务并标注「易漏，坑 1」，既有先例是 `knowledgefile` / `flowversion` 曾因此把子租户资源写进 root。

**验证**：新增 `test/term_library/test_tenant_registration.py`，**断言模块名在 `_TENANT_AWARE_MODEL_MODULES` 中**（用字符串常量断言，不读代码——该列表是字符串，重构容易切断）；另加一条行为断言：在租户 A 建词条，切租户 B 查询**查不到**。

**覆盖 AC**: AC-05

---

#### T-04 迁移：`term_library_term`

**依赖**: T-02

**改动**：
- 新增 Alembic 迁移（`bisheng/core/database/alembic/versions/`）：建 `term_library_term`，含 `tenant_id` 索引
- **遵守 `alembic/AGENTS.md`**：`down_revision` 取 `uv run alembic heads` 的实际单 head（**不按文件名前缀猜**）；DDL 用 `alembic_helpers` 的存在性守卫；**迁移内不做任何 backfill**（§5 禁止）
- 表名以 v2.7.0 的既有前缀风格命名（`v2_7_0_f060_term_library`）

**为什么**：design §4.2；契约表 1 的 Owner 声明。

**回滚**：`downgrade()` 写 `op.drop_table('term_library_term')`（及其索引）。**可逆且无数据损失顾虑**——本特性是新表、无外部引用、无 FK 从别表指进来（术语被删除只影响后续注入，历史不受影响，见 spec §3）。**注意**：`drop_table` 用 `alembic_helpers` 的存在性守卫，避免半途失败后重跑报错。

**验证**：`uv run alembic heads` 打印**恰好一个** revision；`uv run python -m pytest test/database/test_alembic_single_head.py -q` 通过。

**覆盖 AC**: AC-01, AC-05

---

#### T-05 共享解析函数抽取（**改 F059 已发布文件**）

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/sensitive_word/domain/services/tabular_import.py`：抽出 **「读成行」这一层**——扩展名白名单 / 编码回退（utf-8-sig → gbk）/ NUL 检测 / xlsx 读取 / 表头识别 → 返回 `rows: list[list[str]]`。**无 DB、无服务状态、不含列语义**
- 修改 `platform_word_service.py`：`_read_text` / `_read_xlsx` / `_looks_like_header` 改为**委派**新函数，`parse_words_file` 的**公开签名与返回值类型不变**
- **不改变 F059 的对外行为与错误码**（11301 仍是 F059 的），**不改变它「每格一个词」的列语义**
- **⚠ 共享边界**（design 决策 8）：术语库是 `标准词, 别名, 释义` **三列**，敏感词库是「每格一个词」——**列语义不共享**，只共享编码与容器处理。「共用同一套解析器」指的是这一层，不是整个 `parse_words_file`

**为什么**：design §3 决策 7。契约规定「若 F059 先落地解析器，F060 应改为调用而非另写」，而 F059 **确已落盘**。抽共享而非直调 `PlatformWordService`：那是**平台/超管**服务，租户级功能反向依赖它会让依赖方向出错，且 11301 会漏进租户流程。

**验证**：`uv run python -m pytest test/sensitive_word/ -q` **全绿**（这是本任务的主要护栏——改的是已发布文件，既有无回归是硬要求）；另断言 `parse_words_file` 的签名未变。

**覆盖 AC**: AC-11

---

### Wave 1 — 领域服务（依赖 W0）

#### T-06 `build_term_block` 纯函数（**本特性的核心**）

**依赖**: T-01, T-02

**改动**：
- 新增 `src/backend/bisheng/term_library/domain/services/injection.py`：`build_term_block(tenant_id, user_query) -> str`
- 实现 design §4.1 的四步：① 取本租户全库（走 DAO，带租户过滤）② 构建/命中 AC 自动机（内容寻址缓存）③ 匹配 → **归并别名** → **定序** → 按字符数封顶 → 拼区块 ④ 未命中返回 `""`
- **定序口径**（spec AC-23）：按术语在提问中**首次出现位置**升序；位置相同按标准词**长度降序**；仍相同按标准词**升序**（保证全序）
- **封顶**：按字符数封顶，超出时按上述顺序截断并**在区块内说明还剩几条未展开**（spec AC-23：不静默丢）。上限作为**模块级可调常量**（初值见 design §9 待定项 2）
- **大小写**：`ACAutomaton` **不做 lower，由调用方负责**（design 坑 11）——按本特性的口径处理（design §9 待定项 3）
- **缓存**：照搬 `sensitive_word_policy_service` 的**内容寻址**模式（`sha256(词表)` 作 key，变更时 push 失效）。**自建缓存，不复用 F059 的实例**（design 决策 6）
- **⚠ 缓存键不含轮次计数**：助手侧的 callable 在**每次模型调用**时执行（design 决策 5 的代价段），同轮多次调用必须命中同一份结果，否则 ReAct 多步之间注入内容会**变**
- **⚠ 不得读 `department_id`**（INV-17 / design §2.1）

**为什么**：design §3 决策 5/6、§4.1 数据流、§4.3 模块职责。

**验证**：`uv run python -m pytest test/term_library/test_injection.py -q`。**须覆盖**：命中 / 未命中返回空串 / 别名归并到标准词 / 同位置不同长度的定序 / 超上限截断且说明剩余数 / 库为空。**全部用例须变异验证**（改坏实现应转红）。

**覆盖 AC**: AC-20, AC-21, AC-22, AC-23, AC-28

---

#### T-07 注入失败降级 + 自动机复用验证

**依赖**: T-06

**改动**：
- `build_term_block` **任何异常**（DB 不可用 / 自动机构建失败 / 缓存异常）SHALL 记 `warning` 并**返回空串继续对话**——术语库是增强，不是对话的前置条件（design §7.4）
- 加一条测试**钉住 langgraph 的 callable prompt 形态**：构造 `prompt=<callable>` 的 agent 并断言 callable 被调用（design §6 表下注——`prompt` 接受 callable 是**框架公开签名**，非本项目代码，升级可能移除）

**为什么**：design §7.4 可观测与降级；§6 框架依赖风险。

**验证**：`uv run python -m pytest test/term_library/test_injection.py -q`；降级用例通过 monkeypatch 让 DAO 抛异常，断言**返回 `""` 且不抛**。

**覆盖 AC**: AC-28, AC-32

---

#### T-08 词条 CRUD 与校验

**依赖**: T-01, T-02, T-03

**改动**：
- 新增 `src/backend/bisheng/term_library/domain/models/dao.py`：DAO（**每个读方法强制租户过滤**，同 `datasource` 模块的写法）
- 新增 `src/backend/bisheng/term_library/domain/services/term_service.py`：创建 / 更新 / 删除 / 列表（**部门筛选 + 分页**）
- 校验（spec §2.1）：标准词非空且**租户内唯一**（AC-02）；别名**不与同租户任何标准词或别名冲突**（AC-03）；释义非空且 **≤200 字**（AC-04）；总数 **≤500**（AC-06）
- **部门筛选**用 `DepartmentDao.aget_subtree_ids`（含子部门），且 `department_id` 为 `None` 时**不过滤**、指定未知部门时返回**空集而非全集**

**为什么**：design §4.1 / §4.3；AC-13 的「未知部门返回空而非全集」是既有 `_resolve_department_filter` 已定的口径（防止把筛选取空变成范围放大）。

**验证**：`uv run python -m pytest test/term_library/test_term_service.py -q`。**DAO 打桩、service 为被测对象**；须覆盖边界：空标准词 / 空释义 / 释义 201 字 / 第 501 条 / 标准词重复 / 别名撞既有标准词 / 别名撞既有别名 / 未知部门筛选。

**覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-06, AC-13, AC-14, AC-15

---

#### T-09 批量导入（预览 + 覆盖/追加）

**依赖**: T-05, T-08

**改动**：
- 新增 `src/backend/bisheng/term_library/domain/services/term_importer.py`：调用 T-05 的共享解析函数，执行 **F060 侧校验**并抛 **11505/11502/11503/11504**（**不抛 F059 的 11301**）
- **列语义**（design 决策 8）：`标准词, 别名, 释义` 三列，表头行可省略；别名列按分隔符拆成多个（可空）；**释义必填**。**不支持 txt**（纯词表装不下释义）
- 两种模式：**覆盖**（清空后写入）/ **追加**（并入）；覆盖的返回须带**破坏性提示所需的信息**
- **预览**：落库前返回「可导入条数 / 重复数 / 超长丢弃数」（AC-07）
- 无法解析的文件**不产生任何落库**（AC-09）

**为什么**：design §3 决策 7、§4.3 模块职责。

**验证**：`uv run python -m pytest test/term_library/test_term_importer.py -q`。须覆盖：csv/xlsx 各一（**含带表头与不带表头**）/ 空文件 / **txt 被明确拒绝** / 释义列缺失或为空 / 别名列可空 / 与既有词条冲突 / 第 501 条被拒 / 覆盖模式的破坏性。

**另须承接 T-05 挪来的两条跨侧断言**（T-05 实现期发现它需要两个 importer 都存在，故落在本任务）：
- **「同一份文件在两个 importer 里要么都能读、要么都读不了」**——用同一组坏输入（空文件 / GBK 损坏 / 无 BOM 的 UTF-16 / 损坏 xlsx）分别喂 F059 与 F060，断言 verdict 相同（**错误码不同是对的**：11301 vs 11505）。
- **F060 确实走共享读取层**——patch `tabular_import.read_rows` 并断言 term importer 观察到它（防两侧重新各自实现）。

**覆盖 AC**: AC-07, AC-08, AC-09, AC-10

---

### Wave 2 — API（依赖 W1）

#### T-10 术语库 API 与门控

**依赖**: T-08, T-09

**改动**：
- 新增 `src/backend/bisheng/term_library/api/endpoints/terms.py` + `router.py`，并在 `bisheng/api/router.py` 注册
- 端点：`GET /api/v1/term-library`（列表 + 部门筛选 + 分页）/ `POST`（创建）/ `PUT /{id}`（更新）/ `DELETE /{id}` / `POST /import/preview` / `POST /import`
- **门控**（spec AC-16, AC-17, AC-18）：**读** `get_login_user` + `assert_effective_web_menu_contains(..., 'knowledge')`；**写**（POST/PUT/DELETE/import 两个）另加 `create_knowledge`
- **不调用 `PermissionService`**（INV-17 / 决策 2）；**不新增 FGA 资源类型**

**为什么**：design §3 决策 2、§4.1 数据流、§6 Outgoing。门控口径与知识库一致，是本特性权限设计的全部内容。

**验证**：`uv run python -m pytest test/term_library/test_term_api.py -q`。**须有「无权限被拒」的用例**（缺 `knowledge` 读被拒 / 缺 `create_knowledge` 写被拒）——spec AC-17 明确要求「后端拒绝，不依赖前端隐藏入口」。**不要跨模块 import**（RULE-5）。

**覆盖 AC**: AC-16, AC-17, AC-18

---

#### T-11 错误码三语（前端）

**依赖**: T-01

**改动**：
- 在 `src/frontend/packages/locales/src/api_errors/{zh-Hans,en,ja}.json` 追加 **11501~11506** 三语
- 运行生成脚本刷新 `platform/public/locales/*/api_errors.json`（**生成产物不得手改**）

**为什么**：仓库硬规则——新错误码必须三语同时落地，否则 `pnpm check-i18n` 失败。

**验证**：`cd src/frontend && pnpm check-i18n` 通过（且**不新增** baseline 漂移）。

**覆盖 AC**: （支撑 AC-02/03/04/06/09 的前端呈现）

---

### Wave 3 — 注入落点（依赖 W1；三处可并行）

> **⚠ 三处的共同铁律**：注入内容**不得写入任何持久化配置**（INV-18）——不改 `assistant.prompt`、不改应用配置、不改工作流提示词。用户的提示词字段永远保持其编辑时的原文。

#### T-12 助手对话：`prompt` 改 callable

**依赖**: T-06, T-07

**改动**：
- 改 `src/backend/bisheng/api/services/assistant_agent.py` 的 `init_agent`（`:446` 附近）：`prompt` 由字符串改为 **callable**
- callable 从 `state["messages"]` 取**本轮最后一条用户消息**，调 `build_term_block(self.assistant.tenant_id, user_query)`，返回 `[SystemMessage(原文 + 术语块)] + messages`；术语块为空时**返回原文原样**（不追加空区块）
- **不得**把术语块写进 `self.assistant.prompt`（INV-18）

**为什么**：design §3 决策 5、§4.4 第一行、坑 4/5。助手 agent **同会话跨轮复用**（`common/chat/client.py:250-259`），只有 callable 能让「改词下一轮即生效」（AC-25）成立。

**验证**：`uv run python -m pytest test/term_library/test_injection_sites.py -q`（本任务的用例）——**断言 callable 被调用、且返回的 SystemMessage 含术语块**；改词后调两次，断言第二次内容变（AC-25 的单测近似）。

**覆盖 AC**: AC-24, AC-25, AC-26

---

#### T-13 工作流 Agent 节点：同样改 callable

**依赖**: T-06, T-07

**改动**：
- 改 `src/backend/bisheng/workflow/nodes/agent/agent.py`：`_init_agent(system_prompt)`（`:597` 调用处）改为传 callable
- **注意**：该节点在 `:597` 时**还不知道本次输入**（用户提示词在 `_run_once:747` 才 format）——所以**必须**用 callable，不能「先拼好字符串再传」（design 坑 5）
- 节点已有 `self.tenant_id` 可用

**为什么**：design §4.4 第二行、坑 5。

**验证**：`uv run python -m pytest test/term_library/test_injection_sites.py -q` + `uv run python -m pytest test/datasource/test_workflow_compat.py -q`（**回归**：既有内联配置的流程行为不变，AC-32）。

**覆盖 AC**: AC-24, AC-32

---

#### T-14 工作台 SSE：拼 `sys_prompt` 时追加

**依赖**: T-06, T-07

**改动**：
- 改 `src/backend/bisheng/workstation/domain/services/chat_service.py`（`:1600-1608` 拼 `sys_prompt` 处）：追加 `build_term_block(...)` 的返回值；**空串则不追加**
- 本轮提问在该函数作用域内可得，**不需要** callable（这里是逐轮执行的）

**为什么**：design §4.4 第三行。

**验证**：`uv run python -m pytest test/workstation/ -q` 无新增失败（该套件既有失败见「实际偏差记录」）。

**覆盖 AC**: AC-24

---

### Wave 4 — 不变量与合规（依赖 W3）

#### T-15 不变量验证（AC-26, AC-29, AC-30, AC-31, AC-32 + INV-17, INV-18）

**依赖**: T-12, T-13, T-14

**改动**：
- 新增 `test/term_library/test_invariants.py`，覆盖：
  - **INV-17**：① **静态断言**——注入模块（`injection.py`）与注入路径**不出现 `department_id`**；② 行为断言——两个部门各一条术语，**任一部门用户的提问都能命中两条**（部门不限定注入范围）
  - **INV-18**：① 静态断言——注入路径不出现对 `assistant.prompt` / 配置的写入；② 行为断言——改术语后 `AssistantInfo.prompt` **不变**
  - **AC-29**：静态断言 `KnowledgeTypeEnum` 无术语成员；行为断言建术语**不产生 Milvus/ES 集合或索引**
  - **AC-30**：`bisheng/knowledge/` 对 `term_library` **零引用**
  - **AC-31**：`bisheng/term_library/` **不出现 `PermissionService`**、不出现 FGA 资源类型
  - **AC-32**：租户无术语时，对话行为与加本特性前一致

**为什么**：design §7.1/§7.2、spec AC-29, AC-30, AC-31, AC-32 + INV-17/18。这几条都是「**不做破坏**」类断言，**容易被默认成「显然成立」而完全不测**——F058 曾因缺同类测试留下缺口，F061 T-44 已确立了本仓的写法（静态 + 行为双层）。

**验证**：`uv run python -m pytest test/term_library/test_invariants.py -q`。**每条断言须变异验证**（例如给注入路径加一次 `department_id` 读取，INV-17 的静态断言应转红）。

**覆盖 AC**: AC-26, AC-29, AC-30, AC-31, AC-32

---

### Wave 5 — 平台管理页（依赖 W2；与 W3/W4 并行）

> **技术栈**：platform SPA（Zustand + react-query v3 + bs-ui，import 用 `@/`）。
> **参照**：CRUD + 预览式导入仿 `pages/BuildPage/bench/PlatformSensitiveWords.tsx`；部门筛选仿 `pages/KnowledgePage/useKnowledgeDepartmentFilter.ts`。
> ⚠️ 本仓**没有**任何一页同时做过这两件事，需要组合两个先例。

#### T-16 术语库 Tab（列表 + 创建/编辑 + 删除）

**依赖**: T-10, T-11

**改动**：
- 新增 `src/frontend/platform/src/pages/KnowledgePage/TermLibraryTab.tsx`（**新建文件，全量 strict，不得加 `@ts-strict-ignore`**）
- 在 `pages/KnowledgePage/index.tsx` 加第三个 `TabsTrigger`/`TabsContent`（宿主页是遗留 `@ts-strict-ignore`，**不改其既有代码**）
- 列表（分页）+ 创建/编辑弹窗 + 删除确认；controller 走 `@/controllers/API/termLibrary.ts`（**不得直接 `import axios`**，C7/arch-guard RULE-6）

**为什么**：spec AC-12；design §3 决策 3。**门控与接口同集合**——本页在 `/filelib` 下，`permission: 'knowledge'`，与读接口一致（AC-18）。

**验证**：`cd src/frontend && pnpm typecheck && pnpm lint`；手动：`pnpm start`（platform :3001）→ 知识库 → 术语库 Tab → 建词 → 列表出现 → 编辑 → 删除。

**覆盖 AC**: AC-01, AC-12

---

#### T-17 部门筛选 + 批量导入 UI

**依赖**: T-16, T-09

**改动**：
- 部门筛选下拉（含子部门口径），复用既有部门组件
- 导入：文件选择（**仅接受 .csv / .xlsx**）→ **预览**（显示可导入/重复/超长丢弃）→ 选**覆盖/追加** → 确认 → 刷新列表；覆盖模式**须有破坏性提示**
- **前端不解析文件**，只上传字节（`xlsx` 在 platform 是**冻结依赖**，lint 禁止新引用）

**为什么**：spec AC-07/08/13；design §4.1。

**验证**：`pnpm typecheck && pnpm lint`；手动：上传 xlsx → 看到预览数字 → 选追加 → 列表更新；选覆盖 → 有破坏性提示。

**覆盖 AC**: AC-07, AC-08, AC-13

---

#### T-18 权限口径的前端呈现

**依赖**: T-16

**改动**：
- 无 `create_knowledge` 时：**新建 / 编辑 / 删除 / 导入入口不可用**（隐藏或禁用），但**列表可读**（有 `knowledge` 即可进 Tab）
- 403 由响应拦截器统一处理，**不加业务 403 分支**（仓库硬规则）

**为什么**：spec AC-16, AC-17, AC-18；design §3 决策 2。**前端隐藏不是安全边界**——后端已拒（T-10 的用例），此处只是体验。

**验证**：`pnpm typecheck && pnpm lint`；手动：用只有 `knowledge` 的账号登录 → 能看列表、不能写。

**覆盖 AC**: AC-16, AC-17, AC-18

---

### Wave 6 — 收尾

#### T-19 e2e（CI）

**依赖**: 全部

**改动**：
- 新增/扩展 `test/e2e/`：建词 → 对话命中（AC-20/21）→ **不重开会话改词 → 下一轮即生效**（AC-25）→ 未命中不注入（AC-22）→ 覆盖/追加导入
- **AC-25 的行为验证必须在真栈 + LLM 下做**——单测只能近似（T-12），这是本特性唯一「必须 e2e 才能证明」的 AC

**为什么**：SDD 流程步骤 7（强制）；design §7.1。

**验证**：`uv run python -m pytest test/e2e/ -q`（需中间件 + LLM，CI 跑）。

**覆盖 AC**: AC-20, AC-21, AC-22, AC-25

---

#### T-20 文档回写

**依赖**: 全部

**改动**：
- 回填 design §10 偏差记录（实现期与设计不一致之处、被推翻的决策、新增的坑）
- design §9 待定项（注入区块格式 / 字符上限初值 / 大小写）定稿后**覆盖更新**
- 更新本文件「状态」表

**为什么**：`docs/SDD-Guide.md` §3-§4 的偏差分级要求。

**验证**：人工核对 design 与代码现状一致。

**覆盖 AC**: —

---

## AC 追溯矩阵

> **验收线：32 条 AC 必须全部出现在下表。**

| AC | 内容摘要 | 任务 |
|---|---|---|
| AC-01 | 词条增删改查（标准词/别名/释义/归属部门） | T-02, T-08, T-16 |
| AC-02 | 标准词租户内唯一 | T-01, T-08 |
| AC-03 | 别名不与同租户任何标准词或别名冲突 | T-01, T-08 |
| AC-04 | 释义非空且 ≤200 字；标准词非空 | T-01, T-08 |
| AC-05 | 按租户隔离（INV-16），无全局库 | T-03, T-04 |
| AC-06 | 词条总数 ≤500，超限拒绝 | T-01, T-08 |
| AC-07 | 批量导入 txt/csv/xlsx + 差异预览 | T-09, T-17 |
| AC-08 | 覆盖 / 追加两种模式；覆盖有破坏性提示 | T-09, T-17 |
| AC-09 | 无法解析的文件被拒且零落库 | T-01, T-09 |
| AC-10 | 导入遵守与手工录入同一套校验 | T-09 |
| AC-11 | 导入与敏感词库导入对外行为一致 | T-05 |
| AC-12 | 管理页（知识库区第三个 Tab）含列表/创建/编辑/删除/导入/筛选 | T-16 |
| AC-13 | 按归属部门筛选，含子部门 | T-08, T-17 |
| AC-14 | 部门筛选仅影响列表展示，不影响注入范围 | T-08, T-15 |
| AC-15 | 归属部门为空合法且可被筛选区分 | T-02, T-08 |
| AC-16 | 权限与知识库同一套口径，不引入按资源授权 | T-10, T-18 |
| AC-17 | 无 `knowledge` 不能调任何接口；无 `create_knowledge` 不能写 | T-10, T-18 |
| AC-18 | 页面门控与接口门控同集合 | T-10, T-16, T-18 |
| AC-19 | 权限对齐不延伸到部门维度（INV-17） | T-15 |
| AC-20 | 按本轮**用户提问**匹配并注入标准词+释义 | T-06, T-12, T-13, T-14, T-19 |
| AC-21 | 覆盖标准词与别名；命中别名时以标准词为主线并列出别名 | T-06, T-19 |
| AC-22 | 未命中不追加任何内容（不占位） | T-06, T-19 |
| AC-23 | 按字符数封顶；按确定顺序截断并告知剩余条数 | T-06 |
| AC-24 | 覆盖三条主链路（助手/工作流节点/工作台） | T-12, T-13, T-14 |
| AC-25 | 增删改对**此后**对话立即生效（含同会话后续轮次） | T-12, T-19 |
| AC-26 | 注入不写入任何持久化配置（INV-18） | T-12, T-15 |
| AC-27 | 不改变对话其它行为（敏感词关卡/引用/工具链路） | T-15 |
| AC-28 | 匹配与注入不阻塞事件循环 | T-06, T-07 |
| AC-29 | 不进知识库类型枚举、不产生向量、不建集合索引 | T-15 |
| AC-30 | 不修改知识库对象/枚举/检索链路/授权形状 | T-15 |
| AC-31 | 不接 FGA（不新增资源类型、不写元组、不调 PermissionService） | T-10, T-15 |
| AC-32 | 无术语时全部既有行为与发布前一致 | T-07, T-13, T-15 |

**覆盖检查**：32 / 32 ✅

---

## 实际偏差记录

（实现期回填：与 design/spec 不一致的落地、被推翻的决策、测试降级、新增的坑）

### T-09 批量导入（2026-09-21 完成）

| # | 偏差 | 说明 |
|---|---|---|
| 1 | **「空文件」的判定放在 term 侧，未下沉到共享读取层** | 设计原意是把它算作「两个 importer 必须一致判决」的那种情况。实测：把守卫加进 `tabular_import.read_rows` 后**无法变异验证**——F059 侧本就自带「parsed 为空则抛 11301」，删掉共享守卫两个 importer 仍然一致。**一条无法失败的断言等于没测**，而它会改动 F059 已发布文件。故共享层**未改动**（`git diff` 为空），F060 侧自行判定 |
| 2 | **csv 列切分修正：`[,，]+` → `[,，]`** | 测试夹具改为**逗号分隔**（原来用制表符拼「csv」，而共享读取层正是在制表符上切列——两边互相抵消，真实 csv 的列切分路径**一行都没被测到**）。改正夹具后立刻暴露真 bug：`标准词,,释义` 这类**别名留空的常见行**被 `+` 吞掉空列，释义串到别名列，整行按「释义为空」被拒。已修 |
| 3 | **测试夹具修正 2 处**（`test_term_importer.py`） | ① `_patch_dao` 原表里有一个 `term_importer.DepartmentDao` 补丁目标，实现里并不存在（导入器不碰部门），且用了**位置索引**取值——改为按名字取，去掉该行；② `test_the_preview_writes_nothing` 原来是**空断言**（先算一个三元的真值分支再丢弃，只断言一个刚建的空 list）——改为断言 stub 的 `created` |
| 4 | **新增 11505 的一种触发：≥500 条整单拒绝** | `_plan` 里当「导入后规模 > 500」抛 `TermLibraryLimitExceededError`（11504），与 AC-06 一致；同文件内标准词超 255 字符 / 释义超 200 字的行则**丢弃并计入 `dropped_overlong`**（AC-07 要求预览报「超长丢弃数」）。**偏差**：手工录入路径给 11502，导入路径给 11505 —— 与 spec AC-09 一致（没有表单可看，报「文件不可用」） |
| 5 | **`term_importer` 不校验 `department_id`** | 任务书写「导入遵守与手工录入同一套校验」，但导入文件**没有部门列**（设计决策 8 定了三列）。导入的词条 `department_id` 一律为空，由管理员事后在列表里改归属 |
| 6 | **覆盖模式非原子** | 行是一条一条 INSERT（DAO 的既有形态），覆盖中途失败会留下**部分库**。已在模块 docstring 里显式记录为已知限制：文件还在管理员手里，重导即可恢复。F059 的导入是单事务的，本特性不是 |
| 7 | **已知限制：首行含「释义/别名」等词会被当表头丢掉** | 表头识别是共享层既有规则（`HEADER_TOKENS` 含 `释义`/`别名`），命中即丢首行。一份**不带表头**且首行释义恰好写成「释义」的文件会静默少一条。属共享层的既有取舍，本特性未改；如客户反馈再收紧 |
| 8 | **代码风格：新文件用单引号，未跑 `ruff format`** | `pyproject.toml` 的 `[tool.ruff.format] quote-style = "double"` 与已提交的 F060 全部文件（单引号）**互相矛盾**。CI 无 `ruff format --check` 门禁，PostToolUse 钩子写的是 `.venv/bin/ruff`（POSIX 路径，Windows 上不生效）。按「对齐项目现有规范」取**单引号**，与同目录既有文件一致；如需统一格式化，应作为独立改动一次性铺开 |
| 9 | **T-05 未被本次改动触碰** | 上表第 1 条的直接结论：`tabular_import.py` 保持 HEAD 原样 |

### T-10 术语库 API 与门控（2026-09-21 完成）

| # | 偏差 | 说明 |
|---|---|---|
| 1 | **读门控取更严的 `knowledge` 键** | 任务书写「读 `get_login_user` + `assert_effective_web_menu_contains(..., 'knowledge')`」。查既有先例：知识库的**读接口一个门控都没有**，只有写接口查 `CREATE_KNOWLEDGE`（F011 迁移注释：「POST /knowledge/create checks this flag」）。即 `knowledge` 键**在本仓没有调用方**。按任务书**照做**（读查 `knowledge`），后果：平台「知识库」一级菜单已被授予时该键必然在集合内，不会误伤；副作用是它比既有知识库更严，可能有账号能调 `/knowledge/list` 却调不了本接口。**已按任务书落地，此处标注为口径差异供复核** |
| 2 | **写门控复用既有 `assert_effective_web_menu_contains` + `CREATE_KNOWLEDGE`** | 与 `knowledge/api/endpoints/knowledge.py` 的创建接口完全同形 |
| 3 | **`/{term_id:int}` 数字约束 + 字面路由前置** | 直接借用 F061 的教训（`query-failures` 被 id 路由抢占，返回 200 静默走错处理器）。T-10 的测试把它写成**对全部路由的属性断言**——先按单条 id 路由写时，变异只改一条路由**测不出来**（另加一条 `_assert_...` 读门控）。修正后：任一条 id 路由丢失 `:int`、或字面路由排到 id 路由之后，均转红 |
| 4 | **AC-31（包内无 `PermissionService`）留给 T-15** | 本任务先写了个文本级 `'PermissionService' not in source` 断言，被**自己模块 docstring 里的该词**打红——文本扫描分不清调用与文档。且该断言是**包级**的，与 T-15 的 AC-31 重复。已从 T-10 移除，交由 T-15 用 AST 做（只认 `ast.Name` / `ast.Attribute` 等**调用点**，不认注释与文档串） |

### T-12 / T-13 / T-14 三处注入落点（2026-09-21 完成）

> **本任务推翻了任务书的「改 callable」前提，并推翻了 design §6 的一行「已验证」。**

| # | 偏差 | 说明 |
|---|---|---|
| 1 | **两条注入链路的形状被重新设计（T-12 原文只覆盖一半）** | 任务书说「`prompt` 由字符串改为 callable」。实测：助手与工作流节点各有**两个分支**，`callable` 只对 langgraph 分支成立。另一分支走 `ConfigurableAssistant(assistant_message=str)` → `prompt.partial(...)`。**用户裁决：扩展共享 executor**（见第 2 条）。最终形状：**块解析在知道本轮提问的那一层完成，prompt 层只做拼接** |
| 2 | **`bisheng_langchain` 的改动被全部回退（净改动 = 0）** | 先按用户方案扩展了 `create_agent_executor`，实测后**推翻**：`BasePromptTemplate._merge_partial_and_user_variables` 的 `v if not callable(v) else v()` **本来就接受零参 callable**，所以原 executor **无需任何改动**即可接受 builder。我新增的「每 run 渲染一次」不但与既有行为**完全等价**，还多传了参数 → 改动无收益，已 `git checkout` 回退，`bisheng_langchain/` 零 diff |
| 3 | **推翻了 design §6 表下注的「已验证 `_get_prompt_runnable` 的 callable 分支」** | 该结论正确但**不充分**：`inspect.iscoroutinefunction` 才是 langgraph 选同步/异步路径的依据（`prebuilt/chat_agent_executor.py:683`）。**同步 lambda 返回协程会被直接丢弃**（`coroutine was never awaited` → 模型侧 `ValueError`）。故 `term_prompt_callable` 必须是真正的 `async def` 工厂，不能用 lambda |
| 4 | **`run_async_safe` 不能用于异步路径** | langgraph 的 prompt callable 在**事件循环上**执行（实测 `RuntimeError: run_async_safe cannot be called from a running event loop`）。故助手/工作台两条路径改为 **`await` 解析**；只有工作流 Agent 节点（`async_mode` 全仓无调用方，实为同步执行）用 `run_async_safe` |
| 5 | **T-13 的「节点已有 `self.tenant_id`」成立；但 langgraph 分支只能是同步 callable** | 该分支 `prompt=lambda state: ...` 由 langgraph 走**同步** `RunnableCallable(prompt)` 调用，不能 await → 用 `current_turn_block()` 读已解析好的块，不自己读库 |
| 6 | **ReAct 分支需要「声明本轮」** | builder 零参，拿不到本轮提问 → 用 **ContextVar**（非模块全局：并发 run 会串租户，违反 INV-16）声明 `(messages, block)`，`finally` 重置。已测「上一轮的声明不会泄漏到下一轮」 |
| 7 | **注入落点的测试分两层，两层都必要** | ① **机制层**：真 langgraph agent + 假模型，断言**模型实际收到的 system message**（不是图输出 state——prompt 在调模型时才应用，断言 state 会**假绿**）；② **接线层**：对 5 个调用点做源码断言。**只有机制层时，把任一调用点改回旧写法整套测试仍然全绿**（实测 M11/M13/M14/M15/M16 五条变异全部只被接线层抓住） |
| 8 | **`prompt.partial()` 对 callable 是「每次模型调用都重算」** | 实测两次模型调用的回合里 builder 被调 2 次。对 F060 是**加强**（每步都拿到最新词表），故 ReAct 分支不需要 langgraph 分支那样的「每轮重算」保证；代价是单轮内每步各读一次库（`build_term_block` 有缓存兜底） |

### T-15 不变量与合规（2026-09-21 完成）

| # | 偏差 / 要点 | 说明 |
|---|---|---|
| 1 | **静态扫描必须走 AST，不能扫文本** | 本任务首版若照 T-10 的写法用 `'X' not in source` 会**被自己的 docstring 打红**（`terms.py` 的模块说明里就写着 `PermissionService`）。改为解析 AST 只认**真实名字引用**（`ast.Name` / `ast.Attribute` / `ast.alias`），文档与注释里的词不算。变异验证确认：真加一次 `PermissionService.check` 调用会被抓住（M19） |
| 2 | **`ast.FunctionDef` 抓不到 `async def`** | AC-27 的「注入路径不调写方法」扫描，首版用 `FunctionDef` 拿到的 writer 集合是**空的**（DAO 全是 `async def`）——断言「集合非空」这一条正是为了防止这种**静默空转**。改 `ast.AsyncFunctionDef` 后恢复正常 |
| 3 | **每条断言都做了变异验证** | INV-17（注入读部门）/ INV-18（写回 assistant.prompt）/ AC-31（调 `PermissionService`）/ AC-27（调写方法）/ AC-32（空库仍返回区块）/ AC-28（读失败不降级而是抛出）——**6 条全部转红** |
| 4 | **AC-30 用 AST 而非 import 图** | 「`bisheng/knowledge/` 对 `term_library` 零引用」用 AST 名字扫描 + 源码 import 行双重判定；实测当前**零引用**（未变异，因为该断言一旦为真就是真） |

### T-16 / T-17 / T-18 平台管理页（2026-09-21 完成）

| # | 偏差 / 要点 | 说明 |
|---|---|---|
| 1 | **任务书里的两个「参照」路径都要修正** | ① 「部门筛选仿 `useKnowledgeDepartmentFilter.ts`」— 该文件存在，但**不是** `departmentFilter.filterDataParams`，真实字段是 `apiParams`（`KnowledgeFile.tsx:363` 的注释也写错成 `filterDataParams`）；② 「仿 `PlatformSensitiveWords.tsx`」的路径在 `pages/BuildPage/bench/` 下，任务书未写全。按实际接口接线：`deptFilter.selectedDepartmentId` + `deptFilter.includeSubDepartments` |
| 2 | **部门选择器用 `TreeDepartmentSelect`，`modal={false}`** | 该组件注释明确：嵌在 Dialog 里必须传 `modal={false}`，否则多层焦点陷阱导致树节点点不动。编辑弹窗里正是这种嵌套 |
| 3 | **导入默认「追加」而非「覆盖」** | 与平台敏感词库（默认覆盖）**不同**：术语带释义，是有内容成本的资产，覆盖必须被主动选中，不能顺带就发生 |
| 4 | **不新增 `eslint-suppressions.json` 条目** | 新文件首版有 2 条 lint 错误（未用的 `toast`、未用的 `_node`）。suppressions 是**只减不增**的遗留冻结台账，仓库明确禁止新增条目（「never hand-edit the suppressions file」），故**改代码**而非加抑制：删掉重复的 toast（成功提示归父组件，否则一次保存弹两次）、`onChange` 只取第一个参数（与 `CreateDepartmentDialog.tsx:88` 的既有写法一致） |
| 5 | **验证止步于 typecheck + lint + dev 编译** | `pnpm typecheck` **全绿**（389 个 strict 文件）；`pnpm lint` 剩余 53 个错误**全部**在 `dashboard.ts` / `finetune.ts` / `workflow.ts` 三个**我没有改动**的文件里（`git diff` 为空，错误来自它们的既有提交）。另起 dev server 确认 `TermLibraryTab.tsx` 与 `termLibrary.ts` 均被 Vite 正常编译、页面正确 import。**未做真实点击验证**——那需要一个带 `knowledge` 权限的后端账号与运行中的后端；列为待办 |

### T-21（追加）代码审查与修复（2026-09-21）

`/code-review` 跑出 **3 HIGH + 6 MEDIUM**（报告见 [code-review.md](./code-review.md)），其中 **4 条已修并变异验证**：

| 编号 | 问题 | 修复 | 验证 |
|---|---|---|---|
| **H1** | 工作流 Agent 节点的 **langgraph 分支从未注入**（`set_turn` 只在 ReAct 分支里）——AC-24 静默不成立 | `set_turn`/`reset_turn` 提到两个分支之外 | 重放原 bug → 新断言转红 |
| **H2** | **OVERWRITE 在全行被丢弃时清空词库且零写入**（永久数据丢失） | 删除前判 `planned` 为空则抛 11505 | 删守卫 → 转红 |
| **M2** | 唯一性校验大小写敏感 / 匹配不敏感 → `NPL` 与 `npl` 都能建但只有一条可匹配 | 两侧统一 `casefold()` | 退回原串比较 → 2 条转红 |
| **M1** | 释义含逗号被**静默截断** | 共享层新增 `read_csv()`（stdlib `csv.reader`，引号生效）；`_split_columns` 只对单格行再切 | 退回朴素 `split(',')` → 2 条转红；F059 全套 228 条仍全绿 |

**未修**：H3（超管租户范围，需跨特性改前缀表；单租户默认不受影响）、M3/M4/M5/M6、L1~L6。

> **本次审查的方法学教训（值得记入）**：H1 之所以能上线，是因为我给工作流落点写的断言是**源码文本包含**（`'current_turn_block()' in source`）——`set_turn` 有没有被调用它根本不管。**「断言某个符号出现过」不等于「断言接线成立」**；能驱动真实对象时就不要退化成文本匹配。H2 则是「守卫只覆盖了想得到的那一种输入」。

---

## 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-09-21 | **所属版本由 v2.8.0 调整为 v2.7.0**（用户决定；使用户可见的交付版本与登记版本一致，与 F058 同口径）。目录已 `git mv`；表1 两行 / 表2 INV-16~INV-18 / 表3 依赖行 / 错误码 115 均已移入 v2.7.0 契约，v2.8.0 相应移除。**编号不变**：F060 仍为 F060，仍占 INV-16~INV-18 与错误码 115。**本特性尚未实现**，本次为纯登记迁移，无代码改动 |
| 2026-09-21 | **T-05 / T-09 / T-17 修订**（实现期发现的设计缺口，已同步 design 决策 8 与 spec AC-07/AC-11）：导入列语义定为 `标准词, 别名, 释义` 三列、**不支持 txt**；共享解析器的边界收窄为「只共用读成行那一层」 |
| 2026-09-21 | 初版：**20 个任务 / 7 个 Wave**，32 条 AC 全覆盖 | 拆解阶段；基于 design 的 7 条决策 + 11 条坑 + 全套代码盘点 |
