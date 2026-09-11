"""F055: document-KB retrieval builder shared by workflow nodes and the hit-test endpoint.

Extracted from ``RagUtils`` (T-10) so ``POST /knowledge/{id}/hit_test`` and the
workflow rag / knowledge_retriever nodes call the SAME function — the preview
matches production retrieval exactly (design decision 7, AC-14). Lives in the
knowledge domain (like ``space_flow_retrieval``) because both the workflow
module and the knowledge API import it (C1).

Sync by design: the document-KB chain (``RagUtils``) is synchronous (workflow
workers run in Celery threads); the hit-test endpoint calls this through
``run_in_threadpool``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from bisheng.core.vectorstore.multi_retriever import MultiRetriever
from bisheng.knowledge.domain.knowledge_rag import KnowledgeRag
from bisheng.knowledge.domain.models.knowledge import Knowledge
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFileDao
from bisheng.knowledge.rag.risk_compliance_filters import (
    combine_document_scope,
    merge_priority_docs,
    postprocess_risk_docs,
    resolve_retriever_top_k,
)
from bisheng.tool.domain.langchain.knowledge import KnowledgeRetrieverTool

# metadata_filter_fn maps a KB to (milvus_expr, es_filter); (None, None) means
# "filter matched no file — skip this KB" (ConditionCases.get_knowledge_filter
# convention).
MetadataFilterFn = Callable[[Knowledge], tuple[str | None, dict | None]]


def _fetch_non_primary_file_ids(knowledge_ids: list[int]) -> list[int]:
    """Best-effort sync fetch of non-primary file ids for the given knowledges.

    Runs on the shared worker bridge loop via ``run_async_safe`` — NOT
    ``asyncio.run``. ``asyncio.run`` spins up a throwaway loop and closes it,
    leaving the connection it opened in the process-global async DB pool bound
    to a dead loop; a later query then hits "Event loop is closed" / "Future
    attached to a different loop". The bridge keeps every async DB call on one
    loop. Failures degrade gracefully — version filter excluded, retrieval still
    works.
    """
    if not knowledge_ids:
        return []
    from bisheng.core.database import get_async_db_session
    from bisheng.knowledge.domain.repositories.implementations.knowledge_document_version_repository_impl import (
        KnowledgeDocumentVersionRepositoryImpl,
    )
    from bisheng.utils.async_utils import run_async_safe

    async def _fetch():
        async with get_async_db_session() as session:
            repo = KnowledgeDocumentVersionRepositoryImpl(session)
            return await repo.find_non_primary_file_ids_by_knowledge_ids(knowledge_ids)

    try:
        return run_async_safe(_fetch())
    except RuntimeError:
        logger.opt(exception=True).warning("version filter skipped: running inside an event loop")
        return []
    except Exception:
        logger.opt(exception=True).warning("version filter fetch failed")
        return []


def _resolve_tag_file_ids_by_kb(knowledge_ids: list[int], tag_ids: list[int]) -> dict[int, list[int]]:
    """F055 (AC-03): node tag selection → per-KB file id scopes.

    Tags are KB-scoped (business_id = the KB id), so each tag only constrains
    its own KB — other selected KBs stay unrestricted (design 坑 9). Tags not
    belonging to any selected KB are ignored. Empty dict = no tag filter at all.
    (Async twin for the space chain: ``space_flow_retrieval._aresolve_tag_file_ids_by_space``.)
    """
    if not tag_ids:
        return {}
    from bisheng.database.models.group_resource import ResourceTypeEnum
    from bisheng.database.models.tag import TagDao

    selected_kb_ids = set(knowledge_ids)
    tag_ids_by_kb: dict[int, list[int]] = {}
    for tag in TagDao.get_tags_by_ids(tag_ids):
        if tag.business_id is None:
            continue
        try:
            kb_id = int(tag.business_id)
        except (TypeError, ValueError):
            # Tags of other business types (e.g. APPLICATION with a UUID
            # business_id) can reach here via hand-edited flow JSON — skip
            # instead of crashing the whole retrieval.
            continue
        if kb_id in selected_kb_ids:
            tag_ids_by_kb.setdefault(kb_id, []).append(tag.id)

    file_ids_by_kb: dict[int, list[int]] = {}
    for kb_id, kb_tag_ids in tag_ids_by_kb.items():
        links = TagDao.get_resources_by_tags(kb_tag_ids, ResourceTypeEnum.KNOWLEDGE_FILE)
        file_ids_by_kb[kb_id] = sorted({int(one.resource_id) for one in links})
    return file_ids_by_kb


def invoke_knowledge_retriever_tool(
    milvus_retriever,
    es_retriever,
    query: str,
    *,
    max_content: int,
    rrf_weights: list[float],
    rerank: Any = None,
) -> list[Document]:
    """Build + invoke the hybrid retrieval tool for one round (F055 extraction).

    Single construction point shared by the priority two-round flow (AC-10),
    the single-round flow and RagUtils' file-upload path.
    """
    knowledge_retriever_tool = KnowledgeRetrieverTool(
        vector_retriever=milvus_retriever,
        elastic_retriever=es_retriever,
        max_content=max_content,
        rrf_weights=rrf_weights,
        rrf_remove_zero_score=True,
        rerank=rerank,
        sort_by_source_and_index=True,
    )
    return knowledge_retriever_tool.invoke(input={"query": query})


def _build_group_retriever(group: dict[str, list], finally_k: int) -> tuple:
    """F055: build the (milvus, es) MultiRetriever pair for one priority group."""
    milvus_retriever = None
    es_retriever = None
    if group["milvus"]:
        milvus_retriever = MultiRetriever(
            vectors=group["milvus"],
            search_kwargs=group["milvus_kwargs"],
            finally_k=finally_k,
        )
    if group["es"]:
        es_retriever = MultiRetriever(
            vectors=group["es"],
            search_kwargs=group["es_kwargs"],
            finally_k=finally_k,
        )
    return milvus_retriever, es_retriever


def retrieve_knowledge_documents(
    *,
    knowledge_ids: list[int],
    query: str,
    invoke_user_id: int,
    user_name: str,
    check_auth: bool,
    max_content: int,
    rrf_weights: list[float],
    rerank: Any = None,
    metadata_filter_fn: MetadataFilterFn | None = None,
    tag_ids: list[int] | None = None,
    top_k: int | None = None,
    score_threshold: float | None = None,
    knowledge_vector_list: dict | None = None,
) -> list[Document]:
    """F055 (T-10): retrieve from document KBs with all risk-compliance knobs.

    The single entry for the document-KB chain — workflow nodes (``RagUtils``)
    and the hit-test endpoint both call this function, so a hit-test preview
    cannot drift from production retrieval (design decision 7). Knobs default
    off for legacy behaviour (AC-11/AC-13):

    - effective-period + non-primary-version exclusions injected by default
      (null-tolerant, best-effort DB degradation)
    - per-KB tag scopes (AC-03), metadata filter via ``metadata_filter_fn``
    - priority two-round recall (AC-10), rrf_score x weight re-sort (AC-09),
      threshold + explicit top_k (AC-12)

    ``knowledge_vector_list`` is the caller's cache of
    ``KnowledgeRag.get_multi_knowledge_vectorstore_sync`` (RagUtils caches it
    across questions; the endpoint passes None and lets it fetch).
    """
    if not knowledge_ids:
        return []

    if knowledge_vector_list is None:
        knowledge_vector_list = KnowledgeRag.get_multi_knowledge_vectorstore_sync(
            invoke_user_id=invoke_user_id,
            knowledge_ids=knowledge_ids,
            user_name=user_name,
            check_auth=check_auth,
            include_es=rrf_weights[1] > 0,
            include_milvus=rrf_weights[0] > 0,
        )

    retriever_kwargs = resolve_retriever_top_k(top_k)

    # Excluded file ids are fetched once for all KBs — the union is safe because
    # each collection only contains its own files (cross-KB ids are no-ops).
    selected_ids = list(knowledge_vector_list.keys())
    excluded_file_ids = set(_fetch_non_primary_file_ids(selected_ids))
    try:
        excluded_file_ids.update(KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids(selected_ids))
    except SQLAlchemyError:
        logger.opt(exception=True).warning("effective-period filter skipped (DB error)")

    tag_file_ids_by_kb = _resolve_tag_file_ids_by_kb(selected_ids, tag_ids or [])

    groups = {
        0: {"milvus": [], "es": [], "milvus_kwargs": [], "es_kwargs": []},
        1: {"milvus": [], "es": [], "milvus_kwargs": [], "es_kwargs": []},
    }
    weight_map: dict[int, float] = {}
    for knowledge_id, knowledge_info in knowledge_vector_list.items():
        knowledge = knowledge_info.get("knowledge")
        if knowledge.weight == 0:
            # F055: weight-0 KB does not participate in recall (spec §3).
            continue
        weight_map[knowledge_id] = float(knowledge.weight)
        milvus_vector = knowledge_info.get("milvus")
        es_vector = knowledge_info.get("es")

        milvus_filter_str: str | None = None
        es_filter: dict | None = None
        if metadata_filter_fn is not None:
            milvus_filter_str, es_filter = metadata_filter_fn(knowledge)
            if milvus_filter_str is None and es_filter is None:
                # metadata filter matched no file — skip this KB (legacy convention).
                continue

        tag_scope = tag_file_ids_by_kb.get(knowledge_id)
        if tag_scope is not None and not tag_scope:
            # F055: tags selected for this KB but no file carries them — skip
            # (an empty "document_id in []" expr is invalid in Milvus).
            continue

        # AND-combine metadata filter, per-KB tag scope and the excluded
        # (version + effective-period) scope. Same conventions as the former
        # build_primary_only_filter call: base_milvus_expr: str | None,
        # base_es_filter: the list under the "filter" key.
        base_es_list = es_filter.get("filter") if es_filter else None
        combined_milvus_expr, combined_es_list = combine_document_scope(
            excluded_file_ids,
            tag_scope,
            base_milvus_expr=milvus_filter_str or None,
            base_es_filter=base_es_list,
        )

        group = groups.get(knowledge.priority, groups[0])
        if milvus_vector:
            group["milvus"].append(milvus_vector)
            milvus_extra = {"expr": combined_milvus_expr} if combined_milvus_expr else {}
            logger.debug(f"retrieve milvus filter: {milvus_extra}")
            group["milvus_kwargs"].append(milvus_extra | retriever_kwargs)
        if es_vector:
            group["es"].append(es_vector)
            # Reconstruct the ES search_kwargs dict: preserve any base keys from
            # es_filter (e.g. "filter") and override "filter" with combined list.
            combined_es_filter = dict(es_filter) if es_filter else {}
            if combined_es_list:
                combined_es_filter["filter"] = combined_es_list
            elif "filter" in combined_es_filter:
                # No scopes, no base filter list — remove empty entry.
                del combined_es_filter["filter"]
            logger.debug(f"retrieve es filter: {combined_es_filter}")
            group["es_kwargs"].append(combined_es_filter | retriever_kwargs)

    finally_k = retriever_kwargs["k"]
    high_milvus, high_es = _build_group_retriever(groups[1], finally_k)
    low_milvus, low_es = _build_group_retriever(groups[0], finally_k)

    priority_split = bool(high_milvus or high_es) and bool(low_milvus or low_es)
    if priority_split:
        # F055 (AC-10): high-priority KBs are recalled first; the low group only
        # fills the remaining slots up to top_k (design decision 3).
        high_docs = invoke_knowledge_retriever_tool(
            high_milvus, high_es, query, max_content=max_content, rrf_weights=rrf_weights, rerank=rerank
        )
        if len(high_docs) >= finally_k:
            docs = high_docs[:finally_k]
        else:
            low_docs = invoke_knowledge_retriever_tool(
                low_milvus, low_es, query, max_content=max_content, rrf_weights=rrf_weights, rerank=rerank
            )
            docs = merge_priority_docs(high_docs, low_docs, finally_k)
    else:
        docs = invoke_knowledge_retriever_tool(
            high_milvus or low_milvus,
            high_es or low_es,
            query,
            max_content=max_content,
            rrf_weights=rrf_weights,
            rerank=rerank,
        )

    return postprocess_risk_docs(
        docs,
        weight_map=weight_map,
        score_threshold=score_threshold,
        # the priority merge already truncated to finally_k
        top_k=None if priority_split else top_k,
    )
