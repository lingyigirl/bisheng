"""Open API (F076) configuration.

Deployment-level knobs only; the per-tenant half of the enforcement switch
lives in the `open_api_tenant_setting` table and the two are ANDed together
(spec AC-34).
"""

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


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
            '缺行且为 false 时保持"缺行=未启用"的 fail-closed 语义。'
        ),
    )
    service_account_idle_days: int = Field(
        default=90,
        description="Days without use after which the management list flags an account as idle.",
    )
    public_base_url: str = Field(
        default="",
        description=(
            "对接方访问 /api/v2 的根地址（含端口与反代前缀），如 http://10.0.0.5:7860 或 "
            "https://gw.example.com/bisheng。设置后接入指南默认显示它；留空则由后端探测"
            "本机出口 IP + 监听端口作为默认值（探测值在网关/HTTPS 部署下可能不可达，"
            "故不能替代运维配置）。"
        ),
    )

    @field_validator("public_base_url")
    @classmethod
    def _normalize_public_base_url(cls, value: str) -> str:
        """归一化并校验：配置错误必须启动即失败，不能静默给出一个错地址。"""
        value = (value or "").strip().rstrip("/")
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("open_api.public_base_url must be an http(s) URL with a host")
        return value
