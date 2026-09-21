# Tasks: 术语库（租户术语词条 + 运行时对话注入）

**关联规格**: [spec.md](./spec.md)（32 条 AC）
**设计**: [design.md](./design.md)（7 条决策 + 11 条坑）
**版本**: v2.8.0
**所属 Feature**: F060-term-library

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-21 用户确认。`/sdd-review spec` 14 项检查 13 项通过；唯一未通过项（权限口径缺 AC）已补为 AC-16~19，另 1 medium（§4 How 泄漏）+ 2 low 已处理/留待 design |
| design.md | ✅ 已评审 | 2026-09-21 `/sdd-review design` 24 项：**5 条 findings 全部已修**（3 high：§7 测试与可观测 / §8 后续改进 / Constitution Check 三处整块缺失；1 medium：无可操作验证路径；1 medium：「租户白名单」表述不准确）。**Constitution Check PASS**（C1–C7 逐条结论见 design §2） |
| tasks.md | 🚧 拆解中 | 本文件 |
| 实现 | 🔲 未开始 | — |
| e2e-test | 🔲 未开始 | — |
| code-review | 🔲 未开始 | — |

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
- 表名以 v2.8.0 的既有前缀风格命名

**为什么**：design §4.2；契约表 1 的 Owner 声明。

**验证**：`uv run alembic heads` 打印**恰好一个** revision；`uv run python -m pytest test/database/test_alembic_single_head.py -q` 通过。

**覆盖 AC**: AC-01, AC-05

---

#### T-05 共享解析函数抽取（**改 F059 已发布文件**）

**依赖**: 无

**改动**：
- 新增 `src/backend/bisheng/sensitive_word/domain/services/tabular_import.py`：把 `PlatformWordService.parse_words_file` 的**纯解析部分**（格式识别 / 表头识别 / 编码回退 / 单元格切分 / 去重 / 超长丢弃）提到这里，**无 DB、无服务状态**
- 修改 `platform_word_service.py`：`parse_words_file` 改为**委派**新函数，**公开签名与返回值类型不变**
- **不改变 F059 的对外行为与错误码**（11301 仍是 F059 的）

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
- 两种模式：**覆盖**（清空后写入）/ **追加**（并入）；覆盖的返回须带**破坏性提示所需的信息**
- **预览**：落库前返回「可导入条数 / 重复数 / 超长丢弃数」（AC-07）
- 无法解析的文件**不产生任何落库**（AC-09）

**为什么**：design §3 决策 7、§4.3 模块职责。

**验证**：`uv run python -m pytest test/term_library/test_term_importer.py -q`。须覆盖：txt/csv/xlsx 各一 / 空文件 / 不支持格式 / 无有效词条 / 与既有词条冲突 / 第 501 条被拒 / 覆盖模式的破坏性。

**覆盖 AC**: AC-07, AC-08, AC-09, AC-10

---

### Wave 2 — API（依赖 W1）

#### T-10 术语库 API 与门控

**依赖**: T-08, T-09

**改动**：
- 新增 `src/backend/bisheng/term_library/api/endpoints/terms.py` + `router.py`，并在 `bisheng/api/router.py` 注册
- 端点：`GET /api/v1/term-library`（列表 + 部门筛选 + 分页）/ `POST`（创建）/ `PUT /{id}`（更新）/ `DELETE /{id}` / `POST /import/preview` / `POST /import`
- **门控**（spec AC-16~18）：**读** `get_login_user` + `assert_effective_web_menu_contains(..., 'knowledge')`；**写**（POST/PUT/DELETE/import 两个）另加 `create_knowledge`
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

#### T-15 不变量验证（AC-26 / INV-17 / INV-18 / AC-29~32）

**依赖**: T-12, T-13, T-14

**改动**：
- 新增 `test/term_library/test_invariants.py`，覆盖：
  - **INV-17**：① **静态断言**——注入模块（`injection.py`）与注入路径**不出现 `department_id`**；② 行为断言——两个部门各一条术语，**任一部门用户的提问都能命中两条**（部门不限定注入范围）
  - **INV-18**：① 静态断言——注入路径不出现对 `assistant.prompt` / 配置的写入；② 行为断言——改术语后 `AssistantInfo.prompt` **不变**
  - **AC-29**：静态断言 `KnowledgeTypeEnum` 无术语成员；行为断言建术语**不产生 Milvus/ES 集合或索引**
  - **AC-30**：`bisheng/knowledge/` 对 `term_library` **零引用**
  - **AC-31**：`bisheng/term_library/` **不出现 `PermissionService`**、不出现 FGA 资源类型
  - **AC-32**：租户无术语时，对话行为与加本特性前一致

**为什么**：design §7.1/§7.2、spec AC-29~32 + INV-17/18。这几条都是「**不做破坏**」类断言，**容易被默认成「显然成立」而完全不测**——F058 曾因缺同类测试留下缺口，F061 T-44 已确立了本仓的写法（静态 + 行为双层）。

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
- 导入：文件选择 → **预览**（显示可导入/重复/超长丢弃）→ 选**覆盖/追加** → 确认 → 刷新列表；覆盖模式**须有破坏性提示**
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

**为什么**：spec AC-16~18；design §3 决策 2。**前端隐藏不是安全边界**——后端已拒（T-10 的用例），此处只是体验。

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

---

## 变更记录

| 日期 | 变更 | 触发 |
|---|---|---|
| 2026-09-21 | 初版：**20 个任务 / 7 个 Wave**，32 条 AC 全覆盖 | 拆解阶段；基于 design 的 7 条决策 + 11 条坑 + 全套代码盘点 |
