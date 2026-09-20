"""Table-structure collection: shape only, never rows (spec AC-09 / AC-13).

The important assertion in this file is the negative one. AC-13 forbids
reading table data at all, and "we only call reflection" is a property that a
future edit could quietly break — someone adding a `SELECT ... LIMIT 5` to
guess at a column's meaning would be doing something reasonable-sounding and
would cross a line that exists to keep "authorized to query the customer's DB"
and "authorized to read what we copied" the same thing.
"""

from unittest.mock import MagicMock

import pytest

from bisheng.datasource.domain.services import schema_collector
from bisheng.datasource.domain.services.schema_collector import (
    MAX_TABLES,
    collect,
    list_tables,
)


def _datasource(engine='mysql'):
    return MagicMock(engine=engine, id=1, host='h', port=3306, database_name='d',
                     username='u', password='cipher')


class _RecordingInspector:
    """Stands in for SQLAlchemy's Inspector and records every method called."""

    def __init__(self, tables=None, views=None, columns=None, comment=None):
        self._tables = tables or []
        self._views = views or []
        self._columns = columns or {}
        self._comment = comment
        self.calls = []

    def get_table_names(self):
        self.calls.append(('get_table_names',))
        return list(self._tables)

    def get_view_names(self):
        self.calls.append(('get_view_names',))
        return list(self._views)

    def get_columns(self, name):
        self.calls.append(('get_columns', name))
        return [
            {'name': c, 'type': 'VARCHAR(10)', 'comment': None}
            for c in self._columns.get(name, [])
        ]

    def get_table_comment(self, name):
        self.calls.append(('get_table_comment', name))
        if self._comment is None:
            raise NotImplementedError('this dialect does not support comments')
        return {'text': self._comment}


def _patch_connection(monkeypatch, inspector, executed=None):
    """Make `_with_connection` hand `inspector` to the collector.

    Patches at the connection boundary, not at the collector: the code under
    test is the reflection logic, and mocking it away would leave nothing.
    """
    connection = MagicMock()
    if executed is not None:
        connection.execute.side_effect = lambda *a, **k: executed.append(a) or MagicMock()

    monkeypatch.setattr(schema_collector, 'create_engine', lambda *a, **k: MagicMock())

    class _Ctx:
        def __enter__(self):
            return connection

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(schema_collector, 'inspect', lambda conn: inspector)
    monkeypatch.setattr(
        schema_collector, '_with_connection', lambda ds, fn: fn(connection, inspector)
    )
    return connection


# --- listing ---------------------------------------------------------------


def test_list_tables_includes_views(monkeypatch):
    """A customer's reporting layer is often views; omitting them would make
    half their schema invisible (spec AC-09)."""
    inspector = _RecordingInspector(tables=['orders'], views=['v_daily'])
    _patch_connection(monkeypatch, inspector)

    assert list_tables(_datasource()) == ['orders', 'v_daily']


def test_list_tables_dedupes_and_sorts(monkeypatch):
    inspector = _RecordingInspector(tables=['b', 'a'], views=['a'])
    _patch_connection(monkeypatch, inspector)

    assert list_tables(_datasource()) == ['a', 'b']


# --- collecting ------------------------------------------------------------


def test_collect_returns_names_types_and_comments(monkeypatch):
    inspector = _RecordingInspector(tables=['orders'], columns={'orders': ['id', 'amount']},
                                    comment='订单表')
    _patch_connection(monkeypatch, inspector)

    result = collect(_datasource(), ['orders'])

    assert len(result.tables) == 1
    table = result.tables[0]
    assert table.table_name == 'orders'
    assert table.comment == '订单表'
    assert [c['name'] for c in table.columns] == ['id', 'amount']
    assert all(c['type'] for c in table.columns)


def test_collect_all_visible_tables_when_none_named(monkeypatch):
    inspector = _RecordingInspector(tables=['a'], views=['b'])
    _patch_connection(monkeypatch, inspector)

    result = collect(_datasource(), None)

    assert sorted(t.table_name for t in result.tables) == ['a', 'b']


def test_an_unsupported_comment_api_is_not_a_failure(monkeypatch):
    """spec AC-14: a missing comment means "fill it in by hand", not "the
    collect failed". The table still comes back, with comment=None."""
    inspector = _RecordingInspector(tables=['t'], columns={'t': ['id']}, comment=None)
    _patch_connection(monkeypatch, inspector)

    result = collect(_datasource(), ['t'])

    assert result.tables[0].comment is None
    assert [c['name'] for c in result.tables[0].columns] == ['id']


def test_one_unreadable_table_does_not_fail_the_run(monkeypatch):
    """A broken view or dangling synonym costs that table's entry, not the
    whole collect -- the admin sees it missing and re-collects after fixing."""
    inspector = _RecordingInspector(tables=['good', 'bad'], columns={'good': ['id']})
    _patch_connection(monkeypatch, inspector)
    original = inspector.get_columns

    def _maybe_fail(name):
        if name == 'bad':
            raise RuntimeError('view definition is broken')
        return original(name)

    inspector.get_columns = _maybe_fail

    result = collect(_datasource(), ['good', 'bad'])

    by_name = {t.table_name: t for t in result.tables}
    assert sorted(by_name) == ['bad', 'good']
    assert by_name['bad'].columns == []
    assert [c['name'] for c in by_name['good'].columns] == ['id']


def test_collect_reports_truncation_rather_than_losing_tables_silently(monkeypatch):
    """A cap that silently drops tables reads as "your schema is complete"."""
    many = [f't{i}' for i in range(MAX_TABLES + 5)]
    inspector = _RecordingInspector(tables=many)
    _patch_connection(monkeypatch, inspector)

    result = collect(_datasource(), many)

    assert result.truncated is True
    assert len(result.tables) == MAX_TABLES


# --- the negative assertion: no data is read (AC-13) -----------------------


@pytest.mark.parametrize('action', ['list', 'collect'])
def test_no_rows_are_ever_read(monkeypatch, action):
    """spec AC-13: structure only, never table contents.

    Asserted on the SET OF METHODS CALLED rather than on the data returned: a
    collector that also ran `SELECT * FROM t LIMIT 5` would still return
    correct structure, so only the call log can tell the difference.
    """
    inspector = _RecordingInspector(tables=['t'], columns={'t': ['id']})
    executed = []
    _patch_connection(monkeypatch, inspector, executed=executed)

    if action == 'list':
        list_tables(_datasource())
    else:
        collect(_datasource(), ['t'])

    for method, *_ in inspector.calls:
        assert method in {'get_table_names', 'get_view_names', 'get_columns', 'get_table_comment'}, (
            f'unexpected reflection call {method!r}'
        )

    # And nothing was executed through the connection at all -- reflection goes
    # via the inspector, so a raw execute() here would mean someone reached for
    # the rows.
    assert executed == [], f'collection executed raw SQL: {executed}'


def test_the_collector_module_contains_no_row_selecting_sql():
    """A source-level check, deliberately blunt.

    The runtime assertion above covers the paths that exist today; this one
    also catches a `SELECT` added to a branch the tests do not drive, which is
    exactly how AC-13 would get broken by a well-meaning future change
    ("just sample a few values so the model understands the column").
    """
    import inspect as _inspect
    import re

    source = _inspect.getsource(schema_collector)
    # Strip the docstrings, which discuss this rule and would otherwise match.
    code = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
    offenders = re.findall(r'\bSELECT\b', code, flags=re.IGNORECASE)
    assert not offenders, (
        f'{len(offenders)} raw SELECT(s) in the collector; AC-13 allows '
        'structure collection only'
    )
