#!/usr/bin/env python3
"""Audit department-subject ReBAC grants on organization knowledge libraries (F057).

Why this exists
---------------
F057 introduces department *ownership* for organization libraries (type=0/1):
`knowledge.department_id` grants read access to that department and its
descendants, evaluated at READ time. As part of that feature the permission
dialog stops offering "department" as a grantee for `knowledge_library`, so that
department-based access has exactly ONE source (ownership).

That change is only safe if we know what already exists. `knowledge_library`
shares its FGA type with `knowledge_space`, whose `directly_related_user_types`
includes `department#member`, and the generic authorize endpoint accepts it
(there is no `_validate_department_space_grants` equivalent for knowledge
libraries). So customers may already hold department-subject tuples that were
written through the UI.

Why it matters
--------------
Those tuples are written with the subtree EXPANDED into individual
`department:{id}#member` strings, and nothing recomputes them: no department-
change hook touches resource grants anywhere in this codebase (creating a child
department, reparenting, or archiving one triggers no recompute). A grant that
outlives its department is a leak, and — worse for F057 — an owner who changes
the library's `department_id` expecting the old department to lose access will
find it still has it, because the read-time ownership rule ADDS to the tuple
path rather than replacing it.

This script is READ-ONLY. It reports what is out there so a human can decide the
remediation; it deliberately does not revoke anything, because "delete every
department tuple" is not obviously correct — some of those grants may be load-
bearing today, and removing them would silently cut access.

What it reports
---------------
For every `knowledge_library:<id>#<relation>@department:<dept>#member` tuple:
  - the library's name / type / current `department_id` (F057 ownership, if set)
  - the department's name and whether it still exists (a missing department
    means the grant is already inert on the FGA side, but the tuple lingers)
  - an estimate of how many users the grant currently reaches, via the
    department subtree expansion the AUTHORIZE path would have produced

Summary buckets classify each tuple as:
  - `shadowed_by_ownership` — the same department is already the library's
    F057 owner, so removing the tuple loses nothing
  - `redundant_with_ownership` — a DIFFERENT department that is an ancestor of
    (or equal to) the owner, so ownership already covers those users
  - `orphan_department` — the department row is gone; the tuple grants nothing
  - `independent` — grants access ownership would NOT provide; removing it
    WOULD cut access — review these individually

How to run (from src/backend/)
------------------------------
    cd src/backend/
    PYTHONPATH=./ python scripts/audit_knowledge_library_department_tuples.py
    PYTHONPATH=./ python scripts/audit_knowledge_library_department_tuples.py --json report.json
    bash scripts/audit_knowledge_library_department_tuples.sh

Requires the `config` env var to point at the same YAML the service uses when
the default does not (see scripts/AGENTS.md §6.1).
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import sys
from collections import Counter

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlmodel import select  # noqa: E402

from bisheng.core.database import get_async_db_session  # noqa: E402
from bisheng.database.models.department import Department  # noqa: E402
from bisheng.knowledge.domain.models.knowledge import Knowledge, KnowledgeTypeEnum  # noqa: E402

logger = logging.getLogger(__name__)

_OBJECT_TYPE = "knowledge_library"
_DEPARTMENT_PREFIX = "department:"
_MEMBER_SUFFIX = "#member"

# Buckets — see the module docstring for what each means.
SHADOWED = "shadowed_by_ownership"
REDUNDANT = "redundant_with_ownership"
ORPHAN = "orphan_department"
INDEPENDENT = "independent"


def _parse_department_id(tuple_user: str) -> int | None:
    """`department:42#member` -> 42; anything else -> None."""
    if not tuple_user.startswith(_DEPARTMENT_PREFIX):
        return None
    raw = tuple_user[len(_DEPARTMENT_PREFIX):].split("#", 1)[0]
    return int(raw) if raw.isdigit() else None


def _parse_object_id(tuple_object: str) -> int | None:
    """`knowledge_library:55` -> 55."""
    _, _, raw = tuple_object.partition(":")
    return int(raw) if raw.isdigit() else None


def _is_ancestor_or_self(candidate_path: str, target_path: str) -> bool:
    """True when `candidate_path` is `target_path` or an ancestor of it.

    Both are materialized `/1/2/3/` chains with the node's own id last; the
    trailing slash is what stops `/1/2/` from matching `/1/20/`.
    """
    return bool(candidate_path) and str(target_path).startswith(str(candidate_path))


async def _collect_department_tuples() -> list[dict]:
    """Every department-subject tuple on a knowledge_library object.

    OpenFGA's read API REQUIRES an object type in the tuple_key and offers no
    reverse index, so "give me every department-subject tuple" is not a query it
    can answer. The read is therefore anchored per LIBRARY — the smaller and
    far less volatile side — with the object filter doing the heavy lifting.

    Only NORMAL and QA types are scanned: knowledge spaces carry their own
    department grants and are out of F057's scope.
    """
    from bisheng.permission.domain.services.permission_service import PermissionService

    fga = await PermissionService._aget_fga()
    if fga is None:
        raise RuntimeError("OpenFGA client unavailable — check the `config` env var and FGA settings")

    # Select ids only: the audit needs to know which libraries EXIST to anchor
    # the FGA reads, and a narrow projection keeps this cheap on large tenants.
    async with get_async_db_session() as session:
        rows = (
            await session.exec(
                select(Knowledge.id).where(
                    Knowledge.type.in_(
                        [KnowledgeTypeEnum.NORMAL.value, KnowledgeTypeEnum.QA.value]
                    )
                )
            )
        ).all()
    library_ids = [int(r[0] if isinstance(r, tuple) else r) for r in rows]

    tuples: list[dict] = []
    for knowledge_id in library_ids:
        for key in await fga.read_tuples(object=f"{_OBJECT_TYPE}:{knowledge_id}"):
            user = key.get("user", "")
            if _parse_department_id(user) is None:
                continue
            tuples.append(
                {"user": user, "relation": key.get("relation", ""), "object": key.get("object", "")}
            )
    return tuples


async def _count_users_in_subtree(department_id: int) -> int:
    """Users reachable through `department:{id}#member`, subtree included.

    The authorize path writes one tuple per department in the subtree, so this
    mirrors what that grant actually reaches. Returns 0 when the department is
    gone — an orphaned tuple is inert regardless of who used to be in it.
    """
    from bisheng.database.models.department import DepartmentDao, UserDepartment

    dept = await DepartmentDao.aget_by_id(department_id)
    if dept is None or not dept.path:
        return 0
    subtree_ids = await DepartmentDao.aget_subtree_ids(str(dept.path))
    if not subtree_ids:
        return 0

    # Deliberately NOT `UserDepartmentDao.aget_user_ids_for_department`: that
    # helper does `int(r[0])` over `select(UserDepartment.user_id)`, but SQLModel
    # returns bare scalars (not rows) for a single-column select, so it raises
    # "'int' object is not subscriptable". It is pre-existing and out of scope
    # for this audit, so the count is computed from the membership rows instead
    # — the same data, via an accessor that has no such bug.
    user_ids: set[int] = set()
    async with get_async_db_session() as session:
        rows = (
            await session.exec(
                select(UserDepartment.user_id).where(
                    UserDepartment.department_id.in_(subtree_ids)
                )
            )
        ).all()
    for row in rows:
        user_ids.add(int(row if isinstance(row, int) else row[0]))
    return len(user_ids)


async def _load_knowledge(ids: list[int]) -> dict[int, Knowledge]:
    if not ids:
        return {}
    async with get_async_db_session() as session:
        rows = (await session.exec(select(Knowledge).where(Knowledge.id.in_(ids)))).all()
    return {int(k.id): k for k in rows}


async def _load_departments(ids: list[int]) -> dict[int, Department]:
    if not ids:
        return {}
    async with get_async_db_session() as session:
        rows = (await session.exec(select(Department).where(Department.id.in_(ids)))).all()
    return {int(d.id): d for d in rows}


def _classify(entry: dict, knowledge: Knowledge | None, owner_dept: Department | None, grant_dept: Department | None) -> str:
    if grant_dept is None or not grant_dept.path:
        return ORPHAN
    if knowledge is None:
        return INDEPENDENT
    if knowledge.department_id is None:
        # No F057 ownership set: the tuple is the only department-derived access
        # there is, so removing it changes behaviour. Human call.
        return INDEPENDENT
    if int(knowledge.department_id) == int(grant_dept.id):
        return SHADOWED
    if owner_dept is not None and _is_ancestor_or_self(grant_dept.path, owner_dept.path):
        # Ownership already reaches everyone this tuple reaches.
        return REDUNDANT
    return INDEPENDENT


async def _report(apply_placeholder: bool = False, json_path: str | None = None) -> None:
    raw = await _collect_department_tuples()
    logger.info("scanned knowledge_library department tuples: %d", len(raw))

    if not raw:
        logger.info("nothing to review — no department-subject grants on organization libraries.")
        return

    knowledge_ids = [_parse_object_id(t["object"]) for t in raw]
    knowledge_ids = [k for k in knowledge_ids if k is not None]
    grant_dept_ids = [d for d in (_parse_department_id(t["user"]) for t in raw) if d is not None]

    knowledge_by_id = await _load_knowledge(sorted(set(knowledge_ids)))
    dept_by_id = await _load_departments(sorted(set(grant_dept_ids)))

    owner_dept_ids = [
        int(k.department_id) for k in knowledge_by_id.values() if k.department_id is not None
    ]
    owner_depts = await _load_departments(sorted(set(owner_dept_ids)))

    report: list[dict] = []
    buckets: Counter[str] = Counter()

    for t in raw:
        kid = _parse_object_id(t["object"])
        did = _parse_department_id(t["user"])
        knowledge = knowledge_by_id.get(kid) if kid is not None else None
        grant_dept = dept_by_id.get(did) if did is not None else None
        owner_dept = (
            owner_depts.get(int(knowledge.department_id))
            if knowledge is not None and knowledge.department_id is not None
            else None
        )
        bucket = _classify(t, knowledge, owner_dept, grant_dept)
        buckets[bucket] += 1

        reachable = await _count_users_in_subtree(did) if did is not None and grant_dept else 0
        entry = {
            "object": t["object"],
            "relation": t["relation"],
            "grantee": t["user"],
            "bucket": bucket,
            "knowledge_id": kid,
            "knowledge_name": getattr(knowledge, "name", None),
            "knowledge_type": getattr(knowledge, "type", None),
            "knowledge_department_id": getattr(knowledge, "department_id", None),
            "grant_department_id": did,
            "grant_department_name": getattr(grant_dept, "name", None),
            "grant_department_exists": grant_dept is not None,
            "estimate_users_reached": reachable,
        }
        report.append(entry)
        logger.info(
            "  %-34s %-10s dept=%-6s reach~%-5s %s",
            t["object"],
            t["relation"],
            did,
            reachable,
            bucket,
        )

    logger.info("--- summary ---")
    for bucket, count in sorted(buckets.items()):
        logger.info("  %-26s %d", bucket, count)
    independent = buckets.get(INDEPENDENT, 0)
    orphan = buckets.get(ORPHAN, 0)
    logger.info(
        "actionable: %d independent grant(s) need a decision; %d orphaned tuple(s) are already inert",
        independent,
        orphan,
    )
    logger.info("This script is READ-ONLY — nothing was modified.")

    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"summary": dict(buckets), "entries": report}, fh, ensure_ascii=False, indent=2)
        logger.info("report written to %s", json_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", dest="json_path", default=None, help="Also write a JSON report to this path.")
    args = parser.parse_args()

    async def _amain() -> None:
        from bisheng.common.services.config_service import settings
        from bisheng.core.context.manager import close_app_context, initialize_app_context

        await initialize_app_context(config=settings)
        try:
            from bisheng.core.context.tenant import bypass_tenant_filter

            with bypass_tenant_filter():
                await _report(json_path=args.json_path)
        finally:
            await close_app_context()
            gc.collect()
            await asyncio.sleep(0)

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
