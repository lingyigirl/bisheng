"""F061 T-31: binding a datasource to an assistant, end to end through the DAO.

The trap this file exists for: `datasource_id` is the only one of the four
link columns whose default is NULL, and SQL's `NULL != 0` evaluates to NULL
rather than TRUE. A delete filter copied from `update_assistant_tool` would
therefore match nothing, and every save would append another duplicate row
instead of replacing. `test_saving_twice_does_not_duplicate` is that guard.
"""

from unittest.mock import MagicMock, patch

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
    assert [link.datasource_id for link in added] == [7, 8]


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
    tenant gate inside PermissionService.check, so it raises instead."""
    import pytest

    from bisheng.datasource.domain.services import conversation_service

    with pytest.raises(ValueError):
        await conversation_service.build_tools_for_assistant([], None)
