"""Hit-log query path tests (F056, AC-15): service filter forwarding + endpoint
response shape. The DAO SQL itself is exercised by e2e (T018).
"""

from datetime import datetime
from types import SimpleNamespace

from bisheng.sensitive_word.api.endpoints.hit_logs import list_sensitive_word_hit_logs
from bisheng.sensitive_word.domain.services.chat_guard import ChatGuardService

DAO_ALIST_PATH = 'bisheng.sensitive_word.domain.services.chat_guard.SensitiveWordHitLogDao.alist'


def _row():
    return SimpleNamespace(
        id=1,
        business_type='chat_input',
        direction='input',
        flow_id='flow-1',
        chat_id=None,
        user_id=7,
        hits=[{'word': '敏感词', 'count': 2}],
        action='block',
        create_time=datetime(2026, 9, 8, 10, 0, 0),
    )


async def test_alist_hit_logs_forwards_filters_and_builds_items(monkeypatch):
    captured = {}
    login_user = SimpleNamespace(user_id=1, tenant_id=3)

    async def fake_alist(**kwargs):
        captured.update(kwargs)
        return [_row()], 1

    monkeypatch.setattr(DAO_ALIST_PATH, fake_alist)
    monkeypatch.setattr('bisheng.core.context.tenant.get_current_tenant_id', lambda: None)

    result = await ChatGuardService.alist_hit_logs(
        login_user, direction='input', flow_id='flow-1', page=2, page_size=10
    )

    assert captured['tenant_id'] == 3  # ContextVar 缺失时回退 login_user.tenant_id
    assert captured['direction'] == 'input'
    assert captured['flow_id'] == 'flow-1'
    assert captured['page'] == 2
    assert result.total == 1
    item = result.data[0]
    assert item.hits[0].word == '敏感词'
    assert item.hits[0].count == 2
    assert item.action == 'block'


async def test_hit_log_endpoint_returns_page_data(monkeypatch):
    login_user = SimpleNamespace(user_id=1, tenant_id=3)

    async def fake_alist(**kwargs):
        return [_row()], 1

    monkeypatch.setattr(DAO_ALIST_PATH, fake_alist)
    monkeypatch.setattr('bisheng.core.context.tenant.get_current_tenant_id', lambda: 3)

    response = await list_sensitive_word_hit_logs(login_user=login_user)

    assert response.status_code == 200
    assert response.data['total'] == 1
    assert response.data['data'][0]['direction'] == 'input'
