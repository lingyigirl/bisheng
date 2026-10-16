import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Body
from fastapi.middleware.wsgi import WSGIMiddleware

from bisheng.open_api.domain.scopes import open_api_scope

a = WSGIMiddleware

from bisheng.api.services.chat_imp import comment_answer
from bisheng.api.v1.schemas import ChatInput, resp_200
from bisheng.chat_session.domain.chat import ChatSessionService
from bisheng.core.database import get_sync_db_session
from bisheng.database.models.message import ChatMessage, ChatMessageDao
from bisheng.database.models.session import MessageSessionDao
from bisheng.open_endpoints.domain.schemas.message import SyncMessage
from bisheng.open_endpoints.domain.utils import (
    assert_open_api_app_visible,
    assert_open_api_app_visible_sync,
    get_default_operator,
)

router = APIRouter(prefix="/chat", tags=["OpenAPI", "Chat"])


@open_api_scope("chat:invoke", guest=True)
@router.post("/gen_title")
async def gen_title_v2(conversationId: str = Body(..., embed=True)):
    """Get conversation title without authentication (v2 open endpoint)."""
    await asyncio.sleep(5)
    messages = await MessageSessionDao.async_get_one(conversationId)
    # F076 T-29(AC-23):密钥调用时,会话所属应用要在可见范围内(否则 404/11809)。
    if messages is not None:
        await assert_open_api_app_visible(messages.flow_id, get_default_operator())
    return resp_200(data={"title": messages.name if messages else "New Chat"})


@open_api_scope("chat:invoke", guest=True)
@router.get("/history")
async def get_chat_history_v2(*, chat_id: str, flow_id: str, id: str | None = None, page_size: int | None = 20):
    """Get chat history without authentication (v2 open endpoint)."""
    # F076 T-29(AC-23):密钥调用时校验会话所属应用的可见性。
    await assert_open_api_app_visible(flow_id, get_default_operator())
    history = await ChatSessionService.get_chat_history(chat_id, flow_id, id, page_size)
    return resp_200(history)


# @router.get('/source')
# async def query_source(message_id: int, session: Session = Depends(get_session)):
#     """source of message_id"""
#     db_recall = session.query(RecallChunk).where(RecallChunk.message_id == message_id).all()


@open_api_scope("chat:invoke")
@router.post("/liked", status_code=200)
def like_response(*, data: dict):
    message_id = data.get("message_id")
    liked = data.get("liked")
    with get_sync_db_session() as session:
        message = session.get(ChatMessage, message_id)
        # F076 T-29(AC-23):消息所属应用要在可见范围内(不可见与不存在同码 11809)。
        if message is not None:
            assert_open_api_app_visible_sync(message.flow_id, get_default_operator())
        message.liked = liked
        session.add(message)
        session.commit()
    return {"status_code": 200, "status_message": "success"}


@open_api_scope("chat:invoke")
@router.post("/solved", status_code=200)
def solve_response(*, data: dict):
    chat_id = data.get("chat_id")
    solved = data.get("solved")
    with get_sync_db_session() as session:
        messages = session.query(ChatMessage).where(ChatMessage.chat_id == chat_id).all()
        if messages:
            assert_open_api_app_visible_sync(messages[0].flow_id, get_default_operator())
    for message in messages:
        message.solved = solved
    with get_sync_db_session() as session:
        session.add(message)
        session.commit()
    return {"status_code": 200, "status_message": "success"}


@open_api_scope("chat:invoke")
@router.post("/comment", status_code=200)
def comment(*, data: ChatInput):
    with get_sync_db_session() as session:
        message = session.get(ChatMessage, data.message_id)
        if message is not None:
            assert_open_api_app_visible_sync(message.flow_id, get_default_operator())
    comment_answer(data.message_id, data.comment)
    return resp_200()


@open_api_scope("chat:invoke")
@router.post("/sync/messages", status_code=200)
def sync_message(
    *,
    flow_id: UUID = Body(embed=True),
    chat_id: str = Body(embed=True),
    message_list: list[SyncMessage] = Body(embed=True),
    user_id: int = Body(default=None, embed=True),
):
    flow_id = flow_id.hex
    # F076 T-29:密钥调用时按可见性收口,且身份必须取自调用方身份(principal),
    # 不再回落到 default_operator —— 否则密钥调用会把消息记在默认操作员名下。
    login_user = get_default_operator()
    assert_open_api_app_visible_sync(flow_id, login_user)
    user_id = user_id if user_id else login_user.user_id

    batch_message = [
        ChatMessage(
            is_bot=message.is_send,
            source=0,
            message=message.message,
            extra=json.dumps(message.extra),
            type="answer",
            category="answer",
            flow_id=flow_id,
            user_id=user_id,
            chat_id=chat_id,
            create_time=message.create_time,
        )
        for message in message_list
    ]
    ChatMessageDao.insert_batch(batch_message)
    return resp_200()
