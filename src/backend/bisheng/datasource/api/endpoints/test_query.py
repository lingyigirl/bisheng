"""Self-test endpoint: ask one question, see the SQL and the rows.

Why this exists: a datasource has no way to be exercised from its own config
page. Before this, the only path to a natural-language query was to bind the
datasource to an assistant and go chat -- so "did I configure this right?" cost
minutes and answered somewhere else. This is the missing short loop. Sibling of
`test-connection`, one step further along.

Named "test" rather than "query" on purpose: it is a diagnostic, not a product
surface. No history, no conversation, nothing persisted but the audit trail.

A failed query is NOT an error response. `DatasourceQueryService.answer_question`
never raises for SQL that timed out, was rejected by the guard, or came back
bad: it returns `degraded=true` plus the statements it tried. That is the point
of the box -- the admin needs to see what the model attempted, and a 500 would
throw exactly that away.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.datasource import DatasourceNotFoundError
from bisheng.common.schemas.api import resp_200
from bisheng.datasource.api.endpoints.datasources import require_manage
from bisheng.datasource.domain.models.dao import DbDatasourceDao
from bisheng.datasource.domain.services import schema_service
from bisheng.datasource.domain.services.query_service import AnswerContext, DatasourceQueryService

router = APIRouter(prefix='/datasource', tags=['datasource'])

#: Long enough for a real question, short enough that the prompt cannot be
#: stuffed through this door. The question is interpolated into a prompt, so its
#: length is a budget concern as much as a sanity check.
MAX_QUESTION_LENGTH = 500


class DatasourceTestQueryPayload(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_LENGTH)


def _result_dict(context: AnswerContext) -> dict:
    """Flatten an AnswerContext for the wire.

    `statements_tried` is always present, success included: on failure it is the
    only useful output, and reading it is why an admin opened this box.
    """
    outcome = context.outcome
    return {
        'answer': context.answer,
        'degraded': context.degraded,
        'failure_category': context.failure_category,
        'statement': outcome.statement if outcome else None,
        'columns': outcome.columns if outcome else [],
        'rows': outcome.rows if outcome else [],
        'row_count': outcome.row_count if outcome else 0,
        'truncated': outcome.truncated if outcome else False,
        'statements_tried': context.statements_tried,
    }


@router.post('/{datasource_id}/test-query')
async def test_datasource_query(
    datasource_id: int,
    payload: DatasourceTestQueryPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Run one question through the NL->SQL path.

    Gated on `can_manage` -- the same relation every table endpoint uses, and
    STRICTER than the chat path's `viewer`. So this adds no way to read a table
    the caller could not already read through the management page.

    `datasource.enabled` is deliberately NOT checked: testing a datasource
    before switching it on is the whole point. The table list is still
    `enabled_only`, so a datasource with nothing exposed yields an empty schema
    and the model is told about no tables.
    """
    await require_manage(datasource_id, login_user)

    datasource = await DbDatasourceDao.aget_by_id(datasource_id)
    if datasource is None:
        # Same signal as "not yours" -- `require_manage` already refused the
        # unauthorized case, and distinguishing the two here would let a caller
        # probe for ids they cannot see.
        raise DatasourceNotFoundError()

    tables = await schema_service.alist_model_tables(datasource_id)
    context = await DatasourceQueryService.answer_question(
        datasource, payload.question, tables, user_id=login_user.user_id
    )
    return resp_200(_result_dict(context))
