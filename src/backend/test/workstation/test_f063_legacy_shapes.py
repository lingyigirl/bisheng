"""F063 regression: request shapes that predate the feature behave unchanged.

The feature changes a funnel every chat entry shares (`_resolve_user_kb_selection`
feeds both the `search_knowledge_bases` tool whitelist and the prompt's
<selected_knowledge_bases> section), so the regression surface is larger than the
new surface. These are the shapes an OLD client sends — one that has never heard
of `organization_knowledge_all` — and each one has to keep behaving exactly as it
did before:

- the new field is absent from the JSON body (not merely false);
- a concrete `organization_knowledge_ids` list of size 1 / 50 / empty;
- `use_knowledge_base` missing entirely;
- ids whose rows no longer exist.

The `>50` validator is the other half of the contract: the sibling feature (F063)
stops the CLIENT from ever sending more than 50 ids, but the server-side limit
itself must stay in force — "no longer triggered" is not "removed".

ACs covered:
- AC-03: only an explicit all-tier request expands; the legacy shapes never do.
- AC-09: the `>50` rejection semantics are untouched by this feature.
- AC-25: no legacy caller gains or loses behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from bisheng.api.v1.schema.chat_schema import UseKnowledgeBaseParam
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFileDao
from bisheng.knowledge.domain.services.knowledge_permission_service import (
    KnowledgePermissionService,
)
from bisheng.workstation.domain.services import chat_service

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _kb(kb_id: int):
    return SimpleNamespace(id=kb_id, name=f"kb-{kb_id}", description=f"desc-{kb_id}")


def _user(is_admin: bool = False):
    return SimpleNamespace(user_id=7, tenant_id=1, is_admin=lambda: is_admin)


def _data_from_body(body: dict | None):
    """Build the funnel input from a RAW request body, the way the API does.

    Going through `model_validate` (rather than constructing the param with
    keyword arguments) is the point: it is what a legacy client's JSON actually
    hits, including the case where the new field is simply absent.
    """
    return SimpleNamespace(
        use_knowledge_base=(UseKnowledgeBaseParam.model_validate(body) if body is not None else None)
    )


def _patch_env(candidates: list[int], permitted: list[int] | None):
    """Patch the DAO/service entries the funnel reaches, whatever the shape."""
    all_mock = AsyncMock(return_value=[_kb(i) for i in candidates])
    by_ids_mock = AsyncMock(return_value=[_kb(i) for i in candidates])
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
        stop=lambda: [p.stop() for p in patches],
    )


async def _resolve(data, user=None) -> list[dict]:
    return await chat_service._resolve_user_kb_selection(data, user or _user())


LEGACY_BODY = {
    "personal_knowledge_enabled": True,
    "organization_knowledge_ids": [1, 2],
    "knowledge_space_ids": [7],
}


# ---------------------------------------------------------------------------
# the new field is optional and defaults to "off" — the crux of compatibility
# ---------------------------------------------------------------------------


def test_a_legacy_body_without_the_new_field_parses_to_the_old_semantics():
    """A client that has never heard of F063 must not start expanding.

    If the default ever flips to True, every old client silently starts asking
    for the whole tenant's organization KBs — a scope change made by a default,
    which is exactly what this assertion exists to prevent. The second half pins
    it harder: absent and explicitly-False must be indistinguishable downstream.
    """
    absent = UseKnowledgeBaseParam.model_validate(LEGACY_BODY)
    explicit = UseKnowledgeBaseParam.model_validate({**LEGACY_BODY, "organization_knowledge_all": False})

    assert absent.organization_knowledge_all is False
    assert absent.model_dump() == explicit.model_dump()
    assert absent.organization_knowledge_ids == [1, 2]
    assert absent.knowledge_space_ids == [7]


async def test_a_missing_use_knowledge_base_block_still_yields_nothing():
    """`use_knowledge_base` absent (older callers) must stay a no-op."""
    env = _patch_env(candidates=[1, 2, 3], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(_data_from_body(None))
    finally:
        env.stop()

    assert resolved == []
    assert env.by_ids_mock.await_count == 0
    assert env.all_mock.await_count == 0


# ---------------------------------------------------------------------------
# the legacy explicit-id path: same query, same order, never the expansion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 2, 50])
async def test_explicit_ids_load_in_request_order_and_never_expand(size):
    """The funnel must read exactly the ids it was given, org ids first.

    Order matters downstream: `queryChunksFromDB` receives this list and the
    prompt presents the KBs in it. 50 is included because it is the largest
    legacy shape the validator admits — the boundary either side of the limit is
    covered here and in the validator cases below.
    """
    org_ids = list(range(1, size + 1))
    body = {"organization_knowledge_ids": org_ids, "knowledge_space_ids": [900, 901]}
    env = _patch_env(candidates=org_ids + [900, 901], permitted=[1])
    try:
        resolved = await _resolve(_data_from_body(body))
    finally:
        env.stop()

    assert env.all_mock.await_count == 0
    assert env.by_ids_mock.await_args.args[0] == org_ids + [900, 901]
    assert [kb["id"] for kb in resolved] == org_ids + [900, 901]
    # The explicit path does not consult the per-KB verdict — the retrieval path
    # still does (unchanged), and narrowing here would change what the prompt
    # names for every existing caller.
    assert env.filter_mock.await_count == 0


async def test_an_empty_explicit_list_is_still_use_none():
    """`if not ids: return []` predates the feature and must survive it.

    The tempting "empty means everything" rewrite is what the all-tier flag was
    introduced to avoid — folding it in here would invert every old request.
    """
    env = _patch_env(candidates=[1, 2, 3], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(
            _data_from_body({"organization_knowledge_ids": [], "knowledge_space_ids": []})
        )
    finally:
        env.stop()

    assert resolved == []
    assert env.by_ids_mock.await_count == 0
    assert env.all_mock.await_count == 0


async def test_ids_whose_rows_are_gone_yield_nothing_not_everything():
    """A deleted KB id must degrade to "nothing found", never to the expansion."""
    env = _patch_env(candidates=[], permitted=[1, 2, 3])
    try:
        resolved = await _resolve(_data_from_body({"organization_knowledge_ids": [4242]}))
    finally:
        env.stop()

    assert resolved == []
    assert env.all_mock.await_count == 0
    assert env.filter_mock.await_count == 0


# ---------------------------------------------------------------------------
# the >50 validator: unchanged, and still reachable
# ---------------------------------------------------------------------------


def test_fifty_ids_are_accepted_and_fifty_one_are_rejected():
    """AC-09's server limit is untouched: 50 passes, 51 raises.

    This feature makes the client stop enumerating, so the validator is no longer
    hit in practice — that is not the same as removing it. The message is checked
    only loosely (the word "50"), because what must not change is the rejection
    itself, not its wording.
    """
    accepted = UseKnowledgeBaseParam.model_validate(
        {"organization_knowledge_ids": list(range(1, 51)), "knowledge_space_ids": list(range(1, 51))}
    )
    assert len(accepted.organization_knowledge_ids or []) == 50

    with pytest.raises(ValidationError) as org_err:
        UseKnowledgeBaseParam.model_validate({"organization_knowledge_ids": list(range(1, 52))})
    assert "50" in str(org_err.value)

    with pytest.raises(ValidationError) as space_err:
        UseKnowledgeBaseParam.model_validate({"knowledge_space_ids": list(range(1, 52))})
    assert "50" in str(space_err.value)
