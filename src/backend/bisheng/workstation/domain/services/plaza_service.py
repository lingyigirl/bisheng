"""F047 agent plaza — favorites orchestration (list/plaza/stats arrive in Wave 2)."""
from __future__ import annotations

from bisheng.api.services.workflow import WorkFlowService
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.workstation import PlazaAppNotFoundError, PlazaAppNotVisibleError
from bisheng.core.database.manager import get_sync_db_session
from bisheng.database.models.flow import FlowDao, FlowStatus, FlowType
from bisheng.database.models.group_resource import ResourceTypeEnum
from bisheng.database.models.tag import TagDao
from bisheng.workstation.domain.repositories.favorite_repository import FavoriteRepository
from bisheng.workstation.domain.services import plaza_stats

_SUPPORTED_TYPES = {FlowType.ASSISTANT.value, FlowType.WORKFLOW.value}
_SORTS = {'recent', 'update_time', 'visit_count'}


class PlazaService:
    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    @staticmethod
    def add_favorite(login_user: UserPayload, app_id: int | str, app_type: int) -> None:
        """Favorite an app. Idempotent (AC-12).

        Validates the app row exists and is a plaza-visible type (assistant /
        workflow) — stale ids from an outdated UI surface a business error
        instead of silently storing an orphan record.
        """
        flow = FlowDao.get_flow_by_id(str(app_id))
        if flow is None:
            raise PlazaAppNotFoundError()
        if flow.flow_type not in _SUPPORTED_TYPES or int(app_type) != int(flow.flow_type):
            raise PlazaAppNotVisibleError()

        with get_sync_db_session() as session:
            FavoriteRepository(session).add(
                user_id=login_user.user_id,
                app_id=app_id,
                app_type=app_type,
            )

    @staticmethod
    def remove_favorite(login_user: UserPayload, app_id: int | str, app_type: int) -> None:
        """Unfavorite an app. Idempotent (AC-13)."""
        with get_sync_db_session() as session:
            FavoriteRepository(session).remove(
                user_id=login_user.user_id,
                app_id=app_id,
                app_type=app_type,
            )

    @staticmethod
    async def list_favorites(login_user: UserPayload, page: int, limit: int) -> dict:
        """Favorites of the user, newest-first, only online + visible apps
        (AC-14 / AC-16 / AC-23). Mirrors the get_used_apps enrich-after-
        paginate pattern in workstation/api/endpoints/apps.py."""
        with get_sync_db_session() as session:
            favorites = FavoriteRepository(session).list_by_user(user_id=login_user.user_id)
        if not favorites:
            return {"list": [], "total": 0}

        favorite_order = {row.app_id: idx for idx, row in enumerate(favorites)}
        flow_ids = [row.app_id for row in favorites]

        # status=ONLINE drops offline apps; the permission filter drops
        # apps the user can no longer view (AC-16, AC-23).
        apps, _ = await FlowDao.aget_all_apps(id_list=flow_ids, status=FlowStatus.ONLINE.value, page=0, limit=0)
        apps = await WorkFlowService.filter_apps_by_permission_id(login_user, apps, "view_app")
        apps.sort(key=lambda app: favorite_order.get(app["id"], len(favorites)))

        total = len(apps)
        page_items = apps[(page - 1) * limit : (page - 1) * limit + limit]

        resource_tag_dict = TagDao.get_tags_by_resource(None, [app["id"] for app in page_items])
        result = []
        for app in page_items:
            app_id = app["id"]
            app["logo"] = WorkFlowService.get_logo_share_link(app.get("logo"))
            app["tags"] = resource_tag_dict.get(app_id, [])
            result.append(app)

        return {"list": result, "total": total}

    # ------------------------------------------------------------------
    # Plaza list + stats
    # ------------------------------------------------------------------

    @staticmethod
    async def list_plaza(
        login_user: UserPayload,
        keyword: str | None = None,
        flow_types: list[int] | None = None,
        tag_ids: list[int] | None = None,
        sort_by: str = 'recent',
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        """Plaza list (AC-01..AC-08, AC-20). Visibility = ONLINE + view_app.

        One unbounded fetch of the visible set (skip_pagination), then local
        filters (flow_types / tags OR), optional visit-count sort backed by a
        single ES aggregation, then slice the page and inline per-app stats
        (design 决策 1、3、7).
        """
        if sort_by not in _SORTS:
            sort_by = 'recent'

        data, _has_more = await WorkFlowService.get_all_flows(
            login_user,
            keyword,
            FlowStatus.ONLINE.value,
            None,
            None,
            page=1,
            page_size=10,
            skip_pagination=True,
            search_description=True,
            permission_id='view_app',
        )

        if flow_types:
            wanted = {int(t) for t in flow_types}
            data = [app for app in data if int(app.get('flow_type') or 0) in wanted]

        if tag_ids:
            tag_links = TagDao.get_resources_by_tags_batch(
                [int(t) for t in tag_ids],
                [ResourceTypeEnum.WORK_FLOW, ResourceTypeEnum.ASSISTANT],
            )
            tagged_ids = {link.resource_id for link in tag_links}
            data = [app for app in data if app['id'] in tagged_ids]

        if sort_by == 'update_time':
            data.sort(key=lambda app: app.get('update_time') or '', reverse=True)
        elif sort_by == 'visit_count':
            stats = plaza_stats.batch_stats([app['id'] for app in data], days=7)
            data.sort(key=lambda app: stats.get(app['id'], {}).get('recent_7d_visits', 0), reverse=True)

        total = len(data)
        page_items = data[(page - 1) * limit : (page - 1) * limit + limit]

        page_stats = plaza_stats.batch_stats([app['id'] for app in page_items], days=7)
        for app in page_items:
            app['stats'] = page_stats.get(app['id'], {
                'total_visits': 0,
                'recent_7d_visits': 0,
                'success_rate': 0.0,
                'avg_process_time': 0.0,
            })

        return {'data': page_items, 'total': total}

    @staticmethod
    async def app_stats(login_user: UserPayload, app_id: str, days: int = 7) -> dict:
        """Single-app stats for the preview drawer (AC-09, AC-19).

        Checks the app exists, is online and visible to the user (view_app)
        before returning aggregate stats.
        """
        flow = FlowDao.get_flow_by_id(str(app_id))
        if flow is None:
            raise PlazaAppNotFoundError()
        if flow.status != FlowStatus.ONLINE.value or flow.flow_type not in _SUPPORTED_TYPES:
            raise PlazaAppNotVisibleError()

        visible = await WorkFlowService.filter_apps_by_permission_id(
            login_user,
            [{'id': flow.id, 'flow_type': flow.flow_type}],
            'view_app',
        )
        if not visible:
            raise PlazaAppNotVisibleError()

        return plaza_stats.single_stats(app_id, days=days)
