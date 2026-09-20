"""Generation, retry and the failure trail.

The two rules worth testing hardest here are about audiences: the DATABASE's
error message must reach the model (that is what lets it fix the statement)
and must never reach the user (a driver error carries the host, port, schema
and account), and the user's QUESTION must not reach the failure table (it is
conversation text, and INV-11 bounds what that table may hold).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.datasource.domain.services import query_service
from bisheng.datasource.domain.services.executor import QueryOutcome
from bisheng.datasource.domain.services.query_service import (
    MAX_RETRY_ROUNDS,
    DatasourceQueryService,
    categorize_error,
    user_message_for,
)


def _datasource():
    return MagicMock(id=7, tenant_id=1, engine='mysql', name='业务库')


def _tables():
    return [
        {'id': 1, 'table_name': 'orders', 'business_desc': '订单主表',
         'structure_snapshot': [{'name': 'id', 'type': 'INT', 'comment': None}], 'enabled': 1}
    ]


def _model_returning(*replies):
    """A fake LLM that returns the given replies in order."""
    model = MagicMock()
    replies = list(replies)

    async def _ainvoke(prompt):
        if replies:
            return MagicMock(content=replies.pop(0))
        return MagicMock(content='')

    model.ainvoke = _ainvoke
    return model


async def _run(*, model, execute_side_effect, recorded_failures=None, question='有多少订单'):
    """Drive `answer_question` with the model and executor stubbed."""
    failures = recorded_failures if recorded_failures is not None else []

    async def _record(payload):
        # `acreate_best_effort(payload)` is a classmethod taking the row dict.
        failures.append(payload)

    with patch.object(
        DatasourceQueryService, '_resolve_model', AsyncMock(return_value=model)
    ), patch.object(
        query_service.DatasourceExecutor, 'execute', AsyncMock(side_effect=execute_side_effect)
    ), patch.object(
        query_service.DbQueryFailureDao, 'acreate_best_effort', _record
    ):
        return await DatasourceQueryService.answer_question(
            _datasource(), question, _tables(), user_id=3
        )


# --- the happy path ----------------------------------------------------------


async def test_a_successful_query_returns_a_summary_and_the_rows():
    outcome = QueryOutcome(
        statement='SELECT COUNT(*) FROM orders', columns=['c'], rows=[[42]], row_count=1
    )
    result = await _run(
        model=_model_returning('SELECT COUNT(*) FROM orders'),
        execute_side_effect=lambda *a, **k: outcome,
    )

    assert result.failure_category is None
    assert result.outcome is outcome
    assert result.injection_mode == 'full'

    # The rows do NOT appear in the answer text -- they are rendered from
    # `outcome` by the caller, and duplicating them into the message body would
    # give customer data a second retention path.
    assert '42' not in result.answer
    assert '1 行' in result.answer


async def test_an_empty_result_is_an_answer_not_a_failure():
    """`no rows` is a legitimate answer; it must not enter the retry loop."""
    outcome = QueryOutcome(statement='SELECT 1 FROM orders WHERE 0', row_count=0)
    result = await _run(
        model=_model_returning('SELECT 1 FROM orders WHERE 0'),
        execute_side_effect=lambda *a, **k: outcome,
    )

    assert result.failure_category is None
    assert result.degraded is False
    assert '没有匹配的数据' in result.answer


# --- retry (AC-37 / AC-38) ---------------------------------------------------


async def test_a_failure_is_fed_back_and_retried():
    """The model must SEE the database's message -- that is what lets it fix
    the statement."""
    prompts = []
    model = MagicMock()

    async def _ainvoke(prompt):
        prompts.append(prompt)
        return MagicMock(content='SELECT bad_column FROM orders')

    model.ainvoke = _ainvoke
    calls = {'n': 0}

    def _fail_twice_then_succeed(*a, **k):
        calls['n'] += 1
        if calls['n'] < 3:
            raise RuntimeError('unknown column bad_column')
        return QueryOutcome(statement='SELECT id FROM orders', row_count=1)

    result = await _run(model=model, execute_side_effect=_fail_twice_then_succeed)

    assert result.failure_category is None
    assert calls['n'] == 3
    # The second prompt carries the real error, so the model can correct itself.
    assert 'unknown column bad_column' in prompts[1]


async def test_the_retry_budget_is_exactly_three_rounds():
    """Spec AC-38: no more than three. Each round is a model call plus a query
    against a live database, so an unbounded loop is an outage generator."""
    calls = {'n': 0}

    def _always_fail(*a, **k):
        calls['n'] += 1
        raise RuntimeError('syntax error near ")"')

    failures = []
    result = await _run(
        model=_model_returning(*['SELECT bad FROM orders'] * 10),
        execute_side_effect=_always_fail,
        recorded_failures=failures,
    )

    assert calls['n'] == MAX_RETRY_ROUNDS
    assert result.degraded is True
    assert failures, 'the exhausted retry budget was not recorded'


async def test_a_model_that_returns_nothing_does_not_burn_a_query():
    """No statement means no execution; the round is spent generating, not
    querying."""
    executed = []

    result = await _run(
        model=_model_returning('', ''),
        execute_side_effect=lambda *a, **k: executed.append(1),
    )

    assert executed == []
    assert result.degraded is True


# --- AC-39: the user never sees the driver's message -------------------------


async def test_the_user_message_never_carries_the_driver_error():
    """The error reaches the model and stops there.

    A driver message can contain the host, port, schema and account name --
    exactly what INV-19 clause 1 forbids exposing.
    """
    leaky = 'Access denied for user "admin"@\'10.1.2.3\' to database "customer_db"'

    result = await _run(
        model=_model_returning(*['SELECT bad FROM orders'] * 5),
        execute_side_effect=lambda *a, **k: _raise(leaky),
    )

    for secret in ('10.1.2.3', 'customer_db', 'admin', 'Access denied'):
        assert secret not in result.answer, f'{secret!r} leaked into the user message'
    assert result.answer


def _raise(message):
    raise RuntimeError(message)


@pytest.mark.parametrize('message,expected', [
    ('Access denied for user', 'permission'),
    ("Table 'db.nope' doesn't exist", 'unknown_table'),
    ('Unknown column f_0031', 'unknown_column'),
    ('You have an error in your SQL syntax', 'syntax'),
    ('Lock wait timeout exceeded', 'timeout'),
    ('Connection refused', 'connection'),
    ('something nobody has seen before', 'other'),
])
def test_driver_messages_are_reduced_to_a_closed_category(message, expected):
    """A closed set is what makes the admin list groupable and actionable."""
    assert categorize_error(message) == expected


def test_every_category_has_a_user_message():
    for category in ('timeout', 'permission', 'unknown_table', 'unknown_column', 'syntax', 'connection', 'other'):
        assert user_message_for(category)


def test_an_unknown_category_still_produces_a_message():
    assert user_message_for(None) == user_message_for('other')


# --- AC-40: a failure does not take the turn down ----------------------------


async def test_a_failure_degrades_rather_than_raising():
    """The caller keeps whatever document knowledge it had; this feature
    reports its own failure and steps aside."""
    result = await _run(
        model=_model_returning(*['SELECT bad FROM orders'] * 5),
        execute_side_effect=lambda *a, **k: _raise('boom'),
    )

    assert result.degraded is True
    assert result.failure_category is not None
    assert result.answer


# --- AC-41/65: the failure trail's storage boundary --------------------------


async def test_the_failure_record_keeps_statements_but_not_the_question():
    """An admin needs the statements to see which table was being queried.
    They do NOT get the user's question -- that is conversation text, and
    INV-11 bounds what this table may hold (INV-19 clause 3)."""
    secret_question = '帮我查一下客户张三的联系方式'
    failures = []

    await _run(
        model=_model_returning(*['SELECT phone FROM customers'] * 5),
        execute_side_effect=lambda *a, **k: _raise('unknown column phone'),
        recorded_failures=failures,
        question=secret_question,
    )

    assert failures, 'nothing was recorded'
    row = failures[0]
    serialised = repr(row)
    assert secret_question not in serialised, 'the user question reached the failure trail'
    assert '张三' not in serialised

    # Every attempt is kept, including repeats -- an admin reading the trail
    # wants to see that the model tried the same broken statement three times,
    # not just that it tried once.
    assert row['statements'] == ['SELECT phone FROM customers'] * MAX_RETRY_ROUNDS
    assert row['error_category'] == 'unknown_column'
    assert row['datasource_id'] == 7
    # No column could carry a result set.
    assert 'rows' not in row and 'columns' not in row


async def test_the_failure_record_names_the_tables_the_admin_should_describe():
    """AC-42's whole point: the list drives "go describe this table"."""
    failures = []
    await _run(
        model=_model_returning(*['SELECT o.id FROM orders o JOIN customers c ON o.cid = c.id'] * 5),
        execute_side_effect=lambda *a, **k: _raise('unknown column'),
        recorded_failures=failures,
    )

    assert set(failures[0]['tables']) == {'orders', 'customers'}


async def test_a_failed_trail_write_does_not_change_the_outcome():
    async def _boom(payload):
        raise RuntimeError('failure table is gone')

    with patch.object(
        DatasourceQueryService, '_resolve_model', AsyncMock(return_value=_model_returning(*['SELECT bad FROM t'] * 5))
    ), patch.object(
        query_service.DatasourceExecutor, 'execute', AsyncMock(side_effect=lambda *a, **k: _raise('boom'))
    ), patch.object(
        query_service.DbQueryFailureDao, 'acreate_best_effort', _boom
    ):
        result = await DatasourceQueryService.answer_question(_datasource(), 'q', _tables())

    assert result.degraded is True


# --- statement extraction ----------------------------------------------------


@pytest.mark.parametrize('reply,expected', [
    ('SELECT id FROM orders', 'SELECT id FROM orders'),
    ('```sql\nSELECT id FROM orders\n```', 'SELECT id FROM orders'),
    ('```\nSELECT id FROM orders\n```', 'SELECT id FROM orders'),
    ('SELECT id FROM orders;', 'SELECT id FROM orders'),
    ('Here is the SQL:\nSELECT id FROM orders', 'SELECT id FROM orders'),
    ('WITH x AS (SELECT 1) SELECT * FROM x', 'WITH x AS (SELECT 1) SELECT * FROM x'),
])
def test_statement_extraction_handles_the_shapes_models_emit(reply, expected):
    """Formatting only -- `SqlGuard` decides what may run. This exists so a
    fenced block does not look like an invalid statement."""
    assert query_service._extract_statement(reply) == expected


def test_extraction_of_a_reply_with_no_statement_is_empty():
    assert query_service._extract_statement('I cannot help with that.') == ''
    assert query_service._extract_statement('') == ''


# --- AC-27: the slot falls back silently -------------------------------------


async def test_no_configured_slot_falls_back_without_error(monkeypatch):
    """An unconfigured slot is the NORMAL state, not an error (AC-27)."""
    monkeypatch.setattr(
        'bisheng.llm.domain.services.model_slot.resolve_slot_model_id', lambda *a, **k: None
    )
    model = await DatasourceQueryService._resolve_model(_datasource())
    assert model is None


async def test_an_unbuildable_slot_model_is_treated_as_unconfigured(monkeypatch):
    """A slot pointing at a model that cannot be built must degrade to the
    fallback, not raise into the conversation."""
    monkeypatch.setattr(
        'bisheng.llm.domain.services.model_slot.resolve_slot_model_id', lambda *a, **k: 99
    )
    monkeypatch.setattr(
        'bisheng.llm.domain.services.llm.LLMService.get_bisheng_llm_sync',
        MagicMock(side_effect=RuntimeError('model 99 is gone')),
    )

    assert await DatasourceQueryService._resolve_model(_datasource()) is None


# --- AC-42: the admin failure list -------------------------------------------


def test_the_failure_endpoint_is_registered_and_admin_gated():
    """The route exists, and reaching it requires a tenant admin.

    Asserted on the dependency rather than by calling it: the statements are
    the customer's schema in SQL form, so who may read the list is part of the
    contract, not an implementation detail.
    """
    import inspect

    from bisheng.common.dependencies.user_deps import UserPayload
    from bisheng.datasource.api.endpoints.failures import list_query_failures

    sig = inspect.signature(list_query_failures)
    login_dep = sig.parameters['login_user'].default
    # Compare `__func__`: `UserPayload.get_tenant_admin_user` read twice gives
    # two distinct bound-method objects, so `is` on the methods themselves is
    # always False even when the dependency is the right one.
    assert login_dep.dependency.__func__ is UserPayload.get_tenant_admin_user.__func__, (
        'the failure list is no longer restricted to tenant admins'
    )


async def test_the_failure_list_returns_tables_and_category_but_not_the_question(monkeypatch):
    """The page's purpose is "go describe this table"; that needs the tables and
    a category, and needs no conversation text (INV-19 clause 3)."""
    from bisheng.datasource.api.endpoints import failures as failures_module

    row = MagicMock(
        id=1,
        datasource_id=7,
        statements=['SELECT f_0031 FROM orders'],
        tables=['orders'],
        error_category='unknown_column',
        create_time=None,
    )

    with patch.object(
        failures_module.DbQueryFailureDao, 'alist', AsyncMock(return_value=([row], 1))
    ):
        response = await failures_module.list_query_failures(login_user=MagicMock())

    payload = response.data
    assert payload['total'] == 1
    item = payload['data'][0]
    assert item['tables'] == ['orders']
    assert item['error_category'] == 'unknown_column'
    assert item['statements'] == ['SELECT f_0031 FROM orders']
    # No field could carry the user's question or a result set.
    assert 'question' not in item and 'rows' not in item and 'message' not in item


async def test_the_failure_list_filters_reach_the_dao(monkeypatch):
    from bisheng.datasource.api.endpoints import failures as failures_module

    captured = {}

    async def _alist(**kwargs):
        captured.update(kwargs)
        return [], 0

    with patch.object(failures_module.DbQueryFailureDao, 'alist', _alist):
        await failures_module.list_query_failures(
            datasource_id=7, error_category='timeout', page=2, page_size=50, login_user=MagicMock()
        )

    assert captured['datasource_id'] == 7
    assert captured['error_category'] == 'timeout'
    assert captured['page'] == 2 and captured['page_size'] == 50
