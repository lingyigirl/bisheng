"""F076 开放 API 租户级策略表(`open_api_tenant_setting`)。

每租户一行:是否对本租户强制密钥鉴权(spec AC-34)。与部署级开关
`open_api.enforce_keys` **与运算**后才强制;**缺行 = 未启用**(fail-closed)。

与 `service_account` / `api_credential` 同批交付(discovery 决策 D4),
随内核在 Wave 1 落地。

租户感知:登记在 core/database/tenant_filter.py 的 `_TENANT_AWARE_MODEL_MODULES`;
`tenant_id` 是主键且 `autoincrement=False`——本表每租户至多一行。

回滚(整表由 SQLModel `create_all` 建,无 Alembic revision):

    DROP TABLE IF EXISTS open_api_tenant_setting;
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class OpenApiTenantSetting(SQLModelSerializable, table=True):
    """Per-tenant Open API policy; a missing row means "not enforced" (spec AC-34)."""

    __tablename__ = "open_api_tenant_setting"
    __table_args__ = ({"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},)

    tenant_id: int = Field(
        sa_column=Column(Integer, primary_key=True, autoincrement=False, comment="租户ID(每租户一行)"),
    )
    enforce_keys: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=text("0"),
            comment="是否强制密钥鉴权(与部署级开关与运算;缺行=未启用)",
        ),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )
