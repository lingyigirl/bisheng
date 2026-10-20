"""Knowledge graph orchestration and read-side queries.

The graph is built manually from the KB detail page. This service owns:
- build admission (permission/type/force/concurrency),
- job and graph-state persistence,
- read APIs (status, overview, graph, entity search/detail/neighbors),
- clearing, stale marking and cascade cleanup for file/KB deletion.

LLM extraction itself lives in ``knowledge_graph_extraction``; Celery
orchestration lives in ``worker/knowledge/graph_worker.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy import delete, func, or_, update
from sqlmodel import select

from bisheng.common.errcode.knowledge import (
    KnowledgeGraphAlreadyExistsError,
    KnowledgeGraphBuildFailedError,
    KnowledgeGraphEntityNotFoundError,
    KnowledgeGraphJobRunningError,
    KnowledgeGraphModelNotConfiguredError,
    KnowledgeGraphTypeNotSupportedError,
)
from bisheng.core.cache.redis_manager import get_redis_client_sync
from bisheng.core.database import get_sync_db_session
from bisheng.knowledge.domain.knowledge_rag import KnowledgeRag
from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeTypeEnum
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFile, KnowledgeFileDao, KnowledgeFileStatus
from bisheng.knowledge.domain.models.knowledge_graph import (
    DEFAULT_GRAPH_ENTITY_TYPES,
    KnowledgeGraphEntity,
    KnowledgeGraphEvidence,
    KnowledgeGraphJob,
    KnowledgeGraphJobStatus,
    KnowledgeGraphRelation,
    KnowledgeGraphState,
)
from bisheng.knowledge.domain.schemas.knowledge_graph_schema import (
    KnowledgeGraphBuildReq,
    KnowledgeGraphBuildResp,
    KnowledgeGraphCancelResp,
    KnowledgeGraphDataResp,
    KnowledgeGraphEdge,
    KnowledgeGraphEntityDetailResp,
    KnowledgeGraphEntityItem,
    KnowledgeGraphEntityListResp,
    KnowledgeGraphEvidenceItem,
    KnowledgeGraphJobInfo,
    KnowledgeGraphNode,
    KnowledgeGraphOverviewResp,
    KnowledgeGraphRelationItem,
    KnowledgeGraphStatusResp,
)
from bisheng.knowledge.domain.services.knowledge_utils import KnowledgeUtils
from bisheng.llm.domain import LLMService

BUILD_LOCK_PREFIX = "kg:build:"
FILE_LOCK_PREFIX = "kg:file:"
FINALIZE_LOCK_PREFIX = "kg:finalize:"
BUILD_LOCK_TTL = 24 * 3600
FILE_LOCK_TTL = 2 * 3600
FINALIZE_LOCK_TTL = 300
MAX_ENTITY_TYPES = 20
MAX_ENTITY_TYPE_LENGTH = 64
RUNNING_STATUSES = (
    KnowledgeGraphJobStatus.QUEUED.value,
    KnowledgeGraphJobStatus.RUNNING.value,
    KnowledgeGraphJobStatus.CANCEL_REQUESTED.value,
)


class KnowledgeGraphService:
    """Business operations for the manual knowledge-graph feature."""

    # Exposed on the class so worker tasks can reuse the same TTLs.
    BUILD_LOCK_TTL = BUILD_LOCK_TTL
    FILE_LOCK_TTL = FILE_LOCK_TTL
    FINALIZE_LOCK_TTL = FINALIZE_LOCK_TTL

    # ── locks ────────────────────────────────────────────────────────────────

    @staticmethod
    def _redis():
        return get_redis_client_sync()

    @classmethod
    def build_lock_key(cls, knowledge_id: int) -> str:
        return f"{BUILD_LOCK_PREFIX}{knowledge_id}"

    @classmethod
    def file_lock_key(cls, knowledge_id: int, file_id: int) -> str:
        return f"{FILE_LOCK_PREFIX}{knowledge_id}:{file_id}"

    @classmethod
    def finalize_lock_key(cls, job_id: int) -> str:
        return f"{FINALIZE_LOCK_PREFIX}{job_id}"

    @classmethod
    def release_build_lock(cls, knowledge_id: int) -> None:
        try:
            cls._redis().delete(cls.build_lock_key(knowledge_id))
        except Exception:
            logger.exception("release knowledge graph build lock failed knowledge_id={}", knowledge_id)

    # ── build admission ──────────────────────────────────────────────────────

    @classmethod
    def start_build(cls, knowledge: Knowledge, req: KnowledgeGraphBuildReq, user_id: int) -> KnowledgeGraphBuildResp:
        """Validate and enqueue one full graph build."""
        if knowledge.type != KnowledgeTypeEnum.NORMAL.value:
            raise KnowledgeGraphTypeNotSupportedError.http_exception()

        redis = cls._redis()
        lock_key = cls.build_lock_key(knowledge.id)
        lock_token = f"{knowledge.id}:{uuid.uuid4()}"
        if not redis.setNx(lock_key, lock_token, expiration=BUILD_LOCK_TTL):
            raise KnowledgeGraphJobRunningError.http_exception()

        try:
            with get_sync_db_session() as session:
                running = session.exec(
                    select(KnowledgeGraphJob).where(
                        KnowledgeGraphJob.knowledge_id == knowledge.id,
                        KnowledgeGraphJob.status.in_(RUNNING_STATUSES),
                    )
                ).first()
                if running:
                    raise KnowledgeGraphJobRunningError.http_exception()

                entity_count = session.scalar(
                    select(func.count(KnowledgeGraphEntity.id)).where(
                        KnowledgeGraphEntity.knowledge_id == knowledge.id
                    )
                ) or 0
                # Only a completed (or stale) graph requires the explicit
                # force flag. A FAILED build may have left partial rows; those
                # are cleared automatically on the next attempt.
                has_completed_graph = knowledge.graph_state in (
                    KnowledgeGraphState.BUILT.value,
                    KnowledgeGraphState.STALE.value,
                )
                if has_completed_graph and not req.force:
                    raise KnowledgeGraphAlreadyExistsError.http_exception()

                model_id = cls._resolve_model_id(req.model_id)
                entity_types = cls._resolve_entity_types(req.entity_types)

                if req.force or entity_count > 0 or knowledge.graph_state != KnowledgeGraphState.NOT_GENERATED.value:
                    cls._clear_graph_data_sync(session, knowledge.id)

                job = KnowledgeGraphJob(
                    tenant_id=knowledge.tenant_id or 1,
                    knowledge_id=knowledge.id,
                    status=KnowledgeGraphJobStatus.QUEUED.value,
                    model_id=model_id,
                    entity_types=entity_types,
                    create_user_id=user_id,
                )
                session.add(job)
                session.flush()
                job_id = job.id

                session.exec(
                    update(Knowledge)
                    .where(Knowledge.id == knowledge.id)
                    .values(
                        graph_state=KnowledgeGraphState.BUILDING.value,
                        graph_built_at=None,
                        graph_job_id=job_id,
                    )
                )
                session.commit()
        except Exception:
            # Any admission failure must release the lock; re-raise the
            # domain error untouched.
            cls.release_build_lock(knowledge.id)
            raise

        try:
            # Lazy import avoids a service <-> worker import cycle.
            from bisheng.worker.knowledge.graph_worker import build_knowledge_graph_celery

            build_knowledge_graph_celery.delay(knowledge.id, job_id)
        except Exception as exc:
            logger.exception("dispatch knowledge graph build failed knowledge_id={} job_id={}", knowledge.id, job_id)
            try:
                cls._fail_job(job_id, knowledge.id, str(exc))
            finally:
                cls.release_build_lock(knowledge.id)
            raise KnowledgeGraphBuildFailedError(exception=exc).http_exception() from exc

        return KnowledgeGraphBuildResp(job_id=job_id, status=KnowledgeGraphJobStatus.QUEUED.value, total_files=0)

    @classmethod
    def _resolve_model_id(cls, requested: int | None) -> int:
        cfg = LLMService.get_knowledge_llm()
        model_id = requested or getattr(cfg, "graph_model_id", None) or getattr(cfg, "extract_title_model_id", None)
        if not model_id:
            raise KnowledgeGraphModelNotConfiguredError.http_exception()
        return int(model_id)

    @classmethod
    def _resolve_entity_types(cls, requested: list[str] | None) -> list[str]:
        cfg = LLMService.get_knowledge_llm()
        raw = requested or getattr(cfg, "graph_entity_types", None) or DEFAULT_GRAPH_ENTITY_TYPES
        values: list[str] = []
        for item in raw:
            text = str(item or "").strip()
            if text and text not in values:
                values.append(text[:MAX_ENTITY_TYPE_LENGTH])
        if not values:
            values = list(DEFAULT_GRAPH_ENTITY_TYPES)
        if "其他" not in values:
            values = values[: MAX_ENTITY_TYPES - 1]
            values.append("其他")
        else:
            values = values[:MAX_ENTITY_TYPES]
        return values

    # ── read-side ────────────────────────────────────────────────────────────

    @classmethod
    def get_status(cls, knowledge: Knowledge) -> KnowledgeGraphStatusResp:
        with get_sync_db_session() as session:
            job = session.exec(
                select(KnowledgeGraphJob)
                .where(KnowledgeGraphJob.knowledge_id == knowledge.id)
                .order_by(KnowledgeGraphJob.id.desc())
            ).first()
            max_file_update = session.scalar(
                select(func.max(KnowledgeFile.update_time)).where(
                    KnowledgeFile.knowledge_id == knowledge.id,
                    KnowledgeFile.status == KnowledgeFileStatus.SUCCESS.value,
                )
            )
        stale = knowledge.graph_state == KnowledgeGraphState.STALE.value
        if knowledge.graph_built_at and max_file_update and max_file_update > knowledge.graph_built_at:
            stale = True
        return KnowledgeGraphStatusResp(
            state=knowledge.graph_state or KnowledgeGraphState.NOT_GENERATED.value,
            graph_built_at=knowledge.graph_built_at,
            stale=stale,
            job=cls._job_info(job) if job else None,
        )

    @classmethod
    def get_overview(cls, knowledge: Knowledge) -> KnowledgeGraphOverviewResp:
        with get_sync_db_session() as session:
            entity_count = session.scalar(
                select(func.count(KnowledgeGraphEntity.id)).where(
                    KnowledgeGraphEntity.knowledge_id == knowledge.id
                )
            ) or 0
            relation_count = session.scalar(
                select(func.count(KnowledgeGraphRelation.id)).where(
                    KnowledgeGraphRelation.knowledge_id == knowledge.id
                )
            ) or 0
        stale = knowledge.graph_state == KnowledgeGraphState.STALE.value
        return KnowledgeGraphOverviewResp(
            state=knowledge.graph_state or KnowledgeGraphState.NOT_GENERATED.value,
            built_at=knowledge.graph_built_at,
            entity_count=entity_count,
            relation_count=relation_count,
            stale=stale,
        )

    @classmethod
    def get_graph(
        cls,
        knowledge_id: int,
        *,
        limit: int = 200,
        entity_types: list[str] | None = None,
    ) -> KnowledgeGraphDataResp:
        limit = max(1, min(limit, 500))
        with get_sync_db_session() as session:
            total = session.scalar(
                select(func.count(KnowledgeGraphEntity.id)).where(
                    KnowledgeGraphEntity.knowledge_id == knowledge_id
                )
            ) or 0
            entity_stmt = select(KnowledgeGraphEntity).where(KnowledgeGraphEntity.knowledge_id == knowledge_id)
            if entity_types:
                entity_stmt = entity_stmt.where(KnowledgeGraphEntity.entity_type.in_(entity_types))
            entities = session.exec(
                entity_stmt.order_by(
                    KnowledgeGraphEntity.mention_count.desc(), KnowledgeGraphEntity.id.asc()
                ).limit(limit)
            ).all()
            if not entities:
                return KnowledgeGraphDataResp(truncated=total > 0)

            entity_ids = [entity.id for entity in entities]
            relations = session.exec(
                select(KnowledgeGraphRelation)
                .where(
                    KnowledgeGraphRelation.knowledge_id == knowledge_id,
                    KnowledgeGraphRelation.source_entity_id.in_(entity_ids),
                    KnowledgeGraphRelation.target_entity_id.in_(entity_ids),
                )
                .limit(limit * 3)
            ).all()

        degree: dict[int, int] = dict.fromkeys(entity_ids, 0)
        edges: list[KnowledgeGraphEdge] = []
        for relation in relations:
            degree[relation.source_entity_id] = degree.get(relation.source_entity_id, 0) + 1
            degree[relation.target_entity_id] = degree.get(relation.target_entity_id, 0) + 1
            edges.append(
                KnowledgeGraphEdge(
                    id=relation.id,
                    source=relation.source_entity_id,
                    target=relation.target_entity_id,
                    label=relation.rel_type,
                    weight=relation.weight or 1.0,
                    description=relation.description,
                )
            )

        nodes = [
            KnowledgeGraphNode(
                id=entity.id,
                name=entity.name,
                type=entity.entity_type,
                description=entity.description,
                attributes=entity.attributes,
                mention_count=entity.mention_count or 0,
                degree=degree.get(entity.id, 0),
            )
            for entity in entities
        ]
        return KnowledgeGraphDataResp(nodes=nodes, edges=edges, truncated=total > len(nodes))

    @classmethod
    def search_entities(
        cls,
        knowledge_id: int,
        *,
        keyword: str | None = None,
        entity_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> KnowledgeGraphEntityListResp:
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        filters = [KnowledgeGraphEntity.knowledge_id == knowledge_id]
        if keyword:
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(
                    KnowledgeGraphEntity.name.like(pattern),
                    KnowledgeGraphEntity.normalized_name.like(pattern),
                )
            )
        if entity_type:
            filters.append(KnowledgeGraphEntity.entity_type == entity_type)

        with get_sync_db_session() as session:
            total = session.scalar(select(func.count(KnowledgeGraphEntity.id)).where(*filters)) or 0
            entities = session.exec(
                select(KnowledgeGraphEntity)
                .where(*filters)
                .order_by(KnowledgeGraphEntity.mention_count.desc(), KnowledgeGraphEntity.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        return KnowledgeGraphEntityListResp(
            data=[cls._entity_item(entity) for entity in entities],
            total=total,
        )

    @classmethod
    def get_entity_detail(cls, knowledge: Knowledge, entity_id: int) -> KnowledgeGraphEntityDetailResp:
        with get_sync_db_session() as session:
            entity = session.exec(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.id == entity_id,
                    KnowledgeGraphEntity.knowledge_id == knowledge.id,
                )
            ).first()
            if not entity:
                raise KnowledgeGraphEntityNotFoundError.http_exception()

            relations = session.exec(
                select(KnowledgeGraphRelation)
                .where(
                    KnowledgeGraphRelation.knowledge_id == knowledge.id,
                    or_(
                        KnowledgeGraphRelation.source_entity_id == entity_id,
                        KnowledgeGraphRelation.target_entity_id == entity_id,
                    ),
                )
                .limit(100)
            ).all()
            neighbor_ids = {
                relation.target_entity_id if relation.source_entity_id == entity_id else relation.source_entity_id
                for relation in relations
            }
            neighbors = session.exec(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.id.in_(neighbor_ids),
                    KnowledgeGraphEntity.knowledge_id == knowledge.id,
                )
            ).all() if neighbor_ids else []
            neighbor_map = {item.id: item for item in neighbors}

            evidence_rows = session.exec(
                select(KnowledgeGraphEvidence)
                .where(
                    KnowledgeGraphEvidence.knowledge_id == knowledge.id,
                    KnowledgeGraphEvidence.entity_id == entity_id,
                )
                .order_by(KnowledgeGraphEvidence.id.desc())
                .limit(10)
            ).all()

        relation_items: list[KnowledgeGraphRelationItem] = []
        for relation in relations:
            is_out = relation.source_entity_id == entity_id
            other_id = relation.target_entity_id if is_out else relation.source_entity_id
            other = neighbor_map.get(other_id)
            if not other:
                continue
            relation_items.append(
                KnowledgeGraphRelationItem(
                    id=relation.id,
                    direction="out" if is_out else "in",
                    label=relation.rel_type,
                    other_entity=cls._entity_item(other),
                    weight=relation.weight or 1.0,
                    description=relation.description,
                )
            )

        document_names = cls._document_names([row.document_id for row in evidence_rows])
        snippets = cls._fetch_snippets(knowledge, evidence_rows)
        evidence_items = [
            KnowledgeGraphEvidenceItem(
                document_id=row.document_id,
                document_name=document_names.get(row.document_id),
                chunk_index=row.chunk_index,
                snippet=snippets.get(row.id),
            )
            for row in evidence_rows
        ]

        return KnowledgeGraphEntityDetailResp(
            entity=cls._entity_item(entity),
            relations=relation_items,
            evidence=evidence_items,
        )

    @classmethod
    def get_neighbors(cls, knowledge_id: int, entity_id: int, *, limit: int = 100) -> KnowledgeGraphDataResp:
        limit = max(1, min(limit, 300))
        with get_sync_db_session() as session:
            center = session.exec(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.id == entity_id,
                    KnowledgeGraphEntity.knowledge_id == knowledge_id,
                )
            ).first()
            if not center:
                raise KnowledgeGraphEntityNotFoundError.http_exception()
            relations = session.exec(
                select(KnowledgeGraphRelation)
                .where(
                    KnowledgeGraphRelation.knowledge_id == knowledge_id,
                    or_(
                        KnowledgeGraphRelation.source_entity_id == entity_id,
                        KnowledgeGraphRelation.target_entity_id == entity_id,
                    ),
                )
                .limit(limit)
            ).all()
            neighbor_ids = {
                relation.target_entity_id if relation.source_entity_id == entity_id else relation.source_entity_id
                for relation in relations
            }
            neighbors = session.exec(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.id.in_(neighbor_ids),
                    KnowledgeGraphEntity.knowledge_id == knowledge_id,
                )
            ).all() if neighbor_ids else []

        entity_map = {center.id: center, **{item.id: item for item in neighbors}}
        degree: dict[int, int] = {entity_id: 0}
        edges: list[KnowledgeGraphEdge] = []
        for relation in relations:
            if relation.source_entity_id not in entity_map or relation.target_entity_id not in entity_map:
                # A dangling endpoint can survive if a file was deleted while
                # the graph was being built; do not hand it to the renderer.
                continue
            degree[relation.source_entity_id] = degree.get(relation.source_entity_id, 0) + 1
            degree[relation.target_entity_id] = degree.get(relation.target_entity_id, 0) + 1
            edges.append(
                KnowledgeGraphEdge(
                    id=relation.id,
                    source=relation.source_entity_id,
                    target=relation.target_entity_id,
                    label=relation.rel_type,
                    weight=relation.weight or 1.0,
                    description=relation.description,
                )
            )

        nodes = [
            KnowledgeGraphNode(
                id=item.id,
                name=item.name,
                type=item.entity_type,
                description=item.description,
                attributes=item.attributes,
                mention_count=item.mention_count or 0,
                degree=degree.get(item.id, 0),
            )
            for item in entity_map.values()
        ]
        return KnowledgeGraphDataResp(nodes=nodes, edges=edges, truncated=False)

    # ── mutation / cascade ───────────────────────────────────────────────────

    @classmethod
    def cancel(cls, knowledge: Knowledge) -> KnowledgeGraphCancelResp:
        with get_sync_db_session() as session:
            job = session.exec(
                select(KnowledgeGraphJob)
                .where(
                    KnowledgeGraphJob.knowledge_id == knowledge.id,
                    KnowledgeGraphJob.status.in_(RUNNING_STATUSES),
                )
                .order_by(KnowledgeGraphJob.id.desc())
            ).first()
            if not job:
                raise KnowledgeGraphJobRunningError(msg="没有正在运行的生成任务").http_exception()
            if job.status != KnowledgeGraphJobStatus.CANCEL_REQUESTED.value:
                session.exec(
                    update(KnowledgeGraphJob)
                    .where(KnowledgeGraphJob.id == job.id)
                    .values(status=KnowledgeGraphJobStatus.CANCEL_REQUESTED.value)
                )
                session.commit()
            return KnowledgeGraphCancelResp(
                job_id=job.id,
                status=KnowledgeGraphJobStatus.CANCEL_REQUESTED.value,
            )

    @classmethod
    def clear_graph(cls, knowledge: Knowledge) -> None:
        with get_sync_db_session() as session:
            running = session.exec(
                select(KnowledgeGraphJob).where(
                    KnowledgeGraphJob.knowledge_id == knowledge.id,
                    KnowledgeGraphJob.status.in_(RUNNING_STATUSES),
                )
            ).first()
            if running:
                raise KnowledgeGraphJobRunningError.http_exception()
            cls._clear_graph_data_sync(session, knowledge.id)
            session.exec(
                update(Knowledge)
                .where(Knowledge.id == knowledge.id)
                .values(
                    graph_state=KnowledgeGraphState.NOT_GENERATED.value,
                    graph_built_at=None,
                    graph_job_id=None,
                )
            )
            session.commit()
        cls.release_build_lock(knowledge.id)

    @classmethod
    def mark_stale_sync(cls, knowledge_id: int) -> None:
        """Mark a completed graph stale after a source file/chunk changed."""
        try:
            with get_sync_db_session() as session:
                session.exec(
                    update(Knowledge)
                    .where(
                        Knowledge.id == knowledge_id,
                        Knowledge.graph_state == KnowledgeGraphState.BUILT.value,
                    )
                    .values(graph_state=KnowledgeGraphState.STALE.value)
                )
                session.commit()
        except Exception:
            logger.exception("mark knowledge graph stale failed knowledge_id={}", knowledge_id)

    @classmethod
    def on_files_deleted_sync(cls, knowledge_id: int, file_ids: list[int]) -> None:
        """Remove evidence for deleted files and drop now-orphaned graph rows."""
        if not file_ids:
            return
        try:
            with get_sync_db_session() as session:
                session.exec(
                    delete(KnowledgeGraphEvidence).where(
                        KnowledgeGraphEvidence.knowledge_id == knowledge_id,
                        KnowledgeGraphEvidence.document_id.in_(file_ids),
                    )
                )
                cls._cleanup_orphans_sync(session, knowledge_id)
                session.exec(
                    update(Knowledge)
                    .where(
                        Knowledge.id == knowledge_id,
                        Knowledge.graph_state == KnowledgeGraphState.BUILT.value,
                    )
                    .values(graph_state=KnowledgeGraphState.STALE.value)
                )
                session.commit()
        except Exception:
            logger.exception("knowledge graph cascade delete failed knowledge_id={} file_ids={}", knowledge_id, file_ids)

    @classmethod
    def delete_by_knowledge_sync(cls, knowledge_id: int) -> None:
        """Delete every graph row for a knowledge base (KB deletion)."""
        try:
            with get_sync_db_session() as session:
                session.exec(delete(KnowledgeGraphEvidence).where(KnowledgeGraphEvidence.knowledge_id == knowledge_id))
                session.exec(delete(KnowledgeGraphRelation).where(KnowledgeGraphRelation.knowledge_id == knowledge_id))
                session.exec(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.knowledge_id == knowledge_id))
                session.exec(delete(KnowledgeGraphJob).where(KnowledgeGraphJob.knowledge_id == knowledge_id))
                session.commit()
            cls.release_build_lock(knowledge_id)
        except Exception:
            logger.exception("delete knowledge graph rows failed knowledge_id={}", knowledge_id)

    @classmethod
    def cleanup_orphans_sync(cls, knowledge_id: int) -> None:
        with get_sync_db_session() as session:
            cls._cleanup_orphans_sync(session, knowledge_id)
            session.commit()

    # ── helpers ──────────────────────────────────────────────────────────────

    @classmethod
    def _clear_graph_data_sync(cls, session, knowledge_id: int) -> None:
        session.exec(delete(KnowledgeGraphEvidence).where(KnowledgeGraphEvidence.knowledge_id == knowledge_id))
        session.exec(delete(KnowledgeGraphRelation).where(KnowledgeGraphRelation.knowledge_id == knowledge_id))
        session.exec(delete(KnowledgeGraphEntity).where(KnowledgeGraphEntity.knowledge_id == knowledge_id))

    @classmethod
    def _cleanup_orphans_sync(cls, session, knowledge_id: int) -> None:
        entity_has_evidence = select(KnowledgeGraphEvidence.id).where(
            KnowledgeGraphEvidence.entity_id == KnowledgeGraphEntity.id,
            KnowledgeGraphEvidence.knowledge_id == knowledge_id,
        ).exists()
        session.exec(
            delete(KnowledgeGraphEntity).where(
                KnowledgeGraphEntity.knowledge_id == knowledge_id,
                ~entity_has_evidence,
            )
        )
        relation_has_evidence = select(KnowledgeGraphEvidence.id).where(
            KnowledgeGraphEvidence.relation_id == KnowledgeGraphRelation.id,
            KnowledgeGraphEvidence.knowledge_id == knowledge_id,
        ).exists()
        session.exec(
            delete(KnowledgeGraphRelation).where(
                KnowledgeGraphRelation.knowledge_id == knowledge_id,
                ~relation_has_evidence,
            )
        )

    @classmethod
    def _fail_job(cls, job_id: int, knowledge_id: int, error: str) -> None:
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

    @staticmethod
    def _job_info(job: KnowledgeGraphJob) -> KnowledgeGraphJobInfo:
        return KnowledgeGraphJobInfo(
            job_id=job.id,
            status=job.status,
            total_files=job.total_files or 0,
            done_files=job.done_files or 0,
            failed_files=job.failed_files or 0,
            total_chunks=job.total_chunks or 0,
            done_chunks=job.done_chunks or 0,
            token_in=job.token_in or 0,
            token_out=job.token_out or 0,
            error_message=job.error_message,
        )

    @staticmethod
    def _entity_item(entity: KnowledgeGraphEntity) -> KnowledgeGraphEntityItem:
        return KnowledgeGraphEntityItem(
            id=entity.id,
            name=entity.name,
            type=entity.entity_type,
            description=entity.description,
            attributes=entity.attributes,
            mention_count=entity.mention_count or 0,
        )

    @staticmethod
    def _document_names(document_ids: list[int]) -> dict[int, str]:
        if not document_ids:
            return {}
        files = KnowledgeFileDao.get_file_by_ids(sorted(set(document_ids)))
        return {file.id: file.file_name for file in files}

    @classmethod
    def _fetch_snippets(
        cls,
        knowledge: Knowledge,
        evidence_rows: list[KnowledgeGraphEvidence],
    ) -> dict[int, str | None]:
        """Best-effort ES lookup for the first ~500 chars of each evidence chunk."""
        if not evidence_rows:
            return {}
        snippets: dict[int, str | None] = {}
        try:
            es_vectorstore = KnowledgeRag.init_knowledge_es_vectorstore_sync(knowledge=knowledge)
            index_name = knowledge.index_name or knowledge.collection_name
            for row in evidence_rows:
                try:
                    response = es_vectorstore.client.search(
                        index=index_name,
                        body={
                            "query": {
                                "bool": {
                                    "filter": [
                                        {"term": {"metadata.document_id": row.document_id}},
                                        {"term": {"metadata.chunk_index": row.chunk_index}},
                                    ]
                                }
                            },
                            "size": 1,
                        },
                    )
                    hits = response.get("hits", {}).get("hits", [])
                    if not hits:
                        snippets[row.id] = None
                        continue
                    text = hits[0].get("_source", {}).get("text", "")
                    snippets[row.id] = KnowledgeUtils.split_chunk_metadata(text)[:500]
                except Exception:
                    logger.warning(
                        "fetch knowledge graph evidence snippet failed knowledge_id={} document_id={} chunk_index={}",
                        knowledge.id,
                        row.document_id,
                        row.chunk_index,
                    )
                    snippets[row.id] = None
        except Exception:
            logger.exception("init ES for knowledge graph evidence failed knowledge_id={}", knowledge.id)
            return {row.id: None for row in evidence_rows}
        return snippets
