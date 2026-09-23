# Tasks: 数据源字段说明人工订正（064-datasource-field-comment-override）

> 执行流水（append-only）。每项绑定验证标准：`[步骤] → 验证：[检查项]`。
> 需求口径见 [spec.md](./spec.md)，设计真相见 [design.md](./design.md)。
> 开发基线：项目交付集成基线分支（特性间线性叠加，非各自独立分支）。

---

## Wave A — 存储与迁移

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-01 | `DbDatasourceTable` 新增 `column_overrides`（`JsonType`，nullable，中文列注释） | — | `python -c "import ..."` 可导入；`pytest test/datasource/test_schema_service.py` 全绿 | ☑ |
| T-02 | Alembic 迁移 `v2_7_0_f064_field_comment_override`，`down_revision = f063_term_standard_term_unique` | — | `uv run alembic heads` 仍**只有一行**；`pytest test/database/test_alembic_single_head.py` 通过 | ☑ |
| T-03 | **扩展** `DbDatasourceTableDao.aupdate_business_desc` → `aupdate_human_fields`（人工列的唯一写入者，哨兵默认值）—— **不新增 DAO 入口**（C1，见偏差记录） | AC-04 | 单测：① 只传 `business_desc` ⇒ UPDATE 列清单恰为 `{business_desc}`；② 只传 `column_overrides` ⇒ 恰为 `{column_overrides}`；③ 两者同传 ⇒ 恰为两者；④ 都不传 ⇒ **零 UPDATE**；⑤ 显式传 `None` ⇒ 写 NULL（非空字典） | ☑ |

## Wave B — 写入编排与读路径

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-04 | `schema_service.update_column_overrides`：`strip()` + 丢弃空值键（决策 5），再经 `aupdate_human_fields` 写入（**不再有独立的 DAO 入口**，见偏差记录） | AC-09 | 单测：`{"a":" ","b":"x"}` → `{"b":"x"}`；全空 → `None`；`"  含税金额  "` → 存 trim 后的值 | ☑ |
| T-05 | `schema_service.list_tables_for_admin` 返回 `column_overrides`（存的是 NULL，**读出去归一化为 `{}`**，前端免 null-check） | AC-08 | 单测：有订正 ⇒ 原样返回；存 NULL ⇒ 返回 `{}` | ☑ |
| T-06 | `areplace_snapshot` 的 UPDATE 列表**不含**新列（回归确认，非改动） | AC-04 | 两半：DAO 侧既有 `test_snapshot_refresh_updates_only_the_collected_columns` 断言列清单恰为 `{structure_snapshot, table_comment}`（加新列即红）；服务侧新增「重采集**从不**触达人工写入者」 | ☑ |

## Wave C — 注入与召回

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-07 | `render_table`：字段说明「订正优先、库内兜底」—— 与 T-08 共用**同一个**取值函数 `_column_comment`（决策 4 硬要求） | AC-06 | 单测：有订正 ⇒ 用订正且**库内原值不出现**；同一表**逐列**兜底（覆盖一列不抹掉其它列）；悬挂键**不注入**（AC-05）；无该键的旧调用方行为不变 | ☑ |
| T-08 | `_haystack`：同上取值后参与召回 | AC-07 | 单测：目标表**只有订正文本**含该词、且被排在**最后**（`_retrieve` 有「零命中就取前 N 个」兜底，放开头会让坏匹配器也通过）；另置一张含该词的对照表证明匹配确实在工作 | ☑ |
| T-09 | `tool.py:250` 的 dict 带上 `column_overrides`（design §5 第 1 条） | AC-06 | 单测：构造工具后 `tables[0]['column_overrides']` 为订正内容；存 NULL 时为 `{}`（与 T-05 同口径） | ☑ |

## Wave D — API

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-10 | `TableDescriptionPayload` 加可选 `column_overrides`；端点按 `model_fields_set` 分别写入 | AC-01 / 兼容性 | API 测试：只发 `business_desc` ⇒ 订正不变；只发订正 ⇒ 描述不变；两者同发 ⇒ 都生效；显式 `null` ⇒ 清空（**`null` 是值不是省略**）；空 body ⇒ 零写入 | ☑ |
| T-11 | 端点鉴权沿用 `_require_manage`（不新增字段级授权） | AC-11 | API 测试：未授权主体被拒，**且订正在校验之前没有被写入** | ☑ |

## Wave E — 前端

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-12 | `DatasourceTableRow` 加 `column_overrides`；`updateTableDescriptionApi` 支持一次提交两段内容，**不传订正参数时该键不出现在请求体里** | AC-01 | `tsc-strict` 通过；测试断言请求体两段内容 | ☑ |
| T-13 | `TableDescriptionDialog` 增加字段列表（字段名 + 库内原值**只读同屏可见** + 人工说明输入） | AC-08 | 组件测试（**用 `screen`**：Dialog 走 Radix portal，对 `container` 的「不出现」断言恒真） | ☑ |
| T-14 | 详情页字段表显示生效值 + 「已订正」标记；有订正时**不显示**「未采集到注释」告警 | AC-08 / AC-10 | 组件测试：两种状态的断言方向相反（避免恒真）—— 有订正 ⇒ 显示订正+标记且**无**「未采集到注释」；无订正 ⇒ 显示库内原值且**有**该告警 | ☑ |
| T-15 | 弹窗字段列表列出「仅存在于订正」的字段并标「已不在库中」，排在实时字段之后 | AC-05 | 组件测试：该类字段出现且带标记；**反方向**（实时字段不带该标记）同时断言，避免标记是常量 | ☑ |
| T-16 | 三语文案（zh-Hans / en-US / ja），每个语言 7 个 key | i18n CI | `pnpm check-i18n` OK；`eslint` exit 0；三份 JSON 均可解析、key 无重复 | ☑ |

## Wave F — 端到端与交付

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-17 | e2e：写订正 → 重采集 → 注入文本仍为订正；客户库注释未被改写 | AC-02 / AC-04 / AC-06 | `pytest test/e2e/test_e2e_f064_field_override.py` 通过 | ☐ |
| T-18 | 回归：F061 数据源既有测试全绿 | — | `pytest test/datasource/` 通过 | ☐ |
| T-19 | 质量门 | — | `arch-guard.sh` exit 0；平台 `pnpm lint` + `typecheck` 通过 | ☐ |

---

## 执行记录

（实现过程中按 Wave 追加：做了什么、踩了什么坑、偏差与处理。）

### Wave A（2026-09-23）

**T-01 / T-02 按设计实现，无偏差。**

**T-03 —— `/task-review T-03` 抓到 C1 违规（HIGH），决策 2 推翻重写（经用户确认）。**

- **症状**：原设计选定「新增 `DbDatasourceTableDao.aupdate_column_overrides`，与 `areplace_snapshot`、`aupdate_business_desc` 三足鼎立」。`/task-review` 的**检查项 #1（架构分层，HIGH）**判定其违反 `docs/constitution.md:30`：**「never add new DAO entry points for new features」**。
- **为什么不是误报**：`datasource` 模块由 **F061 建立**，本特性是给**既有模块**的 DAO 加口子。仓库里有两次同型先例 —— **F062** 把这条写进 design（四处引用）并立为**坑 14**，用既有 `DepartmentDao.aget_active_by_tenant` 绕开；**F063** 拆 tasks 时撞到同一句，被记为 **BLOCKER**，设计改写为「复用两处既有入口、本特性零新增」。F060/F061 不受此限，因为它们是在**新建模块**时定义 DAO 的初始面。
- **为什么不能「找个既有入口复用」了事**：数过 `DbDatasourceTableDao` 的全部方法（`acreate` / `alist_by_datasource` / `alist_by_ids` / `areplace_snapshot` / `aupdate_business_desc` / `aset_enabled` / `adelete_by_datasource`），**没有一个能承载 `column_overrides`** —— `areplace_snapshot` 写它就等于直接破掉 AC-04 的结构性保证。而 C1 的**前半句**又禁止在 Service 里直接写 ORM。两条合起来是夹逼。
- **处置（选定 C，用户确认）**：**扩展** `aupdate_business_desc` → `aupdate_human_fields(table_id, *, business_desc=…, column_overrides=…)`，成为**人工列的唯一写入者**；`areplace_snapshot` 仍是机器列的唯一写入者。**扩展既有入口不是新增入口**，C1 字面合规。
- **随之而来的实现约束**：人工列上 `None` 是**合法值**（表示清空，决策 5），所以默认值**不能**是 `None`，必须用哨兵 `_UNSET` —— 否则「只保存业务描述」会把全表字段订正清空且不报错。已作为 **design 坑 8** 记录，并写进 `test_datasource` 的一对用例（`test_an_empty_call_writes_nothing_at_all` / `test_explicit_none_clears_rather_than_being_treated_as_unset`）——**只有哨兵正确时这两条才会同时通过**。
- **同步范围**：design 决策 2（重写）+ §2 新增 C1 约束行 + §4.3 `dao.py` 行 + 新增坑 8 + 修订历史；`schema_service.update_description` 改调新方法；3 处既有测试引用改名。
- **对后续 Wave 的影响**：**T-04 / T-10 的形态随之改变** —— 变的不是 service 的函数个数（`update_column_overrides` / `update_description` 两个都保留，各自只写一列），而是**它们下面只有一个 DAO 入口**：`aupdate_human_fields`。端点仍按 `model_fields_set` 决定调用哪一个（T-10 原文「分别写入」正是这个意思）。**决策 3 不受影响**，且与 DAO 层的哨兵同构：API 判「有没有提交」、DAO 判「有没有传」，同一条规则两层各把一次关。
- **验证**：`test/datasource/` **467 passed**；`arch-guard.sh` exit 0；`ruff` 干净；`alembic heads` 单行。

### Wave B（2026-09-23）

**三个任务按设计实现，无偏差**（形态受 Wave A 的决策 2 变更影响，见上）。

- **T-04**：`update_column_overrides` 先归一化再写。两条决策 5 的语义各有一例：`{"a":" ","b":"x"}` → `{"b":"x"}`；全空 / 空入参 → `None`（**不是 `{}`** —— `{}` 会让下游多出一个「订正过、订正为空」态，而 AC-09 只承认两个态）。另加一例「padded 值存的是 trim 后的」：只断言「非空键存活」是抓不住 padding 被原样存下的。
- **T-05**：读路径把存储形态 `NULL` 归一化为 `{}`，前端免 null-check。**存的是 NULL、读的是 `{}`**，两个方向各有一条用例，这个默认值因此是决策而非巧合。
- **T-06**：**非改动，是回归护栏**。DAO 侧已有的 `test_snapshot_refresh_updates_only_the_collected_columns` 断言列清单**恰为** `{structure_snapshot, table_comment}` —— 往 `areplace_snapshot` 里加一行新列就会红。服务侧补了另一半：重采集**从不触达**人工写入者（那是唯一能命名 `column_overrides` 的方法）。
- **变异验证（4/4 捕获）**：① 归一化失效原样存；② 只 strip 不丢弃空白（全空存成 `{}`）；③ 读路径不归一化 `None`；④ 在重采集路径插一次人工写入调用。文件逐字节还原、无残留。
- **验证**：`test/datasource/` **473 passed**；`ruff` 干净。

### Wave C（2026-09-23）

**三个任务按设计实现，无偏差。**

- **T-07 / T-08 共用一个取值函数 `_column_comment`**，这是决策 4 的硬要求：「模型看到的说明」与「召回依据的文本」必须同源。分叉的症状是**没有报错**——只是模型按一套措辞作答、检索按另一套找不到表。
- **逐列兜底，不是整表开关**：覆盖一个字段不抹掉其它字段的库内注释（有用例钉住）。
- **悬挂键不注入**：取值按 `structure_snapshot` 遍历，`column_overrides` 里已不在快照中的键**天然读不到** ⇒ AC-05「保留但不注入」无需显式过滤。用例明确断言该文本**不出现**。
- **T-08 的 fixture 是专门设计的**：`_retrieve` 有「零命中就取前 N 个」的兜底，所以目标表**必须排在最后**——放开头的话，一个读不到订正的坏匹配器也会因兜底而返回它，断言就恒真了。另置一张含该词的对照表，证明匹配确实在工作。
- **T-09**：注入路径的第二个 dict 构造点（design §5 坑 1）。丢掉这个键是**静默半生效**：界面显示订正、模型看到库内原值，全程无报错。用例直接断言 `tables[0]['column_overrides']`。
- **变异验证（4/4 捕获）**：① 取值口径忽略订正；② 优先级翻转（库内注释盖过订正）；③ `_haystack` 忽略订正；④ 注入路径的 dict 丢掉该键。两个文件逐字节还原、无残留。
- **验证**：`test/datasource/` **480 passed**；`ruff` 干净。

### Wave D（2026-09-23）

**两个任务按设计实现，无偏差。**

- **T-10**：payload 加可选 `column_overrides`，端点按 `model_fields_set` 决定写哪一列。**要点是「省略」与「显式 `null`」必须分开**：校验之后两者都是 `None`，`model_fields_set` 是这层区分能存活的唯一地方。既有前端只发 `business_desc` 且**不会随服务端一起升级**，所以「没提到就不写」是这一条的**兼容性承重处**，五个用例把五种组合各钉一次。
- **写入分两条语句**（各调用一个只写一列的 service 函数），而不是合成一条。设计决策 3 反对的是「**两次保存动作**」（半套改动的真实来源），本实现是**一次请求**；保持「一列一个写入者」比省下一条 UPDATE 更值。此点已在下面记录，供复审判断。
- **T-11**：鉴权完全沿用 `_require_manage`，不新增字段级授权（数据源是授权单位，F061 AC-48）。用例额外断言**写入发生在校验之后** —— 仅断言「抛异常」是抓不住「先写后校验」的。
- **端点此前没有任何行为测试**（只有一条路由路径存在性断言），本次是它第一次被真正测到。
- **变异验证（3/3 捕获）**：① 描述无条件写入；② 订正无条件写入；③ 写入挪到权限校验之前。文件逐字节还原、无残留。
- **验证**：`test/datasource/` **486 passed**；`ruff` 干净。

### Wave E（2026-09-23）

**五个任务按设计实现，无偏差。**

- **T-12**：API 层。`column_overrides` **只在调用方传了它时才出现在请求体里** —— 这不是风格问题：服务端按 key 是否存在决定写不写，多传一个 `null` 就会清空订正。
- **T-13 / T-15**：弹窗加字段列表。库内原值**同屏只读可见**（AC-08：不同屏的话「我该订正成什么」无从判断）；`column_overrides` 里已不在快照中的键**排在最后并标「已不在库中」**，不丢弃。
- **T-14**：详情页展示**生效值**（订正优先）而非库内原值，并带「已订正」标记。展示库内原值是最坏的一种错——管理员会在一个**看不到自己订正**的界面上核对订正；而且「未采集到注释」会出现在一个模型其实描述得好好的字段旁边。
- **T-16**：七个 key 三语齐平，用文本插入而非 JSON 往返（diff 最小、不重排全文件）。
- **变异验证（4/4 捕获）**：① 详情页忽略订正；② 订正优先级丢失；③ 弹窗不列悬挂字段；④ 弹窗不显示「已订正」标记。两个文件逐字节还原。
- **测试断言一律用 `screen`**：Dialog 走 Radix portal，`container` 对弹窗内容恒为空，任何「不出现 X」的断言都会假绿。
- **验证**：新测试 **9 passed**；`tsc-strict` 0 错误；`eslint` 干净；`check-i18n` OK。
- **既有失败（与本次无关）**：平台全量 **7 文件 / 15 条**失败（权限、审批、布局、`knowledgePermissionDialog` 的 `lucide-react` mock 缺 `Rocket` 导出）。已用**对照实验**确认：把三份 locale 还原到 HEAD 后重跑，非 i18n-mock 的三个文件仍是 **10 failed**，与带改动时一致 ⇒ 既有失败。
- **过程失误（已修正，记录在案）**：做对照时用了 `cp A B C /tmp/`，三个文件同名 `bs.json`，`cp` 拒绝覆盖并**返回非零** ⇒ `&&` 链短路，`git checkout` 根本没执行。所以那一次「对照」跑的其实仍是改动后的版本，**没有起到隔离作用**；重做时才改用不同文件名备份。文件内容本身未受影响（已逐项校验：各 8 增 1 删、key 无重复、JSON 可解析、`check-i18n` OK）。
