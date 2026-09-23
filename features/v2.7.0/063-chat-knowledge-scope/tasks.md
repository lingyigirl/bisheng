# Tasks: 对话知识范围默认口径（063-chat-knowledge-scope）

**关联规格**: [spec.md](./spec.md)
**设计真相**: [design.md](./design.md)（接手第一入口）
**版本**: v2.7.0

---

## 状态

| 步骤 | 状态 | 备注 |
|------|------|------|
| spec.md | ✅ 已评审 | 2026-09-22 编写并通过 `/sdd-review spec`（25 条 AC，5 组）。**同日按需求方裁决改写 AC-21**（升级前的遗留记录**不做迁移**，一律视作未显式设定落入兜底），并同步改写 §范围边界与 §3 边界情况——原字面「升级即按『全部』口径迁移」会让「只选过知识空间」的用户静默开始检索全公司知识库 |
| design.md | ✅ 已评审 | 2026-09-22 编写并通过 `/sdd-review design`（13 条决策，24 项检查）。首轮 5 项修复（新增**决策 13**「功能关闭不得销毁档位」、决策 3 补 C1 分层落点、C3 笔误、§6.2 无二进制依赖、§7 实跑命令与账号口径；坑表补 13/14）。**★ 后两项变更**：决策 8 重写为「不做迁移」；**拆 tasks 时发现决策 3 落点与 C1 第 30 行冲突（BLOCKER）**，已改为「复用两处既有入口、本特性零新增」（决策本身不变） |
| tasks.md | ✅ 已拆解 | 2026-09-22 拆解完成（**19 个任务 / 7 个 Wave**），同日两轮 `/sdd-review tasks`（21 项）。首轮抓出 5 项（**AC-13~17 只有实现、没有测试任务**；Wave 4 的 Test-First 顺序写反；标记项读写两处重复；Platform 分区行缺失；一处软推迟），第二轮又抓出 2 条 medium（**AC-12 无具体断言**、AC-20 的第二处持久化未入核对清单），已全部修复。最终 **25 条 AC 全覆盖，其中 19 条有测试任务直接覆盖** |
| 实现 | ✅ 完成 | 19 / 19（T-01~T-18；T-03/T-05/T-07/T-09/T-12/T-14/T-17 均按 Test-First 红→绿，变异验证见偏差记录）。偏差处理见 design.md 顶部调整原则 + `docs/SDD-Guide.md` §3-§4 |
| e2e-test | ✅ 已完成（有待重启后补跑项） | `/e2e-test`（强制）已执行。自动化半边 `test/e2e/test_e2e_f063_chat_knowledge_scope.py` **8/8 通过**（真后端 + 远端中间件 `172.19.70.189` + 真模型 deepseek-v4-flash）；人工半边 [e2e-checklist.md](./e2e-checklist.md)。**两项未结**：① e2e 断言的变异验证需重启后端（本地 uvicorn 无 `--reload`）—— 那次挂起的收尾在 code-review 期间暴露成 **H0**（注入行遗留工作区，见偏差记录）；② H1' 新增的落地页路径需要重启后补跑场景，且该场景的断言已改为落在**库里的具体 id 列表**上（断言 `org_knowledge_enabled` 无效，坑 16） |
| code-review | ✅ 已执行两轮（第二轮 2026-09-23） | **第一轮** `/code-review --base feat/2.7.0/045-minimax-tts-adapter`。发现并已修：**H0**（遗留变异注入行，权限面 BLOCKER）、**H1**（`queryKey` 漏 `page_size`）、**H1'**（落地页档位对检索零作用，需求方裁决「落地页兑现承诺」）、**M1**（「不使用」档误报 + 空白 chip）、**M2**（附件条挂载门无测试，已补 `AiChatInput.test.tsx` 并变异反证）。docs 同步（维度 7）已完成：design 决策 14/坑 15 改写、新增坑 16/17、§4.2/§4.3/§6.1/§7/修订历史；e2e-checklist 落地页场景改为断言落库 id；偏差记录 H0/H1/H1'/M1/M2 已追加。**未结**：`pnpm typecheck` 6 个报错与 `pnpm lint` 的 platform 53 个报错均为**既有**（前者在他人文件 `stepTree.ts` / `TaskPanel.test.tsx`；后者在 `platform/` 三个 `controllers/API/*.ts`，超出其冻结抑制计数，与本特性零交集）；e2e 补跑待重启后端 |

---

### code-review 第二轮（2026-09-23，实现合入 `455e69c20` 后）

**方式**：4 个 agent 并发审 7 个维度（后端权限链路 / 前端状态与 payload / 测试真实性 / 文档同步）。

**后端**：五条核心安全要求**逐条对着代码核对，全部成立** —— 与检索侧同一份
`filter_knowledge_ids_by_permission_async(..., "use_kb")`、**真 fail-closed**（收口异常返回 `[]` 而非候选集）、
不手写 `tenant_id`、灵思不双重展开、旧形态逐字不变。**未发现越权或信息泄漏类实现缺陷。**

**抓出并已修**：

| 编号 | 维度 | 内容 |
|---|---|---|
| **H1** | 边界 | `AiChatInput` 两处 legacy 互斥用 `val.length > 0` 判「选了知识库」，而档位标记本身就是数组元素 ⇒ 点「不使用」（或取消最后一个空间）会**静默清空刚挂上的附件** |
| **H2** | 测试 | **AC-20 / AC-24 完全没有自动化测试**，但验收表把它们记成已覆盖；对应手工项从未执行。已补 `knowledgeScopeCoverage.test.ts` 覆盖可自动化内核，并把虚报声明改为诚实标注 |
| **H3** | 文档 | design §4.1 数据流停在开工前 —— 缺整条 `organization_knowledge_all` 分支与**落地页提交链路**（本特性唯一对外行为变化的载体） |
| **M1** | 边界 | 三处清空走 `onSelectedOrgKbsChange([])`，把标记一起删掉 ⇒ 兜底重新生效，用户显式设的「不使用」**变成「全部」**。**其中 `AiChatInput.tsx:578` 一处是审查漏报、由实现者 grep 补出的** |
| **M2** | 边界 | `bsConfig` 未进两个 `useCallback` 依赖 ⇒ 硬刷新后首条消息可能发 `organization_knowledge_all: true`；后端此路径**不再校验**管理员开关，前端这道门是唯一防线 |
| **M3** | 测试 | AC-08 用例输入与 AC-02 **字面相同**；AC-18 是**同一表达式调用两次**；`ChatKnowledge.test.tsx` 的 `onChange` **从未被断言**（`onSelect` 改成空函数，30 条前端测试仍绿） |

**验证**：**9 项变异逐一注入并即时还原**（6 项代码变异 + 3 项覆盖断言变异），**全部被捕获**；
每次变异与还原在同一步完成，事后校验工作区无残留注入行。测试数 68 → **80**。

**未结（不影响功能）**：M4 展开成本不封顶、不缓存（设计决策 11 已接受，AC-09 明文禁止收敛，但 spec §3 缺对应边界条目）；
坑表尚未加 `_resolve_submit_org_ids` 的三态行（§4.1 已写）；坑 6 / 坑 10 描述的是本次已删除的代码；
§1 非目标第 2 条与决策 14 自相矛盾；e2e-checklist 场景编号缺 6；
**e2e 两项待办**（e2e 断言的变异验证、H1' 落地页场景补跑）—— 后端 :7860 已加载 F063 代码，
「需重启后端」这个前提**已不存在**，可直接补跑。

---

## 开发模式

**按 Wave 组织任务**：无依赖的归 Wave 1（可并行），依赖前序的归后续 Wave。每个任务标 `依赖:`。**任何任务的依赖都不得指向后续 Wave**。

**Wave 顺序即执行顺序**：同一 Wave 内无相互依赖可并行；跨 Wave 必须按序。

**任务分类**：

| 分类 | 任务 |
|---|---|
| 版本契约 | T-01（**已完成**） |
| 后端入参 schema（基础设施） | T-02 |
| 后端展开漏斗（Test-First 配对） | T-03, T-04 |
| 后端灵思交接（Test-First 配对） | T-05, T-06 |
| 前端 Client — 状态模型（Test-First 配对） | T-07, T-08, T-09, T-10 |
| 前端 Client — payload 与 UI | T-11, T-12, T-13, T-14, T-15, T-16, T-17 |
| 前端 Platform | **无** —— 本特性不碰 platform 应用（spec §范围边界：管理员配置入口本期不变，仅其配置值不再影响对话默认口径） |
| Worker / Celery | **无** —— 「全部」是**读时求值**，不落库、不进异步任务（设计决策 3），故无 tenant_id 传递问题 |
| 数据表 / 迁移 | **无** —— 不新增表、不新增字段 ⇒ 无 alembic 迁移、无回滚方案需求 |
| 回归与 E2E | T-18, T-19 |

**后端 Test-First（务实版）**：理想流程是先写测试（红）再写实现（绿）。**每个后端实现任务都必须有前置的测试任务**，测试任务先声明期望接口，实现任务满足它。新增测试放 `src/backend/test/workstation/`（与既有 `test_query_chunks_visibility.py`、`test_unified_chat_entry.py` 同目录，根 `AGENTS.md` §6）。中间件 / DM8 / e2e 测试在 **CI** 跑。

**前端**：client 已有 Jest + Testing Library 基建（`pnpm test:ci`，既有 `*.test.tsx` 先例：`components/Linsight/Execution/TaskPanel.test.tsx`），故状态模型**与组件行为**都写自动化断言，不退回纯手动验证。**变异验证是验收条件之一**：写完断言后按设计 §7 把实现改坏一次，确认用例真的红。

**本 feature 特殊口径（实现者不得绕过）**：

1. **零新增 DAO / Service 入口**：C1 第 30 行同一句里既禁「ORM 写进 Service」，也禁「为 new feature 新增 DAO 入口」。候选与收口**全部复用既有入口**（T-04 已点名）。**不得**新增 `aget_ids_by_type` 之类的方法，哪怕它只是只读。
2. **不手写 `tenant_id`**（C3）：候选查询是 SELECT，`tenant_filter.py` 的 `do_orm_execute` 自动注入租户条件（该监听器**只拦 SELECT**，这是本路径成立的前提）。
3. **不加 Recoil atom / selector，不新增 `recoil` import**（冻结，lint 强制）。档位靠既有 `selectedOrgKbs` 原子里的合成标记项承载（设计决策 6），判定逻辑抽进**不依赖 Recoil 的纯函数**。
4. **卡片不用 frosted glass**：汇总卡（T-15）**不得**使用 `backdrop-blur-*` / `backdrop-filter`，含任意值与变体前缀。用半透明背景（`bg-white/80`）替代——信创机器无 GPU 加速，该属性按元素计费且随会话长度累乘（根 `AGENTS.md` §4 硬规则）。新增的任何列表项 / 气泡 / 卡片同理。
5. **不新增 UI 库 / 状态管理库**；**store 不得直连 HTTP**（C7）。
6. **i18n 三语同 PR**（zh-Hans / en / ja），`pnpm check-i18n` 必须通过。**本特性不新增错误码**，故不动 `api_errors` 域及其生成物。
7. **任务模式（灵思）零改动**（spec AC-25）：其选择项、选择器、持久化链路与对外行为保持本版本之前的状态。任务模式发来的请求**从不带** `organization_knowledge_all` ⇒ 展开分支对它恒不触发——这是 AC-25 成立的论证，T-06 要把它写进注释。
8. **不留 fail-open 分支**：展开路径上任何解析失败（候选查询异常 / 判定不可用）按 spec §3 降级为「本轮不检索组织知识库」继续作答，**不得**降级为「放开全部」。收口这一步尤其不得吞异常后返回候选集本身。
9. **检索侧库数放大是「已知接受项」，不是本 Feature 的 TODO**：`queryChunksFromDB` 逐库初始化向量库，本期不动；「全部」档位会放大它，但 spec AC-09 明文**禁止**为此收窄范围。触发条件与两条后续路径见 design §8——实现期**不得**顺手加收敛手段（那会违反 AC-09）。

---

## Tasks

### Wave 0 — 版本契约登记（已完成）

#### T-01 release-contract 登记 ✅

**依赖**: 无

**改动**（已于 2026-09-22 随 spec 编写完成）：
- 表 1：新增 1 行（对话知识范围的默认口径 —— 读取行为，**不落库**；**不拥有** `knowledge` 对象与任何可见性规则，只读复用）
- 表 2 前言：**F063 不新增不变量**，其为 **INV-21** 的一份**新消费者**（新增一条知识库读取路径），INV-21 文本与既有 INVs 均不修改
- 表 3：新增 F063 依赖行（依赖 F062 / F057，只读复用其逐库可见判定）
- 错误码表：新增 1 行（F063 **不新增**错误码）+ 编号现状说明（**118 仍留待后续 Feature**）
- 变更历史：新增 1 行；**「明确不涉及」列表已补「存量知识范围选择记录的迁移」**（需求方 2026-09-22 裁决：按全新口径处理，不做迁移）

**编号扫描**（三来源全部为空）：F 编号 **063** 空闲；错误码 **118** 空闲（114 是前端哨兵，非分配）；**INV 无新增**（下一个仍是 INV-22）。

**覆盖 AC**: —

---

### Wave 1 — 后端入参字段（基础设施，无测试配对）

#### T-02 `UseKnowledgeBaseParam` 新增 `organization_knowledge_all`

**依赖**: 无

**文件**: `src/backend/bisheng/api/v1/schema/chat_schema.py`（`UseKnowledgeBaseParam`，`:60`）

**逻辑**：新增 `organization_knowledge_all: bool | None = False`。**可选、默认 `False`** —— 旧客户端不带该字段时 pydantic 取 `False`，`_resolve_user_kb_selection` 走原路径，语义一字不变（这是「新增字段而非改语义」的全部理由，设计决策 1）。

**不得改**：同类的两个 `>50` `field_validator`（`organization_knowledge_ids` / `knowledge_space_ids`）**保持原样**。它们在「全部」路径上不可达（客户端不发长列表），但旧客户端与任务模式仍依赖其拒绝语义（设计决策 5）。

**为什么不是「空数组 = 全部」**：空数组在现状语义里是「一个都不查」（`chat_service.py:985` 的 `if not ids: return []`），反转它等于静默改变所有既有调用方的语义（坑 1）。

**共享文件影响面**：`UseKnowledgeBaseParam` 是**内部/对外共用**的入参模型，新字段必须**可选且默认 `False`**，否则旧客户端请求直接 422。既有字段的语义、默认值、校验器一律不动。

**验证**：T-03 里构造三种请求体——不带新字段 / 带 `false` / 带 `true`——前两者都能正常解析且行为与改动前相同，第三者进入展开分支。

**覆盖 AC**: AC-03（承载）, AC-10

---

### Wave 2 — 后端展开漏斗（Test-First 严格配对）

> 本 Wave 是后端全部改动的正确性核心。**测试任务在前，实现任务在后**。

#### T-03 测试：`_resolve_user_kb_selection` 的「全部」路径

**依赖**: T-02

**文件**: `src/backend/test/workstation/test_f063_resolve_all_knowledge.py`

**逻辑**：针对 `_resolve_user_kb_selection(data)`（`chat_service.py:970`）的 `all=true` 分支，覆盖以下正交项：

| # | 场景 | 期望 | 依据 |
|---|---|---|---|
| 1 | 非管理员 + `all=true` | 展开集合 == 「租户内全部 `type=0`」经**同一份** `use_kb` 判定收口后的集合 | 决策 3 |
| 2 | **管理员** + `all=true` | 展开集合 == **候选集本身**（检索侧对管理员跳过 `use_kb` 收口，展开侧必须同口径） | 坑 2 |
| 3 | 存在用户**无权使用**的库 | 展开结果**不含**该库的 `name` / `description`（结果会进提示词的 `<selected_knowledge_bases>`） | 坑 3 —— 这是信息泄露断言，不是功能断言 |
| 4 | `all=true` 且同时传了 `organization_knowledge_ids` | 显式列表被**忽略**（互斥） | 决策 1 |
| 5 | 库数 **> 50** | 不抛校验错误、不返回错误、不静默截断到 50 | AC-09 |
| 6 | 租户边界 | 另一租户的同名库**不出现**在展开结果里 | C3 |
| 7 | 标签聚合 | 「全部」路径**不产生**逐库 `KnowledgeFileDao.aget_file_by_filters` 调用（mock 计数断言为 0） | 决策 11 / §8 |
| 8 | **候选不得被浏览视图裁掉** | 构造一个「未归属部门」或「不在 `filter_department_ids` 视图内」的库，断言它**仍在**结果里 | 决策 3 勘误的钉点 |
| 9 | 旧形态回归 | 不带新字段 / 带 `false` 的请求，结果与改动前**逐字节相同** | 决策 1 |

**变异验证（必须做）**：把候选查询改成带上 `filter_department_ids`（或加上 `limit`），用例 5 / 8 必须失败；把管理员分支的收口打开，用例 2 必须失败。任一变异下用例全绿 ⇒ 该用例是空断言。

**基础设施**：`test/workstation/` 已有 conftest 与可参照的既有用例（`test_query_chunks_visibility.py` / `test_unified_chat_entry.py`）。若需新的 mock helper（候选 DAO 与权限 Service 的替身），**在本任务内一并建立**，不另立任务。

**本任务只写测试**，实现由 T-04 满足；测试此刻应**红**。

**覆盖 AC**: AC-03, AC-09, AC-10, AC-11, AC-12

---

#### T-04 实现：`_resolve_user_kb_selection` 的展开

**依赖**: T-03

**文件**: `src/backend/bisheng/workstation/domain/services/chat_service.py`（`_resolve_user_kb_selection`，`:970`）

**逻辑**：新增一条 `all=true` 分支，落点为**两处既有入口**（决策 3 勘误后定稿，**本特性零新增 DAO / Service 入口**）：

1. **候选**：`KnowledgeDao.aget_all_knowledge(knowledge_type=KnowledgeTypeEnum.NORMAL)`（既有，`knowledge/domain/models/knowledge.py:709`）。`page=0, limit=0` ⇒ 不加 limit / offset ⇒ 返回全量。**只传类型，不传 `filter_department_ids`**——后者是平台列表的浏览视图收窄（`llm.py:1473` 的同款用法），带上就把候选裁成「列表页当前视图」而不是超集。
2. **收口**：`if not login_user.is_admin(): permitted = await KnowledgePermissionService().filter_knowledge_ids_by_permission_async(login_user, candidate_ids, "use_kb")`（既有，`knowledge_permission_service.py:259`）。**管理员跳过**——与 `workstation_service.queryChunksFromDB:1116` 的 `if org_kb_ids and not login_user.is_admin()` 同口径。

**展开结果的形状**：与既有路径一致 —— `[{id, name, description, tags, source}]`，`source` 仍按 `kb.id in space_id_set` 判定（空间 id 照旧并入，`all` 与空间**可共存**，AC-01）。**「全部」路径跳过逐库标签聚合**（`tags=[]`）——该聚合是 2N 次查询，正是「全部」档位下不可承受的部分（决策 11）。

**抽取时机已到**：设计决策 2 曾记「出现第二个需要『全部』语义的消费方时再抽共享函数」；`_to_linsight_submit`（T-06）就是第二个消费方。故本任务把展开抽成**同模块私有 helper**（例：`_expand_all_org_knowledge(data, login_user) -> list[int]`），由漏斗与交接两处调用。**不新建服务类**（决策 2 已否决）。

**共享入口影响面（只调用，不改）**：
- `filter_knowledge_ids_by_permission_async` 有 **4 个调用方**（`workstation_service:1116`、`knowledge_department_service:174` 等）。本任务**只新增调用，不改签名、不改语义**——它的行为若变了，平台列表与组织树角标会一起变（这也正是 INV-21 要的耦合）。
- `aget_all_knowledge` 有 **3 个调用方**（`llm.py:1473`、`knowledge_service.py:467` 等），同样只读复用。**不得**为本次需求改它的签名或加参数（要投影参数需另案评估，见决策 3 的「代价」条目）。

**硬约束**：不写 ORM（C1）、不手写 `tenant_id`（C3）、不改函数签名、不新增 DAO / Service 入口（C1）、异常一律**降级为「本轮不检索组织知识库」**而非放开（特殊口径 8）。

**测试**: T-03 全部通过。

**覆盖 AC**: AC-01, AC-03, AC-05, AC-09, AC-10, AC-11, AC-12

---

### Wave 3 — 后端灵思交接（Test-First 配对）

#### T-05 测试：`_to_linsight_submit` 的交接展开

**依赖**: T-04

**文件**: `src/backend/test/workstation/test_f063_linsight_handoff.py`

**逻辑**：`_to_linsight_submit`（`chat_service.py:2287` / `:2357`）**不走** `_resolve_user_kb_selection`，而是直接读 `ukb.organization_knowledge_ids`（坑 8）。灵思任务存储记的是**提交期的具体 id**，所以「全部」必须在此处展开（决策 10）。覆盖：

1. `all=true` ⇒ 交出**具体 id 列表**，且 `org_knowledge_enabled=True`
2. `all=false` + 显式 id 列表 ⇒ 与改动前**逐字节相同**（回归）
3. `all=false` + 空列表 ⇒ `org_knowledge_enabled=False`（「空」不等于「启用」）
4. `all=true` 但用户**无任何有权限的库** ⇒ `org_knowledge_enabled=False`，不得把空列表当「启用」
5. **任务模式守卫**：任务模式发来的请求体（不带 `all` 字段）走本路径时行为与改动前**逐字节相同** —— 这是 AC-25 的自动化证据

**本任务只写测试**，实现由 T-06 满足；测试此刻应**红**。

**覆盖 AC**: AC-25, AC-03

---

#### T-06 实现：`_to_linsight_submit` 展开

**依赖**: T-05

**文件**: `src/backend/bisheng/workstation/domain/services/chat_service.py`（`_to_linsight_submit`，`:2287` / `:2357`）

**逻辑**：`org_ids` 的取值改为在 `all=true` 时调 T-04 抽出的 `_expand_all_org_knowledge(...)`（**复用同一份展开，不得重写一遍**——否则「全部」会有两个口径），`org_knowledge_enabled` 随之为 `bool(org_ids)`。

**AC-25 的论证写进注释**：任务模式（灵思）的输入区吃的是它自己的 store（`TaskModeChatInput.tsx:171` 的 `context.knowledge`），其请求体**从不携带** `organization_knowledge_all`，故本分支对它恒不触发。注释要写清这一点，防后来者以为「改了交接 = 改了任务模式」。

**测试**: T-05 全部通过。

**覆盖 AC**: AC-25, AC-03

---

### Wave 4 — 前端 Client：状态模型（Test-First 配对）

> Recoil 冻结下，档位只能是既有原子里的一个合成标记项（决策 6）。因此**判定逻辑必须抽成不依赖 Recoil 的纯函数**——这既让它可以被单测，也避免新 atom（特殊口径 3）。

#### T-07 测试：三态求值与遗留形态读盘

**依赖**: 无

**文件**: `src/frontend/client/src/utils/knowledgeScope.test.ts`（新建）

**本任务先声明接口**（实现由 T-08 满足，此刻应**红**）。被测接口签名（实现须与此一致）：

```
ORG_SCOPE_MARKER_ID: '__org_scope__'
isOrgScopeMarker(item): boolean
readOrgScope(items): 'all' | 'none' | undefined
resolveOrgScope(items, hasSpaces): 'all' | 'none'
normalizeStoredItems(raw): SelectedOrgKb[]
withOrgScope(items, scope): SelectedOrgKb[]          // 写入 / 覆盖标记项
withoutOrgScopeMarker(items): SelectedOrgKb[]        // 只摘标记项，其余原样
toPayloadKnowledgeScope(items, hasSpaces, featureEnabled)  // 请求体片段
```

**用例**：

| # | 输入 | 期望 | AC |
|---|---|---|---|
| 1 | 无标记 + 无空间 | `all` | AC-02, AC-03 |
| 2 | 无标记 + 有空间 | `none` | AC-02, AC-04 |
| 3 | 显式 `all` + 有空间 | `all`（显式优先，不回弹） | AC-05, AC-07 |
| 4 | 显式 `none`：分别在有空间 / 无空间两种输入下 | 均 `none`（显式优先，不回弹） | AC-01, AC-06, AC-07 |
| 5 | 显式 `all` ⇒ 清空全部空间 | 仍 `all`（有标记 ⇒ 不看兜底） | AC-07 |
| 6 | 无标记 + 曾有空间 ⇒ 清空 | 回到 `all` | AC-08 |
| 7 | **遗留数组**：多个 `type:'org'` 逐项、无标记、有空间 | 归一后无组织项、无标记 ⇒ `none`；且**原数组不被就地修改** | AC-21 |
| 8 | **遗留数组**：含 `type:'space'` 项 + 旧组织项 | space **保留**、旧组织项丢弃 | AC-21 |
| 9 | 标记项 `orgScope` 为非法值（如 `'partial'`） | 按不存在处理 ⇒ 兜底 | 防脏数据 |
| 10 | `toPayloadKnowledgeScope`：求值为 `all` | `organization_knowledge_all=true` 且 `organization_knowledge_ids=[]`（互斥） | AC-05 |
| 11 | 同上，但 `featureEnabled=false` | `organization_knowledge_all=false`、组织 id 空、**知识空间 id 照发** | AC-18 |
| 12 | `withOrgScope` 重复调用 / 已有标记时再写 | **只有一项**标记（幂等，不堆积） | 防重复逻辑 |
| 13 | `withoutOrgScopeMarker` | 标记项被摘掉，空间项与顺序不变；无标记时返回等价数组 | 三态可分 |

**变异验证（必须做）**：把 `resolveOrgScope` 的兜底反写成 `hasSpaces ? 'all' : 'none'`，用例 2 必须失败——否则用例 2 是空断言。

**覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-07, AC-08, AC-18, AC-21, AC-22

---

#### T-08 实现：`knowledgeScope` 纯函数模块 + 类型字段

**依赖**: T-07

**文件**:
- `src/frontend/client/src/store/modeltype.ts`（`SelectedOrgKb`，`:3`；`selectedOrgKbs` 原子，`:43`）
- **新建** `src/frontend/client/src/utils/knowledgeScope.ts`

**逻辑**：

- `SelectedOrgKb` 加**可选**字段 `orgScope?: 'all' | 'none'`（只出现在标记项上；既有消费者读到 `undefined` 不受影响）。**不新增原子、不新增 selector、不新增 `recoil` import**（特殊口径 3）。
- 新建纯函数模块（**不得 import recoil**），导出 T-07 声明的全部符号：
  - `ORG_SCOPE_MARKER_ID` 常量集中一处，禁止散落字符串（根 `AGENTS.md` §3「固定业务常量抽取」）
  - `readOrgScope` —— 标记项**不存在** ⇒ `undefined`（= 未显式设定）；存在 ⇒ 取其 `orgScope`（非法值按不存在处理）。它是 `resolveOrgScope` 的**组成部分**（后者 = 前者 + 兜底），不是给测试专用的导出
  - `resolveOrgScope` —— 三态求值：有标记取标记；无标记走兜底 **`hasSpaces ? 'none' : 'all'`**（spec AC-02）
  - `normalizeStoredItems` —— 读盘归一（决策 8）：**只保留** `type === 'space'` 的项与**合法的**标记项，其余（含旧的逐项 `type:'org'`）**一律丢弃**；非数组 / 元素形状非法 ⇒ 返回 `[]`；**不得就地修改入参**
  - `withOrgScope` / `withoutOrgScopeMarker` —— 标记项的**唯一**读写口，供 T-13（档位切换）与 T-15（移除卡片）共用，避免两处各写一份
  - `toPayloadKnowledgeScope` —— 产出请求体片段；**两种形态互斥**（`all` 为真时 `organization_knowledge_ids` 必须为空数组）；`featureEnabled === false` 时 `organization_knowledge_all = false` 且组织 id 为空

**「不看旧数据里有没有组织项」是刻意的**（决策 8）：旧实现允许显式清空到 `[]`，所以「他有没有在用组织知识库」在旧数据上不可区分，为一个分不清的信号建迁移规则是在给不确定性编码。

**测试**: T-07 全部通过。

**覆盖 AC**: AC-02, AC-07（承载）

---

#### T-09 测试：ChatView 水合 / 持久化（功能关闭不得销毁档位）

**依赖**: T-08

**文件**: `src/frontend/client/src/components/Chat/ChatView.test.tsx`（新建）

**本任务只写测试**（实现由 T-10 满足，此刻应**红**）。`ChatView.tsx:92-161` 的水合 effect 与 `:165-168` 的持久化 effect 是**唯一**的状态读写点，且「一变就写盘」——任何水合阶段的过滤都是**不可逆的迁移**，不是临时视图（坑 14）。用例：

1. 读**遗留形态** ⇒ 归一后写盘（磁盘上不再有旧组织项）
2. **功能关闭**（`bsConfig.knowledgeBase.enabled === false`）时读盘 ⇒ **档位标记仍在状态里**，只吞掉**旧的逐项**组织项（AC-18 / 决策 13）
3. **功能重开**后档位**恢复到关闭前**（AC-18 的「恢复求值」）
4. 刷新后档位保持（AC-19）
5. **无存档 + `bsConfig.orgKbs` 里有 `default_checked` 项** ⇒ **不再**采纳为默认值，档位走兜底（AC-23）

**必须构造性断言**：先存入 `orgScope:'none'`，关闭再开启后读回它**仍在**。只断言「关闭时不下发组织库」是不够的——那种断言在「标记被删掉」的实现下**同样通过**（设计 §7 已写明这条陷阱）。

**断言方式**：用 `screen` / 渲染结果做**构造性**断言（「出现 X」），不要用 `container.innerHTML` 恒为空串式的**否定**断言（Radix portal 的既有教训：否定断言在 portal 场景恒真）。

**覆盖 AC**: AC-18, AC-19, AC-23

---

#### T-10 实现：ChatView 水合 + 持久化

**依赖**: T-09

**文件**: `src/frontend/client/src/components/Chat/ChatView.tsx`（水合 `:92-161`、持久化 `:165-168`）

**逻辑**：

- 读盘改为走 `normalizeStoredItems(raw)`（决策 8：只认知识空间项与合法标记项，其余丢弃）。
- 既有的功能关闭过滤 `saved.filter(item => item?.type !== 'org')` 改为**只过滤旧的逐项组织项**（`type === 'org' && !isOrgScopeMarker(item)`），**标记项保留**（决策 13）。这是本次改动里最容易写错的一行：标记项的 `type` 恰好也是 `'org'`（坑 13），照抄旧写法会把用户档位永久抹掉。
- **无存档时不再读** `bsConfig.orgKbs` 的 `default_checked` 作默认值（AC-23）。既有代码在 `saved === null` 分支里从配置构造默认项，这段要走掉；档位改由 `resolveOrgScope` 的兜底给出。

**测试**: T-09 全部通过。

**覆盖 AC**: AC-18, AC-19, AC-21, AC-22, AC-23

---

### Wave 5 — 前端 Client：payload 与 UI

#### T-11 `useAiChat` 两处 payload 构造

**依赖**: T-08

**文件**: `src/frontend/client/src/hooks/useAiChat.ts`（`:272-292` 发送、`:744-751` 重新生成 / 继续追问）

**逻辑**：两处都改调 `toPayloadKnowledgeScope(...)`，产出 `use_knowledge_base: { personal_knowledge_enabled: false, organization_knowledge_ids, knowledge_space_ids, organization_knowledge_all }`。**互斥**由纯函数保证，两处都不得自己拼一遍。功能关闭时组织库不参与、**知识空间照发**（AC-18）。

**注意**：两处必须同源——只改一处会出现「首次回答带全量、重新生成不带」这类只在特定操作下暴露的偏差。

**测试**: T-07 用例 10 / 11 覆盖纯函数；两处**接线**由 T-19 的「重新生成」场景验收，该场景**必须覆盖，不得跳过**。

**覆盖 AC**: AC-01, AC-02, AC-03, AC-04, AC-05, AC-06, AC-18

---

#### T-12 测试：输入区只有两档、不展示清单

**依赖**: T-10

**文件**: `src/frontend/client/src/components/Chat/Input/ChatKnowledge.test.tsx`（新建）

**本任务只写测试**（实现由 T-13 满足，此刻应**红**）。用例：

1. 组织知识库入口**只有两个档位控件**（「全部」「不使用」），无逐个知识库的勾选项（AC-13）
2. 用户有权限的库有 N 个（含 N=1 与 N=50+ 两种）⇒ **界面上不出现任何知识库名称清单**（AC-14）；断言用**构造性**方式（清单容器的 role / testid 不存在，且不存在逐项 checkbox），并做**变异验证**：把清单渲染加回去，本用例必须红
3. 档位为「全部」时「全部」档位呈选中态；为「不使用」时「不使用」呈选中态（求值口径见 T-07）
4. **功能关闭** ⇒ 整块组织知识库入口不渲染，知识空间区仍在（AC-18）

**覆盖 AC**: AC-13, AC-14, AC-18

---

#### T-13 实现：输入区收敛为两档

**依赖**: T-12

**文件**: `src/frontend/client/src/components/Chat/Input/ChatKnowledge.tsx`

**逻辑**：

- 组织知识库区**只渲染「全部 / 不使用」两个档位**，**删除**清单渲染与逐项勾选（AC-13 / AC-14）。当前档位 = `resolveOrgScope(items, hasSpaces)`；切换用 `withOrgScope(...)` 写标记项，经**既有的 `onSelectedOrgKbsChange` 通道**落盘（不新增 store 入口）。
- **存在性探测**复用既有 `useGetOrgToolList`，把 `page_size` 调小到 **1**（该 hook 现在就是无条件调用的，不是新增请求——坑 6；只判断「0 与非 0」，不翻页到底，因为列表已移除 `total`——坑 5、决策 12）。
- 组织知识库功能关闭时**整块不渲染**（AC-18），知识空间区照旧。
- `preferredIds` / `sortedOrgKbs` 不再参与默认值构造（AC-23）；它们原本只影响排序与首次默认值，从来**不是**白名单（坑 10）。
- **不碰知识空间清单**：其搜索 / 分页 / 多选行为保持现状（spec §范围边界）。

**其它入口**：订阅模块的 AI 助手面板、独立对话页与分享页**复用同一组件**，本任务不为它们单独改动，但**不得**把行为写死成只适用日常问答页（AC-24 由 T-19 验证）。

**i18n**：本任务与 T-15 新增的文案一并交 T-16 处理（三语同 PR）。

**测试**: T-12 全部通过。

**覆盖 AC**: AC-13, AC-14, AC-18, AC-23, AC-24

---

#### T-14 测试：汇总卡与移除语义

**依赖**: T-13

**文件**: `src/frontend/client/src/components/Chat/Input/AttachmentBar.test.tsx`（新建）

**本任务只写测试**（实现由 T-15 满足，此刻应**红**）。用例：

1. 求值为 `all` 且库数 > 0 ⇒ 附件条出现**一张**汇总卡（AC-15）
2. 求值为 `none` ⇒ 不出现；库数为 **0** ⇒ 不出现（AC-15）
3. **卡片数不随库数增长**：库数 = 1 / 20 / 200 三种输入下，组织知识库相关卡片数**恒为 1**（AC-17）——这是本特性最初的需求动因，必须钉住
4. **移除汇总卡 ⇒ 档位变成显式「不使用」**（AC-16）：断言移除后 `resolveOrgScope` 求值为 `none`，且**标记项仍存在**（是 `none` 而不是「标记被删掉」——删掉会回到兜底 ⇒ 又变回「全部」，这是坑 4 的变体）
5. 知识空间卡片行为不变（回归）

**变异验证（必须做）**：把移除实现改成「删掉标记项」，用例 4 必须失败。

**覆盖 AC**: AC-15, AC-16, AC-17

---

#### T-15 实现：附件条汇总卡 + 移除分支

**依赖**: T-14

**文件**:
- `src/frontend/client/src/components/Chat/Input/AttachmentBar.tsx`
- `src/frontend/client/src/components/Chat/AiChatInput.tsx`（移除处理器，`:378`）

**逻辑**：

- 组织知识库求值为 `all` 且**有权限的库数 > 0** ⇒ 显示**一张**「全部组织知识库」汇总卡；求值为 `none` 或库数为 0 ⇒ 不显示（AC-15）。
- 汇总卡是**一张**，不展开为 N 张（AC-17）。
- **移除卡片 ⇒ 显式置为「不使用」**（AC-16）。既有移除处理器按 id 过滤（`AiChatInput.tsx:378` 的 `filter(i => i.id !== kb.id)`），对合成标记项**什么都不做**（坑 4）——必须为标记项加**独立分支**，用 `withOrgScope(items, 'none')` 写入（而不是删掉标记项：删掉等于回到「未显式设定」⇒ 兜底又变回「全部」）。
- 卡片**不得**使用 `backdrop-blur` / `backdrop-filter`（特殊口径 4），用半透明背景。

**测试**: T-14 全部通过。

**覆盖 AC**: AC-15, AC-16, AC-17, AC-24

---

#### T-16 i18n 三语 key

**依赖**: T-13, T-15

**文件**: client 的 locale 资源（`src/frontend/client/src/locales/`）——三个语言文件属**同一逻辑单元**（`check-i18n` 要求三语同 PR，拆开反而会红一次），故本任务的文件数按 1 个单元计。

**逻辑**：两档标签（全部 / 不使用）、汇总卡文案、移除提示，**zh-Hans / en / ja 三语同 PR**（根 `AGENTS.md` §4 硬规则）。`pnpm check-i18n` 通过。

**不需要做的**：**本特性不新增错误码**，故不动 `packages/locales` 的 `api_errors` 域，也不碰其生成物（`api_errors.gen.json` 由 CI 校验，手改必失败）。

**清点结论（已查证，省一次调研）**：本次要改的 5 个前端文件（`ChatKnowledge.tsx` / `AttachmentBar.tsx` / `AiChatInput.tsx` / `ChatView.tsx` / `useAiChat.ts`）在 `client/eslint-suppressions.json` 里只有 `no-explicit-any` / `no-unused-vars` / `no-restricted-imports` / `no-console` / `exhaustive-deps` 五类抑制，**不含硬编码中文类规则** ⇒ 不触发「改到冻结违规文件须同 PR 抽出中文」的欠账（根 `AGENTS.md` §4）。

**覆盖 AC**: —

---

#### T-17 消费者核对（标记项的传染面）

**依赖**: T-13

**文件**：逐一核对 `selectedOrgKbs` 的全部消费者（**只读核对 + 必要的修补，不新增功能**）

**逻辑**：标记项是**塞进既有数组**的合成元素，凡遍历该数组的地方都会看到它（坑 7）。至少覆盖：

- `components/Chat/Messages/MessagesView.tsx:38-41`（消息视图持有的 `selectedOrgKbs` + `store.enableOrgKb`）
- `hooks/Chat/useChatFunctions.ts:63` / `:94-102`（`use_knowledge_base = { knowledges: selectedOrgKbs, ... }`）——**先确认它是不是活代码**：它仍被 `useChatHelpers.ts:9` 与 `useAddedHelpers.ts:6` 引入，但「被引入」不等于「被调用」。判死代码要 grep **导出名**与真实调用链，**只 grep 桶路径会把活代码误判为死代码**。若是死代码，**只文字标注，不删除**（根 `AGENTS.md` §3）。
- `AiChatInput.tsx` / `AttachmentBar.tsx`（T-13 / T-15 已覆盖，此处只做回归）

**产出**：一张「消费者 × 见到标记项时的行为」清单；确认无消费者会把标记项当普通知识库 id 发送或展示。若发现 `useChatFunctions` 是活代码且行为异常，**修它**，并在「实际偏差记录」留一行指针。

**另查一处（AC-20 的风险点）**：确认**是否存在第二处持久化写入**——订阅模块的 AI 助手面板、独立对话页、分享页若各自也往 `localStorage` 写知识范围，就必须改为只读共用（AC-20 明文「共用同一份状态，但不单独保存」）。这类分叉不会在单入口的用例里暴露，只会在「面板里改完刷新，回到日常问答页发现值不一样」时暴露。

**覆盖 AC**: AC-01, AC-24

**产出（T-17 审计结果，2026-09-22）**：消费者 × 见到标记项时的行为

| 消费者 | 见到标记项时的行为 | 处置 |
|---|---|---|
| `ChatView.tsx:76`（持有 + 读盘 + 写盘 + 传给输入区） | 归一后标记项是**唯一存活的 `type:'org'` 项**；写盘把它一并持久化（AC-19 靠这条） | T-09 / T-10 已覆盖 |
| `AiChatInput.tsx:341-348`（挂载理由 / 传参） | 用 `withoutOrgScopeMarker` 数卡片 + `showsOrgKbSummary` 判显示；移除经 `removeSelectedOrgKb` | T-15 已覆盖 |
| `AttachmentBar.tsx:264-286` | 显式 `isOrgScopeMarker` 过滤 ⇒ 不渲染成普通卡片，改为一张汇总卡 | T-14 已覆盖 |
| `ChatKnowledge.tsx`（档位开关） | 按 `readOrgScope` / `withOrgScope` 读写 | T-12 / T-13 已覆盖 |
| `MessagesView.tsx:38-41`（只持 setter，不读值） | 「开启新对话」的 `setSelectedOrgKbs([])` 把档位一并清空 ⇒ 回到 AC-02 兜底（「全部」，即本特性默认值）；`enableOrgKb` 是无消费方的遗留标记 | **加注释，不改行为** |
| `useChatFunctions.ts:63 / 94-102`（`knowledges: selectedOrgKbs`） | **死代码**：`src` 内无 `<ChatContext.Provider>` 挂载点（`useChatContext()` 恒为默认空对象），其产物 `store.submission` 无任何读者 | **只加注释**（警示「若复活必须走 `toPayloadKnowledgeScope`」），不删 |
| `TaskModeInput.tsx:215 / 232`（执行态） | 走 `context.knowledge.length > 0`；与落地页共用原子但 key 不同（落地页 `'new'`；执行态为会话 id），实际不相遇 | **加注释（坑 15），行为零改动**（AC-25） |
| `TaskModeChatInput.tsx`（任务模式**落地页**） | 原判定把档位标记当「已选组织库」⇒「不使用」档误报 `org_knowledge_enabled=true` | **改为按档位判定**（`isOrgScopeAll`）；见 design 决策 14 与 spec AC-25 修订 |
| `KnowledgeSpaceSelect.tsx`（执行态逐库选择器） | 数据源是 `useGetOrgToolList` 全量，**不读** `selectedOrgKbs` ⇒ 见不到标记 | 零改动 |
| `useAiChat.ts` / `useSubmitMessage.ts`（日常 payload） | 经 `toPayloadKnowledgeScope` 构造，标记项被排除 | T-11 已覆盖 |

**AC-20 风险点结论**：全仓对知识范围选择只有**一处**读盘（`ChatView.tsx:104`）与**一处**写盘（`ChatView.tsx:159`）；订阅模块 AI 助手面板（`AiAssistantPanel.tsx:100/237`）与独立对话页 / 分享页只持有同一份原子（内存态），**无第二处持久化** ⇒ AC-20 满足。


---

### Wave 6 — 回归与 E2E

#### T-18 后端回归：旧形态零变化 ✅

**依赖**: T-04, T-06

**文件**: `src/backend/test/workstation/`（与既有 `test_unified_chat_entry.py` 同目录）

**逻辑**：

1. **旧客户端形态**：只发显式 `organization_knowledge_ids`（长度 1 / 50）、或全空、或**完全不带新字段**——在 `_resolve_user_kb_selection` 与 `_to_linsight_submit` 两条路径上，结果与改动前**逐字节相同**。
2. **`>50` 拒绝语义未松动**：仍抛校验错误（既有 AC 不受本特性影响；本特性只是**不再触发**它，不是取消它——契约错误码表的原文）。
3. **既有测试全量重跑**：`test/workstation/test_query_chunks_visibility.py`、`test_unified_chat_entry.py` 不退化；`test/knowledge/` 下与知识库可见性相关的用例不退化。

**为什么单列一任务**：本特性改的是**所有对话入口共用的漏斗**，回归面比新增面更大。

**覆盖 AC**: AC-03, AC-09, AC-25

---

#### T-19 E2E（`/e2e-test`，强制）✅

**依赖**: T-18

**逻辑**：至少覆盖以下 7 个场景（场景 6 分执行态 / 落地页两半），每个都要有**后端日志对照**（`_resolve_user_kb_selection` 的 `resolved N kbs` 行 + `[perf][knowledge.permission_map]` 的候选数/保留数）：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 什么都不选直接问答 | 检索全部有权限的组织知识库；`resolved N kbs` 的 N == 该用户可见的 `type=0` 库数 |
| 2 | 只看一个知识空间 | 不掺任何组织库；结果与升级前该场景一致 |
| 3 | 「全部」+ 叠加一个知识空间 | 两者都参与，`source` 分类正确 |
| 4 | 移除汇总卡后发起该轮 | 该轮不检索组织知识库 |
| 5 | 刷新页面 / **重新生成** | 档位保持（含显式「不使用」这种**与兜底结果相同**的状态）；重新生成走的是与发送同源的 payload（T-11 的接线验收） |
| 6a | 任务模式**执行态**（灵思任务已创建后的输入区） | UI 与行为**零改动**（AC-25 修订后的保护范围）：逐库选择器照旧可用，提交的 `org_knowledge_enabled` 与升级前同义 |
| 6b | 任务模式**落地页**（`/linsight/new`） | 跟随新口径（AC-24）：默认显示「全部组织知识库」汇总卡；不选任何东西直接发起任务 ⇒ 提交体 `org_knowledge_enabled=true`（**这是本次唯一一处对外行为变化**，已由需求方确认）；把卡片移除成「不使用」后再发起 ⇒ `org_knowledge_enabled=false`（旧实现在此处会误报 true） |
| 7 | **档位切到「不使用」后，看平台侧知识库列表与组织树角标** | 数字与集合**一律不变**（AC-12：「不使用」是本轮问答的**作用域选择**，不产生任何授权语义，也不改变其他入口的可见性）——本场景是 AC-12 的具体断言，不能只靠「代码里没写负向授权」这类推断 |

**入口覆盖（AC-24）**：日常问答页、**订阅模块的 AI 助手面板**、独立对话页、分享页——至少各验一次「本口径生效且不因入口分叉」；其中订阅面板刷新后应恢复为日常问答页所保存的选择（AC-20）。

**账号两侧都要验**：非管理员验可见范围收口；管理员验 `is_admin` 跳过收口的分支（`queryChunksFromDB:1116`，坑 2）——单租户模式下**首个注册用户自动成为 `super_admin`**（根 `AGENTS.md` §7），多租户模式需先建租户再建用户。

**中间件**：检索链路真跑需要 Milvus / ES / Redis / MinIO 与 Celery worker（`docs/architecture/09-development-guide.md`）；无法本地拉起时按 SDD-Guide §6 在 CI 跑，并在本文件记明。

**覆盖 AC**: AC-10, AC-11, AC-12, AC-20, AC-24, AC-25

---

## AC 覆盖对照

| AC | 覆盖任务 |
|----|---------|
| AC-01 | T-04, T-07, T-11, T-17 |
| AC-02 | T-07, T-08, T-11 |
| AC-03 | T-02, T-03, T-04, T-05, T-06, T-07, T-11, T-18 |
| AC-04 | T-07, T-11 |
| AC-05 | T-04, T-07, T-11, T-13 |
| AC-06 | T-07, T-11, T-13 |
| AC-07 | T-07, T-08, T-10 |
| AC-08 | T-07 |
| AC-09 | T-03, T-04, T-18 |
| AC-10 | T-02, T-03, T-04, T-19 |
| AC-11 | T-03, T-04, T-19 |
| AC-12 | T-03, T-04, T-19 |
| AC-13 | T-12, T-13 |
| AC-14 | T-12, T-13 |
| AC-15 | T-14, T-15 |
| AC-16 | T-14, T-15 |
| AC-17 | T-14, T-15 |
| AC-18 | T-07, T-09, T-10, T-11, T-12, T-13 |
| AC-19 | T-09, T-10 |
| AC-20 | T-10（同一份状态 / 同一 key）, T-19 |
| AC-21 | T-07, T-10 |
| AC-22 | T-07, T-10 |
| AC-23 | T-09, T-10, T-13 |
| AC-24 | T-13, T-15, T-17, T-19 |
| AC-25 | T-05, T-06, T-18, T-19（**2026-09-22 修订**：保护范围限定到**执行态**；任务模式**落地页**改归属 AC-24，由 T-17 落点、T-19 场景 6 验收） |

**未覆盖项**：无 —— 25 条 AC 都有归属任务。

> ⚠️ **2026-09-23 复审修正**：「有**测试任务**」不等于「有**测试**」。逐条核对代码后发现，本句当时声称的 **19 条**
> 实际只有 **16 条** 有能失败的自动化断言：
> - **AC-08 / AC-18 的用例是假断言** —— 前者输入与 AC-02 用例**字面相同**（「清空空间」这个动作从未被建模），
>   后者是**同一表达式调用两次**（「关闭期」从未被表示）；
> - **AC-20 / AC-24 完全没有自动化测试** —— T-10 的证据是「只有一处读写」的 `grep` **审计结论**而非断言；
>   T-19 的四入口项在 e2e-checklist 里**从未执行**（本文件此前的「T-19 场景 6 验收」是空头支票）。
>
> 四项已于同日补齐：AC-08 / AC-18 改写为可失败并**经变异验证**的用例；AC-20 / AC-24 由
> `components/Chat/knowledgeScopeCoverage.test.ts` 覆盖其**可自动化内核**（「org 入口只有一个渲染方」
> 「知识范围只有一处写盘」「读写同 key」），**四个入口逐个点击仍是未执行的手工项**。

---

## 实际偏差记录

> **只留一行指针**，论证在 design.md（决策 / 坑），这里不重复（见 `docs/SDD-Guide.md` §4）。
> 推翻已 ★ 确认的决策时，先停下与用户重新确认（§3 第四个 ★），再记录。

- **决策 3 落点勘误（拆解期，★ 后）→ 已更新 design 决策 3 + §4.3 + 修订历史**：设计原定「新增 DAO 只读方法 + 新增 Service 只读入口」，与 C1 第 30 行「**never add new DAO entry points for new features**」直接冲突（同一句的前半句才是「never write ORM queries in Service」，原文只引了前半句）。改为**复用两处既有入口、本特性零新增**：`KnowledgeDao.aget_all_knowledge(knowledge_type=NORMAL)` + 既有 `filter_knowledge_ids_by_permission_async`（管理员跳过）。**决策本身（超集候选 + 同一份逐库判定）不变**；顺带消掉两个计划任务。派生出两条钉点：候选**不得**带 `filter_department_ids`（T-03 用例 8）、整行 ORM 取值的内存代价（当前规模可接受，上万库时应补投影参数而非加 DAO 方法）。
- **决策 2 的触发条件已满足 → 已抽同模块私有 helper**：决策 2 记「出现第二个需要『全部』语义的消费方时再抽共享函数」；`_to_linsight_submit`（T-06）即第二个消费方。抽成 `chat_service.py` 内的私有函数（**不新建服务类**，决策 2 已否决该形态）。
- **首轮 `/sdd-review tasks` 的 5 项修复（2026-09-22）**：① 补 T-12 / T-14 两个前端组件测试任务（AC-13~17 此前**只有实现任务、没有测试任务**，形式合规硬伤）并把 Wave 5 重排为 Test-First 配对；② Wave 4 原为「实现（T-07）先于测试（T-08）」，与 Wave 标题声明的 Test-First 矛盾，已交换；③ 标记项的读写原先在 T-13 与 T-15 各写一份，已收敛为 `withOrgScope` / `withoutOrgScopeMarker`（T-08）并补用例；④ 任务分类表补「前端 Platform = 无」行并写明理由；⑤ T-11 的「或至少在 E2E 里验证」是软推迟，已改为 T-19 场景 5 的确定性验收点。
- **第二轮 `/sdd-review tasks` 的 4 项修复（2026-09-22）**：① **AC-12 此前无具体断言**（只有「代码里没写负向授权」这种推断），补 T-19 场景 7——切「不使用」后平台列表与组织树角标必须不变；② **AC-20 的第二处持久化风险**未落在核对清单里，T-17 补「是否存在第二处 `localStorage` 写入」；③ T-16 的 3 个语言文件超出「≤2 文件」的字面，注明属同一逻辑单元（`check-i18n` 要求同 PR）；④ `readOrgScope` 是否属生产代码存疑，注明它是 `resolveOrgScope` 的组成部分而非测试专用导出。
- **T-04 实现偏差（2026-09-22，已合入）**：`_resolve_user_kb_selection` 签名由 `(data)` 改为 `(data, login_user)`。tasks 草稿写的「不改函数签名」是**服务类落点**（已被决策 3 勘误否决）的残留——展开要用 `is_admin()`（与 `queryChunksFromDB` 的 `if org_kb_ids and not login_user.is_admin()` 对齐）与逐库权限判定，`login_user` 不可省。该函数为模块私有、全仓**仅 1 个调用点**（`chat_service.py:1593`，所在函数 `_agent_stream_chat_completion` 形参本来就有 `login_user`），改动封闭、不触及对外契约；旧形态的显式 id 路径行为逐字未变（T-03 用例 `test_explicit_path_is_untouched` 守着）。同批落地：候选查询只传 `knowledge_type`（T-03 用例 8 断言 `await_args.kwargs`），失败一律 fail-closed 到「本轮不使用组织库」。
- **决策 11 具体化（实现期，已合入）**：设计记「`all` 档位跳过逐库标签聚合」。落成的口径是**按语义分档**而非一刀切：「全部」路径只对**用户显式选中的知识空间**做标签聚合（其数量由用户自身选择封顶，保持空间库标签行为与升级前一致），对展开出的组织库集合整体跳过（否则是 2 查询/库的 N+1，正是 AC-09 规模下不能付的）。T-03 用例 `test_tag_aggregation_runs_for_spaces_only` 断言 `aggregated == [7]` 即此口径，实现见 `chat_service.py` 的 `tag_targets`。
- **T-06 落点前移一帧（2026-09-22，已合入）**：决策 10 A 把展开写在 `_to_linsight_submit` 内，但该函数是 **sync**，而被调用的 `_expand_all_org_knowledge` 是 async。改为在 `_to_linsight_submit` 之前新增同模块私有 `_resolve_submit_org_ids(data, login_user) -> list[int] | None`（由 `_task_mode_stream_completion` 调用），`_to_linsight_submit` 保持 sync、**新增可选入参 `org_knowledge_ids=None`**。这样做的理由是既有 **6 处 sync 单参调用点**（`test_unified_chat_entry.py` ×5、`test/linsight/test_linsight_knowledge.py` ×1，后者属另一个 Feature 的测试）无需改动——**它们原样通过本身就是 AC-25 的逐字节回归证据**，为适配本特性去改它们反而会毁掉这份证据。决策 10 的 what（提交期落成具体 id、复用同一份展开、单一口径）未变；`None` 哨兵语义（解析出空列表 ≠ 回退到请求自带的 id 列表）写进了 T-05 用例与代码注释。变异验证：M1 展开恒不执行（5 用例红）、M2 用 falsy 判断替 `is not None`（1 用例红）、M3 标签聚合跑全量展开集（1 用例红）。
- **T-09 测试载体改为「纯函数行为 + 组件静态接线」（2026-09-22，已合入）**：计划是 render `ChatView`，实测其依赖图（react-activation / router / Recoil / react-query / `useLinsightManager` / toast+portal Provider / ~15 个 hooks 与子组件）需要一打 mock，而仓库里已有先例把这条写成了结论——`src/pages/appChat/ChatInput.wiring.test.tsx` 头注释：「Deliberately NOT a full render … each piece is a reason for this test to break for unrelated causes」。故拆成两处：**行为**（遗留存档归一、档位跨「关闭期」存活并在重开时求值、刷新往返、无存档不采纳配置默认）落在 `src/utils/knowledgeScope.test.ts` 的第二组 describe；**接线**落在 `src/components/Chat/ChatView.test.tsx` 的源码断言，钉死三个一行就能踩的坑——①读盘未归一化、②`type !== 'org'` 复活（标记项 `type` 恰是 `'org'`，会把档位永久抹掉）、③重新采纳配置里的 default_checked。断言只用 `orgKbs`（读那份列表的必经字段名）作探针，**不用 `default_checked`**——同一份 config 的 tools 列表合法地用它，用它作探针会误伤（首版就误伤过，已修）。
- **T-10 比计划更简（2026-09-22，已合入）**：计划写「功能关闭时把过滤改成只过滤旧的逐项组织项、保留标记项」。实现里**那一行过滤不需要存在**：`normalizeStoredItems` 已把非 space、非标记项全部丢弃，归一后唯一存活的 `type === 'org'` 项就是标记本身，再加一层过滤是死代码。故关闭期与开启期走**同一条读盘路径**，关闭的效力只体现在请求体（T-11 的 `featureEnabled`，AC-18「关闭时不下发组织库」），标记在关闭期留存 ⇒ 重开即恢复（决策 13）。T-09 的用例 2/3 因此由「归一后标记仍在 + 求值仍为 none」承担。
- **T-07 接口两处微调（2026-09-22，已合入）**：①读盘类入参放宽为 `SelectedOrgKb[] | null | undefined`（localStorage 读出的值天然可为 null），返回类型不变；②`normalizeStoredItems` 对 space 项校验 `id` 为非空字符串——读盘是不可信边界，脏项会变成 `Number(undefined)` → NaN → 整轮请求 422。
- **T-11 与计划一致（2026-09-22，已合入）**：两处 payload 站点（发送 / 重新生成）都改走 `toPayloadKnowledgeScope(selectedOrgKbs, hasKnowledgeSpaces(selectedOrgKbs), bsConfig?.knowledgeBase?.enabled !== false)`，且站点 2 **保持它原本不传 `personal_knowledge_enabled`** 的形态——重新生成与发送的差异是既有事实，本特性不顺手抹平它（两处各留一行注释指向对方，防止单边漂移）。
- **T-12 / T-13 载体与落点（2026-09-22，已合入）**：①**输入区只留一个内联档位块**（小标题 + 两行「全部 / 不使用」），桌面级联子菜单与移动端下钻面板连同 `openSub` / `mobilePanel === 'org'` 状态一并删除——清单没了以后，下钻/级联只是一次多余的点击，还会让同一组两档在两套代码路径里各写一遍（AC-24 要的正是单一行为）；②**存在性探测的落点由 T-13 改到 T-15**：两档本身不需要库数（AC-15 的显示条件是附件条那张卡片的事），若把调用留在 `ChatKnowledge` 就是一个**没有消费方**的请求；`page_size: 1`、无 `preferred_ids`、query key 复用口径**均不变**，只是改由持卡片的组件持有（design 决策 12 已补「实现落点修订」）；③`handleToggle` 收敛为知识空间专用（组织分支的唯一调用方已删），`newItem.name` 用 `?? ''` 兜住 `KnowledgeListItem.name?` 的可选性——原先靠 `any` 蒙过去；④两档行带 `data-org-scope` / `data-selected`，沿用本文件既有的 `data-sub-key` 测试钩子约定，断言不读样式。
- **T-08 泛型化（2026-09-22，随 T-13 合入）**：`withOrgScope` / `withoutOrgScopeMarker` 由 `SelectedOrgKb[]` 改为 `<T extends SelectedOrgKb>(items: T[])`——档位要在 store 的 `SelectedOrgKb`（`type: string`）与输入区的 `KnowledgeItem`（`type: 'org' | 'space'`）之间双向流动，`T` 让两个持有者各自拿回自己的类型（`tsc-strict` 抓到的两处 `TS2345` 即此）。标记项构造处因此有**唯一一处** `as unknown as T`，形状确实相同（id/name/type/orgScope），已在注释里写明理由。
- **Wave 5a 变异验证（T-12）**：把逐项清单渲染加回档位块内 ⇒ **用例 1/2 红**（用例 2 同时被两条路抓到：mock 进探测的 `KB-1` 名称出现、`role=textbox`/`role=checkbox` 出现），已还原（`diff` 验证逐字节一致）。前端基线不变：`tsc-strict` 剩余 6 个报错全在他人文件（`stepTree.ts` + `TaskPanel.test.tsx` 的 jest-dom 类型），本特性文件 0 报错；`ChatKnowledge.tsx` 的 `no-explicit-any` 抑制**整条归零**（11 → 0，已 `--prune-suppressions` 按文件范围清理，diff 只动该文件与上一批的 `ChatView.tsx` 计数）。
- **T-14 / T-15 落点（2026-09-22，已合入）**：①**移除语义抽成具名纯函数** `removeSelectedOrgKb(items, target)`（`utils/knowledgeScope.ts`），而不是把分支内联在 `AiChatInput.tsx:378`：T-14 用例 4 要求「移除后档位 = 显式 `none` 且标记仍在」，若语义只活在调用方，这条断言只能打在测试自建的 harness 上（正是「计划里的测试也可能是废的」那个坑）——抽成纯函数后，断言打的是**生产代码**，变异（改成删标记）真能变红；`AiChatInput` 的处理器改为一行 `onSelectedOrgKbsChange(removeSelectedOrgKb(selectedOrgKbs, kb))`。②**卡片归属的判据也抽成纯函数** `showsOrgKbSummary(items, enabled, kbCount)`：档位卡片既是「附件条是否挂载」的判据（`AiChatInput`），也是「卡片是否渲染」的判据（`AttachmentBar`），两处必须一致，否则默认态要么整条不出现、要么出现一条空条。③**新增要求外的一处修正**：`AiChatInput:330` 的 `hasSelectionTags` 原本按 `selectedOrgKbs.length > 0` 判「有挂载物」，而档位标记是**状态载体不是卡片**，用户把卡片移除成「不使用」后数组长度仍为 1 ⇒ 会挂载一条**空条**。改为 `withoutOrgScopeMarker(...).length > 0`（数卡片，不数数组项），并补上「全部档位卡片自身」也是挂载理由（AC-15 的默认态就是这样，此时数组为空）。④**探测落点**：`AttachmentBar` 与 `AiChatInput` 各调一次 `useGetOrgToolList({ page: 1, page_size: 1 })`，参数完全一致，共用 `['OrgTools', 1, undefined, '']` 一条缓存 —— 若两处参数不同会变成两次请求（design 决策 12 已记）。⑤**卡片定位**：汇总卡以**自身文案**（`com_chat_knowledge_scope_all_card`）被用例定位，**没有**为测试加 `data-*` 钩子 —— 栏内文案本就唯一，加钩子属为测试改生产代码。⑥`AttachmentBar` 新增**必填** `orgKbEnabled` 开关（不给默认值：调用方本来就知道，静默默认会在功能已关闭的表面显示卡片）。
- **T-16 i18n（2026-09-22，已合入）**：三语同 PR 新增 `com_chat_knowledge_scope_all` / `com_chat_knowledge_scope_all_card` / `com_chat_knowledge_scope_none`，按现有文件的字母序插在 `com_chat_knowledge_placeholder_search_space` 之后；`pnpm check-i18n` 通过（未动 `api_errors` 域及其生成物）。清点确认 `com_chat_knowledge_toast_org_limit` **未成为死键**——`components/Linsight/Input/KnowledgeSpaceSelect.tsx:94` 仍在用，故不清理。
- **T-14 变异验证（本次）**：把 `removeSelectedOrgKb` 的标记分支改成「删掉标记项」⇒ **用例 4 红**（`marker` 断言由 `true` 变 `false`；`scope` 断言因仍留着知识空间而不变，可见单独断言状态值**不足以**钉住这条语义——这正是用例 4 同时断言「标记仍在」的原因），已还原（`diff` 逐字节一致）。另：T-14 用例 2 的每一条「不出现」都配了一个**只差一个输入的对照**（同状态但库数/档位翻转 ⇒ 出现 1 张），否则「卡片永远不出现」也能让整条用例绿（负向断言假绿的经典形态）。
- **前端基线（与本次改动无关，供后续排查时排除）**：`src/frontend/client` 全量 `pnpm lint` **在本次改动之外**已有 5 个硬错误（`src/pages/apps/__tests__/explore.test.tsx` 2×`no-explicit-any` + 1×硬编码中文、`test/jsdomEnvironment.js` 2×`no-require-imports`）与两处**陈旧抑制**（`src/pages/apps/explore.tsx` 的 `no-explicit-any: 5`、`exhaustive-deps: 2→1`，以及 `Chat/Messages/Content` 下一个 `no-restricted-syntax: 17→15`），来源同 `8749bf50a`（该提交同时留下 `tsc-strict` 的 6 个报错）。本次**只**按文件范围 prune 了自己文件涉及的条目（`ChatKnowledge.tsx` 的 `no-explicit-any: 11 → 整条移除`、`ChatView.tsx` 28 → 22），未代他人清理。
- **Wave 4 变异验证**：把 `resolveOrgScope` 的兜底反写成 `hasSpaces ? 'all' : 'none'` ⇒ **7 个用例红**（含 T-07 用例 1/2/6/9 与 T-09 用例 5/8），已还原。前端其余基线：`tsc-strict` 6 个报错全在他人文件（`stepTree.ts` + `TaskPanel.test.tsx` 的 jest-dom 类型），客户端 jest 全集 5 个失败套件为既有 transform 基线（`Jest encountered an unexpected token`，与本次改动无关）。
- **T-17 消费者审计（2026-09-22，已合入）**：清单（消费者 × 行为）与 AC-20 写盘点名见 **T-17 任务的产出表**；论证在 **design 决策 14 + 坑 15 / 坑 7 更新**。三项要点：
  ① **`useChatFunctions` 判定为死代码**（不是「被引入就是活代码」的反例）：`src` 内**没有任何** `<ChatContext.Provider>` 挂载点（只有 `StandaloneChatContext` 这个无关的 context），`useChatContext()` 恒为默认空对象；其唯一产物 `store.submission` 全仓**无读者**。按根 `AGENTS.md §3`「与需求无关的死代码只标注不删除」，只在原处加注释，并写明**若复活必须改走 `toPayloadKnowledgeScope`**（否则会把 `__org_scope__` 当知识库 id 发给服务端）。
  ② **发现 AC-25 的前提只对执行态成立，超出原计划范围 → 已停下交需求方裁决**：任务模式**落地页**（`TaskModeChatInput`）整个复用日常 `AiChatInput`，必然继承两档入口与汇总卡；且其 `handleSend` 用 `some(k => k.type === 'org')` / `length > 0` 推导 `checked`(→`org_knowledge_enabled`) 与 `useKnowledgeBase`，而档位标记的 `type` 也是 `'org'`（坑 13）⇒ **「不使用」档会误报「用了组织知识库」**。澄清过程中还确认：落地页的**逐库 id 从未上过线**（`convertTools` 只读 `checked` 布尔，提交 payload 只有 `org_knowledge_enabled` / `personal_knowledge_enabled`），所以对齐两档不丢失任何已生效能力。**裁决：落地页跟随新口径**（默认档位「全部」），执行态维持原样；并接受由此产生的**唯一一处对外行为变化**——落地页首次提问的默认行为由「不检索组织库」变为「检索全部组织库」。同步产出：`isOrgScopeAll(items, enabled)` 成为**唯一档位判定入口**（与 payload 的 `organization_knowledge_all` 同源同值）、**spec AC-25 修订**（保护范围限定到执行态 + 记录落地页跟随与该默认翻转）、**design 决策 14 + 坑 15**。落地页注释统一英文（前端硬规则），文档为中文。
  ③ **两处遗留注释/零改动落点**：`MessagesView`「开启新对话」`setSelectedOrgKbs([])` ⇒ 档位回到兜底（「全部」）而非「不使用」——这是**清空即默认**的语义，与 AC-08 一致，且 `enableOrgKb` 是无消费方的遗留标记，故**不改行为只加注释**；`TaskModeInput` 的 `length > 0` 与落地页共用原子但 key 不同（坑 15），同样只加注释。
- **T-17 新增用例与变异验证（本次）**：`utils/knowledgeScope.test.ts` 新增 `isOrgScopeAll` / `showsOrgKbSummary` 一组 5 条（含一条**跨函数一致性**：对 7 种状态 × 开关两态，逐项断言 `isOrgScopeAll(...) === toPayloadKnowledgeScope(...).organization_knowledge_all` —— 钉住「界面说的档位」与「请求发的档位」是同一个答案）。变异：把 `isOrgScopeAll` 改成 `return enabled`（丢掉档位判定）⇒ **4 条红**，已还原。三条描述文案里的「全部/不使用」因 `no-restricted-syntax`（硬编码中文，Ledger #28）改为 `tier 'all' / 'none'`——该规则连**字符串字面量**里的中文也拦（注释里的中文不拦）。
- **T-18 回归实跑（2026-09-22，本次）**：本特性只改后端两个文件（`chat_schema.py` / `chat_service.py`），故基线对比用「备份 → `git checkout --` 这两个文件 → 重跑 → 还原」完成（**不走 `git stash`**：共享工作区里 stash 会被并行工作流的提交冲掉；备份落 `/tmp/f063_backup/`，还原后 `md5sum` 与备份逐字节一致）。**结果：零退化。**
  ① `test/workstation/`：有 F063 **26 failed / 191 passed**，无 F063 **26 failed / 165 passed** —— 失败集**逐条相同**，F063 净增的 26 条用例全绿。既有失败均为**环境性**，与本次改动无关：`test_workstation_apps_rebac.py` 硬编码了他人机器路径 `D:\Users/zhou/Code/bisheng/...`（`FileNotFoundError`），`test_conversation_export_import.py` 等因缺中间件撞 `object MagicMock can't be used in 'await' expression` 与消息落库乱码。
  ② `test/knowledge/`：两侧各 **75 failed / 825 passed**，`diff` 失败清单**仅差一处内存地址**（`PytestUnraisableExceptionWarning` 的 `Connection.__del__` 指针）⇒ 集合完全一致。
  ③ 无 F063 时的测试总数 191 与有 F063 时的 217 相差 **26**，恰为三个 `test_f063_*.py` 的用例数 ⇒ 除新增外无任何用例被改动或吞掉。
  **中间件限制**：本地仅 MySQL(3306) / Redis(6379) 在线，Milvus(19530) / ES(9200) / MinIO(9000) 未起，故上述「既有失败」里依赖真实中间件的部分（集成类）按 CLAUDE.md 口径属 **CI 覆盖**，本地不构成新增风险。
- **T-19 E2E 实跑（2026-09-22，本次）**：本机后端 `:7860` 与两个前端在跑，中间件**在远端** `172.19.70.189`（Milvus/ES/MinIO/MySQL/Redis/OpenFGA），故探本机 19530/9200/9000 得「未起」是**误判**——真实链路是通的。admin 的 `admin123` / `Bisheng@top1` 均 10600（与 F060 checklist 记录的同一环境状况），由需求方提供可用口令后跑通（口令未落任何文件，仅经 `E2E_ADMIN_PASSWORD` 环境变量传入）。
  **观测点（本 Feature 的关键设计）**：解析结果是**内部状态**，HTTP 响应不带它。可观测物是漏斗自己打的 INFO 行 `[resolve_user_kb] resolved N kbs ids=[...] tag_counts=[...]`（`chat_service.py:1118`）+ 收口那步的 `[perf][knowledge.permission_map] candidates=N kept=M`——正是 T-19 原文要的「后端日志对照」。e2e 用「记录偏移 → 驱动一轮 → 轮询日志」读取，并对日志轮转（00:00 切档）做了容错。
  **结果**：`test_e2e_f063_chat_knowledge_scope.py` **8 passed**（38s）。AC-10 的跨入口一致性实测为真：`all=true` 解析出的 19 个 id 与 `GET /knowledge?type=0` 的集合**对称差为空**——这是单测结构上做不到的那一条（双侧都是同一个 mock，断言恒真）。AC-11 的收口实测：无授权用户 `candidates=19 kept=0`。
  **三处断言返工（第一轮 3 failed，全部是测试自身的问题，非产品问题）**：①`_drive_turn` 原本只读首行就退出 `with`，而漏斗在首事件**之后**才跑 ⇒ 生成器被提前关闭、日志行永不出现（**这是最容易误判成「功能坏了」的坑**，已写进 `_send_turn` 的注释）；②对 SSE 响应体调 `.json()` → `JSONDecodeError`；③**AC-06 原本断言「日志里没有 resolved 行」**——而 `_resolve_user_kb_selection` 在 `if not ids: return []` 处**提前返回、本来就不打日志**，该断言打在一个不存在的观测点上。已改为**正向**观测：`不使用` + 一个知识空间 ⇒ 解析出空间且与组织库集合**交集为空**（比读一条缺失的日志强得多，同时把 T-19 场景 2 也覆盖了）。知识空间 id 由部署**现场发现**（dev 库 `type=3` 是 `[2, 26, 29, 81, 82, 83]`），不硬编码。
  **顺带发现（既有缺陷，非本 Feature 引入，未修）**：`>50` 校验器端到端返回的是 `{status_code:500,"Object of type ValueError is not JSON serializable"}` 而非 422。根因是 `main.py::handle_request_validation_error` 把 `exc.errors()` 直接交给 `JSONResponse`，而 pydantic v2 对 `field_validator` 抛出的 `ValueError` 会在 error 的 `ctx` 里挂**异常对象本身**，`json.dumps` 序列化失败后落入通用 `Exception` 分支。**与 F063 无关**：未改动的 `knowledge_space_ids` 校验器同样复现（已作为 e2e 的对照组断言）。拒绝语义本身未松动（`status_code != 200`，轮次不执行）——按根 `AGENTS.md` §3「与需求无关的问题只标注不修改」，只在 e2e 用例 docstring 里留档。
  **未做**：e2e 断言的**变异验证**。本机 uvicorn 是 `--workers 1` **无 `--reload`** 的单进程，源码改动对运行中的服务不可见——变异 M1（跳过 `use_kb` 收口）跑 AC-11 仍绿，反证了这一点（`permission_map` 行照旧出现）。收尾需重启后端；未擅自重启（进程由需求方启动）。
  > **本行的后续（2026-09-22，code-review H0）**：那次「变异 M1 仍绿」的成因被误解了一半 —— 服务不 reload 只是**让变异不可见**，而当时注入的变异行**从未被撤销**，一直留在工作区里。见下面的 H0 条目。

- **H0（code-review 期间发现并修复的 BLOCKER）：上一次 e2e 变异验证的注入行遗留在了工作区**。`chat_service.py:1009` 处留着 `return candidates  # MUTATION-M1 skip use_kb narrowing`，把 `_expand_all_org_knowledge` 的 `use_kb` 收口整段短路，**任何非管理员都能拿到本租户全量组织库候选集**（AC-11 失效，且是权限面）。**根因**：正是上面那行「未做」的收尾——变异注入后因服务不 reload 而看不出效果，收尾动作（重启后端再跑）被挂起，**撤销这一半也一起被挂起了**，而它在 tasks 里没有留下「当前工作区存在未撤销变异」的标记，后续读者只看到「未做变异验证」这个更轻的描述。**证据与处置**：撤销后 `chat_service.py` 与基线 `/tmp/f063_backup/chat_service.orig.py` **逐字节一致**（`md5sum` 比对）；两条原本被该行蒙绿的 F063 用例 RED→GREEN（收口类断言）；`test/workstation/` 回到文档基线 26 failed / 191 passed；`grep -rn "MUTATION"` 覆盖 `src/` 全树与后端 `bisheng/` 均为空。**教训（已反映为流程要求）**：变异注入必须与撤销成对落地——注入当时若不能验证，宁可不注入；一旦注入，工作区必须留可见标记，不能只靠 tasks 里一句软描述。**

- **H1（code-review 发现并修复）：`useGetOrgToolList` 的 `queryKey` 漏了 `page_size`，探测会截断选择器**。key 原为 `['OrgTools', page, name, preferred_ids]`，而本特性的存在性探测（`ChatKnowledge`→`AttachmentBar`，`page_size: 1`）与灵思组织库选择器（`page_size: 50`）**参数不同却共用全应用唯一的 `QueryClient`** ⇒ 塌成同一条缓存，**谁先挂载谁决定另一侧渲染什么**：探测在前就把选择器截成一行，且**不报错**（比 404 隐蔽）。已把 `page_size` 放进 key（`queries.ts::useGetOrgToolList`），因此 T-14/T-15 落点记录里那句共用 `['OrgTools', 1, undefined, '']` 的表述**已过期**，正确形状是 `['OrgTools', page, page_size, name, preferred_ids]`（design 决策 12 落点与**新增坑 17** 已同步）。注意 key **含**参数并不与「两处必须同参数」矛盾：前者防**不同参数互相污染**，后者防**同参数重复请求**，两条都要。

- **H1'（code-review 发现 → 需求方裁决「落地页兑现承诺」）：决策 14 原样落地时，落地页的档位对检索零作用**。复核链路（在当前源码上逐环验证，不是从报告转述）：① 落地页 `TaskModeChatInput.tsx:155` 把 `AiChatInput` 整个复用、未传 `isLingsi` ⇒ `hasSelectionTags` 为真 ⇒ `AttachmentBar` 挂载、汇总卡渲染；② `:120/:126` 把档位算进 `checked`（→ `org_knowledge_enabled`）与 `useKnowledgeBase`；③ `useLinsightManager.tsx:229-233` 提交时**只发这两个布尔、不发任何 id**；④ `workbench_impl.py` 把它们之外无 id 可存 ⇒ `organization_knowledge_ids` 落 `None`；⑤ `task_exec._resolve_user_knowledge_bases` 只解析 id 列表，并把这两个布尔记为「intentionally NOT consulted」（**坑 16**）。结论：落地页创建的会话**一生不检索任何组织知识库**，而决策 14 与 §578② 记录的是**相反**的「唯一一处对外行为变化」—— 原文只证了「不丢失已生效的能力」（真），漏了「也没获得任何能力」（同样真）。**裁决**：**落地页兑现承诺**，不撤回记录。**实现**（最小改动、不动已 ★ 确认的决策 2/3 落点）：前端新增 `SubmissionState.organizationKnowledgeAll`（落地页唯一写入，值即 `isOrgScopeAll`），`useLinsightManager` 直译为 payload 的 `organization_knowledge_all`（**显式 `false`**，让另两个未设该字段的生产者读作「不展开」而非「字段忘了」）；后端 `LinsightQuestionSubmitSchema` 新增同名字段，由 `submit_user_question`（该路径**唯一写入者**，且持有 `login_user`）在落库前调用**与日常漏斗同一个** `_expand_all_org_knowledge` 展开成具体 id —— 函数内延迟 import，避免 linsight→workstation 模块级环。**展开失败 fail-closed 到「本轮不查」**，绝不退化成「查全部」。**另一个生产者不设该字段**（执行态续的是 id 已定死的既有会话，AC-25；日常对话的灵思入口在到达 submit schema 前已自行解析出 id），故**不存在二次展开**。**测试**：新增 `test/linsight/test_f063_landing_org_scope.py`（3 条，全绿）与 `useLinsightManager.test.tsx`（3 条，全绿），两者均通过**变异验证**（含「值断言之外必须同时断言 `_expand_all_org_knowledge` 被调用 1 次」—— 只断值的话，硬编码一份列表同样能过，那就丢掉了「「全部」只有一个口径」这条证据）。**文档**：design 决策 14 补记、§4.2/§4.3/§6.1/§7 同步、新增坑 16。

- **M1（code-review 发现并修复）：切「不使用」档时误报「用了组织知识库」，并会挂出一条空白 chip**。两处症状同源（坑 13：档位标记的 `type` 恰是 `'org'`）：① `TaskModeInput.tsx` 用 `context.knowledge.length > 0` 推导 `checked`/`useKnowledgeBase` ⇒ 标记项把「不使用」报成「用了」，且**共享 `'new'` key 的窗口真实存在**（`ExecutionFlow` 在 `linsight.session_id` 未落 store 时回退 `'new'`，正是落地页写入的 key）—— 这**推翻了 T-17③「key 不同、只加注释」的结论**，坑 15 已改写为「真实窗口」；② 附件条按标记项渲染出一张**没有库名**的空白 chip。**修复**：`hasPickedKnowledge(items)`（只数真实条目）成为 `TaskModeInput` 的判定入口，`ContextChips` 渲染前先 `withoutOrgScopeMarker`，落地页侧用 `isOrgScopeAll`。三处均补测试。

- **M2（code-review 发现并补齐）：附件条的「挂载门」无测试**。T-17③ 修掉的那个空条 bug（`hasSelectionTags` 数了数组项而非卡片）**没有任何用例钉住**：`AttachmentBar.test.tsx` 测的是**条本身**，不是 `AiChatInput` "要不要挂这条" 的那个布尔 —— 而那个布尔正是决定空条 bug 能否复发的唯一开关，且**两个方向都只差一个字符**（数数组项 ⇒ 「不使用」档挂出空条；去掉 `showsOrgSummaryCard` ⇒ AC-15 默认态那条只装着汇总卡的条整条消失）。**补齐**：新增 `components/Chat/AiChatInput.test.tsx`（3 条源码断言，沿用 T-09 / H1' 的**源码接线**先例，理由见该文件头注释：`AiChatInput` 的渲染依赖图会为一条布尔赔上一打 mock）。**变异验证**：M1 `orgKbCardCount` 退回 `selectedOrgKbs.length`（1 红）、M2 挂载门退回数数组项（2 红）、M3 从门里摘掉 `showsOrgSummaryCard`（1 红），且**带一条无变异的对照跑**（绿）—— 没有这条对照，「全红」也可能只是文件被改坏导致整场跑不起来；还原后 `md5` 与备份逐字节一致（`23f1cee7…`）。`eslint` 该文件 0 报错。

