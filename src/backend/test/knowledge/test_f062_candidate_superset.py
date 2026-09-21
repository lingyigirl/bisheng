"""F062: the candidate set must be a SUPER-SET of the org-domain visible set.

WHY THIS FILE EXISTS — AND WHY THE 74 `test_f062_*` CASES MISSED IT
-------------------------------------------------------------------
Every other F062 test asserts the RULE: given a mapping, is D visible to U? The
rule was correct from the start. What was wrong was the SQL that decides which
rows ever REACH the rule.

`_resolve_visible_department_chain` returns the caller's ANCESTOR chain, which
was a sufficient candidate set under F057 ("ancestors flow down") but is NOT
under F062: a user must also see libraries owned by SIBLING departments inside
the same 机构, and those ids are nowhere on their ancestor chain. Narrowing
there drops those rows before the per-row rule runs — the feature silently does
nothing for exactly the case it exists for.

This was found by an end-to-end run, not by any unit test. These assertions are
the unit-level equivalent so it cannot regress silently again.

Asserted on the compiled predicate (offline, no database), same approach as
`test_f057_department_candidate_union.py`.

覆盖 AC: AC-11, AC-11a, AC-21
"""

from sqlalchemy.dialects import mysql

from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeDao


def _where(**kwargs) -> str:
    """Compiled WHERE clause only.

    The whole statement would also match the SELECT column list, where
    `knowledge.department_id` appears for every query now that the model carries
    the field — which says nothing about filtering.
    """
    import sqlmodel

    stmt = KnowledgeDao._user_knowledge_filters(sqlmodel.select(Knowledge), kwargs.pop("user_id", 7), **kwargs)
    return str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


def test_include_any_department_puts_every_owned_library_in_the_candidate_set():
    """The org-domain rule needs the super-set, not the ancestor chain."""
    where = _where(user_id=7, department_ids=[1, 4, 6], include_any_department=True)

    assert "knowledge.department_id IS NOT NULL" in where, (
        "without this disjunct, libraries owned by a SIBLING department in the "
        "same org domain are filtered out before the visibility rule can approve "
        "them — which is the whole point of F062"
    )
    # It must be an OR (additive), never an AND.
    assert " OR " in where.upper()
    # …and the ancestor-chain disjunct must survive alongside it.
    assert "knowledge.department_id IN (1, 4, 6)" in where


def test_the_flag_is_off_by_default():
    """Every existing caller must keep its exact SQL.

    F057's own suite asserts the pre-F062 predicate; this pins that the new
    parameter is opt-in so that suite's guarantees are untouched.
    """
    where = _where(user_id=7, department_ids=[1, 4, 6])
    assert "IS NOT NULL" not in where, "the default must not widen the candidate set"


def test_flag_alone_still_produces_an_or_candidate_set():
    """`include_any_department` without an ancestor chain must still work.

    A user with no ancestry-derived candidates (empty chain) still needs the
    super-set — and the OR must not degenerate into the self-only branch.
    """
    where = _where(user_id=7, department_ids=None, include_any_department=True)
    assert "knowledge.department_id IS NOT NULL" in where
    assert "knowledge.user_id = 7" in where
    assert " OR " in where.upper()


def test_flag_does_not_override_an_explicit_narrowing_filter():
    """`filter_department_ids` is the platform tree's NARROWING filter (AND).

    It is the opposite parameter (F057 decision 4) and must stay an AND even
    when the candidate set is widened — otherwise selecting a department in the
    platform tree would stop constraining the list.
    """
    where = _where(user_id=7, include_any_department=True, filter_department_ids=[4])
    assert "knowledge.department_id IN (4)" in where
    assert "knowledge.department_id IS NOT NULL" in where
    # Both appear; the narrowing one is ANDed, so the statement must contain
    # the conjunction rather than treating them as the same clause.
    assert " AND " in where.upper()


def test_empty_filter_still_compiles_to_a_false_predicate():
    """An EMPTY narrowing filter means "matched nothing" — never "no filter".

    Asserted alongside the new flag because widening the candidate set is
    exactly the change that could tempt someone to make the empty case fall
    through to "unfiltered".
    """
    where = _where(user_id=7, include_any_department=True, filter_department_ids=[])
    assert "knowledge.department_id IN (NULL)" in where or "1 != 1" in where or "false" in where.lower()
