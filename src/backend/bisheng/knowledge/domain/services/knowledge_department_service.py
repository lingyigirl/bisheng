"""F057: assigning an organization library to a department.

Setting `knowledge.department_id` is an AUTHORIZATION change, not metadata: the
read-time rule (`PermissionService._implicit_department_library_level`) hands
the library to every member of the owning department AND its descendants. Two
consequences shape the guards below:

  * it needs a `manage_kb_*` permission (the grant-management tier), not the
    `edit_kb` that ordinary content edits use;
  * a non-admin may only target departments inside their OWN subtree, mirroring
    the scope rule already applied to knowledge_space department grants.

`knowledge_space` (type=3) is rejected outright — it has its own binding table
(`department_knowledge_space`) and a write-time grant path. The personal library
(type=2) is not an assignable resource either.
"""

import logging

from sqlmodel import select

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.database.models.department import DepartmentDao, UserDepartmentDao
from bisheng.knowledge.domain.models.knowledge import KnowledgeDao, KnowledgeTypeEnum
from bisheng.permission.domain.services.permission_service import _parse_path_ids

logger = logging.getLogger(__name__)

# Types that participate in department ownership: document + QA libraries.
_ASSIGNABLE_TYPES = frozenset({KnowledgeTypeEnum.NORMAL.value, KnowledgeTypeEnum.QA.value})

_MANAGE_PERMISSION_IDS = ("manage_kb_owner", "manage_kb_manager", "manage_kb_viewer")


class KnowledgeDepartmentService:
    """Assign/clear `Knowledge.department_id` with authorization-side guards."""

    @staticmethod
    async def _has_manage_permission(login_user: UserPayload, knowledge_id: int) -> bool:
        """True when the actor holds any `manage_kb_*` permission on the library.

        Reuses the fine-grained evaluator so bound relation models are honoured —
        a custom model that stops granting `manage_kb_viewer` also loses the
        ability to re-assign the library.
        """
        from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

        effective = await KnowledgePermissionService.get_effective_permission_ids_async(
            login_user=login_user,
            knowledge_id=knowledge_id,
        )
        return any(pid in effective for pid in _MANAGE_PERMISSION_IDS)

    @classmethod
    async def _actor_department_paths(cls, user_id: int) -> list[str]:
        """Materialized paths of the departments the actor belongs to."""
        memberships = await UserDepartmentDao.aget_user_departments(user_id)
        if not memberships:
            return []
        departments = await DepartmentDao.aget_by_ids([int(row.department_id) for row in memberships])
        return [str(d.path) for d in departments if d.path]

    @classmethod
    async def assign_department(
        cls,
        login_user: UserPayload,
        knowledge_id: int,
        department_id: int | None,
    ) -> tuple[bool, str | None]:
        """Set (or clear) a library's owning department.

        Returns ``(allowed, error)``; the caller maps `error` onto its response
        envelope. `error` is None on success.
        """
        knowledge = await KnowledgeDao.aquery_by_id(int(knowledge_id))
        if knowledge is None:
            return False, "knowledge not found"
        if knowledge.type not in _ASSIGNABLE_TYPES:
            return False, "department ownership applies to document and QA libraries only"

        is_admin = getattr(login_user, "is_admin", None)
        if not (callable(is_admin) and is_admin()):
            if not await cls._has_manage_permission(login_user, knowledge.id):
                return False, "manage permission on this knowledge base is required"

        # Clearing is always allowed for anyone who passed the manage check —
        # it only ever narrows access, so it must not be blocked by the scope
        # rule (an actor whose department moved away could otherwise be stuck).
        if department_id is None:
            knowledge.department_id = None
            KnowledgeDao.update_one(knowledge)
            return True, None

        target = await DepartmentDao.aget_by_id(int(department_id))
        if target is None or not target.path:
            return False, "unknown department"

        if not (callable(is_admin) and is_admin()):
            # Scope: the actor may only hand the library to a department in their
            # OWN subtree — themselves or a descendant. An actor with no
            # department can assign nothing.
            #
            # The membership test is a plain prefix check, so the DIRECTION is
            # easy to invert: "target is inside an actor department" is
            # `target.path.startswith(actor_path)`, i.e. the actor's path is the
            # one used as the prefix. Written the other way round
            # (`actor_path.startswith(target_path)`) it means "the target is an
            # ANCESTOR of the actor", which would let any manager publish a
            # library to the tenant root — visible to everybody — while refusing
            # the legitimate case of a child department.
            actor_paths = await cls._actor_department_paths(login_user.user_id)
            target_path = str(target.path)
            if not any(target_path.startswith(path) for path in actor_paths):
                return False, "cannot assign to a department outside your scope"

        knowledge.department_id = int(department_id)
        KnowledgeDao.update_one(knowledge)
        logger.info(
            "F057: user %s assigned knowledge %s to department %s",
            getattr(login_user, "user_id", None),
            knowledge.id,
            department_id,
        )
        # Permission results are cached for 10s, and this write changes who may
        # read the library — without this the new department's members would keep
        # being denied for up to that long. (The reverse direction — an old
        # department keeping access — is handled by the same call in
        # clear_department_ownership.)
        await cls._invalidate_department_members(int(department_id))
        return True, None

    @classmethod
    async def department_stats(
        cls,
        login_user: UserPayload,
        knowledge_type,
        permission_id: str,
    ) -> list[dict]:
        """Library counts per owning department, subtree-inclusive.

        The badge must match what clicking the node shows, and the platform tree
        filters by subtree by default, so every descendant's libraries roll up
        into each ancestor's count. Counts are permission-scoped: an operator
        only ever counts libraries they may actually list.
        """
        from bisheng.core.database import get_async_db_session
        from bisheng.database.models.department import DepartmentDao
        from bisheng.knowledge.domain.models.knowledge import Knowledge as KnowledgeModel
        from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService

        # One pass: the libraries owned directly by each department.
        async with get_async_db_session() as session:
            rows = await session.exec(
                select(KnowledgeModel.id, KnowledgeModel.department_id).where(
                    KnowledgeModel.department_id.is_not(None),
                    KnowledgeModel.type == knowledge_type.value,
                )
            )
            ids_by_department: dict[int, list[int]] = {}
            for knowledge_id, department_id in rows.all():
                ids_by_department.setdefault(int(department_id), []).append(int(knowledge_id))

        if not ids_by_department:
            return []

        departments = [
            d for d in await DepartmentDao.aget_by_ids(list(ids_by_department)) if d.path
        ]

        # Scope to what the caller may see BEFORE rolling up, so the badge never
        # advertises a library the list would not return.
        all_ids = [kid for kids in ids_by_department.values() for kid in kids]
        visible = set(
            await KnowledgePermissionService().filter_knowledge_ids_by_permission_async(
                login_user, all_ids, permission_id
            )
        )
        visible_by_department = {
            dept_id: [kid for kid in kids if kid in visible]
            for dept_id, kids in ids_by_department.items()
        }

        # Roll up: a department's count includes every department whose path is
        # prefixed by its own (paths carry a trailing slash, so `/1/2/` cannot
        # spuriously match `/1/20/`).
        totals = {
            department.id: sum(
                len(visible_by_department.get(other.id, []))
                for other in departments
                if str(other.path).startswith(str(department.path))
            )
            for department in departments
        }
        # Drop zero entries. A department whose every library is invisible to the
        # caller would otherwise still be reported, and the mere presence of
        # `{department_id: X, count: 0}` discloses two things the caller is not
        # entitled to: that department X exists, and that it owns at least one
        # library of this type. That contradicts this method's own contract (a
        # badge must never advertise a library the list would not return) and
        # AC-13. The frontend already skips falsy counts, so nothing is lost.
        return [
            {"department_id": dept_id, "count": count}
            for dept_id, count in sorted(totals.items())
            if count
        ]

    @staticmethod
    async def _invalidate_department_members(department_id: int, extra_user_id: int | None = None) -> None:
        """Drop cached permission results for a department's DIRECT members.

        Department ownership is resolved at read time, so nothing wrote an FGA
        tuple for the change — which also means nothing invalidated the cache the
        way `PermissionService.authorize()` does. Cached verdicts live 10s, so
        without this an assignment is invisible (and a revocation still honoured)
        for up to that window.

        Deliberately NOT subtree-expanded: descendants' members are a strictly
        smaller part of the affected set, and expanding means walking the subtree
        and resolving members per department on every assignment. The residual
        window stays bounded by the TTL, and the direct department is what the
        common case changes.

        Best-effort: a cache that cannot be cleared must not fail the assignment
        that already committed — the entry simply expires on its own.
        """
        try:
            from bisheng.database.models.department import DepartmentDao
            from bisheng.permission.domain.services.permission_cache import PermissionCache

            user_ids = set(await DepartmentDao.aget_user_ids_by_department(int(department_id)))
            if extra_user_id is not None:
                user_ids.add(int(extra_user_id))
            for user_id in user_ids:
                await PermissionCache.invalidate_user(user_id)
        except Exception as e:
            # Cache expiry is the fallback; a failed clear must not fail the
            # assignment that already committed.
            logger.warning(
                "F057: could not invalidate permission cache for department %s: %s",
                department_id,
                e,
            )

    @staticmethod
    def department_chain_for_paths(departments) -> list[int]:
        """Every department id appearing on the given rows' materialized paths.

        A department's `path` lists its ancestors AND itself, so unioning the
        paths of a set of departments yields exactly "those departments plus
        their ancestors" — the chain used both for the ownership filter and for
        the member-scoped tree. Extracted because the same three lines were
        inlined at two call sites; the tenant-scope handling around them differs
        per caller and stays there.
        """
        chain: set[int] = set()
        for department in departments:
            chain.update(_parse_path_ids(department.path))
        return sorted(chain)

    @classmethod
    async def clear_department_ownership(cls, department_id: int) -> int:
        """Unclassify every library owned by a department that is going away.

        Called when a department is archived or purged. The DDL deliberately
        adds NO foreign key (see the f057 migration docstring), so nothing at
        the database level stops `knowledge.department_id` from pointing at a
        department row that no longer exists — this is the application-side half
        of that trade-off, and the reason the migration's docstring can promise
        "deleting a department returns its libraries to unclassified".

        NOTE ON RESTORE: archiving is otherwise reversible (`arestore_department`
        flips `status` back to `active`), but this clear is NOT undone — a
        restored department comes back with no libraries. That is deliberate:
        an archived department cannot retain access, and the alternative
        (evaluating `status` at read time and keeping the id) was rejected in
        favour of leaving no dangling references behind. See design 决策 7.

        Returns the number of libraries unclassified, for logging.
        """
        from sqlalchemy import update as sa_update

        from bisheng.core.database import get_async_db_session
        from bisheng.knowledge.domain.models.knowledge import Knowledge as KnowledgeModel

        async with get_async_db_session() as session:
            result = await session.execute(
                sa_update(KnowledgeModel)
                .where(KnowledgeModel.department_id == int(department_id))
                .values(department_id=None)
            )
            await session.commit()

        # Revoking is the direction that matters: without this, users who only
        # held access through the now-gone department keep it until their cached
        # verdict expires (10s). Cleared here rather than at the call site so
        # every caller — platform archive/purge and the org-sync cleanup —
        # inherits it.
        await cls._invalidate_department_members(int(department_id))

        # No tenant scoping is needed and none is applied: `department.id` is a
        # globally unique primary key, so matching on `department_id` is already
        # tenant-exact. It would also be unsafe to rely on one — the tenant
        # filter's `do_orm_execute` hook intercepts SELECT only, so an UPDATE
        # would silently run unscoped regardless.
        return int(result.rowcount or 0)

    @classmethod
    async def browse_departments(
        cls,
        parent_id: int | None = None,
        include_archived: bool = False,
    ) -> list[dict]:
        """One layer of the tenant's department tree, for the knowledge filter.

        This exists because `/departments/*` cannot serve ordinary members. That
        endpoint family is department MANAGEMENT: F038 scopes it to system
        admins, department admins and tenant admins, and answers 21009 for
        everyone else — so the knowledge page, whose audience is everyone, needs
        its own read-only source.

        SCOPE: the whole tenant tree, for every caller — not the caller's own
        branch. The tree is here to let people pick a department to browse, and
        the permission check that matters happens per ROW when the list is
        loaded: the filter narrows a view, it never grants. Selecting a
        department holding nothing the caller may read simply yields an empty
        list, which is also what a genuinely empty department yields.

        Tenant isolation comes from `DepartmentDao.aget_children`, which issues a
        normal ORM SELECT and therefore goes through the tenant-filter hook
        (C3). No explicit `tenant_id` predicate is written here — see C3.

        `has_children` is delegated to the DAO rather than inferred: unlike a
        pruned chain, this is the FULL tree, so a node may well have children
        that are archived away and not themselves renderable.

        Node shape mirrors `/departments/children` so the lazy tree component
        consumes it unchanged.
        """
        # The department module owns the node shape the tree renders; reusing its
        # builder keeps this endpoint's nodes byte-identical to
        # `/departments/children`. Both imports are lazy to match this module's
        # style of keeping cross-module edges out of the top-level import graph.
        from bisheng.database.models.department import DepartmentDao
        from bisheng.department.domain.services.department_service import _dept_node_dict

        layer = await DepartmentDao.aget_children(parent_id, include_archived=include_archived)
        if not layer:
            return []

        # One batched existence query per rendered layer (F038 decision 5) rather
        # than an N+1 EXISTS per node.
        existence = await DepartmentDao.aget_children_existence(
            [int(d.id) for d in layer], include_archived=include_archived
        )
        return [_dept_node_dict(d, has_children=int(d.id) in existence) for d in layer]

    @classmethod
    async def search_departments(cls, keyword: str, limit: int = 50, include_archived: bool = False) -> dict:
        """Name search over the same tenant-wide tree as `browse_departments`.

        Server-side on purpose. The tree is the WHOLE tenant tree, so a client
        that searches only the layers it happens to have expanded will miss every
        match below a collapsed node — the user types a department name that is
        plainly in the organisation and gets "no results".

        Scope matches `browse_departments`: tenant-wide, no management-permission
        gate. `path_prefixes=None` is the DAO's "unscoped" mode; tenant isolation
        still comes from the tenant-filter hook on that SELECT (C3), not from an
        explicit predicate here.

        Returns the pruned-tree shape the lazy tree component already consumes
        (`roots` / `total_matches` / `truncated`), with ancestors attached so a
        hit can be rendered in context.
        """
        from bisheng.database.models.department import DepartmentDao
        from bisheng.department.domain.services.department_service import _dept_node_dict

        kw = (keyword or "").strip()
        if not kw:
            # No query for a blank keyword — a bare LIKE '%%' is a full scan.
            return {"roots": [], "total_matches": 0, "truncated": False}

        # Fetch one extra to detect truncation without a second COUNT query.
        matches = await DepartmentDao.aget_by_name_like(
            kw,
            path_prefixes=None,
            limit=limit + 1,
            include_archived=include_archived,
        )
        truncated = len(matches) > limit
        matches = matches[:limit]
        if not matches:
            return {"roots": [], "total_matches": 0, "truncated": False}

        # Attach each hit's own chain (root→parent) so the tree can expand down to
        # it. Paths list ancestors+self, so the chain is one batched lookup.
        chain_ids: set[int] = set()
        for department in matches:
            chain_ids.update(_parse_path_ids(department.path))
        ancestors = await DepartmentDao.aget_by_ids(sorted(chain_ids))
        if not include_archived:
            ancestors = [d for d in ancestors if d.status != "archived"]

        by_id = {int(d.id): d for d in ancestors}
        existence = await DepartmentDao.aget_children_existence(
            list(by_id), include_archived=include_archived
        )
        matched_ids = {int(d.id) for d in matches}

        # Rebuild the forest parent→child from the path-derived id set.
        nodes = {i: _dept_node_dict(d, has_children=i in existence, matched=i in matched_ids) for i, d in by_id.items()}
        roots: list[dict] = []
        for i, node in nodes.items():
            parent = by_id[i].parent_id
            parent_node = nodes.get(int(parent)) if parent is not None else None
            if parent_node is not None:
                parent_node["children"].append(node)
            else:
                roots.append(node)

        def _sort(layer: list[dict]) -> None:
            layer.sort(key=lambda n: (n["sort_order"], n["id"]))
            for n in layer:
                _sort(n["children"])

        _sort(roots)
        return {"roots": roots, "total_matches": len(matches), "truncated": truncated}
