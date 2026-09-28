"""Re-collection preserves descriptions (AC-12); nothing re-collects on a timer (AC-15).

AC-12 is the reason this file exists. `business_desc` is the only field in the
whole datasource feature that a human wrote, and it is the one the database
cannot regenerate — a column called `f_0031` means whatever the admin says it
means. Every re-collect therefore runs the risk of discarding their work, and
"refresh the row" is the natural way to write that bug.

AC-15 is the mirror image: it is a claim about something that must NOT exist.
Asserted by scanning the scheduler rather than by reading a comment, because
"no timer" is invisible to every test that does not go looking for one.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.datasource.domain.services import schema_collector, schema_service
from bisheng.datasource.domain.services.schema_collector import CollectResult, TableStructure

SERVICE_PATH = Path(schema_service.__file__)


def _datasource():
    from unittest.mock import MagicMock

    return MagicMock(id=7, tenant_id=1, engine='mysql')


def _collected(*names):
    return CollectResult(
        tables=[TableStructure(table_name=n, comment=f'{n} 注释', columns=[{'name': 'id', 'type': 'INT', 'comment': None}]) for n in names]
    )


def _row(table_id, name, *, desc='人工描述', enabled=1, overrides=None):
    from unittest.mock import MagicMock

    row = MagicMock(id=table_id, table_name=name, business_desc=desc, enabled=enabled,
                    table_comment=None, structure_snapshot=[], column_overrides=overrides)
    return row


# --- AC-12: the description survives a re-collect ---------------------------


async def test_refresh_updates_structure_and_never_touches_the_description():
    """The load-bearing test. A re-collect must write structure only.

    A single "save this table row" call would pass every assertion about the
    structure being updated while silently clearing the description — so the
    assertion is that `aupdate_human_fields` is NOT called, and that the write
    that does happen is the snapshot-only one.
    """
    row = _row(11, 'orders')

    with patch.object(schema_collector, 'collect', return_value=_collected('orders')), patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[row])
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ) as replace, patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as desc_write, patch.object(
        schema_service.DbDatasourceTableDao, 'acreate', AsyncMock()
    ):
        result = await schema_service.refresh_tables(_datasource(), ['orders'])

    assert result == {'created': 0, 'refreshed': 1, 'total': 1}
    replace.assert_awaited_once()
    desc_write.assert_not_awaited()
    # And the snapshot write names only the collected columns.
    assert replace.await_args.kwargs['structure_snapshot'] == [
        {'name': 'id', 'type': 'INT', 'comment': None}
    ]


async def test_a_new_table_is_created_without_a_description():
    """A table seen for the first time has no description to preserve."""
    with patch.object(schema_collector, 'collect', return_value=_collected('fresh')), patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[])
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'acreate', AsyncMock()
    ) as create, patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ):
        result = await schema_service.refresh_tables(_datasource(), ['fresh'])

    assert result['created'] == 1
    payload = create.await_args.args[0]
    assert payload['table_name'] == 'fresh'
    assert 'business_desc' not in payload, 'a new table must not be seeded with a description'


async def test_a_table_that_vanished_from_the_database_keeps_its_row():
    """Its columns are gone but its description is not — an admin who fixed the
    upstream problem should find their notes still there."""
    stale = _row(12, 'dropped_table', desc='曾经的表')
    with patch.object(schema_collector, 'collect', return_value=_collected('orders')), patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[stale])
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'acreate', AsyncMock()
    ) as create, patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ) as replace, patch.object(
        schema_service.DbDatasourceTableDao, 'adelete_by_datasource', AsyncMock()
    ) as delete:
        await schema_service.refresh_tables(_datasource(), ['orders'])

    # `orders` is new here, so it is CREATED; the point is that the vanished
    # table is neither deleted nor rewritten -- its row and description stand.
    create.assert_awaited_once()
    assert create.await_args.args[0]['table_name'] == 'orders'
    replace.assert_not_awaited()
    delete.assert_not_awaited()


# --- descriptions -----------------------------------------------------------


async def test_setting_a_description_does_not_rewrite_the_snapshot():
    with patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as desc_write, patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ) as replace:
        await schema_service.update_description(5, '存客户主数据')

    desc_write.assert_awaited_once_with(5, business_desc='存客户主数据')
    replace.assert_not_awaited()


# --- F064: per-field overrides ----------------------------------------------


async def test_setting_column_overrides_goes_through_the_human_writer():
    """F064 AC-01: the overrides are written, and the snapshot is not.

    Two service functions, one DAO writer. The assertion names the DAO call
    because "which columns did it write" is the property that has to stay true
    -- not "some write happened".
    """
    with patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as human_write, patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ) as replace:
        await schema_service.update_column_overrides(5, {'amount': '含税金额'})

    human_write.assert_awaited_once_with(5, column_overrides={'amount': '含税金额'})
    assert replace.await_count == 0


async def test_blank_overrides_are_dropped_and_all_blank_becomes_null():
    """决策 5 / AC-09. `{"a": " ", "b": "x"}` stores `{"b": "x"}`; nothing left stores NULL.

    The second half is the load-bearing one: `{}` would be a third state that
    the UI and the injection path would each have to agree about, and AC-09
    admits exactly two ("订正过" / "从未订正").
    """
    with patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as human_write:
        await schema_service.update_column_overrides(5, {'a': ' ', 'b': 'x'})
        await schema_service.update_column_overrides(5, {'a': '   '})
        await schema_service.update_column_overrides(5, {})

    assert [c.kwargs['column_overrides'] for c in human_write.await_args_list] == [
        {'b': 'x'},
        None,
        None,
    ]


async def test_a_padded_override_is_stored_trimmed():
    """Not merely "non-blank survives" -- the padding is gone from what is stored.

    Otherwise the admin's next visit shows them whitespace they did not type,
    and `_haystack` matches against padding they never meant to include.
    """
    with patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as human_write:
        await schema_service.update_column_overrides(5, {'amount': '  含税金额  '})

    assert human_write.await_args.kwargs['column_overrides'] == {'amount': '含税金额'}


async def test_the_admin_list_carries_the_overrides():
    """F064 AC-08: the UI resolves each field's effective comment from this key."""
    row = _row(11, 'orders', overrides={'amount': '含税金额'})

    with patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[row])
    ):
        tables = await schema_service.list_tables_for_admin(7)

    assert tables[0]['column_overrides'] == {'amount': '含税金额'}


async def test_the_admin_list_presents_no_overrides_as_an_empty_mapping():
    """NULL is the STORED form; the read path normalises it for the front end.

    Asserted as its own case so the default is a decision rather than an
    accident of whatever the row happened to hold.
    """
    row = _row(11, 'orders', overrides=None)

    with patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[row])
    ):
        tables = await schema_service.list_tables_for_admin(7)

    assert tables[0]['column_overrides'] == {}


async def test_a_recollect_never_reaches_the_override_writer():
    """F064 AC-04, at the service layer.

    The DAO test already pins the other half (the re-collect's UPDATE names
    `structure_snapshot` and `table_comment` and nothing else). What this adds
    is the call: a re-collect must not reach for the human writer at all, since
    that is the one method able to name `column_overrides`.
    """
    with patch.object(
        schema_collector, 'collect', return_value=_collected('orders')
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=[_row(11, 'orders')])
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'areplace_snapshot', AsyncMock()
    ) as replace, patch.object(
        schema_service.DbDatasourceTableDao, 'aupdate_human_fields', AsyncMock()
    ) as human_write:
        await schema_service.refresh_tables(_datasource(), ['orders'])

    assert replace.await_count == 1, 'the structure must still be refreshed'
    assert human_write.await_count == 0, (
        'a re-collect wrote through the human-columns writer -- that is the one '
        'path that can overwrite an admin override (F064 AC-04)'
    )


async def test_unticking_a_table_hides_it_and_keeps_its_description():
    """Unticking is reversible; deleting the row is not (the description would
    go with it)."""
    rows = [_row(1, 'keep', enabled=1), _row(2, 'hide', enabled=1, desc='别删我')]

    with patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=rows)
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'aset_enabled', AsyncMock()
    ) as set_enabled, patch.object(
        schema_service.DbDatasourceTableDao, 'adelete_by_datasource', AsyncMock()
    ) as delete:
        changed = await schema_service.set_exposed(7, ['keep'])

    assert changed == 1
    set_enabled.assert_awaited_once_with(2, 0)
    delete.assert_not_awaited()


async def test_ticking_a_previously_hidden_table_reenables_it():
    rows = [_row(3, 'back', enabled=0)]
    with patch.object(
        schema_service.DbDatasourceTableDao, 'alist_by_datasource', AsyncMock(return_value=rows)
    ), patch.object(
        schema_service.DbDatasourceTableDao, 'aset_enabled', AsyncMock()
    ) as set_enabled:
        await schema_service.set_exposed(7, ['back'])

    set_enabled.assert_awaited_once_with(3, 1)


# --- AC-15: nothing re-collects on a schedule -------------------------------


def test_no_collection_is_scheduled():
    """AC-15 forbids periodic re-collection. Asserted against the actual beat
    schedule, not against a comment saying there isn't one."""
    from bisheng.worker.main import celery_app

    entries = celery_app.conf.beat_schedule or {}
    offenders = [
        name
        for name, entry in entries.items()
        if 'collect' in str(entry.get('task', '')).lower()
        or 'schema' in str(entry.get('task', '')).lower()
        or 'datasource' in str(entry.get('task', '')).lower()
    ]
    assert not offenders, f'periodic datasource collection is scheduled: {offenders}'


def test_the_schema_modules_schedule_nothing_themselves():
    """`@app.on_after_configure` / `crontab` in these files would be a schedule
    the beat-config check above cannot see."""
    for path in (SERVICE_PATH, Path(schema_collector.__file__)):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            name = getattr(node, 'id', None) or getattr(node, 'attr', None)
            assert name not in {'crontab', 'beat_schedule', 'on_after_configure'}, (
                f'{path.name} appears to define a schedule'
            )


@pytest.mark.parametrize('name', ['collect', 'list_tables'])
def test_collection_is_only_reachable_through_an_explicit_call(name):
    """Sanity on the module surface: the collector exposes plain functions and
    owns no task/timer of its own."""
    fn = getattr(schema_collector, name)
    assert callable(fn)
    assert not getattr(fn, 'delay', None), f'{name} was turned into a celery task'
