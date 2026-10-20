"""Open API (F076) configuration.

Deployment-level knobs only; the per-tenant half of the enforcement switch
lives in the `open_api_tenant_setting` table and the two are ANDed together
(spec AC-34).
"""

from pydantic import BaseModel, Field


class OpenApiConf(BaseModel):
    """Open API authentication configuration."""

    enforce_keys: bool = Field(
        default=False,
        description=(
            "Deployment-level: require a service-account key on every /api/v2 call. "
            "Off by default so that upgrading changes nothing until an operator "
            "flips it (spec AC-21); the tenant-level switch is ANDed with this one."
        ),
    )
    credential_cache_ttl_seconds: int = Field(
        default=3,
        ge=0,
        le=5,
        description=(
            "Positive-cache TTL for credential lookups. Bounds how long a "
            "revocation or edit can lag — spec AC-11/AC-13 promise ≤5 s, so the "
            "TTL may never exceed that."
        ),
    )
    default_tenant_enforce_keys: bool = Field(
        default=False,
        description=(
            "部署便利（F077）：启动时若【默认租户】尚无策略行，按此值播种一行。"
            "已有行**不覆盖**——DB 是策略的唯一事实源，运维手动关掉不会因重启被改回来；"
            "缺行且为 false 时保持\"缺行=未启用\"的 fail-closed 语义。"
        ),
    )
    service_account_idle_days: int = Field(
        default=90,
        description="Days without use after which the management list flags an account as idle.",
    )
