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
> 补审结论见文末 §补审（2026-09-18）——**结论：无新增 HIGH/MEDIUM，三个文件通过**。

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
