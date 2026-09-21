# Code Review Report — F060 术语库

> ## 修复状态（2026-09-21 复核后回填）
>
> | 编号 | 状态 | 修复 | 变异验证 |
> |---|---|---|---|
> | **H1** | ✅ **已修** | `agent.py`：`set_turn`/`reset_turn` 从 ReAct 分支**提到两个分支之外**（抽出 `_run_once_with_terms`）；补 3 条测试（2 条机制层 + 1 条 AST 断言）替换原来的源码文本断言 | **重放原 bug**（把 `set_turn` 放回分支内）→ AST 断言转红；`current_turn_block()` 恒返 `''` → 机制层转红 |
> | **H2** | ✅ **已修** | `term_importer.import_terms`：覆盖模式在删除前判 `planned` 为空则抛 11505；补 2 条测试（含 append 反向用例） | 删掉守卫 → 转红 |
> | **M2** | ✅ **已修** | `term_service._assert_unique` 与 `injection._build_automaton`/`_match` **统一改用 `casefold()`**；补 4 条测试 | 见下方「修复后复核」 |
> | **M1** | ✅ **已修** | 共享读取层新增 `read_csv()`（标准库 `csv.reader`，引号生效）+ `_pad()`；`term_importer._split_columns` 只对**单格行**（tab 分隔）再切；补 3 条测试 | 退回朴素 `split(',')` → 2 条转红；**F059 全套 228 条仍全绿** |
> | M3 ~ M6、L1 ~ L6 | 🔲 未修 | 见下方「未修项」 | — |
>
> 修复后：`test/term_library/ + test/sensitive_word/ + test/datasource/test_workflow_compat.py` = **368 passed**；ruff 干净（3 条 RUF/UP 为 `sensitive_word` 既有文件，非本次改动）。
>
> **M2 的一处诚实说明**：把匹配侧退回 `lower()` 不会让任何测试转红——`casefold` 与 `lower` 对中文和 ASCII 结果相同，差异只在 `ß`/`ﬁ` 这类字符上。选 `casefold` 是因为它与 `_assert_unique` 用同一个函数、语义上更正确，**不是因为它被测试证明了**。真正的修复点是「两侧都 fold」，那条有变异验证（见上表 M2 行）。
>
> ## 未修项（本轮未处理）
>
> | 编号 | 为何未修 |
> |---|---|
> | **H3**（超管租户范围） | 需与 `/api/v1/datasource`、`/api/v1/platform-sensitive-words` 一起改 `MANAGEMENT_API_PREFIXES`，属跨特性决策，不宜在 F060 单独动。**单租户部署（`multi_tenant.enabled=False`，默认）不受影响。** |
> | M3 唯一索引 | 需新增迁移（改已提交的 `v2_7_0_f060_term_library.py` 或加新版本），且要处理存量重复数据 |
> | M4 `create_user_id` 外泄 | 一行改动，但会动前端类型定义；建议与 M3 一起做 |
> | M5 其余落点的机制层测试 | 助手/工作台两个落点已有机制层覆盖；工作流节点本次已补 |
> | M6 design §4.3/§4.4 未同步 | 尚未回写 |
> | L1 aliases 无上限 / L2 上传全量读入 / L3 缓存撕裂读 / L4 换行未计入字符上限 / L5 `_TURN_MESSAGES` 死代码 / L6 RULE-3 WARNING | 见正文，均为低风险或全仓既有约定 |

**Feature**: F060-term-library
**Review scope**: 仅本次未提交的 F060 改动（不含仓库基线 `feat/2.7.0/045-minimax-tts-adapter...HEAD` 的 739 个文件，也不含并行工作流的 `_WORKSTATION_FLOW_ID` 改动）
**Changed files**: 后端 4 改 + 5 新；前端 1 改 + 2 新；测试 4 新
**审查方式**: 2 个独立审查 agent（正确性/边界/并发；权限/泄漏/路由）+ 主审自查；**每条 HIGH/MEDIUM 均已用可执行代码复现**

---

## Summary

| Dimension | High | Medium | Low | Status |
|-----------|------|--------|-----|--------|
| Correctness（接线） | **1** | 0 | 0 | **NEEDS_FIX** |
| Boundary Conditions | **1** | 1 | 1 | **NEEDS_FIX** |
| Permission & Auth | **1** | 0 | 0 | **NEEDS_FIX** |
| Concurrency Safety | 0 | 1 | 1 | PASS_WITH_WARNINGS |
| Information Leakage | 0 | 1 | 0 | PASS_WITH_WARNINGS |
| Test Coverage | 0 | 1 | 1 | PASS_WITH_WARNINGS |
| Code Style | 0 | 0 | 1 | PASS |
| Docs Sync (design.md) | 0 | 1 | 0 | PASS_WITH_WARNINGS |

## Overall: **NEEDS_FIX**（3 条 HIGH）

---

## HIGH

### H1 —[Correctness] 工作流 Agent 节点的 langgraph 分支**从未**注入术语块（AC-24 静默不成立）

`workflow/nodes/agent/agent.py:385` + `:804` + `:824`

`_init_agent` 把 langgraph 分支接成 `_term_prompt`，其函数体读的是 `current_turn_block()`（ContextVar `_TURN_BLOCK`）。但 `_TURN_BLOCK` **只在 ReAct 分支被写入**（`:804` 的 `set_turn`）；`else:` 分支（`:824`）直接 `self._agent.invoke(...)`，没有 `set_turn`，于是 ContextVar 恒为默认 `''` → `append_block` 原样返回 base prompt → **节点行为与加 F060 之前逐字节相同**，无报错、无日志。

**已复现**（用节点构造的那个函数，不 monkeypatch）：

```
current_turn_block() -> ''
system message       -> '工作流节点系统提示词'
VERDICT: block injected? False
```

触发条件：模型配置 `agent_executor_type = "function call"`（模型配置界面的一等选项）的流程，其 Agent 节点提问命中术语 → 该节点收不到任何术语。

**为何测试没抓到**：`test_injection_sites.py` 对这个落点的断言是**源码文本** `'current_turn_block()' in source` —— 它只要这段字面量在文件里就通过，无论 `set_turn` 有没有被调用。这正是我自己在 tasks.md 里写下的「接线层断言」的盲区。

**修法**：把 `set_turn(...) / reset_turn(...)` 提到 `_run_once` 的两个分支**外面**（整段 `if/else` 包进 try/finally），两条分支共用一次声明。

---

### H2 —[Boundary] OVERWRITE 在「全部行被丢弃」时**清空整个词库且不写入任何内容**（AC-09 + 数据丢失）

`term_library/domain/services/term_importer.py:259-264`

`import_terms` 先删后插，**没有对 `planned` 为空做守卫**。`read_term_rows` 只拒绝「整个文件全空白」（`:120-123`）；一份每行都超长的文件**不是空白文件**，于是 `_plan` 返回 `planned == []`，删除照常执行。

**已复现**：

```
result: TermLibraryTermPreview(added=0, duplicates=0, dropped_overlong=3, removed=3)
rows deleted: 3
rows written: 0
VERDICT: LIBRARY WIPED WITH ZERO ROWS WRITTEN
```

触发：词库有 300 条，管理员选了一份「释义列是长段落（>200 字）」的导出 → 每条都被丢 → 300 条删除、0 条写入、返回 `200 {added: 0, removed: 300}`。**硬删除、无历史**，只能靠重导恢复。

这与模块 docstring 自己的承诺直接冲突（「OVERWRITE 先校验后清空，是『文件不可用』与『库被清空』的区别」）——守卫写了，但只覆盖「全空白」这一种。AC-09 明写「无有效词条 SHALL 被拒且不产生任何落库」。

**修法**：`if mode == OVERWRITE and not planned: raise TermImportFileParseError(...)`，放在删除之前。

---

### H3 —[Permission] 切换了租户范围的超管，读写的是**另一个租户**的词库（INV-16；含覆盖式破坏）

`api/router.py:103` 挂载 `/api/v1/term-library`，但该前缀**不在** `MANAGEMENT_API_PREFIXES`（`common/middleware/admin_scope.py:63-71`）。

于是 `strict_tenant_filter()` → `_resolve_visible_tenant_ids()` 返回 `None` → 落到 `_resolve_tenant_id()` → `get_current_tenant_id()`，后者在 `:88-91` **优先返回 admin scope**。但 scope 只在命中管理前缀时被设置，`/api/v1/term-library` 永远命中不了。

具体后果：超管把范围切到子租户 5（`/api/v1/knowledge` **在**前缀表里，所以文件/QA 两个 Tab 显示的是租户 5 的数据），打开术语库 Tab —— 看到的和写进去的都是 **JWT 租户（通常是 ROOT）** 的词库；`POST /import` 选 **overwrite** 会删掉 **ROOT 租户的全部术语**，而界面上的破坏性提示报的是租户 5 的条数。

**注意**：`multi_tenant.enabled` 默认 `False`（`core/config/multi_tenant.py:9`），故单租户部署不受影响；且这是**全仓系统性缺口**（`/api/v1/datasource`、`/api/v1/platform-sensitive-words` 同样不在表内），修法是前缀表决策而非 F060 局部补丁。但 F060 新挂了这条路由，且它是**唯一带破坏性覆盖导入**的租户级接口。

---

## MEDIUM

### M1 —[Boundary] 释义里含中文逗号会被**静默截断**

`term_importer.py:62`（`COLUMN_SEPARATOR_RE = re.compile(r'[,，]')`）+ `_read_row` 只取 `columns[2]`

csv 行以单格到达（共享读取层只在制表符上切列），故 `_split_columns` 按 `[,，]` 切。`，` 是**普通中文标点**，不是生僻字符。

**已复现**：`不良率,NPL,指逾期90天以上的贷款，含次级与可疑类`
→ definition = `'指逾期90天以上的贷款'`，`'，含次级与可疑类'` **丢失**，预览显示 `added: 1` 无任何提示。截断后的内容正是注入进 prompt 的东西（AC-20）。同类：`不良率,NPL，次级,释义` 只存下别名 `['NPL']`。

**注**：`_split_columns` 的 docstring 只豁免了「第 4 列及以后是管理员自己的备注」——**释义字段内部**的逗号不是那种情况。

---

### M2 —[Permission/Data] 唯一性校验是**大小写敏感**的，而匹配是**不敏感**的 → 两个词条只有一个可用

`term_service.py:85-107`（`_assert_unique` 比原串）vs `injection.py:120-126`（`_build_automaton` 两侧 lower）

**已复现**：建 `NPL` 与 `npl` 两条**都能通过**校验（AC-02/AC-03 明文禁止）；注入时 `setdefault` 让先到的占住 folded key，另一条**永远匹配不到** → 管理员编辑它「看起来没生效」。`injection.py:110-111` 的注释还建立在这个错误前提上（「服务已经禁止这种冲突」）。

**修法**：`_assert_unique` 用 `casefold()` 归一后再比对。

---

### M3 —[Concurrency] `standard_term` 无 DB 层唯一约束 → 并发写入产生重复词条

`term_service.py:130-137` 是 check-then-insert（先 `_assert_unique` 再 `acount` 再 `acreate`）；迁移 `v2_7_0_f060_term_library.py:99-100` 建的是**普通索引**不是唯一索引。

spec §3 明写「以租户内唯一约束为准，冲突方失败并明示，不产生重复词条」。两个管理员同时保存 `不良率`（或双击保存 + 重试）→ 两次都读到「不存在」→ 两条都插入成功。上限 500 同样有此竞态（两个并发 APPEND 各算得 300+300 ≤ 500，实际写 600）。

**注**：别名侧因为存在 JSON 列里**确实**无法建唯一索引（设计已论证），但 `standard_term` 是可索引的。

---

### M4 —[Leakage] `create_user_id` 返回给所有读权限用户，且无人消费

`terms.py:78`（`_term_dict`）→ `termLibrary.ts:13` 声明了但**前端从未渲染**。拿到 `knowledge` 菜单但无用户管理菜单的人，据此可反查每条术语是哪个管理员建的。删掉该字段前端无任何变化。

---

### M5 —[Test Coverage] 三个落点的覆盖方法不一致，导致 H1 漏网

- 助手两个分支：机制层（真 agent + 假模型，断言模型收到的 system message）✅
- 工作流节点：**仅源码文本断言** ❌ —— 这正是 H1 逃逸的原因
- 工作台：源码文本断言（该路径是同步拼接，文本断言尚可接受）

**修法**：给工作流节点的两个分支各补一条机制层断言（构造 `_term_prompt` 同形的 callable，断言 `set_turn` 后块真的进了 system message）。

---

### M6 —[Docs Sync] design.md §4.3 / §4.4 仍是**被推翻的旧形状**

§10.1 记录了「新增 `prompt.py`」「注入形状改为『解析在知道本轮的一层，prompt 层只拼接』」，但 §4.3 模块职责表**没有** `prompt.py` 这一行，§4.4 仍写「三处落点统一改 callable」（实现期证明只对两个 langgraph 分支成立）。新 agent 读 §4.4 会得到错误认知。

按本 skill 维度 7 的判定要点（「design.md 与代码现状偏离 → HIGH」），此处偏差内容本身已在 §10.1 显式记录、非隐瞒，故降为 MEDIUM；但 §4.4 的表述会直接误导人，**必须改**。

---

## LOW

- **L1 [Boundary]** `aliases` 无长度/数量上限（`terms.py:41` 无 `max_length`，`_clean_aliases` 只 trim+去重，导入侧同样不查）。单次调用可写入超大 JSON 列，而该列在 `build_term_block` 的**每轮热路径**上被读。兄弟字段 `standard_term` 255、`definition` 200 都有上限，此处缺一个。
- **L2 [Boundary]** 上传**先全量 `await file.read()`**（`terms.py:135,155`），无 Content-Length 或流式上限，`.xlsx` 亦无解压上限。与 F059 的导入端点同形（同样无上限），故非本特性独有。
- **L3 [Concurrency]** `injection.py:277-278` 的 `_CACHE_KEY` / `_CACHE_VALUE` 是两个独立赋值，**跨线程**可能读到撕裂的一对（单事件循环内安全，无 await 间隔；且 key 含 `_library_digest`，跨租户泄漏需两租户词库逐字节相同）。进程内同时存在 API loop 与 `run_async_safe` 的 bridge loop 时理论可达。改成 `_CACHE = (key, value)` 元组原子更新即可。
- **L4 [Correctness]** `_render_capped` 的字符预算**不计自己的换行符**（`used += len(line)`，但 body 是 `'\n'.join(...)`）。**已复现**：`MAX_BLOCK_CHARS = 2000` 时实际渲染 2243 字符，超 243。AC-23 说「按字符数封顶」。
- **L5 [Dead code]** `prompt.py` 的 `_TURN_MESSAGES` 被 set 但**从无读取方**（`current_turn_text()` 无调用者）。「messages 用于匹配」这句设计说明高估了代码实际做的事。无正确性影响。
- **L6 [Style]** `terms.py:31` 直接 `from bisheng.database.models.role_access import WebMenuResource` → arch-guard RULE-3 WARNING。**全仓 41 个端点、含 knowledge 端点本身同形**，属既有约定级 WARNING（非 VIOLATION），不建议在 F060 单独改。

---

## 已验证为干净的部分

- **错误码混淆**：跨租户 id 在 update/delete 上都返回 11501，与「不存在」不可区分（设计如此）；11503 的消息只含调用者自己租户内的字符串。
- **DAO 租户过滤**：`alist_all` / `alist_page` / `acount` / `aget_by_id` 四条读路径全部包裹；`select(func.count())` 也经 `get_final_froms` 兜底拿到谓词，`total` 不泄漏他租户行数；写操作只碰过滤读出来的实例。
- **路由**：`{term_id:int}` 编译为 `^/term\-library/(?P<term_id>[0-9]+)$`，**不匹配** `/import`、`/import/preview`；两个字面路由还先注册且为 POST-only。
- **前端**：`import axios from "../request"` 解析到 `controllers/request.ts` 受控包装（与 26 个兄弟模块同形），未违反 C7；无业务 403 分支；术语文本按 JSX 文本渲染，无 `dangerouslySetInnerHTML`。
- **ContextVar set/reset 配对**：`agent.py:804/814`、`assistant_agent.py` 的 react_run 均在 `try/finally` 内；参数求值顺序保证块先解析后声明；同 loop 内并发请求互不可见。
- **分页与路径参数边界**：`page ≥ 1`、`page_size 1..100`、offset 钳位；未知部门解析为 `[]`（空页）而非全集。
- **导入去重/预览计数**：`taken` 两侧都覆盖、`taken |= names` 捕获文件内重复、OVERWRITE 正确从空集起算（除 H2、M1 两个例外）。

---

## 建议的修复顺序

1. **H2**（数据丢失，一行守卫）
2. **H1**（功能静默失效，把 set/reset 提到分支外）
3. **M2 / M1**（都是「静默给出错误结果」，且都在核心匹配/导入路径上）
4. **H3**（需与 F059/F061 一起决定前缀表策略）
5. M3 → M6，再 LOW
