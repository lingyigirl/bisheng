"""F078 managed-agent management endpoints (v1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import PageData, resp_200
from bisheng.managed_agent.domain.models.dao import ManagedAgentRunDao
from bisheng.managed_agent.domain.schemas.agent import (
    ManagedAgentCreateReq,
    ManagedAgentItem,
    ManagedAgentUpdateReq,
)
from bisheng.managed_agent.domain.services import classification_service, registry_service

router = APIRouter(prefix="/managed-agents", tags=["ManagedAgent"])


def _language(request: Request) -> str:
    return request.headers.get("accept-language", "zh-Hans")


async def _taxonomy_names(user, request: Request) -> tuple[dict[str, str], dict[str, str]]:
    rows = await classification_service.list_taxonomies(user)
    scenarios: dict[str, str] = {}
    industries: dict[str, str] = {}
    language = _language(request)
    for row in rows:
        target = scenarios if row.kind == "scenario" else industries
        target[row.code] = classification_service.localized_name(row, language)
    return scenarios, industries


def _to_item(agent, scenarios: dict[str, str], industries: dict[str, str]) -> ManagedAgentItem:
    data = registry_service.to_item(
        agent,
        scenario_name=scenarios.get(agent.scenario_code or ""),
        industry_name=industries.get(agent.industry_code or ""),
    )
    return ManagedAgentItem(**data)


@router.get("")
async def list_agents(
    request: Request,
    keyword: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    protocol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    scenario_code: str | None = Query(default=None),
    industry_code: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    rows, total = await registry_service.list_agents(
        user,
        keyword=keyword,
        source_kind=source_kind,
        protocol=protocol,
        status=status,
        scenario_code=scenario_code,
        industry_code=industry_code,
        page=page,
        page_size=page_size,
    )
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(
        PageData[ManagedAgentItem](
            data=[_to_item(row, scenarios, industries) for row in rows],
            total=total,
        )
    )


@router.get("/catalog")
async def list_catalog(
    request: Request,
    keyword: str | None = Query(default=None),
    scenario_code: str | None = Query(default=None),
    industry_code: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    rows, total = await registry_service.list_catalog(
        user,
        keyword=keyword,
        scenario_code=scenario_code,
        industry_code=industry_code,
        page=page,
        page_size=page_size,
    )
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(
        PageData[ManagedAgentItem](
            data=[_to_item(row, scenarios, industries) for row in rows],
            total=total,
        )
    )


@router.get("/{agent_id}")
async def get_agent(
    agent_id: int,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.get_agent(user, agent_id)
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.post("")
async def create_agent(
    req: ManagedAgentCreateReq,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.create_agent(user, req)
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.put("/{agent_id}")
async def update_agent(
    agent_id: int,
    req: ManagedAgentUpdateReq,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.update_agent(user, agent_id, req)
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    await registry_service.delete_agent(user, agent_id)
    return resp_200(None)


@router.post("/{agent_id}/publish")
async def publish_agent(
    agent_id: int,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.transition(user, agent_id, "publish")
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.post("/{agent_id}/offline")
async def offline_agent(
    agent_id: int,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.transition(user, agent_id, "offline")
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.post("/{agent_id}/disable")
async def disable_agent(
    agent_id: int,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    agent = await registry_service.transition(user, agent_id, "disable")
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.post("/{agent_id}/health-check")
async def health_check(
    agent_id: int,
    request: Request,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    # T-19 owns the health implementation; lazy import keeps this endpoint
    # importable while the wave is being built.
    from bisheng.managed_agent.domain.services import health_service

    agent = await health_service.run_health_check(user, agent_id)
    scenarios, industries = await _taxonomy_names(user, request)
    return resp_200(_to_item(agent, scenarios, industries))


@router.get("/{agent_id}/stats")
async def agent_stats(
    agent_id: int,
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    await registry_service.get_agent(user, agent_id)
    return resp_200(
        await ManagedAgentRunDao.astats(
            agent_id=agent_id,
            start_time=start_time,
            end_time=end_time,
        )
    )
