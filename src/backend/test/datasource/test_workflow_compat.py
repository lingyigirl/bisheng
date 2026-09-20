"""F061 T-42: the workflow Agent node's compatibility contract (AC-54/55/56).

Scope note: this file covers the BACKEND half of T-42. AC-53 (the flow editor
offers "pick an existing datasource" instead of inline connection fields) is an
editor-UI change and is not in this change.

The two ACs here are both of a kind that is easy to leave untested because
"nothing changed" feels self-evident:

* **AC-54** — a flow that still carries an inline `sql_agent` block keeps
  working. Per R5 the acceptance is the node's OBSERVABLE behaviour, not whether
  the same code path is reused: `_init_sql_address` was rewritten to delegate to
  `engine_registry`, and that is fine as long as the address a flow gets is the
  one it got before. (`test_engine_registry.py` pins the emitted URIs
  byte-for-byte; this file pins the node-level contract around them.)
* **AC-55** — no new datasource-retrieval node type was introduced. This is a
  "we did NOT do something" claim, so no behavioural test can prove it. It is
  asserted by scanning the node-type enum, which is the thing that would have to
  grow for the claim to break.

AC-56 ("no NEW connections through the inline entry") is a UI boundary —
`SqlConfigItem` is where an inline connection is authored — so its structural
half is asserted here as "the node still only reads what it is given", and the
editor half belongs with AC-53's change.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bisheng.workflow.common.node import NodeType

# ── AC-55: no new node type ──────────────────────────────────────────────────


def test_no_datasource_retrieval_node_type_was_added():
    """AC-55 forbids a standalone datasource-retrieval workflow node.

    A datasource reaches a workflow through the EXISTING agent node's tool list
    (that is design decision 3), so a new node type would be the single clearest
    sign the constraint had been broken. Scanning the enum is the only way to
    assert a negative like this — a behaviour test cannot prove a node type does
    not exist.
    """
    names = {member.name for member in NodeType}
    values = {member.value for member in NodeType}

    offending = {
        member
        for member in names | values
        if 'datasource' in member.lower() or 'db_source' in member.lower()
    }
    assert not offending, (
        f'AC-55 forbids a datasource node type; found {sorted(offending)}. '
        'A datasource is meant to reach a flow as a tool on the agent node.'
    )


def test_the_agent_node_type_still_exists_and_is_unchanged():
    """The other half of AC-55: the constraint says "no NEW node type", not
    "remove the old one". `agent` is the node the datasource rides on."""
    assert NodeType.AGENT.value == 'agent'


# ── AC-54: an inline-configured flow keeps working ───────────────────────────


def _agent_node_with_inline_sql():
    """An `AgentNode` instance carrying the legacy inline `sql_agent` block.

    Built with `__new__` rather than the constructor: `AgentNode.__init__` loads
    tools, an LLM and a knowledge retriever, none of which this test is about,
    and all of which would need stubbing. What is under test is the inline
    parameter handling, which is attribute-level.
    """
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = AgentNode.__new__(AgentNode)
    node.node_params = {
        'sql_agent': {
            'open': True,
            'database_engine': 'mysql',
            'db_address': '10.0.0.7:3306',
            'db_name': 'orders',
            'db_username': 'app',
            'db_password': 'pw',
        }
    }
    return node


def test_a_legacy_inline_block_still_produces_its_address():
    """AC-54's core: the legacy shape parses and yields a usable address."""
    node = _agent_node_with_inline_sql()

    node._sql_agent_params = node.node_params.get('sql_agent', None)
    from bisheng.workflow.nodes.agent.agent import SqlAgentParams

    node._sql_agent = SqlAgentParams.model_validate(node.node_params['sql_agent'])
    address = node._init_sql_address()

    # Byte-identical to the pre-delegation implementation for this shape; the
    # full six-engine matrix lives in test_engine_registry.py.
    assert address == 'mysql+pymysql://app:pw@10.0.0.7:3306/orders?charset=utf8mb4'


def test_the_legacy_engine_spellings_still_validate():
    """A saved flow holds whatever spelling was valid when it was authored.
    `postgres` (not `postgresql`) is the one that appears in older flows."""
    from bisheng.workflow.nodes.agent.agent import SqlAgentParams

    for engine in ['mysql', 'db2', 'postgres', 'postgresql', 'gaussdb', 'oracle']:
        params = SqlAgentParams.model_validate(
            {
                'database_engine': engine,
                'db_address': 'h:1',
                'db_name': 'd',
                'db_username': 'u',
                'db_password': 'p',
                'open': True,
            }
        )
        assert params.database_engine == engine


def test_a_closed_inline_block_produces_no_address():
    """`open: false` means the flow configured the block and turned it off. It
    must stay off — producing an address would add a database to a flow that
    deliberately has none."""
    from bisheng.workflow.nodes.agent.agent import AgentNode, SqlAgentParams

    node = AgentNode.__new__(AgentNode)
    node.node_params = {'sql_agent': {'open': False}}
    node._sql_agent_params = node.node_params.get('sql_agent', None)
    node._sql_agent = (
        SqlAgentParams.model_validate(node.node_params['sql_agent'])
        if (node._sql_agent_params and node._sql_agent_params.get('open', False))
        else None
    )

    assert node._sql_agent is None
    assert node._init_sql_address() == ''


def test_a_flow_with_no_sql_block_is_unaffected():
    """Every flow that never used sql_agent. This is the common case and it must
    not start behaving differently (AC-54 read together with AC-60)."""
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = AgentNode.__new__(AgentNode)
    node.node_params = {}
    node._sql_agent_params = node.node_params.get('sql_agent', None)
    node._sql_agent = None

    assert node._init_sql_address() == ''


def test_an_unsupported_engine_raises_the_branchable_error():
    """The delegation changed this case from a bare `ValueError` to error code
    11702. Documented in the method's own docstring as an intentional fix: a
    ValueError is not branchable, so the failure was indistinguishable from any
    other crash."""
    from bisheng.common.errcode.datasource import DatasourceUnsupportedEngineError
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = AgentNode.__new__(AgentNode)
    node.node_params = {
        'sql_agent': {
            'open': True,
            'database_engine': 'mysql',
            'db_address': 'h:1',
            'db_name': 'd',
            'db_username': 'u',
            'db_password': 'p',
        }
    }
    node._sql_agent = SimpleNamespace(
        database_engine='sqlite',  # not in SUPPORTED_ENGINES
        db_address='h:1',
        db_name='d',
        db_username='u',
        db_password='p',
    )

    with pytest.raises(DatasourceUnsupportedEngineError):
        node._init_sql_address()


def test_the_sql_tool_is_only_built_when_an_address_exists():
    """The agent node's tool list gate. A node with no address must contribute
    no sql tool — otherwise a flow with the block switched off would grow a
    database tool."""
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = AgentNode.__new__(AgentNode)
    node._sql_address = ''

    assert node.init_sql_agent_tool() == []


def test_the_legacy_tool_wrapper_is_still_what_loads():
    """`init_sql_agent_tool` goes through `load_tools`, and that mapping is what
    AC-54's "keeps working" ultimately rests on."""
    from bisheng.workflow.nodes.agent.agent import AgentNode

    node = AgentNode.__new__(AgentNode)
    node._sql_address = 'mysql+pymysql://u:p@h:3306/d'
    node._llm = object()

    with patch('bisheng.workflow.nodes.agent.agent.load_tools') as load_tools:
        load_tools.return_value = ['sql-tool']
        tools = node.init_sql_agent_tool()

    assert tools == ['sql-tool']
    _, kwargs = load_tools.call_args
    assert kwargs['tool_params']['sql_agent']['sql_address'] == node._sql_address
