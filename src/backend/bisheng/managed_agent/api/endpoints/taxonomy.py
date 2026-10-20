"""F078 taxonomy endpoints (v1 management face)."""

from fastapi import APIRouter, Depends, Query

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.managed_agent.domain.schemas.agent import (
    TaxonomyCreateReq,
    TaxonomyItem,
    TaxonomyUpdateReq,
)
from bisheng.managed_agent.domain.services import classification_service

router = APIRouter(prefix="/managed-agent-taxonomies", tags=["ManagedAgent"])


def _to_item(row) -> dict:
    return TaxonomyItem(
        id=row.id,
        kind=row.kind,
        code=row.code,
        name_zh=row.name_zh,
        name_en=row.name_en,
        name_ja=row.name_ja,
        sort_order=row.sort_order,
        is_enabled=row.is_enabled,
    ).model_dump()


@router.get("")
async def list_taxonomies(
    kind: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    rows = await classification_service.list_taxonomies(user, kind=kind, enabled_only=enabled_only)
    return resp_200([_to_item(row) for row in rows])


@router.post("")
async def create_taxonomy(
    req: TaxonomyCreateReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    row = await classification_service.create_taxonomy(user, req)
    return resp_200(_to_item(row))


@router.put("/{row_id}")
async def update_taxonomy(
    row_id: int,
    req: TaxonomyUpdateReq,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    row = await classification_service.update_taxonomy(user, row_id, req)
    return resp_200(_to_item(row))


@router.delete("/{row_id}")
async def delete_taxonomy(
    row_id: int,
    user: UserPayload = Depends(UserPayload.get_login_user),
):
    await classification_service.delete_taxonomy(user, row_id)
    return resp_200(None)
