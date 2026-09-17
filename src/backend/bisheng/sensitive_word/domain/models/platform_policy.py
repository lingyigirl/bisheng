from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlmodel import Field, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT

# No tenant_id column — same reason as `platform_word.py` (design K1 / 坑 2):
# a platform-wide row must not be subject to the auto-injected tenant filter.

POLICY_ROW_ID = 1
"""Single-row table: the platform policy is global, so there is exactly one row."""

DEFAULT_ACTION = "mask"
ALLOWED_ACTIONS = ("block", "mask")

# Initial value for `auto_reply`, taken verbatim from the requirement (spec AC-09).
# It is a *default*, not a constant: a super admin may edit it. It lives here rather
# than in the column's `server_default` so that an empty stored value stays
# distinguishable from "never configured" and can be resolved at read time.
# The fullwidth punctuation is intentional — this is user-facing Chinese copy.
DEFAULT_AUTO_REPLY = '很抱歉，你的提问包含敏感词，系统无法做出判断。如果您有其他问题或需要帮助，欢迎随时告诉我，我会尽力为您提供支持'  # noqa: RUF001


class PlatformSensitiveWordPolicy(SQLModelSerializable, table=True):
    """The single platform-wide guardrail policy (cross-tenant, super-admin only)."""

    __tablename__ = "platform_sensitive_word_policy"
    __table_args__ = ({"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},)

    id: int | None = Field(default=None, primary_key=True)
    enabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"), comment="是否启用平台强制围栏"),
    )
    action: str = Field(
        default=DEFAULT_ACTION,
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'mask'"),
            comment="输出方向命中动作: block=截断并话术收尾, mask=打码继续",
        ),
    )
    auto_reply: str = Field(
        default="",
        sa_column=Column(String(500), nullable=False, server_default=text("''"), comment="命中提示话术"),
    )
    updated_by: int | None = Field(default=None, sa_column=Column(Integer, nullable=True, comment="最后修改人ID"))
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT),
    )


class PlatformSensitiveWordPolicyDao:
    """Data access for the single-row platform policy. No auth here — the
    endpoint layer enforces super-admin (design K9)."""

    @classmethod
    async def aget_policy(cls) -> PlatformSensitiveWordPolicy:
        """Return the stored row, or an unsaved default object when unset.

        Returning a default rather than None keeps the callers free of a
        None-branch; an unset policy means "disabled with a mask action", which
        is exactly what the default object expresses (spec AC-14).
        """
        async with get_async_db_session() as session:
            row = (await session.exec(select(PlatformSensitiveWordPolicy))).first()
            if row is not None:
                return row
        return PlatformSensitiveWordPolicy(
            id=POLICY_ROW_ID,
            enabled=False,
            action=DEFAULT_ACTION,
            auto_reply="",
        )

    @classmethod
    async def aupsert(
        cls,
        *,
        enabled: bool,
        action: str,
        auto_reply: str,
        operator_id: int,
    ) -> PlatformSensitiveWordPolicy:
        async with get_async_db_session() as session:
            row = (await session.exec(select(PlatformSensitiveWordPolicy))).first()
            if row is None:
                row = PlatformSensitiveWordPolicy(id=POLICY_ROW_ID)
            row.enabled = enabled
            row.action = action if action in ALLOWED_ACTIONS else DEFAULT_ACTION
            row.auto_reply = (auto_reply or "")[:500]
            row.updated_by = operator_id
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
