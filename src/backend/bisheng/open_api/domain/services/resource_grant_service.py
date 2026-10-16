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

from loguru import logger

from bisheng.common.errcode.open_api import (
    OpenApiNotFoundError,
    OpenApiResourceGrantsUnavailableError,
    OpenApiResourceNotGrantedError,
    OpenApiResourceTypeUnsupportedError,
)
from bisheng.core.openfga.exceptions import FGAConnectionError
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

#: 权限引擎档位(``PermissionLevel``) -> 代管词表。写入 FGA 的关系就是
#: viewer/editor/manager,而 ``get_permission_level`` 读回的是引擎词表
#: can_read/can_edit/can_manage(模型里两者同构:给 editor 元组,check(editor)
#: 与 check(can_edit) 同为真)。这里做单向归一,避免两个词表在接口上打架。
_LEVEL_TO_RELATION: dict[str, str] = {
    "can_read": "viewer",
    "can_edit": "editor",
    "can_manage": "manager",
}


def _normalize_relation(level: str | None) -> str | None:
    """把引擎档位归一成代管词表;owner / None -> None(= 不经代管管理)。"""
    if level is None:
        return None
    if level in GRANT_LEVELS:
        return level
    return _LEVEL_TO_RELATION.get(level)


async def _direct_relations(owner_user_id: int, resource_type: str, resource_id: str) -> set[str]:
    """读该身份在目标资源上的直接档位元组;FGA 不可达 -> 11814/503。

    读不到与"没有授权"是两件事:代管列表会把前者误标成继承、撤销会误报 404,
    所以故障必须显式暴露,而不是降级成空集。
    """
    try:
        return await PermissionService.list_direct_relations(owner_user_id, resource_type, resource_id)
    except FGAConnectionError as exc:
        logger.error("open_api: FGA unreachable while reading direct relations: {}", exc)
        raise OpenApiResourceGrantsUnavailableError() from exc


def _validate(resource_type: str, level: str | None = None) -> None:
    if resource_type not in GRANTABLE_RESOURCE_TYPES:
        raise OpenApiResourceTypeUnsupportedError(**{"resource_type": resource_type})
    if level is not None and level not in GRANT_LEVELS:
        raise OpenApiResourceTypeUnsupportedError(**{"resource_type": resource_type, "level": level})


async def list_grants(owner_user_id: int) -> list[dict]:
    """列出该身份在各资源类型上可见的资源、档位与来源(管理面展示用)。

    **一律以 owner_user_id 本身评估,不接受 login_user**:权限引擎带用户上下文时
    会对管理员短路(``list_accessible_ids`` 回 None → 列表恒空、
    ``get_permission_level`` 恒回 owner → 档位恒为最高),而租户闸在
    ``login_user is None`` 时短路为"不拒绝、无快捷键"——正是"这个身份本身能看到
    什么"的语义。所以这里刻意不暴露该参数,从签名上杜绝把管理员传进来。

    ``source``:``direct`` = 代管入口直接授予的档位(可在此撤销);
    ``inherited`` = 部门/租户等继承路径给出的可见性(不归代管管理,撤销无对象)。

    实现 = `list_accessible_ids`(关系 viewer 覆盖全部档位) + 逐个取
    `get_permission_level`;服务账号的资源面通常很小,不做缓存。
    """
    rows: list[dict] = []
    for resource_type in GRANTABLE_RESOURCE_TYPES:
        ids = await PermissionService.list_accessible_ids(owner_user_id, "viewer", resource_type)
        if not ids:
            continue
        for resource_id in ids:
            resource_id = str(resource_id)
            level = await PermissionService.get_permission_level(owner_user_id, resource_type, resource_id)
            direct = await _direct_relations(owner_user_id, resource_type, resource_id)
            rows.append(
                {
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "level": _normalize_relation(level),
                    "source": "direct" if direct & set(GRANT_LEVELS) else "inherited",
                }
            )
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
    #: 写入/撤销的目标 (resource_type, resource_id, relation);与请求条目一一对应。
    write_targets: list[tuple[str, str, str]] = []
    revoke_targets: list[tuple[str, str, str]] = []

    for item in grants or []:
        _validate(item["resource_type"], item["level"])
        resource_type = item["resource_type"]
        resource_id = str(item["resource_id"])
        await _assert_admin_can_manage(admin_user_id, resource_type, resource_id, login_user)
        # 档位是"设定"而不是"叠加":同资源上其它直接档位一并撤掉,否则把 editor
        # 调成 viewer 会留下 editor 元组——账号实际权限高于界面所示(降权失效)。
        for relation in sorted(await _direct_relations(owner_user_id, resource_type, resource_id) & set(GRANT_LEVELS)):
            if relation != item["level"]:
                revoke_items.append(
                    AuthorizeRevokeItem(subject_type="user", subject_id=owner_user_id, relation=relation)
                )
                revoke_targets.append((resource_type, resource_id, relation))
        grant_items.append(AuthorizeGrantItem(subject_type="user", subject_id=owner_user_id, relation=item["level"]))
        write_targets.append((resource_type, resource_id, item["level"]))

    for item in revokes or []:
        _validate(item["resource_type"])
        resource_type = item["resource_type"]
        resource_id = str(item["resource_id"])
        # 撤销的对象是**直接授予**的档位元组:继承来的可见性(部门/租户)无从撤销,
        # owner 档不是代管关系——两者都按"授权不存在"处理(11809,防枚举)。
        direct = await _direct_relations(owner_user_id, resource_type, resource_id)
        revocable = sorted(direct & set(GRANT_LEVELS))
        if not revocable:
            raise OpenApiNotFoundError()
        await _assert_admin_can_manage(admin_user_id, resource_type, resource_id, login_user)
        # 可能有多个档位元组(先授 viewer 后升 editor 就会并存),一并撤销才算真的收回。
        for relation in revocable:
            revoke_items.append(AuthorizeRevokeItem(subject_type="user", subject_id=owner_user_id, relation=relation))
            revoke_targets.append((resource_type, resource_id, relation))

    # 按资源聚合后调用(同一资源的 grants/revokes 分开写,避免互相覆盖)
    for (resource_type, resource_id, _relation), prepared in zip(write_targets, grant_items):  # noqa: B905
        await PermissionService.authorize(
            object_type=resource_type,
            object_id=resource_id,
            grants=[prepared],
            # 授权是管理员的显式动作:写不进 FGA 就不能回 200 说"已授权"
            # (失败仍会进 FailedTuple 补偿队列,后台可重试)。
            enforce_fga_success=True,
        )
    for (resource_type, resource_id, _relation), prepared in zip(revoke_targets, revoke_items):  # noqa: B905
        await PermissionService.authorize(
            object_type=resource_type,
            object_id=resource_id,
            revokes=[prepared],
            enforce_fga_success=True,
        )

    # 计数 = **请求条目数**(契约不变):一个撤销条目可能对应多个档位元组。
    return {"granted": len(grants or []), "revoked": len(revokes or [])}


async def _assert_admin_can_manage(admin_user_id: int, resource_type: str, resource_id: str, login_user: Any) -> None:
    """执行代管的管理员必须自己对目标资源有 manager 权限(越界即 11808)。"""
    allowed = await PermissionService.check(admin_user_id, "manager", resource_type, resource_id, login_user=login_user)
    if not allowed:
        raise OpenApiResourceNotGrantedError(**{"resource_type": resource_type, "resource_id": resource_id})
