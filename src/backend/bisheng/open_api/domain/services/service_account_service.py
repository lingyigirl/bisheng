"""F076 服务账号 domain 服务(T-17,spec AC-01~AC-06a)。

三条硬约束:

1. **创建是一个事务** —— `User`(专用身份) + `UserTenant(is_active=1)`(活跃叶子)
   + `ServiceAccount` 三行在同一 session 内一次提交,任何一步失败整体回滚。
   身份与租户成员必须与账号同生共死:只有账号行、没有活跃成员的话,T-06 的
   活性校验会把每次调用判成 11805,账号等于废号。
2. **专用身份零特权** —— 不建任何 UserRole / UserGroup;`external_id` 留空
   (登录路径按 external_id 查询,空值使该账号无法被密码登录命中);
   `password` 写随机串,无人知晓;不参与任何默认角色/分组。
3. **防提权双保险** —— 创建 / 变更 owner 时调用 `assert_owner_assignable`
   (11810);运行期由鉴权依赖的 `owner_is_privileged` 兜底(11816)。

已知边界(design 表 1 的登记口径,记录在 tasks.md 偏差):删除账号时不主动清理
owner 的 FGA 元组 —— 平台没有"按主体批量撤 tuple"的现成口;授权随用户停用失效
(11805 阻断一切调用),主动清理由后续治理承接。
"""

from __future__ import annotations

import secrets
from datetime import datetime

from loguru import logger
from sqlmodel import select

from bisheng.core.context.tenant import bypass_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.database.models.tenant import UserTenant
from bisheng.open_api.domain.models.api_credential import (
    REVOKE_REASON_SUBJECT_DELETED,
    REVOKE_REASON_SUBJECT_DISABLED,
)
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.open_api.domain.models.service_account import ServiceAccount
from bisheng.open_api.domain.services.credential_service import revoke_account_credentials
from bisheng.open_api.domain.services.identity_service import assert_owner_assignable
from bisheng.user.domain.models.user import User

#: 专用身份的命名前缀;重名时追加 -2 / -3 …(名字要能在用户列表里一眼认出来)。
OWNER_NAME_PREFIX = "svc-"
#: `service_account` 名称上限的镜像:截断后仍要为后缀留出空间。
_MAX_OWNER_NAME_BASE = 100

#: 管理面允许被编辑的账号字段白名单。
EDITABLE_FIELDS = frozenset({"name", "description", "resource_owner_user_id"})


def _owner_name_base(account_name: str) -> str:
    """`svc-<账号名>`;过长时截断,保证追加后缀后仍在列宽内。"""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in account_name.strip())
    cleaned = cleaned or "account"
    return f"{OWNER_NAME_PREFIX}{cleaned}"[:_MAX_OWNER_NAME_BASE]


async def _create_dedicated_owner(session, account_name: str, tenant_id: int) -> User:
    """在同一 session 内建专用身份 + 活跃租户成员(S-1/S-2)。

    仅 `flush` 不 `commit` —— 提交由调用方(create_service_account)与账号行一起做。
    """
    base = _owner_name_base(account_name)
    name = base
    suffix = 2
    while True:
        existing = (await session.execute(select(User).where(User.user_name == name))).scalars().first()
        if existing is None:
            break
        name = f"{base}-{suffix}"
        suffix += 1

    owner = User(
        user_name=name,
        password=secrets.token_urlsafe(32),  # 无人知晓的随机口令;登录另有 external_id 空值兜底
        source="local",
        external_id=None,
        delete=0,
    )
    session.add(owner)
    await session.flush()  # 取 owner.user_id

    session.add(
        UserTenant(
            user_id=owner.user_id,
            tenant_id=tenant_id,
            is_active=1,
            is_default=1,
            status="active",
        )
    )
    await session.flush()
    return owner


async def create_service_account(
    *,
    tenant_id: int,
    name: str,
    description: str | None = None,
    created_by: int | None = None,
    resource_owner_user_id: int | None = None,
) -> ServiceAccount:
    """创建一个服务账号,并为其准备专用资源所有者(spec AC-01 / AC-02)。

    默认自动创建专用身份;显式传 `resource_owner_user_id` 时(运维接管/测试)
    先经 `assert_owner_assignable` 校验(11810),不再新建用户。
    """
    async with get_async_db_session() as session:
        if resource_owner_user_id is None:
            owner = await _create_dedicated_owner(session, name, tenant_id)
            owner_id = owner.user_id
        else:
            owner_id = resource_owner_user_id

        if resource_owner_user_id is not None:
            # 显式指定 owner 的路径要过防提权校验;自动创建的是刚生成的普通用户,
            # 无需回查(它连角色都没有)。
            await assert_owner_assignable(owner_id, tenant_id)

        account = ServiceAccount(
            tenant_id=tenant_id,
            name=name,
            description=description,
            resource_owner_user_id=owner_id,
            created_by=created_by,
        )
        session.add(account)
        # 单次提交:身份 / 租户成员 / 账号三行要么都在,要么都不在。
        await session.commit()
        await session.refresh(account)
        return account


async def set_enabled(account_id: int, *, enabled: bool, tenant_id: int) -> bool:
    """启停账号;停用即吊销其全部密钥(spec AC-05)。"""
    account = await ServiceAccountDao.aget_scoped(account_id, tenant_id=tenant_id)
    if account is None:
        return False

    changed = await ServiceAccountDao.aupdate_fields(
        account_id,
        {"disabled_at": None if enabled else datetime.now()},
        tenant_id=tenant_id,
    )
    if changed and not enabled:
        revoked = await revoke_account_credentials(
            account_id, tenant_id=tenant_id, reason=REVOKE_REASON_SUBJECT_DISABLED
        )
        logger.info("open_api: service account {} disabled, {} key(s) revoked", account_id, revoked)
    return bool(changed)


async def update_service_account(account_id: int, fields: dict, *, tenant_id: int) -> bool:
    """改名 / 改描述 / 换 owner;换 owner 走第一重防提权(spec AC-03)。"""
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"open_api: non-editable service account fields: {sorted(unknown)}")

    new_owner = fields.get("resource_owner_user_id")
    if new_owner is not None:
        await assert_owner_assignable(new_owner, tenant_id)

    return bool(await ServiceAccountDao.aupdate_fields(account_id, dict(fields), tenant_id=tenant_id))


async def delete_service_account(account_id: int, *, tenant_id: int) -> bool:
    """软删账号:吊销密钥 → 停用专用身份 → 软删账号行(spec AC-06)。

    顺序有意如此:先断凭据,再断身份,最后隐藏账号 —— 任一步失败时,
    已经失效的部分不会让账号"看起来还能用"。
    """
    account = await ServiceAccountDao.aget_scoped(account_id, tenant_id=tenant_id)
    if account is None:
        return False

    revoked = await revoke_account_credentials(account_id, tenant_id=tenant_id, reason=REVOKE_REASON_SUBJECT_DELETED)

    # 停用专用身份(User.delete=1):阻止它继续作为任何身份的载体,
    # 也让 T-06 的活性校验(读 delete 字段)立即生效。
    with bypass_tenant_filter():
        async with get_async_db_session() as session:
            owner = (
                (await session.execute(select(User).where(User.user_id == account.resource_owner_user_id)))
                .scalars()
                .first()
            )
            if owner is not None:
                owner.delete = 1
                session.add(owner)
                # F076:账号删除意味着专用身份不再承担任何职责,租户链路上的活跃
                # 成员位一并撤掉,避免它出现在成员选择器里。
                membership = (
                    (
                        await session.execute(
                            select(UserTenant).where(
                                UserTenant.user_id == owner.user_id,
                                UserTenant.tenant_id == tenant_id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if membership is not None:
                    membership.is_active = None
                    membership.status = "disabled"
                    session.add(membership)
            await session.commit()

    changed = await ServiceAccountDao.asoft_delete(account_id, tenant_id=tenant_id)
    logger.info("open_api: service account {} deleted, {} key(s) revoked", account_id, revoked)
    return bool(changed)
