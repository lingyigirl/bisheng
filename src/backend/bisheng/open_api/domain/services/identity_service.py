"""F076 服务账号资源所有者的装载与防提权(T-06,spec AC-04 / AC-19 / INV-23)。

owner 是平台在创建服务账号时自动创建的**专用最小权限用户**(T-17 落地创建)。
本模块负责运行期两件事:

1. `load_owner_payload` —— 把 owner 装载成 `UserPayload`(与
   `open_endpoints/domain/utils.py` 的默认操作员装载同款:先取用户,再确认
   **活跃租户 == 服务账号所属租户**,最后 `init_login_user` 预解析超管标记)。
2. `assert_owner_not_privileged` —— 运行时防提权第二重:owner 一旦被授予
   全局超管或租户管理员,该账号的调用一律拒绝(11816)。创建/变更时的第一重
   校验在 T-17(11810)。

读取姿态:owner 的 user_id 来自**已校验的凭据 → 账号行**,是权威值;而此时
租户上下文尚未建立(或仍被中间件钉在 ROOT,design §5 坑 1),因此读用户必须
`bypass_tenant_filter()` + 显式主键条件,否则会因过滤读不到人。
"""

from __future__ import annotations

from loguru import logger

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.core.context.tenant import bypass_tenant_filter
from bisheng.database.models.tenant import UserTenantDao
from bisheng.user.domain.models.user import UserDao

#: 用户状态:1 = active(与 open_endpoints/domain/utils.py 的活跃租户判定同口径)。
_ACTIVE_STATUS = 1


async def load_owner_payload(*, owner_user_id: int, tenant_id: int) -> UserPayload | None:
    """装载服务账号的资源所有者;不存在 / 非活跃 / 不属于该租户 → None。

    返回 None 由调用方转 11805(账号不可用)——这几种情况对调用方是同一件事:
    "这个服务账号现在不能用"。
    """
    with bypass_tenant_filter():
        user = await UserDao.aget_user(owner_user_id)
        # 停用判据是 User.delete(0=正常,1=停用)——T-06 首版误用不存在的
        # `status` 字段,getattr 兜底让检查静默失效;T-17 集成时修正。
        if user is None or int(getattr(user, "delete", 0) or 0) != 0:
            logger.warning("open_api: service-account owner {} missing or disabled", owner_user_id)
            return None
        active = await UserTenantDao.aget_active_user_tenant(owner_user_id)
        if active is not None and active.status != "active":
            logger.warning("open_api: service-account owner {} tenant membership disabled", owner_user_id)
            return None

    if active is None or active.tenant_id != tenant_id:
        # owner 的活跃租户与密钥所属租户不一致 = 归属漂移,按账号不可用处理。
        logger.warning(
            "open_api: service-account owner {} active tenant mismatch (expected {}), refusing",
            owner_user_id,
            tenant_id,
        )
        return None

    return await UserPayload.init_login_user(
        user_id=user.user_id,
        user_name=user.user_name,
        tenant_id=tenant_id,
    )


async def assert_owner_assignable(owner_user_id: int, tenant_id: int) -> None:
    """创建/变更服务账号时校验候选 owner 可被指派(第一重防提权,spec AC-03)。

    - 用户不存在 / 已停用 → 11810(调用方给了一个不可用的主体);
    - 已是全局超管或该租户租户管理员 → 11810;
    - 该用户不是该租户的活跃成员 → 11810。

    第二重(运行期)由 `owner_is_privileged` 在鉴权依赖里执行(11816)。
    """
    from bisheng.common.errcode.open_api import OpenApiOwnerNotAllowedError

    with bypass_tenant_filter():
        user = await UserDao.aget_user(owner_user_id)
        if user is None or int(getattr(user, "delete", 0) or 0) != 0:
            raise OpenApiOwnerNotAllowedError()
        active = await UserTenantDao.aget_active_user_tenant(owner_user_id)

    if active is None or active.tenant_id != tenant_id or active.status != "active":
        raise OpenApiOwnerNotAllowedError()

    payload = await UserPayload.init_login_user(
        user_id=user.user_id,
        user_name=user.user_name,
        tenant_id=tenant_id,
    )
    if await owner_is_privileged(payload, tenant_id):
        raise OpenApiOwnerNotAllowedError()


async def owner_is_privileged(payload: UserPayload, tenant_id: int) -> bool:
    """owner 是否已具备管理员特权(全局超管,或该租户的租户管理员)。"""
    if payload.is_global_super:
        return True
    return await payload.has_tenant_admin(tenant_id)
