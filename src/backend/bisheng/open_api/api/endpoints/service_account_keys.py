"""F076 T-21:密钥管理 API + 可签发权限位目录(design §4.2 / §6.1)。

**明文只出现一次**:`POST /{account_id}/keys` 的响应里带 `plaintext`,此后任何
接口/界面只回 `key_mask`(前缀 + 末八位填充 + 末四位)。库里只存 SHA-256 摘要
(INV-23 条款 1),这一点在 T-05 已由单测锁死。

**权限**:与 T-20 同款 —— 全局超管或当前租户管理员;跨租户一律 404/11809。

**吊销 ≠ 删除**:吊销只写 `revoked_at`(保留记录供审计),并**立即清缓存**
(跨进程生效,spec AC-11 的 ≤5 秒上界由"清缓存 + TTL"双保险承载)。

路由顺序注意:`/keys/revoke-all` 声明在 `/keys/{key_id}/...` 之前,
避免被路径参数吞掉。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.open_api import (
    OpenApiAccountDisabledError,
    OpenApiNotFoundError,
    OpenApiUnknownScopeError,
)
from bisheng.common.schemas.api import PageData, resp_200
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.open_api.domain.models.api_credential import (
    REVOKE_REASON_BATCH,
    REVOKE_REASON_MANUAL,
)
from bisheng.open_api.domain.models.dao import ApiCredentialDao
from bisheng.open_api.domain.scopes import ISSUABLE_OPEN_API_SCOPE_CODES, OPEN_API_SCOPES
from bisheng.open_api.domain.services import credential_service as cred_service
from bisheng.open_api.domain.services import service_account_service as svc

router = APIRouter(prefix="/service-accounts", tags=["OpenApiManagement"])
#: 可签发权限位目录(挂 /api/v1/open-api/scopes)。
scopes_router = APIRouter(prefix="/open-api", tags=["OpenApiManagement"])


class KeyIssueReq(BaseModel):
    name: str | None = Field(default=None, max_length=128, description="密钥名,便于轮换与对账")
    scopes: list[str] = Field(default_factory=list, description="权限位(取自目录端点)")
    expires_at: datetime | None = Field(default=None, description="有效期;不传 = 不过期")


class KeyUpdateReq(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    scopes: list[str] | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)


def _acting_tenant(login_user: UserPayload) -> int:
    return get_current_tenant_id() or login_user.tenant_id


def _validate_scopes(scopes: list[str]) -> None:
    unknown = [s for s in scopes if s not in ISSUABLE_OPEN_API_SCOPE_CODES]
    if unknown:
        raise OpenApiUnknownScopeError(**{"unknown": unknown})


def _key_status(row, now: datetime) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= now:
        return "expired"
    return "active"


def _serialize_key(row, now: datetime) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "key_mask": row.key_mask,
        "scopes": list(row.scopes or []),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "status": _key_status(row, now),
    }


async def _require_account(account_id: int, tenant_id: int):
    account = await svc.get_service_account(account_id, tenant_id=tenant_id)
    if account is None:
        raise OpenApiNotFoundError()
    return account


@router.get("/{account_id}/keys")
async def list_keys(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """列出该账号的全部密钥(含已吊销;一律只回掩码)。"""
    tenant_id = _acting_tenant(login_user)
    await _require_account(account_id, tenant_id)
    rows = await ApiCredentialDao.alist_by_account(account_id, tenant_id=tenant_id)
    now = datetime.now()
    return resp_200(data=PageData(data=[_serialize_key(r, now) for r in rows], total=len(rows)))


@router.post("/{account_id}/keys")
async def issue_key(
    account_id: int,
    req: KeyIssueReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """签发密钥——**明文仅在本响应出现一次**,调用方须当场保存。"""
    tenant_id = _acting_tenant(login_user)
    account = await _require_account(account_id, tenant_id)
    if not account.is_enabled:
        raise OpenApiAccountDisabledError()
    _validate_scopes(req.scopes)

    row, plaintext = await cred_service.issue_credential(
        tenant_id=tenant_id,
        service_account_id=account_id,
        name=req.name,
        scopes=req.scopes,
        expires_at=req.expires_at,
        created_by=login_user.user_id,
    )
    logger.info(
        "open_api: key issued account={} credential={} by user={} (plaintext shown once)",
        account_id,
        row.id,
        login_user.user_id,
    )
    return resp_200(
        data={
            "id": row.id,
            "key_mask": row.key_mask,
            "plaintext": plaintext,
            "scopes": list(row.scopes or []),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "create_time": row.create_time.isoformat() if row.create_time else None,
        }
    )


@router.patch("/{account_id}/keys/{key_id}")
async def update_key(
    account_id: int,
    key_id: int,
    req: KeyUpdateReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """改名称 / 权限位 / 有效期 —— 立即生效,无需重新签发(spec AC-13)。"""
    tenant_id = _acting_tenant(login_user)
    await _require_account(account_id, tenant_id)

    fields = req.model_dump(exclude_unset=True)
    if "scopes" in fields and fields["scopes"] is not None:
        _validate_scopes(fields["scopes"])

    row = await ApiCredentialDao.aget_by_id(key_id, tenant_id=tenant_id)
    if row is None or row.service_account_id != account_id:
        raise OpenApiNotFoundError()

    if not fields:
        return resp_200(data=_serialize_key(row, datetime.now()))

    changed = await cred_service.update_credential(key_id, fields, tenant_id=tenant_id)
    if not changed:
        raise OpenApiNotFoundError()
    refreshed = await ApiCredentialDao.aget_by_id(key_id, tenant_id=tenant_id)
    return resp_200(data=_serialize_key(refreshed, datetime.now()))


@router.post("/{account_id}/keys/revoke-all")
async def revoke_all_keys(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """全量吊销(轮换收尾 / 应急),立即清缓存。"""
    tenant_id = _acting_tenant(login_user)
    await _require_account(account_id, tenant_id)
    revoked = await cred_service.revoke_account_credentials(account_id, tenant_id=tenant_id, reason=REVOKE_REASON_BATCH)
    logger.info("open_api: revoked {} key(s) of account {} by user {}", revoked, account_id, login_user.user_id)
    return resp_200(data={"account_id": account_id, "revoked": revoked})


@router.post("/{account_id}/keys/{key_id}/revoke")
async def revoke_key(
    account_id: int,
    key_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """吊销单把(吊销 ≠ 删除,记录保留供审计)。"""
    tenant_id = _acting_tenant(login_user)
    await _require_account(account_id, tenant_id)
    row = await ApiCredentialDao.aget_by_id(key_id, tenant_id=tenant_id)
    if row is None or row.service_account_id != account_id:
        raise OpenApiNotFoundError()

    changed = await cred_service.revoke_credential(key_id, tenant_id=tenant_id, reason=REVOKE_REASON_MANUAL)
    if not changed:
        raise OpenApiNotFoundError()
    logger.info("open_api: key {} of account {} revoked by user {}", key_id, account_id, login_user.user_id)
    return resp_200(data={"id": key_id, "revoked": True})


@scopes_router.get("/scopes")
async def list_scopes(login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user)):
    """可签发权限位目录(对接方与管理端共用同一份注册表)。"""
    return resp_200(
        data={
            "data": [
                {"code": s.code, "group": s.group, "endpoint_count": len(s.endpoints)}
                for s in OPEN_API_SCOPES
                if s.code in ISSUABLE_OPEN_API_SCOPE_CODES
            ],
            "total": len(ISSUABLE_OPEN_API_SCOPE_CODES),
        }
    )
