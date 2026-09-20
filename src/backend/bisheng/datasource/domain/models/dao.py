"""DAOs for the four F061 tables.

**Every read runs inside ``strict_tenant_filter()``.** Datasources are
tenant-scoped configuration: spec AC-50 requires cross-tenant invisibility and
AC-47 explicitly rules out cross-tenant sharing. The global listener's default
for a Child tenant is the IN-list ``WHERE tenant_id IN (child, ROOT)`` — correct
for *shared* resources, wrong here, and it would hand a Child admin Root's
datasources (plus, in Cloud, every sibling tenant's).

The same trap F058 documented applies: the listener filters SELECTs only
(``tenant_filter.py``: ``if not orm_execute_state.is_select: return``). A SELECT
that returns a Root row, followed by a dirty-flush UPDATE on that instance, is
never re-checked — and ids are small integers, so guessing one is trivial. The
narrowing has to happen on the read that produced the instance.

Same idiom as ``doc_extract/domain/models/extract_template.py`` and
``linsight/domain/models/linsight_skill.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.models.datasource_table import DbDatasourceTable
from bisheng.datasource.domain.models.query_failure import DbQueryFailure
from bisheng.datasource.domain.models.query_log import DbQueryLog


def _rows(result) -> list:
    """Unwrap ORM results.

    The tenant filter's ``do_orm_execute`` hook rewraps SELECTs into Row tuples
    whose single element is the entity. Accessing the entity directly then
    raises KeyError -- the failure F056 hit in production and recorded as its
    pitfall 11. ``row[0]`` is correct whether or not the hook ran.
    """
    return [row[0] for row in result.all()]


class DbDatasourceDao:
    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> DbDatasource:
        datasource = DbDatasource(**data)
        async with get_async_db_session() as session:
            session.add(datasource)
            await session.commit()
            await session.refresh(datasource)
            return datasource

    @classmethod
    async def aget_by_id(cls, datasource_id: int) -> DbDatasource | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(DbDatasource).where(DbDatasource.id == datasource_id)
                )
                rows = _rows(result)
                return rows[0] if rows else None

    @classmethod
    async def aget_by_name(cls, name: str) -> DbDatasource | None:
        """Used for the duplicate-name check (spec AC-... name uniqueness)."""
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(DbDatasource).where(DbDatasource.name == name)
                )
                rows = _rows(result)
                return rows[0] if rows else None

    @classmethod
    async def alist(
        cls,
        *,
        ids: list[int] | None = None,
        enabled_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DbDatasource], int]:
        """Page datasources newest-first.

        ``ids`` is how the caller narrows to what the user is authorized for:
        authorization lives in OpenFGA, not in a column here, so the caller
        resolves the id set and passes it in. ``ids=[]`` is a valid, meaningful
        input meaning "authorized for nothing" and must return empty rather
        than falling through to "no filter".
        """
        if ids is not None and not ids:
            return [], 0

        clauses = []
        if ids is not None:
            clauses.append(DbDatasource.id.in_(ids))
        if enabled_only:
            clauses.append(DbDatasource.enabled == 1)

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                total = await session.scalar(
                    select(func.count()).select_from(DbDatasource).where(*clauses)
                )
                result = await session.exec(
                    select(DbDatasource)
                    .where(*clauses)
                    .order_by(DbDatasource.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                return _rows(result), int(total or 0)

    @classmethod
    async def aupdate(cls, datasource: DbDatasource) -> DbDatasource:
        async with get_async_db_session() as session:
            session.add(datasource)
            await session.commit()
            await session.refresh(datasource)
            return datasource

    @classmethod
    async def adelete(cls, datasource_id: int) -> None:
        """Hard delete.

        Datasources are not soft-deleted: spec AC-62 wants the datasource gone
        for future turns while the history (answers, citations, execution
        records) stays readable. Those records reference the datasource by id
        with no FK, so removing the row achieves exactly that.
        """
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(DbDatasource).where(DbDatasource.id == datasource_id)
                )
                rows = _rows(result)
                if rows:
                    await session.delete(rows[0])
                    await session.commit()


class DbDatasourceTableDao:
    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> DbDatasourceTable:
        row = DbDatasourceTable(**data)
        async with get_async_db_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def alist_by_datasource(
        cls, datasource_id: int, *, enabled_only: bool = False
    ) -> list[DbDatasourceTable]:
        clauses = [DbDatasourceTable.datasource_id == datasource_id]
        if enabled_only:
            clauses.append(DbDatasourceTable.enabled == 1)
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(select(DbDatasourceTable).where(*clauses))
                return _rows(result)

    @classmethod
    async def alist_by_ids(cls, table_ids: list[int]) -> list[DbDatasourceTable]:
        """Fetch specific table rows by id.

        Needed by the description endpoint, which receives a table id and must
        find out WHICH datasource it belongs to before it can check the
        caller's permission -- the id alone does not say.
        """
        if not table_ids:
            return []
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                result = await session.exec(
                    select(DbDatasourceTable).where(DbDatasourceTable.id.in_(table_ids))
                )
                return _rows(result)

    @classmethod
    async def areplace_snapshot(
        cls, table_id: int, *, structure_snapshot: list[dict[str, Any]], table_comment: str | None
    ) -> None:
        """Refresh the collected structure, leaving `business_desc` alone.

        Deliberately targeted at two columns rather than doing a
        ``session.add(row)`` on a full instance: spec AC-12 requires the
        admin-written business description to survive a re-collect, and a
        whole-row write is how it would silently get clobbered (the caller may
        hold a stale instance, and any field the collector does not know about
        would be written back as it was read).
        """
        from sqlalchemy import update as sa_update

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                await session.exec(
                    sa_update(DbDatasourceTable)
                    .where(DbDatasourceTable.id == table_id)
                    .values(structure_snapshot=structure_snapshot, table_comment=table_comment)
                )
                await session.commit()

    @classmethod
    async def aupdate_business_desc(cls, table_id: int, business_desc: str | None) -> None:
        from sqlalchemy import update as sa_update

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                await session.exec(
                    sa_update(DbDatasourceTable)
                    .where(DbDatasourceTable.id == table_id)
                    .values(business_desc=business_desc)
                )
                await session.commit()

    @classmethod
    async def aset_enabled(cls, table_id: int, enabled: int) -> None:
        """Expose or hide one table, leaving its description alone.

        Unticking a table sets `enabled=0` rather than deleting the row: the
        description an admin wrote is what makes unticking cheap to undo, and
        deleting would take it with the row.
        """
        from sqlalchemy import update as sa_update

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                await session.exec(
                    sa_update(DbDatasourceTable)
                    .where(DbDatasourceTable.id == table_id)
                    .values(enabled=enabled)
                )
                await session.commit()

    @classmethod
    async def adelete_by_datasource(cls, datasource_id: int) -> None:
        from sqlalchemy import delete as sa_delete

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                await session.exec(
                    sa_delete(DbDatasourceTable).where(
                        DbDatasourceTable.datasource_id == datasource_id
                    )
                )
                await session.commit()


class DbQueryLogDao:
    @classmethod
    async def acreate(cls, data: dict[str, Any]) -> DbQueryLog:
        row = DbQueryLog(**data)
        async with get_async_db_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def alist(
        cls,
        *,
        datasource_id: int | None = None,
        user_id: int | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DbQueryLog], int]:
        clauses = []
        if datasource_id is not None:
            clauses.append(DbQueryLog.datasource_id == datasource_id)
        if user_id is not None:
            clauses.append(DbQueryLog.user_id == user_id)
        if status:
            clauses.append(DbQueryLog.status == status)
        if start_time:
            clauses.append(DbQueryLog.create_time >= start_time)
        if end_time:
            clauses.append(DbQueryLog.create_time <= end_time)

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                total = await session.scalar(
                    select(func.count()).select_from(DbQueryLog).where(*clauses)
                )
                result = await session.exec(
                    select(DbQueryLog)
                    .where(*clauses)
                    .order_by(DbQueryLog.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                return _rows(result), int(total or 0)


class DbQueryFailureDao:
    @classmethod
    async def acreate_best_effort(cls, data: dict[str, Any]) -> None:
        """Write one failure record without ever breaking the conversation.

        The failure is already being reported to the user by this point; a
        failed write here must not turn a handled query failure into an
        unhandled exception (spec AC-40 keeps the rest of the turn alive).
        Warn with the traceback instead of raising.
        """
        try:
            async with get_async_db_session() as session:
                session.add(DbQueryFailure(**data))
                await session.commit()
        except (SQLAlchemyError, ValidationError):
            logger.opt(exception=True).warning(
                "datasource query failure record write failed for datasource={}",
                data.get('datasource_id'),
            )

    @classmethod
    async def alist(
        cls,
        *,
        datasource_id: int | None = None,
        error_category: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[DbQueryFailure], int]:
        clauses = []
        if datasource_id is not None:
            clauses.append(DbQueryFailure.datasource_id == datasource_id)
        if error_category:
            clauses.append(DbQueryFailure.error_category == error_category)
        if start_time:
            clauses.append(DbQueryFailure.create_time >= start_time)
        if end_time:
            clauses.append(DbQueryFailure.create_time <= end_time)

        with strict_tenant_filter():
            async with get_async_db_session() as session:
                total = await session.scalar(
                    select(func.count()).select_from(DbQueryFailure).where(*clauses)
                )
                result = await session.exec(
                    select(DbQueryFailure)
                    .where(*clauses)
                    .order_by(DbQueryFailure.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                return _rows(result), int(total or 0)
