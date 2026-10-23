"""F076 T-15:密钥自检端点 `GET /api/v2/auth/whoami`(spec AC-33)。

对接方联调与报障时用它确认"这把密钥是谁、能干什么、什么时候到期"——
**需要有效密钥,但不需要任何权限位**(注册表里唯一的 scope=None 例外,
见 `EXEMPT_ENDPOINTS`)。

判定口径:无论总闸是否打开,whoami 都要求一把已通过校验的密钥。
总闸关闭时依赖走旧行为(不装 principal),此时端点自身拒绝(11801)——
也就是说联调自检要在"总闸打开"的窗口里做,与切换流程一致。
"""

from fastapi import APIRouter

from bisheng.common.errcode.open_api import OpenApiKeyMissingError
from bisheng.common.schemas.api import resp_200
from bisheng.open_api.domain.agent_scope import allowed_apps_to_json
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.open_api.domain.models.dao import ServiceAccountDao
from bisheng.open_api.domain.scopes import open_api_scope

router = APIRouter(prefix="/auth", tags=["OpenApi"])


@router.get("/whoami")
@open_api_scope(None)
async def whoami():
    """回显当前密钥的自描述信息(design §4.2 的 whoami 形状)。"""
    principal = get_open_api_principal()
    if principal is None:
        # 走到这里说明总闸关闭(依赖未装 principal)或端点被单独调用:
        # 两种情况下都没有已校验的密钥,一律按"缺密钥"拒绝。
        raise OpenApiKeyMissingError()

    account = await ServiceAccountDao.aget_by_id(principal.service_account_id)
    return resp_200(
        data={
            "account_id": principal.service_account_id,
            "account_name": account.name if account is not None else None,
            "key_mask": principal.key_mask,
            "scopes": list(principal.scopes),
            # None = unrestricted; [] = no agent may be called (spec AC-45).
            "allowed_apps": allowed_apps_to_json(principal),
            "expires_at": principal.expires_at.isoformat() if principal.expires_at else None,
            "tenant_id": principal.tenant_id,
        }
    )
