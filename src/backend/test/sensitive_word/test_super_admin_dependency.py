"""T015/T016 (F059): platform endpoints admit the global super admin and nobody else.

This is the guard that keeps a TENANT administrator out of the platform word
library. `get_tenant_admin_user` — the dependency the tenant policy endpoints
use — deliberately admits a tenant's Child Admin, so reaching for it here (the
obvious-looking choice) would hand every tenant admin the cross-tenant word
library (release-contract INV-10, spec AC-06).
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bisheng.common.dependencies.super_admin import require_global_super_admin

CHECK_PATH = 'bisheng.common.dependencies.super_admin._check_is_global_super'


def _user(user_id=7, tenant_id=1):
    return SimpleNamespace(user_id=user_id, tenant_id=tenant_id)


async def test_super_admin_is_admitted(monkeypatch):
    async def _is_super(user_id):
        return True

    monkeypatch.setattr(CHECK_PATH, _is_super)

    assert await require_global_super_admin(_user()) is not None


async def test_tenant_admin_is_rejected(monkeypatch):
    """The whole point of the module: not a super admin means 403."""

    async def _not_super(user_id):
        return False

    monkeypatch.setattr(CHECK_PATH, _not_super)

    with pytest.raises(HTTPException) as excinfo:
        await require_global_super_admin(_user(tenant_id=5))

    assert excinfo.value.status_code == 403


async def test_a_failing_check_denies_rather_than_admits(monkeypatch):
    """Fails CLOSED.

    `_check_is_global_super` already degrades internally (Redis miss -> FGA ->
    role fallback), so an exception reaching here means every avenue failed. For
    a cross-tenant write surface that must be a denial: admitting on error would
    turn an FGA outage into a mass permission bypass.
    """

    async def _boom(user_id):
        raise RuntimeError('FGA unavailable')

    monkeypatch.setattr(CHECK_PATH, _boom)

    with pytest.raises(HTTPException) as excinfo:
        await require_global_super_admin(_user())

    assert excinfo.value.status_code == 403


async def test_a_user_without_an_id_is_rejected(monkeypatch):
    async def _is_super(user_id):
        return True

    monkeypatch.setattr(CHECK_PATH, _is_super)

    with pytest.raises(HTTPException) as excinfo:
        await require_global_super_admin(SimpleNamespace(user_id=None))

    assert excinfo.value.status_code == 403


async def test_the_check_receives_the_real_user_id(monkeypatch):
    """Guards against a copy-paste that would check the wrong subject."""
    seen = []

    async def _is_super(user_id):
        seen.append(user_id)
        return True

    monkeypatch.setattr(CHECK_PATH, _is_super)

    await require_global_super_admin(_user(user_id=4242))

    assert seen == [4242]
