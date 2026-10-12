"""F061 self-test endpoint: ask one question from the datasource config page.

Two rules carry the weight here, and both are about audiences rather than
plumbing:

* **A caller who may not manage the datasource must be refused BEFORE anything
  is queried.** This endpoint is admin-gated, so a missing `can_manage` would
  turn the config page into a way around the datasource's own permission check
  (AC-49). Asserting only "it raised" would still pass if the refusal happened
  after the model had already been asked -- hence the `assert_not_awaited`.
* **A failed query is 200 + `degraded`, not an error.** `answer_question` never
  raises; it returns the statements it tried. A 500 would throw away the one
  thing the admin opened the box to see.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.common.errcode.datasource import DatasourceNotFoundError
from bisheng.datasource.api.endpoints import test_query
from bisheng.datasource.domain.services.executor import QueryOutcome
from bisheng.datasource.domain.services.query_service import AnswerContext
from bisheng.permission.domain.services.permission_service import PermissionService

_QUESTION = '本期的订单总额是多少'


class _Admin:
    user_id = 3
    tenant_id = 1


def _datasource(enabled=0):
    # `enabled=0` on purpose: the default fixture is a datasource that has NOT
    # been switched on yet, which is exactly when an operator wants to test it.
    return SimpleNamespace(id=7, tenant_id=1, engine='mysql', name='业务库', enabled=enabled)


def _outcome():
    return QueryOutcome(
        statement='SELECT SUM(amount) FROM orders',
        columns=['total'],
        rows=[[1234.5]],
        row_count=1,
    )


def _wire(monkeypatch, *, allowed=True, datasource=None, context=None, captured=None):
    """Patch the four seams the endpoint crosses. Returns nothing; asserts nothing."""

    async def _check(user_id, relation, object_type, object_id, login_user=None, **kwargs):
        return allowed

    monkeypatch.setattr(PermissionService, 'check', _check)
    monkeypatch.setattr(
        test_query.DbDatasourceDao,
        'aget_by_id',
        AsyncMock(return_value=_datasource() if datasource is None else datasource),
    )
    monkeypatch.setattr(
        test_query.schema_service,
        'alist_model_tables',
        AsyncMock(return_value=[{'table_name': 'orders'}]),
    )

    async def _answer(ds, question, tables, *, user_id=None):
        if captured is not None:
            captured.update(datasource=ds, question=question, tables=tables, user_id=user_id)
        return context

    monkeypatch.setattr(test_query.DatasourceQueryService, 'answer_question', _answer)


def _payload(question=_QUESTION):
    return test_query.DatasourceTestQueryPayload(question=question)


async def test_an_authorized_admin_gets_the_statement_and_rows(monkeypatch):
    _wire(monkeypatch, context=AnswerContext(answer='本期合计 1234.5', outcome=_outcome()))

    response = await test_query.test_datasource_query(7, _payload(), login_user=_Admin())

    payload = response.data
    assert payload['statement'] == 'SELECT SUM(amount) FROM orders'
    assert payload['columns'] == ['total']
    assert payload['rows'] == [[1234.5]]
    assert payload['row_count'] == 1
    assert payload['degraded'] is False
    assert payload['truncated'] is False


async def test_an_unauthorized_caller_is_refused_before_anything_is_queried(monkeypatch):
    """The half that matters: refused, and the model was never asked."""
    asked = AsyncMock()
    _wire(monkeypatch, allowed=False, context=AnswerContext(answer=''))
    monkeypatch.setattr(test_query.DatasourceQueryService, 'answer_question', asked)

    with pytest.raises(DatasourceNotFoundError):
        await test_query.test_datasource_query(7, _payload(), login_user=_Admin())

    asked.assert_not_awaited()


async def test_a_missing_datasource_looks_the_same_as_a_forbidden_one(monkeypatch):
    """Both raise 11708 so ids cannot be probed by differential responses."""
    _wire(monkeypatch, datasource=None, context=AnswerContext(answer=''))
    monkeypatch.setattr(test_query.DbDatasourceDao, 'aget_by_id', AsyncMock(return_value=None))

    with pytest.raises(DatasourceNotFoundError):
        await test_query.test_datasource_query(999, _payload(), login_user=_Admin())


@pytest.mark.parametrize('category', ['timeout', 'syntax', 'guard_blocked'])
async def test_a_failed_query_is_200_and_degraded_not_an_error(monkeypatch, category):
    tried = ['SELECT f_0031 FROM orders', 'SELECT oops FROM nope']
    _wire(
        monkeypatch,
        context=AnswerContext(
            answer='这次没能查到数据。',
            outcome=None,
            failure_category=category,
            degraded=True,
            statements_tried=tried,
        ),
    )

    response = await test_query.test_datasource_query(7, _payload(), login_user=_Admin())

    payload = response.data
    assert payload['degraded'] is True
    assert payload['failure_category'] == category
    assert payload['statement'] is None
    assert payload['columns'] == []
    assert payload['rows'] == []
    assert payload['row_count'] == 0
    # The attempted statements are the whole reason the box exists on a failure.
    assert payload['statements_tried'] == tried


async def test_a_disabled_datasource_is_still_testable(monkeypatch):
    """`enabled=0` must not block the self-test -- "try it before I switch it on"."""
    _wire(monkeypatch, datasource=_datasource(enabled=0), context=AnswerContext(answer='ok', outcome=_outcome()))

    response = await test_query.test_datasource_query(7, _payload(), login_user=_Admin())

    assert response.data['row_count'] == 1


async def test_the_query_service_gets_the_exposed_tables_and_the_caller(monkeypatch):
    """Not an empty table list, and not somebody else's user id."""
    captured = {}
    _wire(monkeypatch, context=AnswerContext(answer='ok', outcome=_outcome()), captured=captured)

    await test_query.test_datasource_query(7, _payload(question='订单数'), login_user=_Admin())

    assert captured['tables'] == [{'table_name': 'orders'}]
    assert captured['user_id'] == 3
    assert captured['question'] == '订单数'


async def test_the_endpoint_is_admin_gated():
    """Gated on the dependency, not by a runtime check we could forget to call."""
    import inspect

    from bisheng.common.dependencies.user_deps import UserPayload

    sig = inspect.signature(test_query.test_datasource_query)
    login_dep = sig.parameters['login_user'].default
    # Compare `__func__`: reading the classmethod twice yields distinct bound
    # objects, so `is` on the methods themselves is always False.
    assert login_dep.dependency.__func__ is UserPayload.get_tenant_admin_user.__func__, (
        'the self-test is no longer restricted to tenant admins'
    )


def test_the_question_is_bounded():
    """The question is interpolated into a prompt, so its length is a budget."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        test_query.DatasourceTestQueryPayload(question='')
    with pytest.raises(ValidationError):
        test_query.DatasourceTestQueryPayload(question='x' * (test_query.MAX_QUESTION_LENGTH + 1))
