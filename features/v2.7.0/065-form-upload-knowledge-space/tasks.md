# Tasks: 表单上传组件的知识空间选择与本地文件归集（065-form-upload-knowledge-space）

> 执行流水（append-only）。每项绑定验证标准：`[步骤] → 验证：[检查项]`。
> 需求口径见 [spec.md](./spec.md)，设计真相见 [design.md](./design.md)（接手第一入口）。
> 开发基线：本特性 docs 与代码落在 `feat/2.7.0/guangxi-ns-llm-guardrail`（需求方 2026-09-24 选定，与 F063/F064 同口径）。
> **分区**：初始实现全部为 **Client 分区**（`src/frontend/client/`）。2026-09-24 追加 **Wave G（判重作用域）** 后同时涉及 **Platform 分区**（`src/backend/bisheng/knowledge/`）。始终不涉及 Worker。

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 用户已确认（★ 2026-09-24）；AC-28 为同日补充 |
| design.md | ✅ 已评审 | 用户已确认（★ 2026-09-24）；接手第一入口 |
| tasks.md | ✅ 已拆解 | `/sdd-review tasks` 通过（2026-09-24；3 条 low 已记录跳过） |
| 实现 | 🚧 进行中 | Wave A~E ✅（19/20；T-20 端到端手动验证**未执行**——需真实工作流应用与后端，见执行记录）；Wave F ✅（版式重做 + 批量选择，T-21）；**Wave G 🚧**（归档判重作用域，T-22~T-25）。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |

---

## 开发模式

- **Wave 组织**：Wave A 为纯函数与文案（无依赖，可先行）→ B（选择器）→ C（控件改造，依赖 A+B）→ D（归集，依赖 A+C）→ E（契约与质量门）。逐 Wave 内的任务彼此无依赖，可并行。
- **前端验证形态**：纯函数（T-01/T-02/T-03）**必须有单测**；UI 组件测接线，用 `screen` 断言（Radix portal 场景**禁止**对 `container` 断言「不出现」——对 portal 恒真，F064 T-13 的教训）。测试框架：client 既有 Jest（`src/frontend/client/`，`pnpm test:ci`）。
- **命名约定**：新增文件路径为**拟定路径**，实现时若与既有目录约定冲突可调整，但须保持「纯函数与副作用分离」（design 决策 5/§4.3）。
- **自包含**：每个任务内联文件与逻辑；设计论证指向 design §X，不复制。

---

## Wave A — 纯函数与文案（无依赖）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-01 | 目标文件夹名解析纯函数<br>**文件**：新增 `src/frontend/client/src/pages/appChat/formFile/resolveFolderName.ts` + `resolveFolderName.test.ts`<br>**逻辑**：入参 `(formFields, appName)`；找展示名去首尾空白后 `==='客户名称'` 的字段，取其值 trim；无字段或值为空 → `appName`；`appName` 亦空 → `null` | AC-15, AC-16 | 单测 5 例：① 有客户名称 ⇒ 用其值；② 无该字段 ⇒ 应用名；③ 值为全空白 ⇒ 应用名；④ 应用名亦空 ⇒ `null`；⑤ 值含首尾空白 ⇒ 取 trim 后 | ☑ |
| T-02 | 文件列表合并去重纯函数<br>**文件**：新增 `src/frontend/client/src/pages/appChat/formFile/mergeSelectedFiles.ts` + `.test.ts`<br>**逻辑**：入参 `(existing, incoming, multiple)`；条目形如 `{name, value, source: 'local'|'knowledge', file?: File}`；多选 ⇒ 追加并按 `name` 去重；单选 ⇒ 结果只含最后一项 | AC-09, AC-10, AC-11, AC-12 | 单测：① 多选 2+1 ⇒ 3；② 多选同名（本地↔知识空间交叉）⇒ 不加入且原列表不变；③ 单选第二次 ⇒ 长度 1 且为后者；④ `incoming` 为空 ⇒ 返回原列表（不产生新引用）；⑤ 同名判定对两来源一视同仁 | ☑ |
| T-03 | 归集输入拆分纯函数<br>**文件**：新增 `src/frontend/client/src/pages/appChat/formFile/splitLocalSources.ts` + `.test.ts`<br>**逻辑**：从条目数组筛出 `source === 'local'` 的条目（供归集），并导出「在表单项里找客户名称字段」的查找函数（与 T-01 共用同一判据，避免两处漂移） | AC-21 | 单测：① 混合来源 ⇒ 只返回本地条目；② 全为知识空间来源 ⇒ 返回空数组（调用方据此整体跳过归集）；③ 查找函数与 T-01 对同一输入给出一致结论 | ☑ |
| T-04 | 三语文案<br>**文件**：`src/frontend/client/src/locales/zh-Hans/translation.json`、`.../en/translation.json`、`.../ja/translation.json`<br>**逻辑**：补齐选择器与归集的全部新 key（入口按钮、弹层标题、面包屑根级、空态、失败提示等），三语同 PR 补齐，**禁止硬编码中文** | i18n CI | `pnpm check-i18n` OK；三份 JSON 可解析、无重复 key；`eslint` 对改动文件 exit 0 | ☑ |

## Wave B — 知识空间文件选择器（依赖 Wave A 的 T-04 文案）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-05 | 浏览数据层 hooks<br>**文件**：新增 `src/frontend/client/src/pages/appChat/components/KnowledgeFilePicker/useKnowledgeBrowse.ts` + `.test.ts`<br>**逻辑**：react-query v4（**不得新增 Recoil atom**）。空间列表 = `getMineSpacesApi` + `getJoinedSpacesApi` + `getManagedSpacesApi`（`~/api/knowledge`）按 id 合并去重；子项 = `listKnowledgeFolders`（文件夹）+ `getSpaceChildrenApi`（文件），按 `parent_id` 逐层 | AC-01, AC-06 | 单测（mock `~/api/knowledge`）：① 三个来源合并后按 id 无重复；② 只含返回的空间（不额外请求未授权的）；③ 文件夹与文件按 `file_type` 0/1 正确区分；④ 进入子文件夹后请求带对应 `parent_id` | ☑ |
| T-06 | 选择器骨架：弹层 + 面包屑 + 三级导航<br>**文件**：新增 `src/frontend/client/src/pages/appChat/components/KnowledgeFilePicker/index.tsx` + `.test.tsx`<br>**逻辑**：空间 → 文件夹 → 文件逐层；面包屑可回退；**文件夹条目可进入但不可选中**；空态与加载态 | AC-01, AC-06 | 组件测试（`screen`）：① 选空间后展示其根级文件夹与文件；② 点文件夹 ⇒ 进入（面包屑多一级）而非选中；③ 点面包屑上级 ⇒ 回到上级；④ 无文件时展示空态 | ☑ |
| T-07 | 选择器交互：单选/多选、取消、回传<br>**文件**：同 `KnowledgeFilePicker/index.tsx`（增量）+ `.test.tsx`<br>**逻辑**：`multiple` prop 控制可选数量；`multiple=false` 时选中新项即替换旧项；确认 ⇒ 回传选中项数组；取消 ⇒ 不回调 | AC-02, AC-03, AC-05, AC-08 | 组件测试：① `multiple=false` 连选两项 ⇒ 仅后者被回传；② `multiple=true` 连选两项 ⇒ 两项都在；③ 点取消 ⇒ 回调零调用；④ 未选任何即确认 ⇒ 回调零调用（AC-05） | ☑ |
| T-08 | 选中项 → 字段取值<br>**文件**：新增 `src/frontend/client/src/pages/appChat/components/KnowledgeFilePicker/resolvePickedValues.ts` + `.test.ts`<br>**逻辑**：对每个选中文件调 `getFileDownloadApi(space_id, file_id)`（`~/api/knowledge`）取 `original_url`；单个失败 ⇒ 该文件不产出 + 记录失败，其余照常产出 | AC-04, AC-07 | 单测（mock api）：① 全部成功 ⇒ 每条带 `original_url`；② 两个文件中一个 reject ⇒ 只产出成功那个且失败计数 1；③ 全部 reject ⇒ 产出为空、失败计数 = 总数 | ☑ |

## Wave C — 表单上传组件改造（依赖 Wave A；T-11 依赖 Wave B）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-09 | 合并去重状态 + 保留 `File` 对象<br>**文件**：`src/frontend/client/src/pages/appChat/components/InputFileComponent.tsx`（+ 新增 `.test.tsx`）<br>**逻辑**：组件自持条目列表，用 T-02 的纯函数驱动多选追加 / 单选覆盖；**本地条目保留 `File` 实例直到提交**（design 决策 5 的实现前提 / 坑 12） | AC-09, AC-10, AC-11, AC-12 | 组件测试：① 多选分两次添加 ⇒ 列表累积；② 第二次同名 ⇒ 不累积；③ 单选第二次 ⇒ 只剩后者；④ 本地条目上仍挂着 `File` 实例（断言 `instanceof File`） | ☑ |
| T-10 | 新能力开关（默认关闭）<br>**文件**：同 `InputFileComponent.tsx`（增量）；`src/frontend/client/src/pages/appChat/components/InputForm.tsx`（传 `true`）<br>**逻辑**：加一个默认 `false` 的 prop 控制知识空间入口；仅 `InputForm` 显式开启。`InputFormSkill.tsx` **零改动** | AC-28 | 组件测试：① 不传开关 ⇒ **不渲染**知识空间入口（用 `screen`：入口无 `data-testid`/文案）；② 传 `true` ⇒ 渲染；③ `git diff --stat` 显示 `InputFormSkill.tsx` 无改动 | ☑ |
| T-11 | 接入选择器入口<br>**文件**：同 `InputFileComponent.tsx`（增量）<br>**逻辑**：入口按钮打开 T-06 弹层（`multiple` 跟随字段配置）；T-08 产出的取值并入条目列表；取消/空选不改动列表 | AC-01, AC-02, AC-03, AC-04, AC-05, AC-08 | 组件测试：① 点入口 ⇒ 弹层出现；② 弹层回传 2 项 ⇒ 列表 +2；③ 弹层取消 ⇒ 列表不变；④ 断言传给弹层的 `multiple` 等于字段配置 | ☑ |
| T-12 | 展示名与取值同步<br>**文件**：同 `InputFileComponent.tsx`（增量）<br>**逻辑**：`onChange` 传展示名数组、`onFileChange` 传取值数组，两者下标一一对应；展示名去重 | AC-13, AC-23 | 组件测试：① 3 项 ⇒ 两个数组长度均 3 且下标对应；② 单选 ⇒ 两者长度均 1；③ 展示名数组无重复项 | ☑ |

## Wave D — 归集（依赖 Wave A；T-16 依赖 Wave C）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-13 | 空间解析 / 自动创建（私有）<br>**文件**：新增 `src/frontend/client/src/pages/appChat/formFile/archiveLocalFiles.ts` + `.test.ts`<br>**逻辑**：按名在**空间列表 API**（`getMineSpacesApi` / `getJoinedSpacesApi` / `getManagedSpacesApi`）里找「应用文件」；找到 ⇒ 复用；未找到 ⇒ `createSpaceApi({name:'应用文件', auth_type:'private'})` | AC-19, AC-26, AC-27 | 单测（mock api）：① 已有同名 ⇒ `createSpaceApi` 零调用；② 无 ⇒ 调一次且 `auth_type === 'private'`；③ 创建后用返回的 id 继续（断言后续调用带该 id） | ☑ |
| T-14 | 文件夹解析 / 复用 / 创建<br>**文件**：同 `archiveLocalFiles.ts`（增量）+ `.test.ts`<br>**逻辑**：用 T-01 的结果，在空间根目录按名找目标文件夹（`listKnowledgeFolders`）；找到 ⇒ 复用；未找到 ⇒ `createFolderApi(space_id, {name, parent_id: null})`。**同一提交内只解析一次**（多字段共用） | AC-17, AC-18, AC-25 | 单测（mock api）：① 已有 ⇒ 不创建；② 无 ⇒ 创建一次；③ 同一提交内的多次解析（多字段）⇒ 创建只被调 **1** 次（共用文件夹） | ☑ |
| T-15 | 逐文件判重与登记<br>**文件**：同 `archiveLocalFiles.ts`（增量）+ `.test.ts`<br>**逻辑**：对每个本地条目 `uploadFileToServerApi(space_id, file)` 取 `repeat`；`repeat === true` ⇒ **跳过、不调 `addFilesApi`**；否则 `addFilesApi(space_id, {file_path: [resp.file_path], parent_id: folderId})` | AC-14, AC-20, AC-21 | 单测（mock api）：① **`repeat=true` ⇒ 断言 `addFilesApi` 零调用**（design 坑 1 的核心回归点）；② `repeat=false` ⇒ 调一次且 `parent_id` = 文件夹 id；③ 输入含知识空间来源条目 ⇒ 不参与上传（`uploadFileToServerApi` 零调用）；④ 文件夹名为 `null`（T-01 返回 null）⇒ 整体跳过归集 | ☑ |
| T-16 | 接线到 `InputForm.submit()`，不阻断提交<br>**文件**：`src/frontend/client/src/pages/appChat/components/InputForm.tsx`（增量）+ 测试<br>**逻辑**：提交前组装归集输入（T-03 的本地条目 + T-01 的文件夹名 + `flow.name`）并触发；整体 `try/catch`，失败仅 `console.error`（design 决策 7），**不阻断提交、不改 payload** | AC-22, AC-23 | 组件测试：① 归集步骤 reject ⇒ 提交事件**照常发出**且 payload 与「不归集」时逐字段一致；② `console.error` 被调用；③ `submit` 未抛出；④ 无本地文件时不做任何归集请求 | ☑ |
| T-17 | 回归确认：技能表单与主对话输入区不受影响<br>**文件**：无代码改动（纯验证）<br>**逻辑**：确认 `InputFormSkill.tsx` 与 `InputFiles.tsx` / `useFileDropAndPaste.ts` 未被改动，且技能表单行为不变 | AC-24, AC-28 | ① `git diff --stat` 证明这三个文件零改动；② 手动验证技能表单文件字段仍走原上传路径、无知识空间入口（design §7） | ☑ |

## Wave E — 契约登记与质量门

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-18 | `release-contract.md` 登记核对<br>**文件**：`features/v2.7.0/release-contract.md`<br>**逻辑**：核对表 1 的 F065 行、表 2 的 **INV-22**、表 3 依赖行、变更历史行已在（本特性已预先登记），确认无错漏 | — | 契约含 F065 行 + INV-22 条目 + 变更历史 2026-09-24 行；**未修改**任何既有 INV 文本；`grep INV-22` 命中表 2 | ☑ |
| T-19 | 质量门<br>**文件**：无代码改动<br>**逻辑**：在 `src/frontend/` 跑 `pnpm lint` + `pnpm typecheck` | — | 两个命令 exit 0；**未**新增 `@ts-strict-ignore` 文件头；**未**手改 `eslint-suppressions.json`；改动文件若有既有违规已按规则 `lint:prune` | ☑ |
| T-20 | 端到端手动验证<br>**文件**：无代码改动<br>**逻辑**：按 design §7 的 4 项前置条件与 6 步路径走查 | AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-09, AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18, AC-19, AC-20, AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-27, AC-28 | 走完 design §7 ①~⑥；**重点核对**：⑥ 中既有文件状态未被改成失败（design 坑 1）、空间为**私有**（AC-27）、技能表单无新入口（AC-28） | ☐ |

## Wave F — 增量需求（版式重做 + 批量选择）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-21 | 类型门槛 + 全选/取消全选/清空全部<br>**文件**：`KnowledgeFilePicker/suffixMatch.ts`（新增，AC-29 类型门槛）、`KnowledgeFilePicker/selectAll.ts`（新增，纯函数「本目录可选中集合」）、`KnowledgeFilePicker/index.tsx`（头部三态全选行 + 底部「清空全部已选」）、三语文案、`InputFileComponent.tsx`（透传 `suffixes`）<br>**逻辑**：多选时头部渲染三态复选框（勾选=并入本目录可选中文件、取消=只移除本目录文件），底部「清空全部」跨目录清空；单选模式两者都不渲染；作用域为**当前列表已加载条目**（含关键词过滤，见 design 坑 21） | AC-29, AC-30, AC-31, AC-32 | 单测 + 组件测试：① 全选只纳入本目录可选中文件（置灰行不参与）；② 取消全选只影响本目录，其他目录已选保留；③ 部分选中呈 indeterminate；④ 本目录无可选中文件时全选行禁用；⑤ 「清空全部」跨目录/跨空间生效；⑥ **单选模式下两个入口都不渲染**；⑦ 类型门槛单测（`.PDF` vs `a.pdf`、空列表全通过） | ☑ |

## Wave G — 归档判重改为「文件夹作用域」（推翻初版整空间口径，见 design 决策 11）

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-22 | 后端：判重查询支持同层过滤<br>**文件**：`src/backend/bisheng/knowledge/domain/models/knowledge_file.py`（`_same_folder_filter` + `get_repeat_file` / `get_file_by_condition` 的 `file_level_path` 参数）、`domain/services/knowledge_service.py`（`process_one_file(dedup_file_level_path)`）<br>**逻辑**：不传参数 ⇒ 与改动前**逐字一致**的整空间查询；传了 ⇒ 追加「文件直接在目标文件夹内」条件（根目录兼容 `""` 与 NULL） | AC-20 | 单测（编译 SQL 断言）：① 传路径 ⇒ 含 `file_level_path = '<path>'`；② **不传 ⇒ 语句中不含 `file_level_path`**（零回归证明）；③ 空串 ⇒ 同时含 `= ''` 与 `IS NULL` | ☑ |
| T-23 | 后端：作用域从端点透传到写入<br>**文件**：`domain/services/knowledge_space_service.py`（`_folder_scope_path` / `resolve_folder_dedup_path` / `add_file(dedup_scope)`）、`domain/schemas/knowledge_space_schema.py`（`FileCreateReq.dedup_scope`）、`api/endpoints/knowledge_space.py`、`api/endpoints/knowledge.py`（upload 端点加 `parent_id`/`dedup_scope` Form 参数 + `upload_file` 权限校验）<br>**逻辑**：默认 `space`（既有调用方零回归）；`folder` 时 upload 的 `repeat` 与 files 的登记都只看目标文件夹 | AC-20 | 后端测试：④ `add_file(dedup_scope='folder')` ⇒ `process_one_file` 收到 `dedup_file_level_path == "/<folder_id>"`（且与落库的 `file_level_path` 一致）；⑤ 不传 ⇒ 收到 `None` | ☑（测试已写，**本机无法执行**——后端无依赖环境，见执行记录） |
| T-24 | 前端：归档链路带作用域<br>**文件**：`client/src/api/knowledge.ts`（`uploadFileToServerApi` / `addFilesApi` 可选 `DedupScope`）、`pages/appChat/formFile/archiveLocalFiles.ts`<br>**逻辑**：预判与登记**都**传 `{parentId, dedupScope:'folder'}`；两处作用域必须一致（决策 11） | AC-20 | 组件/单测：① 两次调用都带 `parentId` + `dedupScope:'folder'`；② 重复（`repeat=true`）仍不调 `addFilesApi`；③ 既有编排用例不回归 | ☑ |
| T-25 | 契约修订与质量门<br>**文件**：`features/v2.7.0/release-contract.md`、本文档、spec.md、design.md<br>**逻辑**：AC-20/§3 口径改为目标文件夹；release-contract 的 F065 依赖行补「判重作用域可选、默认整空间」 | — | 契约与 spec/design 三处口径一致；前端 `pnpm test:ci` / `typecheck` / `lint` 通过（既有历史错误不计） | 🚧 |

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- T-03 偏离 → 「客户名称」判据落在 `resolveFolderName.ts` 的 `findCustomerNameFieldKey`（`splitLocalSources.ts` 不再另导一个查找函数，避免两处判据）。纯实现细节，design 无需改动。
- T-05 偏离 → ① 子项列表**只用 `getSpaceChildrenApi`**（按 `file_type` 拆文件夹/文件），不再叠加 `listKnowledgeFolders`——同一响应已含两类，两次请求只会带来同步负担；② 可见空间取 **mine + joined + department**（与 `KnowledgeSpaceSelect` 同一三源），**不用** `getManagedSpacesApi`（它是 mine∪joined 的后端合并结果，冗余）。design 决策 5 的按名解析清单同步收窄。
- T-09 验证项 ④ 偏离 → 「本地条目仍挂 `File` 实例」**不在组件测试里断言**（组件内部 state 不可观测，为其加测试专用出口属过度设计）。改由 Wave D 的 T-15/T-16 间接证明：归集若拿不到 `File`，`uploadFileToServerApi` 就不会被调用。
- 版式重做（需求方变更，非任务偏离）→ 选择器改为**两栏**（左栏分组空间 + 关键词搜索 + 按 `suffixes` 类型置灰 + 底部已选计数），对齐兄弟仓库 `jinbao` 的 `KnowledgeFilePickerDialog`；随之删除无消费者的 `useVisibleSpaces`。论证在 design §4.1/§4.3 与坑 19~20，不在此重复。

---

## 执行记录

> 每完成一个任务追加一行：`T-NN ✅ <日期> <关键证据>`；失败/重跑同样记录，便于回溯。

- T-01 ✅ 2026-09-24 `formFile/resolveFolderName.ts` + 11 例单测（含 1 例 pin 住「客户名称」字面量）
- T-02 ✅ 2026-09-24 `formFile/mergeSelectedFiles.ts` + 10 例单测（含「无新增时返回原引用」）
- T-03 ✅ 2026-09-24 `formFile/splitLocalSources.ts` + 4 例单测
- T-04 ✅ 2026-09-24 `com_knowledge` 新增 4 键 × 3 语言；`node scripts/check-i18n.mjs` ⇒ **OK — no new i18n drift**
- Wave A 汇总 ✅ 2026-09-24 `jest src/pages/appChat/formFile` ⇒ **3 suites / 27 tests passed**
- T-05 ✅ 2026-09-24 `KnowledgeFilePicker/useKnowledgeBrowse.ts`（`mergeVisibleSpaces` / `childStatusFilter` / `toBrowseChildren` + hooks）+ 11 例单测
- T-06 ✅ 2026-09-24 `KnowledgeFilePicker/index.tsx`（弹层 + 面包屑 + 三级导航，文件夹可进入不可选）+ 组件测试
- T-07 ✅ 2026-09-24 同一组件（单选替换 / 多选累积 / 取消零回调 / 空选不可确认）
- T-08 ✅ 2026-09-24 `KnowledgeFilePicker/resolvePickedValues.ts` + 7 例单测（含「空地址视为失败」）
- Wave B 汇总 ✅ 2026-09-24 `jest src/pages/appChat/components/KnowledgeFilePicker` ⇒ **3 suites / 23 tests passed**
- 坑（未预期）✅ 2026-09-24 `~/components/ui` barrel 会把 `filenamify`(ESM) 拖进任何渲染单个 Dialog 的文件 → 改用窄导入 `@bisheng/ui` + `~/components/ui/Dialog`；本特性测试不再需要 `QueryClientProvider`/`RecoilRoot`（mock 掉 `useLocalize`）
- T-09 ✅ 2026-09-24 `InputFileComponent.tsx` 改自持 `FormFileEntry[]` + `mergeSelectedFiles` 驱动；**回调形状刻意不变**（单选标量 / 多选数组，否则会改掉技能表单的 wire 契约）；`disabled` 复位用「返回原引用」避免无限重渲染
- T-10 ✅ 2026-09-24 新增默认关闭的 `knowledgePicker`；`InputForm.tsx` 显式传 `true`；`git status` 确认 `InputFormSkill.tsx` **不在改动清单**
- T-11 ✅ 2026-09-24 接入弹层入口（`data-testid="knowledge-picker-trigger"`）+ `resolvePickedValues` 接线 + 失败 toast（AC-07）
- T-12 ✅ 2026-09-24 展示名与取值同源（同一份 `entries` 派生），故天然一一对应
- Wave C 汇总 ✅ 2026-09-24 `jest src/pages/appChat/components/InputFileComponent.test.tsx` ⇒ **12 tests passed**（无 act 警告）
- 重构（Wave C/D 之间）✅ 2026-09-24 「可见空间」抽到 `formFile/visibleSpaces.ts`（`mergeVisibleSpaces` + `fetchVisibleSpaces`）——选择器与归集共用一个答案，归集模块因此**不依赖 react-query hooks 文件**；`useKnowledgeBrowse.ts` 相应收窄
- T-13 ✅ 2026-09-24 `archiveLocalFiles.resolveArchiveSpace`：复用可见同名空间 / 无则 `createSpaceApi({name, auth_type:'private'})`（AC-19/26/27）
- T-14 ✅ 2026-09-24 `resolveArchiveFolder`：仅匹配 `file_type===0` 且同名的条目，命中复用、否则 `createFolderApi(…, parent_id:null)`（AC-17/18）
- T-15 ✅ 2026-09-24 `addLocalFileToArchive` + `archiveLocalFiles` 编排；**`repeat===true` 时断言 `addFilesApi` 零调用**（design 坑 1 的核心回归）；串行登记以保证同批内去重
- T-16 ✅ 2026-09-24 `InputFileComponent` 新增 `onEntriesChange` 通道（只有它携带 `File`）；`InputForm.submit` 组装并 `void …catch(console.error)`；测试证明**归集 reject 时提交 payload 与成功时逐字段一致**
- T-17 ✅ 2026-09-24 `git status` 证明 `InputFormSkill.tsx` / `InputFiles.tsx` / `useFileDropAndPaste.ts` 三者**零改动**（验证项 ② 的手动部分并入 T-20）
- Wave D 汇总 ✅ 2026-09-24 `jest src/pages/appChat` ⇒ **12 suites / 84 tests passed**（含既有 `ChatInput.wiring.test.tsx`，无回归）
- T-18 ✅ 2026-09-24 release-contract 复核：`INV-22` / `F065-form-upload-knowledge-space` 命中 **5 处**（表 1 行、表 2 行、表 2 备注、表 3 行、变更历史）
- T-19 ✅ 2026-09-24 `tsc-strict`：本特性文件**零错误**（残留 6 个既有：`stepTree.ts` 1 + `TaskPanel.test.tsx` 5，`git status` 证明二者未被改动）；`eslint`（本特性文件）：**零错误**；`vite build`：**✓ built in 1m 6s**
- 既有质量问题（**非本特性引入**，供版本级留意）✅ 2026-09-24 ① client `eslint` 当前仍有 2 个未改动文件报错（`src/pages/apps/__tests__/explore.test.tsx`、`test/jsdomEnvironment.js`）且不在 suppressions 里；② `client/tsconfig.json` 的 `types` 数组写在了 `compilerOptions` **外面**，导致 jest-dom 匹配器类型从不加载 —— 任何未带 `@ts-strict-ignore` 的测试文件都无法用 `toBeInTheDocument` 等，本特性测试因此改用朴素断言
- T-20 ⏸️ **未执行** 2026-09-24 端到端手动验证需真实 `form_input` 应用 + 可达后端 + 知识空间，本会话不具备。已用两处替代信号兜底：① `jest src/pages/appChat` 84 tests（含归集全链路 mock）；② 真实 Vite dev server 按需编译 4 个新模块均 **200**。**design §7 的 ①~⑥ 步仍需人工走查**（重点：坑 1 的「既有文件未被改成失败」与 AC-27「空间为私有」）
- 版式重做 ✅ 2026-09-24 选择器改两栏对齐参考实现：`KnowledgeFilePicker/index.tsx`（左栏 `useCategorizedKnowledgeSpaces` 分组空间 + 打开自动选中、右栏 返回/路径/搜索/复选框行/底部计数）、`useKnowledgeBrowse.ts`（`useBrowseChildren` → `useSpaceChildren(space, parentId, keyword, enabled)`：关键词走 `searchSpaceChildrenApi` 页码分页、否则 `getSpaceChildrenApi` 游标分页，分页规则抽成纯函数 `nextPageParamFor`）、新增 `suffixMatch.ts`（AC-29 类型门槛）、`InputFileComponent` 透传 `suffixes`、三语新增 5 键并删除无消费者的 `pick_confirm_count`
- 版式重做 验证 ✅ 2026-09-24 `jest src/pages/appChat` ⇒ **13 suites / 100 tests passed**（改前 12/84；新增 `suffixMatch.test.ts` 与两栏/搜索/置灰用例、`InputFileComponent` 新增「suffixes 透传」1 例）；`tsc-strict` ⇒ 仅 6 个**既有**错误（`stepTree.ts` 1 + `TaskPanel.test.tsx` 5，`git status` 证明未被本次触碰）；`eslint`（全量）⇒ 仅 5 个**既有**错误（`explore.test.tsx` 3 + `jsdomEnvironment.js` 2）；`node scripts/check-i18n.mjs` ⇒ **OK — no new i18n drift**；`vite build` ⇒ **✓ built in 1m 5s**
- 版式重做 ⏸️ **未人工走查** 2026-09-24 弹窗真实渲染未在本会话验证（同 T-20 前置限制：需真实 `form_input` 应用 + 后端）；本次仅有组件级测试与静态检查通过
- T-21 ✅ 2026-09-24 `selectAll.ts`（`selectableFileIds` + `triState`，纯函数）+ 8 例单测；`index.tsx` 头部三态全选行（`data-testid="picker-select-all"`，含「已选 M / 共 N」）与底部「清空全部已选」（`picker-clear-all`），两者仅多选渲染；三语新增 `pick_select_all` / `pick_select_all_count` / `pick_clear_all`
- T-21 补正 ✅ 2026-09-24 类型门槛（`suffixMatch`）原先标为 AC-08，实为**兄弟仓库 jinbao 的编号**；本仓库 AC-08 是「单选只产生一个文件」。已改标 AC-29（代码注释 / 测试名 / design / tasks 共 11 处），spec 补 AC-29 ~ AC-32
- T-21 验证 ✅ 2026-09-24 `jest src/pages/appChat` ⇒ **14 suites / 114 tests passed**（Wave F 前 13/100；新增 `selectAll.test.ts` 8 例 + 选择器 6 例）；`tsc-strict` 仅 6 个既有错误、`eslint` 仅 5 个既有错误（均未触碰改动文件）；`check-i18n` ⇒ **OK — no new i18n drift**；`vite build` ⇒ **✓ built in 1m 5s**
- T-21 ⏸️ **未人工走查** 2026-09-24 全选/清空在真实弹窗里的观感与跨目录行为未人工验证（同 T-20 前置限制）
- 缺陷修复 ✅ 2026-09-24 需求方反馈「头部 `已选 {0} / 共 {1}`、底部 `已选 {0} 项` 没显示数据」：根因是 i18next 默认插值用 `{{0}}`，单花括号不替换（见 design 坑 22）。三语共修 9 处：`pick_selected_count` / `pick_select_all_count` / `pick_resolve_failed`（后者自 T-04 起就一直是坏的，AC-07 的失败 toast 同样受影响）。验证：`node -e` 直接走 i18next 渲染三语，输出分别为「已选 3 项 / 已选 2 / 共 5 / 文件「a.pdf」…」「3 selected / 2 of 5 selected / …」「3 件選択中 / 5 件中 2 件を選択 / …」；`jest src/pages/appChat` 14/114 passed；`check-i18n` OK
- 缺陷修复 ✅ 2026-09-24 需求方实测「客户名『比亚迪股份有限公司』归档后文件夹为空」：根因是后端判重范围是**整个空间**（`get_repeat_file` 只按 `knowledge_id` 过滤），同内容已在别的文件夹 → `repeat=true` → 归档跳过。经澄清选定**方案 A**：把判重作用域参数化（design 决策 11、spec AC-20 改口径）。这是**推翻已 ★ 确认的口径**，需求方 2026-09-24 明确改定；N 份副本的存储代价已确认接受
- T-22 ✅ 2026-09-24 `knowledge_file.py`：`_same_folder_filter` + `get_repeat_file`/`get_file_by_condition` 的 `file_level_path` 参数（根目录兼容 `""`/NULL，对齐 `SpaceFileDao._root_path_filter`）；`knowledge_service.py`：`process_one_file(dedup_file_level_path)`
- T-23 ✅ 2026-09-24 `knowledge_space_service.py`：`_folder_scope_path` / `resolve_folder_dedup_path`（权限 + 路径）/ `add_file(dedup_scope)`；`FileCreateReq.dedup_scope`；`knowledge_space.py` 透传；`knowledge.py` 上传端点加 `parent_id`/`dedup_scope`（Form）并在 `folder` 时校验 `upload_file` 权限。**默认值均为旧行为**（既有调用方零回归）
- T-24 ✅ 2026-09-24 `api/knowledge.ts`：`DedupScope` / `UploadScopeOptions`，`uploadFileToServerApi` 与 `addFilesApi` 各加可选作用域；`archiveLocalFiles.ts` 两处调用都带 `{parentId, dedupScope:'folder'}`
- Wave G 验证 ✅ 2026-09-24 前端 `jest src/pages/appChat` ⇒ **14 suites / 115 tests passed**（Wave F 后 114，新增「两处调用作用域一致」1 例）；`tsc-strict` 仅 6 个既有错误、`eslint` 仅 5 个既有错误；`check-i18n` ⇒ OK
- Wave G ⏸️ **后端测试未执行** 2026-09-24 本机无后端依赖环境（`src/backend` 无 venv，系统 python 缺 `sqlmodel`/`pytest`；`uv` 可用但未 sync）。已新增 6 例后端测试（SQL 断言 3 例 + `process_one_file` 作用域 1 例 + `add_file` 透传 2 例）并 `py_compile` 通过；**需在有后端环境处执行** `pytest test/knowledge`
- Wave G ⏸️ **未端到端验证** 2026-09-24 design 决策 11 的验收点（同内容在两客户文件夹各一份、同文件夹内仍不重复）需真机 + 后端，未执行
