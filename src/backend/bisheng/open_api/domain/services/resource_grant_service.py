"""F076 T-22:服务账号的资源授权代管(design §2.4 决策 1 / spec AC-06a)。

**代管的语义**:服务账号的可见范围 = 其专用身份(owner 用户)在平台 ReBAC 体系里
的授权。管理员在服务账号详情页"代管"这些授权——底层**就是**
`PermissionService.authorize()`(C4 单入口,不新增授权表、不改授权模型),
主体恒为那个 `svc-*` 用户,前端不需要知道 subject 的存在。

**两道闸**:
1. 资源类型/档位必须在白名单内(11820);
2. 执行授权的管理员**必须自己对目标资源有 manager 权限**(11808)——防止
   "借服务账号绕过自己的管理边界"。

`owner` 档位不在可授予清单里:它是"资源创建者"关系,属于资源归属本身,
不应该通过服务账号代管被改写。
"""

from __future__ import annotations

from typing import Any

from bisheng.common.errcode.open_api import (
    OpenApiNotFoundError,
    OpenApiResourceNotGrantedError,
    OpenApiResourceTypeUnsupportedError,
)
from bisheng.permission.domain.schemas.permission_schema import AuthorizeGrantItem, AuthorizeRevokeItem
from bisheng.permission.domain.services.permission_service import PermissionService

#: 代管开放的四类资源(与上游服务账号授权面一致;顺序即前端展示顺序)。
GRANTABLE_RESOURCE_TYPES: tuple[str, ...] = (
    "knowledge_library",
    "knowledge_space",
    "assistant",
    "workflow",
)
#: 可授予的三档权限。`owner` 刻意不含(见模块 docstring)。
GRANT_LEVELS: tuple[str, ...] = ("viewer", "editor", "manager")


def _validate(resource_type: str, level: str | None = None) -> None:
    if resource_type not in GRANTABLE_RESOURCE_TYPES:
        raise OpenApiResourceTypeUnsupportedError(**{"resource_type": resource_type})
    if level is not None and level not in GRANT_LEVELS:
        raise OpenApiResourceTypeUnsupportedError(**{"resource_type": resource_type, "level": level})


async def list_grants(owner_user_id: int, *, login_user: Any = None) -> list[dict]:
    """列出该身份在各资源类型上可见的资源与档位(管理面展示用)。

    实现 = `list_accessible_ids`(关系 viewer 覆盖全部档位) + 逐个取
    `get_permission_level`;服务账号的资源面通常很小,不做缓存。
    """
    rows: list[dict] = []
    for resource_type in GRANTABLE_RESOURCE_TYPES:
        ids = await PermissionService.list_accessible_ids(owner_user_id, "viewer", resource_type, login_user=login_user)
        if not ids:
            continue
        for resource_id in ids:
            level = await PermissionService.get_permission_level(
                owner_user_id, resource_type, str(resource_id), login_user=login_user
            )
            rows.append({"resource_type": resource_type, "resource_id": str(resource_id), "level": level})
    return rows


async def mutate_grants(
    *,
    owner_user_id: int,
    admin_user_id: int,
    grants: list[dict] | None = None,
    revokes: list[dict] | None = None,
    login_user: Any = None,
) -> dict:
    """代管授权变更:先验"管理员对目标资源有 manager",再以 owner 为主体写 tuple。

    返回 `{'granted': n, 'revoked': n}`;任一条目不合法/越界即整体失败(先检查
    后执行,不会出现"改了一半"的中间态)。
    """
    grant_items: list[AuthorizeGrantItem] = []
    revoke_items: list[AuthorizeRevokeItem] = []

    for item in grants or []:
        _validate(item["resource_type"], item["level"])
        await _assert_admin_can_manage(admin_user_id, item["resource_type"], str(item["resource_id"]), login_user)
        grant_items.append(AuthorizeGrantItem(subject_type="user", subject_id=owner_user_id, relation=item["level"]))

    for item in revokes or []:
        _validate(item["resource_type"])
        resource_id = str(item["resource_id"])
        current = await PermissionService.get_permission_level(
            owner_user_id, item["resource_type"], resource_id, login_user=login_user
        )
        if current is None or current not in GRANT_LEVELS:
            # 撤一个不存在的授权 = 目标不存在(也可能它从未被代管授予过,如 owner 档)
            raise OpenApiNotFoundError()
        await _assert_admin_can_manage(admin_user_id, item["resource_type"], resource_id, login_user)
        revoke_items.append(AuthorizeRevokeItem(subject_type="user", subject_id=owner_user_id, relation=current))

    # 按资源聚合后调用(同一资源的 grants/revokes 分开写,避免互相覆盖)
    for item, prepared in zip(grants or [], grant_items):  # noqa: B905 — 列表等长,按序搭配
        await PermissionService.authorize(
            object_type=item["resource_type"],
            object_id=str(item["resource_id"]),
            grants=[prepared],
        )
    for item, prepared in zip(revokes or [], revoke_items):  # noqa: B905
        await PermissionService.authorize(
            object_type=item["resource_type"],
            object_id=str(item["resource_id"]),
            revokes=[prepared],
        )

    return {"granted": len(grant_items), "revoked": len(revoke_items)}


async def _assert_admin_can_manage(admin_user_id: int, resource_type: str, resource_id: str, login_user: Any) -> None:
    """执行代管的管理员必须自己对目标资源有 manager 权限(越界即 11808)。"""
    allowed = await PermissionService.check(admin_user_id, "manager", resource_type, resource_id, login_user=login_user)
    if not allowed:
        raise OpenApiResourceNotGrantedError(**{"resource_type": resource_type, "resource_id": resource_id})
