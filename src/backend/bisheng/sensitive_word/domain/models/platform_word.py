from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, text
from sqlmodel import Field, delete, func, select

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session

# Why this table has NO tenant_id column (design K1 / 坑 2)
# -------------------------------------------------------
# This is a PLATFORM-level library shared by every tenant. `core/database/tenant_filter.py`
# discovers tenant-aware tables by *column presence* (`_discover_tenant_aware_tables`) and
# then (a) injects `WHERE tenant_id = <current>` on every SELECT via `do_orm_execute`, and
# (b) auto-fills `tenant_id` on every INSERT via `before_flush`. Giving this table a
# `tenant_id` column would therefore make global rows BOTH unwritable (back-filled to the
# current tenant) and unreadable (filtered out) — silently degrading to "library is empty,
# nothing is detected". Omitting the column removes the failure mode at the root instead of
# papering over it with per-call-site `bypass_tenant_filter()`.
#
# Do NOT "unify" this by adding a tenant_id column. If a future feature needs per-tenant
# overrides, merge into a scoped table and redesign the isolation — see F059 design 决策 1.


class PlatformSensitiveWord(SQLModelSerializable, table=True):
    """One entry of the platform-wide (cross-tenant) sensitive word library."""

    __tablename__ = "platform_sensitive_word"
    __table_args__ = (
        UniqueConstraint("word", name="uk_platform_sensitive_word"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: int | None = Field(default=None, primary_key=True)
    word: str = Field(
        sa_column=Column(String(255), nullable=False, comment="敏感词条"),
    )
    source: str = Field(
        default="manual",
        sa_column=Column(
            String(16),
            nullable=False,
            server_default=text("'manual'"),
            comment="录入来源: manual=手工新增, import=批量导入",
        ),
    )
    created_by: int | None = Field(default=None, sa_column=Column(Integer, nullable=True, comment="创建人ID"))
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"),
    )


class PlatformSensitiveWordDao:
    """Data access for the platform word library.

    None of these methods take a tenant id — the table is deliberately global
    (see the module docstring). Callers must be super-admin authorised at the
    endpoint layer; the DAO does no auth of its own.
    """

    @classmethod
    async def acount(cls, keyword: str | None = None) -> int:
        stmt = select(func.count()).select_from(PlatformSensitiveWord)
        if keyword:
            stmt = stmt.where(PlatformSensitiveWord.word.like(f"%{keyword}%"))
        async with get_async_db_session() as session:
            return int(await session.scalar(stmt) or 0)

    @classmethod
    async def alist(cls, keyword: str | None = None, page: int = 1, page_size: int = 20) -> list[PlatformSensitiveWord]:
        stmt = select(PlatformSensitiveWord)
        if keyword:
            stmt = stmt.where(PlatformSensitiveWord.word.like(f"%{keyword}%"))
        stmt = stmt.order_by(PlatformSensitiveWord.id.desc()).offset((page - 1) * page_size).limit(page_size)
        async with get_async_db_session() as session:
            rows = await session.exec(stmt)
            return list(rows.all())

    @classmethod
    async def alist_all_words(cls) -> list[str]:
        """Every word, for building the matcher. Selects the single column only —
        the library can hold tens of thousands of rows and the automaton needs
        nothing else."""
        async with get_async_db_session() as session:
            rows = await session.exec(select(PlatformSensitiveWord.word))
            return [row for row in rows.all() if row]

    @classmethod
    async def aexists(cls, word: str) -> bool:
        async with get_async_db_session() as session:
            stmt = select(func.count()).select_from(PlatformSensitiveWord).where(PlatformSensitiveWord.word == word)
            return bool(int(await session.scalar(stmt) or 0))

    @classmethod
    async def acreate(cls, word: str, source: str = "manual", created_by: int | None = None) -> PlatformSensitiveWord:
        async with get_async_db_session() as session:
            row = PlatformSensitiveWord(word=word, source=source, created_by=created_by)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    @classmethod
    async def acreate_many(cls, words: list[str], source: str = "import", created_by: int | None = None) -> int:
        """Bulk insert, skipping words already present. Returns the number inserted.

        Uses a per-word existence check rather than a bulk INSERT ... ON CONFLICT:
        the unique violation would abort the whole transaction on either dialect,
        and import files routinely repeat words that the parser already de-duped
        against *each other* but not against the database.
        """
        if not words:
            return 0
        inserted = 0
        async with get_async_db_session() as session:
            for word in words:
                stmt = select(func.count()).select_from(PlatformSensitiveWord).where(
                    PlatformSensitiveWord.word == word
                )
                if int(await session.scalar(stmt) or 0):
                    continue
                session.add(PlatformSensitiveWord(word=word, source=source, created_by=created_by))
                inserted += 1
            if inserted:
                await session.commit()
        return inserted

    @classmethod
    async def adelete_by_id(cls, word_id: int) -> bool:
        async with get_async_db_session() as session:
            row = await session.get(PlatformSensitiveWord, word_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @classmethod
    async def areplace_all(cls, words: list[str], source: str = "import", created_by: int | None = None) -> int:
        """Overwrite mode: the library becomes exactly `words`.

        Delete + insert in ONE transaction so a failure mid-way cannot leave the
        library empty (the confirmation preview the admin approved would no longer
        describe what is stored). Returns the number of rows written.
        """
        async with get_async_db_session() as session:
            await session.exec(delete(PlatformSensitiveWord))
            for word in words:
                session.add(PlatformSensitiveWord(word=word, source=source, created_by=created_by))
            await session.commit()
        return len(words)
