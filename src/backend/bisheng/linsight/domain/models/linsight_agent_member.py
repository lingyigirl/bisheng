"""User-saved collaborator templates for task mode (F087, 2026-10-16).

Task-time collaborators live only inside the submitted ``agent_config``
snapshot — there is no reusable library, so every task re-enters the same
member by hand. This table is the missing library: a USER-scoped (not
tenant-shared) set of saved member templates.

Trust model — a saved template can never outgrow its owner:

- Saving validates the member with the SAME rules the task path uses
  (``AgentConfigService.resolve``: model online/llm, tools exist and are
  authorized, connector shape, skills ⊆ tenant-enabled).
- Listing re-checks availability (resources can be deleted later) and reports
  per-member ``issues``; the submit-time validation remains the final gate.

Tenant isolation mirrors ``LinsightSkillDao`` (``tenant_id`` + STRICT tenant
filter on SELECTs); every accessor additionally scopes by ``user_id`` because
the library is personal. ``name`` is unique per (tenant, user) — saving under
an existing name updates that template in place.
"""

from datetime import datetime

from sqlalchemy import Integer
from sqlmodel import Column, DateTime, Field, String, Text, UniqueConstraint, col, select, text

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.context.tenant import strict_tenant_filter
from bisheng.core.database import get_async_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType


class LinsightAgentMemberBase(SQLModelSerializable):
    """Stored fields mirror ``SubagentConfig`` (the task-time member shape)."""

    tenant_id: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), index=True, comment="Tenant ID"),
    )
    user_id: int = Field(
        ...,
        sa_column=Column(Integer, nullable=False, index=True, comment="Owner user id"),
    )
    name: str = Field(
        ...,
        description="Member display name (unique per user)",
        sa_column=Column(String(64), nullable=False, comment="Member display name"),
    )
    description: str = Field(
        ...,
        description="Role description / when to call",
        sa_column=Column(Text, nullable=False, comment="Role description"),
    )
    system_prompt: str = Field(
        default="",
        sa_column=Column(Text, nullable=False, comment="System prompt (<=12000)"),
    )
    model_id: str | None = Field(
        default=None,
        sa_column=Column(String(20), nullable=True, comment="Preferred model id; NULL = inherit main"),
    )
    skill_names: list[str] | None = Field(
        default=None,
        description="Selected skill names",
        sa_column=Column(JsonType, nullable=True, comment="Selected skill names"),
    )
    tool_refs: list[int] | None = Field(
        default=None,
        description="Selected tool ids",
        sa_column=Column(JsonType, nullable=True, comment="Selected tool ids"),
    )
    connector_refs: list[int] | None = Field(
        default=None,
        description="Selected connector (API/MCP action) ids",
        sa_column=Column(JsonType, nullable=True, comment="Selected connector ids"),
    )
    is_default: bool = Field(
        default=False,
        description="Auto-checked into new conversations",
        sa_column=Column("is_default", Integer, nullable=False, server_default=text("0"), comment="Default-carry flag"),
    )
    create_time: datetime = Field(
        default_factory=datetime.now,
        description="Creation time",
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, server_default=UPDATE_TIME_SERVER_DEFAULT),
    )


class LinsightAgentMember(LinsightAgentMemberBase, table=True):
    """One saved collaborator template owned by one user."""

    __tablename__ = "linsight_agent_member"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="uq_linsight_agent_member_tenant_user_name"),
    )

    id: int | None = Field(default=None, primary_key=True, description="Member template unique id")


class LinsightAgentMemberDao:
    """Data access for saved collaborator templates (strict tenant + user scope).

    SELECTs run inside ``strict_tenant_filter()`` for the same reason as
    ``LinsightSkillDao``: the default F012 IN-list would otherwise leak ROOT
    rows into a child tenant's view.
    """

    @classmethod
    async def list_by_user(cls, user_id: int) -> list[LinsightAgentMember]:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                statement = (
                    select(LinsightAgentMember)
                    .where(LinsightAgentMember.user_id == user_id)
                    .order_by(col(LinsightAgentMember.update_time).desc(), col(LinsightAgentMember.id).desc())
                )
                result = await session.exec(statement)
                return list(result.all())

    @classmethod
    async def get_by_name(cls, user_id: int, name: str) -> LinsightAgentMember | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                statement = select(LinsightAgentMember).where(
                    LinsightAgentMember.user_id == user_id,
                    LinsightAgentMember.name == name,
                )
                result = await session.exec(statement)
                return result.first()

    @classmethod
    async def get_by_id(cls, member_id: int, user_id: int) -> LinsightAgentMember | None:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                statement = select(LinsightAgentMember).where(
                    LinsightAgentMember.id == member_id,
                    LinsightAgentMember.user_id == user_id,
                )
                result = await session.exec(statement)
                return result.first()

    @classmethod
    async def update(cls, member: LinsightAgentMember) -> LinsightAgentMember:
        """Persist field-level edits on an owned row (rename included)."""
        async with get_async_db_session() as session:
            member.update_time = datetime.now()
            session.add(member)
            await session.commit()
            await session.refresh(member)
            return member

    @classmethod
    async def set_default(cls, member_id: int, user_id: int, value: bool) -> bool:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                statement = select(LinsightAgentMember).where(
                    LinsightAgentMember.id == member_id,
                    LinsightAgentMember.user_id == user_id,
                )
                result = await session.exec(statement)
                row = result.first()
                if row is None:
                    return False
                row.is_default = value
                row.update_time = datetime.now()
                session.add(row)
                await session.commit()
                return True

    @classmethod
    async def upsert(cls, member: LinsightAgentMember) -> LinsightAgentMember:
        """Insert, or update the row with the same (tenant, user, name)."""
        existing = await cls.get_by_name(member.user_id, member.name)
        async with get_async_db_session() as session:
            if existing is not None:
                existing.description = member.description
                existing.system_prompt = member.system_prompt
                existing.model_id = member.model_id
                existing.skill_names = member.skill_names
                existing.tool_refs = member.tool_refs
                existing.connector_refs = member.connector_refs
                existing.update_time = datetime.now()
                session.add(existing)
                await session.commit()
                await session.refresh(existing)
                return existing
            session.add(member)
            await session.commit()
            await session.refresh(member)
            return member

    @classmethod
    async def delete(cls, member_id: int, user_id: int) -> bool:
        with strict_tenant_filter():
            async with get_async_db_session() as session:
                statement = select(LinsightAgentMember).where(
                    LinsightAgentMember.id == member_id,
                    LinsightAgentMember.user_id == user_id,
                )
                result = await session.exec(statement)
                row = result.first()
                if row is None:
                    return False
                await session.delete(row)
                await session.commit()
                return True
