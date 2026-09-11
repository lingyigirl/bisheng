"""F055 T-06: retrieval injection helpers — effective period, tags, weight, priority, top_k.

Covers AC-03, AC-05, AC-06, AC-07, AC-09, AC-10, AC-11, AC-12, AC-13.
All helpers are pure (no DB/vectorstore) and live in
knowledge/rag/risk_compliance_filters.py (T-08 moved them out of
workflow/common/knowledge.py so the knowledge-space chain can import them
without a knowledge→workflow dependency, design pitfall 3).
"""

from datetime import date, datetime

from langchain_core.documents import Document

from bisheng.knowledge.rag.risk_compliance_filters import (
    apply_kb_weight,
    combine_document_scope,
    merge_priority_docs,
    postprocess_risk_docs,
    resolve_retriever_top_k,
)
from bisheng.workflow.common.condition import ComparisonType
from bisheng.workflow.common.knowledge import ConditionOne


def _doc(text: str, **metadata) -> Document:
    return Document(page_content=text, metadata=metadata)


# ---------------------------------------------------------------- today expression


class TestTodayExpression:
    def _cond(self, right_value: str) -> ConditionOne:
        return ConditionOne(
            id="c1",
            knowledge_id=1,
            metadata_field="upload_time",
            comparison_operation=ComparisonType.LESS_THAN_OR_EQUAL,
            right_value_type="str",
            right_value=right_value,
        )

    def test_preset_time_field_today_resolves_to_date_string(self):
        # preset path returns values raw (MySQL compares them directly) — today
        # must already be a concrete date string there
        cond = self._cond("today")
        filter_field = cond.get_knowledge_file_filter({"field_type": "time"}, parent_node=None, is_preset=True)
        assert filter_field["create_time"]["value"] == datetime.combine(date.today(), datetime.min.time()).strftime(
            "%Y-%m-%d"
        )

    def test_today_is_case_insensitive(self):
        cond = self._cond("TODAY")
        filter_field = cond.get_knowledge_file_filter({"field_type": "time"}, parent_node=None, is_preset=True)
        today_str = datetime.combine(date.today(), datetime.min.time()).strftime("%Y-%m-%d")
        assert filter_field["create_time"]["value"] == today_str

    def test_user_metadata_today_resolves_to_timestamp(self):
        # non-preset path converts the resolved date string to a timestamp
        cond = self._cond("today")
        ts = cond.convert_right_value("time", "today", is_preset=False)
        today_start = datetime.combine(date.today(), datetime.min.time())
        assert ts == int(today_start.timestamp())

    def test_other_time_values_unchanged(self):
        # preset non-today ISO values pass through raw (legacy behaviour)
        cond = self._cond("2026-01-01T00:00:00")
        filter_field = cond.get_knowledge_file_filter({"field_type": "time"}, parent_node=None, is_preset=True)
        assert filter_field["create_time"]["value"] == "2026-01-01T00:00:00"


# ---------------------------------------------------------------- document scope combine


class TestCombineDocumentScope:
    def test_excluded_only(self):
        milvus, es = combine_document_scope(excluded_file_ids=[5, 3], included_file_ids=None)
        assert milvus == "document_id not in [3, 5]"
        assert es == [{"bool": {"must_not": {"terms": {"metadata.document_id": [3, 5]}}}}]

    def test_included_only(self):
        milvus, es = combine_document_scope(excluded_file_ids=[], included_file_ids=[7])
        assert milvus == "document_id in [7]"
        assert es == [{"terms": {"metadata.document_id": [7]}}]

    def test_excluded_and_included_combined(self):
        milvus, es = combine_document_scope(excluded_file_ids=[2], included_file_ids=[7, 9])
        assert milvus == "document_id in [7, 9] and document_id not in [2]"
        assert len(es) == 2

    def test_base_filters_anded(self):
        milvus, es = combine_document_scope(
            excluded_file_ids=[2],
            included_file_ids=None,
            base_milvus_expr="user_metadata['x'] == 'a'",
            base_es_filter=[{"terms": {"metadata.foo": ["a"]}}],
        )
        assert milvus == "user_metadata['x'] == 'a' and document_id not in [2]"
        # base list is copied, exclusion appended
        assert es[0] == {"terms": {"metadata.foo": ["a"]}}
        assert "must_not" in es[1]["bool"]

    def test_no_scopes_returns_bases_unchanged(self):
        assert combine_document_scope([], None) == (None, [])
        assert combine_document_scope([], None, base_milvus_expr="x == 1") == ("x == 1", [])

    def test_inputs_not_mutated(self):
        base_es = [{"terms": {"metadata.foo": ["a"]}}]
        combine_document_scope([1], None, base_es_filter=base_es)
        assert base_es == [{"terms": {"metadata.foo": ["a"]}}]


# ---------------------------------------------------------------- top_k / ef


class TestResolveRetrieverTopK:
    def test_default_legacy(self):
        assert resolve_retriever_top_k(None) == {"k": 100, "param": {"ef": 110}}

    def test_small_top_k_keeps_min_ef(self):
        assert resolve_retriever_top_k(5) == {"k": 5, "param": {"ef": 110}}

    def test_large_top_k_scales_ef(self):
        assert resolve_retriever_top_k(200) == {"k": 200, "param": {"ef": 210}}


# ---------------------------------------------------------------- weight / priority


class TestApplyKbWeight:
    def test_sorts_by_weighted_rrf_score(self):
        docs = [
            _doc("low", knowledge_id=1, rrf_score=0.02),
            _doc("high", knowledge_id=2, rrf_score=0.01),
        ]
        out = apply_kb_weight(docs, {1: 1.0, 2: 5.0})
        assert [d.page_content for d in out] == ["high", "low"]

    def test_weight_zero_kb_dropped(self):
        docs = [_doc("a", knowledge_id=1, rrf_score=0.02), _doc("b", knowledge_id=2, rrf_score=0.03)]
        out = apply_kb_weight(docs, {1: 0.0, 2: 1.0})
        assert [d.page_content for d in out] == ["b"]

    def test_missing_weight_defaults_to_one(self):
        docs = [_doc("a", knowledge_id=9, rrf_score=0.02)]
        assert len(apply_kb_weight(docs, {})) == 1


class TestPostprocessRiskDocs:
    """F055 (T-10): the shared post-processing stage (weight → threshold → top_k)."""

    def test_all_defaults_noop(self):
        docs = [_doc("a", rrf_score=0.01), _doc("b", rrf_score=0.02)]
        out = postprocess_risk_docs(docs, weight_map={1: 1.0}, score_threshold=None, top_k=None)
        assert [d.page_content for d in out] == ["a", "b"]

    def test_threshold_filters_on_rrf_score(self):
        docs = [_doc("keep", rrf_score=0.05), _doc("low", rrf_score=0.01)]
        out = postprocess_risk_docs(docs, weight_map={}, score_threshold=0.02, top_k=None)
        assert [d.page_content for d in out] == ["keep"]

    def test_explicit_top_k_truncates(self):
        docs = [_doc("a"), _doc("b"), _doc("c")]
        out = postprocess_risk_docs(docs, weight_map={}, score_threshold=None, top_k=2)
        assert [d.page_content for d in out] == ["a", "b"]

    def test_non_default_weight_resorts(self):
        docs = [_doc("low", knowledge_id=1, rrf_score=0.02), _doc("high", knowledge_id=2, rrf_score=0.01)]
        out = postprocess_risk_docs(docs, weight_map={1: 1.0, 2: 5.0}, score_threshold=None, top_k=None)
        assert [d.page_content for d in out] == ["high", "low"]

    def test_weight_zero_only_does_not_resort(self):
        # a weight-0 KB alone must not trigger the re-sort guard (AC-11)
        docs = [_doc("b", rrf_score=0.02), _doc("a", rrf_score=0.01)]
        out = postprocess_risk_docs(docs, weight_map={9: 0.0}, score_threshold=None, top_k=None)
        assert [d.page_content for d in out] == ["b", "a"]


class TestMergePriorityDocs:
    def test_high_group_first_then_fill(self):
        high = [_doc("h1"), _doc("h2")]
        low = [_doc("l1"), _doc("l2")]
        out = merge_priority_docs(high, low, top_k=3)
        assert [d.page_content for d in out] == ["h1", "h2", "l1"]

    def test_high_group_satisfies_top_k(self):
        out = merge_priority_docs([_doc("h1"), _doc("h2")], [_doc("l1")], top_k=2)
        assert [d.page_content for d in out] == ["h1", "h2"]

    def test_dedupe_across_groups(self):
        out = merge_priority_docs([_doc("x")], [_doc("x"), _doc("y")], top_k=5)
        assert [d.page_content for d in out] == ["x", "y"]

    def test_empty_high_group(self):
        out = merge_priority_docs([], [_doc("l1"), _doc("l2")], top_k=5)
        assert [d.page_content for d in out] == ["l1", "l2"]
