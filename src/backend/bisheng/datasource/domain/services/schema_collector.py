"""Read a datasource's table structure — and nothing else.

Spec AC-13 draws a hard line here: the platform collects the SHAPE of a table
(names, types, in-database comments) and never its ROWS. No sample values, no
`SELECT ... LIMIT 1` to guess at a column's meaning. That is why every call
below goes through SQLAlchemy's reflection (``inspect()``), which issues
catalog queries only; there is no code path in this module that could read
user data even if someone wanted it to.

Why that matters beyond tidiness: collecting sample values would copy customer
data into the platform's own database, where the datasource's authorization
model (which watches the customer DB's account, not the platform's tables)
would no longer apply. The line is what keeps "authorized to query" and
"authorized to read what we copied" the same thing.

AC-14 used to carve out an exception for DaMeng, on the belief that its
comments were not collectible. That was measured and is false — `dmSQLAlchemy`
implements `get_table_comment` and its column reflection already selects
`ALL_COL_COMMENTS.comments`. So there is no engine-specific branch here; the
"comment unavailable" path is a general fallback for any engine whose catalog
comes back empty, and the UI states it rather than silently leaving blanks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Connection, create_engine
from sqlalchemy.pool import NullPool

from bisheng.common.errcode.datasource import DatasourceSchemaCollectFailedError
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services.engine_registry import (
    ensure_db2_driver_loadable,
    from_datasource,
    spec_for,
)

#: How many tables one collect will enumerate. A schema with more than this is
#: rare, and an unbounded `get_table_names` on a large catalog holds a
#: connection open for a long time. When the cap bites, `truncated` says so --
#: the caller reports it rather than the admin wondering why tables are missing.
MAX_TABLES = 2000


@dataclass(frozen=True)
class TableStructure:
    """One table as the database describes it."""

    table_name: str
    comment: str | None
    columns: list[dict[str, Any]]


@dataclass(frozen=True)
class CollectResult:
    tables: list[TableStructure]
    truncated: bool = False


def list_tables(datasource: DbDatasource) -> list[str]:
    """The tables and views the configured account can see (spec AC-09).

    `get_view_names` is included deliberately: a customer's reporting layer is
    often views, and a datasource that could only see base tables would answer
    "that table does not exist" for half their schema.

    "Can see" is literal -- this reflects what the ACCOUNT can reach, which is
    also the boundary the whole feature operates under (INV-19 clause 4: the
    platform applies no row-level filtering, so the account is the limit).
    """
    return _with_connection(datasource, _list_tables)


def collect(datasource: DbDatasource, table_names: list[str] | None = None) -> CollectResult:
    """Collect structure snapshots for `table_names`, or for every visible table.

    Passing an explicit list is the normal path (the admin ticked boxes); None
    means "everything visible", used when building the first snapshot of a
    freshly created datasource.
    """
    return _with_connection(datasource, lambda conn, inspect_engine: _collect(conn, inspect_engine, table_names))


def _with_connection(datasource: DbDatasource, fn):
    """Open one connection, run `fn`, close it.

    Mirrors `connection_test`: `NullPool` because nothing here is long-lived,
    and a pooled connection would idle on the customer's database after the
    request that created it.

    The try/finally is what makes the failure path honest -- a collect that
    half-succeeds must not leak the connection, and the caller needs an error
    code rather than a driver exception (spec AC-03's sibling for collection).
    """
    ensure_db2_driver_loadable()
    spec = spec_for(datasource.engine)
    url: URL = from_datasource(datasource)
    engine = create_engine(url, connect_args=dict(spec.connect_args), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return fn(connection, inspect(connection))
    except DatasourceSchemaCollectFailedError:
        raise
    except Exception as exc:
        # The message says which datasource, not what the driver said: a driver
        # error can carry the host, port and account, none of which may reach
        # the caller (INV-19 clause 1).
        logger.opt(exception=True).warning(
            'schema collect failed for datasource={}', getattr(datasource, 'id', None)
        )
        raise DatasourceSchemaCollectFailedError() from exc
    finally:
        engine.dispose()


def _list_tables(connection: Connection, inspector) -> list[str]:
    names = list(inspector.get_table_names())
    names.extend(inspector.get_view_names())
    # Sorted for a stable UI; duplicates are possible when a name is both a
    # table and a view on engines that allow it.
    return sorted(set(names))


def _collect(connection: Connection, inspector, table_names: list[str] | None) -> CollectResult:
    wanted = table_names if table_names is not None else sorted(
        set(inspector.get_table_names()) | set(inspector.get_view_names())
    )
    truncated = len(wanted) > MAX_TABLES

    tables = [_describe(inspector, name) for name in wanted[:MAX_TABLES]]
    return CollectResult(tables=tables, truncated=truncated)


def _describe(inspector, table_name: str) -> TableStructure:
    """One table's shape. Never fails the whole collect on its own.

    A single unreadable table (a view whose definition is broken, a synonym
    pointing nowhere) should cost that table's entry, not the entire run --
    the admin can see it is missing and re-collect after fixing it.
    """
    comment = None
    columns: list[dict[str, Any]] = []
    try:
        # SQLAlchemy returns a DICT (`{'text': ...}`), not a string. Storing it
        # raw would persist "{'text': '订单表'}" and put that in front of the
        # model. Caught by the round-trip test, not by reading the docs.
        raw_comment = inspector.get_table_comment(table_name)
        if isinstance(raw_comment, dict):
            comment = raw_comment.get('text')
        elif raw_comment is not None:
            comment = str(raw_comment)
    except Exception:
        # Documented as unsupported on some dialects; AC-14 treats a missing
        # comment as "fill it in by hand", not as a failure.
        logger.debug('table comment unavailable for {}', table_name)

    try:
        for column in inspector.get_columns(table_name):
            columns.append(
                {
                    'name': column.get('name'),
                    'type': str(column.get('type')) if column.get('type') is not None else None,
                    # Present on every dialect that reports comments; absent
                    # (not empty) elsewhere, and the UI distinguishes the two.
                    'comment': column.get('comment'),
                }
            )
    except Exception:
        logger.opt(exception=True).warning('columns unavailable for table {}', table_name)

    return TableStructure(table_name=table_name, comment=comment, columns=columns)
