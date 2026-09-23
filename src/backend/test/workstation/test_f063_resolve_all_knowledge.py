"""F063 tests for the `organization_knowledge_all` expansion path.

Covers `_resolve_user_kb_selection` (chat_service.py) when the client asks for
"every organization knowledge base I am allowed to use" instead of enumerating
ids one by one. The expansion feeds BOTH the `search_knowledge_bases` tool
whitelist and the user prompt's <selected_knowledge_bases> section, so a wrong
expansion leaks KB names into the prompt — see the leakage case below.

ACs covered:
- AC-03: no knowledge space selected -> search every permitted org KB.
- AC-09: more than 50 org KBs -> use them all, never reject or silently narrow.
- AC-10: the expanded set equals the same per-KB visibility verdict used by
  the other knowledge reading entries.
- AC-11: evaluated per request (no caching of the set).
- AC-12: "not using org KBs" is a scope choice, never an authorization effect.

Contract declared here (implementation lands with the paired task):
- `KnowledgeDao.aget_all_knowledge(knowledge_type=KnowledgeTypeEnum.NORMAL)`
  is the ONLY candidate query, called with exactly that one kwarg.
- `KnowledgePermissionService.filter_knowledge_ids_by_permission_async(
  login_user, ids, "use_kb")` is the ONLY narrowing step, skipped for admins
  (same shape as `workstation_service.queryChunksFromDB`).
- On the `all` path the per-KB tag aggregation runs for the explicitly
  selected knowledge spaces only, never for the expanded org set.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.api.v1.schema.chat_schema import UseKnowledgeBaseParam
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao, KnowledgeTypeEnum
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFileDao
from bisheng.knowledge.domain.services.knowledge_permission_service import (
    KnowledgePermissionService,
)
from bisheng.workstation.domain.services import chat_service

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _kb(kb_id: int, name: str | None = None):
    """A Knowledge row stand-in: only id/name/description are read."""
    return SimpleNamespace(id=kb_id, name=name or f"kb-{kb_id}", description=f"desc-{kb_id}")


def _user(is_admin: bool = False):
    """UserPayload stand-in. `is_admin` is a METHOD on the real class."""
    return SimpleNamespace(user_id=7, tenant_id=1, is_admin=lambda: is_admin)


def _data(org_ids: list[int] | None = None, space_ids: list[int] | None = None, all_: bool = False):
    """Only `.use_knowledge_base` is read by the function under test."""
    return SimpleNamespace(
        use_knowledge_base=UseKnowledgeBaseParam(
            personal_knowledge_enabled=False,
            organization_knowledge_ids=org_ids or [],
            knowledge_space_ids=space_ids or [],
            organization_knowledge_all=all_,
        )
    )


def _patch_env(
    candidates: list[int],
    permitted: list[int] | None,
    space_rows: list[int] | None = None,
):
    """Patch the two existing entry points + the tag-aggregation DAO.

    Returns the mocks so callers can assert on call args / counts.
    """
    candidate_rows = [_kb(i) for i in candidates]
    space_kb_rows = [_kb(i) for i in (space_rows or [])]
    all_mock = AsyncMock(return_value=candidate_rows)
    by_ids_mock = AsyncMock(return_value=space_kb_rows)
    filter_mock = AsyncMock(return_value=list(permitted or []))
    files_mock = AsyncMock(return_value=[])
    patches = [
        patch.object(KnowledgeDao, "aget_all_knowledge", new=all_mock),
        patch.object(KnowledgeDao, "aget_list_by_ids", new=by_ids_mock),
        patch.object(
            KnowledgePermissionService,
            "filter_knowledge_ids_by_permission_async",
            new=filter_mock,
        ),
        patch.object(KnowledgeFileDao, "aget_file_by_filters", new=files_mock),
    ]
    for p in patches:
        p.start()
    return SimpleNamespace(
        all_mock=all_mock,
        by_ids_mock=by_ids_mock,
        filter_mock=filter_mock,
        files_mock=files_mock,
        stop=lambda: [p.stop() for p in patches],
    )


async def _resolve(data, user) -> list[dict]:
    return await chat_service._resolve_user_kb_selection(data, user)


# ---------------------------------------------------------------------------
# AC-10 — expansion == the candidate set narrowed by the shared per-KB verdict
# ---------------------------------------------------------------------------


async def test_expands_to_the_permitted_subset_for_a_non_admin():
    env = _patch_env(candidates=[1, 2, 3], permitted=[1, 3])
    try:
        resolved = await _resolve(_data(all_=True), _user())
    finally:
        env.stop()

    assert [kb["id"] for kb in resolved] == [1, 3]
    assert env.filter_mock.await_count == 1
    assert env.filter_mock.await_args.args[2] == "use_kb"


async def test_admin_skips_the_per_kb_narrowing():
    """Retrieval skips `use_kb` for admins; the expansion must match it.

    `queryChunksFromDB` guards with `if org_kb_ids and not login_user.is_admin()`
    — if the expansion narrowed for admins too, "全部" would silently differ
    from what the retrieval path actually reads.
    """
    env = _patch_env(candidates=[1, 2, 3], permitted=[1])
    try:
        resolved = await _resolve(_data(all_=True), _user(is_admin=True))
    finally:
        env.stop()

    assert [kb["id"] for kb in resolved] == [1, 2, 3]
    assert env.filter_mock.await_count == 0


async def test_denied_kb_names_never_reach_the_prompt():
    """The result is interpolated into <selected_knowledge_bases>.

    A superset candidate that is returned WITHOUT narrowing leaks the name and
    description of knowledge bases the user may not use. This is an information
    disclosure assertion, not a functional one — do not weaken it.
    """
    env = _patch_env(candidates=[1, 2], permitted=[1])
    try:
        resolved = await _resolve(_data(all_=True), _user())
    finally:
        env.stop()

    assert [kb["id"] for kb in resolved] == [1]
    assert "kb-2" not in [kb["name"] for kb in resolved]
    assert all("desc-2" != kb["description"] for kb in resolved)


# ---------------------------------------------------------------------------
# AC-03 / AC-09 — scope of the expanded set
# ---------------------------------------------------------------------------


async def test_expansion_ignores_an_explicit_id_list():
    """`all=true` and a concrete id list are mutually exclusive.

    The client only ever sends one of them; if both arrive, the explicit list
    must not be able to smuggle in an id that the expansion would not grant.
    """
    env = _patch_env(candidates=[1, 2], permitted=[1, 2], space_rows=[9])
    try:
        resolved = await _resolve(_data(org_ids=[9], all_=True), _user())
    finally:
        env.stop()

    assert 9 not in [kb["id"] for kb in resolved]
    assert env.all_mock.await_count == 1


async def test_more_than_fifty_kbs_are_all_included():
    """AC-09: `>50` must not reject, error out, or silently narrow to 50."""
    candidates = list(range(1, 61))
    env = _patch_env(candidates=candidates, permitted=candidates)
    try:
        resolved = await _resolve(_data(all_=True), _user())
    finally:
        env.stop()

    assert len(resolved) == 60
    assert sorted(kb["id"] for kb in resolved) == candidates


async def test_candidate_query_passes_only_the_type():
    """The candidate query must stay a plain tenant-wide SELECT.

    Two things depend on this:
    - `filter_department_ids` is the platform list's BROWSE view narrowing. It
      is a legitimate param for that page, but passing it here would cut the
      candidate down to "whatever the list page currently shows" instead of a
      superset — the same class of bug F062 hit with a too-narrow candidate.
    - Tenant scoping comes from C3's `do_orm_execute` listener auto-injecting
      the condition on SELECT. Hand-writing `tenant_id` is forbidden (C3), and
      so is any extra kwarg that would replace the automatic filter.
    """
    env = _patch_env(candidates=[1], permitted=[1])
    try:
        await _resolve(_data(all_=True), _user())
    finally:
        env.stop()

    assert env.all_mock.await_count == 1
    assert env.all_mock.await_args.kwargs == {"knowledge_type": KnowledgeTypeEnum.NORMAL}


# ---------------------------------------------------------------------------
# tag aggregation — the part that must not scale with the KB count
# ---------------------------------------------------------------------------


async def test_tag_aggregation_runs_for_spaces_only():
    """The tag aggregation is 2 queries per KB.

    Over an expanded org set (hundreds of KBs) that is exactly the N+1 this
    feature must not pay. It still runs for the explicitly selected knowledge
    spaces, whose count is bounded by the user's own selection — that keeps the
    space KBs' tags in the prompt exactly as before this feature.
    """
    env = _patch_env(candidates=[1, 2, 3], permitted=[1, 2, 3], space_rows=[7])
    try:
        await _resolve(_data(space_ids=[7], all_=True), _user())
    finally:
        env.stop()

    aggregated = [call.kwargs["knowledge_id"] for call in env.files_mock.await_args_list]
    assert aggregated == [7]


# ---------------------------------------------------------------------------
# old form — the explicit path must stay byte-for-byte identical
# ---------------------------------------------------------------------------


async def test_explicit_path_is_untouched():
    """Regression for every existing caller: no new field, no new query."""
    env = _patch_env(candidates=[1, 2, 3], permitted=[], space_rows=[8, 9])
    try:
        resolved = await _resolve(_data(org_ids=[8, 9]), _user())
    finally:
        env.stop()

    assert [kb["id"] for kb in resolved] == [8, 9]
    assert env.all_mock.await_count == 0
    assert env.by_ids_mock.await_args.args[0] == [8, 9]


async def test_empty_selection_still_returns_empty():
    """`if not ids: return []` must survive — an empty list is not "everything"."""
    env = _patch_env(candidates=[1, 2, 3], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(_data(), _user())
    finally:
        env.stop()

    assert resolved == []
    assert env.all_mock.await_count == 0


async def test_expansion_result_carries_the_documented_shape():
    """Downstream reads id/name/description/tags/source — all five must exist."""
    env = _patch_env(candidates=[1, 2], permitted=[1, 2], space_rows=[5])
    try:
        resolved = await _resolve(_data(space_ids=[5], all_=True), _user())
    finally:
        env.stop()

    by_id = {kb["id"]: kb for kb in resolved}
    assert by_id[1]["name"] == "kb-1"
    assert by_id[1]["description"] == "desc-1"
    assert by_id[1]["tags"] == []
    assert by_id[1]["source"] == "organization"
    assert by_id[5]["source"] == "space"
