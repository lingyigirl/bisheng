"""Persist collected table structures, and maintain the admin's descriptions.

The one rule this module exists to keep is spec AC-12: **a re-collect refreshes
the structure and must NOT touch `business_desc`.** The whole point of the
description is to survive schema churn — a column's meaning is exactly what the
database cannot tell us, so it is the one thing worth keeping across a refresh.
Losing it means every re-collect silently discards work an admin did by hand.

That is why the write path is split in two (`areplace_snapshot` for structure,
`aupdate_business_desc` for the description) rather than one "save the table"
call: a blanket update is how the description would get clobbered, and the DAO
tests assert the generated UPDATE's column list so the split cannot quietly
collapse back into one.

AC-15 — no automatic re-collection — is a property of what is *absent*: there
is no Celery task, no beat schedule and no timer anywhere in this module or the
one it wraps. `test_no_polling.py` asserts that by scanning the schedule rather
than by trusting this sentence.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from bisheng.datasource.domain.models.dao import DbDatasourceTableDao
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services import schema_collector


async def refresh_tables(datasource: DbDatasource, table_names: list[str]) -> dict[str, int]:
    """Collect the named tables and persist their structure.

    Returns counts for the caller to report, because "re-collect" is a button
    an admin presses and the result should say what happened.

    The description is preserved by construction: the only write here is
    `areplace_snapshot`, whose UPDATE names `structure_snapshot` and
    `table_comment` and nothing else.
    """
    result = schema_collector.collect(datasource, table_names)

    existing = {
        row.table_name: row
        for row in await DbDatasourceTableDao.alist_by_datasource(datasource.id)
    }

    created = 0
    refreshed = 0
    for table in result.tables:
        row = existing.get(table.table_name)
        if row is None:
            await DbDatasourceTableDao.acreate(
                {
                    'tenant_id': datasource.tenant_id,
                    'datasource_id': datasource.id,
                    'table_name': table.table_name,
                    'table_comment': table.comment,
                    'structure_snapshot': table.columns,
                    # A newly seen table starts exposed; the admin unticks what
                    # they do not want rather than ticking everything.
                    'enabled': 1,
                }
            )
            created += 1
        else:
            await DbDatasourceTableDao.areplace_snapshot(
                row.id,
                structure_snapshot=table.columns,
                table_comment=table.comment,
            )
            refreshed += 1

    logger.info(
        'datasource {} re-collected: {} new, {} refreshed ({} tables visible{})',
        datasource.id,
        created,
        refreshed,
        len(result.tables),
        ', TRUNCATED' if result.truncated else '',
    )
    return {'created': created, 'refreshed': refreshed, 'total': len(result.tables)}


async def set_exposed(datasource_id: int, table_names: list[str]) -> int:
    """Set which tables are exposed to the model, from the admin's tick-boxes.

    Present-but-unticked and absent are different states, and this preserves
    the difference: unticking sets `enabled=0` (the row and its description
    survive), while a table that was never collected has no row to change.
    Deleting on untick would take the description with it — the admin would
    lose it by unchecking a box.
    """
    rows = await DbDatasourceTableDao.alist_by_datasource(datasource_id)
    wanted = set(table_names)
    changed = 0
    for row in rows:
        should_be = 1 if row.table_name in wanted else 0
        if row.enabled != should_be:
            await DbDatasourceTableDao.aset_enabled(row.id, should_be)
            changed += 1
    return changed


async def update_description(table_id: int, description: str | None) -> None:
    """Set one table's business description (spec AC-11).

    Goes straight to the two-column DAO, so it cannot accidentally rewrite the
    collected snapshot on the way through.
    """
    await DbDatasourceTableDao.aupdate_business_desc(table_id, description)


async def list_tables_for_admin(datasource_id: int) -> list[dict[str, Any]]:
    """What the admin UI shows: the collected structure plus the description.

    Reads the stored snapshot rather than reflecting the customer database —
    the snapshot is what the model sees, so it is what the admin should be
    judging their description against.
    """
    rows = await DbDatasourceTableDao.alist_by_datasource(datasource_id)
    return [
        {
            'id': row.id,
            'table_name': row.table_name,
            'table_comment': row.table_comment,
            'structure_snapshot': row.structure_snapshot or [],
            'business_desc': row.business_desc,
            'enabled': row.enabled,
        }
        for row in rows
    ]
