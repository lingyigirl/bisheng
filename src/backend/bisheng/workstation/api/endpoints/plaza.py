"""F047 agent plaza endpoints — favorites (list/stats endpoints arrive in Wave 2).

Kept in its own router instead of extending apps.py so the shared landing
endpoints (recommended / frequently_used / used / pin) have zero regression
surface and tests can mount this router standalone.
"""
from fastapi import APIRouter, Body, Query

from bisheng.api.v1.schemas import resp_200
from bisheng.workstation.domain.schemas.plaza import FavoriteBody
from bisheng.workstation.domain.services.plaza_service import PlazaService

from ..dependencies import LoginUserDep

router = APIRouter()


@router.post('/app/favorite')
def add_favorite(login_user=LoginUserDep, data: FavoriteBody = Body(...)):
    """Add an app to the user's favorites (idempotent)."""
    PlazaService.add_favorite(login_user, data.app_id, data.app_type)
    return resp_200()


@router.delete('/app/favorite')
def remove_favorite(login_user=LoginUserDep, data: FavoriteBody = Body(...)):
    """Remove an app from the user's favorites (idempotent)."""
    PlazaService.remove_favorite(login_user, data.app_id, data.app_type)
    return resp_200()


@router.get('/app/favorites')
async def get_favorites(
    login_user=LoginUserDep,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """The user's favorites, newest-first, only online + visible apps."""
    result = await PlazaService.list_favorites(login_user, page, limit)
    return resp_200(data=result)


@router.get('/app/plaza')
async def get_plaza(
    login_user=LoginUserDep,
    keyword: str | None = Query(default=None),
    flow_types: list[int] = Query(default=None),
    tag_ids: list[int] = Query(default=None),
    sort_by: str = Query(default='recent', description='recent / update_time / visit_count'),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Agent plaza list: online + view-app-visible apps with per-app stats."""
    result = await PlazaService.list_plaza(
        login_user,
        keyword=keyword,
        flow_types=flow_types,
        tag_ids=tag_ids,
        sort_by=sort_by,
        page=page,
        limit=limit,
    )
    return resp_200(data=result)


@router.get('/app/stats/{app_id}')
async def get_app_stats(
    app_id: str,
    login_user=LoginUserDep,
    days: int = Query(default=7, ge=1, le=90),
):
    """Single-app access stats for the preview drawer."""
    result = await PlazaService.app_stats(login_user, app_id, days=days)
    return resp_200(data=result)
