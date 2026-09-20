"""The datasource as a tool the model may choose to call (spec AC-25/AC-26).

The property that matters most here is a NEGATIVE one: nothing inspects the
question to decide whether to query. AC-25 requires the model to judge for
itself, and a platform-side "this looks like a database question" check would
both contradict the spec and break the moment a question spans both sources.
"""

# ruff: noqa: RUF001 -- the Chinese test data mirrors user-facing text;
# fullwidth punctuation is correct in it.

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.datasource.domain.langchain.tool import (
    DatasourceQueryTool,
    build_datasource_tools,
)
from bisheng.datasource.domain.services.executor import QueryOutcome


def _datasource(ds_id=7, name='业务库'):
    """A plain object, not a MagicMock: a mock auto-creates `description`, and
    that field collides with `BaseTool.description` (see the tool's comment)."""
    return SimpleNamespace(id=ds_id, name=name, tenant_id=1, engine='mysql')


def _tool(**overrides):
    fields = {
        'name': 'datasource_7',
        'description': '在业务库中查询',
        'datasource': _datasource(),
        'tables': [{'table_name': 'orders', 'structure_snapshot': []}],
        'user_id': 3,
    }
    fields.update(overrides)
    return DatasourceQueryTool(**fields)


def _outcome(**overrides):
    fields = {
        'statement': 'SELECT id FROM orders',
        'columns': ['id', 'amount'],
        'rows': [[1, 10], [2, 20]],
        'row_count': 2,
    }
    fields.update(overrides)
    return QueryOutcome(**fields)


def _answer(outcome=None, **overrides):
    from bisheng.datasource.domain.services.query_service import AnswerContext

    fields = {
        'answer': '已查询',
        'outcome': outcome,
        'injection_mode': 'full',
        'failure_category': None,
    }
    fields.update(overrides)
    return AnswerContext(**fields)


# --- AC-26: it is a tool, at the same level as knowledge retrieval -----------


def test_the_tool_is_a_langchain_tool_with_a_query_argument():
    tool = _tool()
    assert tool.name == 'datasource_7'
    # The model must not be asked to write SQL -- that is the feature's job.
    schema = tool.args_schema.model_json_schema()
    assert schema['required'] == ['query']


async def test_the_tool_is_async_so_the_loop_is_not_blocked():
    """AC-21 applies to the agent path too: a sync `_run` would put the query on
    the event loop. Design decision 12 records the legacy `sql_agent` doing
    exactly that."""
    import inspect

    assert inspect.iscoroutinefunction(DatasourceQueryTool._arun), (
        'the tool is not async -- a tool call would block every other '
        'conversation on the process'
    )


async def test_a_call_goes_through_the_query_service(monkeypatch):
    seen = {}

    async def _answer_question(datasource, question, tables, **kwargs):
        seen['question'] = question
        seen['tables'] = tables
        seen['user_id'] = kwargs.get('user_id')
        return _answer(_outcome())

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    tool = _tool()
    result = await tool._arun('这个月有多少订单')

    assert seen['question'] == '这个月有多少订单'
    assert seen['user_id'] == 3
    assert seen['tables'] == tool.tables
    assert '| id | amount |' in result


# --- AC-25: nothing pre-routes ------------------------------------------------


async def test_the_question_is_passed_through_unexamined(monkeypatch):
    """No keyword sniffing, no "is this a database question" gate. The model
    decides; the platform only executes what it asks for."""
    import inspect

    source = inspect.getsource(__import__(
        'bisheng.datasource.domain.langchain.tool', fromlist=['x']
    ))
    for suspicious in ('is_database_question', 'looks_like_sql', 'should_query'):
        assert suspicious not in source, f'{suspicious} suggests pre-routing (AC-25)'


async def test_an_ordinary_conversational_question_still_reaches_the_service(monkeypatch):
    """Even a question that plainly has nothing to do with a database is
    executed if the MODEL chose to call the tool -- deciding that is not the
    platform's job."""
    called = []

    async def _answer_question(datasource, question, tables, **kwargs):
        called.append(question)
        return _answer(_outcome(row_count=0, rows=[]))

    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        _answer_question,
    )

    await _tool()._arun('今天天气怎么样')

    assert called == ['今天天气怎么样']


# --- the result the model sees -----------------------------------------------


async def test_the_model_gets_the_rows(monkeypatch):
    """The model needs the data to answer; withholding it would make the tool
    pointless."""
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        AsyncMock(return_value=_answer(_outcome())),
    )

    result = await _tool()._arun('q')

    assert '1' in result and '10' in result
    assert '| id | amount |' in result


async def test_an_empty_result_reads_as_an_answer_not_an_error(monkeypatch):
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        AsyncMock(return_value=_answer(_outcome(row_count=0, rows=[]))),
    )

    result = await _tool()._arun('q')

    assert '没有匹配的数据' in result
    assert '失败' not in result


async def test_a_truncated_result_says_so_to_the_model(monkeypatch):
    """Otherwise the model answers as if it had seen everything."""
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        AsyncMock(return_value=_answer(_outcome(truncated=True))),
    )

    assert '截断' in await _tool()._arun('q')


async def test_a_failure_is_reported_to_the_model_without_the_driver_error(monkeypatch):
    """The user-facing sentence goes back to the model too, so it can say
    "I could not query that" and, per AC-40, carry on with other sources."""
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        AsyncMock(
            return_value=_answer(
                None, answer='无法连接到数据源，请联系管理员。', failure_category='connection', degraded=True
            )
        ),
    )

    tool = _tool()
    result = await tool._arun('q')

    assert '查询失败' in result
    assert tool.last_error_category == 'connection'
    assert tool.outcomes == []


async def test_the_statement_is_not_returned_to_the_model(monkeypatch):
    """It goes to the citation anchor, which the user sees. The model does not
    need to repeat SQL back, and putting it in the tool result would make the
    model likely to paste it into the answer."""
    monkeypatch.setattr(
        'bisheng.datasource.domain.langchain.tool.DatasourceQueryService.answer_question',
        AsyncMock(return_value=_answer(_outcome())),
    )

    result = await _tool()._arun('q')

    assert 'SELECT' not in result


# --- building the tool set ---------------------------------------------------


async def test_one_tool_per_datasource_and_only_the_exposed_tables(monkeypatch):
    """Only `enabled` tables: an admin unticking a table must hide it from the
    model, and that filter lives in the DAO call."""
    rows = [
        SimpleNamespace(table_name='orders', table_comment=None, business_desc='订单',
                        structure_snapshot=[{'name': 'id', 'type': 'INT', 'comment': None}]),
    ]
    with patch(
        'bisheng.datasource.domain.langchain.tool.DbDatasourceTableDao.alist_by_datasource',
        AsyncMock(return_value=rows),
    ) as alist:
        tools = await build_datasource_tools([_datasource(7), _datasource(8, '报表库')], user_id=3)

    assert len(tools) == 2
    assert alist.await_count == 2
    for call in alist.await_args_list:
        assert call.kwargs.get('enabled_only') is True, 'a disabled table would reach the model'
    assert tools[0].tables[0]['business_desc'] == '订单'


async def test_the_datasource_name_is_in_the_tool_description(monkeypatch):
    """With several tools, the description is what lets the model choose, and
    what the answer's source line shows."""
    with patch(
        'bisheng.datasource.domain.langchain.tool.DbDatasourceTableDao.alist_by_datasource',
        AsyncMock(return_value=[]),
    ):
        tools = await build_datasource_tools([_datasource(7, '客户订单库')])

    assert '客户订单库' in tools[0].description
    assert tools[0].name == 'datasource_7'


async def test_building_with_no_datasources_yields_no_tools(monkeypatch):
    assert await build_datasource_tools([]) == []
