"""F076 T-22:服务账号资源授权代管端点(design §2.7,spec AC-06a)。

- `GET  /api/v1/service-accounts/{id}/resource-grants`  列出该账号可见的资源与档位;
- `POST /api/v1/service-accounts/{id}/resource-grants:mutate` 授予/撤销。

校验与写入都委托 `resource_grant_service`(两道闸:白名单 11820 + 管理员对资源
有 manager 才可代管 11808);权限=超管或当前租户管理员,跨租户一律 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel, Field

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.open_api import OpenApiNotFoundError
from bisheng.common.schemas.api import resp_200
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.database.models.tenant import ROOT_TENANT_ID
from bisheng.open_api.domain.constants import OpenApiAuditAction
from bisheng.open_api.domain.services import agent_scope_service, call_audit_service, identity_service
from bisheng.open_api.domain.services import service_account_service as svc
from bisheng.open_api.domain.services.resource_grant_service import list_grants, mutate_grants
from bisheng.utils import get_request_ip

router = APIRouter(prefix="/service-accounts", tags=["OpenApiManagement"])


class GrantItem(BaseModel):
    resource_type: str = Field(description="knowledge_library | knowledge_space | assistant | workflow | managed_agent")
    resource_id: str
    level: str = Field(description="viewer | editor | manager")


class RevokeItem(BaseModel):
    resource_type: str
    resource_id: str


class GrantMutateReq(BaseModel):
    grants: list[GrantItem] = Field(default_factory=list)
    revokes: list[RevokeItem] = Field(default_factory=list)


def _acting_tenant(login_user: UserPayload) -> int:
    return get_current_tenant_id() or login_user.tenant_id


async def _require_account(account_id: int, tenant_id: int):
    account = await svc.get_service_account(account_id, tenant_id=tenant_id)
    if account is None:
        raise OpenApiNotFoundError()
    return account


@router.get("/{account_id}/resource-grants")
async def list_resource_grants(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """该账号当前可见的资源(底层即专用身份的 ReBAC 授权)。"""
    tenant_id = _acting_tenant(login_user)
    account = await _require_account(account_id, tenant_id)
    rows = await list_grants(account.resource_owner_user_id)
    return resp_200(data={"data": rows, "total": len(rows)})


@router.get("/{account_id}/assignable-agents")
async def list_assignable_agents(
    account_id: int,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """该账号可被加入密钥白名单的智能体(助手/工作流/纳管智能体)。

    ``invocable`` 是提示性的调用档位判断(assistant/workflow: use_app;
    managed_agent: editor),不阻止选择——只读 Key 仍可把该资源加入白名单用于
    详情/列表读取。无授权或 owner 不可用时返回空列表,不抛 401(管理面 401 会
    被 platform 当成登录失效并踢出登录)。
    """
    tenant_id = _acting_tenant(login_user)
    account = await _require_account(account_id, tenant_id)
    owner = await identity_service.load_owner_payload(
        owner_user_id=account.resource_owner_user_id,
        tenant_id=tenant_id,
    )
    if owner is None:
        return resp_200(data={"data": [], "total": 0})
    rows = await agent_scope_service.list_assignable_agents(owner)
    return resp_200(data={"data": rows, "total": len(rows)})


@router.post("/{account_id}/resource-grants:mutate")
async def mutate_resource_grants(
    account_id: int,
    req: GrantMutateReq,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
    request: Request = None,  # FastAPI 按注解注入;默认值让测试可直接调用
):
    """授予/撤销该账号的资源授权(先检查后执行,不会出现改了一半的中间态)。"""
    tenant_id = _acting_tenant(login_user)
    account = await _require_account(account_id, tenant_id)
    # 审计要"关键前后取值":服务只回计数,且"授予=设定档位"会**隐式撤销**同资源其它
    # 直接档位——只按请求条目记账会漏。这里先快照该身份当前可见范围(行很小)。
    before_grants = await list_grants(account.resource_owner_user_id)

    result = await mutate_grants(
        owner_user_id=account.resource_owner_user_id,
        admin_user_id=login_user.user_id,
        grants=[item.model_dump() for item in req.grants],
        revokes=[item.model_dump() for item in req.revokes],
        login_user=login_user,
    )
    logger.info(
        "open_api: grants mutated for account {} by user {}: granted={} revoked={}",
        account_id,
        login_user.user_id,
        result["granted"],
        result["revoked"],
    )
    try:
        after_grants = await list_grants(account.resource_owner_user_id)
        call_audit_service.record_admin_action(
            login_user=login_user,
            tenant_id=tenant_id,
            operator_tenant_id=get_current_tenant_id() or ROOT_TENANT_ID,
            action=OpenApiAuditAction.SERVICE_ACCOUNT_GRANT_MUTATE.value,
            target_type="service_account",
            target_id=account_id,
            target_name=account.name,
            before={"grants": before_grants},
            after={"grants": after_grants},
            extra={
                "requested_grants": [g.model_dump() for g in req.grants],
                "requested_revokes": [r.model_dump() for r in req.revokes],
            },
            ip_address=get_request_ip(request) if request is not None else None,
        )
    except Exception:  # 审计绝不能改变管理动作的结果
        logger.opt(exception=True).warning("open_api: grants audit enqueue failed")
    return resp_200(data={"account_id": account_id, **result})
