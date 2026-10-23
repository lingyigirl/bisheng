"""Knowledge graph HTTP endpoints (manual build + browsing)."""

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import UnifiedResponseModel, resp_200
from bisheng.knowledge.domain.schemas.knowledge_graph_schema import (
    KnowledgeGraphBuildReq,
    KnowledgeGraphPublicQueryReq,
)
from bisheng.knowledge.domain.services.knowledge_graph_service import KnowledgeGraphService
from bisheng.knowledge.domain.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["KnowledgeGraph"])


@router.post(
    "/{knowledge_id}/graph/build",
    response_model=UnifiedResponseModel,
    description="Build or rebuild the knowledge graph for a document knowledge base",
)
async def build_knowledge_graph(
    knowledge_id: int,
    req: KnowledgeGraphBuildReq,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_writable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(
        KnowledgeGraphService.start_build,
        knowledge,
        req,
        login_user.user_id,
    )
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/status",
    response_model=UnifiedResponseModel,
    description="Get graph state and current build progress",
)
async def get_knowledge_graph_status(
    knowledge_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(KnowledgeGraphService.get_status, knowledge)
    return resp_200(data=result)


@router.post(
    "/{knowledge_id}/graph/cancel",
    response_model=UnifiedResponseModel,
    description="Request cancellation of the running graph build",
)
async def cancel_knowledge_graph(
    knowledge_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_writable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(KnowledgeGraphService.cancel, knowledge)
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/overview",
    response_model=UnifiedResponseModel,
    description="Entity/relation counts and build metadata",
)
async def get_knowledge_graph_overview(
    knowledge_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(KnowledgeGraphService.get_overview, knowledge)
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/graph",
    response_model=UnifiedResponseModel,
    description="Top-N graph nodes and the edges among them",
)
async def get_knowledge_graph_data(
    knowledge_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    entity_types: str | None = Query(default=None, description="Comma-separated entity types"),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    types = [item.strip() for item in (entity_types or "").split(",") if item.strip()] or None
    result = await run_in_threadpool(
        KnowledgeGraphService.get_graph,
        knowledge_id,
        limit=limit,
        entity_types=types,
    )
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/entities",
    response_model=UnifiedResponseModel,
    description="Search entities in the knowledge graph",
)
async def search_knowledge_graph_entities(
    knowledge_id: int,
    keyword: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(
        KnowledgeGraphService.search_entities,
        knowledge_id,
        keyword=keyword,
        entity_type=entity_type,
        page=page,
        page_size=page_size,
    )
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/entities/{entity_id}",
    response_model=UnifiedResponseModel,
    description="Entity detail with relations and source chunks",
)
async def get_knowledge_graph_entity_detail(
    knowledge_id: int,
    entity_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(KnowledgeGraphService.get_entity_detail, knowledge, entity_id)
    return resp_200(data=result)


@router.get(
    "/{knowledge_id}/graph/entities/{entity_id}/neighbors",
    response_model=UnifiedResponseModel,
    description="One-hop neighbourhood of an entity",
)
async def get_knowledge_graph_entity_neighbors(
    knowledge_id: int,
    entity_id: int,
    limit: int = Query(default=100, ge=1, le=300),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    await KnowledgeService._get_readable_knowledge(login_user, knowledge_id)
    result = await run_in_threadpool(
        KnowledgeGraphService.get_neighbors,
        knowledge_id,
        entity_id,
        limit=limit,
    )
    return resp_200(data=result)


@router.post(
    "/graph/public/neighbors",
    response_model=UnifiedResponseModel,
    description=(
        "Anonymous graph lookup by knowledge id. With entity_name, returns that entity's "
        "complete 1-hop neighbourhood (an unknown name yields an empty graph). Without "
        "entity_name, returns the whole knowledge-base graph, capped at 500 nodes and "
        "flagged by `truncated`."
    ),
)
async def get_public_knowledge_graph(req: KnowledgeGraphPublicQueryReq):
    # Public endpoint by design: no UserPayload, body params, knowledge_id is the access key.
    result = await run_in_threadpool(
        KnowledgeGraphService.get_public_graph,
        req.knowledge_id,
        req.entity_name,
    )
    return resp_200(data=result)


@router.delete(
    "/{knowledge_id}/graph",
    response_model=UnifiedResponseModel,
    description="Delete the stored graph data for a knowledge base",
)
async def clear_knowledge_graph(
    knowledge_id: int,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
):
    knowledge = await KnowledgeService._get_writable_knowledge(login_user, knowledge_id)
    await run_in_threadpool(KnowledgeGraphService.clear_graph, knowledge)
    return resp_200(data=None)
