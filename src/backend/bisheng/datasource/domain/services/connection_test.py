"""Test-connection probe (spec AC-01 / AC-03 / AC-05).

Three properties this module exists to guarantee:

1. **It writes nothing, on failure or on success.** AC-03 says a failed test
   produces no persisted row, and the natural implementation -- save the
   datasource, then test it -- violates that. This module touches no DAO and
   opens no database session for the platform's own storage: it takes a
   connection payload, opens a connection to the *target* database, closes it,
   and returns. The guarantee is therefore structural rather than a discipline
   the caller has to maintain, and `test_test_connection.py` asserts it with
   tripwires over the write paths.
2. **The result is decidable.** Success or failure, plus a category the caller
   can branch on and turn into a specific message. A driver's own text is not
   decidable -- it varies by version and language.
3. **Write permission is detected, not enforced.** AC-05 wants a warning at
   save time when the account is not read-only, and explicitly does NOT want
   that warning to block saving: refusing writable accounts would push
   customers to bypass the feature (design decision 8 -- the read-only account
   is the primary boundary, this is advice, not a gate). So the probe reports
   `writable` and never refuses.

**On the per-engine write probes.** Each reads the engine's own catalog of
granted privileges -- a read-only query that cannot change anything. They have
been written against each engine's documented catalog interface but have NOT
been exercised against live servers of all six engines (no such servers are
available to this project outside CI). The failure direction is deliberate:
if the catalog query is wrong, unreadable, or unsupported on a given
deployment, the answer is `writable = None` ("undetermined") and a log line,
NEVER `False`. A false "read-only" would suppress exactly the warning AC-05
asks for, which is the only harmful direction here.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, create_engine
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.pool import NullPool

from bisheng.common.errcode.datasource import DatasourceUnsupportedEngineError
from bisheng.datasource.domain.services.credentials import redact
from bisheng.datasource.domain.services.engine_registry import (
    build_url,
    ensure_db2_driver_loadable,
    spec_for,
)

CATEGORY_OK = "ok"
CATEGORY_UNSUPPORTED_ENGINE = "unsupported_engine"
CATEGORY_DRIVER_MISSING = "driver_missing"
CATEGORY_AUTH_FAILED = "auth_failed"
CATEGORY_DATABASE_MISSING = "database_missing"
CATEGORY_HOST_UNREACHABLE = "host_unreachable"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_UNKNOWN = "unknown"

# The caller renders these from the category; they are the fallback for a caller
# that just wants something printable. Free of connection details by
# construction -- see the endpoint's docstring for why the driver's own text
# travels separately.
_MESSAGES = {
    CATEGORY_OK: "Connection succeeded",
    CATEGORY_UNSUPPORTED_ENGINE: "This database engine is not supported",
    CATEGORY_DRIVER_MISSING: "The database driver for this engine is not installed on the server",
    CATEGORY_AUTH_FAILED: "The database rejected the account or password",
    CATEGORY_DATABASE_MISSING: "The database name does not exist or is not reachable with this account",
    CATEGORY_HOST_UNREACHABLE: "The host or port could not be reached",
    CATEGORY_TIMEOUT: "The connection attempt timed out",
    CATEGORY_UNKNOWN: "The connection failed",
}

# Seconds. The probe passes this to the driver where the driver accepts one
# (see `EngineSpec.connect_args`) and bounds the await with `_GRACE_SECONDS`
# more, so a driver that ignores the hint still cannot hold a request open
# indefinitely.
PROBE_TIMEOUT_SECONDS = 10
_GRACE_SECONDS = 5

# Ordered: a "connection timed out" message also contains "connection", and a
# bad-password message from some drivers mentions the connection attempt. The
# first match wins, so the more specific categories come first.
_CATEGORY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        CATEGORY_TIMEOUT,
        ("timed out", "timeout", "ora-12170", "connection has been abandoned"),
    ),
    (
        CATEGORY_AUTH_FAILED,
        (
            "access denied",
            "authentication failed",
            "password authentication",
            "invalid password",
            "invalid username",
            "login failed",
            "not authorized",
            "not authorised",
            "ora-01017",
            "ora-28000",
            "sql30082",
        ),
    ),
    (
        CATEGORY_DATABASE_MISSING,
        (
            "unknown database",
            "does not exist",
            "database not found",
            "cannot open database",
            "invalid database",
            "ora-12514",
            "sqlstate=08004",
        ),
    ),
    (
        CATEGORY_HOST_UNREACHABLE,
        (
            "can't connect",
            "cannot connect",
            "could not connect",
            "connection refused",
            "connection reset",
            "unreachable",
            "no route to host",
            "name or service not known",
            "getaddrinfo",
            "nodename nor servname",
            "host is down",
        ),
    ),
)

# Read-only catalog queries answering "does this account hold a write
# privilege?". Kept per engine rather than unified: the catalogs have nothing in
# common, and a unified query would have to be a lowest common denominator that
# is wrong on all six.
#
# `True` means a write privilege was found; `False` means the catalog was read
# successfully and holds none -- which is the account being genuinely read-only,
# not merely unprobed.
_WRITE_PROBES: dict[str, str] = {
    # MySQL/MariaDB: the account's own grant list. `GRANT USAGE` alone (the
    # no-privileges grant every account is created with) contains no write word.
    "mysql": "SHOW GRANTS FOR CURRENT_USER()",
    # PostgreSQL / openGauss: superusers hold every privilege implicitly and
    # appear in no grant view, so they are checked directly. Table owners do
    # appear in `table_privileges`, so ownership needs no separate branch.
    "postgresql": (
        "SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper "
        "UNION ALL "
        "SELECT 1 FROM information_schema.table_privileges "
        "WHERE grantee = current_user "
        "AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') "
        "LIMIT 1"
    ),
    "gaussdb": (
        "SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper "
        "UNION ALL "
        "SELECT 1 FROM information_schema.table_privileges "
        "WHERE grantee = current_user "
        "AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') "
        "LIMIT 1"
    ),
    # Oracle / DaMeng: `session_privs` covers system and role-derived grants
    # (DBA lands here), `user_tab_privs` covers per-object grants. DM implements
    # the Oracle data dictionary -- the same assumption the schema collector
    # already relies on for table comments.
    "oracle": (
        "SELECT 1 FROM ("
        "SELECT privilege FROM session_privs "
        "UNION ALL "
        "SELECT privilege FROM user_tab_privs"
        ") p WHERE p.privilege IN ("
        "'INSERT', 'UPDATE', 'DELETE', "
        "'INSERT ANY TABLE', 'UPDATE ANY TABLE', 'DELETE ANY TABLE', "
        "'CREATE ANY TABLE', 'DROP ANY TABLE', 'ALTER ANY TABLE') "
        "FETCH FIRST 1 ROW ONLY"
    ),
    "dm": (
        "SELECT 1 FROM ("
        "SELECT privilege FROM session_privs "
        "UNION ALL "
        "SELECT privilege FROM user_tab_privs"
        ") p WHERE p.privilege IN ("
        "'INSERT', 'UPDATE', 'DELETE', "
        "'INSERT ANY TABLE', 'UPDATE ANY TABLE', 'DELETE ANY TABLE', "
        "'CREATE ANY TABLE', 'DROP ANY TABLE', 'ALTER ANY TABLE') "
        "FETCH FIRST 1 ROW ONLY"
    ),
    # DB2 LUW: catalog view of table authorities. 'Y' is granted, 'N' is not,
    # 'G' is grantable -- 'G' implies held, but the view reports the held
    # privilege separately, so only 'Y' is tested.
    "db2": (
        "SELECT 1 FROM SYSCAT.TABAUTH "
        "WHERE GRANTEE IN (CURRENT USER, 'PUBLIC') "
        "AND (INSERTAUTH = 'Y' OR UPDATEAUTH = 'Y' OR DELETEAUTH = 'Y') "
        "FETCH FIRST 1 ROW ONLY"
    ),
}

# Write-word screen for the MySQL grant text.
_MYSQL_WRITE_WORDS = (
    "all privileges",
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "grant option",
    "references",
)


class ConnectionTestPayload(BaseModel):
    """Connection details for a datasource that may not exist yet (AC-01).

    There is deliberately no `datasource_id` variant: testing a stored row would
    mean reading the tenant's datasource table through a permission check (C4)
    and would give this module a database dependency, weakening the AC-03
    guarantee for no gain -- the form that needs a test button is the one that
    has not been saved yet.

    Not named `Test*`: pytest tries to collect any class whose name starts with
    `Test`, and this one has a constructor.
    """

    engine: str
    host: str
    database_name: str
    username: str
    port: int | None = None
    password: str = Field(default="", repr=False)


@dataclass(frozen=True)
class ConnectionTestResult:
    """`category` is the branchable part; `message` and `detail` are for humans.

    `detail` carries the driver's own error text -- useful to the administrator
    who typed the connection details, and not shown anywhere else. The endpoint
    that returns it is admin-gated for this reason, and the password is
    scrubbed out of it before it leaves :meth:`ConnectionTester.test`.
    """

    ok: bool
    category: str
    message: str
    detail: str = ""
    writable: bool | None = None


class ConnectionTester:
    """Opens a connection to the target database, inspects it, closes it."""

    @classmethod
    async def test(
        cls, payload: ConnectionTestPayload, *, timeout: float = PROBE_TIMEOUT_SECONDS
    ) -> ConnectionTestResult:
        """Probe ``payload``. Never raises, never writes.

        Runs the blocking driver work in a thread: this is called from request
        handlers, and a connect attempt against an unreachable host would
        otherwise stall the event loop for every other request in the process
        (the failure mode AC-21 calls out for queries).
        """
        try:
            url = build_url(
                engine=payload.engine,
                host=payload.host,
                port=payload.port,
                database=payload.database_name,
                username=payload.username,
                password=payload.password,
            )
        except DatasourceUnsupportedEngineError:
            return ConnectionTestResult(False, CATEGORY_UNSUPPORTED_ENGINE, _MESSAGES[CATEGORY_UNSUPPORTED_ENGINE])

        spec = spec_for(payload.engine)
        try:
            writable = await asyncio.wait_for(
                asyncio.to_thread(cls._probe, url, spec.engine, spec.connect_args),
                timeout=timeout + _GRACE_SECONDS,
            )
        except TimeoutError:
            # `asyncio.TimeoutError` is an alias of the builtin on 3.11+.
            return ConnectionTestResult(False, CATEGORY_TIMEOUT, _MESSAGES[CATEGORY_TIMEOUT])
        except Exception as exc:
            return cls._failure(exc, payload)

        return ConnectionTestResult(True, CATEGORY_OK, _MESSAGES[CATEGORY_OK], writable=writable)

    # --- blocking half (runs in a worker thread) ------------------------------

    @staticmethod
    def _probe(url: URL, engine: str, connect_args) -> bool | None:
        """Connect, read what we need, close. Returns the write-permission answer.

        `NullPool` because this is a one-shot check: a pooled connection would
        outlive the request that created it, holding an idle session on the
        customer's database.
        """
        # Before the driver is imported, not after: on Windows the DB2 DBAPI
        # cannot load until its vendored DLL directory is on the search path,
        # and `create_engine` is what triggers that import. No-op off Windows
        # and for every other engine.
        ensure_db2_driver_loadable()
        probe_engine = create_engine(url, connect_args=dict(connect_args), poolclass=NullPool)
        try:
            with probe_engine.connect() as connection:
                writable = ConnectionTester._detect_write_permission(connection, engine)
        finally:
            probe_engine.dispose()
        return writable

    @staticmethod
    def _detect_write_permission(connection: Connection, engine: str) -> bool | None:
        """AC-05. `None` means undetermined, and is the answer on every doubt.

        The catch is deliberately broad, and this is the one place in the module
        where that is right: the answer here is advisory, so a failure must
        degrade to "undetermined" rather than propagate. A narrower catch would
        let an unexpected driver exception turn a *successful* connection into a
        reported failure -- the probe would be reporting on itself, not on the
        database.
        """
        try:
            sql = _WRITE_PROBES.get(engine)
            if not sql:
                logger.info(
                    "write-permission probe not implemented for engine={}; reporting undetermined",
                    engine,
                )
                return None

            rows = connection.execute(text(sql)).fetchall()
            if engine == "mysql":
                return ConnectionTester._mysql_grants_allow_writes(rows)
            return bool(rows)
        except Exception:
            # The catalog view may not exist on this deployment, or the account
            # may not be allowed to read it. Undetermined -- never "read-only":
            # a wrong `False` would suppress the warning AC-05 asks for.
            logger.opt(exception=True).info(
                "write-permission probe failed for engine={}; reporting undetermined", engine
            )
            return None

    @staticmethod
    def _mysql_grants_allow_writes(rows) -> bool:
        """MySQL returns its grants as text, so the answer is read from that.

        A read-only account's list is somewhere between `GRANT USAGE ON *.*`
        and a set of `SELECT` grants; any write verb anywhere in it means the
        account is not read-only.
        """
        for row in rows:
            granted = " ".join(str(value) for value in row).lower()
            if any(word in granted for word in _MYSQL_WRITE_WORDS):
                return True
        return False

    # --- classification -------------------------------------------------------

    @classmethod
    def _failure(cls, exc: Exception, payload: ConnectionTestPayload) -> ConnectionTestResult:
        """Turn a driver exception into a decidable category plus safe text."""
        detail = redact(str(exc), secrets=[payload.password])
        category = cls._classify(exc, detail)
        logger.warning(
            "datasource test-connection failed: engine={} host={} category={} detail={}",
            payload.engine,
            payload.host,
            category,
            detail,
        )
        return ConnectionTestResult(
            False, category, _MESSAGES.get(category, _MESSAGES[CATEGORY_UNKNOWN]), detail=detail
        )

    @staticmethod
    def _classify(exc: Exception, detail: str) -> str:
        if isinstance(exc, DatasourceUnsupportedEngineError):
            return CATEGORY_UNSUPPORTED_ENGINE
        if isinstance(exc, (ImportError, ModuleNotFoundError, NoSuchModuleError)):
            # The dialect's driver module is not installed. `NoSuchModuleError`
            # is the SQLAlchemy-side spelling of the same condition, raised by
            # `create_engine` before it ever reaches the driver.
            return CATEGORY_DRIVER_MISSING

        lowered = detail.lower()
        for category, markers in _CATEGORY_MARKERS:
            if any(marker in lowered for marker in markers):
                return category
        return CATEGORY_UNKNOWN


def result_dict(result: ConnectionTestResult) -> dict:
    """Response shape for the endpoint. Byte-identical to the dataclass."""
    return asdict(result)
