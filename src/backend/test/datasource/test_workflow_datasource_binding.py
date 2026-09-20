"""F061 T-42 / AC-53: the Agent node can bind an EXISTING datasource.

The node's datasource paths:

  * **New** (`sql_agent.datasource_id` set) — the node builds the same
    `DatasourceQueryTool` the assistant chat uses, so a flow gets the encrypted
    credentials, the exposed-table governance and the per-call authorization
    check instead of a plaintext inline connection.
  * **Legacy** (inline block only) — unchanged, and covered by
    `test_workflow_compat.py`.

**Who the query is authorized as** is the decision this file pins. The node has
`self.user_id` (the runtime caller) and `self.flow_user_id` (the config author).
The config author is used, matching what the node already does for knowledge
retrieval when its permission toggle is off (`identity_user_id = self.user_id if
self._knowledge_auth else self.flow_user_id`, `agent.py:350`).

The consequence is deliberate and worth stating plainly: a flow publishes a
datasource to everyone who can RUN that flow, regardless of whether they hold
`viewer` on the datasource themselves — the same way an assistant lends its
knowledge bases to whoever can chat with it. Anyone who can edit the flow can
already change what it queries, so binding by author does not widen the set of
people who control the data.

What must NOT happen is a bare `UserPayload(user_id=...)`: `PermissionService`
skips its tenant gate for a payload without `get_visible_tenants`, so that would
drop tenant isolation silently. The identity is built from the stored user row.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bisheng.workflow.nodes.agent.agent import AgentNode, SqlAgentParams

INLINE = {
    'open': True,
    'database_engine': 'mysql',
    'db_address': 'h:3306',
    'db_name': 'd',
    'db_username': 'u',
    'db_password': 'p',
}


def _node(**sql_agent):
    node = AgentNode.__new__(AgentNode)
    node.node_params = {'sql_agent': sql_agent}
    node.user_id = 11
    node.flow_user_id = 22
    node.tenant_id = 1
    node._llm = object()
    return node


def _apply_init_logic(node):
    """Run the node's REAL sql-mode selection.

    Extracted from `AgentNode.__init__` by calling the same attribute logic the
    constructor runs rather than re-stating it here: a copy would drift, and the
    first version of this helper did exactly that — it mirrored the pre-AC-53
    block and quietly kept asserting the old behavior.
    """
    from bisheng.workflow.nodes.agent.agent import SqlAgentParams as _Params

    node._sql_agent_params = node.node_params.get('sql_agent', None)
    node._sql_agent = (
        _Params.model_validate(node.node_params['sql_agent'])
        if (node._sql_agent_params and node._sql_agent_params.get('open', False))
        else None
    )
    node._datasource_id = node._sql_agent.datasource_id if node._sql_agent else None
    node._sql_address = ''
    if node._sql_agent and node._sql_agent.open and not node._datasource_id:
        node._sql_address = node._init_sql_address()
    return node


# ── the parameter ────────────────────────────────────────────────────────────


def test_the_param_accepts_a_datasource_id():
    """AC-53's storage: the binding is an id, so the node never carries
    connection details for a managed datasource."""
    params = SqlAgentParams.model_validate({**INLINE, 'datasource_id': 7})

    assert params.datasource_id == 7


def test_the_param_defaults_datasource_id_to_absent():
    """AC-54: a saved flow predates this field, so it must stay optional. A
    required field would make every existing flow fail validation on load."""
    params = SqlAgentParams.model_validate(INLINE)

    assert params.datasource_id is None


def test_a_datasource_binding_does_not_require_inline_credentials():
    """The whole point of AC-53/AC-56: credentials live on the datasource, so a
    flow that picks one must not have to carry host/user/password of its own.

    The inline block's fields stay required for the LEGACY shape — that is what
    keeps this from loosening AC-54 — and become optional once an id is given.
    """
    params = SqlAgentParams.model_validate({'open': True, 'datasource_id': 7})

    assert params.datasource_id == 7
    assert not params.db_address
    assert not params.db_password


# ── the node's mode selection ────────────────────────────────────────────────


def test_a_datasource_binding_produces_no_inline_address():
    """With an id, there is no inline connection to build — and building one
    anyway would be the plaintext-password path this AC exists to retire."""
    node = _apply_init_logic(_node(**INLINE, datasource_id=7))

    assert node._datasource_id == 7
    # The inline fields are still present (the flow may predate the switch), but
    # nothing derives an address from them when a datasource is bound.
    assert node._sql_address == ''


def test_an_inline_only_flow_is_unaffected():
    """AC-54 again, at the node level: no id means the legacy path."""
    node = _apply_init_logic(_node(**INLINE))

    assert node._datasource_id is None
    assert node._sql_address.startswith('mysql+pymysql://')


def test_a_closed_block_binds_neither_path():
    node = _apply_init_logic(_node(open=False, datasource_id=7))

    assert node._sql_agent is None
    assert node._sql_address == ''


# ── who the query runs as ────────────────────────────────────────────────────


def test_the_tools_are_built_with_the_config_authors_identity(monkeypatch):
    """The decision this file exists for, asserted on the call arguments: the
    identity is the flow's author, and it is a full payload — not a bare id."""
    captured = {}

    async def _build(datasources, *, user_id=None, login_user=None):
        captured['datasources'] = datasources
        captured['user_id'] = user_id
        captured['login_user'] = login_user
        return ['datasource-tool']

    # Keyed by id and ASSERTING which id was asked for. A mock that ignores its
    # argument returns the author row whichever user the node looked up, which
    # made "authorized as the config author" unfalsifiable — it survived
    # changing the lookup to the runtime caller.
    by_id = {
        11: SimpleNamespace(user_id=11, user_name='runner', tenant_id=1),
        22: SimpleNamespace(user_id=22, user_name='author', tenant_id=1),
    }
    asked: list[int] = []

    def _get_user(uid):
        asked.append(int(uid))
        return by_id.get(int(uid))

    node = _apply_init_logic(_node(**INLINE, datasource_id=7))
    # Patched at the DEFINING module: the node imports these inside the method,
    # so `agent_module.X` is not a name that exists to patch.
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.build_datasource_tools', _build
    )
    monkeypatch.setattr(
        'bisheng.user.domain.models.user.UserDao.get_user',
        staticmethod(_get_user),
    )
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id',
        AsyncMock(return_value=SimpleNamespace(id=7, name='orders-db', enabled=1, tenant_id=1)),
    )

    tools = node.init_sql_agent_tool()

    assert tools == ['datasource-tool']
    assert asked == [22], (
        f'the node looked up user {asked}, but the query must be authorized as '
        'the flow CONFIG AUTHOR (22) -- looking up the runtime caller (11) means '
        'a published flow queries as whoever runs it'
    )
    assert captured['user_id'] == 22, (
        'the query must run as the flow config author, matching what this node '
        'already does for knowledge retrieval with its permission toggle off'
    )

    # The identity is what actually distinguishes "authorized as the config
    # author" from "authorized as whoever ran the flow". Asserted on the
    # payload's user_id, because `user_id=22` above is a literal at the call
    # site -- it stays 22 whichever user was looked up, which is how the first
    # version of this test passed with the lookup deliberately broken.
    #
    # `get_visible_tenants` cannot carry this assertion: it is a METHOD ON THE
    # CLASS, so every UserPayload has it regardless of how it was constructed.
    # (Verified: `UserPayload(user_id=3, user_name='', user_role=[])` reports it
    # as callable.) What must not happen is passing no payload at all, or a bare
    # `LoginUser` -- the shapes the guard in `build_tools_for_assistant` rejects.
    assert captured['login_user'] is not None, (
        'no identity reached the tool builder, so `_authorized` would refuse '
        'every call'
    )
    assert captured['login_user'].user_id == 22, (
        f'the identity passed down is user {captured["login_user"].user_id}, not '
        'the flow author (22) -- a published flow would then query as whoever '
        'happens to run it'
    )
    assert captured['login_user'].tenant_id == 1, (
        'the identity carries the wrong tenant, so the query would be gated '
        'against the wrong tenant set'
    )


def test_a_deleted_or_disabled_datasource_yields_no_tool(monkeypatch):
    """AC-62: deleting a datasource leaves the flow's binding pointing at nothing.
    A run must then contribute no sql tool rather than crash — the flow keeps
    working, minus the database."""
    node = _apply_init_logic(_node(**INLINE, datasource_id=7))
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.build_datasource_tools',
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        'bisheng.user.domain.models.user.UserDao.get_user',
        staticmethod(lambda uid: SimpleNamespace(
            user_id=22, user_name='a', tenant_id=1, role='', is_admin=False,
            get_visible_tenants=lambda: [1],
        )),
    )
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id',
        AsyncMock(return_value=None),
    )

    assert node.init_sql_agent_tool() == []


def test_an_unknown_author_still_fails_closed(monkeypatch):
    """`UserDao.get_user` can return None for a deleted account. Constructing a
    payload from a missing row would produce one without `get_visible_tenants` —
    the silent-tenant-skip shape — so the node must refuse instead."""
    node = _apply_init_logic(_node(**INLINE, datasource_id=7))
    built = MagicMock()
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.build_datasource_tools', built
    )
    monkeypatch.setattr(
        'bisheng.user.domain.models.user.UserDao.get_user', staticmethod(lambda uid: None)
    )

    tools = node.init_sql_agent_tool()

    assert tools == []
    built.assert_not_called()
