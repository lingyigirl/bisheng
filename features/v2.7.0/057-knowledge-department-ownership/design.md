# Design: 知识库部门归属（057-knowledge-department-ownership）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动
>
> 调整原则（详见 `docs/SDD-Guide.md` §3-§4）：
> - 实现变化 → **覆盖更新本文档**，只留"今天的状态"、不留旧快照；但每个决策保留"为什么 + 被否方案"和坑（护栏，见 §3/§5）
> - **偏差分级**：推翻已 ★ 确认的决策 → 停下与用户重新确认；纯实现细节 → 直接改 design，不必停

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-16（反向补档：实现先于文档存在，本文档由 2026-09-15 的代码与 2026-09-16 的走查结论回填）

---

## 1. 目标与非目标

- **目标**：让组织知识库（文档库 / 问答库）归属于平台部门树上的**一个**部门，使该部门及其**各级下级部门**的成员可读取。归属判定发生在**读取时**，不写入任何授权元组，因此部门调整（改名、移动、合并）后可见性自动跟随，无需重算。
- **非目标**：
  - 不实现多部门归属（一库一部门）。
  - 不让归属影响**写**权限——归属只追加一条读取来源，编辑/删除/管理仍走既有权限体系。
  - 不改造知识空间（`knowledge_space`）与个人库的既有可见性口径，二者完全不受本 feature 影响。
  - 不为存量部门授权元组做自动清理（属迁移前置运维作业，见 §8）。

---

## 2. 关键约束

本功能特有的硬性约束（决定 §3 方案对比的取舍空间）。

- **OpenFGA 表达不了"向下传递"**：`department.member` 是扁平关系（`core/openfga/authorization_model.py:237`），既无继承也无下钻；唯一有继承的关系 `admin` 是通过 `tupleToUserset(parent, admin)` **向上**走的。因此"祖先部门的库对后代可见"这个方向，FGA 模型本身无法表达——这是决策 1 的根本原因。
- **没有部门变更钩子**：全仓库没有任何"部门创建/移动/归档时重算资源授权"的逻辑。任何把子树展开成元组的方案，都必须在没有钩子的前提下自行保证正确性。
- **`department.path` 是物化路径**：形如 `/1/2/3/`，**最后一段是节点自身 id**，且在移动时由 `update_paths_batch`（`database/models/department.py:529-556`）整体重写。注意 `department_service.py:1512` 有一条**陈旧注释**声称最后一段不是自身 id，那是错的，不要照它改代码。已归档部门**仍在 path 中**（归档是软删，行还在）。
- **全局约束**：遵循 `docs/constitution.md` C1–C7。本 feature 不新增错误码（复用既有 HTTP 错误信封），不新增权限档位，不新增领域对象归属。

---

## 3. 方案对比与选定

### 决策 1：部门归属的可见性用**读时求值**，不写 FGA 元组

- **备选**：
  - A. **读时求值**：`knowledge.department_id` 只存归属，判权时按 `department.path` 前缀比较实时计算。读取路径多一次部门查询，但没有一致性窗口。
  - B. **写入时把子树展开成元组**：给部门 D 的每个后代都写一条 `knowledge_library#viewer` 元组。判权走既有 FGA，零改动。
  - C. **改 FGA 模型**：给 `department.member` 增加继承关系，让 FGA 自己表达下钻。
- **选定**：A
- **原因**：B 有两个**在现有代码下无解**的缺陷——(1) **重复授权泄漏**：部门被移动到别处后，旧子树展开的元组仍然存在，而全仓库没有部门变更钩子去撤销它们，等于知识库的可见范围只增不减；(2) **新建部门漏授权**：展开时还不存在的子部门，之后被创建时不会有人补元组。此外存量元组已经造成过实际问题（见 §8 的审计脚本）。C 需要改授权模型并全量重算，且 DM8 侧的模型同步时机不受本 feature 控制，风险远超收益。A 把正确性建立在 `path` 上——`path` 在移动时被整体重写，天然免疫上述两类漂移。
- **何时该重新考虑**：若部门树深度极大（前缀比较退化为长字符串比较）或读时求值的额外查询成为热点；若未来引入部门变更钩子，B 的重复授权问题被解决，可重新评估缓存化。

### 决策 2：`knowledge.department_id` 可空，NULL = 未分类（不是"全局可见"）

- **备选**：
  - A. **可空字段**，NULL 表示未分类，无归属则不产生任何部门派生可见性。
  - B. 非空 + 默认值（如 0 表示"根部门"）。
- **选定**：A
- **原因**：存量知识库全部没有归属。A 让迁移只需加一列，**所有存量行的有效可见性保持不变**（未分类 = 不加任何东西），这是本次变更对生产安全的核心。B 会让"未分类"与"归属于 0 号部门"语义混淆，且默认值会凭空给存量数据一个部门归属。同时**刻意不设 `server_default`**——设了默认值就等于给每一行一个"被默认归类"的含义。
- **何时该重新考虑**：若产品要求"组织知识库必须有归属"（如强制分类），则改为非空并在创建流程里落归属。

### 决策 3：ON DELETE 不加外键，改由应用层在部门删除/归档时清空

- **备选**：
  - A. 加 `FOREIGN KEY ... ON DELETE SET NULL`。
  - B. 应用层清理：部门删除/归档时 `UPDATE knowledge SET department_id = NULL WHERE department_id = ?`。
- **选定**：B
- **原因**：A 有三重阻力——(1) 本仓库**没有在既有表上 `ALTER TABLE ADD CONSTRAINT FOREIGN KEY` 的先例**（f021/f026 的外键都是在 `create_table` 里建的），而 `knowledge` 是大而热的多租户表，首次在它上面加约束是全局风险；(2) DM8 是 Oracle 兼容模式，`ON DELETE SET NULL` 在 `ALTER TABLE ADD CONSTRAINT` 下的支持与标识符行为需 DB 团队验证（`env.py` 已为 DM8/`dmSQLAlchemy` 打过若干反射补丁，说明该路径坑多）；(3) 应用层已经**必须**知道部门被删这件事——清理成员关系、部门角色、管理员授权都在同一处发生，加一条 UPDATE 是顺手的事。
- **代价与兜底**：悬空的 `department_id` 会在读时求值里**安全降级**——查不到部门 → 不产生授权 → 等同未分类（`test_f056_department_ownership_implicit.py:154` 已钉住）。因此即使清理漏跑，也只是留下一个无害的孤儿 id，不会放大权限。
- **何时该重新考虑**：若 DB 团队认定需要 DB 级约束，则在§8 的存量清理跑完后，另起迁移补 FK。

### 决策 4：归属可见性与平台筛选是两个**相反**的参数，不得合并

- **备选**：
  - A. 两个独立参数：`department_ids`（可见性，OR 进候选集）与 `filter_department_ids`（筛选，独立 AND）。
  - B. 复用一个参数，靠调用场景区分语义。
- **选定**：A
- **原因**：二者语义相反且都必需。可见性参数是**加性**的：`可见 = FGA 授权 OR 本人创建 OR 部门归属`，用 OR 保证「今天能看到的，明天仍然能看到」；如果误用 AND，**所有未分类的存量库会立刻对所有人消失**。筛选参数是**收窄**的：用户在树上点了某部门，就是要排除其他部门，必须 AND。A 用两个名字把这对反义关系显式化，并有编译期 SQL 断言钉住（`test_f056_department_candidate_union.py`）。
- **何时该重新考虑**：若未来出现"既要收窄又要保留归属可见性"的第三种场景，再评估是否需要第三个参数。

### 决策 5：筛选路径的空结果用 isEmpty 谓词，不用"不筛选"

- **备选**：
  - A. 选中的部门已不存在/无 path → 返回**空 filter 列表**，编译成恒假谓词 → 列表为空。
  - B. 同样情况 → 返回 `None`（不筛选）→ 列表为全量。
- **选定**：A
- **原因**：B 是一个**静默的权限越界**：前端部门树是懒加载的，用户可能选中一个刚被删除的节点，此时若降级为"不筛选"，页面会把**整个租户的知识库**都列出来，而用户以为还在看某个部门。宁可空列表也不放宽。
- **何时该重新考虑**：不需要。这是安全默认。

### 决策 6：归属维护需要 `manage_kb_*`，且非管理员受子树作用域约束

- **备选**：
  - A. 归为元数据编辑，用 `edit_kb`。
  - B. 归为授权变更，用 `manage_kb_*`（`can_manage` 档），非管理员只能指派到自己子树内的部门。
- **选定**：B
- **原因**：设置归属会**把知识库交给一整个部门及其后代**——这是授权行为，不是改个名字。若用 `edit_kb`，一个只有编辑权的用户就能把库"送"给任意部门，等于权限提升。子树约束则与 `knowledge_space` 部门授权的既有作用域规则保持一致。
- **作用域方向（易错，已踩过一次）**：允许的是**自身 + 下级**，禁止的是**上级与旁支**。判定是物化路径前缀比较，方向极易写反——「target 在 actor 的子树内」写作 `target.path.startswith(actor_path)`（用 **actor 的** path 作前缀）。初版写成了 `actor_path.startswith(target_path)`，含义是「target 是 actor 的祖先」，于是**允许把库发布到根部门（对全租户可见）、却拒绝assign给自己的下级部门**——既扩大了授权面又破坏了核心用例，且与注释、本文档、spec 三处表述全部相反。由 code-review 发现；修于 `test_f057_department_assignment.py::test_manage_holder_cannot_assign_to_an_ancestor`。
- **何时该重新考虑**：若产品引入"部门负责人"这一独立角色并希望其自助指派，需重新设计门槛。

### 决策 7：归档部门同样清空归属（与删除一致）

- **备选**：
  - A. 归档 = 失效：归档时一并清空归属，与硬删同一条清理路径。
  - B. 归档保留归属：只修筛选路径的状态过滤，让归档节点仍可被选中。
- **选定**：A
- **原因**：本 feature 走查时发现两处行为**互相矛盾**——筛选路径的子树解析只收 `status='active'` 的部门（`aget_subtree_ids`），而权限路径按 `path` 前缀授权、不筛状态，于是已归档部门**选不中却仍授权**。B 会让归档部门持续授权，与"归档即撤销成员关系、部门角色、管理员授权"的既有口径（`DepartmentArchiveCleanupService` 的文件头注释）直接相悖。A 让两条路径自然统一：归档行不再携带归属，读时求值无从授权。
- **何时该重新考虑**：若业务要求"归档只是临时下线、恢复后归属还在"，则需要引入独立的归属存档而不是清空。

### 决策 8：「全部」放在树顶部，不保留独立筛选文字条

- **备选**：
  - A. tab 上方一条「当前筛选：X / 清除」文字条（初版实现）。
  - B. 树顶部一行「全部」，选中态由树内高亮行承载。
  - C. 都不做，页面永远限定在某部门内。
- **选定**：B
- **原因**：A 被用户明确否决——它占用列表上方一整行，而"当前在看哪个部门"这件事**树里已经用高亮行表达过了**，文字条是冗余。但不能因此直接删掉了事（即 C）：树里既没有「全部」也没有「未分类」节点，且页面进来自动选中主部门，删掉文字条就等于让用户**再也回不到全量视图**，连未分类的库都看不到。B 把"回到全部"放回它语义所属的位置——它本就是一种"选中范围"，与选中某个部门并列，而不是一个额外的操作按钮。
- **实现注记**：「全部」行渲染在**页面里**、而非 `LazyDepartmentTree` 内部。因为树的选中态按 `dept_id`（`BS@...` 业务键）比对，而"全部"不是真实部门、没有 `dept_id`；且该树组件另有 3 个调用方（部门管理页、选择器、导航树），不应为单一页面长出假节点。
- **管理员豁免自动选中（2026-09-16 补充）**：初版对**所有人**都自动选中主部门，包括 System Admin。而本部署的 admin 账号落在**访客部门**（`user_department` 指向 `BS@guest`），该部门名下 0 个知识库 → 管理员一进知识库页就被筛成空列表，尽管其权限完全正常（后端对 admin 的 `rebac_list_accessible` 返回 `None`，即不筛选）。管理员改用「全部」作为默认——管理员本就不该被"他属于哪个部门"限定。普通成员行为不变。
- **何时该重新考虑**：若树组件将来支持"虚拟节点/根节点"概念并被多方复用，可把「全部」下沉进组件；若产品要求页面必须限定在单一部门内，则 C 成立、本行可删；若产品认为管理员也应默认看到自己的部门，则移除豁免。

### 决策 10：部门树对所有人返回**完整租户树**（不再按成员身份裁剪）

- **备选**：
  - A. 只返回调用者「所属部门 + 祖先」（决策 9 初版）。
  - B. 返回完整租户树，所有人一致。
- **选定**：B
- **原因**：A 的立论是「链 = 该用户可见的库所属部门集合」——这没错，但它把**浏览**和**授权**混为一谈。后果是树无法用于浏览组织：本部署有 7 个部门，而 System Admin（其部门关系指向访客部门）只能看到 **2 个节点**，且默认筛选还因此把他筛成空列表。用户明确要求「树形数据应该是全部的部门数据」。
  
  改为 B 后权限由什么保证？**逐行判定**：筛选只是收窄一个视图，从不授权——`filter_department_ids` 是独立 AND，且每行仍走 `view_kb` 检查。选中一个自己读不到任何库的部门 → 空列表，与「该部门确实没库」不可区分（这正是 AC-13 要的效果：不泄漏他部门的库存）。
- **连带改动**：搜索也必须改为**服务端**。A 时代链只有几个节点，前端本地过滤尚可；B 之后树是全量，本地过滤只能看到用户已展开的层——搜一个折叠节点下的部门会假报「无结果」。新增 `GET /knowledge/my_departments/search`（复用 `aget_by_name_like` 的 `path_prefixes=None` 非限定模式，租户隔离仍由 tenant-filter hook 保证）。
- **`has_children` 改由 DAO 查询而非链内推导**：全量树里一个节点可能只有已归档的子节点，链内推导会给出一个展开后什么都没有的箭头。
- **何时该重新考虑**：若多租户下需要隐藏子租户挂载节点（`mounted_tenant_id` 非空），B 需要在服务端加过滤；若组织规模大到整棵树下发的成本不可接受，需要回到按需裁剪——但那时应保留一个显式的"浏览全组织"入口，而不是像 A 那样直接砍掉。

### 决策 9：筛选用**自有专用端点**，不用 `/departments/*`

> 本决策的**授权模型**在决策 10 中被修正：初版按成员身份裁剪，现改为返回完整租户树。保留此条是因为「为什么不能用 `/departments/*`」与「为什么不用 grant-subjects」的论证仍然成立。

- **备选**：
  - A. 直接用 `GET /api/v1/departments/{children,search,path-tree}`（初版实现）。
  - B. 复用 `GET /api/v1/permissions/resources/{type}/{id}/grant-subjects/departments/*`。
  - C. 新增 `GET /api/v1/knowledge/my_departments`，仅返回调用者自己的部门链。
- **选定**：C
- **原因**：A **对目标用户根本不可用**，且这是上线后由用户报告才发现的。`/departments/*` 是**部门管理**接口族，F038 给它的可见性是三层**管理**身份——系统管理员 / 部门管理员 / 租户管理员——**普通部门成员不属于任何一层**，一律 21009。而知识库部门筛选器的受众恰恰就是这些普通成员（"我属于哪个部门"），所以初版实现让每个普通用户一打开知识库页就报权限错误。
  
  B 看似对症（F038 文档称其可见性为"租户根子树"、面向更广），但它要求 URL 里带**具体的资源实例**，且要求调用者对该资源有 `can_edit`（`grant_subject_query_service._resource_query_context`）；知识库管理页没有资源实例，这条路走不通。其部门路径解析也**只支持 `knowledge_space`**（`grant_subject_query_repository.resolve_department_space_path` 对其他类型直接返回 `None`）。
  
  C 自成一套：只要求登录，与管理身份正交，不触碰既有端点、不影响其他调用方（其可见性范围在决策 10 中定为完整租户树）。
- **~~返回的链是精确的~~（已被决策 10 取代）**：初版内容 = 调用者所属部门 ∪ 其全部祖先，理由是它「恰好等于该用户可见的知识库所属部门集合」。该推理本身没错，但它混淆了**浏览范围**与**授权范围**——见决策 10，现改为返回完整租户树。
- **前端连带**：`path-tree` 不再被调用（`reveal()` 弃用，预选主部门改为直接高亮）；`search` 最终改为**服务端**实现（见决策 10），而非初版的本地过滤。
- **例外：仍有一处 `/departments/*` 调用**。首屏预选主部门走 `GET /departments/get_user_primary_department`（`controllers/API/department.ts:101`）。它不属于管理范围校验的那三个端点——`department.py:141-163` **不检查部门管辖范围**，只回调用者自己的主部门（`user_id` 缺省取登录人），因此普通成员可用。§5 坑 17 里"`/departments/*` 一律 21009"的说法对这一条不成立，例外就是本 feature 自己放宽的那个端点（见 §4.2）。
- **何时该重新考虑**：若将来 `/departments/*` 增加"成员可见自己部门链"的分支并被其他页面复用，可改回统一口径；若部门树规模远大于"自己 + 祖先"（如需要浏览全公司的下级部门），则本端点不够用，需要重新设计可见性模型。
- **回归护栏**：`test/e2e/test_e2e_f057_my_departments.py` **刻意同时断言两个端点**——同一普通成员 token 下，旧端点必须继续返回 21009、新端点必须 200。只断言新端点无法证明旧端点不适用；只断言旧端点无法证明修复生效。若有人放宽了 `/departments/*` 的可见性，该用例会失败。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**读（列表）**：请求带 `department_id` → 端点 `_resolve_department_filter` 把该部门展开成子树 id 列表（基于 `path`）→ 传入 `KnowledgeService.get_knowledge` → 两条路径分叉：

- **普通用户走 ReBAC 分支**：候选集 = `{FGA 可读 id} ∪ {本人创建} ∪ {部门链上的库}`（第三个并集由 `_resolve_visible_department_chain` 提供，把用户各条 `path` 上的所有 id 取出）→ 逐行权限检查 → 其中 `PermissionService._implicit_department_library_level` 对归属库返回 `can_read`。
- **管理员走 DAO 直查分支**：`KnowledgeDao.aget_all_knowledge`，逐行权限检查被跳过（管理员本就全可见）。

**读（徽标）**：`GET /department_stats` → `KnowledgeDepartmentService.department_stats` → 先按权限**收口**可见 id → 再按 `path` 前缀把每个库累加到其祖先部门 → 返回 `[{department_id, count}]`。

**写（归属）**：`PUT /department` → `KnowledgeDepartmentService.assign_department` → 校验（库存在、类型可指派、有管理权限、非管理员限子树）→ 写 `knowledge.department_id`。**不写任何 FGA 元组**。

**删除联动**：部门删除/归档 → `DepartmentArchiveCleanupService._collect_and_delete_mysql_refs` → 清理成员关系、部门角色、管理员授权、**以及知识库归属**。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `knowledge.department_id` | `INTEGER NULL`，带索引 `ix_knowledge_department_id` | 所属部门；NULL = 未分类。**无外键**（决策 3） | 读时求值、列表筛选、徽标统计 |
| `department.path` | 物化路径 `/1/2/3/`，尾斜杠结尾 | **最后一段是节点自身 id**；移动时整体重写。尾斜杠是前缀比较的关键（`/1/2/` 不能匹配 `/1/20/`） | 前缀比较、子树展开 |
| `GET /api/v1/knowledge` 的 `department_id` | 整数查询参数，可选 | 按部门筛选，**只筛选不授权**；调用方仍需对每行有 `view_kb` | platform 知识库管理页 |
| `GET /api/v1/knowledge` 的 `include_sub_departments` | 布尔，默认 `true` | 配合上一参数，是否含子树 | 同上 |
| `GET /api/v1/knowledge/department_stats` | 返回 `[{department_id, count}]` | **子树包含**：父节点计数含后代；无归属的库不出现 | 部门树徽标 |
| `PUT /api/v1/knowledge/department` | body `{knowledge_id, department_id\|null}` | 设置/取消归属。HTTP 200 + 信封 `status_code=403` 表示被拒 | 管理入口（本期无 UI） |
| `GET /api/v1/departments/get_user_primary_department` | 返回 `[{id, dept_id, name}]` | `user_id` 已改为**可选**，缺省取当前登录人；无主部门返回 `[]` | 页面首屏预选部门树节点 |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `permission_service.py::_implicit_department_library_level` | 读时判定归属库的可见性（祖先或自身 → `can_read`） | 不写元组；不处理 `knowledge_space` |
| `permission_service.py::_implicit_department_space_member_level` | 按 `object_type` 分发；`knowledge_space` 分支保持原有"本部门精确匹配、不含子树"语义 | 两个分支语义**有意不同**，勿合并 |
| `knowledge_service.py::_resolve_visible_department_chain` | 解析用户可见的部门链（各 `path` 上的全部 id），供候选集并集使用 | 不做权限判定；失败返回 `[]` 降级 |
| `knowledge_service.py::get_knowledge` | 列表编排：分叉 ReBAC 分支与管理员分支 | — |
| `knowledge_department_service.py` | 归属写入的服务层：权限门槛、子树作用域、存在性校验；以及徽标统计 | 不直接写 ORM 查询以外的东西；不做前端展示 |
| `knowledge.py::_resolve_department_filter` | 把选中的部门展开成子树 id 列表 | 不授权，只产出筛选 id |
| `department_archive_cleanup_service.py` | 部门归档/删除时的 MySQL 引用清理 | 不做 FGA 清理（由 `DepartmentChangeHandler` 负责） |
| `useKnowledgeDepartmentFilter.ts` | 前端：左侧部门树、当前选择、各节点计数；数据源为 `knowledge/my_departments`（**完整租户树**），搜索走服务端；管理员不自动选部门 | 不渲染列表；列表由两个 tab 组件各自消费；**不调用 `/departments/*`** |
| `KnowledgeDepartmentService.browse_departments()` / `search_departments()` | 后端：逐层返回**完整租户树** / 服务端名称搜索，供知识库页的筛选树使用 | 不做任何权限判定（授权在列表逐行进行）；不写任何数据 |

---

## 5. 已知坑 / 反直觉事实

> 本 feature 大量实现先于文档存在。以下是 2026-09-16 走查**逐条验证过**的反直觉点，含已修复项与仍然存在的项。

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | **`department.path` 最后一段是节点自身 id**，但 `department_service.py:1512` 的注释说不是 | 按注释写比较逻辑会整体错位一格，祖先判定全错 | 以 `_parse_path_ids` 的实现为准；陈旧注释待清理 |
| 2 | **尾斜杠是前缀比较的载荷**：`/1/2/` 必须不能匹配 `/1/20/` | 去掉尾斜杠会让 2 号部门的库对 20 号部门可见（跨部门越权） | `_implicit_department_library_level` 的前缀测试；`test_f056_department_ownership_implicit.py:206` 钉住 |
| 3 | **平台请求拦截器已解包 `response.data.data`**（`controllers/request.ts` 对 `status_code===200` 直接返回内层） | 再写 `res?.data` 会恒为 `undefined`——计数徽标曾因此**整条链路静默失效** | 已修复；`useKnowledgeDepartmentFilter.ts` 现直接使用解包值 |
| 4 | **树的选中态比较用的是业务键 `dept_id`（`BS@...`），不是数值 id** | 传数值 id 会导致**高亮永不出现**，用户看不出当前选中哪个部门 | 已修复；`LazyDepartmentTree.tsx:56` 是判据，`index.tsx` 改传 `dept_id` |
| 5 | **`useInfiniteCursorTable.filterData` 是 `paramsRef` 浅合并**，传 `{}` 不会删除既有 key | 清除筛选后若改成透传整个 `param`，会出现"筛选已清、结果仍被过滤"的幽灵 bug | 当前无害（部门参数走闭包而非 `paramsRef`），但是**改动时的雷区** |
| 6 | **管理员分支与普通用户分支是两套 DAO** | 只给用户分支加参数会导致**管理员筛选静默失效**（列表纹丝不动、无报错） | 已修复；`aget_all_knowledge` 与 `generate_all_knowledge_filter` 一并透传 |
| 7 | **`aget_subtree_ids` 只返回 `active` 部门，`aget_by_id` 不筛状态** | 归档部门"选不中却仍授权"的行为矛盾 | 已按决策 7 统一：归档即清空归属 |
| 8 | **复制知识库会连 `department_id` 一起复制**（`model_dump` 路径） | 副本会对源库的归属部门成员可见——这是**有意**的行为（AC-25），不知情者会当 bug 修 | `copy_knowledge` / `copy_qa_knowledge`；改动前先读 AC-25 |
| 9 | **徽标统计用的是 `type=0`，而 QA 列表是 `type=1`** | 问答库 tab 下的徽标数字与右侧列表对不上 | 前端恒定发 `type: 0`；未修，见 §8 |
| 10 | **`department_stats` 的 `type` 参数非法值会冒泡成 500** | 非法 `type` 得不到 4xx 而是服务端错误 | `KnowledgeTypeEnum(knowledge_type)`；未修，见 §8 |
| 11 | **筛选请求会先发一次不带筛选的**：hook 首次加载发生在主部门 effect 解析之前 | 列表会短暂闪出全量结果，随后被筛选结果替换 | `useKnowledgeDepartmentFilter.ts`；未修，见 §8 |
| 12 | **`includeSubDepartments` 的 setter 全仓库无人调用** | 看似可切换，实为死 UI；`false` 分支只有接口层可达 | 已暴露但未接线；未修，见 §8 |
| 13 | **`if knowledge_id_extra:` 是真值判断**——部门归属的 OR 子句曾放在该分支内 | 典型部门成员无 FGA 授权、无自建库 → 该值为空 → **归属子句被整个跳过，功能对主要受众彻底失效**。已修复：归属判断提为独立条件 | `models/knowledge.py::_user_knowledge_filters`；`test/knowledge/test_f057_department_candidate_union.py` 钉住 |
| 14 | **断言编译后 SQL 字符串的单测会把错误行为固化成「预期」** | 坑 #13 存在期间，7 个 SQL 断言测试**全部通过**——其中一条显式断言了"归属不该在此生效"，正是 bug 本身。写此类测试时要问「这条断言保护的是需求还是现状」 | `test_f057_department_candidate_union.py`；该用例已更名为 `..._apply_even_without_an_extra_grant_list` |
| 15 | **`GET /departments/search` 返回 `{"roots": [...]}` 而非列表** | 按列表解析会恒得空集，清理/E2E 辅助逻辑静默失效（曾致 56 个部门残留） | `test/e2e/test_e2e_f057_knowledge_department.py::_purge_prefixed` |
| 16 | **归档部门有前置条件：零成员 + 无 active 子部门** | 历史残留部门若仍带成员，永远删不掉，逐轮累积 | `department_service.py::_ensure_department_can_archive`；E2E 清理先移除成员再归档 |
| 17 | **`/departments/*` 是部门管理接口，普通成员一律 21009**——其可见性是三层**管理**身份（超管 / 部门管理员 / 租户管理员），**普通成员不在任何一层** | 拿它做普通用户的部门选择器 → **每个普通用户一打开页面就报权限错误**。F057 初版正是这样，上线后由用户报告才发现。与「部门树看起来是公共资源」的直觉相反 | 改用 `my_departments`（决策 9→10）；`useKnowledgeDepartmentFilter.ts` 顶部注释；`test_e2e_f057_my_departments.py` 用**同一 token** 同时断言旧端点 21009、新端点 200 |
| 18 | **`grant-subjects/departments/*` 不是通用选择器**——它要求 URL 带具体资源实例，且要求调用者对该资源有 `can_edit`；其部门路径解析**只支持 `knowledge_space`**，其他类型返回 `None` | 以为"F038 说它面向租户子树、应该通用"而选它 → 在无资源实例的页面（如知识库管理页）直接 `PermissionDeniedError` | `grant_subject_query_service._resource_query_context`（权限门槛）+ `grant_subject_query_repository.resolve_department_space_path:517`（仅 space 有路径）；设计取舍见决策 9 备选 B |
| 19 | **`captureAndAlertRequestErrorHoc` 对 `silent` 请求仍会弹 toast** | `silent: true` 只让**拦截器**跳过全局处理，它 reject 的是**信封对象**而非 `null`；HOC 的 `error === null` 分支才静默 → 想靠 `silent` 压掉一个预期内的失败提示，会失败。要静默只能**不发起该请求** | `controllers/request.ts:135` 与 `:239-241`；F057 降级方案因此放弃，改由后端新端点从源头消除失败 |
| 20 | **路径前缀比较的作用域方向极易写反**：`target.path.startswith(actor_path)` = target 在 actor 子树内（正确）；反过来写 = target 是 actor 的祖先（错误，会允许发布到根部门） | 写反后**同时**扩大授权面（任意管理者可把库发布到全租户可见）**且**破坏核心用例（无法指派给自己的下级部门），而注释/文档/spec 三处都还写着"子树内"，读代码看不出来 | `knowledge_department_service.py::assign_department` 的 `any(target_path.startswith(path) ...)`；`test_f057_department_assignment.py::test_manage_holder_cannot_assign_to_an_ancestor` 钉住反方向 |
| 21 | **`department_stats` 曾为「调用者看不到任何库」的部门也返回 `count: 0` 条目** | 泄漏部门 id 的存在性（配合"该部门至少拥有 1 个该类库"），与 AC-13 及该端点自己的 docstring 冲突。已修复：返回前丢弃 0 值 | `knowledge_department_service.py::department_stats` |
| 22 | **System Admin 在本部署落在访客部门**（`user_department` → `BS@guest`），而该部门名下 0 个知识库 | 「自动选中用户主部门」这一默认对管理员生效时，会把他的知识库列表筛成空——看起来像"管理员权限不够/看不到全部"，实际是前端默认筛选。管理员权限本身正常（后端对其返回不筛选）。已修：管理员豁免自动选中 | `useKnowledgeDepartmentFilter.ts` 的默认选中 effect（`if (user?.role === 'admin') return`）；见决策 8 补充 |
| 23 | **列表端点 `page_size` 默认 10**，本部署 type=0 有 12 条 | 排查「管理员少看到 2 个知识库」时会误判为权限过滤——实际只是第一页。诊断脚本务必显式传 `page_size` | `knowledge/api/endpoints/knowledge.py:409`；E2E 的 `_list_library_ids` 同理 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `knowledge.department_id` 字段 | DB 列（可空、有索引、无外键） | 本 feature；未来任何按部门盘点的功能 |
| `GET /api/v1/knowledge?department_id&include_sub_departments` | HTTP 查询参数 | platform 知识库管理页 |
| `GET /api/v1/knowledge/department_stats` | HTTP API | platform 部门树徽标 |
| `GET /api/v1/knowledge/my_departments?parent_id&include_archived` | HTTP API | platform 知识库页的部门筛选树（**完整租户树，仅需登录**，见决策 9/10） |
| `GET /api/v1/knowledge/my_departments/search?keyword&limit` | HTTP API | 同上的服务端名称搜索（决策 10） |
| `PUT /api/v1/knowledge/department` | HTTP API | 本期无归属编辑 UI；创建流程会调它做自动绑定 |
| `GET /api/v1/departments/get_user_primary_department` 的 `user_id` 可选化 | HTTP 契约**放宽** | platform 页面预选；Gateway 的原调用方式不受影响 |
| `KnowledgeDepartmentService` | 内部 Python API | `knowledge.py` 端点 |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `department.path` 的物化路径语义与维护 | 隐式数据契约 | 若未来改为邻接表或 path 语义变更，前缀比较全部失效 |
| `UserDepartmentDao.aget_user_departments` | DAO | 用户部门关系是可见性的输入 |
| `PermissionService` 的五级短路与隐式回退位 | 内部 API | 归属判定挂在"FGA 判否后的隐式回退"上；若短路口径调整，本 feature 的生效点随之改变 |
| `DepartmentArchiveCleanupService` 的调用时机 | 内部 API | 若部门删除/归档改走别的路径而不经过该服务，归属清空会被漏掉 |
| 平台请求拦截器的解包行为 | 隐式前端契约 | 拦截器一旦改变解包层级，前端取值需同步（坑 #3） |

---

## 7. 测试与可观测

- **单元 / 集成**：4 个新增测试文件，共 32 个用例。
  - `test/knowledge/test_f056_department_assignment.py`（9）——归属写入的权限门槛、作用域、类型限制、异常路径。
  - `test/knowledge/test_f056_department_candidate_union.py`（7）——**编译期 SQL 断言**，钉住"可见性 OR / 筛选 AND"这对反义参数，含空筛选必须恒假。不需要数据库，可离线跑。
  - `test/permission/test_f056_department_ownership_implicit.py`（10）——读时求值的全部方向性：后代可见、自身可见、**祖先不可见**、兄弟不可见、悬空 id 安全、prefix 不跨兄弟 id。
  - `test/permission/test_relation_model_template_snapshot.py`（6）——关系模型快照语义（相邻主题，归属变更依赖权限模板解析）。
- **缺口**：端点层（`_resolve_department_filter`、两个新端点、`user_id` 可选化）目前**无测试覆盖**。
- **手动验证**：以普通用户身份在知识库管理页选部门节点，确认列表收窄、徽标数字与列表条数一致、选中行高亮；再以管理员身份重复一次（验证坑 #6 已修）；最后删除一个部门，确认其名下知识库回到未分类。

---

## 8. 后续改进 / 不打算做的事

**已知短板（本期明确不修，理由随附）**

| 短板 | 不修的理由 |
|---|---|
| 无归属编辑 UI（只能调接口） | 超出本期范围；筛选视图已可用，编辑 UI 另起任务 |
| 树里没有「未分类」节点 | 产品决定，见 spec 已知缺口 |
| 徽标恒用 `type=0`，QA tab 下对不上 | 属展示一致性问题，非权限问题；改动需前后端一起定口径 |
| `department_stats` 非法 `type` 返回 500 | 应改为 4xx；属既有错误处理口径问题，牵连面广 |
| 首屏多一次未筛选请求 | 性能优化项，非正确性问题；需重排 hook 的加载时序 |
| `includeSubDepartments` 是死 UI | 未接线；接线需先定交互（是否需要"仅本部门"） |
| 存量部门授权元组的清理 | 已提供**只读审计脚本**（把元组分成 shadowed / redundant / orphan / independent 四类），撤销与否是人工决策——部分元组可能仍在承载访问 |
| `department_stats` 无界扫描 + 逐行鉴权（code-review F2-3） | 一条查询取全租户该类型全部库 id，再逐个鉴权（列表路径有 50/批的上界，此处没有）。任何登录用户可反复调用 → FGA 放大。修需定"徽标要覆盖多少库"的口径，属设计问题不是补丁 |
| 归属可指派到**已归档**部门（code-review F2-1 残留） | `assign_department` 校验存在性与 path，不校验 `status`。后果是读时仍授权（归档节点只删了自身成员关系），且该节点在筛选树里选不中。作者已知（`purge` 有兜底注释）但未在文档声明；修需先定"归档部门能否被指派"的产品口径 |
| `department_stats` 返回的节点字段面偏宽（code-review F4-3） | 复用管理端的 `_dept_node_dict`，含 `mounted_tenant_id` 等租户拓扑字段。筛选器只需要 `id/dept_id/name/has_children`；收窄需新增精简节点类型 |
| 权限缓存的**后代成员**残留窗口（code-review F2-7） | 已在指派/清空路径定向失效**直属成员**；子树展开成本远高于它消除的 ≤10s 窗口（TTL 上界）。属既有模式 |

**已在 code-review 中修复，不列为短板**（保留在此仅为对照）：

| 项 | 修复 |
|---|---|
| 作用域方向写反（F2-1） | `assign_department` 曾允许指派到**祖先**（含根部门）却拒绝指派到下级——前缀比较方向反了。已修，见决策 6 与坑 20 |
| `department_stats` 泄漏 0 值部门（F2-2） | 已改为丢弃 `count == 0` 的条目，见坑 21 |
| 归属变更不失效权限缓存（F2-7 主因） | 指派与清空路径现调用 `_invalidate_department_members` |
| 清理与部门变更不同事务（并发） | `apurge_department` 的清理移到 commit 之后；两条路径语义一致 |
| `department_chain_for_paths` 死代码 + 逻辑内联复制两遍 | 已改为共用（`knowledge_service` 与 `my_departments` 都调用它） |

**触发条件**

- 若引入**部门变更钩子**（部门创建/移动/归档时统一重算资源授权），决策 1 的备选 B 重新具备可行性，可评估把读时求值改为物化元组以换取查询性能。
- 若产品要求**多部门归属**，需把 `department_id` 改为关联表，并重新设计可见性并集语义。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-16 | 初版 | 反向补档：实现先于文档存在，本文档由代码走查回填，同期修复 4 处走查确认的缺陷 |
| 2026-09-16 | §3 增决策 8；§5 增坑 13–16 | E2E 抓出可见性/管理员筛选两处静默失效；用户要求移除筛选文字条，改为树顶「全部」 |
| 2026-09-16 | §3 增决策 9；§4.3、§6.1 同步；§5 增坑 17–19 | 用户报告普通成员打开知识库页报 21009。查实为初版误用部门管理端点族（A/B 对照确认非本次引入的回归），改用自有专用端点 |
| 2026-09-16 | §3 增决策 10（修正决策 9 的授权模型）；§5 增坑 22–23；§4.3、§6.1 同步 | 用户要求「树形数据应该是全部的部门数据」。初版按成员身份裁剪导致管理员只看到 2 个节点并被默认筛成空列表；改为完整租户树 + 服务端搜索 |
| 2026-09-16 | §3 增决策 8 管理员豁免说明 | System Admin 在本部署落在访客部门，自动选中主部门会把他的列表筛成空 |
