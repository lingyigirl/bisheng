# E2E 覆盖报告: 057-knowledge-department-ownership

**执行时间**: 2026-09-16
**执行环境**: 本地后端 `127.0.0.1:7860`（指向共享开发库 `172.19.70.189:3306/bisheng`）
**测试文件**: `src/backend/test/e2e/test_e2e_f057_knowledge_department.py`

---

## API 测试结果

| AC-ID | 描述 | 状态 | 测试方法 |
|-------|------|------|---------|
| AC-01 | 归属部门成员可读取该库 | ✅ 通过 | `test_ac01_member_of_owning_department_sees_library` |
| AC-02 | 下级部门成员可读取（向下传递） | ✅ 通过 | `test_ac02_descendant_member_sees_ancestor_library` |
| AC-03 | 兄弟/上级部门不可见（方向守卫） | ✅ 通过 | `test_ac03_sibling_department_member_gets_nothing` |
| AC-09 | 按部门筛选只返回该部门子树 | ✅ 通过 | `test_ac09_filter_narrows_to_department_subtree` |
| AC-11 | 选择「全部」恢复完整结果 | ✅ 通过 | `test_ac11_clearing_filter_restores_full_list` |
| AC-12 | 部门统计返回子树包含的计数 | ✅ 通过 | `test_ac12_department_stats_counts_owned_libraries` |
| AC-14 | 返回调用者主部门（供预选） | ✅ 通过 | `test_ac14_primary_department_is_returned_for_caller` |
| AC-15 | 未知部门筛选返回空而非全量 | ✅ 通过 | `test_ac15_unknown_department_filter_is_empty_not_unfiltered` |
| AC-16 | 管理员同样受筛选约束 | ✅ 通过 | `test_ac16_admin_filter_also_narrows` |
| AC-17 | 无管理权限不得改归属 | ✅ 通过 | `test_ac17_assignment_requires_manage_permission` |
| AC-19 | 取消归属回到未分类 | ✅ 通过 | `test_ac19_admin_can_clear_ownership` |
| AC-20 | 目标部门不存在则拒绝 | ✅ 通过 | `test_ac20_unknown_target_department_rejected` |
| AC-21 | 知识空间不可指派 / 问答库可指派 | ✅ 通过 | `test_ac21_space_library_is_not_assignable` / `test_ac21_qa_library_is_assignable` |
| AC-22 | 部门删除后其库回到未分类 | ✅ 通过 | `test_ac22_archive_unclassifies_libraries` |
| AC-24 | 悬空部门不产生可见性 | ✅ 通过 | `test_ac24_dangling_department_grants_nothing` |
| AC-10 | 页面显示当前筛选并可清除 | ⏭️ 跳过（UI 交互，见手动清单） | — |
| AC-13 | 徽标只统计有权限的库 | ⏭️ 跳过（UI 交互，见手动清单） | — |
| AC-04, AC-05, AC-06, AC-07, AC-08, AC-18, AC-23, AC-25 | 读时求值内部语义 | ⏭️ 跳过（由 35 个单元测试覆盖） | — |

**通过: 16/17 用例** | **跳过: 3 组（UI / 单元覆盖）** | **失败: 0**

> 用例数（17）与 AC 数（25）不等：部分用例一次覆盖多条 AC，部分 AC 由单元测试承担。

---

## E2E 暴露并修复的产品缺陷

本轮 E2E 的最大价值不在「测试通过」，而在**抓出了 3 个单测无法发现的真实缺陷**——全部是「代码声称支持、实际静默失效」。

### 1. 部门归属可见性对普通成员完全不生效（严重）

**现象**：普通部门成员登录后知识库列表恒为空。

**证据**：服务端日志 `accessible_ids=0 creator_ids=0 candidate_ids=0`。

**根因**：`KnowledgeDao._user_knowledge_filters` 里，部门归属的 OR 子句被放在
`if knowledge_id_extra:` 的真值分支内。而最典型的部门成员**没有任何 FGA 授权、也没有自建库**，
`knowledge_id_extra` 为空 → 走 `else` 分支只查 `user_id = me` → 归属子句被整个跳过。

**影响**：F057 对其**主要受众**是彻底失效的。而原有单元测试断言的是编译后的 SQL 字符串，
恰好把错误行为固化成了「预期」，因此全部通过。

**修复**：`knowledge.py:328-350`，把归属判断提为独立条件，与 `knowledge_id_extra` 同级。

### 2. 管理员路径丢弃部门筛选（T-21 实际从未实现）

**现象**：管理员在知识库页选中部门节点，列表纹丝不动，且无任何报错。

**根因**：`aget_all_knowledge` 与 `generate_all_knowledge_filter` 根本没有部门参数——
端点在 `knowledge.py` 接收了 `department_id`、服务层也透传了，到 DAO 层被静默丢弃。

**影响**：前端对管理员同样渲染部门树且无角色分支，所以这是可达的日常操作。

**修复**：给上述两个 DAO 方法加 `filter_department_ids` 并在服务层的 admin 分支透传。

> **流程教训**：tasks.md 中 T-21 此前被我标记为 ✅ 完成，属**误标**。我在写文档时依据的是
> 「代码里有相关改动」的印象，而非验证实际行为。已在 tasks.md 更正，并补了 3 个单测。

### 3. 测试清理逻辑失效，在共享库累积垃圾

**现象**：每轮 E2E 在共享开发库留下 3 个部门，多轮累积至 56 个；用户累积至 30 个。

**根因（两层）**：
- `GET /departments/search` 返回 `{"roots": [...]}`，清理代码读的是 `.data` → 恒为空列表；
- 即便拿到节点，`DELETE`（归档）会因**部门仍有成员**被拒，而历史残留部门的成员从未被移除。

**修复**：清理改为「搜索含归档的部门 → 逐部门先移除全部成员 → 归档 → 硬删」，且深度优先。
两轮内累积的 56 个残留已全部清除（验证：`depts left: 0`）。

---

## 数据隔离验证

| 检查项 | 结果 |
|---|---|
| 测试数据前缀 | `e2e-f057-`（≥5 字符，符合规范） |
| 运行后残留部门（本套件前缀） | **0** |
| 运行后残留知识库（本套件前缀） | **0** |
| `knowledge` 表总行数变化 | 12 → 12（未影响任何非测试数据） |
| 非测试数据是否被触碰 | 否 |

测试租户：本部署为单租户（`tenant_id=1`），未创建独立测试租户；隔离靠名称前缀保证。

---

---

## 追加：普通成员 21009（2026-09-16 用户报告后）

**症状**：普通用户打开知识库页，`GET /departments/{id}/path-tree` 返回 `21009 No permission for this department operation`。

**根因**：`/departments/*` 是**部门管理**接口族，F038 将其可见性限定为三层**管理**身份（系统管理员 / 部门管理员 / 租户管理员）——**普通部门成员不属于任何一层**。而 F057 的部门筛选器受众正是这些普通成员，因此必然报错。

**归属判定**：**非本 feature 引入的回归**。A/B 对照——将改动全部 stash、另起 7861 端口跑改动前的后端——普通用户**同样 21009**。属初版选错接口。

**修复**：新增 `GET /api/v1/knowledge/my_departments`（仅需登录），前端筛选树换用该端点，**不再有任何请求打到 `/departments/*`**。

> **后续修正（同日）**：该端点初版按**成员身份**返回「所属部门 ∪ 祖先」，用户随后要求「树形数据应该是全部的部门数据」——该裁剪会让管理员只看到 2 个节点、并被默认筛选筛成空列表。现改为返回**完整租户树**，并新增服务端搜索 `GET /knowledge/my_departments/search`（全量树下前端本地过滤会漏掉未展开层）。详见 design 决策 10。

**回归护栏**：`test/e2e/test_e2e_f057_my_departments.py` 用**同一普通成员 token** 同时断言：旧端点必须继续返回 21009、新端点必须 200。

| 验证 | 结果 |
|---|---|
| 新套件 | **4/4 通过** |
| F057 E2E 总 | **21/21 通过** |
| 单元测试总 | **43/43 通过** |
| typecheck / lint | 全过 / 53（=基线） |
| arch-guard | 无 VIOLATION |

**整体状态: PASS**（原 17 项 + 本节 4 项）

- 自动化用例 17/17 通过
- 单元测试 35/35 通过
- 迁移链完整：`f056_knowledge_department`（head）= 数据库 `alembic_version`
- 前端 `pnpm typecheck` 全通过
- 遗留：AC-10 / AC-13 属 UI 交互，需按 `e2e-checklist.md` 手动验证
