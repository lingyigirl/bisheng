from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, text
from sqlmodel import Field

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database.dialect_helpers import (
    UPDATE_TIME_SERVER_DEFAULT,
    JsonType,
    LargeText,
)

#: Definition length cap (spec AC-04). Enforced in the service, stated here so
#: the limit travels with the column it belongs to.
MAX_DEFINITION_LENGTH = 200

#: Mirrors the `standard_term` column width. A term longer than this cannot be
#: stored, so an import drops the row and counts it rather than letting the
#: INSERT fail halfway through a file (see `term_importer`).
MAX_STANDARD_TERM_LENGTH = 255

#: Library size cap per tenant (spec AC-06).
MAX_TERMS_PER_TENANT = 500


class TermLibraryTerm(SQLModelSerializable, table=True):
    """One glossary entry: a standard term, its aliases, and what it means (F060).

    A tenant-level shared configuration, NOT a controlled resource: there is no
    per-term authorization, no FGA tuple, and the department field below grants
    nothing. That is the opposite of `knowledge.department_id`, where the
    department IS a visibility boundary — see the module docstring on the
    injection service for why the two must not be conflated (contract INV-17).

    Uniqueness spans `standard_term` AND every entry in `aliases`, per tenant
    (spec AC-02 / AC-03). The ALIAS half is enforced in the service, because
    aliases live inside a JSON column and no index can reach them —
    `test/term_library/test_term_service.py` holds that rule down.

    The `standard_term` half is enforced BOTH ways, and needs to be:
    `_assert_unique` is a read-then-insert check, so two admins saving the same
    term at the same moment would otherwise both pass it and both insert. The
    unique index below is what makes the loser of that race fail (code review M3).
    """

    __tablename__ = 'term_library_term'
    # The dialect kwargs dict must come LAST: SQLAlchemy reads a trailing dict as
    # table options and every other entry as a schema item, so putting it first
    # raises "'SchemaItem' object expected, got {'mysql_charset': ...}".
    __table_args__ = (
        # `(tenant_id, standard_term)`, not `standard_term` alone: the rule is
        # per tenant, and a global index would let one tenant's term block
        # another tenant's (contract INV-16).
        #
        # Case-SENSITIVE, which is what the collation gives for free. The service
        # compares with `casefold()` and remains the authority for names that
        # differ only in case — a portable functional index over the folded value
        # is not expressible across MySQL and DM8.
        UniqueConstraint('tenant_id', 'standard_term', name='uk_term_library_term_tenant_standard'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, index=True, comment='租户ID'),
    )
    standard_term: str = Field(
        sa_column=Column(String(255), nullable=False, comment='标准词; 租户内唯一'),
    )
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JsonType,
            nullable=True,
            comment='别名数组; 与标准词一同参与租户内唯一性校验',
        ),
    )
    definition: str = Field(
        sa_column=Column(
            LargeText,
            nullable=False,
            comment='释义; 注入对话时的解释正文, 上限 200 字',
        ),
    )
    #: DISPLAY METADATA, NOT AN AUTHORIZATION BOUNDARY (contract INV-17).
    #
    # The contract spells this out because the neighbouring feature does the
    # opposite: a knowledge library's department decides who can see it
    # (`PermissionService._implicit_department_library_level`), while this one
    # only groups rows in the admin list. Nothing on the injection path may read
    # this column, and no permission resolver may be derived from it -- doing so
    # would both violate the invariant and silently narrow which terms reach a
    # conversation.
    department_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            nullable=True,
            index=True,
            comment='归属部门ID; 仅用于管理页筛选, 不参与鉴权与注入范围 (INV-17)',
        ),
    )
    create_user_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, comment='创建人ID'),
    )
    create_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间'),
    )
    update_time: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT, comment='更新时间'),
    )
