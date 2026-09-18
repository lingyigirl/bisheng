"""F057: department-owned libraries join the candidate OR — never narrow it.

The list endpoint builds its candidate set as an OR of {explicit FGA grants,
libraries the user created}. Department ownership adds a third disjunct. The
distinction matters: OR keeps the change additive (a library the user can reach
today stays reachable), while AND would silently hide every library that is not
on the user's department chain — including all pre-F057 data, which is
unclassified.

These assert the compiled SQL predicate rather than a live database, so they run
offline.
"""

from sqlalchemy.dialects import mysql

from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeDao, KnowledgeTypeEnum


def _where(**kwargs) -> str:
    """Compiled WHERE clause only.

    Asserting on the whole statement would match the SELECT column list —
    `knowledge.department_id` appears there for every query now that the model
    carries the field, which says nothing about filtering.
    """
    import sqlmodel

    stmt = KnowledgeDao._user_knowledge_filters(
        sqlmodel.select(Knowledge),
        kwargs.pop("user_id", 7),
        **kwargs,
    )
    compiled = stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    return sql.split("WHERE", 1)[1] if "WHERE" in sql else ""


def test_department_ids_widen_the_or():
    """With grants present, department ownership is an extra OR branch."""
    sql = _where(
        knowledge_id_extra=[10, 11],
        knowledge_type=KnowledgeTypeEnum.NORMAL,
        department_ids=[30, 31],
    )
    assert "department_id IN (30, 31)" in sql
    # All three sources must sit under one OR — no bare AND of department_id.
    assert " OR " in sql
    assert "AND knowledge.department_id IN" not in sql


def test_omitting_department_ids_preserves_legacy_sql():
    """No department_ids → the predicate is exactly what it was before F057."""
    sql = _where(knowledge_id_extra=[10, 11], knowledge_type=KnowledgeTypeEnum.NORMAL)
    assert "department_id" not in sql


def test_empty_department_ids_is_a_noop():
    """An empty chain must not inject `IN ()` (which is invalid on some engines)."""
    sql = _where(knowledge_id_extra=[10, 11], knowledge_type=KnowledgeTypeEnum.NORMAL, department_ids=[])
    assert "department_id" not in sql


def test_narrow_filter_is_a_separate_and_clause():
    """The platform browsing filter must NARROW, not widen.

    These two parameters are opposites and must never be conflated: granting
    visibility (`department_ids`, OR) vs filtering a browsing view
    (`filter_department_ids`, AND).
    """
    sql = _where(
        knowledge_id_extra=[10, 11],
        knowledge_type=KnowledgeTypeEnum.NORMAL,
        filter_department_ids=[30, 31],
    )
    # Outside the OR group → it can only remove rows.
    assert "AND knowledge.department_id IN (30, 31)" in sql


def test_union_and_narrow_can_coexist():
    """Both set: union widens the candidate set, narrow then restricts it."""
    sql = _where(
        knowledge_id_extra=[10],
        knowledge_type=KnowledgeTypeEnum.NORMAL,
        department_ids=[30],
        filter_department_ids=[31],
    )
    assert "OR knowledge.department_id IN (30)" in sql
    assert "AND knowledge.department_id IN (31)" in sql


def test_empty_narrow_filter_yields_zero_rows_not_no_filter():
    """An EMPTY narrow filter must be a false predicate, never "no filter".

    The endpoint returns `[]` when the selected department is unknown or has no
    path. If that were treated as "no filter", a stale tree selection would
    silently widen to every library in the tenant.
    """
    sql = _where(
        knowledge_id_extra=[10],
        knowledge_type=KnowledgeTypeEnum.NORMAL,
        filter_department_ids=[],
    )
    assert "1 != 1" in sql


def _admin_where(**kwargs) -> str:
    """Compiled WHERE for the ADMIN path (`generate_all_knowledge_filter`).

    Separate helper because the admin branch builds its predicate through a
    different function than `_user_knowledge_filters` — and that difference is
    exactly where the filter went missing.
    """
    import sqlmodel
    from sqlalchemy.dialects import mysql as _mysql

    stmt = KnowledgeDao.generate_all_knowledge_filter(sqlmodel.select(Knowledge), **kwargs)
    compiled = stmt.compile(dialect=_mysql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    return sql.split("WHERE", 1)[1] if "WHERE" in sql else ""


def test_admin_path_honours_the_narrow_filter():
    """AC-16: the admin branch must narrow too.

    It did not: `aget_all_knowledge` / `generate_all_knowledge_filter` had no
    department parameter at all, so the endpoint accepted `department_id`,
    threaded it into the service, and the admin branch dropped it on the floor.
    The frontend renders the department tree for admins with no role branch, so
    "click a node, list does not move" was reachable.
    """
    sql = _admin_where(knowledge_type=KnowledgeTypeEnum.NORMAL, filter_department_ids=[30, 31])
    assert "knowledge.department_id IN (30, 31)" in sql


def test_admin_path_empty_filter_yields_zero_rows():
    """AC-15 on the admin branch: empty filter is a FALSE predicate.

    Same rule as the ReBAC path — an unknown/archived department must produce an
    empty list, never silently widen to the whole tenant.
    """
    sql = _admin_where(knowledge_type=KnowledgeTypeEnum.NORMAL, filter_department_ids=[])
    assert "1 != 1" in sql


def test_admin_path_without_filter_is_unchanged():
    """No filter → the predicate is exactly what it was before F057."""
    sql = _admin_where(knowledge_type=KnowledgeTypeEnum.NORMAL)
    assert "department_id" not in sql


def test_department_ids_apply_even_without_an_extra_grant_list():
    """Ownership must survive the self-only path — that path IS the main case.

    The clause used to sit inside an `if knowledge_id_extra:` truthiness branch,
    so a user with no FGA grants and no self-created libraries (empty
    `knowledge_id_extra`) fell through to `user_id = me` and silently lost every
    department-owned library. That is precisely the population F057 targets, so
    the rule was a no-op for its primary audience.

    Caught live by the e2e suite: the list endpoint logged `accessible_ids=0
    creator_ids=0 candidate_ids=0` for a department member and returned [].
    """
    sql = _where(user_id=7, knowledge_type=KnowledgeTypeEnum.NORMAL, department_ids=[30])
    assert "OR knowledge.department_id IN (30)" in sql
    assert "knowledge.user_id = 7" in sql
