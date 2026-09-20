"""F061 T-31: binding a datasource to an assistant, end to end through the DAO.

The trap this file exists for: `datasource_id` is the only one of the four
link columns whose default is NULL, and SQL's `NULL != 0` evaluates to NULL
rather than TRUE. A delete filter copied from `update_assistant_tool` would
therefore match nothing, and every save would append another duplicate row
instead of replacing. `test_saving_twice_does_not_duplicate` is that guard.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.database.models.assistant import AssistantLinkDao


def _session_capturing(added):
    """A session whose delete-call and added rows we can inspect afterwards."""
    session = MagicMock()
    session.query.return_value.filter.return_value.delete.return_value = 0
    session.add.side_effect = added.append
    return session


def _ctx(session):
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False
    return ctx


def test_saving_twice_does_not_duplicate():
    added = []
    session = _session_capturing(added)

    with patch(
        'bisheng.database.models.assistant.get_sync_db_session',
        return_value=_ctx(session),
    ):
        AssistantLinkDao.update_assistant_datasource('a1', [7, 8])
        first_filter = session.query.return_value.filter.call_args

    # The filter must be IS NOT NULL, not != 0: NULL != 0 is NULL, not TRUE,
    # so a `!= 0` filter deletes nothing and the next save duplicates.
    #
    # The criteria are compiled to SQL rather than stringified: SQLAlchemy's
    # `BinaryExpression.__repr__` is the plain object repr, so `str(call_args)`
    # reads the same for every possible filter and could never catch this.
    rendered = ' AND '.join(
        str(one.compile(compile_kwargs={'literal_binds': True})) for one in first_filter.args
    )
    assert 'IS NOT NULL' in rendered.upper(), f'delete filter would not match NULL rows: {rendered}'
    # The widening clause: without it, one assistant's save wipes every
    # assistant's bindings -- a larger blast radius than the duplication above.
    assert 'assistantlink.assistant_id' in rendered, f'delete is not scoped to one assistant: {rendered}'
    assert [link.datasource_id for link in added] == [7, 8]

    # A dropped commit() is silent loss the mock cannot otherwise reveal: the
    # rows are staged on the fake session and nothing else would notice.
    session.commit.assert_called_once()


def test_an_empty_list_clears_the_binding():
    added = []
    session = _session_capturing(added)

    with patch(
        'bisheng.database.models.assistant.get_sync_db_session',
        return_value=_ctx(session),
    ):
        AssistantLinkDao.update_assistant_datasource('a1', [])

    assert added == []
    session.query.return_value.filter.return_value.delete.assert_called_once()


async def test_the_login_user_contract_is_enforced():
    """Task 0's guard: a None or bare-LoginUser caller would silently skip the
    tenant gate inside PermissionService.check, so it raises instead.

    Both halves are asserted: the guard's message names both, and the
    bare-object one is what Task 3's wiring could most easily regress into.
    """
    from bisheng.datasource.domain.services import conversation_service

    with pytest.raises(ValueError):
        await conversation_service.build_tools_for_assistant([], None)
    with pytest.raises(ValueError):
        await conversation_service.build_tools_for_assistant([], SimpleNamespace(user_id=3))


def test_the_detail_buckets_datasource_links_before_the_else():
    """A datasource row must be recognised, not fall through to the `else` that
    logs 'not expect link info' -- it would still work, but every datasource
    binding would write an error line on every detail read."""
    import inspect

    from bisheng.api.services import assistant as assistant_service

    source = inspect.getsource(assistant_service)
    else_at = source.index('not expect link info')
    datasource_at = source.index('one.datasource_id')
    assert datasource_at < else_at, 'datasource branch is missing or after the else'


def test_every_assistant_info_response_reports_datasource_bindings():
    """All AssistantInfo construction sites that can carry bindings must pass
    datasource_list. The update response is the easy one to miss: omitting it
    reports [] right after a save, so the UI clears the selection just made."""
    import inspect

    from bisheng.api.services import assistant as assistant_service

    source = inspect.getsource(assistant_service)
    constructors = source.count('AssistantInfo(')
    passing = source.count('datasource_list=')
    # create_assistant is the one site that legitimately omits it.
    assert passing >= constructors - 1, (
        f'{constructors} AssistantInfo sites but only {passing} pass datasource_list'
    )


async def test_the_update_response_reports_what_the_store_holds():
    """The update response has to describe the stored bindings, not the request.

    Neither static check above can see the value. Two cases matter: the save
    skips falsy ids, so echoing the request could name a datasource that was
    never written; and None leaves the bindings untouched, so reporting the
    schema default [] reads as a clear, and the picker drops a selection the
    admin never touched.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from bisheng.api.services import assistant as assistant_service
    from bisheng.api.v1.schemas import AssistantUpdateReq
    from bisheng.database.models.assistant import Assistant

    # A stand-in for the link table: the save writes it, the read reads it.
    store = {'links': []}

    def _save(_assistant_id, datasource_list):
        store['links'] = [
            SimpleNamespace(datasource_id=one, tool_id=0, knowledge_id=0, flow_id='')
            for one in datasource_list
            if one
        ]

    stored = Assistant(id='a1', name='n', user_id=1)
    telemetry = MagicMock()
    telemetry.log_event = AsyncMock()

    with (
        patch.object(assistant_service.AssistantDao, 'get_one_assistant', return_value=stored),
        patch.object(assistant_service.AssistantDao, 'update_assistant', new=MagicMock()),
        patch.object(assistant_service.AssistantService, 'check_update_permission_async', new=AsyncMock()),
        patch.object(assistant_service.AssistantService, 'update_assistant_hook', new=MagicMock()),
        patch.object(assistant_service.AssistantLinkDao, 'update_assistant_datasource', side_effect=_save),
        patch.object(
            assistant_service.AssistantLinkDao,
            'get_assistant_link',
            new=AsyncMock(side_effect=lambda _assistant_id: store['links']),
        ),
        patch.object(assistant_service, 'telemetry_service', new=telemetry),
        patch.object(assistant_service, 'user_may_share_app', new=AsyncMock(return_value=False)),
    ):

        async def _update(**overrides):
            req = AssistantUpdateReq(id='a1', model_name='m', temperature=1.0, **overrides)
            return await assistant_service.AssistantService.update_assistant(MagicMock(), MagicMock(), req)

        # The save drops the falsy id, so the response must not name it back
        assert (await _update(datasource_list=[7, 0, 8])).datasource_list == [7, 8]
        assert [link.datasource_id for link in store['links']] == [7, 8]

        # None leaves them untouched -- [] here would clear the admin's picker
        assert (await _update()).datasource_list == [7, 8]

        # An empty list is a clear, and the response says so
        assert (await _update(datasource_list=[])).datasource_list == []


def _payload(user_id=3):
    """A UserPayload, which carries get_visible_tenants. The contract guard
    rejects None and bare LoginUser, because both silently skip the tenant gate."""
    from bisheng.common.dependencies.user_deps import UserPayload

    return UserPayload(user_id=user_id, user_name='', user_role=[])


def _link(datasource_id):
    from bisheng.database.models.assistant import AssistantLink

    return AssistantLink(assistant_id='a1', datasource_id=datasource_id)


async def test_nothing_bound_yields_no_tools():
    """AC-60: every existing assistant keeps behaving exactly as before."""
    from bisheng.datasource.domain.services import conversation_service

    assert await conversation_service.build_tools_for_assistant([], _payload()) == []


async def test_a_bound_and_authorized_datasource_yields_a_tool(monkeypatch):
    from bisheng.datasource.domain.services import conversation_service

    async def _allowed(login_user, datasource_ids):
        return list(datasource_ids)

    datasource = SimpleNamespace(id=7, name='客户订单库', enabled=True)

    monkeypatch.setattr(conversation_service, 'authorized_datasource_ids', _allowed)
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceDao',
        SimpleNamespace(aget_by_id=AsyncMock(return_value=datasource)),
    )
    monkeypatch.setattr(
        'bisheng.datasource.domain.models.dao.DbDatasourceTableDao',
        SimpleNamespace(alist_by_datasource=AsyncMock(return_value=[])),
    )
    # `langchain/tool.py` binds the table DAO at import time, so patching the dao
    # module alone leaves the real one in place and this test would read a live
    # database (it passed that way here only because MySQL answered and no rows
    # existed for id 7). Patch the name the builder actually looks up.
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DbDatasourceTableDao',
        SimpleNamespace(alist_by_datasource=AsyncMock(return_value=[])),
    )

    tools = await conversation_service.build_tools_for_assistant([_link(7)], _payload())

    assert [tool.name for tool in tools] == ['datasource_7']


async def test_init_tools_wires_the_datasource_tools():
    """The wiring itself. Asserted on source because driving init_tools needs a
    live assistant plus an LLM; the behaviour is covered by the tests above."""
    import inspect

    from bisheng.api.services import assistant_agent

    source = inspect.getsource(assistant_agent.AssistantAgent.init_tools)
    assert 'build_tools_for_assistant' in source, 'init_tools never asks for datasource tools'
    assert 'UserPayload(' in source, (
        'init_tools must pass a UserPayload -- a bare user id would skip the tenant gate'
    )
