"""Test-connection probe (spec AC-01 / AC-03 / AC-05).

The engine is the only faked object -- everything above it (URL assembly,
classification, redaction, the write-permission reading, the async bound) is
the real code. `create_engine` is the boundary the probe actually talks to, so
faking it there keeps the assertions about behaviour instead of about which
methods got called.

The AC-03 guarantee ("a failed test writes nothing") gets three layers of
protection, because it is the kind of requirement that is easy to satisfy today
and easy to break in a later refactor: armed tripwires on every write path
(which catch a call however it is imported), a source-level assertion, and the
behavioural assertions on the returned result.
"""

import asyncio
import inspect
from unittest.mock import patch

import pytest
from sqlalchemy.exc import NoSuchModuleError, OperationalError

from bisheng.datasource.domain.models.dao import (
    DbDatasourceDao,
    DbDatasourceTableDao,
    DbQueryFailureDao,
    DbQueryLogDao,
)
from bisheng.datasource.domain.services import connection_test
from bisheng.datasource.domain.services.connection_test import (
    CATEGORY_AUTH_FAILED,
    CATEGORY_DATABASE_MISSING,
    CATEGORY_DRIVER_MISSING,
    CATEGORY_HOST_UNREACHABLE,
    CATEGORY_OK,
    CATEGORY_TIMEOUT,
    CATEGORY_UNKNOWN,
    CATEGORY_UNSUPPORTED_ENGINE,
    ConnectionTester,
    ConnectionTestPayload,
)

PASSWORD = "Sup3r$ecret@db/1"


def _payload(**overrides) -> ConnectionTestPayload:
    values = {
        "engine": "mysql",
        "host": "10.0.0.7",
        "port": 3306,
        "database_name": "sales",
        "username": "app",
        "password": PASSWORD,
    }
    values.update(overrides)
    return ConnectionTestPayload(**values)


def _driver_error(message: str, *, dialect: str = "pymysql") -> OperationalError:
    """A SQLAlchemy `OperationalError` wrapping a driver exception, which is
    what `create_engine(...).connect()` raises in production."""
    return OperationalError("SELECT 1", {}, Exception(message))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, *, rows=(), execute_error=None):
        self._rows = list(rows)
        self._execute_error = execute_error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement):
        self.executed.append(statement)
        if self._execute_error:
            raise self._execute_error
        return _FakeResult(rows=self._rows)


class _FakeEngine:
    def __init__(self, connection=None, connect_error=None):
        self._connection = connection
        self._connect_error = connect_error
        self.disposed = False

    def connect(self):
        if self._connect_error:
            raise self._connect_error
        return self._connection

    def dispose(self):
        self.disposed = True


def _engine_factory(engine: _FakeEngine):
    return patch.object(connection_test, "create_engine", return_value=engine)


# --- the request payload never carries a secret in its repr ------------------


def test_payload_repr_hides_the_password():
    """The payload crosses FastAPI, whose validation errors render the input.
    A password echoed in a 422 body is a leak on a read path."""
    assert PASSWORD not in repr(_payload())
    assert PASSWORD not in str(_payload())


# --- outcomes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_connection_is_reported_and_closed():
    connection = _FakeConnection()
    engine = _FakeEngine(connection=connection)
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.ok is True
    assert result.category == CATEGORY_OK
    assert engine.disposed, "the probe must not leave the pooled connection open"


@pytest.mark.asyncio
async def test_unreachable_host():
    engine = _FakeEngine(connect_error=_driver_error("Can't connect to MySQL server on '10.0.0.7' (10061)"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.ok is False
    assert result.category == CATEGORY_HOST_UNREACHABLE


@pytest.mark.asyncio
async def test_bad_credentials():
    engine = _FakeEngine(connect_error=_driver_error("Access denied for user 'app'@'10.0.0.8' (using password: YES)"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.category == CATEGORY_AUTH_FAILED


@pytest.mark.asyncio
async def test_missing_database():
    engine = _FakeEngine(connect_error=_driver_error('FATAL:  database "sales" does not exist', dialect="psycopg2"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload(engine="postgresql"))

    assert result.category == CATEGORY_DATABASE_MISSING


@pytest.mark.asyncio
async def test_driver_error_that_mentions_a_timeout():
    engine = _FakeEngine(connect_error=_driver_error("connection timed out"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.category == CATEGORY_TIMEOUT


@pytest.mark.asyncio
async def test_unclassifiable_failure_stays_generic():
    engine = _FakeEngine(connect_error=_driver_error("something nobody has seen before"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.category == CATEGORY_UNKNOWN
    assert result.ok is False


@pytest.mark.asyncio
async def test_missing_driver_module():
    """`create_engine` raises this before any connection is attempted, which is
    the state DB2 is in on a host where its wheel is unavailable."""
    with patch.object(
        connection_test,
        "create_engine",
        side_effect=NoSuchModuleError("Can't load plugin: sqlalchemy.dialects:db2.ibm_db"),
    ):
        result = await ConnectionTester.test(_payload(engine="db2"))

    assert result.category == CATEGORY_DRIVER_MISSING


@pytest.mark.asyncio
async def test_unsupported_engine_is_rejected_without_connecting():
    """AC-06. The connection must not even be attempted -- asserted on the
    factory rather than on the URL, since the point is that no driver runs."""
    with patch.object(connection_test, "create_engine") as factory:
        result = await ConnectionTester.test(_payload(engine="sqlite", database_name="/tmp/x.db"))

    assert result.category == CATEGORY_UNSUPPORTED_ENGINE
    assert factory.call_count == 0


@pytest.mark.asyncio
async def test_slow_driver_is_bounded_even_when_it_ignores_the_timeout_hint(monkeypatch):
    """DB2 and DaMeng take no connect-timeout argument, so the await is bounded
    independently; otherwise an unreachable host holds a request open until the
    OS gives up."""
    monkeypatch.setattr(connection_test, "_GRACE_SECONDS", 0.05)

    def _hang(url, engine, connect_args):
        import time

        time.sleep(2)

    monkeypatch.setattr(ConnectionTester, "_probe", staticmethod(_hang))

    result = await asyncio.wait_for(ConnectionTester.test(_payload(), timeout=0.01), timeout=2)

    assert result.category == CATEGORY_TIMEOUT


# --- write permission (AC-05) ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grants,expected",
    [
        (("GRANT ALL PRIVILEGES ON *.* TO 'app'@'%'",), True),
        (("GRANT SELECT, INSERT ON `sales`.* TO 'app'@'%'",), True),
        (("GRANT USAGE ON *.* TO 'app'@'%'",), False),
        (("GRANT SELECT ON `sales`.* TO 'app'@'%'",), False),
        (("GRANT USAGE ON *.* TO 'app'@'%'", "GRANT SELECT ON `sales`.* TO 'app'@'%'"), False),
    ],
)
async def test_mysql_write_permission_is_read_from_the_grant_list(grants, expected):
    rows = [(grant,) for grant in grants]
    with _engine_factory(_FakeEngine(connection=_FakeConnection(rows=rows))):
        result = await ConnectionTester.test(_payload())

    assert result.writable is expected


@pytest.mark.asyncio
async def test_write_permission_from_a_catalog_row():
    """PostgreSQL/GaussDB answer with a row when a write privilege exists."""
    with _engine_factory(_FakeEngine(connection=_FakeConnection(rows=[(1,)]))):
        result = await ConnectionTester.test(_payload(engine="postgresql"))

    assert result.writable is True


@pytest.mark.asyncio
async def test_read_only_account_is_reported_as_such():
    with _engine_factory(_FakeEngine(connection=_FakeConnection(rows=[]))):
        result = await ConnectionTester.test(_payload(engine="oracle"))

    assert result.ok is True
    assert result.writable is False


@pytest.mark.asyncio
async def test_unreadable_catalog_reports_undetermined_never_read_only():
    """AC-05's warning must not be suppressed by a catalog the account cannot
    read: undetermined is not the same answer as read-only."""
    connection = _FakeConnection(execute_error=RuntimeError("relation does not exist"))
    with _engine_factory(_FakeEngine(connection=connection)):
        result = await ConnectionTester.test(_payload())

    assert result.ok is True
    assert result.writable is None


@pytest.mark.asyncio
async def test_engine_without_a_probe_reports_undetermined(monkeypatch):
    """The guard for an engine added to the registry with no probe written yet.
    Losing it would turn "not probed" into a failed connection."""
    monkeypatch.setattr(connection_test, "_WRITE_PROBES", {})
    with _engine_factory(_FakeEngine(connection=_FakeConnection(rows=[]))):
        result = await ConnectionTester.test(_payload())

    assert result.ok is True
    assert result.writable is None


@pytest.mark.asyncio
async def test_failed_connection_reports_no_write_permission():
    engine = _FakeEngine(connect_error=_driver_error("connection refused"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert result.writable is None


# --- the password does not survive the failure path --------------------------


@pytest.mark.asyncio
async def test_password_is_scrubbed_from_the_driver_message():
    """Some drivers echo the whole DSN in their authentication error, and that
    text is otherwise a free-form string nobody screens."""
    engine = _FakeEngine(
        connect_error=_driver_error(f"FATAL: password authentication failed (dsn=host=db password={PASSWORD})")
    )
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    assert PASSWORD not in result.detail
    assert PASSWORD not in repr(result)
    assert "password authentication failed" in result.detail, "the diagnosable part survives"


@pytest.mark.asyncio
async def test_the_generic_message_carries_no_connection_details():
    """What a caller shows without reading `detail`: a category-derived
    sentence, free of host, port, account and driver text."""
    engine = _FakeEngine(connect_error=_driver_error("Access denied for user 'app'@'10.0.0.7'"))
    with _engine_factory(engine):
        result = await ConnectionTester.test(_payload())

    for leak in ("10.0.0.7", "3306", "app", "sales"):
        assert leak not in result.message, result.message


# --- AC-03: nothing is written, on failure or on success ---------------------


def _write_tripwires():
    """Arm every write path reachable from this module.

    `patch.object` on the classes themselves, so it catches the call no matter
    how a future edit imports them -- which is the failure mode a test that
    mocked "the DAO I expect it to use" would miss. F058's review found three
    HIGH defects that passed 100+ green tests precisely because the layer under
    test was the mocked one.
    """
    return [
        patch.object(DbDatasourceDao, "acreate", side_effect=AssertionError("wrote a datasource")),
        patch.object(DbDatasourceDao, "aupdate", side_effect=AssertionError("wrote a datasource")),
        patch.object(DbDatasourceDao, "adelete", side_effect=AssertionError("deleted a datasource")),
        patch.object(DbDatasourceTableDao, "acreate", side_effect=AssertionError("wrote a datasource table")),
        patch.object(
            DbDatasourceTableDao,
            "areplace_snapshot",
            side_effect=AssertionError("wrote a datasource table"),
        ),
        patch.object(
            DbDatasourceTableDao,
            "aupdate_business_desc",
            side_effect=AssertionError("wrote a datasource table"),
        ),
        patch.object(DbQueryLogDao, "acreate", side_effect=AssertionError("wrote a query log")),
        patch.object(
            DbQueryFailureDao,
            "acreate_best_effort",
            side_effect=AssertionError("wrote a failure record"),
        ),
        patch(
            "bisheng.datasource.domain.models.dao.get_async_db_session",
            side_effect=AssertionError("opened a session"),
        ),
    ]


@pytest.mark.asyncio
async def test_failed_probe_writes_nothing():
    """AC-03. The implementation this guards against is the natural one: save
    the datasource first, then test it, so the row is already there when the
    test fails."""
    engine = _FakeEngine(connect_error=_driver_error("connection refused"))
    tripwires = _write_tripwires()
    for tripwire in tripwires:
        tripwire.start()
    try:
        with _engine_factory(engine):
            result = await ConnectionTester.test(_payload())
    finally:
        for tripwire in reversed(tripwires):
            tripwire.stop()

    assert result.ok is False


@pytest.mark.asyncio
async def test_successful_probe_writes_nothing_either():
    """The same tripwires on the happy path: the probe reports, it does not
    persist the connection info it was handed."""
    tripwires = _write_tripwires()
    for tripwire in tripwires:
        tripwire.start()
    try:
        with _engine_factory(_FakeEngine(connection=_FakeConnection())):
            result = await ConnectionTester.test(_payload())
    finally:
        for tripwire in reversed(tripwires):
            tripwire.stop()

    assert result.ok is True


def test_the_probe_module_has_no_database_write_path():
    """Belt and braces for the tripwires: they cover the write paths that exist
    today, this covers a path added later that they would not know about.
    `test_no_polling.py` uses the same static-assertion approach for AC-15."""
    source = inspect.getsource(connection_test)
    for forbidden in ("get_async_db_session", "session.add", "Dao", ".commit()"):
        assert forbidden not in source, f"the probe must not be able to write: found {forbidden}"
