# Tasks: 数据源字段说明人工订正（064-datasource-field-comment-override）

> 执行流水（append-only）。每项绑定验证标准：`[步骤] → 验证：[检查项]`。
> 需求口径见 [spec.md](./spec.md)，设计真相见 [design.md](./design.md)。
> 开发基线：项目交付集成基线分支（特性间线性叠加，非各自独立分支）。

---

## Wave A — 存储与迁移

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-01 | `DbDatasourceTable` 新增 `column_overrides`（`JsonType`，nullable，中文列注释） | — | `python -c "import ..."` 可导入；`pytest test/datasource/test_schema_service.py` 全绿 | ☐ |
| T-02 | Alembic 迁移 `v2_7_0_f064_field_comment_override`，`down_revision = f063_term_standard_term_unique` | — | `uv run alembic heads` 仍**只有一行**；`pytest test/database/test_alembic_single_head.py` 通过 | ☐ |
| T-03 | `DbDatasourceTableDao.aupdate_column_overrides`：定向只写该列 | AC-04 | 单测：调用后 `structure_snapshot` / `business_desc` 不变 | ☐ |

## Wave B — 写入编排与读路径

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-04 | `schema_service.update_column_overrides`：`strip()` + 丢弃空值键（决策 5） | AC-09 | 单测：`{"a":" ","b":"x"}` 落库为 `{"b":"x"}`；全空 → `None` | ☐ |
| T-05 | `schema_service.list_tables_for_admin` 返回 `column_overrides` | AC-08 | 单测：返回字典含该键 | ☐ |
| T-06 | `areplace_snapshot` 的 UPDATE 列表**不含**新列（回归确认，非改动） | AC-04 | 单测：重采集后订正仍在 | ☐ |

## Wave C — 注入与召回

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-07 | `render_table`：字段说明「订正优先、库内兜底」 | AC-06 | 单测：有订正时注入文本用订正；无订正时用库内注释 | ☐ |
| T-08 | `_haystack`：同上取值后参与召回 | AC-07 | 单测：**只有订正文本**含该词时该表仍被检索命中（避免恒真断言） | ☐ |
| T-09 | `tool.py:250` 的 dict 带上 `column_overrides`（design §5 第 1 条） | AC-06 | 单测：构造工具后其 `tables[0]` 含该键 | ☐ |

## Wave D — API

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-10 | `TableDescriptionPayload` 加可选 `column_overrides`；端点按 `model_fields_set` 分别写入 | AC-01 / 兼容性 | API 测试：只发 `business_desc` 时订正不变；两者同发时都生效 | ☐ |
| T-11 | 端点鉴权沿用 `_require_manage`（不新增字段级授权） | AC-11 | API 测试：未授权主体仍被拒（既有用例回归） | ☐ |

## Wave E — 前端

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-12 | `DatasourceTableRow` 加 `column_overrides?`；`updateTableDescriptionApi` 支持一次提交两段内容 | AC-01 | `tsc-strict` 通过；测试断言请求体 | ☐ |
| T-13 | `TableDescriptionDialog` 增加字段列表（字段名 + 库内原值只读 + 人工说明输入） | AC-08 | 组件测试（用 `screen`，不对 Dialog 容器断言） | ☐ |
| T-14 | 详情页字段表显示生效值 + 「已订正」标记；有订正时**不显示**「未采集到注释」告警 | AC-08 / AC-10 | 组件测试：两种状态的断言方向相反（避免恒真） | ☐ |
| T-15 | 弹窗字段列表列出「仅存在于订正」的字段并标「已不在库中」 | AC-05 | 组件测试：该类字段出现且带标记 | ☐ |
| T-16 | 三语文案（zh-Hans / en-US / ja） | i18n CI | `pnpm check-i18n` OK；`eslint` exit 0 | ☐ |

## Wave F — 端到端与交付

| ID | 任务 | 覆盖 AC | 验证标准 | 状态 |
|---|---|---|---|---|
| T-17 | e2e：写订正 → 重采集 → 注入文本仍为订正；客户库注释未被改写 | AC-02 / AC-04 / AC-06 | `pytest test/e2e/test_e2e_f064_field_override.py` 通过 | ☐ |
| T-18 | 回归：F061 数据源既有测试全绿 | — | `pytest test/datasource/` 通过 | ☐ |
| T-19 | 质量门 | — | `arch-guard.sh` exit 0；平台 `pnpm lint` + `typecheck` 通过 | ☐ |

---

## 执行记录

（实现过程中按 Wave 追加：做了什么、踩了什么坑、偏差与处理。）
