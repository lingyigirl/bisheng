"""What the model is shown about a datasource's schema (spec AC-28).

Two things are asserted here that are easy to get wrong in opposite directions:
the injected block must contain what the model needs to write correct SQL
(names, types, and the admin's description), and must NOT contain anything the
platform is forbidden to read (table rows -- spec AC-13).
"""

import pytest

from bisheng.datasource.domain.models.query_log import INJECTION_FULL, INJECTION_RETRIEVED
from bisheng.datasource.domain.services import schema_prompt


def _table(name, *, desc=None, comment=None, columns=(('id', 'INT', None),)):
    return {
        'table_name': name,
        'business_desc': desc,
        'table_comment': comment,
        'structure_snapshot': [
            {'name': c, 'type': t, 'comment': cm} for c, t, cm in columns
        ],
    }


# --- rendering ---------------------------------------------------------------


def test_the_block_carries_name_columns_types_and_the_description():
    table = _table('orders', desc='订单主表', columns=[('id', 'INT', None), ('amount', 'DECIMAL(10,2)', '金额')])

    rendered = schema_prompt.render_table(table)

    assert 'orders' in rendered
    assert '订单主表' in rendered, 'the admin description is the point of the feature'
    assert 'id' in rendered and 'INT' in rendered
    assert 'amount' in rendered and 'DECIMAL(10,2)' in rendered
    assert '金额' in rendered, 'the in-database column comment'


def test_the_admin_description_wins_over_the_database_comment():
    """`business_desc` is written FOR the model; the DB comment is whatever a
    DBA typed years ago. When both exist the former is the better signal."""
    table = _table('orders', desc='客户订单主表', comment='t_order_old')

    rendered = schema_prompt.render_table(table)

    assert '客户订单主表' in rendered
    assert 't_order_old' not in rendered


def test_the_database_comment_is_used_when_no_description_exists():
    """Better than nothing for a table the admin has not got to yet."""
    assert 't_order' in schema_prompt.render_table(_table('orders', comment='t_order'))


def test_a_table_with_no_comment_renders_without_a_placeholder():
    rendered = schema_prompt.render_table(_table('orders'))
    assert '说明' not in rendered


# --- F064: per-field overrides ----------------------------------------------


def _with_overrides(table, overrides):
    table['column_overrides'] = overrides
    return table


def test_a_field_override_replaces_the_in_database_comment():
    """F064 AC-06: for that column, the override is what the model reads."""
    table = _table('orders', columns=[('amount', 'DECIMAL(10,2)', '库里的旧注释')])

    rendered = schema_prompt.render_table(_with_overrides(table, {'amount': '含税金额'}))

    assert '含税金额' in rendered
    assert '库里的旧注释' not in rendered, (
        'the override REPLACES the collected comment for that column; showing both '
        'would hand the model two descriptions of one field, which is the ambiguity '
        'the override exists to remove'
    )


def test_columns_without_an_override_keep_their_collected_comment():
    """The fallback is per COLUMN, not per table.

    Overriding one field must not blank the others -- a whole-table switch here
    would silently strip every comment the collector had gathered.
    """
    table = _table('orders', columns=[('id', 'INT', '主键'), ('amount', 'DECIMAL(10,2)', '旧注释')])

    rendered = schema_prompt.render_table(_with_overrides(table, {'amount': '含税金额'}))

    assert '主键' in rendered, 'a column with no override keeps what was collected'
    assert '含税金额' in rendered
    assert '旧注释' not in rendered


def test_an_override_for_a_column_that_is_gone_is_not_injected():
    """F064 AC-05: a dangling key is KEPT, not rendered.

    Injection walks the snapshot, so a key with no matching column is never
    looked up. AC-05 wants it kept in storage (the admin should see that their
    note still exists) while staying out of what the model reads.
    """
    table = _table('orders', columns=[('id', 'INT', None)])

    rendered = schema_prompt.render_table(_with_overrides(table, {'old_column': '已删除字段的说明'}))

    assert '已删除字段的说明' not in rendered


def test_retrieval_matches_on_a_field_override():
    """F064 AC-07, in the form that cannot pass by accident.

    `授信额度` appears ONLY in the field override -- every other string on that
    table is worded to exclude it -- so this goes red the moment `_haystack`
    stops reading overrides.

    Fixture layout is load-bearing in two ways. The target is placed LAST and a
    control that legitimately contains the term FIRST, because `_retrieve` falls
    back to the first few tables when nothing matches at all: with the target in
    front, a matcher that read nothing would still return it and the assertion
    would pass for the wrong reason. The control also arms the fixture by proving
    that matching works at all.
    """
    control = _table('credit_limit', desc='授信额度台账')
    filler = [_table(f'filler_{i}', desc='填充表' * 400) for i in range(60)]
    target = _table('t_9001', desc='一张表', comment='表注释', columns=[('f_1', 'INT', '字段注释')])

    chosen, mode = schema_prompt.select_tables(
        [control, *filler, _with_overrides(target, {'f_1': '授信额度'})], '授信额度是多少'
    )
    names = [t['table_name'] for t in chosen]

    assert mode == INJECTION_RETRIEVED, 'the fixture must be large enough to force retrieval'
    assert 'credit_limit' in names, 'the control must match, or the fixture proves nothing'
    assert 't_9001' in names, (
        'the table was not matched on its override text -- retrieval has to search '
        'the words the model is shown, or an override describes a column the '
        'question can never reach'
    )


def test_a_table_with_no_overrides_key_at_all_renders_as_before():
    """Older callers build the dict without the key (the workflow path, tests).

    Reading it defensively is what keeps F064 additive for them.
    """
    rendered = schema_prompt.render_table(_table('orders', columns=[('id', 'INT', '主键')]))

    assert '主键' in rendered


# --- AC-13: no sample values ------------------------------------------------


def test_the_block_contains_no_row_data():
    """The prompt is exactly where "just show three example values" is tempting,
    and it would copy customer data into a prompt, a log and a persisted record
    under an authorization model that covers none of them."""
    import inspect
    import re

    source = inspect.getsource(schema_prompt)
    # Strip docstrings first: this module's own docstring discusses sample
    # values in order to forbid them, and a naive scan flags the explanation.
    code = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
    for forbidden in ('SELECT', 'fetch', 'sample', 'example_value'):
        assert forbidden not in code, (
            f'{forbidden!r} appears in the schema prompt builder -- AC-13 allows '
            'structure only'
        )


def test_rendering_only_reads_the_snapshot_keys():
    """The renderer must not reach for anything the collector did not store --
    in particular it cannot invent a value field."""
    table = _table('orders', columns=[('id', 'INT', None)])
    before = repr(table)
    schema_prompt.render_table(table)
    assert repr(table) == before, 'rendering mutated the table dict'


# --- AC-28: the two modes, and which one ran ---------------------------------


def test_a_small_schema_is_injected_whole():
    tables = [_table('a'), _table('b')]

    block, mode = schema_prompt.build_schema_block(tables, '随便问点什么')

    assert mode == INJECTION_FULL
    assert 'a' in block and 'b' in block


def test_an_oversized_schema_degrades_to_retrieval():
    """Beyond the budget the prompt stops describing the database and starts
    copying it, and the model's attention spreads across irrelevant tables."""
    # Big enough to exceed the budget on purpose -- the budget is sized for
    # real schemas, so a test that trips it has to be genuinely oversized.
    wide = [
        _table(f'table_{i}', desc='x' * 200, columns=[('c', 'INT', None)] * 200)
        for i in range(30)
    ]

    _, mode = schema_prompt.build_schema_block(wide, '订单')

    assert mode == INJECTION_RETRIEVED, 'the budget did not bite'


def test_retrieval_keeps_the_table_the_question_is_about():
    wide = [
        _table('customers', desc='客户主表', columns=[('c', 'INT', None)] * 4000),
        _table('orders', desc='订单主表', columns=[('c', 'INT', None)] * 4000),
    ]

    chosen, mode = schema_prompt.select_tables(wide, '订单有多少')

    assert mode == INJECTION_RETRIEVED
    assert any(t['table_name'] == 'orders' for t in chosen), 'the relevant table was dropped'


def test_retrieval_matches_on_the_admins_own_words():
    """Matching on the description is what makes the ranking tunable: an admin
    whose table is not being found can fix it by editing the description. An
    embedding-based ranker would score better on paper and give them nothing to
    change."""
    wide = [
        _table(f'noise_{i}', desc='无关', columns=[('c', 'INT', None)] * 4000) for i in range(5)
    ] + [_table('t_9911', desc='客户合同', columns=[('c', 'INT', None)] * 4000)]

    chosen, _ = schema_prompt.select_tables(wide, '查一下客户合同')

    assert any(t['table_name'] == 't_9911' for t in chosen)


def test_retrieval_never_returns_nothing():
    """An empty schema block would read to the model as "this datasource has no
    tables", which is a different and wrong message."""
    wide = [_table(f'n{i}', desc='x' * 500, columns=[('c', 'INT', None)] * 2000) for i in range(10)]

    chosen, _ = schema_prompt.select_tables(wide, 'zzzzz')

    assert chosen, 'retrieval returned nothing'


@pytest.mark.parametrize(
    'question',
    [
        '订单',
        'a b c',
        '\N{FULLWIDTH QUESTION MARK}',  # punctuation only -- no usable terms
        '',
    ],
)
def test_retrieval_survives_odd_questions(question):
    wide = [_table(f'n{i}', desc='x' * 500, columns=[('c', 'INT', None)] * 2000) for i in range(6)]
    chosen, _ = schema_prompt.select_tables(wide, question)
    assert chosen


def test_an_empty_table_list_produces_an_empty_block():
    block, _mode = schema_prompt.build_schema_block([], '任意问题')
    assert block == ''
