"""The executor: non-blocking, read-only where possible, bounded, and audited.

Every test here is about something the executor must NOT do. That is not a
coincidence — the feature's promise is "the model can query your database
safely", and every way of breaking it is an action rather than an omission:
block the loop, skip the guard, return everything, or copy the rows into the
audit trail.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bisheng.common.errcode.datasource import DatasourceStatementRejectedError
from bisheng.datasource.domain.services import executor as executor_module
from bisheng.datasource.domain.services.executor import (
    MAX_ROWS,
    QUERY_TIMEOUT_SECONDS,
    DatasourceExecutor,
    QueryOutcome,
)


def _datasource(engine='mysql'):
    return MagicMock(id=7, tenant_id=1, engine=engine, host='h', port=3306,
                     database_name='d', username='u', password='cipher')


def _patch_sync(monkeypatch, outcome=None, side_effect=None):
    """Patch the blocking half and capture the audit rows.

    `_run_sync` is replaced because it needs a real database; everything around
    it — validation, the timeout, the audit write — is the code under test.
    """
    recorded: list[dict] = []

    async def _record(datasource, statement, **kwargs):
        # `datasource` and `statement` are positional in the real signature, so
        # the spy mirrors it. `datasource_id` is derived from the object rather
        # than passed -- asserting it here checks the executor handed over the
        # right datasource, which is what this level can see.
        recorded.append({'statement': statement, 'datasource_id': datasource.id, **kwargs})

    monkeypatch.setattr(DatasourceExecutor, '_record', staticmethod(_record))

    if side_effect is not None:
        monkeypatch.setattr(DatasourceExecutor, '_run_sync', staticmethod(side_effect))
    else:
        monkeypatch.setattr(
            DatasourceExecutor,
            '_run_sync',
            staticmethod(lambda ds, stmt: outcome or QueryOutcome(statement=stmt, columns=['id'], rows=[[1]], row_count=1)),
        )
    return recorded


# --- AC-21: the event loop is never blocked ---------------------------------


async def test_the_query_runs_off_the_event_loop(monkeypatch):
    """The loop must stay schedulable while a query is in flight.

    A heartbeat coroutine ticks during the 'query'; if the executor blocked the
    loop the heartbeat would not progress until the query returned. This is the
    property AC-21 asks for, and the counter-example the design records is the
    legacy `sql_agent`, whose `arun` calls the sync `run`.
    """
    ticks = 0

    def _slow_run(ds, stmt):
        import time

        time.sleep(0.15)
        return QueryOutcome(statement=stmt, row_count=0)

    _patch_sync(monkeypatch, side_effect=_slow_run)

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        await DatasourceExecutor.execute(_datasource(), 'SELECT id FROM t')
    finally:
        beat.cancel()

    assert ticks > 3, (
        f'the heartbeat only ran {ticks} times during a 150ms query -- the loop '
        'was blocked (spec AC-21)'
    )


async def test_the_sync_work_is_dispatched_to_a_thread(monkeypatch):
    """Asserted directly as well: `to_thread` is the mechanism, and a future
    edit calling `_run_sync(...)` inline would still pass the timing test on a
    fast machine while blocking on a slow one."""
    seen = {}
    real_to_thread = asyncio.to_thread

    async def _spy(fn, *args, **kwargs):
        seen['used'] = True
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(executor_module.asyncio, 'to_thread', _spy)
    _patch_sync(monkeypatch)

    await DatasourceExecutor.execute(_datasource(), 'SELECT id FROM t')

    assert seen.get('used'), 'the query ran on the event loop instead of a worker thread'


# --- the guard is not bypassable --------------------------------------------


@pytest.mark.parametrize(
    'statement',
    [
        'DROP TABLE t',
        'SELECT 1; DROP TABLE t',
        'WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x',
        'SELECT * FROM t FOR UPDATE',
    ],
)
async def test_a_rejected_statement_never_reaches_the_driver(monkeypatch, statement):
    """Validation lives in the executor so no caller can skip it."""
    ran = []

    def _should_not_run(ds, stmt):
        ran.append(stmt)
        raise AssertionError('the statement reached the database')

    recorded = _patch_sync(monkeypatch, side_effect=_should_not_run)

    with pytest.raises(DatasourceStatementRejectedError):
        await DatasourceExecutor.execute(_datasource(), statement)

    assert ran == [], 'a rejected statement was executed'
    # And the rejection is auditable.
    assert recorded and recorded[0]['status'] == 'blocked'


async def test_a_blocked_statement_is_logged_without_its_result(monkeypatch):
    recorded = _patch_sync(monkeypatch)
    with pytest.raises(DatasourceStatementRejectedError):
        await DatasourceExecutor.execute(_datasource(), 'DROP TABLE t')

    row = recorded[0]
    assert row['status'] == 'blocked'
    assert row['row_count'] is None
    assert 'rows' not in row and 'columns' not in row


# --- AC-18 / AC-35: bounds and truncation -----------------------------------


async def test_truncation_is_reported_not_hidden(monkeypatch):
    """Hitting the cap returns what was fetched AND says it was cut short."""
    outcome = QueryOutcome(
        statement='SELECT id FROM t',
        columns=['id'],
        rows=[[i] for i in range(MAX_ROWS)],
        row_count=MAX_ROWS,
        truncated=True,
    )
    recorded = _patch_sync(monkeypatch, outcome=outcome)

    result = await DatasourceExecutor.execute(_datasource(), 'SELECT id FROM t')

    assert result.truncated is True
    assert result.row_count == MAX_ROWS
    assert recorded[0]['status'] == 'truncated', 'the audit row hid the truncation'


def test_the_truncation_probe_fetches_one_row_past_the_cap():
    """Fetching exactly the cap cannot tell "200 rows" from "more than 200".

    Asserted on the source because the probe lives inside `_run_sync`, which
    every test here replaces (it needs a real database). A behavioural test
    would have to drive a live connection to reach it, and this is the one line
    that makes `truncated` meaningful rather than always-False.
    """
    import inspect

    source = inspect.getsource(DatasourceExecutor._run_sync)
    assert 'MAX_ROWS + 1' in source, 'the truncation probe row is gone'
    assert 'truncated = len(fetched) > MAX_ROWS' in source


async def test_a_hung_query_times_out_instead_of_holding_a_thread(monkeypatch):
    import time

    def _hang(ds, stmt):
        time.sleep(QUERY_TIMEOUT_SECONDS + 5)
        return QueryOutcome(statement=stmt)

    recorded = _patch_sync(monkeypatch, side_effect=_hang)
    # Shorten the ceiling so the test does not take 30 seconds.
    monkeypatch.setattr(executor_module, 'QUERY_TIMEOUT_SECONDS', 0.05)

    with pytest.raises(Exception) as exc:
        await DatasourceExecutor.execute(_datasource(), 'SELECT id FROM t')

    assert 'timeout' in type(exc.value).__name__.lower() or 'Timeout' in type(exc.value).__name__
    assert recorded and recorded[0]['status'] == 'failed'


# --- AC-19: read-only session, degraded observably --------------------------


@pytest.mark.parametrize('engine,expect_statement', [
    ('mysql', 'SET TRANSACTION READ ONLY'),
    ('postgresql', 'SET TRANSACTION READ ONLY'),
    ('oracle', 'SET TRANSACTION READ ONLY'),
    ('dm', 'SET TRANSACTION READ ONLY'),
])
def test_engines_with_a_readonly_statement_have_one(engine, expect_statement):
    assert DatasourceExecutor._begin_readonly(MagicMock(), engine) in (True, False)
    assert engine in executor_module._READONLY_STATEMENTS
    assert executor_module._READONLY_STATEMENTS[engine] == expect_statement


def test_an_engine_without_one_degrades_without_failing():
    """AC-19: absence is the degradation case. Raising here would take the
    feature offline on that engine and add no protection -- the account's own
    permissions still apply."""
    assert DatasourceExecutor._begin_readonly(MagicMock(), 'db2') is True


def test_a_failing_readonly_statement_degrades_quietly():
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError('this engine does not support it')

    assert DatasourceExecutor._begin_readonly(connection, 'mysql') is True


def test_a_successful_readonly_session_is_not_reported_as_degraded():
    connection = MagicMock()
    assert DatasourceExecutor._begin_readonly(connection, 'mysql') is False


# --- AC-22/23/65: the audit trail, and what it must not contain --------------


async def test_the_audit_row_never_carries_result_data(monkeypatch):
    """INV-19 clause 3 / AC-23: statement and status only.

    Asserted by serialising the WHOLE row and searching for the cell values,
    not by checking individual fields -- a per-field check misses any field
    added later, which is exactly how a result would leak into the log.
    """
    secret = 'CUSTOMER-SECRET-4417'
    outcome = QueryOutcome(
        statement='SELECT card_no FROM customers',
        columns=['card_no'],
        rows=[[secret]],
        row_count=1,
    )
    recorded = _patch_sync(monkeypatch, outcome=outcome)

    await DatasourceExecutor.execute(_datasource(), 'SELECT card_no FROM customers', user_id=3)

    row = recorded[0]
    serialised = repr(row)
    assert secret not in serialised, f'the result data reached the audit row: {serialised}'
    assert 'rows' not in row and 'columns' not in row, 'the row carries a result-shaped field'

    # And it does carry what an audit needs.
    assert row['statement'] == 'SELECT card_no FROM customers'
    assert row['status'] == 'success'
    assert row['user_id'] == 3
    assert row['datasource_id'] == 7


async def test_the_audit_row_records_the_affected_tables(monkeypatch):
    recorded = _patch_sync(monkeypatch)
    await DatasourceExecutor.execute(_datasource(), 'SELECT a.id FROM orders a JOIN customers c ON a.cid = c.id')

    assert set(recorded[0]['tables']) == {'orders', 'customers'}


async def test_a_failed_audit_write_does_not_fail_the_query(monkeypatch):
    """The query has already run and the answer is on its way; losing the audit
    row must not lose the answer."""
    async def _boom(**kwargs):
        raise RuntimeError('audit table is gone')

    monkeypatch.setattr(executor_module.DbQueryLogDao, 'acreate', _boom)
    monkeypatch.setattr(
        DatasourceExecutor,
        '_run_sync',
        staticmethod(lambda ds, stmt: QueryOutcome(statement=stmt, row_count=0)),
    )

    result = await DatasourceExecutor.execute(_datasource(), 'SELECT id FROM t')

    assert result.row_count == 0


# --- degradation is recorded (AC-19 / AC-20) --------------------------------


async def test_a_readonly_degradation_is_written_to_the_audit_row(monkeypatch):
    outcome = QueryOutcome(statement='SELECT id FROM t', row_count=0, readonly_degraded=True)
    recorded = _patch_sync(monkeypatch, outcome=outcome)

    await DatasourceExecutor.execute(_datasource('db2'), 'SELECT id FROM t')

    assert recorded[0]['readonly_degraded'] is True


async def test_a_guard_degradation_is_written_to_the_audit_row(monkeypatch):
    """AC-20: when the parser could not read the statement and the narrow tier
    decided, that must be visible afterwards."""
    recorded = _patch_sync(monkeypatch)
    # A real DM/Oracle MODEL clause: legal, unparseable, and passes the narrow tier.
    await DatasourceExecutor.execute(
        _datasource('dm'),
        'SELECT * FROM t MODEL RETURN UPDATED ROWS DIMENSION BY (a) MEASURES (b) RULES (b[1] = 1)',
    )

    # `_record` receives the DEGRADE_* marker; the int conversion happens at the
    # DAO write, one layer further in.
    assert recorded[0]['guard_degraded'] == 'parse_failed'
