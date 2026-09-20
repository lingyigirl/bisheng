"""What the conversation layer needs from the datasource feature.

Three jobs, and they share one rule: **authorization is decided by
`PermissionService`, never by a listing.** Spec AC-49 has two halves and the
second is the easy one to drop -- a datasource the user is not authorized for
must not merely be absent from the picker, it must be REFUSED if the id is
passed directly. Filtering a list and calling it done is the classic version of
this bug: the API stays open and only the UI looks right.

AC-52 (authorization changes take effect on the next turn) follows from doing
the check per turn rather than caching a resolved set. That is also why nothing
here memoizes across calls.

The citation anchor exists so an answer can be traced back: which datasource,
which tables, and the statement that ran (AC-31). The result rows travel
separately, bounded by the executor's cap (AC-35) -- they are what the user
expands to look at, not part of the anchor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from bisheng.common.errcode.datasource import DatasourceNotFoundError
from bisheng.datasource.domain.langchain.tool import build_datasource_tools
from bisheng.datasource.domain.models.datasource import DbDatasource

#: The relation a user needs to USE a datasource in a conversation. Reading and
#: managing are separate points (AC-44); using is what a chat turn requires.
USE_RELATION = 'viewer'


@dataclass
class CitationAnchor:
    """What the answer points at, and what the expand panel renders (AC-31).

    ``rows`` is bounded by the executor's row cap and is NOT re-trimmed here
    (AC-35): a second trimming rule would eventually disagree with the first.
    """

    datasource_id: int
    datasource_name: str
    tables: list[str] = field(default_factory=list)
    statement: str = ''
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False

    def to_payload(self) -> dict:
        """The shape persisted with the message and read back on reload (AC-34).

        Kept flat and JSON-safe: it round-trips through the message's existing
        `extra`/citation storage, so it must survive json.dumps without a
        custom encoder.
        """
        return {
            'datasource_id': self.datasource_id,
            'datasource_name': self.datasource_name,
            'tables': list(self.tables),
            'statement': self.statement,
            'columns': list(self.columns),
            'rows': [list(row) for row in self.rows],
            'truncated': self.truncated,
        }


async def authorized_datasource_ids(login_user, datasource_ids: list[int]) -> list[int]:
    """The subset of `datasource_ids` the user may use, in the given order.

    A per-id check rather than a listing filter, because the caller may have
    received the ids from anywhere. Unauthorized ids are dropped silently --
    the caller is building a source list, and telling it WHICH ids exist but are
    forbidden would leak the existence of another department's datasources.
    """
    if not datasource_ids:
        return []

    from bisheng.permission.domain.services.permission_service import PermissionService

    kept: list[int] = []
    for datasource_id in datasource_ids:
        try:
            allowed = await PermissionService.check(
                user_id=login_user.user_id,
                relation=USE_RELATION,
                object_type='db_datasource',
                object_id=str(datasource_id),
                login_user=login_user,
            )
        except Exception:
            logger.opt(exception=True).warning(
                'datasource permission check failed for id={}; denying',
                datasource_id,
            )
            allowed = False
        if allowed:
            kept.append(datasource_id)
    return kept


async def build_tools_for_assistant(
    assistant_links: list,
    login_user,
    *,
    user_id: int | None = None,
) -> list:
    """Tools for the datasources an assistant is linked to, minus unauthorized.

    `login_user` MUST be a `UserPayload` (callable `get_visible_tenants`), not
    a bare `LoginUser` and not `None`. `PermissionService.check` skips its
    tenant gate for both of those (`permission_service.py:1018-1020`), so a
    caller that passes one silently drops tenant isolation instead of failing.
    Build one the way `ToolExecutor._build_permission_user` does.

    `assistant_links` are `AssistantLink` rows; only the ones carrying a
    `datasource_id` are considered. The authorization filter runs HERE rather
    than at the link level because a link is the admin's intent and the check is
    the invoker's right -- an assistant shared with another user must not lend
    them its datasource.

    Returns [] when nothing survives, which is the normal state for every
    existing assistant (AC-60).
    """
    if login_user is None or not callable(getattr(login_user, 'get_visible_tenants', None)):
        raise ValueError(
            'build_tools_for_assistant requires a UserPayload with get_visible_tenants; '
            'passing None or a bare LoginUser would skip the tenant gate silently'
        )

    from bisheng.datasource.domain.models.dao import DbDatasourceDao

    wanted = [link.datasource_id for link in assistant_links if getattr(link, 'datasource_id', None)]
    if not wanted:
        return []

    allowed = set(await authorized_datasource_ids(login_user, wanted))
    if not allowed:
        return []

    datasources: list[DbDatasource] = []
    for datasource_id in wanted:
        if datasource_id not in allowed:
            continue
        datasource = await DbDatasourceDao.aget_by_id(int(datasource_id))
        # A link whose datasource was deleted is skipped, not an error: deleting
        # a datasource is allowed to leave stale links behind (AC-62 keeps the
        # history readable and does not cascade).
        if datasource is not None and datasource.enabled:
            datasources.append(datasource)

    return await build_datasource_tools(datasources, user_id=user_id)


async def resolve_bound_datasource(login_user, datasource_id: int) -> DbDatasource:
    """Fetch one datasource for a turn, refusing it if unauthorized.

    The refusal half of AC-49. Raises the "not found or no access" error, which
    deliberately does not distinguish the two (spec: telling them apart would
    let a caller probe which ids exist in another tenant).
    """
    from bisheng.datasource.domain.models.dao import DbDatasourceDao

    if not await authorized_datasource_ids(login_user, [int(datasource_id)]):
        raise DatasourceNotFoundError()

    datasource = await DbDatasourceDao.aget_by_id(int(datasource_id))
    if datasource is None or not datasource.enabled:
        raise DatasourceNotFoundError()
    return datasource
