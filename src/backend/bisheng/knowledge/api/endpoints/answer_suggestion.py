"""Answer-bar suggestions endpoint (F059).

RUF001 is suppressed for this file: the field descriptions are Chinese
user-facing copy and use fullwidth punctuation, which is what the rule calls
"ambiguous".
"""

# ruff: noqa: RUF001

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.knowledge.domain.services.answer_suggestion_service import AnswerSuggestionService

router = APIRouter(prefix='/chat', tags=['chat-suggestions'])


class SuggestionRequest(BaseModel):
    chat_id: str | None = Field(default=None, description='会话ID')
    message_id: int | None = Field(default=None, description='被建议的那条回答的消息ID')
    flow_id: str | None = Field(default=None, description='应用ID，用于解析其绑定的知识库')
    question: str = Field(default='', description='本轮用户提问')
    answer: str = Field(default='', description='本轮回答（前端持有的守卫后文本）')


@router.post('/suggestions')
async def generate_suggestions(
    *,
    payload: SuggestionRequest,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    """Scenario keywords + related questions for one finished answer (F059).

    Lives in the knowledge module because the questions half is a knowledge
    retrieval feature (it calls `retrieve_knowledge_documents`); the `/chat`
    prefix is semantic naming, not module ownership. Putting it in
    `chat_session` would make that module import knowledge's retrieval layer and
    trip the API-layer cross-import rule (constitution C1 / arch-guard RULE-5).

    `answer` is supplied by the client rather than looked up from the message row
    on purpose: what the user actually READ is what the suggestions should follow,
    and for a streamed answer the stored text may differ (a blocked or masked
    round is rewritten before persisting). The text is treated purely as prompt
    input — nothing is persisted, so it carries no injection surface.

    Never fails loudly: each half degrades to an empty list, and the page simply
    renders no bar (spec AC-32/AC-41).
    """
    result = await AnswerSuggestionService.generate(
        question=payload.question,
        answer=payload.answer,
        invoke_user_id=login_user.user_id,
        tenant_id=get_current_tenant_id() or getattr(login_user, 'tenant_id', None),
        # Without it the questions half has no knowledge base to search and is
        # skipped — keywords still come back, which is the intended degradation
        # for an app with no binding (spec AC-34/AC-42).
        flow_id=payload.flow_id,
        user_name=getattr(login_user, 'user_name', '') or '',
    )
    return resp_200({'keywords': result.keywords, 'questions': result.questions})
