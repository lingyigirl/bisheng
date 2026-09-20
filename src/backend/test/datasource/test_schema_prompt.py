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
