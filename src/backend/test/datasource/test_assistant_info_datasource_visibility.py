"""F061 T-32(a): `AssistantInfo.datasource_list` must not disclose datasources
the caller cannot use.

Why this field and not a list endpoint: the sources a chat turn may draw on are
the admin's `AssistantLink` rows, and `AssistantInfo` is what the client reads
to render the app. Returning the raw bound ids to anyone holding `use_app` tells
them that a datasource exists — `conversation_service` refuses to disclose
exactly that on the filtering path ("telling it WHICH ids exist but are
forbidden would leak the existence of another department's datasources"), so
the same rule has to hold here or the earlier care is undone by a field nobody
looked at.

The EDITOR is the case that makes the naive fix wrong. `update_assistant` writes
`datasource_list` wholesale, so an editor who reads a FILTERED list and saves it
back would silently unbind every datasource they personally cannot use — data
loss caused by a read-side hardening. Editors therefore keep the full list; the
distinction is `edit_app` vs `use_app`, which is the same pair the write path
already enforces.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.api.services.assistant import AssistantService
from bisheng.permission.domain.services.application_permission_service import (
    ApplicationPermissionService,
)
from bisheng.permission.domain.services.permission_service import PermissionService


class _User:
    user_id = 3
    tenant_id = 1

    def get_visible_tenants(self):
        return [1]

    def is_admin(self):
        return False


class _Assistant:
    """Stands in for the `Assistant` row.

    `get_assistant_info` spreads `assistant.model_dump()` into `AssistantInfo`,
    so a bare namespace raises there — before it ever reaches the field under
    test.
    """

    id = 'app-1'
    name = '订单助手'
    user_id = 99
    is_delete = False
    status = 1
    tenant_id = 1
    # Read by the method under test before it builds the response.
    logo = ''

    def model_dump(self, **kwargs):
        return {
            'id': self.id,
            'name': self.name,
            'user_id': self.user_id,
            'is_delete': self.is_delete,
            'status': self.status,
            'tenant_id': self.tenant_id,
            'desc': '',
            'logo': '',
            'prompt': '',
        }


def _assistant():
    return _Assistant()


def _row(datasource_id):
    """A datasource row as the DAO returns it, for the resolution step."""
    return SimpleNamespace(id=int(datasource_id), name=f'db-{datasource_id}', engine='mysql',
                           host='h', port=1, database_name='d', username='u',
                           password='ciphertext', description=None, enabled=1)


def _links(*datasource_ids):
    return [
        SimpleNamespace(tool_id=None, knowledge_id=None, flow_id=None, datasource_id=ds_id)
        for ds_id in datasource_ids
    ]


async def _info(monkeypatch, *, can_edit: bool, viewer_for: set[int]):
    """Run `get_assistant_info` with the app-permission and datasource-grant
    answers stubbed, and return the resulting `datasource_list`."""

    async def _has_any_permission(login_user, object_type, object_id, permission_ids):
        if 'edit_app' in set(permission_ids):
            return can_edit
        # The read gate: caller may use the app.
        return True

    async def _check(user_id, relation, object_type, object_id, login_user=None, **kwargs):
        return int(object_id) in viewer_for

    monkeypatch.setattr(
        ApplicationPermissionService, 'has_any_permission_async', _has_any_permission
    )
    monkeypatch.setattr(PermissionService, 'check', _check)

    with (
        patch(
            'bisheng.api.services.assistant.AssistantDao.aget_one_assistant',
            AsyncMock(return_value=_assistant()),
        ),
        patch(
            'bisheng.api.services.assistant.AssistantLinkDao.get_assistant_link',
            AsyncMock(return_value=_links(7, 8)),
        ),
        patch.object(
            AssistantService,
            'get_link_info',
            staticmethod(lambda tool_list, flow_list, knowledge_list: ([], [], [])),
        ),
        patch.object(
            AssistantService, 'get_logo_share_link_async', AsyncMock(return_value='')
        ),
        patch(
            'bisheng.api.services.assistant.user_may_share_app',
            AsyncMock(return_value=False),
        ),
        # The resolution step reads each visible row to get its name. Stubbed
        # here because leaving it real sends this test at a live MySQL (the
        # F061 T-31 postmortem records that exact mistake).
        patch(
            'bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id',
            AsyncMock(side_effect=lambda ds_id: _row(ds_id)),
        ),
    ):
        info = await AssistantService.get_assistant_info('app-1', _User())

    return info.datasource_list


async def test_a_user_without_the_grant_does_not_see_the_bound_datasource(monkeypatch):
    """AC-49: not theirs to use, so not disclosed."""
    assert [e.id for e in await _info(monkeypatch, can_edit=False, viewer_for={7})] == [7]


async def test_a_user_with_no_grants_sees_none_of_them(monkeypatch):
    assert await _info(monkeypatch, can_edit=False, viewer_for=set()) == []


async def test_an_editor_sees_the_full_binding(monkeypatch):
    """Not a leak: an editor may change the binding, so they must be able to see
    it. Filtering here would make the next save drop the missing ids."""
    assert [e.id for e in await _info(monkeypatch, can_edit=True, viewer_for={7})] == [7, 8]


async def test_an_editor_who_is_granted_everything_is_unchanged(monkeypatch):
    """The common case today must be byte-identical to before (AC-60)."""
    assert [e.id for e in await _info(monkeypatch, can_edit=True, viewer_for={7, 8})] == [7, 8]


async def test_no_bound_datasource_short_circuits_before_any_check(monkeypatch):
    """Every existing assistant binds none, so this path must not start making
    permission calls — it is the hot path for the whole install base."""
    calls = []

    async def _has_any_permission(login_user, object_type, object_id, permission_ids):
        calls.append(set(permission_ids))
        return True

    monkeypatch.setattr(
        ApplicationPermissionService, 'has_any_permission_async', _has_any_permission
    )

    with (
        patch(
            'bisheng.api.services.assistant.AssistantDao.aget_one_assistant',
            AsyncMock(return_value=_assistant()),
        ),
        patch(
            'bisheng.api.services.assistant.AssistantLinkDao.get_assistant_link',
            AsyncMock(return_value=[]),
        ),
        patch.object(
            AssistantService,
            'get_link_info',
            staticmethod(lambda tool_list, flow_list, knowledge_list: ([], [], [])),
        ),
        patch.object(
            AssistantService, 'get_logo_share_link_async', AsyncMock(return_value='')
        ),
        patch(
            'bisheng.api.services.assistant.user_may_share_app',
            AsyncMock(return_value=False),
        ),
        # The resolution step reads each visible row to get its name. Stubbed
        # here because leaving it real sends this test at a live MySQL (the
        # F061 T-31 postmortem records that exact mistake).
        patch(
            'bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id',
            AsyncMock(side_effect=lambda ds_id: _row(ds_id)),
        ),
    ):
        info = await AssistantService.get_assistant_info('app-1', _User())

    assert info.datasource_list == []
    # Only the read gate ran; no edit_app probe and no per-id datasource check.
    assert calls == [{'view_app', 'use_app'}]
