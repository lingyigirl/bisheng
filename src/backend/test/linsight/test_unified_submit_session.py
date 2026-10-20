"""F035 Track J (TJ-2) — unified submit session type.

Under the unified conversation model, a task turn lives inside a daily
workstation conversation (flow_type=WORKSTATION=15), never its own
flow_type=LINSIGHT=20 session. When ``submit_user_question`` is called without
a ``session_id`` (first turn, no daily session yet), it must create the session
as flow_type=15 — matching how ``workstation/chat_service`` mints daily chats —
so the conversation reads as one stream regardless of task mode (C8, design §3.1).

DAO / telemetry / file-processing are patched; the unit under test is the
session-creation branch of submit_user_question, not DB or MinIO access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bisheng.database.models.flow import FlowType
from bisheng.database.models.message import ChatMessage, ChatMessageDao
from bisheng.database.models.session import MessageSession, MessageSessionDao
from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersionDao
from bisheng.linsight.domain.schemas.linsight_schema import LinsightQuestionSubmitSchema
from bisheng.linsight.domain.services import workbench_impl
from bisheng.linsight.domain.services.workbench_impl import LinsightWorkbenchImpl


@pytest.fixture
def patch_submit_io(monkeypatch: pytest.MonkeyPatch):
    """Isolate submit_user_question from DB/telemetry/MinIO; capture session + chat messages."""
    captured: dict = {"messages": []}

    async def _fake_insert_session(_cls, data: MessageSession):
        captured["session"] = data
        return data

    async def _fake_insert_version(_cls, data):
        captured["version"] = data
        return data

    async def _fake_insert_message(_cls, data: ChatMessage):
        captured["messages"].append(data)
        return data

    monkeypatch.setattr(MessageSessionDao, "async_insert_one", classmethod(_fake_insert_session))
    monkeypatch.setattr(LinsightSessionVersionDao, "insert_one", classmethod(_fake_insert_version))
    monkeypatch.setattr(ChatMessageDao, "ainsert_one", classmethod(_fake_insert_message))
    monkeypatch.setattr(LinsightWorkbenchImpl, "_process_submitted_files", AsyncMock(return_value=None))
    monkeypatch.setattr(workbench_impl.telemetry_service, "log_event", AsyncMock(return_value=None))
    monkeypatch.setattr(workbench_impl, "get_redis_client", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.agent_config_service.LLMService.get_workbench_llm",
        AsyncMock(return_value=MagicMock(linsight_default_model_id="3")),
    )
    monkeypatch.setattr(
        "bisheng.linsight.domain.services.event_replay.EventReplayService.append", AsyncMock(return_value="1-0")
    )

    return captured


async def test_explicit_empty_members_do_not_inherit_previous_members(patch_submit_io, monkeypatch):
    from bisheng.linsight.domain.schemas.agent_config import AgentConfig

    user = MagicMock(user_id=1)
    session = MessageSession(chat_id="chat1", name="Test", flow_type=FlowType.WORKSTATION.value, user_id=1)
    monkeypatch.setattr(MessageSessionDao, "async_get_one", AsyncMock(return_value=session))
    previous = AsyncMock()
    monkeypatch.setattr(LinsightSessionVersionDao, "get_session_versions_by_session_id", previous)
    submit = LinsightQuestionSubmitSchema(question="No collaborators", session_id="chat1", agent_config=AgentConfig())
    _, version = await LinsightWorkbenchImpl.submit_user_question(submit, user)
    assert version.agent_config["subagents"] == []
    assert version.agent_config["recovery_protocol"] == 1
    previous.assert_not_awaited()


async def test_submit_without_session_id_creates_workstation_session(patch_submit_io):
    """A fresh task turn (no session_id) creates a flow_type=15 session, not 20."""
    login_user = MagicMock()
    login_user.user_id = 1
    submit_obj = LinsightQuestionSubmitSchema(question="帮我写个周报", session_id=None)

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, login_user)

    assert patch_submit_io["session"].flow_type == FlowType.WORKSTATION.value


async def test_submit_persists_user_question_turn(patch_submit_io):
    """submit writes the user question as a ChatMessage(category='question') in the stream."""
    login_user = MagicMock()
    login_user.user_id = 1
    submit_obj = LinsightQuestionSubmitSchema(question="帮我写个周报", session_id=None)

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, login_user)

    user_turns = [m for m in patch_submit_io["messages"] if m.category == "question"]
    assert len(user_turns) == 1
    assert user_turns[0].is_bot is False
    assert "帮我写个周报" in user_turns[0].message


async def test_submit_persists_display_files_on_user_turn(patch_submit_io):
    """The user question turn carries the uploaded attachments (daily display
    shape) so they survive a refresh — mirrors daily-chat question envelope."""
    import json

    login_user = MagicMock()
    login_user.user_id = 1
    submit_obj = LinsightQuestionSubmitSchema(question="总结下文件内容", session_id=None)
    display_files = [
        {
            "file_id": "d1",
            "filepath": "/tmp-dir/abc.pdf?X-Amz-Algorithm=AWS4",
            "type": "application/pdf",
            "filename": "abc.pdf",
            "file_name": "abc.pdf",
        }
    ]

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, login_user, display_files=display_files)

    user_turns = [m for m in patch_submit_io["messages"] if m.category == "question"]
    assert len(user_turns) == 1
    # files column populated for reload rendering
    assert user_turns[0].files is not None
    assert "abc.pdf" in user_turns[0].files
    # and embedded in the {"query","files"} envelope the client reads
    envelope = json.loads(user_turns[0].message)
    assert envelope["files"] and envelope["files"][0]["file_id"] == "d1"


async def test_follow_up_inherits_previous_agent_config(patch_submit_io, monkeypatch):
    """F035 follow-up continuity: a continuation that omits member configuration
    re-resolves the previous version's collaborator snapshot, so the user
    does not have to re-add every collaborator between rounds."""
    from types import SimpleNamespace

    from bisheng.linsight.domain.services.agent_config_service import AgentConfigService
    from bisheng.linsight.domain.services.event_replay import EventReplayService

    captured_resolve: dict = {}

    async def _fake_resolve(_self, config, user, tenant_id, main_model, **kwargs):
        from bisheng.linsight.domain.schemas.agent_config import AgentConfig

        config = config or AgentConfig()
        if config.subagents:
            captured_resolve["config"] = config
        snapshot = config.model_dump()
        snapshot.update(event_protocol=2, recovery_protocol=1, main_model_id=str(main_model), skill_digests={})
        return snapshot

    monkeypatch.setattr(AgentConfigService, "resolve", _fake_resolve)

    session = MessageSession(chat_id="chat1", name="New Chat", flow_type=FlowType.WORKSTATION.value, user_id=1)
    monkeypatch.setattr(MessageSessionDao, "async_get_one", AsyncMock(return_value=session))

    prev = SimpleNamespace(
        id="prev00000000",
        agent_config={
            "schema_version": 1,
            "subagents": [
                {
                    "id": "agent-abc123",
                    "name": "资料研究员",
                    "model_id": "3",
                    "tool_refs": [38, 6],
                    "description": "搜索资料、核对事实，返回带来源的摘要。",
                    "skill_names": [],
                    "system_prompt": "",
                    "connector_refs": [],
                }
            ],
            "main_model_id": "3",
            "skill_digests": {},
        },
    )
    monkeypatch.setattr(LinsightSessionVersionDao, "get_session_versions_by_session_id", AsyncMock(return_value=[prev]))

    # The inherited snapshot re-enters the replay-init branch: keep it off Redis.
    monkeypatch.setattr(workbench_impl, "get_redis_client", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(EventReplayService, "append", AsyncMock(return_value=None))

    login_user = MagicMock()
    login_user.user_id = 1
    submit_obj = LinsightQuestionSubmitSchema(question="接着问", session_id="chat1")

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, login_user)

    version = patch_submit_io["version"]
    assert version.session_id == "chat1"
    assert version.agent_config is not None
    assert version.agent_config["subagents"][0]["name"] == "资料研究员"
    assert version.model == "3"
    assert captured_resolve["config"].subagents[0].name == "资料研究员"


def test_unified_entry_canonicalizes_persisted_agent_config_snapshot():
    """A follow-up echoing the previous round's persisted collaborator snapshot
    (resolver-added fields included) must be canonicalized, not rejected —
    without this the strict submit schema 500'd with four ``extra_forbidden``
    errors and the message silently vanished (2026-10-15)."""
    from bisheng.api.v1.schema.chat_schema import APIChatCompletion
    from bisheng.workstation.domain.services.chat_service import _to_linsight_submit

    data = APIChatCompletion(
        text="接着问",
        clientTimestamp="2026-10-15T13:43:00",
        model="3",
        conversationId="chat1",
        agent_config={
            "schema_version": 1,
            "subagents": [
                {
                    "id": "agent-x1",
                    "name": "资料研究员",
                    "model_id": "3",
                    "tool_refs": [38, 6],
                    "description": "搜索资料、核对事实",
                    "skill_names": [],
                    "system_prompt": "",
                    "connector_refs": [],
                }
            ],
            # Resolver-added fields a persisted snapshot carries; the strict
            # AgentConfig schema forbids them as extras.
            "main_model_id": "3",
            "skill_digests": {"agent-x1": {}},
            "event_protocol": 2,
            "recovery_protocol": 1,
        },
    )

    submit = _to_linsight_submit(data)

    assert submit.agent_config is not None
    assert submit.agent_config.schema_version == 1
    assert submit.agent_config.subagents[0].name == "资料研究员"
