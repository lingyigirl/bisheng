"""T017 (F059): platform-sourced hit rows must not leak their words to tenants.

INV-10 says the platform library is authoritative content that tenants cannot
read. A hit-log row is a tenant-visible surface, so it is the most direct place
that content could escape. These tests pin the redaction at the SERVICE layer,
because that is what actually keeps the words out of the HTTP response — hiding
them in the browser would still ship them over the wire.

The row itself must survive: a tenant administrator needs to tell "the guardrail
blocked this" from "the system is broken" (spec AC-18).
"""

from datetime import datetime
from types import SimpleNamespace

from bisheng.sensitive_word.domain.services.chat_guard import (
    SOURCE_PLATFORM,
    SOURCE_TENANT,
    ChatGuardService,
)

DAO_LIST_PATH = 'bisheng.sensitive_word.domain.models.hit_log.SensitiveWordHitLogDao.alist'


def _row(*, source, hits=None, tenant_id=1, row_id=1):
    return SimpleNamespace(
        id=row_id,
        tenant_id=tenant_id,
        business_type='chat_input',
        direction='input',
        flow_id='app-1',
        chat_id='chat-1',
        user_id=7,
        hits=hits if hits is not None else [{'word': '信贷违规', 'count': 2}],
        action='block',
        source=source,
        create_time=datetime(2026, 9, 17, 12, 0, 0),
    )


def _stub_rows(monkeypatch, rows):
    async def _alist(**kwargs):
        return rows, len(rows)

    monkeypatch.setattr(DAO_LIST_PATH, _alist)


async def test_platform_rows_lose_their_words_for_tenants(monkeypatch):
    """The words are gone; the fact of the block is not (spec AC-18)."""
    _stub_rows(monkeypatch, [_row(source=SOURCE_PLATFORM, hits=[{'word': '信贷违规', 'count': 2}])])
    login_user = SimpleNamespace(tenant_id=1, user_id=9)

    page = await ChatGuardService.alist_hit_logs(login_user, redact_platform_hits=True)

    item = page.data[0]
    assert item.hits == [], 'platform hit words must not reach a tenant'
    assert item.source == SOURCE_PLATFORM
    assert item.direction == 'input'
    assert item.action == 'block'
    assert item.hit_count == 1, 'the tenant still learns how many words matched'


async def test_tenant_rows_keep_their_words(monkeypatch):
    """Spec AC-19: the tenant's own policy content stays fully visible."""
    _stub_rows(monkeypatch, [_row(source=SOURCE_TENANT, hits=[{'word': '租户自配词', 'count': 1}])])
    login_user = SimpleNamespace(tenant_id=1, user_id=9)

    page = await ChatGuardService.alist_hit_logs(login_user, redact_platform_hits=True)

    assert [hit.word for hit in page.data[0].hits] == ['租户自配词']


async def test_a_null_source_is_treated_as_tenant(monkeypatch):
    """A NULL means an old process wrote the row mid-deploy.

    The column's server default is 'tenant', so NULL can only come from a
    process that predates the column. Defaulting the other way would blank out
    words the tenant is entitled to see.
    """
    _stub_rows(monkeypatch, [_row(source=None, hits=[{'word': '租户词', 'count': 1}])])
    login_user = SimpleNamespace(tenant_id=1, user_id=9)

    page = await ChatGuardService.alist_hit_logs(login_user, redact_platform_hits=True)

    assert page.data[0].source == SOURCE_TENANT
    assert [hit.word for hit in page.data[0].hits] == ['租户词']


async def test_mixed_page_redacts_only_the_platform_rows(monkeypatch):
    """The realistic case: both layers hit different rounds, one page shows both."""
    _stub_rows(
        monkeypatch,
        [
            _row(source=SOURCE_PLATFORM, hits=[{'word': '平台词', 'count': 1}], row_id=2),
            _row(source=SOURCE_TENANT, hits=[{'word': '租户词', 'count': 1}], row_id=1),
        ],
    )
    login_user = SimpleNamespace(tenant_id=1, user_id=9)

    page = await ChatGuardService.alist_hit_logs(login_user, redact_platform_hits=True)

    by_source = {item.source: item for item in page.data}
    assert by_source[SOURCE_PLATFORM].hits == []
    assert [hit.word for hit in by_source[SOURCE_TENANT].hits] == ['租户词']


async def test_redaction_is_off_by_default(monkeypatch):
    """Default preserves F056's behaviour for any caller that does not opt in."""
    _stub_rows(monkeypatch, [_row(source=SOURCE_PLATFORM, hits=[{'word': '平台词', 'count': 1}])])
    login_user = SimpleNamespace(tenant_id=1, user_id=9)

    page = await ChatGuardService.alist_hit_logs(login_user)

    assert [hit.word for hit in page.data[0].hits] == ['平台词']


async def test_super_admin_view_does_not_redact(monkeypatch):
    """Spec AC-20: the platform admin owns the library, so nothing is hidden.

    Redacting here would defeat the view's purpose — the admin is looking at it
    precisely to see which platform words are firing.
    """
    _stub_rows(monkeypatch, [_row(source=SOURCE_PLATFORM, hits=[{'word': '平台词', 'count': 3}], tenant_id=5)])

    page = await ChatGuardService.alist_hit_logs_for_super_admin()

    item = page.data[0]
    assert [hit.word for hit in item.hits] == ['平台词']
    assert item.tenant_id == 5, 'the cross-tenant view must attribute each row'
    assert item.hit_count == 1


async def test_super_admin_view_passes_the_source_filter_through(monkeypatch):
    """The tenant filter is bypassed but the explicit filters still apply."""
    captured = {}

    async def _alist(**kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(DAO_LIST_PATH, _alist)

    await ChatGuardService.alist_hit_logs_for_super_admin(tenant_id=None, source=SOURCE_PLATFORM)

    assert captured['tenant_id'] is None
    assert captured['source'] == SOURCE_PLATFORM
