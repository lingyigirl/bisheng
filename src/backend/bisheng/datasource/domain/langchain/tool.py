"""The datasource as a tool the model can choose to call.

Spec AC-26 requires it to sit at the SAME level as document-knowledge
retrieval, so that within one turn the model picks either or both. That is why
this is a plain `BaseTool` registered into the same `func_tools` list the
knowledge tools go into (`workflow/nodes/agent/agent.py`), not a node type or a
separate routing layer.

**Async, and it matters.** `BaseTool._arun` is what the agent calls; a `_run`
that blocks would put the query on the event loop, which is exactly what spec
AC-21 forbids and what design decision 12 records the legacy `sql_agent` doing
(`tool.py:71-75` — its `arun` delegates to the sync `run`). This delegates the
other way: the blocking work already happens in a worker thread inside the
executor.

**No forced pre-routing** (AC-25). The tool is OFFERED; whether it is called is
the model's decision. Nothing here inspects the question to decide whether to
query -- a platform-side "this looks like a database question" check would
contradict the spec and would break the moment a question spans both sources.
"""

# ruff: noqa: RUF001 -- the tool name/description are Chinese text the model
# reads; fullwidth punctuation is correct in them, not a typo.

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.datasource.domain.models.dao import DbDatasourceTableDao
from bisheng.datasource.domain.models.datasource import DbDatasource
from bisheng.datasource.domain.services.query_service import DatasourceQueryService

#: The relation a user needs to USE a datasource in a conversation. Deliberately
#: the same value `conversation_service.USE_RELATION` holds, and deliberately
#: repeated rather than imported: `conversation_service` imports this module, so
#: importing back would be a cycle. Both name the same rule, and the test that
#: asserts this relation would fail if the two drift.
USE_RELATION = 'viewer'

#: What the model is told when the caller may not use the datasource. The same
#: sentence as "it does not exist" on purpose -- spec AC-49: distinguishing the
#: two would let a caller probe which ids exist in another tenant.
_REFUSED_MESSAGE = '查询失败：数据源不可用或无权访问，请联系管理员。'


class DatasourceToolInput(BaseModel):
    query: str = Field(description='用户的问题（保留其原始措辞，不要自行改写为 SQL）')


class DatasourceQueryTool(BaseTool):
    """Answers a question by querying one datasource.

    Holds no result state: what came back is returned to the model as text, and
    the structured outcomes (rows, citation anchor) are collected by the caller
    through `outcomes`, which the conversation layer reads when composing the
    answer's sources -- one entry per execution, in call order, so a turn that
    queried twice still cites both.
    """

    name: str = 'datasource_query'
    description: str = (
        '在已配置的业务数据库中查询数据。适用于用户询问具体的业务数据'
        '（如订单、客户、库存等记录）。传入用户的问题即可，不需要自己写 SQL。'
    )
    args_schema: type[BaseModel] = DatasourceToolInput

    # Typed `Any`, deliberately. `BaseTool` is a pydantic model, so annotating
    # this as `DbDatasource` would make pydantic VALIDATE AND REBUILD it -- and
    # `password` holds Fernet ciphertext that only gets decrypted at connect
    # time. Rebuilding the row here would carry the credential into another
    # object for no reason, and a validation error (the real model has fields a
    # SimpleNamespace does not) would make the tool unconstructible.
    #
    # `description` is also why this is not named `datasource_description`:
    # `BaseTool` already owns `description`, and a sub-field of the same name
    # collides.
    datasource: Any
    tables: list[dict] = Field(default_factory=list)
    user_id: int | None = None
    #: The invoking caller, used for the per-call authorization check. Typed
    #: `Any` for the same reason `datasource` is: a `UserPayload` is a pydantic
    #: model whose validation here would be pure overhead, and this attribute is
    #: only ever passed straight back to `PermissionService`.
    #:
    #: Absent or None means "cannot be checked", which REFUSES every call rather
    #: than allowing them -- see `_authorized`. The caller that builds tools from
    #: an assistant's links always has this (`build_tools_for_assistant` raises
    #: without it), so a tool built with None is a wiring mistake, not a state
    #: the product puts itself in.
    login_user: Any = None
    #: Every successful execution this turn, oldest first. A LIST rather than a
    #: single slot: a model that queries twice in one turn must leave two
    #: anchors, and a last-write-wins slot would silently drop the first.
    outcomes: list[Any] = Field(default_factory=list)
    last_error_category: str | None = None

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager=None) -> str:
        # The agent calls `_arun`; this exists because `BaseTool` requires one
        # of the two, and because a synchronous caller (the legacy workflow node
        # path) must not silently get `NotImplementedError`.
        import asyncio

        return asyncio.run(self._arun(query))

    async def _authorized(self) -> bool:
        """May the invoking caller use this datasource RIGHT NOW?

        Re-checked on every call, not once when the tool was built. The agent is
        reused across the turns of one chat (`common/chat/client.py` only
        rebuilds it when the session switches), so a build-time check would leave
        a revoked datasource answering questions in an already-open chat -- which
        is precisely what spec AC-52 forbids.

        A per-call check costs one `PermissionService.check`, which is cached
        (L2), and buys the guarantee the docstring of `conversation_service`
        promises. It is also strictly earlier than the turn, so a revoke still
        takes effect on the next turn as the spec requires.
        """
        if self.login_user is None:
            # Fail closed: without a caller there is nothing to check against,
            # and allowing the query would be the one outcome that is never
            # right. Logged at warning because it means a call site is wrong.
            logger.warning(
                'datasource tool {} has no login_user; refusing the query '
                '(build it through build_datasource_tools)',
                self.name,
            )
            return False

        from bisheng.permission.domain.services.permission_service import PermissionService

        try:
            return await PermissionService.check(
                user_id=self.user_id,
                relation=USE_RELATION,
                object_type='db_datasource',
                object_id=str(self.datasource.id),
                login_user=self.login_user,
            )
        except Exception:
            # A permission backend that is down must not become an open door.
            logger.opt(exception=True).warning(
                'datasource permission check failed for id={}; denying',
                self.datasource.id,
            )
            return False

    async def _arun(self, query: str, run_manager=None) -> str:
        """Query, and return the model's view of the result.

        The model gets the ROWS as text (it needs them to answer) and a short
        description. It does NOT get the statement -- that goes to the citation
        anchor, which the user sees and the model does not need to repeat.
        """
        # `outcomes` is deliberately NOT reset here. It accumulates across every
        # call within one turn -- resetting per call is exactly the single-slot
        # bug this list replaced; the caller clears it once, per run.
        self.last_error_category = None

        if not await self._authorized():
            # No outcome is appended, so a refused call leaves no citation anchor
            # and no result table -- there is nothing to cite. The failure is
            # also NOT filed through the query-failure trail: nothing was sent to
            # the database, and that list exists to drive schema-description work
            # (AC-42), not to log authorization refusals.
            return _REFUSED_MESSAGE

        context = await DatasourceQueryService.answer_question(
            self.datasource, query, self.tables, user_id=self.user_id
        )

        if context.outcome is None:
            # A failure. The message is already user-safe; handing it back to
            # the model lets it answer "I could not query that" and, per AC-40,
            # carry on with any document knowledge it has.
            self.last_error_category = context.failure_category
            logger.info(
                'datasource tool call failed for datasource={} category={}',
                self.datasource.id,
                context.failure_category,
            )
            return f'查询失败：{context.answer}'

        self.outcomes.append(context.outcome)
        return self._render(context.outcome)

    @staticmethod
    def _render(outcome) -> str:
        """Rows as a compact text table, capped by the executor already.

        Text rather than JSON because the model reads it, and a markdown table
        is both shorter and closer to how the surrounding prompt is written.
        """
        if not outcome.rows:
            return '查询已执行，但没有匹配的数据。'

        header = ' | '.join(outcome.columns)
        divider = ' | '.join('---' for _ in outcome.columns)
        lines = [f'| {header} |', f'| {divider} |']
        for row in outcome.rows:
            cells = ' | '.join('' if c is None else str(c) for c in row)
            lines.append(f'| {cells} |')

        note = '\n（结果超过显示上限，已截断）' if outcome.truncated else ''
        return '\n'.join(lines) + note


async def build_datasource_tools(
    datasources: list[DbDatasource],
    *,
    user_id: int | None = None,
    login_user: Any = None,
) -> list[DatasourceQueryTool]:
    """One tool per authorized datasource.

    One per datasource rather than one tool with a datasource argument: the
    model has no way to pick correctly from a list of opaque ids, and the tool
    NAME is what shows up in the answer's sources. Per-datasource tools also
    mean the exposed table list is resolved once, at build time, instead of on
    every call.

    `login_user` is the invoking caller. It is REQUIRED whenever there is
    something to build, because without it the tool it produces cannot check
    authorization and will refuse every call -- so a caller that forgot it gets
    a loud error here rather than a silent deny at query time. Passing it costs
    nothing at the normal call site: `build_tools_for_assistant` already holds a
    `UserPayload` and already raises without one.

    An empty `datasources` is the one exception, and it is the common case: an
    assistant with no datasource bound must keep building fine (spec AC-60),
    and there is nothing to authorize.
    """
    if datasources and login_user is None:
        raise ValueError(
            'build_datasource_tools requires login_user: the built tool re-checks '
            'authorization on every call (spec AC-52), and without a caller it '
            'would refuse every query'
        )

    tools: list[DatasourceQueryTool] = []
    for datasource in datasources:
        rows = await DbDatasourceTableDao.alist_by_datasource(datasource.id, enabled_only=True)
        tables = [
            {
                'table_name': row.table_name,
                'table_comment': row.table_comment,
                'business_desc': row.business_desc,
                'structure_snapshot': row.structure_snapshot or [],
            }
            for row in rows
        ]
        # `description` is passed EXPLICITLY. It is a required `BaseTool` field,
        # and the datasource object also carries a `description` attribute --
        # without this, pydantic would read the datasource's own description
        # (or fail on a model that has none) instead of the tool's.
        #
        # The datasource's name goes in the description because that is what
        # lets the model choose between several tools, and what shows up in the
        # answer's source line.
        tools.append(
            DatasourceQueryTool(
                name=f'datasource_{datasource.id}',
                description=(
                    f'在业务数据库「{datasource.name}」中查询数据。'
                    '适用于询问该库中的具体业务记录（订单、客户、库存等）。'
                    '传入用户的问题即可，不需要自己写 SQL。'
                ),
                datasource=datasource,
                tables=tables,
                user_id=user_id,
                login_user=login_user,
            )
        )
    return tools
