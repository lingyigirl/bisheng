"""F076 T-20:服务账号管理 API(design §4.2)。

**权限**:`get_tenant_admin_user` —— 全局超管或当前租户管理员;其余一律 403
(平台既有口径,spec AC-41)。**作用租户**取 `get_current_tenant_id()`(超管切
子租户时以切换后的为准),回落登录态租户 —— 与 `get_tenant_admin_user` 的
admin-scope 口径一致。

**跨租户一律 404**:所有读写都带 `tenant_id` 收窄,查不到就按"不存在或不可访问"
(11809 → 真 HTTP 404)拒绝,不泄露"存在但不属于你"。

**形状**(design §4.2):
`{id, name, description, resource_owner_user_id, enabled, key_count, last_used_at}`
`key_count` = 未吊销密钥数;`last_used_at` = 该账号下密钥的最近使用时间。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.common.schemas.api import PageData, resp_200
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.open_api.domain.models.dao import ApiCredentialDao
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services import service_account_service as svc

router = APIRouter(prefix="/service-accounts", tags=["OpenApiManagement"])


class ServiceAccountCreateReq(BaseModel):
    name: str = Field(..., max_length=128, description="对接系统名称(租户内唯一)")
    description: str | None = Field(default=None, max_length=512)


class ServiceAccountUpdateReq(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    resource_owner_user_id: int | None = Field(default=None, description="更换资源所有者(须为非管理员的同租户活跃用户)")


def _acting_tenant(login_user: UserPayload) -> int:
    """本请求的作用租户(admin-scope 优先,回落登录态租户)。"""
    return get_current_tenant_id() or login_user.tenant_id


def _serialize(account: ServiceAccount, stats: dict | None = None) -> dict:
    stats = stats or {}
    last_used = stats.get("last_used_at")
    return {
        "id": account.id,
        "name": account.name,
        "description": account.description,
        "resource_owner_user_id": account.resource_owner_user_id,
        "enabled": account.is_enabled,
        "key_count": stats.get("key_count", 0),
        "last_used_at": last_used.isoformat() if isinstance(last_used, datetime) else None,
    }


@router.get("")
async def list_service_accounts(
    keyword: str | None = Query(default=None, description="按名称模糊搜索"),
    page: int = Query(default=1, gt=0),
    page_size: int = Query(default=20, gt=0, le=200),
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """分页列出本租户的服务账号(软删不可见),含密钥数与最近使用。"""
    tenant_id = _acting_tenant(login_user)
    accounts, total = await svc.list_service_accounts(
        tenant_id=tenant_id, keyword=keyword, page=page, page_size=page_size
    )
    stats = await ApiCredentialDao.astats_by_accounts([a.id for a in accounts], tenant_id=tenant_id)
    return resp_200(data=PageData(data=[_serialize(a, stats.get(a.id)) for a in accounts], total=total))


@router.post("")
async def create_service_account(
    req: ServiceAccountCreateReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """创建服务账号(同事务自动生成专用资源所有者,spec AC-02)。"""
    tenant_id = _acting_tenant(login_user)
    account = await svc.create_service_account(
        tenant_id=tenant_id,
        name=req.name,
        description=req.description,
        created_by=login_user.user_id,
    )
    logger.info("open_api: service account {} created by user {}", account.id, login_user.user_id)
    return resp_200(data=_serialize(account))


@router.get("/{account_id}")
async def get_service_account(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    tenant_id = _acting_tenant(login_user)
    account = await svc.get_service_account(account_id, tenant_id=tenant_id)
    if account is None:
        raise OpenApiNotFoundError()
    stats = await ApiCredentialDao.astats_by_accounts([account.id], tenant_id=tenant_id)
    return resp_200(data=_serialize(account, stats.get(account.id)))


@router.patch("/{account_id}")
async def update_service_account(
    account_id: int,
    req: ServiceAccountUpdateReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """改名 / 改描述 / 换 owner(换 owner 过防提权 11810;改名查重 11813)。"""
    tenant_id = _acting_tenant(login_user)
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        account = await svc.get_service_account(account_id, tenant_id=tenant_id)
        if account is None:
            raise OpenApiNotFoundError()
        return resp_200(data=_serialize(account))

    changed = await svc.update_service_account(account_id, fields, tenant_id=tenant_id)
    if not changed:
        raise OpenApiNotFoundError()
    account = await svc.get_service_account(account_id, tenant_id=tenant_id)
    return resp_200(data=_serialize(account))


@router.post("/{account_id}/enable")
async def enable_service_account(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    return await _set_enabled(account_id, enabled=True, login_user=login_user)


@router.post("/{account_id}/disable")
async def disable_service_account(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """停用即吊销该账号全部密钥(spec AC-05)。"""
    return await _set_enabled(account_id, enabled=False, login_user=login_user)


async def _set_enabled(account_id: int, *, enabled: bool, login_user: UserPayload):
    tenant_id = _acting_tenant(login_user)
    changed = await svc.set_enabled(account_id, enabled=enabled, tenant_id=tenant_id)
    if not changed:
        raise OpenApiNotFoundError()
    account = await svc.get_service_account(account_id, tenant_id=tenant_id)
    return resp_200(data=_serialize(account))


@router.delete("/{account_id}")
async def delete_service_account(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """软删账号:吊销密钥 → 停用专用身份 → 软删账号行(spec AC-06)。"""
    tenant_id = _acting_tenant(login_user)
    changed = await svc.delete_service_account(account_id, tenant_id=tenant_id)
    if not changed:
        raise OpenApiNotFoundError()
    logger.info("open_api: service account {} deleted by user {}", account_id, login_user.user_id)
    return resp_200(data={"id": account_id, "deleted": True})
