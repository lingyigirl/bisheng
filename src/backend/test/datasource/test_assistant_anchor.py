"""F061 T-31: a database answer carries an anchor the UI can expand (AC-29/31/34).

The anchor is built from the SAME executions the model saw, taken off the tool's
accumulated outcomes rather than re-derived -- re-deriving would eventually
disagree with what ran.
"""

from types import SimpleNamespace

from bisheng.api.services.assistant_agent import AssistantCitationToolWrapper
from bisheng.citation.domain.schemas.citation_schema import CitationType
from bisheng.datasource.domain.langchain.tool import DatasourceQueryTool
from bisheng.datasource.domain.services.executor import QueryOutcome


def _tool_with_outcomes(*statements):
    tool = DatasourceQueryTool(
        name='datasource_7',
        description='在业务库中查询',
        datasource=SimpleNamespace(id=7, name='客户订单库', tenant_id=1, engine='mysql'),
        tables=[],
        user_id=3,
    )
    for statement in statements or ('SELECT id FROM orders',):
        tool.outcomes.append(
            QueryOutcome(statement=statement, columns=['id'], rows=[[1]], row_count=1)
        )
    return tool


def test_wrapping_a_datasource_tool_produces_a_datasource_item():
    # CitationRegistryCollector lives in citation_prompt_helper, NOT in schemas.
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    wrapped = AssistantCitationToolWrapper.wrap(_tool_with_outcomes(), CitationRegistryCollector())

    items = wrapped.datasource_citation_items()

    assert len(items) == 1
    item = items[0]
    assert item.type == CitationType.DATASOURCE
    # Attribute access, not subscript: `CitationSourcePayload` is a validating
    # union, so what comes back is a schema instance (same as
    # test_citation_security.py pins for the resolve path).
    payload = item.sourcePayload
    assert payload.datasourceId == 7
    assert payload.datasourceName == '客户订单库'
    assert payload.statement == 'SELECT id FROM orders'
    assert payload.rows == [[1]]
    assert payload.tables == ['orders']


def test_each_execution_gets_its_own_anchor():
    """A turn that queried twice must produce two anchors, not one."""
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    wrapped = AssistantCitationToolWrapper.wrap(
        _tool_with_outcomes('SELECT 1 FROM orders', 'SELECT 2 FROM customers'),
        CitationRegistryCollector(),
    )

    items = wrapped.datasource_citation_items()

    assert [item.sourcePayload.statement for item in items] == [
        'SELECT 1 FROM orders',
        'SELECT 2 FROM customers',
    ]
    # Distinct ids, or two anchors collide in the registry.
    assert len({item.citationId for item in items}) == 2


def test_a_non_datasource_tool_produces_no_datasource_items():
    """The wrapper is applied to every tool; only datasource tools yield anchors."""
    from langchain_core.tools import tool

    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    @tool
    def some_other_tool(query: str) -> str:
        """An unrelated tool."""
        return 'ok'

    wrapped = AssistantCitationToolWrapper.wrap(some_other_tool, CitationRegistryCollector())

    assert wrapped.datasource_citation_items() == []


def test_the_agent_wraps_and_collects_a_datasource_tool():
    """The other half of the bridge: a datasource tool the agent never wraps
    produces no anchor, however correct `datasource_citation_items` is.

    `__new__` rather than the constructor: `init_assistant` needs a live
    assistant and an LLM, and neither is consulted by the two methods here.
    """
    from bisheng.api.services.assistant_agent import AssistantAgent
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.citation_registry_collector = CitationRegistryCollector()

    wrapped = agent.wrap_citation_tool(_tool_with_outcomes())
    assert isinstance(wrapped, AssistantCitationToolWrapper)

    agent.tools = [wrapped]
    assert [item.citationId for item in agent.collect_citation_registry_items()] == [
        'datasource_7_0'
    ]


def test_datasource_anchors_survive_the_text_marker_filter():
    """The tool renders a plain markdown table and emits no citation marker, so
    a turn that ALSO cited a document -- and therefore has real markers -- would
    drop the datasource anchor. AC-31/34 require it to persist."""
    from bisheng.citation.domain.schemas.citation_schema import (
        CitationRegistryItemSchema,
        CitationType,
    )
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CITATION_END_MARKER,
        CITATION_START_MARKER,
        extract_citation_ids_from_text,
        select_registry_items_for_persistence,
    )

    def _marker(citation_id: str) -> str:
        return f'{CITATION_START_MARKER}{citation_id}:1{CITATION_END_MARKER}'

    rag = CitationRegistryItemSchema(
        citationId='rag1', type=CitationType.RAG, sourcePayload={'fileId': 1}
    )
    datasource = CitationRegistryItemSchema(
        citationId='ds1',
        type=CitationType.DATASOURCE,
        sourcePayload={'datasourceId': 7, 'statement': 'SELECT 1'},
    )
    answer = f'按文档回答{_marker("rag1")}'

    # Guard the guard: a malformed marker makes extract_citation_ids_from_text
    # return empty, the function returns EVERYTHING, and this test would pass
    # without the exemption existing.
    assert extract_citation_ids_from_text(answer) == {'rag1'}

    kept = select_registry_items_for_persistence([rag, datasource], answer)

    assert datasource in kept, 'the datasource anchor was dropped by the marker filter'
    assert rag in kept


def test_unreferenced_rag_items_are_still_filtered():
    """The exemption is for datasources only -- do not loosen RAG behaviour."""
    from bisheng.citation.domain.schemas.citation_schema import (
        CitationRegistryItemSchema,
        CitationType,
    )
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CITATION_END_MARKER,
        CITATION_START_MARKER,
        select_registry_items_for_persistence,
    )

    cited = CitationRegistryItemSchema(
        citationId='rag1', type=CitationType.RAG, sourcePayload={'fileId': 1}
    )
    uncited = CitationRegistryItemSchema(
        citationId='rag2', type=CitationType.RAG, sourcePayload={'fileId': 2}
    )
    answer = f'{CITATION_START_MARKER}rag1:1{CITATION_END_MARKER}'

    kept = select_registry_items_for_persistence([cited, uncited], answer)

    assert kept == [cited]


def test_a_second_turn_does_not_recite_the_first_turns_query():
    """The agent is REUSED across turns in a chat, so anything accumulated on a
    tool survives into the next answer unless the per-run reset clears it. The
    datasource exemption makes that visible: turn 2 would otherwise list turn
    1's query as a source it never ran."""
    from bisheng.citation.domain.services.citation_prompt_helper import (
        CitationRegistryCollector,
    )

    from bisheng.api.services.assistant_agent import AssistantAgent

    agent = AssistantAgent.__new__(AssistantAgent)
    agent.citation_registry_collector = CitationRegistryCollector()
    agent.tools = [AssistantCitationToolWrapper.wrap(_tool_with_outcomes('SELECT 1 FROM t1'), agent.citation_registry_collector)]

    # Turn 1: the model queried once.
    assert len(agent.collect_citation_registry_items()) == 1

    # Turn boundary.
    agent.reset_citation_registry_items()

    # Turn 2: no query this turn, so there is nothing to cite.
    assert agent.collect_citation_registry_items() == [], (
        'turn 1 anchors leaked into turn 2 -- reset did not clear the tool outcomes'
    )
