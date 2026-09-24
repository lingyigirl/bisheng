# Design: 表单上传组件的知识空间选择与本地文件归集（065-form-upload-knowledge-space）

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
**最后更新**: 2026-09-24

---

## 1. 目标与非目标

- **目标**：让「应用对话」里工作流 `form_input` 节点的**文件表单字段**支持两种取件方式——从知识空间挑选既有文件、或从本地上传；两种来源产出同一种取值，走同一条既有提交链路。同时把**本地上传**的文件按业务归属（客户名称 / 应用名称）归档进该用户自己的「应用文件」知识空间。
- **非目标**：不改后端输入节点的解析与落库行为；不改知识空间的去重口径；不覆盖主对话输入区（聊天附件）的上传链路；不提供重复文件的覆盖交互；不跨用户共享归档文件。

---

## 2. 关键约束

全局架构铁律（分层 / 双 DB / 多租户 / 权限 / 错误码）遵循 `docs/constitution.md` C1–C7，不在此重抄。本特性特有的约束：

- **纯前端改动**：不新增后端端点、不新增表 / 字段、不新增错误码。所有写入复用知识空间既有 API。
- **运行时依赖**：依赖后端 ①`POST /api/v1/knowledge/upload/{knowledge_id}` 返回的 `repeat` 判重标志；②`GET /api/v1/knowledge/space/{space_id}/files/{file_id}/download` 返回的 `original_url`（presigned）；③输入节点 `parse_upload_file` 对文件取值的解析方式（§5 坑 5）。这些是**隐式数据契约**，见 §6.2。
- **权限（C4）**：自动创建空间走既有 `POST /api/v1/knowledge/space` 端点——服务端已在该路径调用 `PermissionService.authorize()`，复用即满足 C4，不新增授权口径。用户**无建空间权限**时创建会失败，按 AC-22 降级为「不归集、不阻断提交」。
- **前端全局规则只引用不重抄**：Recoil 冻结 / 单文件 ≤ 600 行 / i18n 三语 / 语义化 token 等见 `client/AGENTS.md` 与根 `AGENTS.md §4`。其中 **Recoil 冻结直接决定**本特性不能用 atom 承载选择器选中态（决策 2）——这是本特性受该规则约束的具体落点。
- **配额**：归档上传走 `@require_quota(KNOWLEDGE_SPACE_FILE)`，可能因空间文件配额不足而失败；按 AC-22 不阻断提交。

---

## 3. 方案对比与选定

### 决策 1：归集的实现位置——纯前端复用既有 API（选定） vs 新增后端端点

- **备选**：
  - A. **纯前端**：复用 `createSpaceApi` / `listKnowledgeFolders` / `createFolderApi` / `uploadFileToServerApi` / `addFilesApi`。
  - B. 新增后端端点「按客户名称归档文件」（把建空间、建文件夹、判重、登记收敛成一次调用）。
- **选定**：A。
- **原因**：所需能力在既有端点里已齐备，且判重逻辑（`get_repeat_file`）已在后端；新增端点要动后端分层与错误码，收益仅为少几次往返。本特性是前端交互为主的需求，B 的成本不划算。
- **何时该重新考虑**：若出现「必须在服务端保证归档原子性」或「需要批量归档大量文件且往返成本成为瓶颈」的诉求。

### 决策 2：知识空间选择器——新建弹层组件（选定） vs 复用知识库页面组件

- **备选**：
  - A. **新建**一个选择器弹层，按「空间 → 文件夹 → 文件」浏览，单选/多选跟随字段配置。
  - B. 复用 `pages/knowledge/SpaceDetail/index.tsx` 的 `KnowledgeSpaceContent`。
  - C. 复用 `ChatKnowledge`。
- **选定**：A。
- **原因**：B 是页面级区域、与知识库页面深度耦合（注入 space / files / 上传 / 拖拽 / 权限回调），且其选中态存在 **Recoil atom**（`pages/knowledge/selectionStore.ts`）——而 Recoil 已冻结，不能新增/借用 atom 做跨模块选中态；C 选的是**知识空间**而不是**文件**，形状不对（`{id,name,type}`，无 file 信息）。故三者都不可直接复用。
- **何时该重新考虑**：若知识库页面把 `KnowledgeSpaceContent` 重构为受控、无 Recoil 依赖的展示组件，可改为复用。

### 决策 3：两种来源的「取值」形态

- **备选**：
  - A. 统一为**可下载地址**：本地文件用上传端点返回的 `file_path`；知识空间文件用 `getFileDownloadApi().original_url`。知识空间文件的地址在**选中时**即取。
  - B. 知识空间文件提交 `file_id`（需要后端支持按 id 解析）。
  - C. 知识空间文件在提交时再换取地址。
- **选定**：A。
- **原因**：后端 `parse_upload_file`（`workflow/nodes/input/input.py`）把字段值当 URL 列表处理——`urlparse` 取末段为文件名、`file_download()` 下载内容（`core/cache/utils.py:379` 的 `file_download` 显式支持 MinIO presigned URL 与相对路径两个分支）。B 需要后端改造且会让 `file_download` 无法工作；C 需要把 `space_id/file_id` 混进字段值、并在提交前插入异步解析步骤，而 `InputForm` 的提交是同步组装 payload 的。
- **原因（选中时取的具体理由）**：需求方明确「与原有逻辑一致」——本地上传也是**选中即上传**，取值在选中时确定。两者时机一致，组件内的状态机只需一套。
- **已知代价 / 何时该重新考虑**：presigned URL 有有效期，用户长时间停留后提交可能过期（见 §5 坑 7）。若实测过期造成明显失败率，改为 C（提交前统一换取）。

### 决策 4：合并与去重的状态归属——放在 `InputFileComponent` 内部（选定）

- **备选**：
  - A. 组件自持已选列表（含来源元数据），通过 `onChange` / `onFileChange` 同步给 `InputForm`。
  - B. 由 `InputForm` 持有并回传。
- **选定**：A。
- **原因**：`InputForm` 对文件字段固定传 `value={''}`（`InputForm.tsx:155`），且 `formDataRef` 是 `useRef`——**写入不触发重渲染**。因此「多选合并」无法靠父组件回传的 prop 驱动；`InputForm` 侧只能被动接收数组。组件自持列表是唯一能保证展示与取值一致的方案（§5 坑 2）。
- **何时该重新考虑**：若 `InputForm` 改为受控组件（用 useState 管理表单值），可把合并逻辑上提。

### 决策 5：去重必须在**登记前**判定——绝不对重复文件调 `addFilesApi`（选定）

- **备选**：
  - A. **先判重**：归档时先对每个本地文件调用上传端点 `POST /api/v1/knowledge/upload/{space_id}`，用其响应的 `repeat` 标志判定；`repeat=true` 则**跳过、不登记**。判定**作用域与登记作用域必须是同一个**（见决策 11）。
  - B. 直接调 `addFilesApi`，把返回行里 `status===3` 的当作重复静默跳过。
  - C. 拉取空间子项列表，按**文件名**预筛，再对未命中的调 `addFilesApi`。
- **选定**：A。
- **原因**：**B 会造成破坏性副作用**——`KnowledgeService.process_one_file`（`knowledge/domain/services/knowledge_service.py:1447-1462`）在命中重复时**不是**只返回标记，而是**修改既有文件行**：把 `object_name` 改成 `tmp/{id}.{ext}`、把本次内容写入该 tmp 对象、写 `remark`、并把 `status` 置为 `FAILED`，等待前端决定是否 retry 覆盖。因此「调用后忽略 status===3」会把一个原本正常的既有文件变成 FAILED 状态（检索不到），与 AC-20「保留既有文件、静默跳过」直接冲突。C 只按**文件名**判重，无法覆盖「同内容不同名」的情形，仍会落到 B 的破坏路径。
- **A 的判重与后端完全一致**：上传端点用的 `KnowledgeFileDao.get_repeat_file(knowledge_id, md5_, file_name, file_level_path)`（`knowledge/domain/models/knowledge_file.py:454`）与 `KnowledgeService.process_one_file` 里的判重用的是**同一套**查询（按内容 md5 或文件名、排除非主版本残留），所以 `repeat` 是 `addFilesApi` 判重结论的**可靠预判**。**查询范围由两边共同传入的 `dedup_scope` 决定**（决策 11）——只传一边，预判与登记就会对「算不算重复」给出不同答案。
- **代价**：被判重的文件需要多上传一次（第一次为表单取值而上传，第二次为判重+登记而上传）。归档是提交后的后台动作、单次少量文件，代价可接受；换来的是**既有文件绝不被改坏**。
- **实现前提（必须遵守）**：表单组件在本地文件上传后**必须保留其 `File` 对象直到提交**——第二次上传的输入就是它。现有 `isSSO` 路径（`uploadFileWithProgress`）拿到 `file_path` 后即丢弃 `File`，若不改这一点，提交时无输入可用，判重无法进行（§5 坑 12）。
- **何时该重新考虑**：若上传端点增加「只判重不落盘」的接口，可省掉第二次上传。

### 决策 6：「应用文件」空间——私有、每人各自一份（选定）

- **备选**：A. 私有（`auth_type=private`，创建者私有）；B. 公开（全租户共用）；C. 私有 + 管理员预建共享。
- **选定**：A（需求方 2026-09-24 裁决）。
- **原因**：业务上要求归档材料不对外暴露。**接受的行为**：私有空间对他人不可见 ⇒ 按名查找只能命中自己创建的 ⇒ 每人各自一份；跨用户共享检索不成立，AC-20 的去重也只在各自空间内生效（spec §3 已写明此为接受行为而非缺陷）。
- **何时该重新考虑**：若后续要求「同一客户的材料全组可见」，需改为 B 或 C，并同步修订 spec §3 与该条决策。

### 决策 7：归集时机与失败处理——提交时统一执行、尽力而为、不阻断提交

- **备选**：
  - A. 上传成功即建文件夹并登记。
  - B. **提交时**统一建文件夹并登记（需求方 2026-09-24 选定）。
- **选定**：B。
- **原因**：文件夹名取决于「客户名称」字段的**值**，而该值可能与文件字段在同一张表单里、且可能在选完文件之后才填写。选 A 会在客户名称尚未确定时就建好文件夹（或建错）。B 在提交时一次性求值，语义正确。
- **失败处理**：归集整体为**尽力而为**（AC-22）——任一步失败都不阻断提交、不丢失用户已填内容。失败仅 `console.error` 记录：提交完成后组件通常已卸载/输入区已复位，此时弹 toast 会给用户一个无从处置的提示。**何时该重新考虑**：若线上出现「静默失败导致归档长期缺失且无人察觉」，改为向提交结果区追加一条可查的非阻断提示。
- **顺序**：先解析/创建空间（AC-19/26/27）→ 再解析/创建目标文件夹（AC-15/16/17/18）→ 再逐文件判重与登记（AC-20/25）。

### 决策 8：新增不变量 INV-22（写边界）

- **备选**：A. 在 `release-contract.md` 表 2 新增 INV-22；B. 仅登记变更历史，不加不变量。
- **选定**：A（需求方 2026-09-24 确认）。
- **原因**：本特性引入了**自动创建知识空间**与**按业务名建文件夹**这两项写入行为，且目标空间由「按名解析」得到。把「只写当前用户自己的『应用文件』空间」钉成不变量，可防后续改动把写入扩散到用户的其他知识空间（例如复用同名空间时误判归属）。
- **何时该重新考虑**：若业务上允许归档到用户指定的其他空间，则该不变量需重新评审。

### 决策 9：新能力以**显式开关**挂载——因为该控件被两个表单共用

- **备选**：
  - A. 给 `InputFileComponent` 加一个默认**关闭**的开关（如 `knowledgePicker`），只有 `InputForm` 传 `true`。
  - B. 不加开关，两个表单都获得新能力。
  - C. 从 `InputFileComponent` 分叉出一个新组件专供 `InputForm`。
- **选定**：A。
- **原因**：`InputFileComponent` 有**两个**调用方——`InputForm.tsx`（`form_input` 节点，本特性目标）与 `InputFormSkill.tsx`（会话报告生成表单）。需求方 2026-09-24 裁决**只改前者**。B 会让技能表单出现无从解释的知识空间入口（且归集线挂在 `InputForm.submit()`，技能表单只拿能力不落归档，行为不一致）。C 会把两个表单共用的上传/校验/toast 逻辑复制一份，后续修一处漏一处——是比 A 更差的重复。
- **注意**：技能表单的字段来自 `getVariablesApi`，**没有 `multiple` 字段**（视为单选），因此合并语义对它天然是「覆盖」= 既有行为，无回归；真正需要开关挡住的是**知识空间入口**（归集只挂在 `InputForm.submit()`，技能表单本就碰不到）。
- **何时该重新考虑**：若需求方后续要求技能表单也支持从知识空间选文件（并归档），把开关打开、并在 `InputFormSkill.handleStart` 里补上归集调用即可——届时需同步修订 spec AC-28 与本决策。

### 决策 10：批量选择的**作用域**——「当前文件夹」默认动作 + 独立的「清空全部」（选定）

- **备选**：
  - A. 「全选 / 取消全选」的作用域 = **当前文件夹**，另给一个独立的「清空全部已选」。
  - B. 只提供一个动作，语义就是清空全部。
  - C. 两个动作都作用于**已加载的全部条目**（跨文件夹、跨空间）。
- **选定**：A（需求方 2026-09-24 选定；对应 AC-30 ~ AC-32）。
- **原因**：选中态是**跨文件夹累积**的（`selected` 以 `spaceId:fileId` 为键，见决策 4 的状态自持），用户会分次补齐。若「取消全选」等于清空全部，一次误点就会丢掉前面所有挑选——恰是 AC-09 / AC-12「分次补齐、不因一次操作清空」要防的事；而只给「清空全部」又不满足「在这个目录里快速反选」的诉求。故两个动作分开，作用域写进标签本身。
- **被否 C 的理由**：让「全选」跨目录生效，一次点击会产出大量用户没看见的选中项（其他目录、其他空间），提交前的复核成本反而更高。
- **已知代价**：弹窗底部多一个入口。
- **何时该重新考虑**：若后续要求「跨目录全选整个空间」，必须连带设计选中项预览，否则用户不知道选了什么。

### 决策 11：判重作用域——按**目标文件夹**（推翻初版的「整空间」，需求方 2026-09-24 改定）

- **背景**：初版口径是「整空间判重」（后端 `get_repeat_file` 只按 `knowledge_id` 过滤）。需求方实测发现：同一份材料已归档在「流动资金贷款尽调报告」文件夹时，客户名「比亚迪股份有限公司」文件夹的归档被判重跳过 → **目标文件夹是空的**，业务上不可接受。
- **备选**：
  - A. **给判重加作用域参数**（选定）：`upload/{space_id}` 与 `space/{space_id}/files` 各加可选 `dedup_scope`（默认 `space` = 今天的行为）；传 `folder` 时判重 SQL 追加「文件直接在该文件夹内」的条件（`file_level_path == f"{父目录.file_level_path}/{父目录.id}"`；空间根为 `""`/NULL）。
  - B. 复用既有的 `skip_dedup`（`process_one_file` 已有该开关，F028 导出在用）：前端改为按**文件名**在目标文件夹内判重。改动最小，但**内容级判重降级为文件名级**——同内容不同名会在同一文件夹里存两份，同名不同内容反而被跳过。
  - C. 命中空间级重复时，把既有文件**复制/引用**进目标文件夹。需要文件级 copy/link 端点，本仓库当前没有（只有 `/files/move` 移动与 `/knowledge/copy` 整库复制），成本高于 A。
- **选定**：A（需求方 2026-09-24 选定）。
- **原因**：判定口径在服务端（客户端看不到候选行的 md5），所以「按文件夹不重复」只能由后端提供；A 用可选参数承载，**既有调用方零回归**，且保住内容级判重；B 的降级会让「同内容不同名」在同一个文件夹内重复，与「文件夹内不重复」的诉求不符。
- **代价（需求方已知并接受）**：同一份文件归到 N 个客户文件夹即 **N 份副本**，占 N 份存储与配额；这是「每个文件夹各存一份」的必然结果。
- **必须成对使用**：预判（upload 的 `repeat`）与登记（`addFilesApi`）**必须传同一个作用域**。只把预判放大到整空间、登记按文件夹，就会出现「目标文件夹被跳过而变空」——正是本次要修的缺陷。
- **何时该重新考虑**：① 若产品改为「同一内容在空间内只存一份、多文件夹引用」，需要文件级 link/引用模型（比参数化大得多）；② 若 N 份副本的存储成本成为问题，可改为「跨文件夹命中时不复制、而在目标文件夹放一条引用」。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

**取值（选择/上传阶段）**

`InputForm 渲染文件字段` → `InputFileComponent`
→ 本地上传：`uploadFileWithProgress`（`isSSO` 路径，`api/apps.ts:298`）→ `POST /api/v1/knowledge/upload` → 取 `res.file_path`
→ 知识空间选择：**新增弹层** → `getFileDownloadApi(space_id, file_id)` → 取 `original_url`
→ 两条支路都回写组件内列表 → `onChange(文件名)` 给 `InputForm.updataFileName`、`onFileChange(取值)` 给 `InputForm.handleChange`

**提交**

`InputForm.submit()`（`InputForm.tsx:65`）→ 校验 required → 组装 `valuesObject[key] = 取值数组` 与展示用 `message` 文本
→ `emitAreaTextEvent(FORM_SUBMIT)`（`pages/appChat/useAreaText.ts:15/183`）
→ `useWebsocket.ts:491` 以 `action:'input'` 发出 `data:{[node_id]:{data:{[字段key]: [取值,...]}, message, ...}}`

**后端消费**

`InputNode.parse_upload_file(key, key_info, value: list[str])`（`workflow/nodes/input/input.py:340`）
→ 每个取值 `urlparse(...).path.split('/')[-1]` 取文件名 → `KnowledgeService.get_upload_file_original_name` 去 md5 前缀
→ `get_upload_file_path_content` → `file_download(file_url)` 下载（`core/cache/utils.py:379`）
→ 按节点配置的策略抽取文本 / 写入临时知识库

**归集（本特性新增，提交时触发）**

`InputForm.submit()` → 解析「客户名称」字段值（缺省用 `flow.name`）→ 按名在**可见空间**（`getMineSpacesApi` + `getJoinedSpacesApi` + `getDepartmentSpacesApi` 合并去重）里解析「应用文件」空间（无则 `createSpaceApi` 建私有）→ `getSpaceChildrenApi` 在该空间根目录找目标文件夹（无则 `createFolderApi`）→ 逐文件 `uploadFileToServerApi(space_id, file, undefined, {parentId, dedupScope:'folder'})` 取 `repeat`（**该文件夹内**是否已有） → 重复则跳过，否则 `addFilesApi(space_id, {file_path, parent_id, dedup_scope:'folder'})`

> 后端侧：`dedup_scope=folder` 时，`POST /knowledge/upload/{space_id}` 用 `resolve_folder_dedup_path`（先做 `upload_file` 权限校验，再算 `file_level_path`）把 `get_repeat_file` 的查询收窄到该文件夹；`POST /space/{space_id}/files` 把同一作用域透传给 `process_one_file` 的 `dedup_file_level_path`。两端**必须传同一个作用域**（决策 11）。

> 注意：`listKnowledgeFolders` 需要**已有** `space_id`、只列该空间下的文件夹，**不能**用来按名查找空间——按名解析必须走空间列表 API。
>
> 空间来源清单：**不请求 `getManagedSpacesApi`**——它是 mine∪joined 的后端合并结果，与上面三源重复；`department` 则**必须**保留，部门空间在 mine/joined 里都不出现。选择器的左栏**直接复用 `useCategorizedKnowledgeSpaces`**（同一三源，且按部门/我创建的/我加入的分组，与知识空间页面共用 query key）；归集侧按名解析仍走 `formFile/visibleSpaces` 的 `fetchVisibleSpaces`（同一答案的扁平合并）。
> 子项列表也只用 `getSpaceChildrenApi`（响应同时含文件夹与文件，按 `file_type` 拆分），不再叠加 `listKnowledgeFolders`；**带关键词时**改走 `searchSpaceChildrenApi`（在当前目录内搜索，页码分页）。

### 4.2 关键数据结构 / 字段约定

| 字段 / 结构 | 类型 / 格式 | 说明 | 谁会消费 |
|---|---|---|---|
| 表单文件字段的 `key` | string | 后端在 `InputNode.__init__` 里对 `type==='file'` 的项**重新生成** `file_<uuid8>` 运行时 key（`input.py:64-71`） | 后端节点参数；客户端只用提交时的静态 key，由 `handle_input` 做映射 |
| 文件字段的取值 | 数组，元素为**可下载地址**字符串 | 本地：上传端点 `file_path`；知识空间：`original_url`（presigned） | 后端 `parse_upload_file` |
| 「客户名称」字段的匹配 | 同表单内 `item.value`（展示名）去首尾空白后 `=== '客户名称'`，取该字段的值 | 值去首尾空白后作为文件夹名 | 归集流程 |
| 文件夹名 | 字符串 | 客户名称值；缺字段或值为空 → `flow.name` | `createFolderApi` |
| 目标空间名 | 固定字符串「应用文件」 | 私有；按名在用户可见空间内解析 | `createSpaceApi` / 空间列表 API |
| `UploadFileResponse.repeat` | boolean | 上传端点按 `get_repeat_file` 判定「该 space 内是否已有同内容/同名文件」 | 归集的判重步骤（决策 5） |
| `KnowledgeFile._raw` / `status===3` | — | `addFilesApi` 响应中重复行的既有标记（客户端为此存在「保留原始行供 retry」的约定） | **本特性不使用**——为避免既文件的破坏性副作用（坑 1） |

### 4.3 关键模块职责

| 模块 / 文件 | 职责 | 不做什么 |
|---|---|---|
| `pages/appChat/components/InputFileComponent.tsx` | 表单文件字段的取件（本地 / 知识空间）、合并与去重、展示；**并在提交前保留本地文件的 `File` 对象**（供归集的第二次上传，决策 5）。**被 `InputForm.tsx` 与 `InputFormSkill.tsx` 共用**，新能力以默认关闭的开关挂载（决策 9） | 不做提交编排、不做归集；不对技能表单开启知识空间入口 |
| （新增）知识空间文件选择器组件 | **两栏**：左栏分组空间（`useCategorizedKnowledgeSpaces`，打开即选中第一个，切换空间不清空左栏），右栏返回箭头 + 路径 + 关键词搜索（`searchSpaceChildrenApi`）+ 带复选框的文件行 + **列表头部三态「全选本目录」（AC-30）** + 底部已选计数与**「清空全部已选」（AC-31）**；单选/多选、按字段 `suffixes` 置灰（AC-29）、回传选中项 | 不写知识空间、不判定重复、不新增 HTTP 端点、**不在单选模式下渲染批量选择入口（AC-32）** |
| （新增）归集模块（纯函数 + 薄副作用层） | 输入为「字段的本地文件列表（含 `File` 对象）+ 客户名称值 + 应用名」；解析文件夹名、解析/创建空间与文件夹、逐文件判重与登记——判重与登记都带 `dedup_scope: 'folder'` + 目标 `parent_id`（决策 11） | 不碰表单状态、不阻断提交；不改动其他调用方的判重口径 |
| `pages/appChat/components/InputForm.tsx` | 表单值与展示名的组装、提交、触发归集 | 不持有文件合并状态（决策 4） |
| `api/knowledge.ts` | 空间/文件夹/子项/上传/登记/下载地址；上传与登记各带可选 `dedup_scope`/`parent_id` | 不新增端点、不改既有调用的默认行为 |
| `knowledge/domain/models/knowledge_file.py`（Platform 分区） | 判重查询支持可选 `file_level_path`（同层过滤）；默认参数下行为与改动前逐字一致 | 不改变 md5/文件名匹配规则与版本行排除 |
| `knowledge/domain/services/knowledge_space_service.py`（Platform 分区） | `_folder_scope_path` / `resolve_folder_dedup_path`（权限 + 目录路径）；`add_file(dedup_scope)` 透传给 `process_one_file` | 不改变未传作用域时的整空间判重 |
| `knowledge/api/endpoints/knowledge.py`（Platform 分区） | 上传端点接受 `parent_id`/`dedup_scope`，`folder` 时按该文件夹回答 `repeat`（并做 `upload_file` 权限校验） | 不改变不带参数时的响应 |

---

## 5. 已知坑 / 反直觉事实

| # | 反直觉事实 | 如果不知道会怎样 | 在哪处理 |
|---|---|---|---|
| 1 | `addFilesApi` 命中重复时**会改既有文件行**：`object_name` 改指 `tmp/{id}.{ext}`、写入本次内容、写 `remark`、`status` 置 `FAILED`（等前端 retry 覆盖）。它**不是**只返回一个重复标记 | 用「调用后按 `status===3` 跳过」实现去重，会把用户空间里原本正常的文件变成 FAILED（检索不到） | 归集必须先判重（决策 5）；判重用上传端点的 `repeat` |
| 2 | `InputForm` 对文件字段固定传 `value={''}`，且 `formDataRef` 是 `useRef`——写入不触发重渲染 | 把合并结果只放在 `formDataRef`，输入框展示不会更新；或试图用受控 prop 驱动合并，永远拿不到上一次的值 | 合并状态由 `InputFileComponent` 自持（决策 4） |
| 3 | 后端去重范围**默认是整个 knowledge（空间）**，由调用方通过 `dedup_scope` 决定是否收窄到某个文件夹（决策 11）；无论哪种范围都排除「非主版本」的残留行 | 误以为判重永远是整空间（于是归档会把「已在别的客户文件夹」的文件跳过，目标文件夹变空——2026-09-24 实测缺陷）；或误以为默认已是文件夹级 | `get_repeat_file` / `get_file_by_condition`（`knowledge_file.py:442/454`）+ `process_one_file` 的 `dedup_file_level_path`；调用方见 `archiveLocalFiles.ts` |
| 4 | 该响应里 `status===3` 实际语义是 DUPLICATE（配合 `oldFileLevelPath`），但 `mapFileStatus` 把 3 映射为 `FAILED`，且 `RawKnowledgeFile.status` 的注释写着「3 success」——三处口径不一致 | 按注释理解 `status`，判断重复时取错分支 | 客户端既有约定以 `status===3` + `_raw` 为准；本特性绕开该字段（坑 1） |
| 5 | `parse_upload_file` 用 `urlparse(取值).path.split('/')[-1]` 取文件名，再过 `get_upload_file_original_name` 去 md5 前缀 | 取值若不带「文件名在路径末段」的形状（如纯 `file_id`），后端会把 id 当文件名、解析出空内容 | 取值统一为地址（决策 3） |
| 6 | `file_download` 有三个分支：本地路径、http(s) URL（命中 MinIO share host 走 SDK，否则普通 GET）、相对 MinIO 路径（需含 `X-Amz-Algorithm`） | 以为只能接受本地上传那种路径，从而误判「知识空间文件没法复用」 | 直接影响需求 1 的可行性判断（`core/cache/utils.py:379`） |
| 7 | 知识空间文件的 `original_url` 是 **presigned**，有时效 | 用户打开选择器后长时间停留再提交，后端下载失败 | 已知边界，spec §3 记录；改法见决策 3 的「何时重新考虑」 |
| 8 | 空间文件的下载地址需要**单独授权**，与预览权限分开（预览接口的注释明说「viewing does not imply download」） | 用预览地址或以为选了就能取到下载地址，导致部分文件取不到 | `getFileDownloadApi`（`api/knowledge.ts:1777`），失败按 AC-07 处理 |
| 9 | 上传有大小上限，来自 `bishengConfig.uploaded_files_maximum_size`（默认 50MB） | 归集的第二次上传未校验大小，超出上限时后端报错、归集失败 | 复用组件内既有的 `checkFileSize` |
| 10 | 表单的 `file` 字段在**构造表单 schema 时**就被替换为运行时 key（`file_<uuid8>`），提交用的是**静态 key**，由 `InputNode.handle_input` 做映射 | 误以为提交要发运行时 key（或反之），导致字段匹配不上 | 客户端无需感知；记录以防后端改动时误判 |
| 11 | 拖拽/粘贴上传走的是另一套入口（`useFileDropAndPaste`），与表单文件字段无关 | 误把主对话输入区的上传纳入归集范围，违反 AC-24 | 归集入口只挂在 `InputForm` 的提交 |
| 12 | `InputFileComponent` 的 `isSSO` 路径在拿到 `file_path` 后**丢弃 `File` 对象**，只留下文件名与路径 | 提交时想再上传一次做判重，却已拿不到文件本体；只能退回去调 `addFilesApi`，从而踩坑 1 | 组件需改为保留 `File` 直到提交（决策 5 的实现前提） |
| 13 | `listKnowledgeFolders` 需要**已有** `space_id`，只列某个空间的文件夹；**没有**「按名查空间」的能力 | 误用它按名解析「应用文件」空间，永远查不到 | 按名解析走空间列表 API（mine + joined + department；**不用** managed，见 §4.1） |
| 14 | `InputFileComponent` **被两个表单共用**：`InputForm.tsx`（`form_input` 节点）与 `InputFormSkill.tsx`（会话报告生成表单） | 直接给控件加选择器入口，技能表单凭空多出一个知识空间入口；且它只拿能力、拿不到归档（归集挂在 `InputForm.submit()`），两个入口行为不一致 | 新能力用默认关闭的开关挂载，只有 `InputForm` 传 `true`（决策 9） |
| 15 | 技能表单的字段来自 `getVariablesApi`，**没有 `multiple` 字段** | 以为需要为技能表单单独处理多选，或误判它有回归 | 缺 `multiple` ⇒ 按单选处理，与既有行为等价（决策 9 的「注意」） |
| 16 | `import { ... } from '~/components/ui'` 是**全量 barrel**：它经 `ui/index.ts` 把 `CommentDialog` → `~/hooks` → `usePresets` → `filenamify`（ESM-only）全部拉进来 | 任何渲染/测试单个对话框的文件都会因 jest 无法转译 `filenamify` 而**整个测试套件起不来**（不是断言失败，是加载失败） | 新代码用**窄导入**：`@bisheng/ui` + `~/components/ui/Dialog`（本特性 T-06 即如此） |
| 17 | `useLocalize` 读 Recoil atom，而 Recoil 的**任何 import 都被禁止**（client `AGENTS.md` + lint） | 组件测试要么被迫 import recoil 建 `RecoilRoot`（lint 报错），要么渲染即抛「must be used inside a RecoilRoot」 | 只断言 testid 的测试直接 mock `~/hooks/useLocalize`（本特性 T-06 即如此） |
| 18 | `client/tsconfig.json` 把 `types` 数组写在了 **`compilerOptions` 外面**，等于没生效 → jest-dom 的类型从不加载 | 新测试文件里 `toBeInTheDocument` / `toBeDisabled` 会让 `pnpm typecheck` 报 `Property '…' does not exist on type 'JestMatchers<…>'`；而既有的解法（`@ts-strict-ignore` 头）**禁止**在新文件使用，于是新测试无法走老路 | 本特性测试**不用** jest-dom 匹配器：`getBy*` 缺失即抛，故 `expect(...).toBeTruthy()` / `queryBy*).toBeNull()` / `(el as HTMLButtonElement).disabled` 等价。**根因属既有缺陷**，修它是另一件事 |
| 19 | 本选择器的版式参考**兄弟仓库 `jinbao`** 的 `KnowledgeFilePickerDialog`，但它的可用性判定来自 `@bisheng/file-field`（`isSelectable` / `togglePick` / `unselectableReason` / `PickedFile`），**本仓库没有该包**，`KnowledgeFile` 也没有它的 `objectName` 字段 | 照抄参考实现会 import 一个不存在的包，或按 `objectName` 判「文件尚未就绪」而恒为 undefined，把可用行全判死 | 只移植版式与交互：可用性判定用本仓库的 `KnowledgeFilePicker/suffixMatch.ts`（按字段 `suffixes` 大小写不敏感匹配；列表为空/未声明 ⇒ 全可选），选中项仍是本仓库的 `PickedKnowledgeFile{spaceId,fileId,name}`。**不引入新依赖** |
| 20 | 一行文件行里既要放本行的点击（选中）又要放 `Checkbox`，而 Radix 的 `Checkbox` 本身渲染成 `<button>` | 用 `<button>` 包行会形成 button 套 button（非法嵌套） | 行用 `div[role=button]` + `onKeyDown` 处理 Enter/Space（参考实现同款），`Checkbox` 加 `pointer-events-none` 只作展示；禁用态走 `aria-disabled` |
| 21 | 「全选」只覆盖**当前列表里已加载的条目**：分页是游标/页码两套（见 `useKnowledgeBrowse.ts`），`PAGE_SIZE=200`，列表底部另有「加载更多」；**搜索状态下**当前列表是搜索结果，于是全选作用于「匹配关键词的那批」而非整个文件夹 | 以为「全选」= 整个文件夹的全部文件，于是在超过一页的目录、或带关键词的列表里给出不完整的选中却不自知；也可能反过来以为搜索时禁用全选才对 | AC-30 已写明作用域；本条是给维护者的提醒——**作用域 = 屏幕上那份列表**（含关键词过滤），要做「跨页/忽略关键词的全选」得先把后续页拉全或清空关键词，不是改个 `onClick` 的事 |
| 22 | `~/locales/i18n.ts` 用 i18next **默认插值**：占位符是 `{{0}}`（双花括号），单花括号 `{0}` 原样输出。本仓库两种写法都存在（`com_inputfile_exceed_limit` 用 `{{0}}`，是对的） | 文案里写成 `"已选 {0} 项"` 不会报错、不会告警，UI 上就字面显示 `{0}`——本特性**踩了两次**（`pick_resolve_failed` 自 T-04 起、`pick_selected_count` / `pick_select_all_count` 自 T-21 起） | 占位符统一 `{{0}}` / `{{1}}`，参数仍是 `{ 0: n }`；组件测试 mock 掉了 `useLocalize`，**测不到**这类问题，只能靠真机或对照既有 `{{0}}` 文案自查 |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 形式 | 谁在用 |
|---|---|---|
| 「应用文件」知识空间（名字字面量） | 知识空间实体 + 命名约定 | 本特性自己（后续若有其他 feature 要读该空间，须先登记） |
| 文件夹命名口径「客户名称 → 否则应用名称」 | 业务约定 | 同上 |
| **INV-22** 写边界 | 版本契约不变量 | 所有后续 feature（写入该空间时需遵守） |
| 知识空间文件选择器组件 | 前端组件（props: 空间/文件夹浏览、单选/多选开关；回调回传选中项） | 本特性（表单上传组件）；后续若其他输入区要选知识空间文件可复用 |
| 归集模块（含纯函数「解析文件夹名」「合并去重」） | 前端模块内部接口 | 本特性；纯函数可被单测与其他表单入口复用 |

> **不新增**任何 HTTP API、消息字段或前端导出接口。表单提交的 payload 形状不变（AC-23）。

### 6.2 我依赖别人的（Incoming）

| 依赖 | 形式 | 风险点 |
|---|---|---|
| 输入节点对文件取值的解析方式（URL 形态 → 下载 → 抽取） | 隐式数据契约 | 若后端改为只接受 `file_id`，本特性的取值形态失效（决策 3 需重评） |
| `POST /api/v1/knowledge/upload/{knowledge_id}` 的 `repeat` 语义**与 `dedup_scope`/`parent_id` 入参**（本特性新增的可选契约，默认 `space` = 原语义） | 隐式数据契约（F065 起显式化）：`folder` 时带 `upload_file` 权限校验，`repeat` 只回答「该文件夹内是否已有」 | 若该字段被移除、或作用域参数被忽略（回到整空间语义），归档会重新出现「目标文件夹变空」；若只按文件名判定，判重会失真（决策 5 的前提） |
| `addFilesApi` 命中重复时的破坏性副作用 | 隐式行为契约 | 本特性靠「预先判重」绕开；若后端改为无副作用，可简化（决策 5 的「何时重新考虑」） |
| `getFileDownloadApi` 返回 presigned `original_url` | 隐式数据契约 | presigned 有效期（坑 7） |
| 知识空间按名解析（空间列表 API 的可见范围） | 隐式数据契约 | 私有空间的可见范围决定「每人一份」（决策 6） |
| `listKnowledgeFolders` / `createFolderApi` / `createSpaceApi` / `uploadFileToServerApi` | 内部 HTTP API | 既有端点，复用不改造 |

---

## 7. 测试与可观测

- **单元测试（Vitest/Jest，对应 tasks 的组件级任务）**：合并与去重（多选追加 + 同名跳过；单选覆盖）、展示名与取值一致性、文件夹名解析（有客户名称 / 无字段 / 空值 / 全空白 / 应用名为空）、判重分支（`repeat=true` → 不调 `addFilesApi`）。
- **纯函数优先**：把「解析文件夹名」「合并去重」「拆分本地/知识空间来源」抽成纯函数测试，UI 组件只测接线。
- **前置条件**：①`pnpm dev:client`（:4001），并有一个后端可用（`VITE_DEV_API_TARGET`）；②一个**工作流型应用**，其输入节点为 `form_input` 且含至少一个 `type=file` 字段（另需两个变体：配 `multiple` 的、不配的）；③一张已上传到某知识空间的文件（供选择器挑）；④两个变体应用的差别是**有无 label 为「客户名称」的字段**。
- **手动验证路径**：打开该应用 → ①从知识空间选文件（单选/多选各一次）→ ②本地选文件 → ③分次追加验证合并与同名去重 → ④单选验证覆盖 → ⑤填/不填客户名称各提交一次 → ⑥到该用户自己的「应用文件」空间核对：空间存在且**私有**、文件夹名正确、文件已入、重复文件未重复入**且既有文件状态未被改成失败**（坑 1 的回归点）。
- **关键排障点**：归集失败只 `console.error`（决策 7），排查时需看浏览器控制台；建议在归集模块内为每一步打印可区分的前缀日志（空间/文件夹/判重/登记）。
- **e2e**：按 `/e2e-test` 走一遍上述 ①–⑤（Playwright）。

---

## 8. 后续改进 / 不打算做的事

- **不打算做**：重复文件的覆盖交互（本期静默跳过）；按文件夹（而非整空间）去重；跨用户共享归档；文件夹名自定义。
- **已知短板**：
  - presigned 地址的时效性（坑 7）——改为「提交前统一换取」可消除，代价是提交路径插入异步步骤。
  - 被判重的文件需要多上传一次（决策 5 的代价）——后端若提供「只判重不落盘」接口即可省掉。
  - 归集失败静默（决策 7）——可观测性弱，靠控制台日志。
- **重构触发条件**：若知识库页面把文件浏览重构为受控组件（决策 2 的「何时重新考虑」），选择器应改为复用，避免两套浏览逻辑漂移。

---

## 修订历史

| 日期 | 改动 | 触发原因 |
|---|---|---|
| 2026-09-24 | 初版 | spec ★ 确认后编写 |
| 2026-09-24 | 新增决策 9（共享控件的新能力开关）+ 坑 12~18 | spec/tasks 评审：技能表单共用该控件（影响面）；实现期暴露 barrel 导入、Recoil 冻结、tsconfig `types` 错位三处坑 |
| 2026-09-24 | §4.1/坑 13：可见空间收窄为 mine+joined+department，按名解析不再列 `managed`；子项列表只用 `getSpaceChildrenApi` | 实现 T-05 时确认 `managed` 是 mine∪joined 的后端合并（冗余） |
| 2026-09-24 | §4.1/§4.3 + 坑 19~20：选择器改为两栏（左栏分组空间、右栏路径+搜索+复选框行+计数）、按字段 `suffixes` 置灰、带关键词走 `searchSpaceChildrenApi` | 需求方要求版式对齐兄弟仓库 `jinbao` 的 `KnowledgeFilePickerDialog`；该参考实现依赖本仓库没有的 `@bisheng/file-field`，故只移植版式 |
| 2026-09-24 | 新增决策 10 + 坑 21 + §4.3；spec 补 AC-29 ~ AC-32 | 需求方要求「文件夹下支持全选/取消全选」；澄清后确定作用域为当前文件夹、另给独立的「清空全部」、单选不提供。**同时补正**：类型置灰（`suffixes`）此前被误标为 AC-08（那是「单选只产生一个文件」），现归到 AC-29 |
| 2026-09-24 | **新增决策 11 + 修订 AC-20/§3**：判重作用域由「整个空间」改为「目标文件夹」（后端加可选 `dedup_scope`，默认行为不变）；坑 3、决策 5、§4.1、§4.3 同步改写 | 需求方实测：「同一份材料已在另一个客户文件夹 → 本客户文件夹被判重跳过而变空」。改为每文件夹各存一份（N 份副本的存储代价已确认接受），这是**推翻 ★ 确认口径**的一次变更 |
