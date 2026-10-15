"""F076 服务账号表(`service_account`)。

一个对接系统一个服务账号(spec AC-01)。其资源所有者是一个由平台在创建账号时
自动创建的**专用最小权限用户**(spec AC-02):不可交互登录、零角色零资源授权,
创建/变更时与运行时双重防提权(spec AC-03 / AC-04)。

服务账号本身不是自然人、不参与登录——它只在开放 API 的密钥鉴权链路里出现
(design §3 决策 1)。

租户感知:本表登记在 core/database/tenant_filter.py 的 `_TENANT_AWARE_MODEL_MODULES`;
`tenant_id` 必须 `default=None`(tenant/test_tenant_id_default_guard.py 守卫)。

回滚(本特性不写 Alembic revision,整表由 SQLModel `create_all` 建,
post-deploy 回滚见 design §2 约束 5):

    DROP TABLE IF EXISTS service_account;
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class ServiceAccount(SQLModelSerializable, table=True):
    """A service account representing one integrated system (spec AC-01).

    The account owns nothing itself: its resource visibility is exactly the
    ReBAC grants of ``resource_owner_user_id`` (INV-23 clause 2). Soft delete
    keeps the name unavailable for reuse so audit references stay unambiguous.
    """

    __tablename__ = "service_account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uk_service_account_tenant_name"),
        Index("idx_service_account_tenant_status", "tenant_id", "deleted_at", "disabled_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    name: str = Field(
        sa_column=Column(String(128), nullable=False, comment="对接系统名称(租户内唯一)"),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True, comment="用途说明"),
    )
    resource_owner_user_id: int = Field(
        sa_column=Column(
            Integer,
            nullable=False,
            index=True,
            comment="资源所有者:平台自动创建的专用最小权限用户(不得为管理员)",
        ),
    )
    disabled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="停用时间;非空即名下密钥全部失效"),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, comment="软删时间;非空即不可用,名字不复用"),
    )
    created_by: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment="创建人(管理员)ID"),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment="更新时间"),
    )

    @property
    def is_enabled(self) -> bool:
        """启用 = 未停用且未删除(spec AC-05 / AC-06)。"""
        return self.disabled_at is None and self.deleted_at is None
