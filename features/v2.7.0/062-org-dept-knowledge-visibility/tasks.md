# Tasks: 机构/部门标记与树形知识库可见范围（062-org-dept-knowledge-visibility）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-21 编写并通过 /sdd-review spec（25 条 AC）。首轮抓出 11 项问题（含 AC-11 口径歧义会致测试写反、AC-12 漏嵌套机构节点、机构归档语义缺失），已全部修复并新增 AC-04a / AC-08a / AC-11a / AC-12a / AC-20a / AC-20b / AC-24 / AC-25；两条 low 为有意保留 |
| design.md | ✅ 已评审 | 2026-09-21 编写并通过 /sdd-review design（13 条决策）。首轮抓出 **1 条 C3 BLOCKER** + 2 条 high（规模前提与本仓 3w+ 部署的事实矛盾；调用方数量勘误）+ 3 条 medium，已全部修复 |
| tasks.md | ✅ 已拆解 | 2026-09-21 拆解完成，同日通过 /sdd-review tasks（21 项）：**26 个任务 / 7 个 Wave，31 条 AC 全覆盖**。首轮抓出 3 条 high（4 组 Test-First 顺序违规、Wave 与依赖不一致、影响面端点缺实现任务）+ 3 条 medium（T-24 粒度过大、两处延迟决策）+ 4 条 low，已全部修复并**重排编号**；**第二轮复审 LGTM**（AC 覆盖双向校验 31/31、6 组 Test-First 配对全部测试在前、全部依赖指向更早任务、无范围式 AC 写法、无残留延迟决策） |
| 实现 | ✅ 完成 | **26 / 26**（Wave 0~F 全部）。Wave 0/A 已过 `/task-review`。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |
| e2e-test | 🚧 进行中 | T-24/T-25/T-26 已写并**全部通过**（见下方「E2E 结果」）。按 SDD 流程，`/e2e-test` 尚未正式执行 |
| code-review | ✅ 已执行 | 2026-09-21 两 agent 并行审 6 维度（边界/权限/并发/泄漏/风格/文档）。抓出 **3 条 HIGH**（归档守卫被删、`invalidate_all` 零调用、前端 `is_tenant_admin` 字段不存在）+ 2 条 MEDIUM，全部修复并测试钉住。报告见本文件「code-review 结果」 |

### E2E 结果（2026-09-21）

| 套件 | 结果 |
|---|---|
| `test_e2e_f062_org_domain_visibility.py`（T-25 跨入口） | **6 / 6 通过** |
| `test_e2e_f062_archive_direction.py`（T-26 归档方向） | **4 / 4 通过** |
| `test_e2e_f057_knowledge_department.py`（升级不变性回归） | **17 / 17 通过** |

> **E2E 抓到一个 74 个单测全部漏掉的核心缺陷**：候选集仍是 F057 的「祖先链」，而 F062 的机构域规则能到达**兄弟部门**——那些库在 SQL 阶段就被排除，逐行判定再正确也没有机会评估。现象是「角标说 3、列表返回 2」。修复见「实际偏差记录」，回归测试 `test_f062_candidate_superset.py`。

### code-review 结果（2026-09-21）

| 维度 | High | Medium | 处理 |
|---|---|---|---|
| 权限与认证 | 1 | 0 | 归档只读守卫被误删 → 已恢复 + 测试 |
| 文档同步 | 1 | 7 | `invalidate_all` 未落地 → 已接；其余文档欠账已补 |
| 代码风格 | 1 | 3 | 前端 `is_tenant_admin` 不存在 → 改 `is_child_admin` |
| 边界条件 | 0 | 2 | `DepartmentCreate.is_department` 被丢弃 → 已修 |
| 并发安全 | 0 | 2 | 审计实参位置 → 已修；缓存无上限 → 记入 design §8 |
| 测试覆盖 | 0 | 1 | 见「已知短板」 |

> 两条 HIGH 是**真实的权限漏洞**（归档部门可写、翻转后有 10 秒 stale-allow 窗口），审查物有所值。**另有两条审查发现的真实功能缺口**：批量标注 UI 未实现、移动路径无确认弹窗——**两条均已于同日补齐**（见下）。

### 第二轮 code-review（2026-09-21，缺口补齐之后）

两个 agent：一个审本轮新增代码（移动影响面 / 批量标注），一个**复核前轮 7 项修复是否仍成立**。

**复核结论：7 / 7 全部仍成立**，无被后续改动破坏的项。两处数字比预期更严格——归档守卫 9 处（预期 8）、缓存失效调用点 8 个（预期 7），多出的正是新增的 `abatch_set_node_type` 及其守卫与失效调用。

**新抓到的 2 条 HIGH（均已修复 + 测试钉住）**：

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | **移动影响面的观察者用了变更前的 path** —— `viewer_path` 恒取 `mapping`，而移动会重写子树 path，于是「变更后」拿旧 path 比新 domain，**必然判为变化**，影响面虚高。翻转不改 path 所以掩盖了它 | `after_path` 改取 `changed`；回归用例 `test_move_impact_uses_the_viewers_path_AFTER_the_move_too`，经变异验证 |
| 2 | **前端 `nodeType` 切换部门后不同步** —— 只在挂载时初始化，而 `DepartmentSettings` 无 `key` 不重挂载。切到另一个节点后 `markerChanged` 恒真，**只改个名字保存就会把该节点悄悄翻成机构**（反向则是无意的可见性放宽） | load effect 与 `restoreBaseline` 都补 `setNodeType` |

**一并修复（medium）**：`bsConfirm` 用 Esc 关闭时 `onClose` 不触发 → Promise 永不 settle → 保存按钮卡死。**未改共享组件**（全平台所有 `bsConfirm` 调用方同此行为，属既有），而是在本调用侧把 `setSaving(true)` 移到确认**之后**——最坏情况变成「这次没保存，可重试」而非「按钮永久禁用」。

**未修（低，记入 design §8）**：移动影响面未校验目标父节点合法性（环/跨租户会产出无意义的 0 或不可能存在的树）；`dept_ids` 未去重；批量影响面「逐节点求和」在祖先与后代同选时会低估（选择集内部有交互，非线性可加）。

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 0（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**Wave 顺序即执行顺序**：同一 Wave 内的任务无相互依赖，可并行；跨 Wave 必须按序。**任何任务的依赖都不得指向后续 Wave**（首轮拆解曾出现该问题）。

**任务分类**：

| 分类 | 任务 |
|---|---|
| 基础设施（迁移 / 模型 / schema） | T-01, T-02, T-03, T-04 |
| 后端判定核心（Test-First 配对） | T-05, T-06, T-07, T-08, T-09, T-10, T-11 |
| 后端标记写入与权限（Test-First 配对） | T-12, T-13, T-14 |
| 后端影响面与审计（Test-First 配对） | T-15, T-16, T-17 |
| 前端 Platform | T-18, T-19, T-20, T-21, T-22 |
| 前端 Client | **无**——本特性客户端**零代码改动**，仅消费后端可见性判定（spec AC-21 的一致性要求由 T-25 断言，不需 client 侧实现） |
| Worker | **无**——判定全部发生在读路径上，可同步求值；缓存失效由部门变更路径同步触发，不走异步任务 |
| 版本契约 | T-23（已完成） |
| 回归与 E2E | T-24, T-25, T-26 |

**后端 Test-First（务实版）**：理想流程是先写测试（红）再写实现（绿）。**每个后端实现任务都必须有前置的测试任务**，测试任务先声明期望接口，实现任务满足它。中间件 / DM8 / e2e 测试在 **CI** 跑，不依赖本地（见 `docs/SDD-Guide.md` §6）。

**前端**：当前用「手动验证」（每个任务附验证步骤）。目标是 Playwright 交互测试（🚧 规划中）。

**本 feature 特殊口径**：

- **不写 FGA 元组**：可见性由读时求值提供（design 决策 3）。本 feature 的任何任务都不得调用 `PermissionService.authorize()` 来落地机构域可见性。
- **不改 `department_stats`**：该文件与端点的汇总口径保持不变（design 决策 13）。只有在 T-25 的回归中才碰它——且是**验证**，不是修改。
- **不新增 DAO 入口**：C1 明文禁止为 new feature 新增 DAO 入口。映射数据源用既有的 `DepartmentDao.aget_active_by_tenant`（`database/models/department.py:501`）。
- **判定必须 fail-closed**：任何一处解析失败（部门缺失、路径为空、缓存不可用）都必须退化为「不授予」，不得退化为「放开」。

---

## Tasks

### Wave 0 — 数据模型与迁移（基础设施，无测试配对）

#### T-01 迁移：`department` 加机构/部门标记列 ✅

**依赖**: 无

**文件**: `src/backend/bisheng/core/database/alembic/versions/v2_7_0_{NNN}_department_node_type.py`

**逻辑**：
- `revision` 命名与 `down_revision` 按**当前迁移链尾**对齐（先 `alembic heads` 查，不要假设）
- `upgrade()`：`column_exists` 守卫下 `add_column`；整型、`nullable=False`、`server_default=text("0")`、带列注释
- `downgrade()`：`column_exists` 守卫下对称回滚
- **纯 DDL**（`alembic/AGENTS.md` §5）：`server_default` 行填充是唯一允许的数据效果
- **不建索引**：本特性不按该列查询（映射是整租户载入，见 T-07），加索引是死索引

**为什么**：`server_default 0`（= 机构）是升级零行为变化的唯一保证——见 design 决策 2。全标机构时新规则**恒等于** F057，这是恒等不是近似。

**验证**：`alembic upgrade --sql` 离线干跑不报 `NoInspectionAvailable`（沿用 F057 的 `column_exists` / `index_exists` 共享 helper，不用裸 `sa.inspect`）。

**覆盖 AC**: AC-01, AC-02

---

#### T-02 模型：`Department` 加标记字段 ✅

**依赖**: T-01

**文件**: `src/backend/bisheng/database/models/department.py`

**逻辑**：`Department` 加字段，与既有 `is_tenant_root`（`:136`）/ `is_deleted`（`:159`）并列，保持同样的 `SmallInteger` + `server_default` + 中文注释风格。**不新增任何 DAO 方法**——映射用既有 `aget_active_by_tenant`（`:501`）。

**关键约束**：值域 `0` = 机构 / `1` = 部门，语义是「**是否对其上级透明**」，不是「节点类型」（design 决策 1）。写注释时用这个语义表述。

**验证**：`aget_active_by_tenant` 返回的行带上新字段且值正确。

**覆盖 AC**: AC-01, AC-03

---

#### T-03 Schema 与树节点 dict 透传 ✅

**依赖**: T-02

**文件**:
- `src/backend/bisheng/department/domain/schemas/department_schema.py`（`DepartmentTreeNode`，`:85`）
- `src/backend/bisheng/department/domain/services/department_service.py`（`_dept_node_dict`，`:280`）

**逻辑**：两处都加新字段。`DepartmentTreeNode` **必须给默认值**（参照同文件 `is_tenant_root: bool = False`）——否则 `_dept_node_dict` 手工构造 dict 的路径与 pydantic 校验会因缺字段报错。

**为什么**：F038 的懒加载树走的是手写 dict + ORJSON 路径（`_dept_node_dict`），与 pydantic 模型是**两处**，只改一处会静默丢字段。

> 本任务与 T-12 都改 `department_schema.py`，但改的是**不同类**（本任务是响应模型 `DepartmentTreeNode`，T-12 是请求模型 `DepartmentCreate`/`DepartmentUpdate`），互不覆盖。

**验证**：`GET /api/v1/departments/children` 与 `GET /api/v1/knowledge/my_departments` 的响应里能看到新字段且值正确。

**覆盖 AC**: AC-01, AC-08a

---

#### T-04 测试 fixture：`table_definitions.py` 同步新列 ✅

**依赖**: T-02

**文件**: `src/backend/test/fixtures/table_definitions.py`

**逻辑**：`department` 建表语句加新列。

**为什么**：该 DDL 是手工维护的。模型加了列而 fixture 没加，会在 INSERT 时报 `table department has no column named ...`——F057 的 T-03 踩过同一个坑。

**验证**：跑任一创建 department 的测试不报列缺失。

**覆盖 AC**: —

---

### Wave A — 判定核心（Test-First 严格配对）

> 本 Wave 是全部改动的正确性核心。**每个实现任务之前都有配对的测试任务**。
> Wave 内的顺序即依赖链：T-05 → T-06 → T-07 → T-08 → T-09 → T-10 → T-11。

#### T-05 测试：`nearest_org_path` 纯函数真值表 ✅

**依赖**: T-02

**文件**: `src/backend/test/permission/test_f062_nearest_org_path.py`

**逻辑**：针对**纯函数**（输入 `dept_id` + 内存映射，输出路径或 None）覆盖：

- 节点自身是机构 → 返回自身 path（**不是**向上找严格祖先）——design 坑 #2
- 节点是部门 → 返回最近机构祖先的 path
- 链路上有多个机构 → 取**最近**的那个，不是根
- 链路上无任何机构（极端构造）→ 按 AC-14 仍可判定，不得抛异常
- 映射缺该 id → 返回 None（fail-closed）
- 映射中某节点的 `parent_id` 悬空 → 返回 None，不得死循环

**本任务只写测试**，实现由 T-08 满足；测试此刻应**红**。

**覆盖 AC**: AC-09, AC-12, AC-12a, AC-13, AC-14

---

#### T-06 测试：映射缓存三条必测 ✅

**依赖**: T-02

**文件**: `src/backend/test/permission/test_f062_department_mapping_cache.py`

**逻辑**（三条都是「不写就会静默错」的类型）：

1. **租户隔离**（C3 门禁项）：构造两个租户、**相同的部门 id、不同的树形**，断言 A 的判定不受 B 的映射影响。**变异验证**：去掉缓存键里的租户维度，本例必须失败。
2. **大租户退化**：构造超过阈值的节点数，断言走的是逐级回溯分支，**没有**触发全量载入。
3. **fail-closed**：映射不可用（DB 异常 / 缓存不可达）时断言**不授予**，不得放开。

**本任务只写测试**，实现由 T-07 满足；测试此刻应**红**。

**覆盖 AC**: AC-19

---

#### T-07 实现：按租户的部门映射缓存 + **落点选型** ✅

**依赖**: T-06

**文件**: **落点必须在本任务内显式决定并写进「实际偏差记录」**（design §4.3「待 design 评审后定稿」）

**三种落点各自的硬约束**（design §4.3 已列，实现者不得绕过）：

| 落点 | 约束 |
|---|---|
| `permission/domain/services/` 内 | **不得** import DAO（跨域越层）；载入须经 `department` 域 Service 暴露的方法 |
| 由调用方（`knowledge` 域）载入后传入 | `knowledge` 域已合法依赖 `department` DAO，可行；但需让调用点都能拿到映射 |
| `common/` 或 `core/` | 触发 arch-guard **RULE-1**（二者不得 import `domain/`）——**不建议** |

**逻辑**（三条硬性要求，缺一不可）：

1. **键必须含租户**：`{tenant_id: {dept_id: nearest_org_path}}`。多租户下部门 id 可能重复，跨租户共享会让 A 的判定命中 B 的路径 → **跨租户越权（C3）**。
2. **懒加载 + 每租户条目上限**：不预热、不做全局扫描；首次命中某租户才载入；设条目上限。
3. **超大租户退化**：单租户节点数超阈值（**5000**）时**不做全量载入**，退回逐级回溯 + 请求内 memo。理由见 design 坑 #13 与 `department_service.py:1519`。

**数据源**：`DepartmentDao.aget_active_by_tenant(tenant_id)`（既有，`:501`）——**不要新增 DAO 方法**。

**本任务确立模块落点**，T-08 的纯函数随之落在同一模块。

**测试**: T-06 全部通过。

**覆盖 AC**: AC-19

---

#### T-08 实现：`nearest_org_path` 纯函数 ✅

**依赖**: T-07

**文件**: `src/backend/bisheng/permission/domain/services/`（**落在 T-07 确立的模块内**）

**逻辑**：沿 `parent_id` 上行，**先判自身**再向上，遇到第一个机构（`is_org == 0`）即返回其 `path`；到根仍未遇机构则返回根 path。带访问计数上界防环。

**关键约束**：**不查库、不缓存、不做租户判断**——纯输入输出。这样它可被 `permission` 域自由调用而不触碰 C1 的分层约束（见 design §4.3）。

**测试**: T-05 全部通过。

**覆盖 AC**: AC-09, AC-12, AC-12a, AC-13, AC-14

---

#### T-09 实现：映射缓存失效接入点 ✅

**依赖**: T-07

**文件**: 部门变更路径（`department/domain/services/department_service.py` 的新增 / 更新 / 移动 / 归档）

**逻辑**：在部门**新增 / 改名 / 移动 / 归档 / 标记翻转**五类变更后失效本租户映射。**改名不改 path 的 id 链**——映射若只存不含名称的路径则改名无影响，若同时缓存了节点名则必须失效（按实际缓存的字段判断）。

**与 T-17 的区别**：本任务是**映射**失效（必须精确到租户）；T-17 的 `PermissionCache.invalidate_all()` 是**判定结果**失效（可以粗放）。

**验证**：改一个节点的标记后，下一次判定的结果立即按新映射计算（10 秒 TTL 内也生效）。

**覆盖 AC**: AC-06, AC-19

---

#### T-10 测试：可见范围规则矩阵 ✅

**依赖**: T-08, T-09

**文件**: `src/backend/test/permission/test_f062_org_domain_visibility.py`

**逻辑**：按 spec §2.2 的判定式建**树形 × 归属位置 × 用户位置**的矩阵。**必须包含的最小集**：

| # | 场景 | 期望 | 对应 AC |
|---|---|---|---|
| 1 | 用户描述的 6 节点树（总行 → 信贷研发/信贷审批部/一级分行 → 审批部1/2），用户在一级分行下 | 恰好 6 个节点全部可见 | AC-10, AC-11 |
| 2 | **全树标机构** | 与 F057 既有用例**逐条相同** | AC-20 |
| 3 | 根下加二级分行（机构） | 看不到二级分行**本身** | AC-12 |
| 4 | 同上 | 看不到二级分行**下属部门** | AC-12a |
| 5 | 部门套多层部门（信贷研发 → 一组 → 一组A） | 可见（无限层穿透） | AC-13 |
| 6 | 用户兼职两个部门 | 两处可见集的**并集** | AC-15 |
| 7 | 用户无任何部门 | 可见集为空 | AC-16 |
| 8 | 根节点标机构 / 标部门 | **结果无差异** | AC-14 |
| 9 | 公共祖先自身是机构 | **不阻断**（同机构域） | AC-11a |
| 10 | 知识空间类型 | **不受本规则影响** | AC-18 |
| 11 | 多租户：同为 id、不同树形的两个租户 | 互不影响 | AC-19 |

**必须做的变异验证**：把 T-08 的「先判自身」改成「先向上」（取严格祖先），**用例 3 必须失败**——否则说明用例 3 是空断言（design 坑 #2 正是这个易错点）。

**本任务只写测试**，实现由 T-11 满足；测试此刻应**红**。

**覆盖 AC**: AC-10, AC-11, AC-11a, AC-12, AC-12a, AC-13, AC-14, AC-15, AC-16, AC-18, AC-19, AC-20

---

#### T-11 实现：`_implicit_department_library_level` 替换规则 ✅

**依赖**: T-10

**文件**: `src/backend/bisheng/permission/domain/services/permission_service.py`（`:1185`）

**逻辑**：把既有前缀比较的**左侧**从 `owning_department.path` 换成 `nearest_org_path(knowledge.department_id, mapping)`。前置的 `knowledge.department_id is None → None`、`memberships 为空 → None`、异常 fail-closed 等分支**全部保留不动**。

**关键约束**：
- **只改这一处比较**。候选集、分页、列表分叉、管理员分支**一律不动**（design 决策 8 / §4.1）。
- 相邻的 `_implicit_department_space_member_level`（`:1158`）**不得合并、不得顺手改**——两个分支语义有意不同（知识空间走成员制，design 决策 9）。
- 全标机构时必须**逐条等于**改动前行为（AC-20）——这是本任务最重要的回归基准。
- **共享文件影响面**：`_implicit_department_library_level` 有 4 个调用点，其上游 `get_implicit_permission_level` 有 9 个调用点、跨 3 个模块（`permission_service` / `fine_grained_permission_service` / `resource_permission` 端点）。改的是**内部行为**、不动签名，故调用方无需改动；但回归需覆盖这三条路径（见 T-24）。

**测试**: T-10 全部通过。

**覆盖 AC**: AC-09, AC-10, AC-11, AC-11a, AC-12, AC-12a, AC-18, AC-20

---

### Wave B — 标记写入与权限（Test-First 配对）

#### T-12 Schema：创建 / 更新入参 ✅

**依赖**: T-02

**文件**: `src/backend/bisheng/department/domain/schemas/department_schema.py`

**逻辑**：
- `DepartmentCreate`（`:34`）加可选字段，**默认机构**（AC-03：不继承父节点）
- `DepartmentUpdate`（`:42`）加可选字段（`None` = 不改）

**关键约束**：入参默认值必须是「机构」——与 AC-02 的存量默认值同一套语义（不透明是安全缺省），不得从父节点继承（AC-03）。

> 本任务与 T-03 都改本文件，但改的是**不同类**（本任务是请求模型，T-03 是响应模型），互不覆盖。

**覆盖 AC**: AC-03

---

#### T-13 测试：字段级权限门槛 ✅

**依赖**: T-12

**文件**: `src/backend/test/department/test_f062_node_type_permission.py`

**逻辑**：
- 部门管理员改**自己子树**内的 `name` → **允许**（回归：确认本特性没有误伤既有能力）
- 部门管理员改**自己子树**内的标记 → **拒绝（403）**
- 系统管理员改任意节点的标记 → 允许
- 租户管理员改其管辖范围内的标记 → 允许；范围外 → 拒绝
- 同时提交 `name` + 标记：**整单拒绝**（`name` 的改动一并放弃）——本任务钉住这一种语义，实现不得静默丢弃 `is_org` 而只应用 `name`

**本任务只写测试**，实现由 T-14 满足；测试此刻应**红**。

**覆盖 AC**: AC-04, AC-04a

---

#### T-14 实现：`aupdate_department` 的字段级权限分支 ✅

**依赖**: T-13

**文件**: `src/backend/bisheng/department/domain/services/department_service.py`（`aupdate_department`）

**逻辑**：标记字段单独校验——只有系统管理员 / 租户管理员可改；其余字段（`name` / `sort_order` / `default_role_ids` / `admin_user_ids`）**沿用既有权限门不动**。租户管理员的范围与其**既有**部门管理范围一致（AC-04a：不扩大也不缩小）。同单含被拒字段时整单拒绝（T-13 已钉住该语义）。

**为什么**：不改会留下一条**自下而上的提权通道**——一级分行的部门管理员把自己标成「部门」即可解除全局屏障（design 决策 11）。

> 本任务与 T-03 / T-09 都改 `department_service.py`，但改的是**不同函数**（本任务 `aupdate_department`、T-03 `_dept_node_dict`、T-09 的变更路径），互不覆盖。

**测试**: T-13 全部通过。

**覆盖 AC**: AC-04, AC-04a

---

### Wave C — 影响面与审计（Test-First 配对）

#### T-15 测试：影响面口径 + 审计落库 ✅

**依赖**: T-11, T-14

**文件**: `src/backend/test/knowledge/test_f062_impact_and_audit.py`

**逻辑**：
- 构造已知树形，断言影响面的**部门数 / 知识库数与手工推算一致**（不是「>0」这类空断言）
- 断言影响面口径与实际可见范围变化**一致**：弹窗说的 N，事后验证确实变了 N
- 断言 `audit_log` 落了一行，且 `action` / `target_type` / `target_id` / `metadata` 四要素正确
- 断言变更后 10 秒内（TTL 未过期）判定已按新规则

**本任务只写测试**，实现由 T-16 / T-17 满足；测试此刻应**红**。

**覆盖 AC**: AC-05, AC-06, AC-07

---

#### T-16 实现：影响面统计 Service + 只读端点 ✅

**依赖**: T-15

**文件**:
- `src/backend/bisheng/knowledge/domain/services/knowledge_department_service.py`（统计逻辑）
- `src/backend/bisheng/department/api/endpoints/department.py`（只读端点 + router）

**逻辑**：给定待变更的节点，计算「多少个部门 / 多少个知识库的可见范围会发生变化」，并暴露为只读 HTTP 端点供确认弹窗（T-21）调用。口径必须与 T-11 的判定**同源**——直接复用同一份映射与判定函数，**不得另写一套近似算法**。

**端点契约**（T-18 前端按此封装）：`GET /api/v1/departments/{dept_id}/node-type-impact`，返回受影响部门数与知识库数。

**为什么**：影响面数字是操作者的唯一刹车（design 决策 10）。口径分叉会让弹窗说谎——比没有弹窗更糟。

**测试**: T-15 全部通过。

**覆盖 AC**: AC-05

---

#### T-17 实现：审计留痕 + 判定缓存失效 ✅

**依赖**: T-15

**文件**: 变更路径（`department` 域）+ 审计调用

**逻辑**：
- 写入既有 `audit_log` 表，走 `AuditLogDao.ainsert_v2()`（`database/models/audit_log.py:376`）
- `action` 新增到部门模块的枚举，沿用既有 `dept.*` 命名空间（先例：`org_sync/domain/constants.py:34-36`；枚举类写法参照 `TenantAuditAction`）。本次新增两个 action：标记翻转、节点移动
- `target_type='department'`、`target_id=` 节点的 `dept_id`
- `metadata` 记 `{from_node_type, to_node_type, affected_departments, affected_libraries}`；移动另记 `{from_parent_id, to_parent_id}`
- `tenant_id` / `operator_tenant_id` 按 `ainsert_v2` docstring 的 §5.4 规则填
- 变更后调 `PermissionCache.invalidate_all()`（`permission_cache.py:143`）
- **把这两个 action 加入 `_UI_VISIBLE_V2_ACTIONS`**（`audit_log.py:178`），使管理员能在「系统操作」审计页查到——标记翻转是高风险权限变更，应当可见。同时按该元组注释的既有约定，**同步前端 `getActionsApi` / `getActionsByModuleApi` 的白名单**

**测试**: T-15 全部通过。

**覆盖 AC**: AC-06, AC-07

---

### Wave D — 前端 Platform（手动验证）

#### T-18 API 层 ✅

**依赖**: T-14, T-16

**文件**: `src/frontend/platform/src/controllers/API/department.ts`

**逻辑**：标记相关请求封装（创建 / 更新时带标记）+ 影响面查询 `GET /api/v1/departments/{dept_id}/node-type-impact`。遵守既有 API Layer Pattern，i18n 三语同步。

**注意**：拦截器已解包 `response.data.data`（`controllers/request.ts`）——**不要再写 `res?.data`**，会恒为 `undefined` 且静默失效（design 坑 #12）。store 不得直连 HTTP（C7）。

**覆盖 AC**: AC-05

---

#### T-19 树节点标记呈现 ✅

**依赖**: T-03, T-18

**文件**: `src/frontend/platform/src/pages/DepartmentPage/components/DepartmentTree.tsx`

**逻辑**：节点行上可辨认地区分「机构 / 部门」（图标或文字标记）。

**落点已定**：**仅在组织管理页的树上呈现**（AC-08a 的落点）。各类部门**选择器**（`LazyDepartmentTree` 的其余消费方）**不呈现**——避免改变既有布局高度导致错位，也避免把组织层级概念泄漏到无关的授权选择场景。

**手动验证**：打开平台组织管理页，确认每个节点都能看出机构/部门。

**覆盖 AC**: AC-08a

---

#### T-20 标记编辑 + 批量标注 ✅

**依赖**: T-18, T-19

**文件**: `src/frontend/platform/src/pages/DepartmentPage/` 的编辑表单与树的多选区

**逻辑**：
- 单节点：编辑表单里可切换标记（调用 T-18）— **已实现** ✅
- 批量：树支持多选后一次性设置标记（AC-08）— **未实现**，`DepartmentTree` 无多选态、无批量提交；两条 i18n 键（`batchMark` / `batchSelected`）已加但**零引用**，属死键

**状态说明（2026-09-21，code-review 发现）**：本任务此前被标为 ✅，实际只完成了单节点那一半。**AC-08 的「多选 + 批量设置」是缺口**，已记入 design §8 已知短板。当前批量标注只能逐个节点在设置页改。

**手动验证**：单节点改标记 → 刷新后生效 ✅。批量为待办。

**覆盖 AC**: AC-03（完成）、AC-08（**部分**：引导条已做，批量入口未做）

---

#### T-21 确认弹窗（翻转 / 移动 / 批量标注） ✅

**依赖**: T-18

**文件**: `src/frontend/platform/src/pages/DepartmentPage/` 的确认交互

**逻辑**：提交翻转、移动或批量标注前，先取影响面数量，展示「N 个部门 / M 个知识库的可见范围将发生变化」，确认后才提交（AC-05 已含批量）。

- 翻转 — **已实现** ✅（`confirmNodeTypeChange`，含 `unavailable` / 零值 / 计数的三分支展示）
- 移动 — **未实现**：`DepartmentSettings` 只在 `markerChanged` 时弹窗，改父节点走 `moveDepartmentApi` **直接提交**
- 批量标注 — 未实现（依赖 T-20 的批量入口）

**状态说明（2026-09-21，code-review 发现）**：本任务此前被标为 ✅。**移动路径的确认弹窗是缺口**，而移动会重排整个子树的机构域，影响面不小于翻转，且 `amove_department` 后端**已经**在写审计并计算影响面——前端没把它用起来。已记入 design §8。

**一个实现细节（易踩）**：`bsConfirm` 是回调式的，且有三个出口（ok / cancel / **关闭 X，只触发 `onClose`**）。只接 `onOk`/`onCancel` 会让 Promise 永不 settle、保存按钮卡死——实现里用了一个 `settle` 守卫。

**手动验证**：翻转一个机构 → 弹窗出现且有具体数字 → 取消则不变更 → 确认则变更 ✅。移动：直接提交，无弹窗（待办）。

**覆盖 AC**: AC-05（**部分**：翻转已覆盖，移动/批量未覆盖）

---

#### T-22 空状态引导条 ✅

**依赖**: T-18, T-19

**文件**: `src/frontend/platform/src/pages/DepartmentPage/`

**逻辑**：当前租户无任何节点标为「部门」时，展示**可关闭**的提示，引导标注；提示内提供直达批量标注（T-20）的入口。

**为什么**：本特性上线后不会自动生效（design 决策 2），引导是降低实施成本的手段。

**手动验证**：在全新租户（全部默认机构）打开组织管理页 → 出现提示 → 关闭后不再出现 → 标一个部门后提示消失。

**覆盖 AC**: AC-08

---

### Wave E — 版本契约登记（已完成）

#### T-23 release-contract 登记 ✅

**依赖**: 无

**改动**（已于 2026-09-21 随 spec 编写完成）：
- 表 1：新增 2 行（`department` 标记字段；机构域可见范围规则）+ 1 行只读复用说明（`department_stats` **不修改**）
- 表 2：新增 **INV-20**（部门派生可见性只增不减、无负向授权）、**INV-21**（标记是唯一隔离边界、对所有读取入口无条件生效）
- 表 3：新增 F062 依赖行（依赖 F057）
- 错误码表：新增一行（F062 不新增错误码）
- 表 1 规则补充「**「对象」的粒度是数据对象，不是行为**」——为 F062 的显式例外（改变 F057 的行为）提供可追溯的登记机制并堵住后门
- 变更历史：新增一行（含 Impacted Specs 的完整说明）
- **F057 spec 回写**：AC-03 收窄为只约束向上；新增 AC-03a / AC-03b 承载横向与嵌套机构边界，并声明以 F062 §2.2 为唯一口径

**编号扫描**（按契约要求扫三来源，全部为空）：F 编号 **062** 空闲；错误码 **118** 空闲（114 是前端哨兵，非分配）；INV 下一个为 **INV-20**。

**覆盖 AC**: AC-25

---

### Wave F — 回归与 E2E

#### T-24 升级不变性 + 上游调用路径回归 ✅

**依赖**: 全部实现任务（T-01~T-22）

**文件**: `src/backend/test/e2e/test_e2e_f062_upgrade_invariance.py`

**逻辑**：

1. **升级不变性（最高优先级，AC-02）**：构造一批存量数据（全标机构），跑升级前后的同一组可见性断言，要求**逐条相同**。这条不能只靠推理，必须有独立用例。
2. **F057 既有用例全量重跑**：F062 扩展并收窄了 F057 的 AC-03，改动后必须确认 F057 的既有回归没有静默退化。
3. **上游调用路径回归**：T-11 改动的是 `_implicit_department_library_level`，其上游 `get_implicit_permission_level` 有 9 个调用点、跨 3 个模块（`permission_service` / `fine_grained_permission_service` / `resource_permission` 端点）。改的是内部行为、签名未变，但需**至少各覆盖一条**，确认没有连带破坏。

**覆盖 AC**: AC-02, AC-20

---

#### T-25 跨入口一致性 + 角标 ✅

**依赖**: T-24

**文件**: `src/backend/test/e2e/test_e2e_f062_cross_entry.py`

**逻辑**：

1. **跨入口一致性（AC-21 / AC-23 / AC-24）**：客户端列表 / 平台列表 / 检索白名单**三处取同一批断言**。重点覆盖工作台对话与**灵思任务内核**（`linsight/domain/task_exec.py:1227` 的 `_resolve_allowed_knowledge_ids`）——INV-21 明确要求这两个入口也在内，但它们不在客户端列表的自然回归路径上，**最容易漏接**。
2. **角标一致性（AC-22）**：断言平台树上节点的角标数字 == 点击该节点后列表返回的条数。**本特性不应改动 `department_stats`**——若这条断言失败，先查是不是有人顺手改了它（design 决策 13）。

**覆盖 AC**: AC-21, AC-22, AC-23, AC-24

---

#### T-26 机构归档 fail-closed + 可见权限级别 ✅

**依赖**: T-24

**文件**: `src/backend/test/e2e/test_e2e_f062_archive_and_permission_level.py`

**逻辑**：

1. **机构归档 fail-closed（AC-20a / AC-20b）**：归档一个机构 → 断言子树用户**失去**可见性，且**没有**因此看到归档前不可见的知识库。方向写反会变成越权，而正向用例测不出来。
2. **多租户（AC-19）**：归档 / 翻转操作不跨租户生效。
3. **权限级别（AC-17）**：横流来的库只有查看 / 使用，无编辑 / 删除 / 成员管理。

**覆盖 AC**: AC-17, AC-19, AC-20a, AC-20b

---

## AC 覆盖对照

| AC | 覆盖任务 |
|----|---------|
| AC-01 | T-01, T-02, T-03 |
| AC-02 | T-01, T-24 |
| AC-03 | T-02, T-12, T-20 |
| AC-04 | T-13, T-14 |
| AC-04a | T-13, T-14 |
| AC-05 | T-15, T-16, T-18, T-21 |
| AC-06 | T-09, T-15, T-17 |
| AC-07 | T-15, T-17 |
| AC-08 | T-20, T-22 |
| AC-08a | T-03, T-19 |
| AC-09 | T-05, T-08, T-11 |
| AC-10 | T-10, T-11 |
| AC-11 | T-10, T-11 |
| AC-11a | T-10, T-11 |
| AC-12 | T-05, T-08, T-10, T-11 |
| AC-12a | T-05, T-08, T-10, T-11 |
| AC-13 | T-05, T-08, T-10 |
| AC-14 | T-05, T-08, T-10 |
| AC-15 | T-10 |
| AC-16 | T-10 |
| AC-17 | T-26 |
| AC-18 | T-10, T-11 |
| AC-19 | T-06, T-07, T-09, T-10, T-26 |
| AC-20 | T-10, T-11, T-24 |
| AC-20a | T-26 |
| AC-20b | T-26 |
| AC-21 | T-25 |
| AC-22 | T-25 |
| AC-23 | T-25 |
| AC-24 | T-25 |
| AC-25 | T-23（已完成） |

**未覆盖项**：无。31 条 AC 全部有归属任务。

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认，再记录。

- **T-03 偏离 → 已更新 design §4.3**（2026-09-21 实现期）：树节点构造点是 **3 处**而非 2 处——除 `_dept_node_dict`（`department_service.py:280`）与 pydantic 模型外，`:570` 还有一处自建 `DepartmentTreeNode`（非懒加载树端点）。漏掉它会该端点静默丢字段而懒加载树照常有。
- **T-04 偏离 → 范围扩大 10 倍**（2026-09-21 实现期）：`table_definitions.py` 只是 10 个之一。其余 9 个测试文件各自内联了一份同形的 `department` DDL（`test_department_api/dao/service/sso_dao/init_root_department`、`test_global_members_search`、`test_grant_departments_lazy`、`test_tenant_users_query_source`）。这是本仓「DDL 手工复制」的结构性脆弱点，**任何 `department` 加列都必须全量同步**，否则在 INSERT 时才报错。
- **字段命名偏离 → 已记入迁移 docstring**（2026-09-21 实现期）：需求原文「0-机构，1-部门」，但字段取名 `is_department` 而非 `is_org`——`is_org == 0` 的布尔前缀与取值语义相反，是最易写反的一类命名。`is_department = 0` 读作「非部门 → 机构」，且与本表 `is_tenant_root` / `is_deleted` 的 1=true 房内风格一致。
- **既有缺陷修复（独立于 F062，2026-09-21）**：`department` 的 `concurrent_session_limit`（F030 引入）在 7 个测试 DDL 中缺失，导致 `test/department/` 在 HEAD 上就有 **62 个失败**。已补齐（8 处插入），失败降至 2。此改动与 F062 无因果关系，但**必须修**——否则 T-24 的「F057 回归是否静默退化」分不出基线红与新增红。修复后 `test/department/` + `test/tenant/test_tenant_users_query_source.py`：**229 passed / 2 failed**。
- **E2E 抓到的核心缺陷：候选集未随规则放宽（2026-09-21）** —— `_resolve_visible_department_chain` 返回的是用户的**祖先链**（F057 语义），而 F062 的机构域规则还能到达**兄弟部门**（同一机构内），那些库的 `department_id` 根本不在链上，SQL 阶段就被排除，逐行判定再正确也没有机会评估。**现象：角标说 3、列表返回 2**——角标走「对全部归属库做权限收口」，所以是对的，恰恰反证了候选集是错的。修复：`_user_knowledge_filters` 新增 `include_any_department`（默认 **False**，既有调用方 SQL 一字不变），由 `get_knowledge` 传 `True`（design 决策 7 的超集口径）。回归测试 `test_f062_candidate_superset.py`（5 用例，经变异验证）。<br/>**为什么 74 个单测全漏**：它们**全部在测规则**（给定映射，D 对 U 可见吗），规则从第一天就是对的；没有一条测**候选集**。这是 E2E 才能发现的缺陷类型。<br/>**另记一条操作教训**：验证期间后端进程启动于 17:00 而改动在 17:59，跑的是旧代码，一度把「服务端未重载」误判成「写入路径有缺陷」。**验证前先确认被测代码正在运行**。
- **已知残留（未修，不在本特性范围）**：上述 2 个失败是 `test_department_service.py` 里 5 处遗留的 `member_count` 断言（`:475/:972/:974` 等），而 `member_count` 已被 T014 刻意从 `DepartmentTreeNode` 移除（有专门的 `test_department_tree_no_member_count.py` 断言其移除）。**与 F062 无关**，属 T014 那次遗漏的测试清理，建议另行处理。

### Wave A 实现记录（2026-09-21）

**T-07 落点选型 → 已定：`bisheng/permission/domain/services/department_org_domain.py`（新模块）**

design §4.3 曾把「permission 域不得 import DAO」列为候选 A 的约束。**该前提经查证不成立**：arch-guard 无此规则（RULE-4 只约束 `domain.models → domain.services`），且 `database/models/*` 是共享层——`permission/domain/services/application_permission_service.py:102` 已有直接 import `DepartmentDao` 的同类先例。故选落点 A，缓存与纯函数同置，无新跨模块依赖。**design §4.3 的「待定稿」条目可关闭。**

**偏离：F057 一个既有用例的数据是错的（已修，语义未变）**

`test_f057_department_ownership_implicit.py::test_ancestor_of_owning_department_gets_nothing` 给「归属部门」和「用户部门」**同一个 id=30**，只在 `path` 上假装父子（`owning=_dept(30,"/1/30/31/")` vs `user=_dept(30,"/1/30/")`）。F057 比较 `owning.path` 故碰巧通过；F062 比较 `nearestOrg(30).path`＝自身的 `/1/30/`，用户自己命中自己。**改的是 id（30→31，与注释一致），不是断言**——改后新旧规则都判不可见。另为该文件补了同源的 mapping stub（原本只 mock F057 的 DAO，F062 会让它真的去查库）。

**偏离：`_resolve_org_domain_path_uncached` 为计划外新增**

design 只在决策 6 ③ 描述「超大租户退化到逐级回溯」，未点名落点。实现为 `PermissionService._resolve_org_domain_path_uncached`（纯只读、带 256 步环上界），由 `_resolve_org_domain_path` 在 mapping 为 None 时调用。属决策 6 ③ 的具体化，非新决策。

### Wave A 测试与变异验证

新增 3 个测试文件、**73 个用例**（T-05 15 + T-06 8 + T-10 32 + F057 既有 10 等）。**两处 canary 经变异验证确认真实有效**：

| 变异 | 预期 | 实测 |
|---|---|---|
| 「自身是机构返回自身」→「只找严格祖先」 | canary 失败 | ✅ T-05 红 3 个；T-10 红 **10** 个 |
| 去掉嵌套机构剪枝（机构也向上归并） | 屏障用例失败 | ✅ T-10 红 **9** 个（含 AC-20「全机构退化为 F057」） |
| 缓存键去掉租户维度 | C3 用例失败 | ✅ T-06 红 **2** 个（跨租户） |

**Wave A 回归结果**：`test/permission/` + `test/department/` = **828 passed / 30 failed**。逐条定性后，**无一条由 F062 引入**：

| 失败 | 数量 | 定性 |
|---|---|---|
| `test_department_service.py` 的 `member_count` 残留 | 2 | 既有的 T014 遗漏（见上） |
| `test_permission_service_f013.py::test_is_shared_to_true_when_fga_allows` | 1 | **既有**——该用例测 `_is_shared_to` + FGA mock，**不碰 F062 任何代码路径**；失败在 `assert_awaited_once_with`（多调用一次）。单跑与全量跑同样失败 |
| `test_permission_enrichment.py` 两个 | 2 | **测试间污染**——单跑通过、全量跑失败，与 F062 无关 |

> 合计 5 个真实失败（`--tb=no` 的 FAILED 行是 5 条，其余 25 行是 warnings；先前误读为 30 个）。
