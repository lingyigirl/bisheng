"""F078 纳管分类法（``managed_agent_taxonomy``）。

场景/行业两个 kind 共用一张表：``kind`` + 稳定 ``code`` + 三语名称 + 排序 +
启用位。``managed_agent.scenario_code`` / ``industry_code`` 只存 code，不建 FK；
由服务层校验分类项存在且启用（AC-49），停用后既有绑定保留、展示回退 code。

租户感知与回滚同 ``managed_agent``：登记 ``_TENANT_AWARE_MODEL_MODULES``；
整表由 SQLModel ``create_all`` 建，回滚 ``DROP TABLE IF EXISTS
managed_agent_taxonomy``。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class TaxonomyKind(StrEnum):
    """分类维度（列 ``kind``，String(16)）。"""

    SCENARIO = "scenario"
    INDUSTRY = "industry"


class ManagedAgentTaxonomy(SQLModelSerializable, table=True):
    """One tenant-scoped scenario/industry taxonomy item (AC-48)."""

    __tablename__ = "managed_agent_taxonomy"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "kind",
            "code",
            name="uk_managed_agent_taxonomy_tenant_kind_code",
        ),
        Index(
            "idx_managed_agent_taxonomy_list",
            "tenant_id",
            "kind",
            "is_enabled",
            "sort_order",
        ),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment="租户ID"),
    )
    kind: str = Field(
        sa_column=Column(String(16), nullable=False, comment="分类维度(scenario/industry)"),
    )
    code: str = Field(
        sa_column=Column(String(64), nullable=False, comment="稳定分类标识(租户内同 kind 唯一)"),
    )
    name_zh: str = Field(
        sa_column=Column(String(128), nullable=False, comment="简体中文名称"),
    )
    name_en: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="英文名称(缺失回退 code)"),
    )
    name_ja: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True, comment="日文名称(缺失回退 code)"),
    )
    sort_order: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0"), comment="排序"),
    )
    is_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("1"), comment="是否启用"),
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
