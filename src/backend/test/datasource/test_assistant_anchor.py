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
