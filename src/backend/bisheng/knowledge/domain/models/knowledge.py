from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, field_validator
from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlmodel import Column, DateTime, Field, case, delete, func, or_, select, text, update
from sqlmodel.sql.expression import Select, SelectOfScalar, col

from bisheng.common.models.base import SQLModelSerializable
from bisheng.core.database import get_async_db_session, get_sync_db_session
from bisheng.core.database.dialect_helpers import UPDATE_TIME_SERVER_DEFAULT, JsonType, name_sort_clauses
from bisheng.core.database.manager import get_database_connection
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFile, KnowledgeFileDao
from bisheng.user.domain.models.user import UserDao


class KnowledgeTypeEnum(Enum):
    QA = 1  # QAThe knowledge base upon
    NORMAL = 0  # Docly Knowledge Base
    PRIVATE = 2  # Workbench Personal Knowledge Base
    SPACE = 3  # Knowledge Space


class AuthTypeEnum(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    APPROVAL = "approval"


class KnowledgeState(Enum):
    UNPUBLISHED = 0
    PUBLISHED = 1  # Document Knowledge Base Success Status
    COPYING = 2
    REBUILDING = 3  # Status in Document Knowledge Base Reconstruction
    FAILED = 4  # Status of Documentation Knowledge Base Reconstruction Failure


class MetadataFieldType(str, Enum):
    """Metadata field type"""

    STRING = "string"
    NUMBER = "number"
    TIME = "time"

    # Case-insensitive enumeration matching
    @classmethod
    def _missing_(cls, value: Any) -> Optional["MetadataFieldType"]:
        if isinstance(value, str):
            for member in cls:
                if member.value.lower() == value.lower():
                    return member
        return None


class KnowledgeBase(SQLModelSerializable):
    user_id: int | None = Field(default=None, index=True)
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=False, server_default=text("1"), index=True, comment="Tenant ID"),
    )
    name: str = Field(index=True, min_length=1, max_length=200, description="Knowledge Base Name")
    type: int = Field(
        index=False,
        default=KnowledgeTypeEnum.NORMAL.value,
        description="Knowledge Base Type, value from KnowledgeTypeEnum",
    )
    description: str | None = Field(default=None, index=True)
    model: str | None = Field(default=None, index=False)
    collection_name: str | None = Field(default=None, index=False)
    index_name: str | None = Field(default=None, index=False)
    state: int | None = Field(
        index=False, default=KnowledgeState.PUBLISHED.value, description="value from KnowledgeState"
    )
    is_released: bool = Field(default=False, description="is released to knowledge space square")
    auth_type: AuthTypeEnum = Field(default=AuthTypeEnum.PUBLIC, description="Authentication Type")
    is_shared: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,
            nullable=False,
            server_default=text("0"),
            comment="F017: Root resource shared to all children (mirrors FGA shared_with tuples)",
        ),
    )
    auto_tag_enabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0"), comment="是否启用自动标签"),
    )
    auto_tag_library_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True, index=True, comment="绑定的自动标签库ID"),
    )
    # F055: risk-compliance KB — retrieval-time weighting and priority.
    # weight=0 excludes the KB from multi-KB recall (spec §3); priority=1 is the
    # high-priority group consulted first (design decision 3).
    weight: float = Field(
        default=1.0,
        sa_column=Column(Float, nullable=False, server_default=text("1"), comment="知识库权重, 检索排序用, F055"),
    )
    priority: int = Field(
        default=0,
        sa_column=Column(
            Integer, nullable=False, server_default=text("0"), comment="知识库调用优先级 0=普通 1=优先, F055"
        ),
    )

    # F057: department ownership for organization libraries (type=0/1).
    # NULL = unclassified — NOT "global". The read-time rule ADDS an ownership
    # grant on top of FGA visibility, so unclassified keeps today's access.
    # Intentionally a plain nullable int, NOT a ForeignKey: the "deleting a
    # department unclassifies its libraries" behaviour is applied by the
    # platform, and a dangling id degrades safely (no department found → no
    # ownership grant). See v2_7_0_f057_knowledge_department.py for the full
    # rationale, incl. the DM8/ALTER-TABLE constraint risk.
    department_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            nullable=True,
            index=True,
            comment="F057 所属部门ID 归属:所属部门及其下级部门可见;NULL=未分类不参与部门可见性",
        ),
    )
    metadata_fields: list[dict] | None = Field(
        default=None,
        sa_column=Column(JsonType, nullable=True),
        description="Metadata Field Configuration for Knowledge Base",
    )
    create_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    )
    update_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime, nullable=False, server_default=UPDATE_TIME_SERVER_DEFAULT)
    )

    @field_validator("model", mode="before")
    @classmethod
    def convert_model(cls, v: Any) -> str:
        if isinstance(v, int):
            v = str(v)
        return v


class Knowledge(KnowledgeBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class _KnowledgeIdTextExpression(FunctionElement):
    """将 knowledge.id 转成字符串表达式并按数据库方言处理 collation。"""

    type = String()
    inherit_cache = True


@compiles(_KnowledgeIdTextExpression, "mysql")
def _compile_knowledge_id_text_mysql(element, compiler, **kw):
    # MySQL 连接的 collation 可能是 0900_ai_ci 而 business_id 是 unicode_ci。
    # 仅给 CAST 结果对齐字段 collation 避免影响连接配置和 business_id 索引。
    inner = compiler.process(element.clauses.clauses[0], **kw)
    return f"CAST({inner} AS CHAR) COLLATE utf8mb4_unicode_ci"


@compiles(_KnowledgeIdTextExpression)
def _compile_knowledge_id_text_default(element, compiler, **kw):
    # DM8 等数据库不支持 MySQL 字符集名 保留原始 CAST 行为。
    inner = compiler.process(element.clauses.clauses[0], **kw)
    return f"CAST({inner} AS CHAR)"


class KnowledgeRead(KnowledgeBase):
    id: int
    user_name: str | None = None
    copiable: bool | None = None
    is_pinned: bool | None = False
    permission_ids: list[str] | None = None


class KnowledgeUpdate(BaseModel):
    knowledge_id: int
    name: str | None = None
    description: str | None = None
    # F055: None = unchanged; weight=0 excludes the KB from multi-KB recall.
    # Negative weights would invert the rrf_score x weight ordering — rejected.
    # priority is two-level (design decision 3).
    weight: float | None = Field(default=None, ge=0)
    priority: int | None = Field(default=None, ge=0, le=1)


class KnowledgeCreate(BaseModel):
    user_id: int | None = Field(default=None, index=True)
    name: str = Field(index=True, min_length=1, max_length=200, description="Knowledge Base Name")
    type: int = Field(
        index=False,
        default=KnowledgeTypeEnum.NORMAL.value,
        description="Knowledge Base Type, value from KnowledgeTypeEnum",
    )
    description: str | None = Field(default=None, index=True)
    model: str | None = Field(default=None, index=False)
    collection_name: str | None = Field(default=None, index=False)
    index_name: str | None = Field(default=None, index=False)
    state: int | None = Field(
        index=False, default=KnowledgeState.PUBLISHED.value, description="value from KnowledgeState"
    )
    is_released: bool = Field(default=False, description="is released to knowledge space square")
    auth_type: AuthTypeEnum = Field(default=AuthTypeEnum.PUBLIC, description="Authentication Type")
    is_shared: bool = Field(default=False)
    auto_tag_enabled: bool = Field(default=False)
    auto_tag_library_id: int | None = None
    metadata_fields: list[dict] | None = Field(
        default=None, description="Metadata Field Configuration for Knowledge Base"
    )
    is_partition: bool | None = None

    @field_validator("model", mode="before")
    @classmethod
    def convert_model(cls, v: Any) -> str:
        if isinstance(v, int):
            v = str(v)
        return v


class KnowledgeDao(KnowledgeBase):
    @classmethod
    def insert_one(cls, data: Knowledge) -> Knowledge:
        with get_sync_db_session() as session:
            session.add(data)
            session.commit()
            session.refresh(data)
            return data

    @classmethod
    async def async_insert_one(cls, data: Knowledge) -> Knowledge:
        async with get_async_db_session() as session:
            session.add(data)
            await session.commit()
            await session.refresh(data)
            return data

    @classmethod
    def update_one(cls, data: Knowledge) -> Knowledge:
        with get_sync_db_session() as session:
            session.add(data)
            session.commit()
            session.refresh(data)
            return data

    @classmethod
    async def aupdate_one(cls, data: Knowledge) -> Knowledge:
        async with get_async_db_session() as session:
            session.add(data)
            await session.commit()
            await session.refresh(data)
            return data

    @classmethod
    async def async_update_state(cls, knowledge_id: int, state: KnowledgeState, update_time: datetime | None = None):
        async with get_async_db_session() as session:
            statement = update(Knowledge).where(col(Knowledge.id) == knowledge_id)
            statement = statement.values(state=state.value, update_time=update_time or datetime.now())
            await session.exec(statement)
            await session.commit()

    @classmethod
    def update_state(cls, knowledge_id: int, state: KnowledgeState, update_time: datetime | None = None):
        with get_sync_db_session() as session:
            statement = update(Knowledge).where(col(Knowledge.id) == knowledge_id)
            statement = statement.values(state=state.value, update_time=update_time or datetime.now())
            session.exec(statement)
            session.commit()

    @classmethod
    def update_knowledge_update_time(cls, knowledge: Knowledge):
        statement = update(Knowledge).where(Knowledge.id == knowledge.id).values(update_time=text("NOW()"))
        with get_sync_db_session() as session:
            session.exec(statement)
            session.commit()

    @classmethod
    async def async_update_knowledge_update_time_by_id(cls, knowledge_id: int):
        statement = update(Knowledge).where(col(Knowledge.id) == knowledge_id).values(update_time=text("NOW()"))
        async with get_async_db_session() as session:
            await session.exec(statement)
            await session.commit()

    @classmethod
    def query_by_id(cls, knowledge_id: int) -> Knowledge:
        with get_sync_db_session() as session:
            return session.get(Knowledge, knowledge_id)

    @classmethod
    async def aquery_by_id(cls, knowledge_id: int) -> Knowledge:
        async with get_async_db_session() as session:
            return await session.get(Knowledge, knowledge_id)

    @classmethod
    async def async_query_by_id(cls, knowledge_id: int) -> Knowledge:
        async with get_async_db_session() as session:
            result = await session.execute(select(Knowledge).where(Knowledge.id == knowledge_id))
            return result.scalars().first()

    @classmethod
    def get_list_by_ids(cls, ids: list[int]) -> list[Knowledge]:
        with get_sync_db_session() as session:
            return session.exec(select(Knowledge).where(Knowledge.id.in_(ids))).all()

    @classmethod
    async def aget_list_by_ids(cls, ids: list[int]) -> list[Knowledge]:
        async with get_async_db_session() as session:
            result = await session.exec(select(Knowledge).where(col(Knowledge.id).in_(ids)))
            return result.all()

    @classmethod
    async def aget_knowledge_ids_created_by(
        cls,
        user_id: int,
        knowledge_type: KnowledgeTypeEnum,
    ) -> list[int]:
        """当前用户在指定类型下创建的知识库主键，用于与 OpenFGA can_read 列表取并集。"""
        async with get_async_db_session() as session:
            stmt = select(Knowledge.id).where(
                Knowledge.user_id == user_id,
                Knowledge.type == knowledge_type.value,
            )
            result = await session.exec(stmt)
            rows = result.all()
        out: list[int] = []
        for row in rows:
            if row is None:
                continue
            rid = row[0] if isinstance(row, tuple) else row
            out.append(int(rid))
        return out

    @classmethod
    def _user_knowledge_filters(
        cls,
        statement: Any,
        user_id: int,
        knowledge_id_extra: list[int] = None,
        knowledge_type: KnowledgeTypeEnum = None,
        name: str = None,
        page: int = 0,
        limit: int = 0,
        filter_knowledge: list[int] = None,
        department_ids: list[int] = None,
        filter_department_ids: list[int] = None,
        include_any_department: bool = False,
    ) -> Union[Select, SelectOfScalar]:
        # F057: department ownership is an ADDITIONAL visibility source
        # (visible = FGA grant OR own OR department-owned), so it joins the same
        # OR and must never become an AND.
        #
        # It is applied OUTSIDE the `knowledge_id_extra` check on purpose. That
        # check is a truthiness test, and the most common department member has
        # NO FGA grants and NO self-created libraries — so `knowledge_id_extra`
        # arrives empty and the `else` branch would select `user_id = me` alone,
        # silently dropping ownership. That is exactly the population this
        # feature exists for, so nesting it under the truthy branch made the
        # whole rule a no-op for its primary audience.
        if knowledge_id_extra or department_ids or include_any_department:
            candidate_clauses = [Knowledge.user_id == user_id]
            if knowledge_id_extra:
                candidate_clauses.append(Knowledge.id.in_(knowledge_id_extra))
            if department_ids:
                candidate_clauses.append(Knowledge.department_id.in_(department_ids))
            if include_any_department:
                # F062: the candidate set must be a SUPER-SET of the visible set.
                #
                # F057's `department_ids` (the caller's ANCESTOR chain) was a
                # sufficient candidate set only because its rule was
                # "ancestors flow down". F062 lets a user see libraries owned by
                # departments that are NOT on that chain — siblings inside the
                # same 机构 domain — so narrowing here would DROP those rows
                # before the (correct) per-row rule ever ran, and the feature
                # would silently not work for exactly the case it exists for.
                #
                # "Every department-owned library" is the cheap super-set;
                # `_scan_visible_knowledge` already filters per row, so
                # precision belongs there, not here (design 决策 7).
                candidate_clauses.append(Knowledge.department_id.is_not(None))
            statement = statement.where(or_(*candidate_clauses))
        else:
            statement = statement.where(Knowledge.user_id == user_id)
        if filter_knowledge:
            statement = statement.where(Knowledge.id.in_(filter_knowledge))
        # F057 platform tree: NARROW to the departments the operator clicked.
        # Deliberately an AND, the opposite of `department_ids` above — that one
        # grants visibility (/workspace, additive), this one filters a browsing
        # view. The caller passes the whole selected subtree (resolved from
        # `department.path`), so "include sub-departments" is a caller concern.
        #
        # `is not None`, NOT truthiness: an EMPTY list means "the filter matched
        # no department" and must yield zero rows. Treating it as "no filter"
        # would silently widen a stale tree selection to the whole tenant.
        # `in_([])` compiles to a false predicate, which is exactly right here.
        if filter_department_ids is not None:
            statement = statement.where(Knowledge.department_id.in_(filter_department_ids))
        # 使用 is not None：避免 NORMAL=0 被当作「未传类型」而漏掉 type 条件
        if knowledge_type is not None:
            statement = statement.where(Knowledge.type == knowledge_type.value)
        if name:
            conditions = [col(Knowledge.name).like(f"%{name}%"), col(Knowledge.description).like(f"%{name}%")]

            file_knowledge_ids = KnowledgeFileDao.get_knowledge_ids_by_name(name)
            if file_knowledge_ids:
                conditions.append(Knowledge.id.in_(file_knowledge_ids))

            if conditions:
                statement = statement.where(or_(*conditions))

        if page and limit:
            statement = statement.offset((page - 1) * limit).limit(limit)
        return statement

    @classmethod
    def get_user_knowledge(
        cls,
        user_id: int,
        knowledge_id_extra: list[int] = None,
        knowledge_type: KnowledgeTypeEnum = None,
        name: str = None,
        page: int = 0,
        limit: int = 10,
        filter_knowledge: list[int] = None,
    ) -> list[Knowledge]:
        statement = select(Knowledge)

        statement = cls._user_knowledge_filters(
            statement, user_id, knowledge_id_extra, knowledge_type, name, page, limit, filter_knowledge
        )

        statement = statement.order_by(Knowledge.update_time.desc())
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    async def aget_user_knowledge(
        cls,
        user_id: int,
        knowledge_id_extra: list[int] = None,
        knowledge_type: KnowledgeTypeEnum = None,
        name: str = None,
        sort_by: str = "update_time",
        page: int = 0,
        limit: int = 10,
        filter_knowledge: list[int] = None,
        preferred_ids: list[int] | None = None,
        cursor: Sequence | None = None,
        department_ids: list[int] = None,
        filter_department_ids: list[int] = None,
        include_any_department: bool = False,
    ) -> list[Knowledge]:
        """List user-visible knowledge bases.

        F027: when ``cursor`` is provided, the OFFSET path is bypassed and a
        keyset WHERE predicate is injected for ``sort_by`` ∈
        ``{create_time, update_time}``. ``preferred_ids`` pinning is ignored
        when ``cursor`` is set (pinning is a first-page-only feature).

        F057: ``department_ids`` widens the candidate OR with libraries owned by
        those departments (the caller passes the ids on the current user's
        ancestor chain). It is additive — omit it to keep pre-F057 behaviour.

        F062: ``include_any_department`` widens it further to EVERY
        department-owned library. Required because the org-domain rule can reach
        departments outside the caller's ancestor chain (siblings within the same
        机构), which ``department_ids`` alone would filter out before the rule
        ran. Off by default so every existing caller keeps its exact SQL.
        """
        statement = select(Knowledge)

        # When using cursor-based keyset, suppress OFFSET inside _user_knowledge_filters.
        page_for_filter = 0 if cursor is not None else page
        statement = cls._user_knowledge_filters(
            statement,
            user_id,
            knowledge_id_extra,
            knowledge_type,
            name,
            page_for_filter,
            limit,
            filter_knowledge,
            department_ids,
            filter_department_ids,
            include_any_department,
        )
        if cursor is not None:
            # Cursor mode: append keyset WHERE and LIMIT only.
            statement = cls._apply_keyset_where(statement, sort_by, cursor)
            statement = statement.limit(limit)
        elif not page and limit:
            # The first cursor page uses page=0 and cursor=None.  Keep it
            # bounded instead of accidentally returning every candidate row.
            statement = statement.limit(limit)

        order_clauses = []
        # Pinning is a first-page-only UX; suppress it when continuing a cursor.
        if preferred_ids and cursor is None:
            # Float pinned ids to the top of the global sort so they land on page 1
            # no matter what sort_by is, then fall back to the requested sort.
            order_clauses.append(case((Knowledge.id.in_(preferred_ids), 0), else_=1))
        if sort_by == "create_time":
            order_clauses.append(Knowledge.create_time.desc())
            order_clauses.append(Knowledge.id.desc())  # tie-breaker for keyset
        elif sort_by == "update_time":
            order_clauses.append(Knowledge.update_time.desc())
            order_clauses.append(Knowledge.id.desc())
        elif sort_by == "name":
            db_conn = await get_database_connection()
            order_clauses.extend(name_sort_clauses(db_conn.async_engine.dialect.name))
        if order_clauses:
            statement = statement.order_by(*order_clauses)
        async with get_async_db_session() as session:
            return (await session.exec(statement)).all()

    @classmethod
    def _apply_keyset_where(cls, statement, sort_by: str, cursor: Sequence):
        """F027: inject keyset WHERE for cursor continuation.

        Only valid for sort_by ∈ {create_time, update_time}; name sort uses
        pseudo-cursor (offset) per AD-15.
        """
        from bisheng.database.utils.keyset import build_keyset_where

        if sort_by == "create_time":
            sort_cols = (Knowledge.create_time, Knowledge.id)
        elif sort_by == "update_time":
            sort_cols = (Knowledge.update_time, Knowledge.id)
        else:
            # name sort or unknown — cursor mode is invalid; service should
            # have routed this to the offset path. Defensive no-op.
            return statement
        return statement.where(build_keyset_where(sort_cols, tuple(cursor), descending=True))

    @classmethod
    def count_user_knowledge(
        cls,
        user_id: int,
        knowledge_id_extra: list[int] = None,
        knowledge_type: KnowledgeTypeEnum = None,
        name: str = None,
    ) -> int:
        statement = select(func.count(Knowledge.id))
        statement = cls._user_knowledge_filters(statement, user_id, knowledge_id_extra, knowledge_type, name)
        with get_sync_db_session() as session:
            return session.scalar(statement)

    @classmethod
    async def acount_user_knowledge(
        cls,
        user_id: int,
        knowledge_id_extra: list[int] = None,
        knowledge_type: KnowledgeTypeEnum = None,
        name: str = None,
    ) -> int:
        statement = select(func.count(Knowledge.id))
        statement = cls._user_knowledge_filters(statement, user_id, knowledge_id_extra, knowledge_type, name)
        async with get_async_db_session() as session:
            return await session.scalar(statement)

    @classmethod
    def count_by_filter(cls, filters: list[Any]) -> int:
        with get_sync_db_session() as session:
            return session.scalar(select(Knowledge.id).where(*filters))

    @classmethod
    def judge_knowledge_permission(cls, user_name: str, knowledge_ids: list[int]) -> list[Knowledge]:
        """Filter knowledge_ids to those the user can read.

        F008 follow-up: delegates to ReBAC via PermissionService instead of
        the legacy role_access table. Admin still gets the full set; owners
        and tenant-admin scope are picked up by list_accessible_ids' implicit
        scope expansion.
        """
        if not knowledge_ids:
            return []
        user_info = UserDao.get_user_by_username(user_name)
        if not user_info:
            return []

        from bisheng.permission.domain.services.owner_service import _run_async_safe
        from bisheng.permission.domain.services.permission_service import PermissionService
        from bisheng.user.domain.services.auth import LoginUser

        login_user = LoginUser.init_login_user_sync(
            user_id=user_info.user_id,
            user_name=user_name,
        )
        accessible_ids = _run_async_safe(
            PermissionService.list_accessible_ids(
                user_id=login_user.user_id,
                relation="can_read",
                object_type="knowledge_library",
                login_user=login_user,
            ),
        )
        if accessible_ids is None:
            return cls.get_list_by_ids(knowledge_ids)

        accessible_set = {int(x) for x in accessible_ids}
        filtered = [kid for kid in knowledge_ids if int(kid) in accessible_set]
        return cls.get_list_by_ids(filtered) if filtered else []

    @classmethod
    async def ajudge_knowledge_permission(cls, user_name: str, knowledge_ids: list[int]) -> list[Knowledge]:
        """Async variant of :meth:`judge_knowledge_permission`. Same semantics."""
        if not knowledge_ids:
            return []
        user_info = await UserDao.aget_user_by_username(user_name)
        if not user_info:
            return []

        from bisheng.permission.domain.services.permission_service import PermissionService
        from bisheng.user.domain.services.auth import LoginUser

        login_user = await LoginUser.init_login_user(
            user_id=user_info.user_id,
            user_name=user_name,
        )
        accessible_ids = await PermissionService.list_accessible_ids(
            user_id=login_user.user_id,
            relation="can_read",
            object_type="knowledge_library",
            login_user=login_user,
        )
        if accessible_ids is None:
            return await cls.aget_list_by_ids(knowledge_ids)

        accessible_set = {int(x) for x in accessible_ids}
        filtered = [kid for kid in knowledge_ids if int(kid) in accessible_set]
        return await cls.aget_list_by_ids(filtered) if filtered else []

    @classmethod
    def filter_knowledge_by_ids(
        cls, knowledge_ids: list[int], keyword: str = None, page: int = 0, limit: int = 0
    ) -> (list[Knowledge], int):
        """
        Based on keywords and knowledge baseidFilter out the corresponding knowledge base

        """
        statement = select(Knowledge)
        count_statement = select(func.count(Knowledge.id))
        if knowledge_ids:
            statement = statement.where(Knowledge.id.in_(knowledge_ids))
            count_statement = count_statement.where(Knowledge.id.in_(knowledge_ids))
        if keyword:
            statement = statement.where(
                or_(Knowledge.name.like("%" + keyword + "%"), Knowledge.description.like("%" + keyword + "%"))
            )
            count_statement = count_statement.where(
                or_(Knowledge.name.like("%" + keyword + "%"), Knowledge.description.like("%" + keyword + "%"))
            )
        if page and limit:
            statement = statement.offset((page - 1) * limit).limit(limit)
        statement = statement.order_by(Knowledge.update_time.desc())
        with get_sync_db_session() as session:
            return session.exec(statement).all(), session.scalar(count_statement)

    @classmethod
    def generate_all_knowledge_filter(
        cls,
        statement,
        name: str = None,
        knowledge_type: KnowledgeTypeEnum = None,
        filter_department_ids: list[int] = None,
    ):
        if knowledge_type is not None:
            statement = statement.where(Knowledge.type == knowledge_type.value)

        # F057 platform tree: the admin path needs the same NARROWING filter as
        # the ReBAC path. Without it an administrator clicking a department node
        # saw the list not move at all — the parameter was accepted by the
        # endpoint and then silently dropped here. Same `is not None` rule as
        # `_user_knowledge_filters`: an EMPTY list must yield zero rows, never
        # "no filter", or a stale tree selection widens to the whole tenant.
        if filter_department_ids is not None:
            statement = statement.where(Knowledge.department_id.in_(filter_department_ids))

        if name:
            conditions = [col(Knowledge.name).like(f"%{name}%"), col(Knowledge.description).like(f"%{name}%")]

            file_knowledge_ids = KnowledgeFileDao.get_knowledge_ids_by_name(name)
            if file_knowledge_ids:
                conditions.append(Knowledge.id.in_(file_knowledge_ids))

            if conditions:
                statement = statement.where(or_(*conditions))

        return statement

    @classmethod
    def get_all_knowledge(
        cls, name: str = None, knowledge_type: KnowledgeTypeEnum = None, page: int = 0, limit: int = 0
    ) -> list[Knowledge]:
        statement = select(Knowledge)
        statement = cls.generate_all_knowledge_filter(statement, name=name, knowledge_type=knowledge_type)

        if page and limit:
            statement = statement.offset((page - 1) * limit).limit(limit)
        statement = statement.order_by(Knowledge.update_time.desc())
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    async def aget_all_knowledge(
        cls,
        name: str = None,
        knowledge_type: KnowledgeTypeEnum = None,
        sort_by: str = "update_time",
        page: int = 0,
        limit: int = 0,
        preferred_ids: list[int] | None = None,
        cursor: Sequence | None = None,
        filter_department_ids: list[int] = None,
    ) -> list[Knowledge]:
        """Admin/scoped-super-admin path; same cursor semantics as
        ``aget_user_knowledge`` (F027 AD-15).

        F057: ``filter_department_ids`` narrows the browsing view, exactly as it
        does on the ReBAC path. Admins bypass *visibility* checks, but the
        platform tree filter is a view concern and must still apply — the
        frontend renders the department tree for admins with no role branch.
        """
        statement = select(Knowledge)
        statement = cls.generate_all_knowledge_filter(
            statement,
            name=name,
            knowledge_type=knowledge_type,
            filter_department_ids=filter_department_ids,
        )

        if cursor is not None:
            statement = cls._apply_keyset_where(statement, sort_by, cursor)
            statement = statement.limit(limit)
        elif page and limit:
            statement = statement.offset((page - 1) * limit).limit(limit)
        elif limit:
            statement = statement.limit(limit)

        order_clauses = []
        if preferred_ids and cursor is None:
            order_clauses.append(case((Knowledge.id.in_(preferred_ids), 0), else_=1))
        if sort_by == "create_time":
            order_clauses.append(Knowledge.create_time.desc())
            order_clauses.append(Knowledge.id.desc())
        elif sort_by == "update_time":
            order_clauses.append(Knowledge.update_time.desc())
            order_clauses.append(Knowledge.id.desc())
        elif sort_by == "name":
            db_conn = await get_database_connection()
            order_clauses.extend(name_sort_clauses(db_conn.async_engine.dialect.name))
        if order_clauses:
            statement = statement.order_by(*order_clauses)
        async with get_async_db_session() as session:
            return (await session.exec(statement)).all()

    @classmethod
    def count_all_knowledge(cls, name: str = None, knowledge_type: KnowledgeTypeEnum = None) -> int:
        statement = select(func.count(Knowledge.id))
        statement = cls.generate_all_knowledge_filter(statement, name=name, knowledge_type=knowledge_type)
        with get_sync_db_session() as session:
            return session.scalar(statement)

    @classmethod
    async def acount_all_knowledge(cls, name: str = None, knowledge_type: KnowledgeTypeEnum = None) -> int:
        statement = select(func.count(Knowledge.id))
        statement = cls.generate_all_knowledge_filter(statement, name=name, knowledge_type=knowledge_type)
        async with get_async_db_session() as session:
            return await session.scalar(statement)

    @classmethod
    def update_knowledge_list(cls, knowledge_list: list[Knowledge]):
        with get_sync_db_session() as session:
            for knowledge in knowledge_list:
                session.add(knowledge)
            session.commit()

    @classmethod
    def get_knowledge_by_name(
        cls,
        name: str,
        user_id: int = 0,
        knowledge_type: KnowledgeTypeEnum | int | None = None,
    ) -> Knowledge | None:
        """Get a knowledge base by its exact name, owner, and optional type."""
        statement = select(Knowledge).where(Knowledge.name == name)
        if user_id:
            statement = statement.where(Knowledge.user_id == user_id)
        if knowledge_type is not None:
            type_value = knowledge_type.value if isinstance(knowledge_type, KnowledgeTypeEnum) else knowledge_type
            statement = statement.where(Knowledge.type == type_value)
        with get_sync_db_session() as session:
            return session.exec(statement).first()

    @classmethod
    async def aget_knowledge_by_name(
        cls,
        name: str,
        user_id: int = 0,
        knowledge_type: KnowledgeTypeEnum | int | None = None,
    ) -> Knowledge | None:
        """Async variant of :meth:`get_knowledge_by_name`."""
        statement = select(Knowledge).where(Knowledge.name == name)
        if user_id:
            statement = statement.where(Knowledge.user_id == user_id)
        if knowledge_type is not None:
            type_value = knowledge_type.value if isinstance(knowledge_type, KnowledgeTypeEnum) else knowledge_type
            statement = statement.where(Knowledge.type == type_value)
        async with get_async_db_session() as session:
            return (await session.exec(statement)).first()

    @classmethod
    def delete_knowledge(cls, knowledge_id: int, only_clear: bool = False):
        """
        Delete or empty the knowledge base
        """
        # <g id="Bold">Medical Treatment:</g>knowledge file
        with get_sync_db_session() as session:
            session.exec(delete(KnowledgeFile).where(KnowledgeFile.knowledge_id == knowledge_id))
            # Do not delete knowledge base records when clearing the knowledge base
            if not only_clear:
                session.exec(delete(Knowledge).where(Knowledge.id == knowledge_id))
            session.commit()

    @classmethod
    async def async_delete_knowledge(cls, knowledge_id: int, only_clear: bool = False):
        async with get_async_db_session() as session:
            await session.exec(delete(KnowledgeFile).where(col(KnowledgeFile.knowledge_id) == knowledge_id))
            if not only_clear:
                await session.exec(delete(Knowledge).where(col(Knowledge.id) == knowledge_id))
            await session.commit()

    @classmethod
    def get_knowledge_by_time_range(
        cls, start_time: datetime, end_time: datetime, page: int = 0, page_size: int = 0
    ) -> list[Knowledge]:
        """Get a list of knowledge bases based on the creation timeframe"""
        statement = select(Knowledge).where(Knowledge.create_time >= start_time, Knowledge.create_time < end_time)
        if page and page_size:
            statement = statement.offset((page - 1) * page_size).limit(page_size)
        statement = statement.order_by(col(Knowledge.id).asc())
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    def get_first_knowledge(cls) -> Knowledge | None:
        """Get the first knowledge base"""
        statement = select(Knowledge).order_by(col(Knowledge.id).asc()).limit(1)
        with get_sync_db_session() as session:
            return session.exec(statement).first()

    # ─── Knowledge Space specific ────────────────────────────────────────────

    @classmethod
    def _exclude_department_spaces(cls, statement):
        from bisheng.knowledge.domain.models.department_knowledge_space import DepartmentKnowledgeSpace

        return statement.where(~col(Knowledge.id).in_(select(DepartmentKnowledgeSpace.space_id)))

    @classmethod
    def count_spaces_by_user(cls, user_id: int, exclude_department_spaces: bool = False) -> int:
        """Count how many Knowledge Spaces a user has created"""
        statement = select(func.count(Knowledge.id)).where(
            Knowledge.user_id == user_id, Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        if exclude_department_spaces:
            statement = cls._exclude_department_spaces(statement)
        with get_sync_db_session() as session:
            return session.scalar(statement)

    @classmethod
    async def async_count_spaces_by_user(cls, user_id: int, exclude_department_spaces: bool = False) -> int:
        """Async: Count how many Knowledge Spaces a user has created"""
        statement = select(func.count(Knowledge.id)).where(
            Knowledge.user_id == user_id, Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        if exclude_department_spaces:
            statement = cls._exclude_department_spaces(statement)
        async with get_async_db_session() as session:
            return await session.scalar(statement)

    @classmethod
    def get_spaces_by_user(cls, user_id: int, order_by: str = "update_time") -> list[Knowledge]:
        """Get all Knowledge Spaces created by a user"""
        statement = select(Knowledge).where(
            Knowledge.user_id == user_id, Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        statement = cls._apply_space_order(statement, order_by)
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    async def async_get_spaces_by_user(cls, user_id: int, order_by: str = "update_time") -> list[Knowledge]:
        """Async: Get all Knowledge Spaces created by a user"""
        statement = select(Knowledge).where(
            Knowledge.user_id == user_id, Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        statement = cls._apply_space_order(statement, order_by)
        async with get_async_db_session() as session:
            result = await session.exec(statement)
            return result.all()

    @classmethod
    def get_spaces_by_ids(cls, space_ids: list[int], order_by: str = "update_time") -> list[Knowledge]:
        """Get Knowledge Spaces by a list of IDs"""
        if not space_ids:
            return []
        statement = select(Knowledge).where(
            Knowledge.id.in_(space_ids), Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        statement = cls._apply_space_order(statement, order_by)
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    async def async_get_spaces_by_ids(cls, space_ids: list[int], order_by: str = "update_time") -> list[Knowledge]:
        """Async: Get Knowledge Spaces by a list of IDs"""
        if not space_ids:
            return []
        statement = select(Knowledge).where(
            Knowledge.id.in_(space_ids), Knowledge.type == KnowledgeTypeEnum.SPACE.value
        )
        statement = cls._apply_space_order(statement, order_by)
        async with get_async_db_session() as session:
            result = await session.exec(statement)
            return result.all()

    @classmethod
    def get_public_spaces(cls, order_by: str = "update_time") -> list[Knowledge]:
        """Get all PUBLIC and APPROVAL Knowledge Spaces (Knowledge Square)"""
        statement = select(Knowledge).where(
            Knowledge.type == KnowledgeTypeEnum.SPACE.value,
            Knowledge.auth_type.in_([AuthTypeEnum.PUBLIC.value, AuthTypeEnum.APPROVAL.value]),
        )
        statement = cls._apply_space_order(statement, order_by)
        with get_sync_db_session() as session:
            return session.exec(statement).all()

    @classmethod
    async def async_get_public_spaces(cls, keyword: str = None, order_by: str = "update_time") -> list[Knowledge]:
        """Async: Get all PUBLIC and APPROVAL Knowledge Spaces (Knowledge Square)"""
        statement = select(Knowledge).where(
            Knowledge.type == KnowledgeTypeEnum.SPACE.value,
            Knowledge.is_released == True,  # noqa: E712 — `IS 1` is rejected by DM8; `= 1` works on both MySQL and DM8
            Knowledge.auth_type.in_([AuthTypeEnum.PUBLIC.value, AuthTypeEnum.APPROVAL.value]),
        )
        if keyword:
            statement = statement.where(
                or_(Knowledge.name.like(f"%{keyword}%"), Knowledge.description.like(f"%{keyword}%"))
            )
        statement = cls._apply_space_order(statement, order_by)
        async with get_async_db_session() as session:
            result = await session.exec(statement)
            return result.all()

    @classmethod
    def update_space(cls, space: Knowledge) -> Knowledge:
        """Persist an updated Knowledge Space record"""
        with get_sync_db_session() as session:
            session.add(space)
            session.commit()
            session.refresh(space)
            return space

    @classmethod
    async def async_update_space(cls, space: Knowledge) -> Knowledge:
        """Async: Persist an updated Knowledge Space record"""
        async with get_async_db_session() as session:
            session.add(space)
            await session.commit()
            await session.refresh(space)
            return space

    @classmethod
    async def async_get_public_spaces_paginated(
        cls,
        user_id: int,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[tuple[Any, ...]]:
        """
        Paginated query of released public/approval spaces for the Knowledge Square.
        Uses multi-table LEFT JOIN:
        - LEFT JOIN space_channel_member for current user's subscription status
        - LEFT JOIN subquery for subscriber count (status=ACTIVE)
        Returns list of tuples:
        (Knowledge, user_subscription_status, user_subscription_update_time, subscriber_count)
        """
        from bisheng.common.models.space_channel_member import (
            REJECTED_STATUS_DISPLAY_WINDOW,
            BusinessTypeEnum,
            MembershipStatusEnum,
            SpaceChannelMember,
        )

        rejection_cutoff = datetime.now() - REJECTED_STATUS_DISPLAY_WINDOW

        kid_str = _KnowledgeIdTextExpression(Knowledge.id)

        # Subquery: count unique active subscribers per space
        subscriber_subq = (
            select(
                SpaceChannelMember.business_id,
                func.count(func.distinct(SpaceChannelMember.user_id)).label("subscriber_count"),
            )
            .where(
                SpaceChannelMember.business_type == BusinessTypeEnum.SPACE,
                SpaceChannelMember.status == MembershipStatusEnum.ACTIVE,
            )
            .group_by(SpaceChannelMember.business_id)
            .subquery()
        )

        # Main query with LEFT JOINs
        query = (
            select(
                Knowledge,
                SpaceChannelMember.status.label("user_subscription_status"),
                SpaceChannelMember.update_time.label("user_subscription_update_time"),
                func.coalesce(subscriber_subq.c.subscriber_count, 0).label("subscriber_count"),
            )
            .outerjoin(
                SpaceChannelMember,
                (kid_str == SpaceChannelMember.business_id)
                & (SpaceChannelMember.business_type == BusinessTypeEnum.SPACE)
                & (SpaceChannelMember.user_id == user_id),
            )
            .outerjoin(
                subscriber_subq,
                kid_str == subscriber_subq.c.business_id,
            )
            .where(
                Knowledge.type == KnowledgeTypeEnum.SPACE.value,
                Knowledge.is_released == True,  # noqa: E712 — `IS 1` is rejected by DM8; `= 1` works on both MySQL and DM8
                Knowledge.auth_type.in_([AuthTypeEnum.PUBLIC.value, AuthTypeEnum.APPROVAL.value]),
            )
        )

        # Keyword filter
        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(
                or_(
                    Knowledge.name.like(like_pattern),
                    Knowledge.description.like(like_pattern),
                )
            )

        subscriber_count = func.coalesce(subscriber_subq.c.subscriber_count, 0)

        # Sort by lightweight membership facts only. Permission evaluation remains
        # a response-layer concern and does not affect SQL pagination.
        subscription_order = case(
            (SpaceChannelMember.status.is_(None), 0),
            (
                (SpaceChannelMember.status == MembershipStatusEnum.REJECTED)
                & (SpaceChannelMember.update_time < rejection_cutoff),
                0,
            ),
            else_=1,
        )
        query = query.order_by(
            subscription_order.asc(),
            subscriber_count.desc(),
            func.coalesce(Knowledge.update_time, Knowledge.create_time).desc(),
            Knowledge.id.asc(),
        )

        # Pagination
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        async with get_async_db_session() as session:
            result = await session.exec(query)
            return list(result.all())

    @classmethod
    async def async_count_public_spaces(cls, keyword: str | None = None) -> int:
        """Count total released public/approval spaces matching the keyword filter."""
        query = (
            select(func.count())
            .select_from(Knowledge)
            .where(
                Knowledge.type == KnowledgeTypeEnum.SPACE.value,
                Knowledge.is_released == True,  # noqa: E712 — `IS 1` is rejected by DM8; `= 1` works on both MySQL and DM8
                Knowledge.auth_type.in_([AuthTypeEnum.PUBLIC.value, AuthTypeEnum.APPROVAL.value]),
            )
        )

        if keyword:
            like_pattern = f"%{keyword}%"
            query = query.where(
                or_(
                    Knowledge.name.like(like_pattern),
                    Knowledge.description.like(like_pattern),
                )
            )

        async with get_async_db_session() as session:
            return await session.scalar(query) or 0

    @staticmethod
    def _apply_space_order(statement, order_by: str):
        if order_by == "create_time":
            return statement.order_by(Knowledge.create_time.desc())
        elif order_by == "name":
            return statement.order_by(Knowledge.name.asc())
        else:
            return statement.order_by(Knowledge.update_time.desc())
