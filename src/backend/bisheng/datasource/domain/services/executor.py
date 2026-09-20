"""Run one validated SELECT against a customer database.

Four constraints shape everything here, and they are all about what this
module must NOT do:

**It must not block the event loop** (spec AC-21). Every engine driver in the
supported set is synchronous, so the connection and the fetch run in a worker
thread via `asyncio.to_thread`. "Make the chat wait behind someone else's slow
query" is exactly the failure this prevents, and design decision 12 records the
existing `sql_agent` as the counter-example: its `arun` delegates to the sync
`run`, so a slow query stalls every other conversation on the process.

**It must not be the only thing keeping a query read-only** (design decision 8).
The real boundary is the database account's own permissions; the read-only
session attempted here and the platform's SQL guard are the second and third
layers. A read-only session is attempted and its absence is RECORDED rather
than treated as a failure (spec AC-19) — refusing to execute because an engine
cannot do `SET TRANSACTION READ ONLY` would take the feature offline on that
engine for no security gain, since the account permission still applies.

**It must not store the result data** (spec AC-23 / INV-19 clause 3). The
`db_query_log` row carries the statement, the tables, the row count and the
outcome — never the rows. Counts and status are what make truncation and
failures auditable; the rows themselves are the customer's data and this
module has no field to put them in.

**It must not run indefinitely.** A row cap and a timeout bound every query
(spec AC-18), and hitting the cap TRUNCATES with a flag rather than failing —
the user gets what was fetched, and the answer says it was cut short.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, create_engine
from sqlalchemy.pool import NullPool

from bisheng.common.errcode.datasource import (
    DatasourceExecutionTimeoutError as ExecutionTimeoutError,
)
from bisheng.common.errcode.datasource import (
    DatasourceStatementRejectedError,
)
from bisheng.datasource.domain.models.dao import DbQueryLogDao
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services.engine_registry import (
    ensure_db2_driver_loadable,
    from_datasource,
    spec_for,
)
from bisheng.datasource.domain.services.sql_guard import DEGRADE_NONE, SqlGuard

#: Rows returned to the caller, and the same number persisted for the answer's
#: result table. One constant on purpose: spec AC-35 forbids a second trimming
#: rule, and two caps would eventually disagree.
MAX_ROWS = 200

#: Wall-clock ceiling for one query, including connection and fetch. A query
#: that outlives this is reported as a timeout rather than left to hold a
#: worker thread (and a customer connection) open.
QUERY_TIMEOUT_SECONDS = 30

# Read-only session statements, per engine. The engines that cannot express one
# simply have no entry: absence is the degradation case in AC-19, and inventing
# a statement they do not understand would turn a degraded query into a failed
# one.
_READONLY_STATEMENTS: dict[str, str] = {
    'mysql': 'SET TRANSACTION READ ONLY',
    'postgresql': 'SET TRANSACTION READ ONLY',
    'gaussdb': 'SET TRANSACTION READ ONLY',
    'oracle': 'SET TRANSACTION READ ONLY',
    'dm': 'SET TRANSACTION READ ONLY',
    # DB2 has no session-level equivalent that is safe to issue here; the
    # account's own permissions remain the boundary.
}


@dataclass
class QueryOutcome:
    """What the caller gets back. Never carries unbounded data.

    ``rows`` is capped at `MAX_ROWS`; ``truncated`` says whether the cap bit.
    ``tables`` is a summary the audit trail uses, not the data itself.
    """

    statement: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    readonly_degraded: bool = False
    guard_degraded: str = DEGRADE_NONE
    duration_ms: int = 0


def tables_from_statement(statement: str) -> list[str]:
    """Table names mentioned in the statement.

    Deliberately a coarse parse rather than the guard's AST: this runs on
    the success path where the statement already passed validation, and a
    name it misses costs a less informative log line, not a wrong decision.

    Public because the citation anchor needs the same summary across a module
    boundary (F061 T-31). It is the table list the USER reads, so a second
    parser living next to it would be a second answer to "which tables did this
    touch" -- and the two would disagree the first time one was improved.
    """
    import re

    found = re.findall(r'\b(?:from|join)\s+([A-Za-z_][\w.$]*)', statement, flags=re.IGNORECASE)
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


class DatasourceExecutor:
    """Executes one already-validated statement. Stateless."""

    @classmethod
    async def execute(
        cls,
        datasource: DbDatasource,
        statement: str,
        *,
        user_id: int | None = None,
        injection_mode: str | None = None,
    ) -> QueryOutcome:
        """Validate, run, and record. The only entry point.

        Validation happens HERE rather than at the call site so no caller can
        forget it: a path that skips `SqlGuard` would be a hole straight to the
        database, and "remember to validate first" is not a property a caller
        can be trusted to preserve across refactors.
        """
        verdict = SqlGuard.validate(statement, engine=datasource.engine)

        if not verdict.allowed:
            # A rejected statement is recorded too: an admin debugging "why
            # can't the model query this table" needs to see that the guard
            # refused it and why.
            await cls._record(
                datasource,
                statement,
                status='blocked',
                tables=[],
                row_count=None,
                truncated=False,
                readonly_degraded=False,
                guard_degraded=verdict.degraded,
                injection_mode=injection_mode,
                duration_ms=0,
                user_id=user_id,
            )
            raise DatasourceStatementRejectedError(
                msg=verdict.reason or DatasourceStatementRejectedError.Msg
            )

        started = time.monotonic()
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(cls._run_sync, datasource, statement),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            await cls._record(
                datasource,
                statement,
                status='failed',
                tables=[],
                row_count=None,
                truncated=False,
                readonly_degraded=False,
                guard_degraded=verdict.degraded,
                injection_mode=injection_mode,
                duration_ms=int((time.monotonic() - started) * 1000),
                user_id=user_id,
            )
            raise ExecutionTimeoutError() from exc
        except Exception:
            await cls._record(
                datasource,
                statement,
                status='failed',
                tables=[],
                row_count=None,
                truncated=False,
                readonly_degraded=False,
                guard_degraded=verdict.degraded,
                injection_mode=injection_mode,
                duration_ms=int((time.monotonic() - started) * 1000),
                user_id=user_id,
            )
            raise

        outcome.guard_degraded = verdict.degraded
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        await cls._record(
            datasource,
            statement,
            status='truncated' if outcome.truncated else 'success',
            tables=tables_from_statement(statement),
            row_count=outcome.row_count,
            truncated=outcome.truncated,
            readonly_degraded=outcome.readonly_degraded,
            guard_degraded=verdict.degraded,
            injection_mode=injection_mode,
            duration_ms=outcome.duration_ms,
            user_id=user_id,
        )
        return outcome

    # --- the blocking half ---------------------------------------------------

    @staticmethod
    def _run_sync(datasource: DbDatasource, statement: str) -> QueryOutcome:
        """Connect, attempt a read-only session, fetch at most MAX_ROWS+1 rows.

        `MAX_ROWS + 1` is deliberate: fetching exactly the cap cannot tell
        "there were 200 rows" from "there were more". The extra row is the
        probe, and it is dropped before returning.
        """
        ensure_db2_driver_loadable()
        spec = spec_for(datasource.engine)
        url: URL = from_datasource(datasource)
        engine = create_engine(url, connect_args=dict(spec.connect_args), poolclass=NullPool)
        try:
            with engine.connect() as connection:
                readonly_degraded = DatasourceExecutor._begin_readonly(connection, datasource.engine)
                result = connection.execute(text(statement))
                columns = list(result.keys())
                fetched = result.fetchmany(MAX_ROWS + 1)
                connection.rollback()  # nothing was written; release locks promptly
        finally:
            engine.dispose()

        truncated = len(fetched) > MAX_ROWS
        rows = [list(row) for row in fetched[:MAX_ROWS]]
        return QueryOutcome(
            statement=statement,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            readonly_degraded=readonly_degraded,
        )

    @staticmethod
    def _begin_readonly(connection: Connection, engine: str) -> bool:
        """Try to open a read-only session. True when it had to be skipped.

        Best-effort by design (AC-19): the account's own permissions are the
        primary boundary, so an engine that cannot do this executes normally
        and the caller records that it did. Raising here would make the feature
        unusable on that engine while adding no protection.
        """
        statement = _READONLY_STATEMENTS.get(engine)
        if statement is None:
            return True
        try:
            connection.execute(text(statement))
            return False
        except Exception:
            logger.debug('read-only session unavailable on {}; executing without it', engine)
            return True

    # --- the record ----------------------------------------------------------

    @staticmethod
    async def _record(
        datasource: DbDatasource,
        statement: str,
        *,
        status: str,
        tables: list[str],
        row_count: int | None,
        truncated: bool,
        readonly_degraded: bool,
        guard_degraded: str,
        injection_mode: str | None,
        duration_ms: int,
        user_id: int | None,
    ) -> None:
        """Write the audit row. Never carries result data (AC-23 / INV-19 ③).

        Best-effort: the query has already run and its answer is on its way, so
        a failed audit write must not fail the turn. It is `logger.warning`
        rather than silent because an audit trail that stops recording without
        anyone noticing is worse than one that reports its own gaps.
        """
        try:
            await DbQueryLogDao.acreate(
                {
                    'tenant_id': datasource.tenant_id,
                    'user_id': user_id,
                    'datasource_id': datasource.id,
                    'tables': tables,
                    'statement': statement,
                    'status': status,
                    'row_count': row_count,
                    'truncated': 1 if truncated else 0,
                    'readonly_degraded': 1 if readonly_degraded else 0,
                    'guard_degraded': 1 if guard_degraded != DEGRADE_NONE else 0,
                    'injection_mode': injection_mode,
                    'duration_ms': duration_ms,
                }
            )
        except Exception:
            logger.opt(exception=True).warning(
                'datasource query log write failed for datasource={}', datasource.id
            )
