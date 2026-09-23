"""F063 — the landing page's org-KB tier reaches the stored task scope.

The linsight landing page (`/linsight/new`) cannot enumerate the user's
organization knowledge bases, so its 「全部」 tier travels as a flag and the
server materialises the permitted ids at submit time. Without that expansion the
flag would be inert: the task run resolves its scope from the version row's id
lists ONLY (`task_exec._resolve_user_knowledge_bases` — the coarse booleans are
deliberately not consulted), so a session created from the landing page would
search no organization KB for its whole life, while its UI claimed the opposite.

DAO / telemetry / file-processing are patched (same harness as
`test_unified_submit_session.py`); the unit under test is the submit-time
expansion, not DB or MinIO access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bisheng.database.models.message import ChatMessage, ChatMessageDao
from bisheng.database.models.session import MessageSession, MessageSessionDao
from bisheng.linsight.domain.models.linsight_session_version import LinsightSessionVersionDao
from bisheng.linsight.domain.schemas.linsight_schema import LinsightQuestionSubmitSchema
from bisheng.linsight.domain.services import workbench_impl
from bisheng.linsight.domain.services.workbench_impl import LinsightWorkbenchImpl
from bisheng.workstation.domain.services import chat_service


@pytest.fixture
def patch_submit_io(monkeypatch: pytest.MonkeyPatch):
    """Isolate submit_user_question from DB/telemetry/MinIO; capture the version row."""
    captured: dict = {}

    async def _fake_insert_session(_cls, data: MessageSession):
        return data

    async def _fake_insert_version(_cls, data):
        captured["version"] = data
        return data

    async def _fake_insert_message(_cls, data: ChatMessage):
        return data

    monkeypatch.setattr(MessageSessionDao, "async_insert_one", classmethod(_fake_insert_session))
    monkeypatch.setattr(LinsightSessionVersionDao, "insert_one", classmethod(_fake_insert_version))
    monkeypatch.setattr(ChatMessageDao, "ainsert_one", classmethod(_fake_insert_message))
    monkeypatch.setattr(LinsightWorkbenchImpl, "_process_submitted_files", AsyncMock(return_value=None))
    monkeypatch.setattr(workbench_impl.telemetry_service, "log_event", AsyncMock(return_value=None))

    return captured


def _user() -> MagicMock:
    user = MagicMock()
    user.user_id = 1
    return user


def _stub_expand(monkeypatch: pytest.MonkeyPatch, rows: list) -> AsyncMock:
    """Patch the shared 「全部」 helper on its own module — the expansion site
    imports it by name at call time, so this patches what actually runs."""
    stub = AsyncMock(return_value=rows)
    monkeypatch.setattr(chat_service, "_expand_all_org_knowledge", stub)
    return stub


async def test_all_tier_is_materialised_into_the_stored_ids(patch_submit_io, monkeypatch):
    """Tier 「全部」 → the version row carries the expanded permitted ids."""
    stub = _stub_expand(monkeypatch, [MagicMock(id=11), MagicMock(id=12)])
    submit_obj = LinsightQuestionSubmitSchema(question="查一下制度", organization_knowledge_all=True)

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, _user())

    assert patch_submit_io["version"].organization_knowledge_ids == [11, 12]
    # Value alone would also pass on a hard-coded list; the call pins the reuse of
    # the single 「全部」 definition.
    assert stub.await_count == 1


async def test_expansion_failure_stays_empty_instead_of_falling_back(patch_submit_io, monkeypatch):
    """The helper fails closed (returns []); an empty result must stay empty.

    Regression guard for the `or None` shape: falling back to the request's own
    ids here would turn "nothing is permitted" into "search whatever was listed",
    i.e. a permission widening on the failure path.
    """
    _stub_expand(monkeypatch, [])
    submit_obj = LinsightQuestionSubmitSchema(
        question="查一下制度",
        organization_knowledge_all=True,
        organization_knowledge_ids=[999],
    )

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, _user())

    assert patch_submit_io["version"].organization_knowledge_ids is None


async def test_explicit_ids_are_untouched_without_the_flag(patch_submit_io, monkeypatch):
    """AC-25: every pre-existing submit path is byte-for-byte unchanged.

    The daily task-mode handoff resolves its ids itself and never sets the flag,
    so nothing must expand a second time (and the supplied list must survive).
    """
    stub = _stub_expand(monkeypatch, [MagicMock(id=99)])
    submit_obj = LinsightQuestionSubmitSchema(
        question="查一下制度",
        organization_knowledge_ids=[7, 8],
        knowledge_space_ids=[31],
    )

    await LinsightWorkbenchImpl.submit_user_question(submit_obj, _user())

    version = patch_submit_io["version"]
    assert version.organization_knowledge_ids == [7, 8]
    assert version.knowledge_space_ids == [31]
    assert stub.await_count == 0
