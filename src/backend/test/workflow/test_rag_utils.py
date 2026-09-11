"""RagUtils F055/T-10 tests — vectorstore cache + delegation to the shared builder.

The document-KB builder logic (search_kwargs injection, priority groups,
post-processing) moved to
``bisheng.knowledge.domain.services.knowledge_flow_retrieval`` in T-10 and is
covered by ``test/knowledge/test_knowledge_flow_retrieval.py``. Here: RagUtils
caches the vectorstore list across questions and delegates retrieval with the
node's knobs (design decision 7 — the hit-test endpoint shares the same function).
"""

from unittest.mock import MagicMock, patch

from bisheng.knowledge.rag.version_filter import build_primary_only_filter
from bisheng.workflow.common.knowledge import ConditionCases, RagUtils

# ---------------------------------------------------------------------------
# Helpers — build a minimal RagUtils without full BaseNode init
# ---------------------------------------------------------------------------


def _make_rag_utils(knowledge_vector_list: dict) -> RagUtils:
    """Construct a RagUtils instance without triggering BaseNode.__init__."""
    obj = object.__new__(RagUtils)
    obj._knowledge_vector_list = knowledge_vector_list
    obj._metadata_filter = ConditionCases(enabled=False)
    obj._keyword_weight = 0.5
    obj._vector_weight = 0.5
    obj._retriever_kwargs = {"k": 100, "param": {"ef": 110}}
    obj._multi_milvus_retriever = None
    obj._multi_es_retriever = None
    obj._tag_ids = []  # F055: tag filter disabled in these tests
    obj._top_k = None
    obj._score_threshold = None
    return obj


def _make_knowledge_info(
    knowledge_id: int,
    with_milvus: bool = True,
    with_es: bool = True,
):
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


def _wire_knowledge_node(obj: RagUtils, knowledge_ids: list[int]):
    """Set the node attributes the knowledge delegation path reads."""
    obj._knowledge_type = "knowledge"
    obj._knowledge_value = knowledge_ids
    obj._max_chunk_size = 15000
    obj._rerank_model = None
    obj.user_id = "u1"
    obj.user_info = MagicMock()
    obj.user_info.user_name = "tester"
    obj._knowledge_auth = True


# ---------------------------------------------------------------------------
# RagUtils behaviour
# ---------------------------------------------------------------------------


def test_init_knowledge_retriever_populates_and_reuses_vectorstore_cache():
    """T-10: init only caches the vectorstore list; the second call reuses it."""
    obj = _make_rag_utils(knowledge_vector_list={})
    _wire_knowledge_node(obj, [5])

    with patch(
        "bisheng.workflow.common.knowledge.KnowledgeRag.get_multi_knowledge_vectorstore_sync",
        return_value={5: _make_knowledge_info(5)},
    ) as fetch_mock:
        obj.init_knowledge_retriever()
        obj.init_knowledge_retriever()

    fetch_mock.assert_called_once()  # cache hit on the second call
    assert 5 in obj._knowledge_vector_list


def test_retrieve_question_delegates_to_shared_builder_with_node_knobs():
    """T-10 (design decision 7): the knowledge path calls the shared builder
    with the node's tags/top_k/threshold and the cached vectorstore list."""
    obj = _make_rag_utils(knowledge_vector_list={5: _make_knowledge_info(5)})
    _wire_knowledge_node(obj, [5])
    obj._tag_ids = [11]
    obj._top_k = 7
    obj._score_threshold = 0.02

    with patch("bisheng.workflow.common.knowledge.retrieve_knowledge_documents", return_value=[]) as builder_mock:
        obj.retrieve_question("测试问题?")

    kwargs = builder_mock.call_args.kwargs
    assert kwargs["knowledge_ids"] == [5]
    assert kwargs["query"] == "测试问题?"
    assert kwargs["tag_ids"] == [11]
    assert kwargs["top_k"] == 7
    assert kwargs["score_threshold"] == 0.02
    assert kwargs["check_auth"] is True
    assert kwargs["knowledge_vector_list"] is obj._knowledge_vector_list
    assert callable(kwargs["metadata_filter_fn"])


def test_retrieve_question_file_path_uses_shared_tool_invocation():
    """T-10: the file-upload path reuses the extracted tool invocation helper."""
    obj = _make_rag_utils(knowledge_vector_list=[])
    obj._knowledge_type = "file"
    obj._max_chunk_size = 100
    obj._rerank_model = None
    obj._multi_milvus_retriever = MagicMock()
    obj._multi_es_retriever = MagicMock()

    with patch("bisheng.workflow.common.knowledge.invoke_knowledge_retriever_tool", return_value=[]) as invoke_mock:
        obj.retrieve_question("q?")

    assert invoke_mock.call_args.args[:2] == (obj._multi_milvus_retriever, obj._multi_es_retriever)


# ---------------------------------------------------------------------------
# Integration: verify build_primary_only_filter is used (not mocked)
# ---------------------------------------------------------------------------


class TestBuildPrimaryOnlyFilterDirect:
    """Directly verify build_primary_only_filter output shapes used in production."""

    def test_no_exclusions_unchanged(self):
        milvus_expr, es_filter = build_primary_only_filter(
            [],
            base_milvus_expr="document_id in [1, 2]",
            base_es_filter=[{"terms": {"metadata.document_id": [1, 2]}}],
        )
        assert milvus_expr == "document_id in [1, 2]"
        assert es_filter == [{"terms": {"metadata.document_id": [1, 2]}}]

    def test_with_exclusions_combined(self):
        milvus_expr, es_filter = build_primary_only_filter(
            [10, 20],
            base_milvus_expr="document_id in [1, 2]",
            base_es_filter=[{"terms": {"metadata.document_id": [1, 2]}}],
        )
        assert milvus_expr == "document_id in [1, 2] and document_id not in [10, 20]"
        assert {"bool": {"must_not": {"terms": {"metadata.document_id": [10, 20]}}}} in es_filter
        assert {"terms": {"metadata.document_id": [1, 2]}} in es_filter

    def test_no_base_with_exclusions(self):
        milvus_expr, es_filter = build_primary_only_filter([10, 20])
        assert milvus_expr == "document_id not in [10, 20]"
        assert len(es_filter) == 1
        assert es_filter[0] == {"bool": {"must_not": {"terms": {"metadata.document_id": [10, 20]}}}}
