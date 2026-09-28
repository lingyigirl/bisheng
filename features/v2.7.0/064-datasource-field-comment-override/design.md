# Design: 数据源字段说明人工订正（064-datasource-field-comment-override）

> **本文档定位 — 现状快照（Why this How）**
>
> 只写本特性的 How：决策 + 备选 + 今天的代码长什么样 + 踩过才知道的坑。
> 需求口径在 [spec.md](./spec.md)；执行流水在 [tasks.md](./tasks.md)。
> 全局法则（DDD 分层 / 双 DB / 多租户 / 权限 / 错误码 / 安全）见 `docs/constitution.md`，本文不重复。

---

## 1. 目标与非目标

**目标**：给「字段级人工订正」一个可操作的落点——管理员能为单个字段写说明，该说明**只存在平台侧**、**不被重新采集覆盖**、并**优先于库内采集注释**注入模型（spec AC-01 ~ AC-11）。

**非目标**（对应 spec §范围边界）：不回写客户库；不改表级业务描述口径；不引入字段级授权；不设长度上限；不采集样例值。

---

## 2. 关键约束

| 约束 | 依据 | 对本特性的影响 |
|---|---|---|
| **平台对客户库没有写通道** | **INV-19 ②**：查询仅放行单条 SELECT，拒绝多语句 / 写操作 / 结构变更 | AC-02 不是新增承诺，而是既有约束的推论——本特性**不需要**为此加任何拦截代码 |
| 重新采集不得覆盖人工内容 | F061 AC-12（表级口径） | 扩展为字段级：新列必须**不被** `areplace_snapshot` 的 UPDATE 列表提及 |
| **C1 禁止为 new feature 新增 DAO 入口** | `docs/constitution.md:30`；先例：F062 坑 14、F063 设计改写 | 人工列的写入**只能落在既有入口上** ⇒ 决策 2 由「新增第三个写入方法」改为「**扩展** `aupdate_business_desc`」。且 `None` 在此是合法值，扩展时默认值必须用哨兵（坑 8） |
| 单一 head 的迁移图 | `alembic/AGENTS.md` §0 | 新迁移的 `down_revision` 必须取 `uv run alembic heads` 的真实输出 |
| 双 DB（MySQL / DM8） | 宪法 C2 | 列类型沿用既有 `JsonType` / `LargeText`，不引入新类型 |
| 多租户自动过滤 | 宪法 C3 | 新列挂在既有 `db_datasource_table` 上，随行的 `tenant_id` 与 `strict_tenant_filter()` 自动生效，无需新代码 |
| 错误码段 | 模块 117（F061） | 本特性不新增错误路径 → 不新增错误码（见 release-contract 编码登记） |
| 前端 i18n | 根 `AGENTS.md` §4 | 新增文案三语同 PR 落地，走 `pnpm check-i18n` |

---

## 3. 方案对比与选定

### 决策 1：订正内容存哪

- **备选 A**：复用 `structure_snapshot`，在每列上多写一个 `override` 键。
  → **否决**：`refresh_tables` 走 `areplace_snapshot` **整列替换** `structure_snapshot`，订正会被下次采集抹掉——直接违反 AC-04，而且失败是静默的（采集完才丢）。
- **备选 B**：新建独立表（`table_id` + `column_name` + `override`）。
  → **否决**：为「随表一起读、随表一起写」的几十个短字符串新增一张表、一处归属登记、一次 join，收益不抵复杂度（也偏离极简口径）。
- **选定 C**：在既有 `db_datasource_table` 上新增一列 `column_overrides`（`JsonType`，`{"字段名": "人工说明"}`）。

**原因**：与行的生命周期天然一致（表被删则订正随之消失），读路径零额外查询；且「一个字段的订正」本就属于「这张表的元数据」，不是独立实体。

### 决策 2：写入路径的切法

- **备选 A**：一次 UPDATE 同时写 `structure_snapshot` + `table_comment` + `business_desc` + `column_overrides`。
  → **否决**：把「机器采集」与「人工内容」重新混进同一条写路径，正是 F061 `schema_service.py` 模块文档串里明确要避免的事（AC-12 会被一行改动破掉）。
- **备选 B（原选定，Wave A 实现期推翻）**：新增 `aupdate_column_overrides(table_id, overrides)`，与 `areplace_snapshot`、`aupdate_business_desc` **三足鼎立**，每列一个写入者。
  → **否决**：**C1 明文「never add new DAO entry points for new features」**（`docs/constitution.md:30`）。`datasource` 模块由 F061 建立，本特性是给**既有模块**加口子 —— F062 把这条写成**坑 14**、F063 为同一句**改写了自己的设计**，是同一处红线的两次先例。而且**没有可复用的既有入口**：`areplace_snapshot` 不写这一列（写了就直接破 AC-04），`aupdate_business_desc` 只写描述；C1 前半句又禁止在 Service 里直接写 ORM —— 两条合起来是夹逼。
- **选定 C**：把 `aupdate_business_desc` **扩展**为 `aupdate_human_fields(table_id, *, business_desc=…, column_overrides=…)`，成为**人工列的唯一写入者**；`areplace_snapshot` 仍是机器列的唯一写入者。

**原因**：
- **扩展既有入口不是新增入口**，C1 字面合规。代价是把「三足」并成「两条腿」（机器 / 人工）而不是「四足」——而人工那两条腿本来就由同一次提交写入（决策 3），合并反而更贴合。
- **AC-04 / AC-12 仍结构性成立**：机器列与人工列各由一个方法独占，`areplace_snapshot` 的 UPDATE 列表根本不提 `column_overrides`。
- 与决策 3 **同构**：API 层按 `model_fields_set` 判「有没有提交」，DAO 层按**哨兵**判「有没有传」，同一条规则在两层各把一次关，谁也不会撤销对方的谨慎。
- 人工列仍只有一个写入者（就是这一个方法）。

**实现约束（改这条决策时必须一起看）**：`None` 在人工列上是**合法值**（表示清空，决策 5），所以默认值**不能**用 `None`，必须用哨兵 —— 否则「只保存业务描述」会顺手把字段订正全清空。见 §5 坑 8。

**何时该重新考虑**：若人工列增至三个以上，或某列需要独立于其它列的写入时序（例如订正要能单独保存、不带上业务描述），「一个方法 + 哨兵」会开始别扭；届时正确做法是**回头谈 C1 的措辞**（修订宪法），而不是再悄悄加一个 DAO 方法。

### 决策 3：API 形态（一次提交 vs 两个端点）

- **备选 A**：新增一个专写 `column_overrides` 的端点。
  → **否决**：弹窗里改完表级描述与若干字段说明要点两次保存，中途失败会留下半套改动；需求方明确要「一次提交」。
- **选定 B**：扩展既有 `PUT /api/v1/datasource/table-metadata/{table_id}` 的 payload，用 **`model_fields_set` 判断字段是否被显式提交**，只写提交了的那些列。

**原因**：旧调用方（只发 `business_desc`）行为**完全不变**——这是向后兼容的关键：若改成「没传就写 None」，既有前端每次保存字段说明都会把业务描述清空。

### 决策 4：注入取值与召回

- `render_table`：字段说明取 `column_overrides[字段名]`（非空）优先，否则 `comment`。
- `_haystack`：同法取值后再拼字符串。

**原因**：两处用的是同一份结构快照，取值口径分叉会出现「模型看到的说明」与「召回依据的文本」不一致，而这类不一致只在问数答错时才会被发现。

**消失字段天然被排除**：两处都按 `structure_snapshot` 遍历，`column_overrides` 里那些已不在快照中的键不会被读到 → 满足 AC-05「保留但不注入」，无需显式过滤。

### 决策 5：空串 = 撤销订正

在 `schema_service` 写入侧做归一化：`strip()` 后丢弃空值键，DB 中不出现 `{"col": ""}`。

**原因**：`render_table` 用真值判断（`if comment`），存成空串与存成不存在在**注入侧**表现一致，但在**界面侧**必须区分——空串会让人以为「订正过、订正为空」，而 AC-09 要求它显示为「未订正、回落库内注释」。归一化在最靠写入的那一层做，界面就不会看到这种歧义状态。

### 决策 6：界面落点

扩展现有 `TableDescriptionDialog`（表级 textarea 保持在上），下方新增字段列表；每行 = 字段名 + 库内注释原值（**只读**）+ 人工说明输入框。

**原因**：需求方已确认「扩展现有弹窗，一次提交」；同时库内原值必须**在同一屏只读可见**，否则管理员无法判断「我该订正成什么」（AC-08）。

---

## 4. 系统现状（接手必读）

### 4.1 数据流

```
                    ┌── 管理页读：GET /{id}/tables
                    │     schema_service.list_tables_for_admin()   ← 构造 dict ①
采集:  refresh_tables ┤
      areplace_snapshot            ┌── 注入读：build_datasource_tools()
      (structure_snapshot          │     tool.py:250 列表推导         ← 构造 dict ②
       + table_comment)            │            ↓
                                   │     query_service.answer_question(tables)
                                   │            ↓
                                   │     schema_prompt.build_schema_block()
                                   │       ├─ render_table()  → 提示词
                                   │       └─ _haystack()     → 召回匹配
写订正: PUT table-metadata/{id} ───┘
        → aupdate_column_overrides
```

**两条读路径各自构造 table dict，是本特性最容易被漏掉的地方**（见 §5 第 1 条）。

### 4.2 关键字段约定

| 字段 | 归属 | 谁写 | 语义 |
|---|---|---|---|
| `structure_snapshot` | 机器 | `refresh_tables`（`areplace_snapshot`） | `[{"name","type","comment"}]`；`comment` 缺失 = 该引擎采集不到注释（F061 AC-14），空串 = 有该能力但此列无注释 |
| `table_comment` | 机器 | 同上 | 库内表注释 |
| `business_desc` | 人工 | `update_description` | 表级业务描述 |
| **`column_overrides`** | **人工** | **`update_column_overrides`（本特性）** | **`{"字段名": 人工说明}`；键为字段名而非下标，与快照顺序无关** |

### 4.3 关键模块职责

| 文件 | 职责 | 本特性改动 |
|---|---|---|
| `datasource/domain/models/datasource_table.py` | 表模型 | 新增 `column_overrides` 列 |
| `datasource/domain/models/dao.py` | 定向 UPDATE | **扩展** `aupdate_business_desc` → `aupdate_human_fields`（人工列的唯一写入者，按「传了才写」，哨兵默认值）；**不新增 DAO 入口**（C1，见决策 2） |
| `datasource/domain/services/schema_service.py` | 写入编排 + 归一化 | 新增 `update_column_overrides`（含空值归一化）；`list_tables_for_admin` 带回新列 |
| `datasource/domain/services/schema_prompt.py` | 注入 + 召回 | `render_table` / `_haystack` 取值改「订正优先」 |
| `datasource/domain/langchain/tool.py` | 工具构造（dict ②） | dict 里带上 `column_overrides` |
| `datasource/api/endpoints/datasources.py` | API | payload 加字段 + presence-based 写入 |
| `core/database/alembic/versions/v2_7_0_f064_*.py` | DDL | 新增迁移 |
| `platform/src/pages/DatasourcePage/TableDescriptionDialog.tsx` | 订正入口 | 增加字段列表 |
| `platform/src/pages/DatasourcePage/DatasourceTablesView.tsx` | 字段表展示 | 生效值 + 「已订正」标记；有订正则不再显示「未采集到注释」告警 |

---

## 5. 已知坑 / 反直觉事实

1. **注入路径有两处构造 table dict，漏一处会静默半生效。** `schema_service.list_tables_for_admin()`（管理页读）与 `datasource/domain/langchain/tool.py:250`（注入读）**各自**拼 dict。只改前者，界面会显示订正、模型却看不到——且**没有任何报错**，只能通过问数结果异常发现。两处都必须带上 `column_overrides`。
2. **`render_table` 的注释判定是真值判断**（`if comment`），`None` 与 `""` 在注入侧等价；语义区分只存在于界面侧。别指望在注入侧区分「无订正」与「订正为空」。
3. **扩展 PUT 的 payload 必须按 `model_fields_set` 写**，不能按「值为 None 就写 None」。否则旧前端（只发 `business_desc`）的每次保存都会把订正清空，而现有前端**不会**因为这次改动同步升级。
4. **迁移 head 只能问 alembic**（`alembic/AGENTS.md` §1）。本次实测：手写脚本解析 `revision` / `down_revision` 得出 **17 个假 head**（部分文件用括号换行的多行写法，正则抓不到父引用），而 `uv run alembic heads` 给出唯一真 head `f063_term_standard_term_unique`。手写解析**不可信**。
5. **前端测试断言 Dialog 内容必须用 `screen`,不能对 `container.innerHTML` 断言**：Radix Dialog 走 portal，`container` 里恒为空，任何「不出现 X」的断言都会假绿。
6. **订正文本会进入注入预算**：字段说明越长，越可能触发既有的 retrieval 降级。这是既有机制（F061 schema 预算）覆盖的，本特性不新增保护，但要知道「订正写太长会把 schema 挤进 retrieval 模式」。
7. **`column_overrides` 的键是字段名**。字段改名 = 旧键成为「已不在库中」的悬挂键（AC-05 保留），新名需重新订正；这是有意的（改名后旧说明描述的是另一个字段，不能自动迁移）。
8. **人工列上 `None` 是合法值（= 清空），所以写入方法的默认值必须是哨兵，不能是 `None`。** `aupdate_human_fields` 用 `_UNSET` 区分「没传这个字段」与「传了 None」 | 把哨兵换回 `None` 默认值（看起来更简洁）后，**只保存业务描述的那一次请求会把全表的字段订正清空** —— 而且不报错、界面刷新后才看得出来。反过来，把「没传」当成「写 NULL」也会让 API 层辛苦分辨的 `model_fields_set` 在下一层被抹平 | `dao.py` 的 `_UnsetType` / `_UNSET`；决策 2 的「实现约束」；`test_dao.py` 里 `test_an_empty_call_writes_nothing_at_all` 与 `test_explicit_none_clears_rather_than_being_treated_as_unset` 是一对，**只有哨兵正确时这两条才会同时通过** |

---

## 6. 对外契约与依赖

### 6.1 我提供给别人的（Outgoing）

| 契约 | 变更 | 兼容性 |
|---|---|---|
| `PUT /api/v1/datasource/table-metadata/{table_id}` | 请求体新增**可选** `column_overrides: dict[str,str] \| null` | **向后兼容**：不提交该键即不写该列 |
| `GET /api/v1/datasource/{id}/tables` | 响应每行新增 `column_overrides`。**存储形态是 `NULL`，响应里恒为对象**（无订正 ⇒ `{}`） | 纯新增字段，旧前端忽略即可。`{}`/`NULL` 的差异**不出现在对外契约上**：调用方只需处理对象形态，不必判空 |
| 注入给模型的 schema 文本 | 字段说明可能来自人工订正 | 文本形态不变（仍是 `-- 说明` 后缀） |

### 6.2 我依赖别人的（Incoming）

| 依赖 | 用途 | 是否修改 |
|---|---|---|
| F061 `schema_service` / `schema_prompt` / `DbDatasourceTableDao` | 采集、注入、召回、写入链路 | 只扩展，不改既有行为 |
| F061 错误码模块 117 | 写入失败的既有信封 | 不扩展 |
| `JsonType`（`core/database/dialect_helpers`） | 双 DB 的 JSON 列 | 复用 |

---

## 7. 测试与可观测

| 层 | 覆盖 | 说明 |
|---|---|---|
| `test/datasource/test_schema_prompt.py` | AC-06 / AC-07 | 订正优先于库内注释；`_haystack` 含订正文本（用「只有订正词能命中」的用例，避免断言恒真） |
| `test/datasource/test_schema_service.py` | AC-04 / AC-09 | 重采集后订正仍在；空串 → 键被丢弃 |
| `test/datasource/test_datasources_api.py` | AC-01 / 兼容性 | 只发 `business_desc` 时订正不变（回归 AC-12 的兼容面）；同时提交两者时都生效 |
| `platform/src/test/tableDescriptionDialog.test.tsx` | AC-01 / AC-08 / AC-09 | 提交载荷含两段内容；标注与只读原值 |
| `platform/src/test/datasourceTablesView*.test.tsx` | AC-08 / AC-10 | 生效值 + 「已订正」；有订正时无「未采集到注释」告警 |
| `test/e2e/test_e2e_f064_field_override.py` | AC-02 / AC-04 / AC-06 | 端到端：写订正 → 重采集 → 注入文本仍为订正；并**断言客户库侧的注释未被改写**（若测试库可用） |

---

## 8. 后续改进 / 不打算做的事

- **不做订正的版本历史 / 审计**：表级 `business_desc` 今天也没有，两者口径保持一致；真要做应表级字段级一起做。
- **不做字段改名时的订正迁移**：见 §5 第 7 条。
- **不做批量导入/导出订正文本**（spec 已排除）。
- **不做长度上限**：与表级业务描述同口径；若日后要加，表级字段级应同时加。

---

## 修订历史

| 日期 | 变更 |
|---|---|
| 2026-09-23 | 首版：随 spec 一并落定 6 项决策与 7 条已知坑 |
| 2026-09-23 | **决策 2 推翻重写（原选定 B → 选定 C）：写入路径由「新增 `aupdate_column_overrides`、三足鼎立」改为「扩展 `aupdate_business_desc` → `aupdate_human_fields`、机器/人工两条腿」。** 触发：Wave A 的 `/task-review T-03` 抓到 **C1 违规（HIGH）** —— `docs/constitution.md:30` 明文「never add new DAO entry points for new features」，而 `datasource` 模块由 F061 建立，本特性是给既有模块加口子（F062 坑 14、F063 设计改写是同一处红线的两次先例）。**且无路可退**：没有能承载该列的既有入口（`areplace_snapshot` 写它就直接破 AC-04），C1 前半句又禁止在 Service 里写 ORM。**决策变更经用户确认（★ 决策的推翻必须重新确认）**。同步：§2 新增 C1 约束行、§4.3 的 `dao.py` 行改写、**新增坑 8**（`None` 是合法值 ⇒ 默认值必须是哨兵）。**决策 1 / 3 / 4 / 5 / 6 不变**；AC-04 仍是结构性的（机器列与人工列各由一个方法独占） |
