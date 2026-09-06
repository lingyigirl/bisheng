# Tasks: 流程版本比较（F051-flow-version-compare）

**关联规格**: [spec.md](./spec.md)
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-02 用户确认 |
| design.md | ✅ 已评审 | 2026-09-02 用户确认（含 Constitution Check） |
| tasks.md | ✅ 已拆解 | 2026-09-02 /sdd-review tasks 通过 |
| 实现 | ✅ 已完成 | 10 / 10 完成。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 开发模式

**按 Wave 组织任务**：按依赖分组——无依赖的归 Wave 1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`，让 agent 能自行判断下一步、把无依赖的并行跑。

**后端 Test-First（务实版）**：
- 先写测试（红），再写实现（绿）。本 feature 无中间件依赖（纯内存 diff + DB 读），无需搭 pytest 基础设施（conftest.py 已存在）。
- 中间件 / DM8 / e2e 测试在 **CI** 跑，不依赖本地（见 `docs/SDD-Guide.md` §6）。

**前端**：当前用「手动验证」（每个任务附验证步骤）。组件级断言用 platform 既有 Vitest（`src/test/`）。

**自包含任务**：每个任务内联文件、逻辑、AC 覆盖，实现期不必回读 spec。但**设计论证（为什么这么做）指向 design §X，不复制**（避免第三处漂移）。

**设计锚点**：diff 口径 → design 决策 2；权限口径 → 决策 3；路由顺序坑 → 决策 4 + 坑 1；Sheet + DOM 高亮 → 决策 5/6 + 坑 3/4；错误码 → 决策 7。

---

## Tasks

### 后端 diff 纯函数（Test-First 配对）

- [x] **T001**: diff 纯函数单元测试（先红）
  **文件**: `src/backend/test/common/test_flow_version_diff.py`
  **逻辑**: 用 design §4.2 真实形状 fixture（5 节点 workflow：`start/input/code/llm/end`）编写用例：
  ① 节点 added / removed / modified 三态识别；② modified 节点 fields 含 `path`/`old_value`/`new_value`；
  ③ `group_params`/`params` 列表按 `key` 对齐——参数顺序打乱不产生差异；
  ④ `position` 与顶层 `viewport` 变化不产生差异；
  ⑤ edge 按 `(source, target, sourceHandle, targetHandle)` 匹配、忽略 `edge.id`；
  ⑥ key 一侧缺失 = 删除/新增，`None ↔ ""` = 修改（design 决策 2 缺失/空值口径）；
  ⑦ 两版本完全一致 → `changed: false` 且无 changes。
  **覆盖 AC**: AC-04, AC-05, AC-06, AC-07
  **依赖**: 无

- [x] **T002**: diff 纯函数实现
  **文件**: `src/backend/bisheng/common/utils/flow_version_diff.py`
  **逻辑**: 按 design 决策 2 实现 `compare_flow_versions(data_from: dict, data_to: dict) -> dict`：
  节点按 `node.id` 精确匹配、只比 `node.data`（剔除 `position`/`viewport`）；
  递归 diff，list-of-dict 含 `key` 者按 key 对齐、其余按索引；edge 按连接四元组匹配；
  产出 design §4.2 响应中 `node_changes`/`edge_changes`/`changed` 结构。
  **约束**: 纯函数，不 import domain/api（C1 RULE-1），不碰 DB。
  **测试**: T001 全部通过
  **覆盖 AC**: AC-04, AC-05, AC-06, AC-07
  **依赖**: T001

### 后端 API 层（Test-First 配对）

- [x] **T003**: compare 端点集成测试（先红）
  **文件**: `src/backend/test/api/test_workflow_version_compare.py`
  **逻辑**: httpx + `test/e2e/helpers/auth.py`（login 拿 token），用例：
  ① happy path：`GET /api/v1/workflow/versions/compare?flow_id=..&from_version_id=..&to_version_id=..` 返回 200，
  data 含 `from_version`/`to_version`/`changed`/`node_changes`/`edge_changes` 且结构与 design §4.2 一致；
  ② version_id 不存在 / 不属于该 flow → 返回错误码 10500；
  ③ 无 `view_app`/`use_app` 权限用户 → 403 业务码（UnAuthorizedError，与 `get_one_flow` 口径一致；e2e 中该用户若恰有权限则条件跳过）；
  ④ 同版本对比 → 200 且 `changed: true`。
  **基础设施**: 测试数据用 DB 中既有流程（会议纪要智能助手 `c6c9b2c3287447cb8db2577152362cb4`，v1.5/v1.6 有真实参数差异），不造数据。
  **覆盖 AC**: AC-04, AC-05, AC-12, AC-13, AC-14
  **依赖**: T002

- [x] **T004**: FlowService.compare_versions + 端点 + 路由
  **文件**: `src/backend/bisheng/api/services/flow.py`、`src/backend/bisheng/api/v1/workflow.py`
  **逻辑**: ① `FlowService.compare_versions(cls, user, flow_id, from_version_id, to_version_id)`：
  权限 `ApplicationPermissionService.has_any_permission_async(user, 'workflow', flow_id, ['view_app', 'use_app'])` 不通过 → raise `UnAuthorizedError`（design 决策 3）；
  `FlowVersionDao.get_version_by_id` 取两版本，任一为 None 或 `flow_id != flow_id` → `NotFoundVersionError.return_resp()`（10500）；
  调 T002 纯函数，组装 design §4.2 响应（`from_version`/`to_version` 含 id/name/update_time）。
  ② 端点：`GET /versions/compare`（query 三参数），**必须声明在 `GET /versions/{version_id}` 之前**（design 坑 1，否则 int 解析 422）。
  **影响范围**: 仅新增 Service 方法与端点；不改动既有版本接口（list/detail/create/update/delete/change_version）任何行为。
  **测试**: T003 全部通过
  **覆盖 AC**: AC-12, AC-13, AC-14
  **依赖**: T002, T003

### 前端 Platform

- [x] **T005**: controller 函数
  **文件**: `src/frontend/platform/src/controllers/API/workflow.ts`
  **逻辑**: 新增 `compareWorkFlowVersions(params: {flow_id, from_version_id, to_version_id})`，走既有 request 封装（C7），返回 `data` 字段（typed）。
  **覆盖 AC**: AC-12
  **手动验证**: 无独立验证（随 T007 联调）。
  **依赖**: T004

- [x] **T006**: i18n 三语
  **文件**: `src/frontend/platform/public/locales/zh-Hans/bs.json`、`public/locales/en-US/bs.json`、`public/locales/ja/bs.json`
  **逻辑**: 在 `skills` 段新增（沿用既有 `saveVersion`/`supportVersions` 命名风格）：
  `compareVersions`（版本对比）、`compareOldVersion`/`compareNewVersion`（旧版本/新版本）、
  `compareStart`（开始对比）、`compareSelectDifferentVersions`（请选择两个不同版本）、
  `compareIdentical`（两个版本内容相同）、`compareNodeAdded`/`compareNodeRemoved`/`compareNodeModified`（节点新增/删除/修改）、
  `compareEdgeAdded`/`compareEdgeRemoved`（连线新增/删除）、`compareNoChanges`（无变化）等；三语同 PR（根 AGENTS.md §4 i18n 规则）。
  **覆盖 AC**: AC-03, AC-07（文案载体）
  **依赖**: 无

- [x] **T007**: VersionCompareSheet 组件
  **文件**: `src/frontend/platform/src/pages/BuildPage/flow/VersionCompareSheet.tsx`（新，超 600 行再拆子组件）
  **逻辑**: bs-ui `Sheet`（右侧抽屉，design 决策 5）：
  ① 顶部两个版本选择器（数据源复用 `getWorkFlowVersions` 列表），默认值按 AC-02（旧=按 id 序早于当前选中版本的最近一个，新=当前选中版本）；
  ② 两个选择相同 → 「开始对比」disabled + 提示（AC-03）；
  ③ 点击「开始对比」→ T005 controller → 渲染变更列表：节点分组（新增/删除/修改徽标，name 展示），
  modified 节点展开字段行（`path` 语义化展示 + 旧值→新值，长文本默认折叠、点击展开，AC-05/AC-08）；
  ④ 连线增删行；⑤ `changed: true` → 空态提示（AC-07）；⑥ 点击节点条目 → 回调 `onLocateNode(nodeId)`（画布联动在 T008）。
  **覆盖 AC**: AC-02, AC-03, AC-04, AC-05, AC-07, AC-08
  **手动验证**:
  - 打开 http://172.19.70.141:3001/flow/c6c9b2c3287447cb8db2577152362cb4（会议纪要智能助手）
  - 版本下拉 → 版本对比 → 默认应选中 v1.5（旧）/ v1.6（新）；开始对比 → 列表出现 `llm_4b8e2` 修改项及 `group_params` 字段差异
  - 同版本选择 → 按钮禁用
  **依赖**: T005, T006

- [x] **T008**: Header 入口 + 画布联动
  **文件**: `src/frontend/platform/src/pages/BuildPage/flow/Header.tsx`、`src/frontend/platform/src/pages/BuildPage/flow/index.tsx`
  **逻辑**: ① 版本下拉（`useVersion` 的 RadioGroup 底部）加分隔线 + 「版本对比」按钮（AC-01），点击关闭下拉、打开 T007 Sheet；
  ② Sheet 状态放 flow/index（Header 不持有）；③ 画布联动（design 决策 6）：点击条目 →
  React Flow instance `fitView({ nodes: [{ id: nodeId }], duration: 400 })` 居中；
  对 `.react-flow__node[data-id="<nodeId>"]` DOM 元素加高亮 outline class（若尚未渲染，用 `instance.getNode` 判断并等渲染后补加——design 坑 4）；
  **绝不写 store/nodes 状态**（坑 3，否则触发 hasChanged 脏提示，违反 AC-10）；
  ④ 关闭 Sheet → 移除高亮 class 并复位（AC-11）。
  **覆盖 AC**: AC-01, AC-09, AC-10, AC-11
  **手动验证**:
  - 对比 v1.5/v1.6 → 点击 `llm_4b8e2` 条目 → 画布居中且该节点带高亮描边；编辑器顶部**无**「未保存修改」提示
  - 关闭 Sheet → 高亮消失；拖动节点位置后另存版本、再对比 → 无位置类差异（AC-06 端到端确认）
  **依赖**: T007

- [x] **T009**: VersionCompareSheet 组件测试（Vitest）
  **文件**: `src/frontend/platform/src/test/versionCompareSheet.test.tsx`
  **逻辑**: mock controller 返回 fixture（design §4.2 形状）：① 渲染节点新增/删除/修改三态与字段旧→新值（AC-04/05）；
  ② 两版本相同 → 「开始对比」disabled（AC-03）；③ `changed: true` → 空态提示文案（AC-07）。
  **覆盖 AC**: AC-03, AC-04, AC-05, AC-07
  **依赖**: T007

### 端到端验收（SDD step 7 必做）

- [x] **T010**: /e2e-test 验收
  **逻辑**: 运行 `/e2e-test features/v2.7.0/051-flow-version-compare`：API 端到端（pytest + httpx，认证/权限/10500 路径）+ 页面手动验证清单（AC-01 ~ AC-11 逐条走查）。
  **覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14
  **依赖**: T008, T009

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- T003 偏离 → 测试落位调整：`test/api` 无法起活体服务（conftest mock 链），改为静态契约测试（含路由顺序守卫）；活体用例放 `test/e2e/`（沿用 f045/f047 既有模式）；e2e 403 用例改为环境条件跳过（test 用户在本环境对该流程恰有权限）。详见 design 决策 3 / 坑 1。
