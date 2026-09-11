"""F055 T-10: retrieve_knowledge_documents — the shared document-KB builder.

Ported from test_rag_utils (T-07) after the builder moved out of RagUtils:
search_kwargs assertions for the Milvus/ES injection (metadata filter, version +
effective exclusions, per-KB tags), the metadata-filter skip convention, the
version-fetch smoke tests, priority two-round recall and weight-0 skip.
"""

from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from bisheng.knowledge.domain.services import knowledge_flow_retrieval
from bisheng.knowledge.domain.services.knowledge_flow_retrieval import retrieve_knowledge_documents


def _make_knowledge_info(knowledge_id: int, with_milvus: bool = True, with_es: bool = True):
    """Return a knowledge_info dict with mock vector stores."""
    knowledge = MagicMock()
    knowledge.id = knowledge_id
    knowledge.metadata_fields = []
    knowledge.weight = 1.0  # F055: default weight, KB participates
    knowledge.priority = 0  # F055: default priority, low group
    info = {"knowledge": knowledge}
    if with_milvus:
        info["milvus"] = MagicMock(name=f"milvus_{knowledge_id}")
    if with_es:
        info["es"] = MagicMock(name=f"es_{knowledge_id}")
    return info


def _mock_effective_exclusions():
    """F055: stub the effective-period DAO fetch (no DB in unit tests)."""
    return patch(
        "bisheng.knowledge.domain.models.knowledge_file.KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids",
        return_value=[],
    )


def _fake_multi_retriever(vectors, search_kwargs, finally_k):
    captured_milvus = []
    captured_es = []
    if vectors and hasattr(vectors[0], "_mock_name") and "milvus" in str(vectors[0]._mock_name):
        captured_milvus.extend(search_kwargs)
    else:
        captured_es.extend(search_kwargs)
    return captured_milvus, captured_es


def _call(
    knowledge_vector_list: dict,
    *,
    metadata_filter_fn=None,
    excluded: list[int] | None = None,
    milvus_kwargs_capture: list | None = None,
    es_kwargs_capture: list | None = None,
    multi_retriever_calls: list | None = None,
    fetch_calls: list | None = None,
    tool_docs=None,
    **builder_kwargs,
):
    """Invoke retrieve_knowledge_documents with the heavy deps mocked.

    ``milvus_kwargs_capture`` / ``es_kwargs_capture`` collect the search_kwargs
    passed to MultiRetriever; ``multi_retriever_calls`` collects all calls
    (used for skip assertions); ``fetch_calls`` records the version-fetch
    inputs; ``tool_docs`` is what the retrieval tool returns.
    """
    milvus_capture: list = milvus_kwargs_capture if milvus_kwargs_capture is not None else []
    es_capture: list = es_kwargs_capture if es_kwargs_capture is not None else []
    calls: list = multi_retriever_calls if multi_retriever_calls is not None else []

    def _fake(vectors, search_kwargs, finally_k):
        calls.append((vectors, search_kwargs))
        milvus_part, es_part = _fake_multi_retriever(vectors, search_kwargs, finally_k)
        milvus_capture.extend(milvus_part)
        es_capture.extend(es_part)
        return MagicMock()

    def _fetch_side_effect(ids):
        if fetch_calls is not None:
            fetch_calls.append(ids)
        return excluded or []

    tool_instance = MagicMock()
    tool_instance.invoke = MagicMock(return_value=tool_docs if tool_docs is not None else [])

    with (
        patch(
            "bisheng.knowledge.domain.services.knowledge_flow_retrieval._fetch_non_primary_file_ids",
            side_effect=_fetch_side_effect,
        ),
        _mock_effective_exclusions(),
        patch("bisheng.knowledge.domain.services.knowledge_flow_retrieval.MultiRetriever", side_effect=_fake),
        patch(
            "bisheng.knowledge.domain.services.knowledge_flow_retrieval.KnowledgeRetrieverTool",
            MagicMock(return_value=tool_instance),
        ),
    ):
        docs = retrieve_knowledge_documents(
            knowledge_ids=[1],
            query="q",
            invoke_user_id=1,
            user_name="tester",
            check_auth=False,
            max_content=1000,
            rrf_weights=[0.5, 0.5],
            metadata_filter_fn=metadata_filter_fn,
            knowledge_vector_list=knowledge_vector_list,
            **builder_kwargs,
        )
    return docs


# ---------------------------------------------------------------------------
# search_kwargs injection (ported from test_rag_utils T-07)
# ---------------------------------------------------------------------------


class TestNoExclusions:
    def test_milvus_expr_unchanged_when_no_exclusions(self):
        """With no excluded ids, the milvus expr must match the metadata filter."""
        knowledge_vector_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        _call(
            knowledge_vector_list,
            metadata_filter_fn=lambda kb: (
                "document_id in [5, 6]",
                {"filter": [{"terms": {"metadata.document_id": [5, 6]}}]},
            ),
            milvus_kwargs_capture=milvus_capture,
            es_kwargs_capture=es_capture,
        )

        assert len(milvus_capture) == 1
        assert len(es_capture) == 1
        milvus_kw = milvus_capture[0]
        assert milvus_kw["expr"] == "document_id in [5, 6]"
        assert milvus_kw["k"] == 100
        assert milvus_kw["param"] == {"ef": 110}
        es_kw = es_capture[0]
        assert es_kw["filter"] == [{"terms": {"metadata.document_id": [5, 6]}}]
        assert es_kw["k"] == 100

    def test_no_expr_and_no_filter_key_when_no_metadata_filter(self):
        """No metadata filter + no exclusions: expr absent, filter key absent."""
        knowledge_vector_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        _call(
            knowledge_vector_list,
            metadata_filter_fn=lambda kb: ("", {}),
            milvus_kwargs_capture=milvus_capture,
            es_kwargs_capture=es_capture,
        )

        assert len(milvus_capture) == 1
        assert len(es_capture) == 1
        assert "expr" not in milvus_capture[0]
        assert "filter" not in es_capture[0]


class TestWithExclusions:
    def _run_with_exclusions(self, base_milvus_str, base_es_filter_dict, excluded):
        knowledge_vector_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        _call(
            knowledge_vector_list,
            metadata_filter_fn=lambda kb: (base_milvus_str, base_es_filter_dict),
            excluded=excluded,
            milvus_kwargs_capture=milvus_capture,
            es_kwargs_capture=es_capture,
        )
        return milvus_capture, es_capture

    def test_with_metadata_filter_and_exclusions(self):
        milvus_kws, es_kws = self._run_with_exclusions(
            base_milvus_str="document_id in [5, 6]",
            base_es_filter_dict={"filter": [{"terms": {"metadata.document_id": [5, 6]}}]},
            excluded=[10, 20],
        )
        milvus_expr = milvus_kws[0]["expr"]
        assert "document_id in [5, 6]" in milvus_expr
        assert "document_id not in [10, 20]" in milvus_expr
        assert " and " in milvus_expr
        es_filter = es_kws[0]["filter"]
        assert {"terms": {"metadata.document_id": [5, 6]}} in es_filter
        assert {"bool": {"must_not": {"terms": {"metadata.document_id": [10, 20]}}}} in es_filter

    def test_no_metadata_filter_with_exclusions(self):
        milvus_kws, es_kws = self._run_with_exclusions(
            base_milvus_str="",
            base_es_filter_dict={},
            excluded=[10, 20],
        )
        assert milvus_kws[0]["expr"] == "document_id not in [10, 20]"
        es_filter = es_kws[0]["filter"]
        assert len(es_filter) == 1
        assert es_filter[0] == {"bool": {"must_not": {"terms": {"metadata.document_id": [10, 20]}}}}

    def test_ids_are_sorted_in_output(self):
        milvus_kws, es_kws = self._run_with_exclusions(
            base_milvus_str="",
            base_es_filter_dict={},
            excluded=[20, 10],  # intentionally unsorted
        )
        assert "not in [10, 20]" in milvus_kws[0]["expr"]
        must_not_ids = es_kws[0]["filter"][0]["bool"]["must_not"]["terms"]["metadata.document_id"]
        assert must_not_ids == [10, 20]


class TestSkipsNoneFilter:
    def test_knowledge_skipped_when_no_files_match_filter(self):
        """metadata filter → (None, None): that KB is excluded from retrieval."""
        knowledge_vector_list = {1: _make_knowledge_info(1)}
        multi_retriever_calls = []
        fetch_calls = []
        _call(
            knowledge_vector_list,
            metadata_filter_fn=lambda kb: (None, None),
            excluded=[10],
            multi_retriever_calls=multi_retriever_calls,
            fetch_calls=fetch_calls,
        )

        assert len(multi_retriever_calls) == 0
        # fetch still runs (before the per-KB loop, legacy behaviour)
        assert fetch_calls == [[1]]


class TestEmptyVectorList:
    def test_empty_knowledge_vector_list_returns_empty(self):
        """Empty KB list short-circuits before any retrieval construction."""
        multi_retriever_calls = []
        fetch_calls = []
        docs = _call({}, multi_retriever_calls=multi_retriever_calls, fetch_calls=fetch_calls)
        assert docs == []
        assert len(multi_retriever_calls) == 0
        assert fetch_calls == [[]]


# ---------------------------------------------------------------------------
# F055 knobs at the shared-builder level
# ---------------------------------------------------------------------------


class TestLegacyDefaults:
    def test_no_f055_params_produce_legacy_kwargs(self):
        """AC-11/AC-13: with nothing configured (no metadata filter fn, no tags,
        no top_k, no threshold) the search kwargs are EXACTLY the legacy
        k=100/ef=110 — no expr key, no filter key."""
        kb_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        _call(
            kb_list,
            milvus_kwargs_capture=milvus_capture,
            es_kwargs_capture=es_capture,
        )

        assert milvus_capture[0] == {"k": 100, "param": {"ef": 110}}
        # legacy also merged the retriever kwargs (param included) into ES kwargs
        assert es_capture[0] == {"k": 100, "param": {"ef": 110}}


class TestPriorityTwoRound:
    def _run_priority(self, *, top_k, high_docs, low_docs=None, low_raises=False):
        """Two KBs (5 = priority 1, 6 = priority 0); the tool mock returns
        ``high_docs`` on the first (high-group) call and ``low_docs`` on the
        second (low-group) call."""
        kb_list = {5: _make_knowledge_info(5), 6: _make_knowledge_info(6)}
        kb_list[5]["knowledge"].priority = 1
        calls = {"n": 0}

        def _side_effect(input):
            calls["n"] += 1
            if calls["n"] == 1:
                return high_docs
            if low_raises:
                raise AssertionError("low group must not be retrieved when high satisfies top_k")
            return low_docs or []

        tool_instance = MagicMock()
        tool_instance.invoke = MagicMock(side_effect=_side_effect)
        with (
            patch.object(knowledge_flow_retrieval, "_fetch_non_primary_file_ids", return_value=[]),
            _mock_effective_exclusions(),
            # MultiRetriever is a pydantic model — patch it out like _call does.
            patch(
                "bisheng.knowledge.domain.services.knowledge_flow_retrieval.MultiRetriever",
                MagicMock(return_value=MagicMock()),
            ),
            patch(
                "bisheng.knowledge.domain.services.knowledge_flow_retrieval.KnowledgeRetrieverTool",
                MagicMock(return_value=tool_instance),
            ),
        ):
            docs = retrieve_knowledge_documents(
                knowledge_ids=[5, 6],
                query="q",
                invoke_user_id=1,
                user_name="tester",
                check_auth=False,
                max_content=1000,
                rrf_weights=[0.5, 0.5],
                top_k=top_k,
                knowledge_vector_list=kb_list,
            )
        return docs

    def test_high_group_first_then_low_fills(self):
        """AC-10: high-priority KB round runs first, low group fills top_k."""
        docs = self._run_priority(
            top_k=3,
            high_docs=[Document(page_content="h1", metadata={}), Document(page_content="h2", metadata={})],
            low_docs=[Document(page_content="l1", metadata={})],
        )
        assert [d.page_content for d in docs] == ["h1", "h2", "l1"]

    def test_high_group_satisfies_top_k_skips_low(self):
        """AC-10: the low group is not consulted when high already fills top_k."""
        docs = self._run_priority(
            top_k=2,
            high_docs=[Document(page_content="h1", metadata={}), Document(page_content="h2", metadata={})],
            low_raises=True,
        )
        assert [d.page_content for d in docs] == ["h1", "h2"]


class TestWeightZeroSkip:
    def test_weight_zero_kb_not_retrieved(self):
        """spec §3: a weight-0 KB does not participate in recall at all."""
        kb_list = {5: _make_knowledge_info(5)}
        kb_list[5]["knowledge"].weight = 0.0
        multi_retriever_calls = []
        with (
            patch.object(knowledge_flow_retrieval, "_fetch_non_primary_file_ids", return_value=[]),
            _mock_effective_exclusions(),
        ):
            docs = _call(kb_list, multi_retriever_calls=multi_retriever_calls)
        assert docs == []
        assert len(multi_retriever_calls) == 0


class TestTagScopeEmptySkip:
    def test_tags_selected_but_no_files_skip_kb(self):
        """Tags configured for the KB but no file linked → KB skipped."""
        kb_list = {5: _make_knowledge_info(5)}
        multi_retriever_calls = []
        with (
            patch.object(knowledge_flow_retrieval, "_fetch_non_primary_file_ids", return_value=[]),
            patch.object(knowledge_flow_retrieval, "_resolve_tag_file_ids_by_kb", return_value={5: []}),
            _mock_effective_exclusions(),
        ):
            docs = _call(kb_list, tag_ids=[11], multi_retriever_calls=multi_retriever_calls)
        assert docs == []
        assert len(multi_retriever_calls) == 0


# ---------------------------------------------------------------------------
# Version-fetch smoke tests (moved from test_rag_utils T-07)
# ---------------------------------------------------------------------------


class TestFetchNonPrimaryFileIds:
    def test_empty_knowledge_ids_returns_empty(self):
        assert knowledge_flow_retrieval._fetch_non_primary_file_ids([]) == []

    def test_exception_returns_empty(self):
        """Any exception from the bridge returns [] (best-effort path)."""
        with patch("asyncio.run", side_effect=Exception("simulated DB failure")):
            result = knowledge_flow_retrieval._fetch_non_primary_file_ids([1, 2, 3])
        assert result == []

    def test_runtime_error_returns_empty(self):
        """RuntimeError (already-running event loop) returns [] gracefully."""
        with patch("asyncio.run", side_effect=RuntimeError("event loop already running")):
            result = knowledge_flow_retrieval._fetch_non_primary_file_ids([1, 2, 3])
        assert result == []


class TestExplicitTopK:
    def test_explicit_top_k_overrides_search_kwargs_k(self):
        """AC-12 wiring: explicit top_k drives the retriever k/ef (ef floor 110)."""
        kb_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        _call(kb_list, top_k=20, milvus_kwargs_capture=milvus_capture, es_kwargs_capture=es_capture)
        assert milvus_capture[0] == {"k": 20, "param": {"ef": 110}}
        assert es_capture[0] == {"k": 20, "param": {"ef": 110}}

    def test_explicit_top_k_links_ef_above_floor(self):
        """AC-12 wiring: ef = max(k+10, 110)."""
        kb_list = {1: _make_knowledge_info(1)}
        milvus_capture, _ = [], []
        _call(kb_list, top_k=150, milvus_kwargs_capture=milvus_capture)
        assert milvus_capture[0] == {"k": 150, "param": {"ef": 160}}


class TestTagScopeInjection:
    def test_tag_scope_flows_into_search_kwargs(self):
        """AC-03 wiring: resolved per-KB tag file ids become the Milvus
        `document_id in [...]` expr and the ES terms filter."""
        kb_list = {1: _make_knowledge_info(1)}
        milvus_capture, es_capture = [], []
        with patch.object(knowledge_flow_retrieval, "_resolve_tag_file_ids_by_kb", return_value={1: [7, 9]}):
            _call(kb_list, tag_ids=[11], milvus_kwargs_capture=milvus_capture, es_kwargs_capture=es_capture)
        assert milvus_capture[0]["expr"] == "document_id in [7, 9]"
        assert es_capture[0]["filter"] == [{"terms": {"metadata.document_id": [7, 9]}}]
