"""Celery tasks for the manual knowledge-graph build.

The coordinator enumerates successfully parsed files and fans out one task per
file. File tasks are idempotent: they delete that document's evidence before
re-extracting, so an ``acks_late`` redelivery is safe.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import delete, update
from sqlmodel import select

from bisheng.core.cache.redis_manager import get_redis_client_sync
from bisheng.core.database import get_sync_db_session
from bisheng.knowledge.domain.knowledge_rag import KnowledgeRag
from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeDao
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFile, KnowledgeFileDao, KnowledgeFileStatus
from bisheng.knowledge.domain.models.knowledge_graph import (
    KnowledgeGraphEntity,
    KnowledgeGraphEvidence,
    KnowledgeGraphJob,
    KnowledgeGraphJobStatus,
    KnowledgeGraphRelation,
    KnowledgeGraphState,
)
from bisheng.knowledge.domain.services.knowledge_graph_extraction import (
    extract_chunk_graph,
)
from bisheng.knowledge.domain.services.knowledge_graph_service import KnowledgeGraphService
from bisheng.knowledge.domain.services.knowledge_utils import KnowledgeUtils
from bisheng.worker.main import bisheng_celery

GRAPH_QUEUE = "graph_celery"
CHUNK_SEARCH_SIZE = 10000
JOB_TIMEOUT = timedelta(hours=2)


@bisheng_celery.task(
    acks_late=True,
    time_limit=1800,
    soft_time_limit=1500,
    name="bisheng.worker.knowledge.graph.extract_knowledge_graph_file_celery",
)
def extract_knowledge_graph_file_celery(knowledge_id: int, job_id: int, file_id: int) -> None:
    """Extract entities/relations from one file and update job progress."""
    redis = get_redis_client_sync()
    lock_key = KnowledgeGraphService.file_lock_key(knowledge_id, file_id)
    lock_token = str(uuid.uuid4())
    if not redis.setNx(lock_key, lock_token, expiration=KnowledgeGraphService.FILE_LOCK_TTL):
        logger.info("knowledge graph file task already running knowledge_id={} file_id={}", knowledge_id, file_id)
        return

    try:
        knowledge = KnowledgeDao.query_by_id(knowledge_id)
        if not knowledge:
            logger.warning("knowledge graph file task: knowledge missing knowledge_id={}", knowledge_id)
            return
        with get_sync_db_session() as session:
            job = session.get(KnowledgeGraphJob, job_id)
        if not job or job.status in (
            KnowledgeGraphJobStatus.CANCEL_REQUESTED.value,
            KnowledgeGraphJobStatus.CANCELLED.value,
            KnowledgeGraphJobStatus.FAILED.value,
            KnowledgeGraphJobStatus.SUCCESS.value,
            KnowledgeGraphJobStatus.PARTIAL_FAILED.value,
        ):
            logger.info("knowledge graph file task skipped job_id={} status={}", job_id, getattr(job, "status", None))
            return

        file = next(iter(KnowledgeFileDao.get_file_by_ids([file_id])), None)
        if not file:
            logger.warning("knowledge graph file task: file missing knowledge_id={} file_id={}", knowledge_id, file_id)
            _mark_file_failed(job_id, file_id, "文件不存在")
            _finalize_job_if_done(job_id)
            return

        chunks = _load_chunks(knowledge, file_id)
        if not chunks:
            logger.info("knowledge graph file task: no chunks knowledge_id={} file_id={}", knowledge_id, file_id)
            _mark_file_done(job_id, file_id)
            _finalize_job_if_done(job_id)
            return

        # Idempotency: clear this document's old evidence before re-extracting.
        with get_sync_db_session() as session:
            session.exec(
                delete(KnowledgeGraphEvidence).where(
                    KnowledgeGraphEvidence.knowledge_id == knowledge_id,
                    KnowledgeGraphEvidence.document_id == file_id,
                )
            )
            session.commit()

        processed_chunks = 0
        chunk_failures = 0
        with get_sync_db_session() as session:
            entity_cache: dict[tuple[str, str], KnowledgeGraphEntity] = {}
            relation_cache: dict[tuple[int, int, str], KnowledgeGraphRelation] = {}
            seen_evidence: set[tuple[str, int, int]] = set()

            for index, chunk in enumerate(chunks):
                if _is_cancel_requested(job_id):
                    logger.info("knowledge graph file task cancelled job_id={} file_id={}", job_id, file_id)
                    break

                chunk_text = chunk["text"]
                chunk_index = chunk["chunk_index"]
                chunk_pk = chunk["chunk_pk"]
                entities, relations, tin, tout, failed = extract_chunk_graph(
                    knowledge_id=knowledge_id,
                    file_name=file.file_name,
                    chunk_text=chunk_text,
                    entity_types=list(job.entity_types or []),
                    model_id=int(job.model_id),
                    invoke_user_id=int(job.create_user_id or 0),
                )
                processed_chunks += 1
                chunk_failures += failed
                if failed:
                    # A failed chunk still counts toward progress and token
                    # usage; otherwise a file with failures would never reach
                    # 100% in the UI.
                    _touch_job_progress(session, job_id, tin, tout)
                    session.commit()
                    continue

                for entity_data in entities:
                    entity = _upsert_entity(
                        session,
                        entity_cache,
                        knowledge_id=knowledge_id,
                        tenant_id=knowledge.tenant_id or 1,
                        data=entity_data,
                    )
                    evidence_key = ("entity", entity.id, chunk_index)
                    if evidence_key not in seen_evidence:
                        seen_evidence.add(evidence_key)
                        session.add(
                            KnowledgeGraphEvidence(
                                tenant_id=knowledge.tenant_id or 1,
                                knowledge_id=knowledge_id,
                                entity_id=entity.id,
                                document_id=file_id,
                                chunk_index=chunk_index,
                                chunk_pk=chunk_pk,
                                confidence=1.0,
                            )
                        )

                for relation_data in relations:
                    source = _find_entity(
                        session, entity_cache, knowledge_id, relation_data["source_normalized"]
                    )
                    target = _find_entity(
                        session, entity_cache, knowledge_id, relation_data["target_normalized"]
                    )
                    if not source or not target:
                        continue
                    relation = _upsert_relation(
                        session,
                        relation_cache,
                        knowledge_id=knowledge_id,
                        tenant_id=knowledge.tenant_id or 1,
                        source_id=source.id,
                        target_id=target.id,
                        data=relation_data,
                    )
                    evidence_key = ("relation", relation.id, chunk_index)
                    if evidence_key not in seen_evidence:
                        seen_evidence.add(evidence_key)
                        session.add(
                            KnowledgeGraphEvidence(
                                tenant_id=knowledge.tenant_id or 1,
                                knowledge_id=knowledge_id,
                                relation_id=relation.id,
                                document_id=file_id,
                                chunk_index=chunk_index,
                                chunk_pk=chunk_pk,
                                confidence=relation_data.get("weight"),
                            )
                        )

                # Commit per chunk so a large file does not hold one huge
                # transaction and progress survives a worker restart.
                _touch_job_progress(session, job_id, tin, tout)
                session.commit()
                if index and index % 20 == 0:
                    logger.info(
                        "knowledge graph extraction progress knowledge_id={} file_id={} chunks={}/{}",
                        knowledge_id,
                        file_id,
                        index + 1,
                        len(chunks),
                    )

        if chunk_failures and chunk_failures >= len(chunks):
            _mark_file_failed(job_id, file_id, f"全部 {len(chunks)} 个分块抽取失败")
        else:
            _mark_file_done(job_id, file_id)
        _finalize_job_if_done(job_id)
    except Exception as exc:
        logger.exception("knowledge graph file task failed knowledge_id={} job_id={} file_id={}", knowledge_id, job_id, file_id)
        _mark_file_failed(job_id, file_id, str(exc))
        _finalize_job_if_done(job_id)
    finally:
        try:
            redis.delete(lock_key)
        except Exception:
            logger.exception("release knowledge graph file lock failed key={}", lock_key)


@bisheng_celery.task(
    acks_late=True,
    time_limit=600,
    soft_time_limit=540,
    name="bisheng.worker.knowledge.graph.build_knowledge_graph_celery",
)
def build_knowledge_graph_celery(knowledge_id: int, job_id: int) -> None:
    """Enumerate files, count chunks and fan out per-file extraction tasks."""
    knowledge = KnowledgeDao.query_by_id(knowledge_id)
    if not knowledge:
        logger.warning("knowledge graph build: knowledge missing knowledge_id={}", knowledge_id)
        return

    with get_sync_db_session() as session:
        job = session.get(KnowledgeGraphJob, job_id)
        if not job:
            logger.warning("knowledge graph build: job missing job_id={}", job_id)
            return
        if job.status == KnowledgeGraphJobStatus.CANCEL_REQUESTED.value:
            _finalize_job_if_done(job_id)
            return
        job.status = KnowledgeGraphJobStatus.RUNNING.value
        job.start_time = job.start_time or datetime.now()
        session.add(job)
        session.commit()

    try:
        files = _list_build_files(knowledge_id)
        if not files:
            _fail_job(job_id, knowledge_id, "知识库中没有解析成功的文件")
            return

        # The files can be marked SUCCESS in MySQL while the ES index has been
        # dropped/reset (common in dev). Fail early with an actionable message
        # instead of letting every file task hit a 404.
        index_name = _es_index_name(knowledge)
        try:
            if not _es_index_exists(knowledge):
                _fail_job(
                    job_id,
                    knowledge_id,
                    f"知识库 ES 索引不存在: {index_name}, 请重新解析文件或重建索引后重试",
                )
                return
        except Exception as exc:
            logger.exception("knowledge graph ES index check failed knowledge_id={}", knowledge_id)
            _fail_job(job_id, knowledge_id, f"无法连接 ES 或检查索引失败: {exc}")
            return

        total_chunks = _count_chunks(knowledge, files)
        with get_sync_db_session() as session:
            session.exec(
                update(KnowledgeGraphJob)
                .where(KnowledgeGraphJob.id == job_id)
                .values(total_files=len(files), total_chunks=total_chunks)
            )
            session.commit()

        dispatched = 0
        for file in files:
            if _is_cancel_requested(job_id):
                logger.info("knowledge graph build cancelled before dispatch job_id={}", job_id)
                break
            try:
                extract_knowledge_graph_file_celery.delay(knowledge_id, job_id, file.id)
                dispatched += 1
            except Exception as exc:
                logger.exception(
                    "dispatch knowledge graph file failed knowledge_id={} job_id={} file_id={}",
                    knowledge_id,
                    job_id,
                    file.id,
                )
                _mark_file_failed(job_id, file.id, f"任务派发失败: {exc}")

        if dispatched == 0:
            _finalize_job_if_done(job_id)
    except Exception as exc:
        logger.exception("knowledge graph build failed knowledge_id={} job_id={}", knowledge_id, job_id)
        _fail_job(job_id, knowledge_id, str(exc))


@bisheng_celery.task(
    acks_late=True,
    time_limit=600,
    soft_time_limit=540,
    name="bisheng.worker.knowledge.graph.reconcile_knowledge_graph_job_celery",
)
def reconcile_knowledge_graph_job_celery() -> None:
    """Fail jobs that stopped making progress (worker killed, queue lost, ...)."""
    from bisheng.core.context.tenant import bypass_tenant_filter

    threshold = datetime.now() - JOB_TIMEOUT
    try:
        with bypass_tenant_filter():
            with get_sync_db_session() as session:
                jobs = session.exec(
                    select(KnowledgeGraphJob).where(
                        KnowledgeGraphJob.status.in_(
                            (
                                KnowledgeGraphJobStatus.QUEUED.value,
                                KnowledgeGraphJobStatus.RUNNING.value,
                                KnowledgeGraphJobStatus.CANCEL_REQUESTED.value,
                            )
                        ),
                        KnowledgeGraphJob.update_time < threshold,
                    )
                ).all()
                for job in jobs:
                    if job.status == KnowledgeGraphJobStatus.CANCEL_REQUESTED.value:
                        # Cancelled builds leave partial rows; clear them so the
                        # UI does not present a half-built graph.
                        KnowledgeGraphService._clear_graph_data_sync(session, job.knowledge_id)
                        session.exec(
                            update(KnowledgeGraphJob)
                            .where(KnowledgeGraphJob.id == job.id)
                            .values(status=KnowledgeGraphJobStatus.CANCELLED.value, end_time=datetime.now())
                        )
                        session.exec(
                            update(Knowledge)
                            .where(Knowledge.id == job.knowledge_id)
                            .values(graph_state=KnowledgeGraphState.NOT_GENERATED.value, graph_built_at=None)
                        )
                    else:
                        session.exec(
                            update(KnowledgeGraphJob)
                            .where(KnowledgeGraphJob.id == job.id)
                            .values(
                                status=KnowledgeGraphJobStatus.FAILED.value,
                                error_message="知识图谱生成任务超时",
                                end_time=datetime.now(),
                            )
                        )
                        session.exec(
                            update(Knowledge)
                            .where(Knowledge.id == job.knowledge_id)
                            .values(graph_state=KnowledgeGraphState.FAILED.value)
                        )
                    KnowledgeGraphService.release_build_lock(job.knowledge_id)
                session.commit()
    except Exception:
        logger.exception("reconcile knowledge graph jobs failed")


# ── helpers ──────────────────────────────────────────────────────────────────


def _list_build_files(knowledge_id: int) -> list[KnowledgeFile]:
    files = KnowledgeFileDao.get_file_by_filters(
        knowledge_id,
        status=[KnowledgeFileStatus.SUCCESS.value],
    )
    if not files:
        return []
    try:
        from bisheng.knowledge.domain.services.knowledge_flow_retrieval import _fetch_non_primary_file_ids

        non_primary = set(_fetch_non_primary_file_ids([knowledge_id]))
    except Exception:
        logger.exception("knowledge graph version filter failed knowledge_id={}", knowledge_id)
        non_primary = set()
    return [file for file in files if file.id not in non_primary]


def _es_index_name(knowledge: Knowledge) -> str:
    return knowledge.index_name or knowledge.collection_name


def _es_index_exists(knowledge: Knowledge) -> bool:
    index_name = _es_index_name(knowledge)
    es_vectorstore = KnowledgeRag.init_es_vectorstore_sync(index_name)
    return bool(es_vectorstore.client.indices.exists(index=index_name))


def _count_chunks(knowledge: Knowledge, files: list[KnowledgeFile]) -> int:
    try:
        es_vectorstore = KnowledgeRag.init_es_vectorstore_sync(_es_index_name(knowledge))
        index_name = _es_index_name(knowledge)
        total = 0
        for file in files:
            try:
                response = es_vectorstore.client.count(
                    index=index_name,
                    body={"query": {"term": {"metadata.document_id": file.id}}},
                )
                total += int(response.get("count", 0) or 0)
            except Exception:
                logger.warning(
                    "knowledge graph chunk count failed knowledge_id={} file_id={}",
                    knowledge.id,
                    file.id,
                )
        return total
    except Exception:
        logger.exception("knowledge graph ES init for chunk count failed knowledge_id={}", knowledge.id)
        return 0


def _load_chunks(knowledge: Knowledge, file_id: int) -> list[dict[str, Any]]:
    index_name = _es_index_name(knowledge)
    es_vectorstore = KnowledgeRag.init_es_vectorstore_sync(index_name)
    response = es_vectorstore.client.search(
        index=index_name,
        body={
            "query": {"term": {"metadata.document_id": file_id}},
            "size": CHUNK_SEARCH_SIZE,
        },
    )
    hits = response.get("hits", {}).get("hits", [])
    total = response.get("hits", {}).get("total", {})
    total_count = total.get("value") if isinstance(total, dict) else total
    if total_count and int(total_count) > len(hits):
        logger.warning(
            "knowledge graph file has more than {} chunks, only the first {} are processed file_id={}",
            CHUNK_SEARCH_SIZE,
            len(hits),
            file_id,
        )
    chunks: list[dict[str, Any]] = []
    for hit in hits:
        source = hit.get("_source", {}) or {}
        metadata = source.get("metadata", {}) or {}
        text = KnowledgeUtils.split_chunk_metadata(source.get("text", "") or "")
        if not text.strip():
            continue
        chunks.append(
            {
                "text": text,
                "chunk_index": int(metadata.get("chunk_index") or 0),
                "chunk_pk": hit.get("_id"),
            }
        )
    return chunks


def _upsert_entity(
    session,
    cache: dict[tuple[str, str], KnowledgeGraphEntity],
    *,
    knowledge_id: int,
    tenant_id: int | None,
    data: dict[str, Any],
) -> KnowledgeGraphEntity:
    key = (data["normalized_name"], data["entity_type"])
    entity = cache.get(key)
    if entity is None:
        entity = session.exec(
            select(KnowledgeGraphEntity).where(
                KnowledgeGraphEntity.knowledge_id == knowledge_id,
                KnowledgeGraphEntity.normalized_name == data["normalized_name"],
                KnowledgeGraphEntity.entity_type == data["entity_type"],
            )
        ).first()
    if entity is None:
        entity = KnowledgeGraphEntity(
            tenant_id=tenant_id,
            knowledge_id=knowledge_id,
            name=data["name"],
            normalized_name=data["normalized_name"],
            entity_type=data["entity_type"],
            description=data.get("description"),
            attributes=data.get("attributes"),
            aliases=[],
            mention_count=1,
        )
        session.add(entity)
        session.flush()
    else:
        if data.get("description") and (
            not entity.description or len(data["description"]) > len(entity.description)
        ):
            entity.description = data["description"]
        if data.get("attributes"):
            merged = dict(entity.attributes or {})
            merged.update(data["attributes"])
            entity.attributes = merged
        if data["name"] != entity.name:
            aliases = list(entity.aliases or [])
            if data["name"] not in aliases:
                aliases.append(data["name"])
                entity.aliases = aliases
        entity.mention_count = (entity.mention_count or 0) + 1
        session.add(entity)
    cache[key] = entity
    return entity


def _find_entity(
    session,
    cache: dict[tuple[str, str], KnowledgeGraphEntity],
    knowledge_id: int,
    normalized_name: str,
) -> KnowledgeGraphEntity | None:
    for entity in cache.values():
        if entity.normalized_name == normalized_name:
            return entity
    return session.exec(
        select(KnowledgeGraphEntity).where(
            KnowledgeGraphEntity.knowledge_id == knowledge_id,
            KnowledgeGraphEntity.normalized_name == normalized_name,
        )
    ).first()


def _upsert_relation(
    session,
    cache: dict[tuple[int, int, str], KnowledgeGraphRelation],
    *,
    knowledge_id: int,
    tenant_id: int | None,
    source_id: int,
    target_id: int,
    data: dict[str, Any],
) -> KnowledgeGraphRelation:
    key = (source_id, target_id, data["rel_type"])
    relation = cache.get(key)
    if relation is None:
        relation = session.exec(
            select(KnowledgeGraphRelation).where(
                KnowledgeGraphRelation.knowledge_id == knowledge_id,
                KnowledgeGraphRelation.source_entity_id == source_id,
                KnowledgeGraphRelation.target_entity_id == target_id,
                KnowledgeGraphRelation.rel_type == data["rel_type"],
            )
        ).first()
    if relation is None:
        relation = KnowledgeGraphRelation(
            tenant_id=tenant_id,
            knowledge_id=knowledge_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            rel_type=data["rel_type"],
            description=data.get("description"),
            weight=data.get("weight", 0.7),
            attributes=data.get("attributes"),
            mention_count=1,
        )
        session.add(relation)
        session.flush()
    else:
        if data.get("description") and (
            not relation.description or len(data["description"]) > len(relation.description)
        ):
            relation.description = data["description"]
        if data.get("attributes"):
            merged = dict(relation.attributes or {})
            merged.update(data["attributes"])
            relation.attributes = merged
        relation.weight = max(relation.weight or 0.0, data.get("weight", 0.7))
        relation.mention_count = (relation.mention_count or 0) + 1
        session.add(relation)
    cache[key] = relation
    return relation


def _claim_file(job_id: int, file_id: int) -> bool:
    """Best-effort idempotency marker for acks_late redelivery.

    The first outcome (done/failed) for a file wins; a redelivered task after
    the job finalized returns early because the job status is terminal. If
    Redis is unavailable, fall back to counting (the old behaviour).
    """
    try:
        redis = get_redis_client_sync()
        return bool(redis.connection.sadd(f"kg:job:{job_id}:processed", str(file_id)))
    except Exception:
        logger.exception("claim knowledge graph file failed job_id={} file_id={}", job_id, file_id)
        return True


def _touch_job_progress(session, job_id: int, token_in: int, token_out: int) -> None:
    """Persist per-chunk progress so the UI moves during a long file."""
    session.exec(
        update(KnowledgeGraphJob)
        .where(KnowledgeGraphJob.id == job_id)
        .values(
            done_chunks=KnowledgeGraphJob.done_chunks + 1,
            token_in=KnowledgeGraphJob.token_in + token_in,
            token_out=KnowledgeGraphJob.token_out + token_out,
        )
    )


def _mark_file_done(job_id: int, file_id: int) -> None:
    if not _claim_file(job_id, file_id):
        return
    with get_sync_db_session() as session:
        session.exec(
            update(KnowledgeGraphJob)
            .where(KnowledgeGraphJob.id == job_id)
            .values(done_files=KnowledgeGraphJob.done_files + 1)
        )
        session.commit()


def _mark_file_failed(job_id: int, file_id: int, error: str) -> None:
    if not _claim_file(job_id, file_id):
        return
    with get_sync_db_session() as session:
        session.exec(
            update(KnowledgeGraphJob)
            .where(KnowledgeGraphJob.id == job_id)
            .values(
                failed_files=KnowledgeGraphJob.failed_files + 1,
                error_message=(error or "")[:1000],
            )
        )
        session.commit()


def _fail_job(job_id: int, knowledge_id: int, error: str) -> None:
    with get_sync_db_session() as session:
        session.exec(
            update(KnowledgeGraphJob)
            .where(KnowledgeGraphJob.id == job_id)
            .values(
                status=KnowledgeGraphJobStatus.FAILED.value,
                error_message=(error or "")[:1000],
                end_time=datetime.now(),
            )
        )
        session.exec(
            update(Knowledge)
            .where(Knowledge.id == knowledge_id)
            .values(graph_state=KnowledgeGraphState.FAILED.value)
        )
        session.commit()
    KnowledgeGraphService.release_build_lock(knowledge_id)


def _is_cancel_requested(job_id: int) -> bool:
    with get_sync_db_session() as session:
        status = session.scalar(select(KnowledgeGraphJob.status).where(KnowledgeGraphJob.id == job_id))
    return status == KnowledgeGraphJobStatus.CANCEL_REQUESTED.value


def _finalize_job_if_done(job_id: int) -> None:
    redis = get_redis_client_sync()
    lock_key = KnowledgeGraphService.finalize_lock_key(job_id)
    if not redis.setNx(lock_key, str(uuid.uuid4()), expiration=KnowledgeGraphService.FINALIZE_LOCK_TTL):
        return
    try:
        with get_sync_db_session() as session:
            job = session.exec(
                select(KnowledgeGraphJob).where(KnowledgeGraphJob.id == job_id).with_for_update()
            ).first()
            if not job:
                return
            if job.status in (
                KnowledgeGraphJobStatus.SUCCESS.value,
                KnowledgeGraphJobStatus.PARTIAL_FAILED.value,
                KnowledgeGraphJobStatus.FAILED.value,
                KnowledgeGraphJobStatus.CANCELLED.value,
            ):
                return
            finished = (job.done_files or 0) + (job.failed_files or 0)
            if finished < (job.total_files or 0):
                return

            if job.status == KnowledgeGraphJobStatus.CANCEL_REQUESTED.value:
                KnowledgeGraphService._clear_graph_data_sync(session, job.knowledge_id)
                job.status = KnowledgeGraphJobStatus.CANCELLED.value
                job.end_time = datetime.now()
                session.add(job)
                session.exec(
                    update(Knowledge)
                    .where(Knowledge.id == job.knowledge_id)
                    .values(graph_state=KnowledgeGraphState.NOT_GENERATED.value, graph_built_at=None)
                )
            else:
                KnowledgeGraphService._cleanup_orphans_sync(session, job.knowledge_id)
                if (job.total_files or 0) > 0 and (job.failed_files or 0) >= (job.total_files or 0):
                    job.status = KnowledgeGraphJobStatus.FAILED.value
                    graph_state = KnowledgeGraphState.FAILED.value
                    built_at = None
                elif (job.failed_files or 0) > 0:
                    job.status = KnowledgeGraphJobStatus.PARTIAL_FAILED.value
                    graph_state = KnowledgeGraphState.BUILT.value
                    built_at = datetime.now()
                else:
                    job.status = KnowledgeGraphJobStatus.SUCCESS.value
                    graph_state = KnowledgeGraphState.BUILT.value
                    built_at = datetime.now()
                job.end_time = datetime.now()
                session.add(job)
                session.exec(
                    update(Knowledge)
                    .where(Knowledge.id == job.knowledge_id)
                    .values(graph_state=graph_state, graph_built_at=built_at)
                )
            session.commit()
            knowledge_id = job.knowledge_id
        KnowledgeGraphService.release_build_lock(knowledge_id)
        try:
            redis.delete(f"kg:job:{job_id}:processed")
        except Exception:
            logger.exception("cleanup knowledge graph job marker failed job_id={}", job_id)
    except Exception:
        logger.exception("finalize knowledge graph job failed job_id={}", job_id)
        # A finalize bug must not leave the job RUNNING and the build lock held
        # forever; fall back to a plain FAILED state so the user can retry.
        try:
            with get_sync_db_session() as session:
                job = session.get(KnowledgeGraphJob, job_id)
                if job and job.status not in (
                    KnowledgeGraphJobStatus.SUCCESS.value,
                    KnowledgeGraphJobStatus.PARTIAL_FAILED.value,
                    KnowledgeGraphJobStatus.FAILED.value,
                    KnowledgeGraphJobStatus.CANCELLED.value,
                ):
                    session.exec(
                        update(KnowledgeGraphJob)
                        .where(KnowledgeGraphJob.id == job_id)
                        .values(
                            status=KnowledgeGraphJobStatus.FAILED.value,
                            error_message="知识图谱收尾失败, 请重试",
                            end_time=datetime.now(),
                        )
                    )
                    session.exec(
                        update(Knowledge)
                        .where(Knowledge.id == job.knowledge_id)
                        .values(graph_state=KnowledgeGraphState.FAILED.value)
                    )
                    session.commit()
                    KnowledgeGraphService.release_build_lock(job.knowledge_id)
            try:
                redis.delete(f"kg:job:{job_id}:processed")
            except Exception:
                logger.exception("cleanup knowledge graph job marker failed job_id={}", job_id)
        except Exception:
            logger.exception("fallback fail knowledge graph job failed job_id={}", job_id)
    finally:
        try:
            redis.delete(lock_key)
        except Exception:
            logger.exception("release knowledge graph finalize lock failed key={}", lock_key)
