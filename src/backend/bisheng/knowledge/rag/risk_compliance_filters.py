"""F055: pure risk-compliance retrieval helpers shared by both retrieval chains.

No DB/vectorstore access — ``workflow/common/knowledge.py`` (document-KB path) and
``knowledge/domain/services/space_flow_retrieval.py`` (knowledge-space path) both
import from here (design 坑 3: the two chains must stay in lockstep). Lives under
``knowledge/rag`` next to ``version_filter.py`` because the knowledge domain must
not depend on the workflow module (C1).
"""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.documents import Document

DEFAULT_RETRIEVER_K = 100
DEFAULT_RETRIEVER_EF = 110


def resolve_retriever_top_k(top_k: int | None) -> dict:
    """F055 (AC-12/AC-13): k/ef search kwargs. ef must exceed k for Milvus HNSW.

    Legacy default (k=100, ef=110) when top_k is unset — zero regression for
    existing workflows (design decision 5).
    """
    k = top_k if top_k and top_k > 0 else DEFAULT_RETRIEVER_K
    return {"k": k, "param": {"ef": max(k + 10, DEFAULT_RETRIEVER_EF)}}


def combine_document_scope(
    excluded_file_ids: Iterable[int],
    included_file_ids: Iterable[int] | None,
    *,
    base_milvus_expr: str | None = None,
    base_es_filter: list[dict] | None = None,
) -> tuple[str | None, list[dict]]:
    """F055 (AC-03/AC-05): AND-combine base filters with document include/exclude scopes.

    - excluded_file_ids  -> ``document_id not in [...]`` (version filter + effective
      period share this shape, ``build_primary_only_filter`` convention)
    - included_file_ids  -> ``document_id in [...]`` (tag filter; None = unrestricted,
      empty list = no clause — callers handle the "tags matched no file" skip)
    ES side: exclusion is a bool/must_not clause, inclusion a terms clause (the
    ``filter``-list convention of the metadata filter). Deterministic sorted ids;
    inputs never mutated.
    """
    excluded = sorted(set(excluded_file_ids))
    included = sorted(set(included_file_ids)) if included_file_ids is not None else None

    es_out: list[dict] = list(base_es_filter) if base_es_filter else []
    clauses: list[str] = []
    if base_milvus_expr:
        clauses.append(base_milvus_expr)
    if included:
        clauses.append("document_id in [" + ", ".join(str(i) for i in included) + "]")
        es_out.append({"terms": {"metadata.document_id": included}})
    if excluded:
        clauses.append("document_id not in [" + ", ".join(str(i) for i in excluded) + "]")
        es_out.append({"bool": {"must_not": {"terms": {"metadata.document_id": excluded}}}})

    milvus_expr: str | None = " and ".join(clauses) if clauses else None
    return milvus_expr, es_out


def apply_kb_weight(docs: list[Document], kb_weight_map: dict[int, float]) -> list[Document]:
    """F055 (AC-09/AC-11): re-sort docs by rrf_score * KB weight; drop weight-0 KBs.

    KBs missing from the map count as weight 1.0 (legacy behaviour). Docs without
    an rrf_score sort after scored docs (stable order preserved).
    """
    weighted: list[tuple[float, int, Document]] = []
    for idx, doc in enumerate(docs):
        kb_id = doc.metadata.get("knowledge_id")
        if kb_id is not None:
            try:
                # Milvus carries int64, ES carries text — normalise before lookup.
                kb_id = int(kb_id)
            except (TypeError, ValueError):
                kb_id = None
        weight = float(kb_weight_map.get(kb_id, 1.0)) if kb_id is not None else 1.0
        if weight == 0:
            continue
        score = float(doc.metadata.get("rrf_score") or 0.0)
        weighted.append((score * weight, idx, doc))
    weighted.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in weighted]


def merge_priority_docs(high_docs: list[Document], low_docs: list[Document], top_k: int) -> list[Document]:
    """F055 (AC-10): high-priority KB docs first; fill up to top_k from the low group.

    Dedupes by page_content across the two retrieval rounds (same chunk can be
    recalled from both groups only when the KB set overlaps, which the UI prevents,
    but the helper stays safe regardless).
    """
    seen: set[str] = set()
    out: list[Document] = []
    for doc in high_docs:
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            out.append(doc)
    if len(out) < top_k:
        for doc in low_docs:
            if doc.page_content in seen:
                continue
            seen.add(doc.page_content)
            out.append(doc)
            if len(out) >= top_k:
                break
    return out


def postprocess_risk_docs(
    docs: list[Document],
    *,
    weight_map: dict[int, float],
    score_threshold: float | None,
    top_k: int | None,
) -> list[Document]:
    """F055 (AC-09/AC-12): KB-weight re-sort → score-threshold filter → top_k cut.

    Each stage is a no-op when nothing is configured, so existing workflows keep
    their exact behaviour (AC-11/AC-13). The weight re-sort only runs when the
    map carries a non-default weight — with all weights at 1.0 the caller's order
    (RRF fusion, rerank order, or the priority merge) is preserved. When weights
    ARE configured the re-sort keys on rrf_score and deliberately takes
    precedence over rerank order (design decision 3). The final cut only applies
    when ``top_k`` is explicitly set — the legacy recall default (k=100) already
    limits each engine, but the fused list can exceed it (AC-13).
    """
    if weight_map and any(weight != 1.0 for weight in weight_map.values()):
        docs = apply_kb_weight(docs, weight_map)
    if score_threshold is not None:
        docs = [one for one in docs if float(one.metadata.get("rrf_score") or 0.0) >= score_threshold]
    if top_k is not None:
        docs = docs[: resolve_retriever_top_k(top_k)["k"]]
    return docs
