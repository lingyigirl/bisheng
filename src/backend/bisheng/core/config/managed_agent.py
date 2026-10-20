"""F078 managed-agent configuration.

All knobs live in YAML, not the DB: the invoke/health consumers are API and
Celery processes that read config at startup, and the DB config layer caches for
~100s (an emergency switch must not sit behind that).

``enabled`` is a deployment-level switch and defaults to **false**: when off,
every managed-agent route answers 404 and the platform menu/client tab are
hidden, so an existing deployment is byte-for-byte unchanged (spec AC-63).
"""

from pydantic import BaseModel, Field


class ManagedAgentConf(BaseModel):
    """Managed-agent runtime knobs (all overridable from ``config.yaml``)."""

    enabled: bool = Field(
        default=False,
        description="Deployment-level feature switch; off means 404 + hidden UI (spec AC-63).",
    )
    connect_timeout_seconds: float = Field(default=10.0, description="Outbound TCP/TLS connect timeout.")
    read_timeout_seconds: float = Field(default=60.0, description="Outbound response read timeout.")
    write_timeout_seconds: float = Field(default=30.0, description="Outbound request write timeout.")
    pool_timeout_seconds: float = Field(default=10.0, description="Outbound connection-pool wait timeout.")
    invoke_overall_timeout_seconds: float = Field(
        default=120.0,
        description="Overall wall-clock deadline for one invoke (covers slow-drip upstreams).",
    )
    max_response_bytes: int = Field(
        default=1_000_000,
        description="Hard cap on an upstream response body; enforced while streaming (spec AC-17).",
    )
    rate_limit_per_minute: int = Field(
        default=60,
        description="Per tenant+agent invocation limit; health test-invokes are excluded (spec AC-26).",
    )
    health_interval_seconds: int = Field(
        default=300,
        description="Beat tick for the managed-agent health scan; the beat entry itself is fixed at 300s.",
    )
    health_failure_threshold: int = Field(
        default=3,
        description="Consecutive health failures before marking unhealthy (spec AC-42).",
    )
    auto_offline_on_health_failure: bool = Field(
        default=False,
        description="When true, reaching the threshold also moves the agent offline; default only marks unhealthy.",
    )
    max_agents_per_tenant: int = Field(
        default=100,
        description="Tenant-wide cap on registry rows; creation is refused beyond it (spec AC-12).",
    )
    egress_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "Exact hosts or host:port entries allowed for outbound calls. Empty means "
            "no host is allowed — operators must opt in explicitly (spec AC-16)."
        ),
    )
    proxy: str | None = Field(
        default=None,
        description="Explicit outbound proxy URL for corporate environments; trust_env stays false.",
    )
    allow_private_networks: bool = Field(
        default=False,
        description=(
            "TEST/DEVELOPMENT ONLY: allow outbound calls to private/loopback addresses "
            "(e.g. a local mock agent server in e2e). Never enable in production."
        ),
    )
