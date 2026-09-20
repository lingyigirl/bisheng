"""F061 T-40: `AssistantInfo.datasource_list` is a resolved read model.

Its three siblings in the same schema are already resolved: `tool_list`,
`flow_list` and `knowledge_list` each carry `{id, name, ...}` for the client to
render directly, and the platform's save path maps them back down to ids on
write (`apps.tsx`, `editAssistant/index.tsx`). `datasource_list` was added as a
bare `list[int]`, which broke that convention and made the field unusable for
its stated purpose: a chat user cannot resolve an id to a name, because the
management endpoint that would do it is admin-gated.

Why this has to be resolved on the server rather than by the client: the picker
that fetches `GET /api/v1/datasource` is admin-only, so a chat user has no way
to turn `7` into a name at all. Resolving here is also where the authorization
filter already lives (T-32), so the two stay in one place.

AC-60 is the constraint that matters for a shape change: an assistant with no
datasource bound must serialize exactly as before — an empty list — because that
is every assistant in the install base.
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
    id = 'app-1'
    name = 'assistant'
    user_id = 99
    is_delete = False
    status = 1
    tenant_id = 1
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


def _datasource_row(ds_id: int, name: str):
    """A row as `DbDatasourceDao.aget_by_id` returns it."""
    return SimpleNamespace(
        id=ds_id,
        name=name,
        engine='mysql',
        host='10.0.0.1',
        port=3306,
        database_name='db',
        username='reader',
        # Present on the real row; must NOT reach the response.
        password='fernet-ciphertext',
        description='orders',
        enabled=1,
        has_password=True,
    )


def _links(*datasource_ids):
    return [
        SimpleNamespace(tool_id=None, knowledge_id=None, flow_id=None, datasource_id=ds_id)
        for ds_id in datasource_ids
    ]


async def _info(monkeypatch, *, can_edit: bool, viewer_for: set[int], rows: dict):
    async def _has_any_permission(login_user, object_type, object_id, permission_ids):
        return can_edit if 'edit_app' in set(permission_ids) else True

    async def _check(user_id, relation, object_type, object_id, login_user=None, **kwargs):
        return int(object_id) in viewer_for

    async def _aget_by_id(datasource_id):
        return rows.get(int(datasource_id))

    monkeypatch.setattr(
        ApplicationPermissionService, 'has_any_permission_async', _has_any_permission
    )
    monkeypatch.setattr(PermissionService, 'check', _check)

    with (
        patch(
            'bisheng.api.services.assistant.AssistantDao.aget_one_assistant',
            AsyncMock(return_value=_Assistant()),
        ),
        patch(
            'bisheng.api.services.assistant.AssistantLinkDao.get_assistant_link',
            AsyncMock(return_value=_links(*rows.keys())),
        ),
        patch.object(
            AssistantService,
            'get_link_info',
            staticmethod(lambda tool_list, flow_list, knowledge_list: ([], [], [])),
        ),
        patch.object(AssistantService, 'get_logo_share_link_async', AsyncMock(return_value='')),
        patch(
            'bisheng.api.services.assistant.user_may_share_app',
            AsyncMock(return_value=False),
        ),
        patch(
            'bisheng.datasource.domain.models.dao.DbDatasourceDao.aget_by_id',
            AsyncMock(side_effect=_aget_by_id),
        ),
    ):
        return await AssistantService.get_assistant_info('app-1', _User())


async def test_the_bound_datasource_comes_back_with_its_name(monkeypatch):
    """The whole point: the client renders a name, and it has no other way to
    learn one."""
    rows = {7: _datasource_row(7, 'orders-db')}

    info = await _info(monkeypatch, can_edit=False, viewer_for={7}, rows=rows)

    assert len(info.datasource_list) == 1
    entry = info.datasource_list[0]
    assert entry.id == 7
    assert entry.name == 'orders-db'


async def test_the_shape_matches_its_sibling_fields(monkeypatch):
    """A client that renders `knowledge_list` must be able to render this the
    same way. Asserted as a property of the entry rather than on the class, so a
    rename does not silently satisfy it."""
    rows = {7: _datasource_row(7, 'orders-db')}

    info = await _info(monkeypatch, can_edit=False, viewer_for={7}, rows=rows)

    entry = info.datasource_list[0]
    assert hasattr(entry, 'id'), 'the entry must carry an id, like knowledge_list entries'
    assert hasattr(entry, 'name'), 'the entry must carry a name, like knowledge_list entries'


async def test_the_credential_never_reaches_the_response(monkeypatch):
    """INV-19 clause 1: `password` holds Fernet ciphertext and no read path may
    return it. A resolved model is exactly where a credential would leak — the
    row is right there, and `**row.__dict__` is the mistake that does it."""
    rows = {7: _datasource_row(7, 'orders-db')}

    info = await _info(monkeypatch, can_edit=False, viewer_for={7}, rows=rows)

    serialized = info.datasource_list[0].model_dump()
    assert 'password' not in serialized
    assert 'fernet-ciphertext' not in str(serialized)


async def test_an_unauthorized_datasource_is_still_absent(monkeypatch):
    """T-32's filter must survive the shape change — a resolved model carries a
    NAME, so leaking here discloses more than an id did."""
    rows = {7: _datasource_row(7, 'orders-db'), 8: _datasource_row(8, 'payroll-db')}

    info = await _info(monkeypatch, can_edit=False, viewer_for={7}, rows=rows)

    assert [entry.id for entry in info.datasource_list] == [7]
    assert 'payroll-db' not in str(info.datasource_list)


async def test_an_editor_still_sees_everything(monkeypatch):
    """The T-32 carve-out: an editor's read must stay complete or their next save
    drops the bindings they could not see."""
    rows = {7: _datasource_row(7, 'orders-db'), 8: _datasource_row(8, 'payroll-db')}

    info = await _info(monkeypatch, can_edit=True, viewer_for={7}, rows=rows)

    assert sorted(entry.id for entry in info.datasource_list) == [7, 8]


async def test_a_deleted_datasource_is_skipped_rather_than_crashing(monkeypatch):
    """AC-62: deleting a datasource leaves stale link rows behind on purpose.
    Resolution must tolerate the missing row — the whole message must not 500
    because one binding points at something that is gone."""
    rows = {7: _datasource_row(7, 'orders-db'), 8: None}

    info = await _info(monkeypatch, can_edit=True, viewer_for={7, 8}, rows=rows)

    assert [entry.id for entry in info.datasource_list] == [7]


async def test_no_bound_datasource_serializes_as_an_empty_list(monkeypatch):
    """AC-60: every existing assistant binds nothing, so this is the shape the
    install base sees. It must not have changed."""
    info = await _info(monkeypatch, can_edit=False, viewer_for=set(), rows={})

    assert info.datasource_list == []
