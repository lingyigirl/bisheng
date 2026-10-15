"""F076 开放 API 的请求级身份上下文(T-06)。

鉴权依赖校验通过后把 `OpenApiPrincipal` 装进 ContextVar;`open_endpoints/domain/utils.py`
的三个身份助手(`get_default_operator` / `get_default_operator_async` / `resolve_operator`)
会**优先返回 principal 里已装载的 owner UserPayload** —— 这是"44 个既有 /api/v2 端点
零改造地以服务账号身份执行"的接缝(design §4.1 ⑥ / §4.3)。

生命周期:依赖内 set,请求结束 `finally` reset —— 与租户 ContextVar 同一节奏,
绝不跨请求泄漏身份。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注用:避免领域层在导入期拉起用户模块
    from bisheng.common.dependencies.user_deps import UserPayload


@dataclass(frozen=True, slots=True)
class OpenApiPrincipal:
    """本次调用已校验通过的服务账号身份(凭据 + 资源所有者)。"""

    credential_id: int
    tenant_id: int
    service_account_id: int
    owner_user_id: int
    key_mask: str
    scopes: tuple[str, ...]
    #: 已装载的 owner 身份;端点经 utils 的身份助手取到它(不参与比较/展示)。
    owner_payload: UserPayload = field(repr=False, compare=False)


_current_principal: ContextVar[OpenApiPrincipal | None] = ContextVar("open_api_principal", default=None)


def get_open_api_principal() -> OpenApiPrincipal | None:
    """当前请求的开放 API 主体;未走密钥鉴权时为 None(旧行为分支)。"""
    return _current_principal.get()


def set_open_api_principal(principal: OpenApiPrincipal) -> Token:
    return _current_principal.set(principal)


def reset_open_api_principal(token: Token) -> None:
    _current_principal.reset(token)
