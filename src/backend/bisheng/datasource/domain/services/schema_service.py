"""Persist collected table structures, and maintain the admin's descriptions.

The one rule this module exists to keep is spec AC-12: **a re-collect refreshes
the structure and must NOT touch what an admin wrote.** The whole point of that
content is to survive schema churn — a column's meaning is exactly what the
database cannot tell us, so it is the one thing worth keeping across a refresh.
Losing it means every re-collect silently discards work an admin did by hand.
F064 extends the rule from the table-level description down to per-field
overrides (its AC-04).

That is why the write path is split in two — `areplace_snapshot` for the
machine-collected columns, `aupdate_human_fields` for everything an admin
authors — rather than one "save the table" call: a blanket update is how that
content would get clobbered, and the DAO tests assert the generated UPDATE's
column list so the split cannot quietly collapse back into one.

Within the human half the split is by PRESENCE instead (`None` is a real value,
meaning "cleared"): the caller names what it wants written, and the DAO writes
exactly that.

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

    Goes straight to the human-columns DAO writer, which cannot name
    `structure_snapshot` at all, so it cannot rewrite the collected snapshot on
    the way through. `column_overrides` is left unset -- and unset means "not
    written", not "written as NULL" -- so saving a description never clears the
    field overrides beside it.
    """
    await DbDatasourceTableDao.aupdate_human_fields(table_id, business_desc=description)


def _normalise_overrides(overrides: dict[str, str] | None) -> dict[str, str] | None:
    """Strip each value and drop the ones that stripped to nothing (决策 5).

    An empty string and an absent key behave identically on the INJECTION side
    (`render_table` tests truthiness), but not in the UI: an empty value would
    read as "订正过、订正为空", where AC-09 requires "从未订正、回落库内注释".
    Normalising at the closest point to the write is what keeps that ambiguity
    out of the database, so no reader ever has to guess between the two.

    Returns `None` rather than `{}` when nothing is left: NULL is the stored
    form of "no overrides", and `{}` would be indistinguishable from "cleared"
    further downstream.
    """
    if not overrides:
        return None
    cleaned = {name: value.strip() for name, value in overrides.items() if value.strip()}
    return cleaned or None


async def update_column_overrides(table_id: int, overrides: dict[str, str] | None) -> None:
    """Set one table's per-field overrides (F064 AC-01).

    Normalises first (see above), then writes through the human-columns DAO
    writer -- the same one `update_description` uses. Two service functions, one
    writer: each names exactly one column, so neither can clear the other.
    """
    await DbDatasourceTableDao.aupdate_human_fields(
        table_id, column_overrides=_normalise_overrides(overrides)
    )


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
            # F064: the admin UI resolves each field's EFFECTIVE comment from
            # this plus the snapshot. Defaulting to {} rather than None keeps
            # the front end from having to nil-check before indexing, and is
            # read-only either way -- the stored form stays NULL (决策 5).
            'column_overrides': row.column_overrides or {},
            'enabled': row.enabled,
        }
        for row in rows
    ]


async def alist_model_tables(datasource_id: int) -> list[dict[str, Any]]:
    """What the model sees: the EXPOSED tables, in the shape the prompt builder reads.

    Sibling of `list_tables_for_admin`, and deliberately not the same function:
    this one is `enabled_only` (an unexposed table must never reach the model,
    AC-16/17) and carries no `id`/`enabled`, which are admin-UI concerns. Keeping
    the two side by side makes that difference readable; folding them into one
    call with a flag would hide it.

    Shared by the assistant's tool (`domain/langchain/tool.py`) and the config
    page's self-test endpoint — both must hand the model the same view of the
    datasource, or "it worked in chat but not in the test box" becomes a bug class.
    """
    rows = await DbDatasourceTableDao.alist_by_datasource(datasource_id, enabled_only=True)
    return [
        {
            'table_name': row.table_name,
            'table_comment': row.table_comment,
            'business_desc': row.business_desc,
            'structure_snapshot': row.structure_snapshot or [],
            # F064: this is the SECOND place a table dict is built -- the admin list
            # is the other (`list_tables_for_admin`). They are independent, and
            # omitting this key here is a silent half-failure: the admin's override
            # shows in the UI, `render_table` and `_haystack` read it as absent, and
            # the model answers from the raw in-database comment with nothing
            # reporting a problem.
            'column_overrides': row.column_overrides or {},
        }
        for row in rows
    ]
