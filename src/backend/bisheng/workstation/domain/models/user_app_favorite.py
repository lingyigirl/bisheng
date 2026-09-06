"""User app favorite model (F047 agent plaza).

A favorite is an explicit, user-initiated bookmark of an agent app
(workflow / assistant), distinct from the auto-collected "frequently used"
(UserLink type=used_app_pin) and the "pin" features. Tenant-aware:
tenant_id is auto-injected by the tenant filter listener on INSERT.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT


class UserAppFavoriteBase(SQLModelSerializable):
    user_id: int = Field(index=True, description='Favorite owner user id')
    # Flow ids are UUID strings for workflows and ints for assistants —
    # store both as str so the uniqueness key works across app types.
    app_id: str = Field(index=True, max_length=64, description='App (flow) id')
    app_type: int = Field(description='FlowType value: assistant=5 / workflow=10')
    tenant_id: int = Field(
        sa_column=Column(
            Integer, nullable=False, index=True,
            comment='Owner tenant; auto-injected on INSERT (C3)',
        ),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP')),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT),
    )


class UserAppFavorite(UserAppFavoriteBase, table=True):
    __tablename__ = 'user_app_favorite'
    __table_args__ = (
        UniqueConstraint('user_id', 'app_id', 'app_type', name='uq_user_app_favorite'),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )
    id: int | None = Field(default=None, primary_key=True)
