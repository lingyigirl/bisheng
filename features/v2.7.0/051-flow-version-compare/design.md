# Design: 流程版本比较（F051-flow-version-compare）

> **本文档定位 — 现状快照（Why this How）**
>
> - `spec.md` 回答 **做什么**（目标、AC、边界）
> - `design.md`（本文）回答 **为什么这么实现**：关键决策、运行时不直观的事实、对外契约
> - `tasks.md` 是 **流水账**：拆了哪些任务、做了什么改动
>
> 调整原则（详见 `docs/SDD-Guide.md` §3-§4）：
> - 实现变化 → **覆盖更新本文档**，只留"今天的状态"、不留旧快照；但每个决策保留"为什么 + 被否方案"和坑（护栏，见 §3/§5）
> - **偏差分级**：推翻已 ★ 确认的决策 → 停下与用户重新确认；纯实现细节 → 直接改 design，不必停
> - `tasks.md`「实际偏差记录」只留一行指针（如"T7 偏离 → 更新 design 决策3"），论证在本文档，不重复

**关联**: [spec.md](./spec.md) · [tasks.md](./tasks.md)
**版本**: v2.7.0
**最后更新**: 2026-09-02（初版）

---

## 1. 目标与非目标

- **目标**：让流程开发者能在工作流编辑器中对比任意两个历史版本的结构化差异（节点增/删/改 + 字段级新旧值），并一键在画布定位变更节点，支撑升级确认与问题定位。
- **非目标**：assistant 编辑器（无版本功能）；版本合并/回滚；运行指标对比（F046 领域）；差异导出；客户端 H5。

---

## 2. 关键约束

- 全局铁律遵循 `docs/constitution.md` C1–C7（合规映射见下），不在此重抄。
- 本功能特有约束：
  - **纯读功能**：不新增表/字段/写路径，`flow` / `flow_version` 零写入（AC §3）。
  - **版本数据格式依赖**：diff 算法依赖上游 `flow_version.data` 的既有结构（`nodes[].data.group_params[].params[].key` 等，见 §4.2）。上游（流程编辑器保存格式）变更会影响本功能。
  - **性能目标**（对应 spec 已移除的性能 AC）：对比查询在 1s 内返回。数据量级为单流程 5–50 节点、版本数据 KB 级，纯内存 diff 实测应在百毫秒内。
  - **权限口径**：与流程读取接口一致（AC-14 已确认）。

**Constitution 合规映射**：

| 条款 | 如何满足 |
|---|---|
| C1 分层 | 端点 `api/v1/workflow.py` → `FlowService.compare_versions`（`api/services/flow.py`，沿既有版本接口模式）→ `FlowVersionDao` 取数；diff 算法为纯函数，放 `bisheng/common/utils/flow_version_diff.py`（不 import domain/api，满足 RULE-1） |
| C2 双 DB | 纯读、无新 SQL 方言用法（SELECT 走既有 DAO），无新表/字段 |
| C3 多租户 | 全走 SELECT 自动过滤；跨租户版本查询返回 None → 10500，不泄漏存在性 |
| C4 权限 | 走 `ApplicationPermissionService.has_any_permission_async` 统一入口（决策 3），不查 `role_access` |
| C5 错误码 | 复用 10500 `NotFoundVersionError` 与既有 `UnAuthorizedError`，**不新增错误码**（release-contract 已登记） |
| C6 密钥 | 无新配置/密钥 |
| C7 前端 | 新增 controller 函数 `compareWorkFlowVersions`（`@/controllers/API/workflow`），组件不直接 HTTP |

---

## 3. 方案对比与选定

### 决策 1：diff 在哪一侧计算

- **备选**：
  - A. 后端新增 `/versions/compare` 只读端点 — 权限/租户口径服务端单点执行；未来其他客户端（client、脚本、审计）可复用；前端只渲染。
  - B. 纯前端 diff（复用 `getVersionDetails` 拉两份数据在浏览器算）— 不动后端，但权限口径散落在前端，且每次对比仍要两次请求，无实质节省。
- **选定**：A（用户 2026-09-02 已确认）。
- **原因**：新端点把「口径」（归一化、权限、租户）收敛在一处；对 C4 的可执行性也更好（spec AC-14）。
- **何时该重新考虑**：若未来 diff 展示规则需要随前端高频调整（如字段级过滤/隐藏），可在后端只做结构化归并、把展示映射放前端。

### 决策 2：diff 归一化口径（核心算法）

- **备选**：
  - A. 全量 JSON diff（含 `position` / `viewport` / edge.id）— 实现最简，但画布拖动/缩放会产生海量噪音差异。
  - B. 业务口径 diff：节点按 `node.id` 精确匹配，只比较 `node.data`（忽略 `position` 与 `viewport`）；参数列表内按 dict 的 `key` 字段对齐；边按 `(source, target, sourceHandle, targetHandle)` 匹配，忽略生成式 `edge.id`。
- **选定**：B。
- **原因**：与既有「未保存修改」判定口径一致（`Header.tsx:816 hasChanged` 忽略位置、只比 edges 和 node.data）；按 key 对齐避免参数顺序变化产生虚假差异（spec §3）。实测证据：用户流程 9 个版本节点 id 完全稳定（`start_5x1aa` 等跨版本不变）。
- **缺失/空值口径**（对应 spec §3）：key 在一侧缺失视为「删除/新增」字段差异；key 两侧都在但值不同（含 None ↔ 空串）视为「修改」——缺失与空值严格区分。
- **何时该重新考虑**：若出现节点 id 跨版本重生成（复制/导入流程导致），需引入 name+type 模糊匹配——当前无此场景证据。

### 决策 3：权限口径

- **备选**：
  - A. 流程读取口径：`ApplicationPermissionService.has_any_permission_async(user, 'workflow', flow_id, ['view_app', 'use_app'])`，不通过即拒（与 `FlowService.get_one_flow` 一致）。
  - B. 版本详情口径：`get_version_info` 现状——无任何权限校验，只查库返回。
- **选定**：A（spec AC-14 已确认）。
- **原因**：B 会暴露节点参数（提示词等敏感配置）给任何持有 version_id 的登录用户，违反 C4；A 与编辑器的可见性口径天然一致（能看到流程的人才能对比它的版本）。注：存量 `get_version_info` 无校验是既有缺陷，本功能不沿用、也不在本期顺手修（越界）。
- **何时该重新考虑**：若安全审计要求收紧存量版本接口，届时与本端点一并统一。

### 决策 4：端点形态与路由

- **备选**：
  - A. `GET /api/v1/workflow/versions/compare?flow_id=&from_version_id=&to_version_id=`
  - B. `POST /versions/compare`（body 携带三个 id）
- **选定**：A。
- **原因**：纯只读语义用 GET 最清晰；参数就 3 个标量，query 足够。
- **坑（必须遵守）**：`GET /versions/compare` 必须声明在 `GET /versions/{version_id}` **之前**——FastAPI 按声明序匹配，否则 `compare` 会被当 int 解析成 422（见 §5 坑 1）。
- **何时该重新考虑**：若对比请求需要携带 diff 选项（如忽略字段清单），改 POST body。

### 决策 5：前端交互容器

- **备选**：
  - A. 右侧抽屉（bs-ui `Sheet`）：画布保持可见可操作，点击变更条目可即时看到画布定位（AC-09）。
  - B. 居中 Dialog：遮罩挡住画布，与「画布联动」冲突。
- **选定**：A。
- **原因**：画布联动是核心验收（AC-09/10/11），只有抽屉形态不遮画布。spec AC-02 的「弹窗」按浮层语义实现为抽屉，属实现细节不算偏离。
- **何时该重新考虑**：若未来窄屏/移动端要求全屏对比视图。

### 决策 6：画布高亮实现

- **备选**：
  - A. DOM 级高亮：对 `.react-flow__node[data-id="<node_id>"]` 元素加 outline class，配合 React Flow instance `fitView({ nodes: [{id}] })` 居中。
  - B. store 注入：把高亮样式写进 zustand 的 nodes 状态。
- **选定**：A。
- **原因**：B 会污染编辑器数据 → 触发 `hasChanged` 脏检查 → 出现「未保存修改」提示，违反 AC-10（对比不得修改流程数据）。A 完全不碰 store，关闭抽屉移除 class 即复原（AC-11）。
- **何时该重新考虑**：若 React Flow 版本升级导致 DOM 结构变化（`data-id` 属性不可用）。

### 决策 7：错误码

- **备选**：A. 新增 105xx 对比专用错误码；B. 复用存量错误码。
- **选定**：B。版本不存在/已删除/不属于该 flow → 10500 `NotFoundVersionError`；无权限 → 沿用 `get_one_flow` 的 `UnAuthorizedError`（raise，非 return_resp，口径一致）。
- **原因**：spec AC-13 已按 10500 验收；无新增语义场景，新错误码只会增加 i18n 与文档负担（release-contract 已登记「不新增」）。
- **何时该重新考虑**：出现「两版本相同」需要业务提示码时（现状由响应 `changed: false` 表达，前端本地提示）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

`版本下拉（Header.tsx）点「版本对比」 → 关闭下拉、打开右侧 Sheet → 选择 旧版本/新版本（默认按 AC-02）→ 点「开始对比」→ GET /versions/compare → Sheet 渲染变更列表（节点分组 + 字段旧→新值）→ 点击条目 → React Flow 居中 + DOM 高亮该节点 → 关闭 Sheet 移除高亮`

后端：`workflow.py 端点 → FlowService.compare_versions（权限 → 取两个版本 → 校验归属 → 调纯函数 diff）→ resp_200(结构化差异)`

### 4.2 关键数据结构 / 字段约定

**请求**：`GET /api/v1/workflow/versions/compare?flow_id=<str>&from_version_id=<int>&to_version_id=<int>`

**响应**（`UnifiedResponseModel.data`）：

| 字段 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| `from_version` / `to_version` | `{id, name, update_time}` | 被比较的两个版本元信息 | Sheet 标题/回显 |
| `changed` | `bool` | 两个版本业务数据是否一致（AC-07 依据） | Sheet 空态提示 |
| `node_changes` | `[{node_id, node_name, node_type, change: 'added'\|'removed'\|'modified', fields: [{path, old_value, new_value}]}]` | `node_name`/`node_type` 取 `data.name`/`data.type`；added 取新版本、removed 取旧版本、modified 取新版本；`fields` 仅 modified 有 | 变更列表 + 画布定位 |
| `edge_changes` | `[{source, target, sourceHandle, targetHandle, change: 'added'\|'removed'}]` | 连线增删（业务上随节点增删出现，也独立展示） | 变更列表 |

`path` 格式：点分路径，列表段用 `[key]` 标注对齐键，如 `group_params[模型参数].params[batch_variable].value`（无 key 的列表按索引 `[0]`）。`old_value`/`new_value` 原样全量下发（KB 级），长文本折叠在前端做（AC-08）。**参数级增/删的特殊口径**：key 对齐的参数仅一侧存在时，字段 path 落在参数本身（`...params[batch_variable]`）且值取该参数的 `value` 字段（便于前端展示「参数 X：无 → v」）；两侧都存在时才递归进 `...params[X].value`。

**上游数据形状**（`flow_version.data`，实测 v20）：

- `nodes[]`: `{id, data: {name, type, description, group_params, tab?, v?}, type, position}` —— `position` 与顶层 `viewport: {x, y, zoom}` 不参与 diff（AC-06）。
- `edges[]`: `{id: 'xy-edge__<source><handle>-<target><handle>'（生成式）, source, target, sourceHandle, targetHandle, animated, type}` —— `edge.id` 不参与 diff。
- 参数列表项为 `{key, value, tab, help, ...}` 形 dict —— key 对齐匹配的依据。

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `api/v1/workflow.py`（新端点） | 参数接收、调 Service | 不碰 ORM、不算 diff |
| `api/services/flow.py::FlowService.compare_versions` | 权限校验、版本加载与归属校验、组装响应 | 不写 diff 逻辑（调纯函数） |
| `common/utils/flow_version_diff.py`（新，纯函数） | 归一化 + 递归 diff（决策 2 口径） | 不 import domain/api、不碰 DB |
| `@/controllers/API/workflow`（新函数） | 封装 compare HTTP 调用（C7） | 不处理展示逻辑 |
| `flow/Header.tsx`（改） | 版本下拉加「版本对比」入口 | 不持有对比状态（由 flow/index 管理） |
| `flow/index.tsx` + 新 `VersionCompareSheet` 组件（新） | Sheet 状态、变更列表渲染、React Flow 联动定位/高亮 | 不直接 HTTP、不写 store |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `GET /versions/{version_id}` 已存在且 `version_id: int`；`/versions/compare` 若声明在其后，`compare` 会先被 int 解析 → 422 | 对比接口永远打不通 | 新路由声明在 `{version_id}` 之前 |
| 2 | 存量 `get_version_info` 无任何权限校验（只查库） | 抄它的模式会让对比端点裸奔，违反 C4 | 权限按 `get_one_flow` 口径（决策 3） |
| 3 | 编辑器脏检查 `hasChanged` 实时比对 store 里的 nodes 数据 | 高亮若写 store 会立即触发「未保存修改」提示 | 高亮只加 DOM class（决策 6） |
| 4 | React Flow v12 节点 DOM 是异步渲染的，首次点击条目时 `.react-flow__node[data-id]` 可能还不存在 | 高亮静默失效 | 先 `instance.getNode(id)` 判断存在 + fitView 定位；DOM class 在渲染后补加（tasks 阶段验证时序） |
| 5 | 版本列表按 id 倒序；「上一个版本」（AC-02 默认旧版本）指 id 小于当前选中版本 id 的最近一个非删除版本 | 默认选择错位 | 前端按 id 排序取前驱 |
| 6 | `edge.id` 是生成式（`xy-edge__...`），且 `viewport` 随画布操作漂移 | 全量 diff 会产生海量噪音差异 | 决策 2 口径排除 |
| 7 | 跨租户/不存在版本在 SELECT 自动过滤下查不到（返回 None） | 误以为要手写 tenant 条件（C3 禁止） | 直接判 None → 10500，不泄漏存在性 |
| 8 | 既有 `FlowService.compare_flow_node` / `compare_flow_stream`（`FlowCompareReq`）是**版本运行输出对比**（跑测试用例比结果），与本功能的**结构对比**同名不同域 | 改错函数、或误以为对比功能已存在而放弃开发 | 本功能方法是 `compare_versions`（新），不触碰 compare_flow_node 链路 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| `GET /api/v1/workflow/versions/compare` | HTTP API | platform 前端（VersionCompareSheet） |
| `compareWorkFlowVersions` | 前端 controller 函数 | `VersionCompareSheet` |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| `flow_version.data` 节点/参数结构（§4.2 上游形状） | 隐式数据契约 | 流程编辑器保存格式变更会静默影响 diff 输出 |
| `FlowVersionDao.get_version_by_id` | 内部 DAO | 其无 tenant 显式条件（依赖自动过滤），正常 |
| `ApplicationPermissionService.has_any_permission_async` | 内部 Service | 权限模型变更影响 AC-14 口径 |
| bs-ui `Sheet` / React Flow v12 instance API | 前端库 | 升级需回归画布联动（坑 4） |

---

## 7. 测试与可观测

- **单元（后端）**：`test/common/test_flow_version_diff.py` —— 纯函数矩阵：节点增/删/改、参数按 key 对齐（顺序乱序不产生差异）、position/viewport 排除、edge 匹配忽略 id、嵌套路径 `path` 格式、完全相同 → `changed: false`。用真实形状 fixture（§4.2）。
- **API 集成（后端）**：`test/api/` 下 httpx 用例（auth helper）：正常对比、版本不存在/跨 flow → 10500、无权限 → 401、同版本对比 → `changed: true`。覆盖 AC-12/13/14。
- **前端组件（Vitest）**：`VersionCompareSheet` 渲染变更列表、同版本禁用「开始对比」（AC-03）、空态提示（AC-07）。
- **e2e（/e2e-test，step 7 必做）**：API e2e + 页面手动清单。
- **手动验证**：用「会议纪要智能助手」（`c6c9b2c3287447cb8db2577152362cb4`，9 个版本）——对比 v1.5（id 19）vs v1.6（id 20）应显示 `llm_4b8e2` 节点 `group_params` 变化；对比 v1.6 vs v1.6 提示相同；拖动节点位置后另存版本，对比应无业务差异。
- **性能**：1s 目标（KB 级数据，纯内存 diff，预期 <100ms）；无新增日志/指标需求。

---

## 8. 后续改进 / 不打算做的事

- assistant 编辑器版本管理（含对比）——前提是 assistant 先有版本能力，另起 feature。
- 差异结果导出 / 打印——spec 明确排除，有需求另起。
- 节点模糊匹配（id 重生成场景）——当前无证据，决策 2 触发条件。
- 参数级一键恢复（把旧版本某个字段值写回当前草稿）——涉及写路径与脏检查交互，另起 feature。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-02 | 初版 | feature 设计完成 |
