"""F076 权限位(scope)注册表与端点标记助手(T-08,design §2.5 / §4.2)。

两张必须一致的东西:

1. **注册表**(`OPEN_API_SCOPES`):每条 scope 硬编码它覆盖的 `(METHOD, path)` 端点,
   **不用通配**——上游同类实现即如此,通配会让"注册表 ↔ 真实路由"的双向守卫
   (T-12)失去意义。44 条路由 = 42 HTTP + 2 WS(WS 用方法串 `WS`)。
2. **标记助手**(`open_api_scope` 装饰器 + `get_open_api_scope_marker`):
   把 scope 挂到端点函数上,鉴权依赖经 `request.scope["endpoint"]` 读取。
   **fail-closed**:没打标记的端点会被依赖判为 500/11811(服务端配置错误),
   而不是默认放行。

`EXEMPT_ENDPOINTS` 是显式例外清单:whoami 需要密钥但**不需要**任何权限位
(它不是"某能力"的调用,只是凭据自检,T-15 落地)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: WebSocket 路由在注册表里的"方法"串(HTTPConnection 没有 methods 属性)。
WS = "WS"

_V2 = "/api/v2"


@dataclass(frozen=True, slots=True)
class OpenApiScope:
    """One issuable permission scope and the endpoints it guards."""

    code: str
    endpoints: tuple[tuple[str, str], ...]
    group: str


#: 一期可签发的 9 项权限位（F077 追加 `schedule:trigger`）。`delegate` 为二期(受控代表调用)预留:
#: 可签发但当前不产生任何生效行为(design §2.6,spec 已在 §2.6 声明)。
OPEN_API_SCOPES: tuple[OpenApiScope, ...] = (
    OpenApiScope(
        code="workflow:invoke",
        group="app",
        endpoints=(
            ("POST", f"{_V2}/workflow/invoke"),
            ("POST", f"{_V2}/workflow/stop"),
            (WS, f"{_V2}/workflow/chat/{{workflow_id}}"),
        ),
    ),
    OpenApiScope(
        code="workflow:read",
        group="app",
        endpoints=(("GET", f"{_V2}/flows/{{flow_id}}"),),
    ),
    OpenApiScope(
        code="assistant:invoke",
        group="app",
        endpoints=(
            ("POST", f"{_V2}/assistant/chat/completions"),
            (WS, f"{_V2}/assistant/chat/{{assistant_id}}"),
            ("POST", f"{_V2}/llm/workbench/asr"),
            ("POST", f"{_V2}/llm/workbench/tts"),
        ),
    ),
    OpenApiScope(
        code="assistant:read",
        group="app",
        endpoints=(
            ("GET", f"{_V2}/assistant/info/{{assistant_id}}"),
            ("GET", f"{_V2}/assistant/list"),
        ),
    ),
    OpenApiScope(
        code="chat:invoke",
        group="workstation",
        endpoints=(
            ("POST", f"{_V2}/chat/gen_title"),
            ("GET", f"{_V2}/chat/history"),
            ("POST", f"{_V2}/chat/liked"),
            ("POST", f"{_V2}/chat/solved"),
            ("POST", f"{_V2}/chat/comment"),
            ("POST", f"{_V2}/chat/sync/messages"),
        ),
    ),
    OpenApiScope(
        code="schedule:trigger",
        group="schedule",
        endpoints=(("POST", f"{_V2}/schedule/trigger"),),
    ),
    OpenApiScope(
        code="knowledge:read",
        group="knowledge",
        endpoints=(
            ("GET", f"{_V2}/filelib/"),
            ("GET", f"{_V2}/filelib/file/list"),
            ("POST", f"{_V2}/filelib/retrieve"),
            ("GET", f"{_V2}/filelib/download_statistic"),
            ("GET", f"{_V2}/filelib/detail_qa"),
            ("POST", f"{_V2}/filelib/query_qa"),
            ("GET", f"{_V2}/knowledge/get_metadata_fields/{{knowledge_id}}"),
            # 读语义而非写语义——按字段列出的"用户元数据查询"走 GET/读权限位
            ("POST", f"{_V2}/knowledge/file/list_user_metadata"),
            ("GET", f"{_V2}/citation/{{citation_id}}"),
        ),
    ),
    OpenApiScope(
        code="knowledge:write",
        group="knowledge",
        endpoints=(
            ("POST", f"{_V2}/filelib/"),
            ("PUT", f"{_V2}/filelib/"),
            ("DELETE", f"{_V2}/filelib/{{knowledge_id}}"),
            ("DELETE", f"{_V2}/filelib/clear/{{knowledge_id}}"),
            ("POST", f"{_V2}/filelib/file/{{knowledge_id}}"),
            ("DELETE", f"{_V2}/filelib/file/{{file_id}}"),
            ("POST", f"{_V2}/filelib/delete_file"),
            ("POST", f"{_V2}/filelib/chunks"),
            ("POST", f"{_V2}/filelib/chunks_string"),
            ("POST", f"{_V2}/filelib/add_qa"),
            ("POST", f"{_V2}/filelib/add_relative_qa"),
            ("DELETE", f"{_V2}/filelib/qa/{{qa_id}}"),
            ("POST", f"{_V2}/filelib/update_qa"),
            ("POST", f"{_V2}/knowledge/add_metadata_fields"),
            ("PUT", f"{_V2}/knowledge/modify_metadata_fields"),
            ("DELETE", f"{_V2}/knowledge/delete_metadata_fields"),
            ("POST", f"{_V2}/knowledge/file/add_user_metadata"),
            ("PUT", f"{_V2}/knowledge/file/modify_user_metadata"),
            ("DELETE", f"{_V2}/knowledge/file/delete_user_metadata"),
        ),
    ),
    OpenApiScope(
        code="delegate",
        group="delegation",
        # 二期(受控代表调用)预留:当前没有端点归属该权限位。
        endpoints=(),
    ),
)

OPEN_API_SCOPE_MAP: dict[str, OpenApiScope] = {s.code: s for s in OPEN_API_SCOPES}
OPEN_API_SCOPE_CODES: frozenset[str] = frozenset(OPEN_API_SCOPE_MAP)
#: 一期可签发的权限位(与全部注册表一致;二期如有不可签发位再拆分)。
ISSUABLE_OPEN_API_SCOPE_CODES: frozenset[str] = OPEN_API_SCOPE_CODES

#: 唯一一条隐含规则:能写知识库的密钥,自然能读(与上游同口径)。
#: 只影响判定,不改变已签发密钥的 scope 列表。
OPEN_API_SCOPE_IMPLIED_BY: dict[str, frozenset[str]] = {
    "knowledge:read": frozenset({"knowledge:write"}),
}

#: 需要密钥、但**不需要**任何权限位的端点(whoami 凭据自检,T-15 落地)。
EXEMPT_ENDPOINTS: frozenset[tuple[str, str]] = frozenset({("GET", f"{_V2}/auth/whoami")})


def is_scope_granted(required: str | None, granted: frozenset[str] | set[str]) -> bool:
    """`required=None` 表示"仅需凭据";否则按注册表 + 隐含规则判定。"""
    if required is None:
        return True
    if required in granted:
        return True
    return not OPEN_API_SCOPE_IMPLIED_BY.get(required, frozenset()).isdisjoint(granted)


OPEN_API_SCOPE_ATTR = "__open_api_scope__"


@dataclass(frozen=True, slots=True)
class OpenApiScopeMarker:
    """挂在端点函数上的标记;`scope=None` 表示"需凭据但不需要权限位"。

    `guest=True` 标记"平台免登录页依赖"的端点(discovery 决策 D5):强制开启后,
    这些端点在 `enable_guest_access` 开着时仍允许匿名访问(旧行为);开关关掉则与
    其余端点一视同仁。清单被守卫测试冻结,新增端点默认**不**进豁免。
    """

    scope: str | None
    guest: bool = False


def open_api_scope(scope: str | None, *, guest: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """端点标记装饰器。

    **运行期校验**(不是静态检查):scope 必须已在注册表里登记;写错名字会在
    应用导入期立刻炸,而不是等到某个请求把 500/11811 打给客户。
    `guest=True` 只用于平台免登录页依赖的 8 个端点(见 `OpenApiScopeMarker`)。
    """
    if scope is not None and scope not in OPEN_API_SCOPE_CODES:
        raise ValueError(f"open_api: unknown scope {scope!r}")
    marker = OpenApiScopeMarker(scope=scope, guest=guest)

    def _decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, OPEN_API_SCOPE_ATTR, marker)
        return func

    return _decorate


def get_open_api_scope_marker(endpoint: Any) -> OpenApiScopeMarker | None:
    """读取端点标记;`None` = 该端点漏标(fail-closed → 500/11811)。"""
    marker = getattr(endpoint, OPEN_API_SCOPE_ATTR, None)
    return marker if isinstance(marker, OpenApiScopeMarker) else None


def all_registered_endpoints() -> set[tuple[str, str]]:
    """注册表声明的全部 `(METHOD, path)`(守卫测试的双向比对用)。"""
    return {pair for scope in OPEN_API_SCOPES for pair in scope.endpoints}
