"""F078 纳管智能体注册表（``managed_agent``）。

一条记录 = 一个可被统一调用/调度的智能体：本平台应用投影（``source_kind=native``）
或第三方智能体（low_code / high_code）。第三方记录不写入 ``flow`` / ``assistant``
表；原生记录只保存 ``native_app_id`` + ``native_app_type``，不复制应用数据
（design 决策 5 / INV-26）。

租户感知：本表登记在 ``core/database/tenant_filter.py`` 的
``_TENANT_AWARE_MODEL_MODULES``；``tenant_id`` 必须 ``default=None``。

双库约束（C2）：``agent_card`` 用 ``dialect_helpers.JsonType``（DM8 退化为
CLOB，只整读整写、永不作为查询条件）；``credential_ciphertext`` /
``description`` 用 ``LargeText``；``update_time`` 用
``UPDATE_TIME_SERVER_DEFAULT``；所有状态迁移走 ``UPDATE ... WHERE id=? AND
tenant_id=?`` 并判 ``rowcount``，禁 ``RETURNING``。

``endpoint_hash`` 非空：外部记录为规范化接入地址的 SHA-256；原生投影为
``sha256("native:{native_app_type}:{native_app_id}")`` 哨兵，避免依赖 DM8 的
“多 NULL 唯一”行为（design 决策 5）。

回滚（整表由 SQLModel ``create_all`` 建，不写 Alembic revision）：

    DROP TABLE IF EXISTS managed_agent;
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType, LargeText


class SourceKind(StrEnum):
    """纳管来源（列 ``source_kind``，String(16)）。"""

    NATIVE = "native"
    LOW_CODE = "low_code"
    HIGH_CODE = "high_code"


class AgentProtocol(StrEnum):
    """接入协议（列 ``protocol``，String(32)）；A2A 不在本期。"""

    NATIVE = "native"
    OPENAI_COMPATIBLE = "openai_compatible"
    CUSTOM_HTTP = "custom_http"


class AgentStatus(StrEnum):
    """生命周期状态（列 ``status``，String(16)）；仅 ONLINE 可调用/调度。"""

    DRAFT = "draft"
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class HealthStatus(StrEnum):
    """健康状态（列 ``health_status``，String(16)）。"""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class HealthMethod(StrEnum):
    """健康检查方式（列 ``health_method``，String(16)）。"""

    HEALTH_URL = "health_url"
    TEST_INVOKE = "test_invoke"


class CredentialType(StrEnum):
    """出站鉴权方式（列 ``credential_type``，String(16)）。"""

    NONE = "none"
    BEARER = "bearer"
    API_KEY = "api_key"


class ManagedAgent(SQLModelSerializable, table=True):
    """A tenant-scoped registry row for one managed agent (F078)."""

    __tablename__ = "managed_agent"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uk_managed_agent_tenant_name"),
        UniqueConstraint("tenant_id", "endpoint_hash", name="uk_managed_agent_tenant_endpoint_hash"),
        Index("idx_managed_agent_tenant_status", "tenant_id", "status"),
        Index("idx_managed_agent_tenant_source", "tenant_id", "source_kind"),
        Index("idx_managed_agent_tenant_class", "tenant_id", "scenario_code", "industry_code"),
        Index("idx_managed_agent_health", "status", "health_status", "last_health_check_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    name: str = Field(
        sa_column=Column(String(128), nullable=False, comment="纳管名称(租户内唯一)"),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(LargeText, nullable=True, comment="描述"),
    )
    source_kind: str = Field(
        sa_column=Column(String(16), nullable=False, comment="来源(native/low_code/high_code)"),
    )
    protocol: str = Field(
        sa_column=Column(String(32), nullable=False, comment="协议(native/openai_compatible/custom_http)"),
    )
    status: str = Field(
        default=AgentStatus.DRAFT.value,
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'draft'"),
            comment="生命周期状态(draft/online/offline/disabled)",
        ),
    )
    version: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="智能体版本标识"),
    )
    native_app_id: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="原生应用ID(仅 native 投影)"),
    )
    native_app_type: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="原生应用类型(5=assistant/10=workflow)"),
    )
    endpoint: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="第三方接入地址原文(仅外部智能体)"),
    )
    endpoint_hash: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            comment="接入地址规范化SHA-256;原生投影用 native:{type}:{id} 哨兵",
        ),
    )
    model_name: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="OpenAI-compatible 模型名"),
    )
    agent_card: dict | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True, comment="Agent Card/能力清单(整读整写,不查询)"),
    )
    credential_ciphertext: str | None = Field(
        default=None,
        sa_column=Column(LargeText, nullable=True, comment="Fernet 加密的出站凭据(不回显)"),
    )
    credential_type: str = Field(
        default=CredentialType.NONE.value,
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'none'"),
            comment="出站鉴权方式(none/bearer/api_key)",
        ),
    )
    has_credential: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"), comment="是否已配置凭据"),
    )
    health_url: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="健康检查 URL(可选)"),
    )
    health_method: str = Field(
        default=HealthMethod.TEST_INVOKE.value,
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'test_invoke'"),
            comment="健康检查方式(health_url/test_invoke)",
        ),
    )
    health_status: str = Field(
        default=HealthStatus.UNKNOWN.value,
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'unknown'"),
            comment="健康状态(unknown/healthy/unhealthy)",
        ),
    )
    consecutive_health_failures: int = Field(
        default=0,
        sa_column=Column(
            Integer,
            nullable=False,
            server_default=text("0"),
            comment="连续健康检查失败次数",
        ),
    )
    last_health_check_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="最近一次健康检查时间"),
    )
    last_health_ok_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="最近一次健康检查成功时间"),
    )
    last_call_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="最近一次调用时间"),
    )
    last_run_status: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True, comment="最近一次调用状态(success/failed)"),
    )
    last_latency_ms: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="最近一次调用耗时(ms)"),
    )
    scenario_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="场景分类 code"),
    )
    industry_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True, comment="行业分类 code"),
    )
    user_id: int = Field(
        sa_column=Column(Integer, nullable=False, index=True, comment="创建者/资源所有者"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=UPDATE_TIME_SERVER_DEFAULT,
            comment="更新时间",
        ),
    )

    @property
    def is_callable(self) -> bool:
        return self.status == AgentStatus.ONLINE.value
