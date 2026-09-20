"""F061 T-33: what an answer must and must not carry about its database sources.

The mechanism decision matters for reading this file. AC-29 ("明示数据来源") and
AC-30 ("区分标注各自的出处") are satisfied by the DATASOURCE ANCHOR, not by asking
the model to write the datasource's name in prose. That is not a shortcut: the
citation prompt explicitly forbids naming a source in natural language
(`citation.yaml`, Prohibited Behavior §4 — "不得把来源标识改写为自然语言来源名称"), so a
prose requirement would contradict the rules every assistant already runs under.
The anchor carries the datasource, the tables and the statement, and T-41 renders
it under the answer.

Two of these assertions are the kind that go unnoticed when missing:

* **AC-36** is a negative — a turn that queried nothing must contribute no
  database section at all, not an empty one. Nothing about "we rendered nothing"
  fails loudly if it regresses into "we rendered an empty box".
* **AC-30** is about coexistence — the datasource anchor must survive an answer
  that ALSO cited documents. The persistence filter drops unreferenced items
  whenever the answer contains citation markers, and the datasource tool emits
  none, so without the exemption a mixed answer would silently lose its anchor.
"""

# ruff: noqa: RUF001 -- the Chinese test data mirrors user-facing text;
# fullwidth punctuation is correct in it.

from types import SimpleNamespace

from bisheng.api.services.assistant_agent import AssistantCitationToolWrapper
from bisheng.citation.domain.schemas.citation_schema import CitationRegistryItemSchema, CitationType
from bisheng.citation.domain.services.citation_prompt_helper import (
    CITATION_END_MARKER,
    CITATION_START_MARKER,
    CitationRegistryCollector,
    extract_citation_ids_from_text,
    select_registry_items_for_persistence,
)
from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.services.executor import QueryOutcome, tables_from_statement


def _tool(*, name='客户订单库', ds_id=7):
    return DatasourceQueryTool(
        name=f'datasource_{ds_id}',
        description='在业务库中查询',
        datasource=SimpleNamespace(id=ds_id, name=name, tenant_id=1, engine='mysql'),
        tables=[],
        user_id=3,
    )


def _outcome(statement='SELECT id, amount FROM orders JOIN customers ON 1 = 1', rows=((1, 10),)):
    return QueryOutcome(
        statement=statement,
        columns=['id', 'amount'],
        rows=[list(row) for row in rows],
        row_count=len(rows),
    )


def _wrapped(tool):
    return AssistantCitationToolWrapper.wrap(tool, CitationRegistryCollector())


def _rag_item(citation_id='rag1'):
    """A document item.

    `citationId` is the GROUP id (`rag1`), while the marker in the answer carries
    `rag1:1` — `_split_citation_key` strips the `:item` suffix before comparing.
    Setting both to the same string is the mistake that silently disables the
    filter this file tests.
    """
    return CitationRegistryItemSchema(
        citationId=citation_id,
        type=CitationType.RAG,
        sourcePayload={'chunk_id': f'{citation_id}:1'},
    )


# ── AC-29: the answer identifies which datasource and which tables ────────────


def test_the_anchor_names_the_datasource_and_its_tables():
    """AC-29's two required facts: 来自哪个数据源 / 哪些表."""
    tool = _tool(name='客户订单库')
    tool.outcomes.append(_outcome('SELECT id FROM orders JOIN customers ON 1 = 1'))

    items = _wrapped(tool).datasource_citation_items()

    assert len(items) == 1
    payload = items[0].sourcePayload
    assert payload.datasourceName == '客户订单库'
    assert payload.datasourceId == 7
    assert payload.tables == ['orders', 'customers'], 'the tables that were read must be part of the anchor'


def test_the_anchor_carries_the_statement_for_review():
    """AC-31's `sql`: the user must be able to check what actually ran."""
    tool = _tool()
    tool.outcomes.append(_outcome("SELECT id FROM orders WHERE status = 'paid'"))

    payload = _wrapped(tool).datasource_citation_items()[0].sourcePayload

    assert "status = 'paid'" in payload.statement


def test_a_query_without_a_from_clause_reports_no_tables_rather_than_inventing_them():
    """`SELECT 1` is a legitimate probe and touches no table. An empty list is
    the honest answer; a guessed name would put a wrong table in front of the
    user, which is worse than showing none."""
    assert tables_from_statement('SELECT 1') == []


# ── AC-30: documents and a database in ONE answer, both labelled ─────────────


def test_a_datasource_anchor_survives_an_answer_that_also_cited_a_document():
    """The mixed answer is the case the exemption exists for.

    `select_registry_items_for_persistence` drops unreferenced items once the
    answer contains any citation marker. The datasource tool renders a markdown
    table and emits no marker, so a documented answer WOULD have dropped the
    anchor — the user would see citations for the documents and nothing at all
    for the query that ran.
    """
    tool = _tool()
    tool.outcomes.append(_outcome())
    datasource_items = _wrapped(tool).datasource_citation_items()
    rag_item = _rag_item("rag1")

    # The answer cited the document and said nothing marker-shaped about the DB.
    # The markers are the real private-use characters, built from the constants:
    # spelled `⟦…⟧` this test passes without ever reaching the exemption, because
    # `extract_citation_ids_from_text` finds nothing and the function returns
    # early — which is why it was mutation-checked.
    answer = (
        f'根据文档{CITATION_START_MARKER}rag1:1{CITATION_END_MARKER}'
        '以及查询结果，本季度订单为 10 笔。'
    )
    assert extract_citation_ids_from_text(answer) == {'rag1'}, (
        'the answer must actually carry a citation marker, or the filter this '
        'test is about never runs'
    )

    kept = select_registry_items_for_persistence([rag_item, *datasource_items], answer)

    assert rag_item in kept
    assert datasource_items[0] in kept, (
        'the datasource anchor was dropped from a mixed answer -- the user would '
        'see where the documents came from but not where the numbers did'
    )


def test_both_kinds_keep_a_distinct_type_so_the_ui_can_label_them_differently():
    """AC-30's "区分标注" needs the two to be distinguishable downstream; a
    datasource item typed as RAG would be rendered as a document."""
    tool = _tool()
    tool.outcomes.append(_outcome())

    datasource_items = _wrapped(tool).datasource_citation_items()
    rag_item = _rag_item()

    assert datasource_items[0].type == CitationType.DATASOURCE
    assert rag_item.type == CitationType.RAG
    assert datasource_items[0].type != rag_item.type


def test_a_turn_that_queried_twice_offers_both_as_sources():
    """AC-30-adjacent: two queries in one answer must not collapse to the last."""
    tool = _tool()
    tool.outcomes.append(_outcome('SELECT id FROM orders'))
    tool.outcomes.append(_outcome('SELECT id FROM customers'))

    items = _wrapped(tool).datasource_citation_items()

    assert len(items) == 2
    assert [i.sourcePayload.tables[0] for i in items] == ['orders', 'customers']


# ── AC-36: nothing to show means show nothing ────────────────────────────────


def test_a_turn_that_queried_nothing_produces_no_datasource_section():
    """The negative constraint. `[]` is what the renderer branches on — there is
    no "empty database area" state for it to draw, because an empty section reads
    as "a database was consulted and returned nothing"."""
    tool = _tool()

    assert _wrapped(tool).datasource_citation_items() == []


def test_a_failed_query_produces_no_datasource_section_either():
    """A failure leaves no outcome (see `test_tool_outcomes`), so there is no
    anchor to render — and AC-39's failure sentence is the answer's whole story
    for that source. An empty result table next to "查询失败" would be noise."""
    tool = _tool()
    tool.last_error_category = 'unknown_table'

    assert _wrapped(tool).datasource_citation_items() == []


def test_a_successful_query_returning_zero_rows_still_produces_a_section():
    """The complement, and the reason AC-36 is worded "未查询" rather than "无结果":
    a query that ran and matched nothing IS a database source, and the user is
    entitled to see the statement that returned nothing."""
    tool = _tool()
    tool.outcomes.append(_outcome('SELECT id FROM orders WHERE 1 = 0', rows=()))

    items = _wrapped(tool).datasource_citation_items()

    assert len(items) == 1
    assert items[0].sourcePayload.rows == []
