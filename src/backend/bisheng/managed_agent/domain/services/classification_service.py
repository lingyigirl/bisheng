"""F078 scenario/industry taxonomy lifecycle.

Reads are open to any logged-in user (the client catalog needs them); writes are
limited to the global super admin or the current tenant admin. A code still
referenced by a managed agent cannot be deleted (12109) — disabling it is the
supported way to stop offering it while keeping existing bindings.
"""

from __future__ import annotations

import re

from loguru import logger

from bisheng.common.errcode.managed_agent import (
    ManagedAgentDuplicateError,
    ManagedAgentNotFoundError,
    ManagedAgentTaxonomyInvalidError,
)
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.managed_agent.domain.models.dao import ManagedAgentDao, ManagedAgentTaxonomyDao
from bisheng.managed_agent.domain.models.taxonomy import ManagedAgentTaxonomy, TaxonomyKind
from bisheng.managed_agent.domain.schemas.agent import TaxonomyCreateReq, TaxonomyUpdateReq

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def _is_admin(user) -> bool:
    if getattr(user, "is_admin", lambda: False)():
        return True
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return False
    checker = getattr(user, "has_tenant_admin", None)
    if checker is None:
        return False
    try:
        return bool(await checker(tenant_id))
    except Exception:
        logger.warning("managed_agent: tenant-admin check failed for taxonomy write")
        return False


async def _assert_admin(user) -> None:
    if not await _is_admin(user):
        raise ManagedAgentNotFoundError(msg="only tenant administrators may maintain taxonomies")


def _validate(kind: str, code: str, name_zh: str) -> None:
    if kind not in {item.value for item in TaxonomyKind}:
        raise ManagedAgentTaxonomyInvalidError(msg="kind must be scenario or industry")
    if not _CODE_RE.match(code):
        raise ManagedAgentTaxonomyInvalidError(msg="code must be 1-64 letters/digits/_/-")
    if not name_zh:
        raise ManagedAgentTaxonomyInvalidError(msg="name_zh is required")


async def list_taxonomies(
    user,
    *,
    kind: str | None = None,
    enabled_only: bool = False,
) -> list[ManagedAgentTaxonomy]:
    if kind is not None and kind not in {item.value for item in TaxonomyKind}:
        raise ManagedAgentTaxonomyInvalidError(msg="kind must be scenario or industry")
    return await ManagedAgentTaxonomyDao.alist(kind=kind, enabled_only=enabled_only)


async def create_taxonomy(user, req: TaxonomyCreateReq) -> ManagedAgentTaxonomy:
    await _assert_admin(user)
    _validate(req.kind, req.code, req.name_zh)
    if await ManagedAgentTaxonomyDao.aget_by_code(req.kind, req.code):
        raise ManagedAgentDuplicateError(msg="taxonomy code already exists")
    row = ManagedAgentTaxonomy(
        kind=req.kind,
        code=req.code,
        name_zh=req.name_zh,
        name_en=req.name_en,
        name_ja=req.name_ja,
        sort_order=req.sort_order,
        is_enabled=req.is_enabled,
    )
    return await ManagedAgentTaxonomyDao.acreate(row)


async def update_taxonomy(user, row_id: int, req: TaxonomyUpdateReq) -> ManagedAgentTaxonomy:
    await _assert_admin(user)
    current = await ManagedAgentTaxonomyDao.aget(row_id)
    if current is None:
        raise ManagedAgentNotFoundError()
    code = req.code or current.code
    name_zh = req.name_zh or current.name_zh
    _validate(current.kind, code, name_zh)
    if code != current.code:
        if await ManagedAgentTaxonomyDao.aget_by_code(current.kind, code):
            raise ManagedAgentDuplicateError(msg="taxonomy code already exists")
    fields = {
        "code": code,
        "name_zh": name_zh,
        "name_en": req.name_en if req.name_en is not None else current.name_en,
        "name_ja": req.name_ja if req.name_ja is not None else current.name_ja,
        "sort_order": req.sort_order if req.sort_order is not None else current.sort_order,
        "is_enabled": req.is_enabled if req.is_enabled is not None else current.is_enabled,
    }
    if await ManagedAgentTaxonomyDao.aupdate_fields(row_id, fields, tenant_id=current.tenant_id) != 1:
        raise ManagedAgentNotFoundError()
    updated = await ManagedAgentTaxonomyDao.aget(row_id)
    if updated is None:
        raise ManagedAgentNotFoundError()
    return updated


async def delete_taxonomy(user, row_id: int) -> None:
    await _assert_admin(user)
    current = await ManagedAgentTaxonomyDao.aget(row_id)
    if current is None:
        raise ManagedAgentNotFoundError()
    if await ManagedAgentDao.acount_by_taxonomy(current.kind, current.code):
        raise ManagedAgentTaxonomyInvalidError(msg="taxonomy code is still referenced; disable it instead")
    if await ManagedAgentTaxonomyDao.adelete(row_id, tenant_id=current.tenant_id) != 1:
        raise ManagedAgentNotFoundError()


def localized_name(row: ManagedAgentTaxonomy, language: str | None) -> str:
    language = (language or "zh-Hans").lower()
    if language.startswith("en") and row.name_en:
        return row.name_en
    if language.startswith("ja") and row.name_ja:
        return row.name_ja
    return row.name_zh or row.code
