"""F063 tests for the task-mode (linsight) handoff of the "全部" tier.

`_to_linsight_submit` deliberately does NOT go through `_resolve_user_kb_selection`
— the linsight session_version stores the CONCRETE ids chosen at submit time
("these are the SPECIFIC ids the user chose"), so "全部" has to be materialised
here as well (design 决策 10 A). It reuses the same expansion helper as the daily
funnel, so "全部" never grows a second definition.

The async boundary sits in `_resolve_submit_org_ids` rather than inside
`_to_linsight_submit`: the submit mapper stays sync, which keeps its six existing
callers (test_unified_chat_entry.py ×5, test/linsight/test_linsight_knowledge.py
×1) working unchanged — those are the byte-for-byte regression evidence for
AC-25, so they must not be edited to fit this feature.

ACs covered:
- AC-03: "全部" resolves to the permitted org KBs at submit time.
- AC-25: a task-mode request never carries the new field, so this path behaves
  exactly as before for it (see the guard case below).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.api.v1.schema.chat_schema import APIChatCompletion, UseKnowledgeBaseParam
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao, KnowledgeTypeEnum
from bisheng.knowledge.domain.services.knowledge_permission_service import (
    KnowledgePermissionService,
)
from bisheng.workstation.domain.services import chat_service

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _kb(kb_id: int):
    return SimpleNamespace(id=kb_id, name=f"kb-{kb_id}", description="")


def _user(is_admin: bool = False):
    return SimpleNamespace(user_id=7, tenant_id=1, is_admin=lambda: is_admin)


def _data(
    org_ids: list[int] | None = None,
    space_ids: list[int] | None = None,
    all_: bool = False,
    *,
    with_kb: bool = True,
):
    """A real APIChatCompletion — task mode, as the handoff only serves that path."""
    return APIChatCompletion(
        clientTimestamp="0",
        conversationId="chat-1",
        model="727",
        text="q",
        task_mode=True,
        use_knowledge_base=(
            UseKnowledgeBaseParam(
                personal_knowledge_enabled=False,
                organization_knowledge_ids=org_ids or [],
                knowledge_space_ids=space_ids or [],
                organization_knowledge_all=all_,
            )
            if with_kb
            else None
        ),
    )


def _patch_expansion(candidates: list[int], permitted: list[int] | None):
    """Patch the SAME two entries the daily funnel uses (T-03's contract).

    Asserting through the real `_expand_all_org_knowledge` — instead of patching
    that helper itself — is what proves the handoff and the daily funnel share one
    definition of "全部". Patching the helper would make this test pass even if
    the handoff grew its own copy.
    """
    all_mock = AsyncMock(return_value=[_kb(i) for i in candidates])
    filter_mock = AsyncMock(return_value=list(permitted or []))
    patches = [
        patch.object(KnowledgeDao, "aget_all_knowledge", new=all_mock),
        patch.object(
            KnowledgePermissionService,
            "filter_knowledge_ids_by_permission_async",
            new=filter_mock,
        ),
    ]
    for p in patches:
        p.start()
    return SimpleNamespace(all_mock=all_mock, filter_mock=filter_mock, stop=lambda: [p.stop() for p in patches])


async def _resolve(data, user) -> list[int] | None:
    return await chat_service._resolve_submit_org_ids(data, user)


def _submit(data, org_ids):
    return chat_service._to_linsight_submit(data, org_ids)


# ---------------------------------------------------------------------------
# AC-03 — "全部" becomes concrete ids on the submit
# ---------------------------------------------------------------------------


async def test_all_tier_hands_over_concrete_permitted_ids():
    env = _patch_expansion(candidates=[1, 2, 3], permitted=[1, 3])
    try:
        resolved = await _resolve(_data(all_=True), _user())
        submit = _submit(_data(all_=True), resolved)
    finally:
        env.stop()

    assert resolved == [1, 3]
    assert submit.organization_knowledge_ids == [1, 3]
    assert submit.org_knowledge_enabled is True
    assert env.filter_mock.await_args.args[2] == "use_kb"


async def test_all_tier_ignores_an_explicit_id_list():
    """`all` and a concrete list are mutually exclusive.

    If both arrive, the explicit list must not be able to smuggle an id the
    expansion would not grant into the stored session_version — including the
    degenerate case where the expansion resolves to NOTHING: falling back to the
    request's ids there would hand linsight exactly the KBs the user did not pick.
    This is the case that separates the `None` sentinel from a falsy check.
    """
    env = _patch_expansion(candidates=[1, 2], permitted=[])
    try:
        resolved = await _resolve(_data(org_ids=[9], all_=True), _user())
        submit = _submit(_data(org_ids=[9], all_=True), resolved)
    finally:
        env.stop()

    assert resolved == []
    assert 9 not in (submit.organization_knowledge_ids or [])
    assert submit.org_knowledge_enabled is False


async def test_all_tier_with_nothing_permitted_is_not_enabled():
    """An empty resolution must NOT be stored as "enabled with zero ids".

    The `None` sentinel is what separates "the caller resolved '全部' to nothing"
    from "the request carries an explicit list"; collapsing the two would either
    re-introduce the explicit ids the user did not pick, or flip the boolean.
    """
    env = _patch_expansion(candidates=[1, 2], permitted=[])
    try:
        resolved = await _resolve(_data(all_=True), _user())
        submit = _submit(_data(all_=True), resolved)
    finally:
        env.stop()

    assert resolved == []
    assert submit.org_knowledge_enabled is False
    assert not submit.organization_knowledge_ids


# ---------------------------------------------------------------------------
# AC-25 — task mode is unaffected
# ---------------------------------------------------------------------------


async def test_task_mode_request_never_triggers_the_expansion():
    """AC-25: task mode's own input has no such field, so this branch is inert.

    The task-mode input reads its own store (`TaskModeChatInput.tsx`) and sends no
    `organization_knowledge_all`. `None` therefore means "behave as before" for
    every request that predates this feature.
    """
    env = _patch_expansion(candidates=[1, 2, 3], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(_data(org_ids=[2745], space_ids=[3124]), _user())
        # Legacy shape: the caller passes None (no second argument at all).
        submit = chat_service._to_linsight_submit(_data(org_ids=[2745], space_ids=[3124]))
    finally:
        env.stop()

    assert resolved is None
    assert env.all_mock.await_count == 0
    assert submit.organization_knowledge_ids == [2745]
    assert submit.knowledge_space_ids == [3124]
    assert submit.org_knowledge_enabled is True


async def test_legacy_no_knowledge_request_still_disables_org_knowledge():
    env = _patch_expansion(candidates=[1], permitted=[1])
    try:
        resolved = await _resolve(_data(with_kb=False), _user())
        submit = chat_service._to_linsight_submit(_data(with_kb=False))
    finally:
        env.stop()

    assert resolved is None
    assert submit.org_knowledge_enabled is False
    assert submit.organization_knowledge_ids is None


async def test_explicit_empty_selection_is_not_treated_as_all():
    """`all=false` + `[]` means "use none" — it must not fan out to everything."""
    env = _patch_expansion(candidates=[1, 2, 3], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(_data(all_=False), _user())
        submit = _submit(_data(all_=False), resolved)
    finally:
        env.stop()

    assert resolved is None
    assert env.all_mock.await_count == 0
    assert submit.organization_knowledge_ids is None


async def test_expansion_failure_degrades_to_disabled_not_to_everything():
    """Fail-closed: a permission-check failure must not hand linsight every KB."""
    all_mock = AsyncMock(return_value=[_kb(1), _kb(2)])
    filter_mock = AsyncMock(side_effect=RuntimeError("openfga down"))
    with (
        patch.object(KnowledgeDao, "aget_all_knowledge", new=all_mock),
        patch.object(
            KnowledgePermissionService,
            "filter_knowledge_ids_by_permission_async",
            new=filter_mock,
        ),
    ):
        resolved = await _resolve(_data(all_=True), _user())
        submit = _submit(_data(all_=True), resolved)

    assert resolved == []
    assert submit.org_knowledge_enabled is False


async def test_candidate_query_passes_only_the_type():
    """Same candidate contract as the daily funnel (T-03 case 8)."""
    env = _patch_expansion(candidates=[1], permitted=[1])
    try:
        await _resolve(_data(all_=True), _user())
    finally:
        env.stop()

    assert env.all_mock.await_args.kwargs == {"knowledge_type": KnowledgeTypeEnum.NORMAL}
