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


def test_a_database_answer_still_goes_through_the_output_guard():
    """AC-63: adding a datasource must not create a path that bypasses the
    compliance guard. A datasource is an ordinary assistant tool (the agent
    wraps every tool in `wrap_citation_tool`), so its rows reach the user inside
    the `result[-1].content` that `handle_gpts_message` binds to `answer` after
    `gpts_agent.run` -- the same answer every other assistant produces, and so
    the same guard it has to pass on the way out.

    Scoped to THAT method, not the module: an `output_guard` mention anywhere
    in `common/chat/client.py` stays green if the call moves off the answer
    path, which is exactly the regression AC-63 forbids. The ordering checks
    pin both halves of the contract -- the guard runs BEFORE the answer is
    persisted and sent, and the binding those calls read is the one the guard
    wrote into (not the raw model output).

    This is an EXISTENCE anchor, not behavioural verification: the real case
    (an answer containing a sensitive word comes back truncated or masked)
    needs a live backend with a configured policy, and belongs to /e2e-test.
    """
    import ast
    import inspect
    import textwrap

    from bisheng.common.chat import client as chat_client

    method = chat_client.ChatClient.handle_gpts_message
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))

    def _is_guard_call(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'output_guard'
        )

    guard_calls = [node for node in ast.walk(tree) if _is_guard_call(node)]
    assert guard_calls, (
        'the output guard call left handle_gpts_message -- a database answer '
        'would no longer be checked (AC-63)'
    )
    guard_line = min(node.lineno for node in guard_calls)

    # ...and it is fed the answer, not some other string. A guard called on
    # something else is a guard that runs and decides nothing.
    guarded_args = [
        arg for node in guard_calls for arg in list(node.args) + [kw.value for kw in node.keywords]
    ]
    assert any(isinstance(arg, ast.Name) and arg.id == 'answer' for arg in guarded_args), (
        'the guard is fed something other than `answer` -- what is sent to the '
        'user is not what was checked (AC-63)'
    )

    # Where `answer` leaves the method: the bot message row and the answer frame
    # sent to the client. Both are searched for rather than hard-coded, so
    # reformatting or a positional/kwarg change does not silently turn this
    # anchor into a no-op.
    answer_uses = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('add_message', 'send_response'):
            continue
        passes_answer = [
            arg for arg in node.args if isinstance(arg, ast.Name) and arg.id == 'answer'
        ] + [kw.value for kw in node.keywords if isinstance(kw.value, ast.Name) and kw.value.id == 'answer']
        if passes_answer:
            answer_uses.append((node.func.attr, node.lineno))

    # Guard the guard: if the answer frame stops carrying `answer`, the check
    # below would still hold over the persistence call alone and say nothing
    # about what the user actually sees.
    assert 'send_response' in {name for name, _ in answer_uses}, (
        'no answer frame carries `answer` any more -- re-check what AC-63 anchors'
    )
    first_use = min(lineno for _, lineno in answer_uses)
    assert first_use > guard_line, (
        f'the answer is used at line {first_use} but the guard only runs at line '
        f'{guard_line}: unguarded text would reach the user (AC-63)'
    )

    # ...and what those calls read is the guarded text: the LAST write to
    # `answer` must take its value from the guard, directly or through a name
    # the guard was assigned into. Matching by data flow rather than by line
    # order matters -- `answer = result[-1].content` placed AFTER the guard call
    # satisfies every ordering check while sending the raw model output.
    guarded_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(_is_guard_call(sub) for sub in ast.walk(node.value)):
            guarded_names |= {target.id for target in node.targets if isinstance(target, ast.Name)}

    answer_writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'answer' for target in node.targets)
    ]
    assert answer_writes, 'handle_gpts_message no longer binds `answer`'
    last_write = max(answer_writes, key=lambda node: node.lineno)
    reads_the_guard = any(_is_guard_call(sub) for sub in ast.walk(last_write.value)) or any(
        isinstance(sub, ast.Name) and sub.id in guarded_names for sub in ast.walk(last_write.value)
    )
    assert reads_the_guard, (
        'the last write to `answer` does not read the guard result -- the guard '
        'runs and its output is discarded, so the raw model output is persisted '
        'and sent (AC-63)'
    )
