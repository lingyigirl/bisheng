"""F041 unit tests — space retrieval helpers (identity, no tenant switch).

Covers design decision 2/3 + gotcha 5.10: building the config-author identity for
knowledge-space retrieval must NOT switch the current tenant ContextVar.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.documents import Document

from bisheng.core.context.tenant import get_current_tenant_id, set_current_tenant_id
from bisheng.knowledge.domain.services import space_flow_retrieval
from bisheng.knowledge.domain.services.knowledge_file_visibility_service import IndexFilter


async def test_scoped_login_user_no_tenant_switch():
    """abuild_scoped_login_user builds the author payload without touching the
    current tenant ContextVar (gotcha 5.10)."""
    set_current_tenant_id(7)  # flow tenant already in context

    fake_user = SimpleNamespace(user_id=42, user_name="author")
    fake_payload = SimpleNamespace(user_id=42, user_name="author", tenant_id=7)

    with (
        patch.object(space_flow_retrieval.UserDao, "aget_user", AsyncMock(return_value=fake_user)),
        patch.object(
            space_flow_retrieval.UserPayload,
            "init_login_user",
            AsyncMock(return_value=fake_payload),
        ) as init_mock,
    ):
        result = await space_flow_retrieval.abuild_scoped_login_user(user_id=42, tenant_id=7)

    assert result is fake_payload
    # Identity built within the passed (flow) tenant, not the author's active tenant.
    assert init_mock.await_args.kwargs["tenant_id"] == 7
    assert init_mock.await_args.kwargs["user_id"] == 42
    # The crucial invariant: current tenant is untouched.
    assert get_current_tenant_id() == 7


async def test_scoped_login_user_none_when_missing():
    """No user_id / unknown user → None (caller treats as 'no visible files')."""
    assert await space_flow_retrieval.abuild_scoped_login_user(user_id=None, tenant_id=1) is None

    with patch.object(space_flow_retrieval.UserDao, "aget_user", AsyncMock(return_value=None)):
        assert await space_flow_retrieval.abuild_scoped_login_user(user_id=999, tenant_id=1) is None


# --- filter-combination helpers (pure) ---


def test_and_milvus_expr_combines_and_skips_empty():
    assert space_flow_retrieval._and_milvus_expr(None, None) is None
    assert space_flow_retrieval._and_milvus_expr("document_id in [1]", None) == "(document_id in [1])"
    assert (
        space_flow_retrieval._and_milvus_expr("document_id in [1]", "meta == 'x'")
        == "(document_id in [1]) and (meta == 'x')"
    )


def test_merge_es_clauses_concats():
    assert space_flow_retrieval._merge_es_clauses(None, None) == []
    assert space_flow_retrieval._merge_es_clauses([{"a": 1}], None, [{"b": 2}]) == [{"a": 1}, {"b": 2}]


# --- aretrieve_space_documents guards + drop logic ---


async def test_aretrieve_returns_empty_when_no_identity_or_spaces():
    assert (
        await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[5], query="q", identity_user=None, max_content=1000
        )
        == []
    )
    ident = SimpleNamespace(user_id=1, is_admin=lambda: False)
    assert (
        await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[], query="q", identity_user=ident, max_content=1000
        )
        == []
    )


def _mock_retrieval(
    vis_instance,
    docs,
    retriever_cls_target="bisheng.knowledge.domain.services.space_flow_retrieval.KnowledgeRetrieverTool",
):
    """Patch the heavy retrieval deps of _aretrieve_one_space (Milvus/ES = 测试降级)."""
    vector = MagicMock()
    vector.as_retriever = MagicMock(return_value=MagicMock())
    rag = MagicMock()
    rag.init_knowledge_milvus_vectorstore = AsyncMock(return_value=vector)
    rag.init_knowledge_es_vectorstore = AsyncMock(return_value=vector)
    tool_instance = MagicMock()
    tool_instance.ainvoke = AsyncMock(return_value=docs)
    return (
        patch("bisheng.knowledge.domain.knowledge_rag.KnowledgeRag", rag),
        patch(retriever_cls_target, MagicMock(return_value=tool_instance)),
    )


def _mock_effective_exclusions():
    """F055 (T-08): stub the async effective-period DAO fetch (no DB in unit tests)."""
    return patch(
        "bisheng.knowledge.domain.models.knowledge_file.KnowledgeFileDao.aget_non_effective_file_ids_by_knowledge_ids",
        AsyncMock(return_value=[]),
    )


async def test_space_filter_open_runtime_drops_unauthorized():
    """AC-12/AC-13: only files the (runtime) user has view_file on survive."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    space = SimpleNamespace(id=5)

    doc_ok = Document(page_content="ok", metadata={"document_id": 1})
    doc_no = Document(page_content="nope", metadata={"document_id": 2})

    vis = MagicMock()
    vis.build_index_prefilter = AsyncMock(
        return_value=IndexFilter(strategy="in", milvus_expr="document_id in [1, 2]", es_filter=[], accessible_size=2)
    )
    vis.post_filter_visible_files = AsyncMock(return_value={1})  # only file 1 visible
    vis._config = MagicMock(
        return_value=SimpleNamespace(retrieval_initial_multiplier=1, retrieval_expansion_multiplier=2)
    )
    vis_cls = MagicMock(return_value=vis)

    rag_patch, tool_patch = _mock_retrieval(vis, [doc_ok, doc_no])
    with (
        patch("bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id", AsyncMock(return_value=space)),
        patch(
            "bisheng.knowledge.domain.services.knowledge_file_visibility_service.KnowledgeFileVisibilityService",
            vis_cls,
        ),
        rag_patch,
        tool_patch,
        _mock_effective_exclusions(),
    ):
        result = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[5], query="q", identity_user=runtime, max_content=1000
        )

    assert [d.metadata["document_id"] for d in result] == [1]  # unauthorized doc 2 dropped
    # Visibility service built with the runtime identity (AC-12).
    assert vis_cls.call_args.args[1] is runtime


async def test_space_filter_close_author_identity():
    """AC-14: toggle OFF filters by the config-author identity (not the runtime user)."""
    author = SimpleNamespace(user_id=99, is_admin=lambda: False)
    space = SimpleNamespace(id=5)
    doc = Document(page_content="c", metadata={"document_id": 1})

    vis = MagicMock()
    vis.build_index_prefilter = AsyncMock(
        return_value=IndexFilter(strategy="in", milvus_expr="document_id in [1]", es_filter=[], accessible_size=1)
    )
    vis.post_filter_visible_files = AsyncMock(return_value={1})
    vis._config = MagicMock(
        return_value=SimpleNamespace(retrieval_initial_multiplier=1, retrieval_expansion_multiplier=2)
    )
    vis_cls = MagicMock(return_value=vis)

    rag_patch, tool_patch = _mock_retrieval(vis, [doc])
    with (
        patch("bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id", AsyncMock(return_value=space)),
        patch(
            "bisheng.knowledge.domain.services.knowledge_file_visibility_service.KnowledgeFileVisibilityService",
            vis_cls,
        ),
        rag_patch,
        tool_patch,
        _mock_effective_exclusions(),
    ):
        result = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[5], query="q", identity_user=author, max_content=1000
        )

    assert len(result) == 1
    assert vis_cls.call_args.args[1] is author  # author drives the filter


# --- F055 (T-08): tags / effective period / top_k / weight / priority ---


def _mock_retrieval_capturing(vis_instance, docs):
    """_mock_retrieval variant that captures ``as_retriever(search_kwargs=...)``."""
    captured = []
    vector = MagicMock()
    vector.as_retriever = MagicMock(side_effect=lambda **kwargs: captured.append(kwargs) or MagicMock())
    rag = MagicMock()
    rag.init_knowledge_milvus_vectorstore = AsyncMock(return_value=vector)
    rag.init_knowledge_es_vectorstore = AsyncMock(return_value=vector)
    tool_instance = MagicMock()
    tool_instance.ainvoke = AsyncMock(return_value=docs)
    return (
        patch("bisheng.knowledge.domain.knowledge_rag.KnowledgeRag", rag),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval.KnowledgeRetrieverTool",
            MagicMock(return_value=tool_instance),
        ),
        captured,
    )


def _space(sid, weight=1.0, priority=0):
    return SimpleNamespace(id=sid, weight=weight, priority=priority)


async def test_one_space_injects_tag_and_effective_scopes():
    """F055 (AC-03/AC-05): tag inclusion + effective exclusion land in both engines."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    space = _space(5)

    vis = MagicMock()
    vis.build_index_prefilter = AsyncMock(
        return_value=IndexFilter(strategy="in", milvus_expr="document_id in [1, 2, 3]", es_filter=[], accessible_size=3)
    )
    vis.post_filter_visible_files = AsyncMock(return_value={1})
    vis._config = MagicMock(
        return_value=SimpleNamespace(retrieval_initial_multiplier=1, retrieval_expansion_multiplier=2)
    )
    vis_cls = MagicMock(return_value=vis)

    rag_patch, tool_patch, captured = _mock_retrieval_capturing(
        vis, [Document(page_content="c", metadata={"document_id": 1})]
    )
    with (
        patch(
            "bisheng.knowledge.domain.services.knowledge_file_visibility_service.KnowledgeFileVisibilityService",
            vis_cls,
        ),
        rag_patch,
        tool_patch,
    ):
        docs = await space_flow_retrieval._aretrieve_one_space(
            space=space,
            query="q",
            identity_user=runtime,
            max_content=1000,
            metadata_filter_fn=None,
            rrf_weights=None,
            rerank=None,
            sort_by_source_and_index=True,
            version_repo=None,
            request=MagicMock(),
            tag_file_ids=[1, 2],
            excluded_file_ids={3},
        )

    assert len(docs) == 1
    milvus_kwargs = captured[0]["search_kwargs"]
    assert "document_id in [1, 2]" in milvus_kwargs["expr"]
    assert "document_id not in [3]" in milvus_kwargs["expr"]
    es_kwargs = captured[1]["search_kwargs"]
    assert {"terms": {"metadata.document_id": [1, 2]}} in es_kwargs["filter"]
    assert {"bool": {"must_not": {"terms": {"metadata.document_id": [3]}}}} in es_kwargs["filter"]


async def test_one_space_top_k_overrides_k_and_ef():
    """F055 (AC-12): node top_k replaces the fixed k=100 with linked ef."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    space = _space(5)

    vis = MagicMock()
    vis.build_index_prefilter = AsyncMock(
        return_value=IndexFilter(strategy="in", milvus_expr="document_id in [1]", es_filter=[], accessible_size=1)
    )
    vis.post_filter_visible_files = AsyncMock(return_value={1})
    vis._config = MagicMock(
        return_value=SimpleNamespace(retrieval_initial_multiplier=1, retrieval_expansion_multiplier=2)
    )
    vis_cls = MagicMock(return_value=vis)

    rag_patch, tool_patch, captured = _mock_retrieval_capturing(
        vis, [Document(page_content="c", metadata={"document_id": 1})]
    )
    with (
        patch(
            "bisheng.knowledge.domain.services.knowledge_file_visibility_service.KnowledgeFileVisibilityService",
            vis_cls,
        ),
        rag_patch,
        tool_patch,
    ):
        await space_flow_retrieval._aretrieve_one_space(
            space=space,
            query="q",
            identity_user=runtime,
            max_content=1000,
            metadata_filter_fn=None,
            rrf_weights=None,
            rerank=None,
            sort_by_source_and_index=True,
            version_repo=None,
            request=MagicMock(),
            top_k=5,
        )

    milvus_kwargs = captured[0]["search_kwargs"]
    assert milvus_kwargs["k"] == 5
    assert milvus_kwargs["param"] == {"ef": 110}
    es_kwargs = captured[1]["search_kwargs"]
    assert es_kwargs["k"] == 5


async def test_tag_resolution_per_space():
    """F055 (AC-03): tags resolve per space via business_id; foreign tags ignored."""
    spaces = [_space(1), _space(2)]
    tags = [SimpleNamespace(id=11, business_id="1"), SimpleNamespace(id=12, business_id="999")]
    links = [SimpleNamespace(resource_id="5"), SimpleNamespace(resource_id="7")]
    with (
        patch("bisheng.database.models.tag.TagDao.aget_tags_by_ids", AsyncMock(return_value=tags)),
        patch("bisheng.database.models.tag.TagDao.aget_resources_by_tags", AsyncMock(return_value=links)),
    ):
        resolved = await space_flow_retrieval._aresolve_tag_file_ids_by_space(spaces, [11, 12])
    assert resolved == {1: [5, 7]}


async def test_tag_resolution_empty_tag_ids_no_db():
    assert await space_flow_retrieval._aresolve_tag_file_ids_by_space([], []) == {}


async def test_priority_two_round_high_first_then_fill():
    """F055 (AC-10): high-priority spaces first; low group fills up to top_k."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    high_space = _space(1, priority=1)
    low_space = _space(2)

    async def _fake_one_space(space, **kwargs):
        if space.id == 1:
            return [Document(page_content="h", metadata={"knowledge_id": 1})]
        return [Document(page_content="l", metadata={"knowledge_id": 2})]

    with (
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            AsyncMock(side_effect=lambda sid: {1: high_space, 2: low_space}.get(sid)),
        ),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval._aretrieve_one_space",
            AsyncMock(side_effect=_fake_one_space),
        ),
        _mock_effective_exclusions(),
    ):
        docs = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[1, 2], query="q", identity_user=runtime, max_content=1000, top_k=2
        )

    assert [d.page_content for d in docs] == ["h", "l"]


async def test_priority_high_satisfies_top_k_skips_low():
    """F055 (AC-10): the low group is not consulted when high already fills top_k."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    high_space = _space(1, priority=1)
    low_space = _space(2)

    async def _fake_one_space(space, **kwargs):
        if space.id == 1:
            return [Document(page_content="h1", metadata={}), Document(page_content="h2", metadata={})]
        raise AssertionError("low group must not be retrieved when high satisfies top_k")

    with (
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            AsyncMock(side_effect=lambda sid: {1: high_space, 2: low_space}.get(sid)),
        ),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval._aretrieve_one_space",
            AsyncMock(side_effect=_fake_one_space),
        ),
        _mock_effective_exclusions(),
    ):
        docs = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[1, 2], query="q", identity_user=runtime, max_content=1000, top_k=2
        )

    assert [d.page_content for d in docs] == ["h1", "h2"]


async def test_weight_resort_across_spaces():
    """F055 (AC-09): final order = rrf_score * space weight (0.01*2 < 0.03*1)."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    heavy = _space(1, weight=2.0)
    light = _space(2, weight=1.0)

    async def _fake_one_space(space, **kwargs):
        if space.id == 1:
            return [Document(page_content="a", metadata={"knowledge_id": 1, "rrf_score": 0.01})]
        return [Document(page_content="b", metadata={"knowledge_id": 2, "rrf_score": 0.03})]

    with (
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            AsyncMock(side_effect=lambda sid: {1: heavy, 2: light}.get(sid)),
        ),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval._aretrieve_one_space",
            AsyncMock(side_effect=_fake_one_space),
        ),
        _mock_effective_exclusions(),
    ):
        docs = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[1, 2], query="q", identity_user=runtime, max_content=1000
        )

    # concatenation order was [a, b]; weighted order is [b, a]
    assert [d.page_content for d in docs] == ["b", "a"]


async def test_weight_zero_space_skipped():
    """F055 (spec §3): weight-0 spaces do not participate in recall at all."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    muted = _space(1, weight=0.0)
    active = _space(2)

    async def _fake_one_space(space, **kwargs):
        if space.id == 1:
            raise AssertionError("weight-0 space must not be retrieved")
        return [Document(page_content="b", metadata={"knowledge_id": 2})]

    with (
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            AsyncMock(side_effect=lambda sid: {1: muted, 2: active}.get(sid)),
        ),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval._aretrieve_one_space",
            AsyncMock(side_effect=_fake_one_space),
        ),
        _mock_effective_exclusions(),
    ):
        docs = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[1, 2], query="q", identity_user=runtime, max_content=1000
        )

    assert [d.page_content for d in docs] == ["b"]


async def test_threshold_and_top_k_single_pass():
    """F055 (AC-12): threshold filters on rrf_score, then explicit top_k truncates."""
    runtime = SimpleNamespace(user_id=7, is_admin=lambda: False)
    space = _space(1)

    async def _fake_one_space(space, **kwargs):
        return [
            Document(page_content="keep", metadata={"knowledge_id": 1, "rrf_score": 0.05}),
            Document(page_content="low", metadata={"knowledge_id": 1, "rrf_score": 0.01}),
            Document(page_content="extra", metadata={"knowledge_id": 1, "rrf_score": 0.04}),
        ]

    with (
        patch(
            "bisheng.knowledge.domain.models.knowledge.KnowledgeDao.aquery_by_id",
            AsyncMock(return_value=space),
        ),
        patch(
            "bisheng.knowledge.domain.services.space_flow_retrieval._aretrieve_one_space",
            AsyncMock(side_effect=_fake_one_space),
        ),
        _mock_effective_exclusions(),
    ):
        docs = await space_flow_retrieval.aretrieve_space_documents(
            space_ids=[1], query="q", identity_user=runtime, max_content=1000, top_k=1, score_threshold=0.02
        )

    assert [d.page_content for d in docs] == ["keep"]


def test_build_space_knowledge_tool_defaults_rrf_weights_when_none():
    """Regression: agent-node / assistant callers omit rrf_weights, so
    build_space_knowledge_tool passes None. KnowledgeRetrieverTool.rrf_weights is
    typed List[float] and pydantic v2 rejects an explicit None — the inner
    SpaceKnowledgeRetrieverTool must be built with the default [0.5, 0.5] instead
    of raising ValidationError. (KnowledgeRagTool is patched out to isolate the
    fix from its unrelated real-llm requirement.)"""
    with patch.object(space_flow_retrieval, "KnowledgeRagTool", MagicMock()) as rag_tool_cls:
        space_flow_retrieval.build_space_knowledge_tool(
            name="knowledge_space_retriever",
            description="desc",
            llm=MagicMock(),
            space_ids=[5],
            identity_user_id=7,
            tenant_id=1,
            # rrf_weights intentionally omitted → defaults to None inside the builder
        )
    inner = rag_tool_cls.call_args.kwargs["knowledge_retriever_tool"]
    assert inner.rrf_weights == [0.5, 0.5]  # None was normalized, not rejected
    assert inner.space_ids == [5]
    assert inner.access_scope == "per_user"
