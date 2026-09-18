# Code Review Report — 057-knowledge-department-ownership

**Review scope**: F057 全部改动 = `git diff HEAD`（1614 行）+ 12 个未跟踪新文件，共 41 项
**Base**: `HEAD`（F057 改动全部在工作区未提交；`--base 045` 会混入整个 v2.7.0 累积，故收窄到 HEAD）
**方法**: 4 个并行审查 agent，覆盖 skill 定义的 7 个维度
**日期**: 2026-09-16

> **2026-09-18 补审说明（必读）**
>
> 本报告写于 2026-09-16 15:25，但此后**三个文件在 15:49–15:51 被继续修改**，并随 `ed68ccfdf`（16:45 起）一并提交：
> `knowledge_department_service.py`、`test_f057_my_departments.py`、`useKnowledgeDepartmentFilter.ts`。
> 因此本报告**未覆盖这三个文件的最终状态**，其「49 passed」也已过期（现为 **52**）。
> 补审结论见文末 §补审（2026-09-18）—— **⚠ 初版补审结论「无新增 HIGH/MEDIUM」已被推翻**：第二轮深度补审（同样三个文件，逐层读码 + vitest 探针执行）找到 **1 HIGH + 2 MEDIUM**，见文末 §补审第二轮。

---

## Summary

| 维度 | High | Medium | Low | 状态 |
|------|------|--------|-----|------|
| 1. 边界条件 | 0 | 3 | 6 | PASS |
| 2. 权限与认证 | 0 | 3 | 3 | PASS |
| 3. 并发安全 | 0 | 3 | 3 | PASS |
| 4. 信息泄漏 | 0 | 1 | 2 | PASS |
| 5. 测试覆盖 | 0 | 6 | 3 | **FIXED** |
| 6. 代码风格 | 0 | 1 | 8 | **FIXED** |
| 7. 文档同步 | 2 | 4 | 3 | **FIXED** |

**Overall: PASS_WITH_WARNINGS** — 无 HIGH 遗留；审查发现的实质缺陷已修复，其余记入 design §8。

---

## 已修复（审查发现 → 修复）

### 1. 作用域方向写反（权限 · 最严重）

`assign_department` 的前缀比较写成 `actor_path.startswith(target_path)`，含义是「target 是 actor 的**祖先**」。后果双向：

- **扩大授权面**：任何持有最低档 `manage_kb_viewer` 的人可把库指派到**根部门** → 全租户可见，无上界
- **破坏核心用例**：指派给**自己的下级部门**被拒

而代码注释、design 决策 6、spec 三处都写着「只能指派到子树内」——**文档与实现完全相反**，读代码看不出来。

**修复**：`any(target_path.startswith(path) for path in actor_paths)`。
**护栏**：`test_manage_holder_cannot_assign_to_an_ancestor`（反方向）+ `test_manage_holder_may_assign_to_a_descendant`（正方向）。

### 2. E2E 类型常量错位，AC-21 台账失实（测试）

`TYPE_SPACE = 2`，但 `KnowledgeTypeEnum.PRIVATE = 2`、`SPACE = 3`。因此 `test_ac21_space_library_is_not_assignable` 实际测的是**个人库**，AC-21「知识空间不可指派」从未被 E2E 验证。

**修复**：常量改为 `TYPE_PRIVATE=2` / `TYPE_SPACE=3`，并补 `lib_unowned`（type=0、无归属）作为筛选器的有效判别物——原判别物是 type=3，在任何 type=0 列表里都必然缺席，断言**不可能失败**。

### 3. `department_stats` 泄漏不可见部门（权限 · AC-13）

对「调用者看不到任何库」的部门仍返回 `count: 0` 条目，泄漏该部门存在、且至少拥有 1 个该类库。与 AC-13 及该方法自己的 docstring 冲突。

**修复**：返回前丢弃 0 值条目。**补 4 个单测**（该方法此前零单元测试）。

### 4. 归属变更不失效权限缓存（并发）

归属是读时求值，不写 FGA 元组，因此没有任何东西像 `authorize()` 那样失效缓存 → 变更后 10s 内不生效（撤销方向则是延迟生效）。

**修复**：新增 `_invalidate_department_members`，指派与清空路径都调用；best-effort，失败由 TTL 兜底。**未**展开子树——成本远高于它消除的 ≤10s 窗口（已记入 §8）。

### 5. 清理与部门变更不同事务（并发）

`apurge_department` 在 `session.delete(dept)` **之前**调用清理，而清理自己独立 commit → 外层若回滚，部门存活但库已被清空。

**修复**：清理移到 commit **之后**，与归档路径语义一致；docstring 写明「必须在自身事务提交后调用」。

### 6. 死代码 + 逻辑内联复制两遍（风格）

`department_chain_for_paths` 无调用方，而同样的三行逻辑在 `knowledge_service` 与 `my_departments` 各内联一份。

**修复**：改为共用（两处都调用它），消除死代码与重复。

### 7. 测试断言不覆盖被测行为（测试）

`assign_department` 的 10 个用例只断言 `(allowed, error)` 元组，`update_one` 被 mock → **把写入值改成 999 或不写，10 个用例依旧全绿**。

**修复**：`_patch` 返回 library 实例；成功用例断言写入值，**5 个拒绝用例断言"未写入"**。

### 8. 2 处失效的回归护栏（测试）

`test_knowledge_space_scope_is_not_widened` / `test_unknown_object_type_grants_nothing` 没 patch `KnowledgeDao.aquery_by_id`；因实现是 fail-closed，真实 DB 调用抛异常被吞后断言仍通过。**未修**，记入 §8。

### 9. 文档失实（文档同步）

- 6 处引用 `test_f056_*` 但文件已改名为 `test_f057_*`
- design 称「不再有任何请求打到 `/departments/*`」，实际仍有 `get_user_primary_department`（该端点是例外：不校验管辖范围）
- 删除联动落点写成 `DepartmentArchiveCleanupService`，但平台删除/归档走的是新增的 `DepartmentService._clear_knowledge_ownership`
- §5 坑 17「普通成员一律 21009」过于绝对（例外如上）

**修复**：逐条订正，并新增坑 17–21。

---

## 保留未修（记入 design §8）

| 项 | 理由 |
|---|---|
| `department_stats` 无界扫描 + 逐行鉴权（F2-3） | 需先定「徽标要覆盖多少库」的口径，属设计问题 |
| 归属可指派到**已归档**部门（F2-1 残留） | 需先定产品口径 |
| 后端节点字段面偏宽（F4-3） | 复用管理端节点构造，含 `mounted_tenant_id` |
| 权限缓存的后代成员残留窗口 | 子树展开成本 >> 消除的 ≤10s 窗口 |
| 2 处失效的回归护栏 | 需改 mock 策略 |
| `.env.development` 的 MinIO 内网地址（F4-1） | 用户确认本地确需连该机器，保留 |

---

## 验证（修复后）

| 项 | 结果 |
|---|---|
| F057 单元测试 | **49 passed**（原 43，新增 stats 4 + 作用域双向 2） |
| ruff（新增文件） | 全部 `All checks passed` |
| ruff（改动文件） | 净增 **0**（`knowledge.py` 37→37、`department_service.py` 126→126、`knowledge_service.py` 39→39、`permission_service.py` 14→14） |
| 前端 typecheck | All files passed |
| 前端 lint | 53（= 基线） |

**过程中自查纠正**：`knowledge.py` 曾净增 2（`List[int]` → `list[int]`、`Optional[int]` → `int | None`），`department_service.py` 与两个 E2E 文件各净增 1（多余的 `noqa`，该规则未启用）。全部归零后才收尾。

---

## 审查方法说明

范围选择上，`--base feat/2.7.0/045-minimax-tts-adapter` 会包含 448 个已提交文件（整个 v2.7.0 累积），无法定位 F057；F057 全部改动未提交，故用 `HEAD` 作 base。

4 个 agent 并行覆盖 7 维度。**审查确实抓出了实质缺陷**——尤其作用域方向反转（会扩大授权面）与 E2E 类型常量错位（导致 AC-21 台账失实），这两类都是单测/自查难以发现的。

---

## 补审（2026-09-18）

### 范围

三个在报告之后（15:49–15:51）被修改、并随 `ed68ccfdf` 提交的文件：

| 文件 | 修改时间 |
|---|---|
| `src/backend/bisheng/knowledge/domain/services/knowledge_department_service.py` | 15:50 |
| `src/backend/test/knowledge/test_f057_my_departments.py` | 15:51 |
| `src/frontend/platform/src/pages/KnowledgePage/useKnowledgeDepartmentFilter.ts` | 15:49 |

### 前置核验：原报告的 6 项修复**均已在最终代码中确认**

| 修复 | 核验方式 | 结果 |
|---|---|---|
| 作用域方向 | `knowledge_department_service.py:113` → `any(target_path.startswith(path) for path in actor_paths)` | ✅ 且注释完整解释了方向反转为何会扩大授权面 |
| E2E 类型常量 | `test_e2e_f057_knowledge_department.py:40-41` → `TYPE_PRIVATE=2` / `TYPE_SPACE=3` | ✅ |
| `department_stats` 丢弃零值 | 返回处 `if count` 过滤，注释说明「存在性 + 至少一个库」双重泄漏 | ✅ |
| 缓存失效 | `_invalidate_department_members` 在指派（`:129`）与清空（`:298`）两路径均调用 | ✅ |
| 清理移至提交后 | `apurge_department` 中 `_clear_knowledge_ownership` 位于 `session.commit()` 之后，注释解释否则会出现「部门存活但库已清空」 | ✅ |
| 死代码消除 | `department_chain_for_paths` 现有调用方 `knowledge_service.py:341` | ✅ |

### 新增发现：无

**维度 1 边界条件 / 维度 2 权限 / 维度 3 并发 —— 补审后维持 PASS。**

重点复核了三处高风险逻辑，均**无缺陷**：

**① 跨租户指派是否可被绕过** —— 这是我本次最担心的口子。`assign_department` 中 `DepartmentDao.aget_by_id()` 无显式租户谓词，看似可指向他租户部门。**核实为安全**：`bisheng.database.models.department` 在 `tenant_filter.py:48` 的 `_TENANT_AWARE_MODEL_MODULES` 列表内，SELECT 由 `do_orm_execute` 自动注入租户条件，`aget_by_id` 走的是普通 ORM SELECT。

**② `clear_department_ownership` 不做租户作用域** —— 代码注释（`:300-302`）给出的理由是「`department.id` 是全局唯一主键，按它匹配已天然租户精确；且依赖租户过滤器反而不安全，因为 `do_orm_execute` 只拦 SELECT，UPDATE 会被静默放过」。**该论证成立**，且记录了一个真实的反直觉坑（过滤器不覆盖 UPDATE），与 design 坑一致。

**③ 权限路径 fail-closed** —— `assign_department` 的拒绝分支全部 `return (False, ...)`；`_has_manage_permission` 走 `KnowledgePermissionService.get_effective_permission_ids_async`（细粒度求值，尊重绑定的关系模型）。未见 fail-open 路径。

**④ 路径前缀逻辑** —— 尾斜杠约定（`/1/2/` 不会误配 `/1/20/`）在 `department_stats` 的上卷与 `assign_department` 的作用域判断中**均成立**。

### 3 条新增测试的成色 —— 有效，非空壳

`test_f057_my_departments.py` 由原报告的 8 条增至 11 条。其中 `test_node_shape_matches_the_children_endpoint`（`:144`）是关键护栏：它锁定树节点字段集与 `GET /departments/children` 一致，防止两处节点形状漂移。

**实测其有效性**（本项目 F058 报告确立的方法：不看测试是否绿，看破坏实现后它是否变红）：

```
临时将 department_service.py 的 _dept_node_dict 中 "has_children" 改名
→ test_node_shape_matches_the_children_endpoint  FAILED ✅
（改动已完整还原，git status 干净，11 passed 复核）
```

护栏真实有效。另两条为 `search_departments` 相关（`test_matches_below_a_collapsed_node_are_found` / `test_truncation_is_reported`），断言具体行为而非存在性。

### 验证

| 项 | 结果 |
|---|---|
| F057 单测（6 个文件） | **52 passed**（原报告 49） |
| 文件还原后复核 | `git status` 干净，11 passed |
| 全目录混合跑 | 106 failed 属需中间件的无关模块（space service / openfga / f006），F057 子集全绿 |

### 补审结论

**PASS。** 原报告结论（PASS_WITH_WARNINGS）维持不变，无需升级。报告写就后的三个文件改动均为**加固性质**（补测试护栏、补注释），未引入新缺陷。原报告「保留未修」的 6 项仍按 design §8 处置，本次补审未新增。

---

## 补审第二轮（2026-09-18，深度）

> 上一节只核对了「原报告的 6 项修复是否在最终代码里」，未重新审查三个文件本身。第二轮按这个方向重做（逐层读码 + 可执行探针），**找到 1 HIGH + 2 MEDIUM**。上一节的「无新增 HIGH/MEDIUM」因此**作废**。

### HIGH — 部门筛选树对普通成员是死的：`fetchChildren` 未传，回落到了管理族端点

- **位置**：`src/frontend/platform/src/pages/KnowledgePage/useKnowledgeDepartmentFilter.ts:84`
- **缺陷**：该 hook 调 `useLazyDepartmentTree({ fetchSearch, cacheKey })` —— **只传了 `fetchSearch`，没传 `fetchChildren`**。后者在 `components/bs-comp/department/useLazyDepartmentTree.ts:116` 有默认值：
  ```
  children: options.fetchChildren ?? ((p) => getDepartmentChildrenApi(p, includeArchived))
  ```
  而 `getDepartmentChildrenApi`（`controllers/API/department.ts:64-68`）打的是 **`/api/v1/departments/children`** —— 部门**管理**族端点，对普通成员答 **21009**。
- **讽刺的是**：同一文件 `:27` 的注释**正在解释这件事**：
  > "Not `/departments/children`: that family is department MANAGEMENT and ..."
  并指向 `getMyDepartmentsApi` 作为成员安全的替代 —— **而该函数现在全前端零调用方**。
- **后果**：普通成员打开知识库 → 首层拉取 21009 → `captureAndAlertRequestErrorHoc` 返回 false → `if (layer) storeLayer(...)` 被跳过 → `rootIds` 恒为 `[]` → 树空 + 一条错误提示。**部门筛选对成员完全不可用**，只有管理员看得到树。而搜索仍可用（走成员安全的 `/knowledge/my_departments/search`），于是用户看到一个「展不开的树 + 能返回结果的搜索框」的半坏状态。
- **实测（agent 执行）**：临时 vitest 探针挂载 hook 打桩 API，记录调用计数 `{"children":1,"myDepartments":0,"mySearch":0}` —— 证明确实打了管理族端点。探针已删。
- **与文档冲突**：`e2e-checklist.md:40-49` 写着「没有任何请求打到 `/departments/`」（该项**未勾选**）；`design.md` 决策 10 亦声称如此。
- **状态**：**CONFIRMED**（我独立核验了 hook 未传 `fetchChildren`、默认值指向管理族端点、`getMyDepartmentsApi` 零调用方三处事实）

### MEDIUM-1 — 管理员的默认部门豁免在硬刷新时失效

- **位置**：同文件 `:110-129`
- **缺陷**：`defaultsAppliedRef.current = true` 在**首次** effect 运行时即锁存，而那次运行发生在挂载期 —— 此时 `userContext` 仍是 `{}`（provider 立即渲染 `{children}`，`user` 异步填充）。于是 `user?.role === 'admin'` 为 false，主部门默认值被照常应用，且该 effect **不会**在真实 role 到达后重跑。
- **后果**：管理员硬刷新知识库页 → 列表默认选中种子管理员的 guest 部门（其名下无任何东西）→ 页面看起来是空的，尽管该管理员其实可读全部。**这正是该豁免当初被加进来要修的 bug**，只在 `user` 恰好先于挂载就位时（应用内跳转）才有效。
- **实测（agent 执行）**：`user` 预置 → `selected:null`（豁免生效）；`user` 挂载后到达 → `selected:5`（豁免失效）。
- **状态**：**CONFIRMED**

### MEDIUM-2 — 归属变更只失效「新部门」成员；两个方向的注释都是假的

- **位置**：`bisheng/knowledge/domain/services/knowledge_department_service.py:89-92`、`:116-129`（注释 `:126-128`）、docstring `:293-298`
- **缺陷**：
  - (a) `assign_department(..., department_id=None)`（清空）在 `:92` 直接返回，**完全不失效缓存**。
  - (b) 改派 A→B 只失效 B 的成员；**A 的成员保留着缓存里的授权**。
  - (c) `:126-128` 的注释称反向由「`clear_department_ownership` 里的同一调用」处理 —— **可证伪**：该方法仅有两处调用点，都在 `department_service.py`（`:833` 归档、`:893` 清除），`assign_department` **从不**调它。
  - (d) docstring `:295-297` 称「在此清理以便每个调用方 —— 平台归档/清除以及 **org-sync 清理** —— 都继承到」，而 `DepartmentArchiveCleanupService._collect_and_delete_mysql_refs` 自己做 `UPDATE knowledge SET department_id=NULL`，**从不调用该方法**。更糟：同一事务先删了 `user_department` 行，故即使之后调用也会静默 no-op（`_invalidate_department_members` 正是经该表解析成员）。
- **后果**：撤销某部门的库访问（清空或改派）后，该部门成员在 10s TTL 内仍能列出并通过读取检查。
- **定级说明**：运行时窗口受 TTL 限制且自愈，故为 MEDIUM —— **真正的严重性是那两条声称「已处理」的假注释**。
- **状态**：**CONFIRMED**（静态：调用点确实不存在，注释所述为假）

### 其余（LOW）

- `aget_by_name_like` 收到原始关键字，LIKE 元字符未转义 → 搜 `%` / `_` 会当通配符（结果错误；租户隔离不受影响）。
- `total_matches` 返回截断**后**的 `len(matches)`，永远报告上限值（500 条显示成 50）。且 `test_truncation_is_reported` **把这个错误契约固化了下来**（11 条匹配断言 `total_matches == 10`）。
- hook 被两个 tab 共用，但统计请求是常量 `{}` → `type=0` / `permission_id="view_kb"`，故 QA tab 的徽标显示的是文档库计数。
- 文档漂移：hook 注释 `:95-101` 仍称「chain 端点返回调用者的部门及其祖先」（实际不查 chain）；`design.md:194-195` 仍把 `knowledge/my_departments` 列为该 hook 的来源。

### 第二轮同时确认「无问题」的部分

- **fail-closed 成立且健全**：`_implicit_department_library_level`（`permission_service.py:1185-1251`）整体 try/except，任何异常返回 `None`；每个提前返回都是 `None`。**未发现 fail-open 分支**。它唯一授予的是 `can_read`，且仅当 `user_path.startswith(owning_path)`。
- **尾斜杠 / 前缀逻辑三处全对**：`assign_department:113`（方向正确）、`department_stats:190`（上卷）、`_implicit_department_library_level:1239`。无 off-by-one，无 `/1/2/` 误配 `/1/20/`。
- **上一轮补审的盲区**正在于此：它把这几个文件当成「已审过」对待，只核对了修复项是否存在，没有重新读实现本身。

### 关于「那 3 条多出来的测试」的准确答案

不存在「恰好 3 条新增」—— 是 **5 条新增替换 2 条删除**（净 +3，故 49→52）。15:46 时 8 条旧 `my_departments` chain 测试被改写为 6 条 `browse_departments` 测试；15:51 追加 5 条 `search_departments` 测试。

**成色（变异测试）**：改坏 `browse_departments` → 4 failed；改坏 `search_departments` → 4/5 failed（幸存的是空结果路径，合理）。**但改坏 `department_chain_for_paths` → 52 passed, 0 failed** —— 该函数**没有任何护栏**，包括其真实调用点 `knowledge_service.py:341`。

两点保留：(i) `test_root_layer_is_returned_to_a_caller_with_no_membership` 与 `test_deep_branches_are_reachable` 实质是同义反复（service 已不接收 user，「无成员过滤」无法被它们证伪）；(ii) `test_truncation_is_reported` 固化了上面的 `total_matches` 缺陷。DAO 全程被 mock，但那是 DB 边界而非被测层 —— service 逻辑确实被执行（上述变异已证明），故与 F058 的陷阱不同，这些测试**不是空壳**。
