"""F055 T-17: KnowledgeFileService.modify_file_effective_period regressions.

AC-17 — the new field maintenance honors the existing write permission model.
AC-08 — the edit only touches the DB row (takes effect at retrieval
immediately); it must never trigger a re-parse.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.common.errcode.knowledge import KnowledgeFileNotExistError
from bisheng.database.models.role_access import AccessType
from bisheng.knowledge.domain.schemas.knowledge_schema import ModifyFileEffectivePeriodReq
from bisheng.knowledge.domain.services.knowledge_file_service import KnowledgeFileService
from bisheng.knowledge.domain.services.knowledge_permission_service import KnowledgePermissionService


def _make_service(file_model, knowledge_model, access_allowed: bool):
    """Service with mocked repos; patch the permission check to a fixed answer."""
    file_repo = MagicMock()
    file_repo.find_by_id = AsyncMock(return_value=file_model)
    file_repo.update = AsyncMock(side_effect=lambda model: model)
    knowledge_repo = MagicMock()
    knowledge_repo.find_by_id = AsyncMock(return_value=knowledge_model)
    service = KnowledgeFileService(
        knowledge_file_repository=file_repo,
        knowledge_repository=knowledge_repo,
    )
    return service, file_repo


def _req(effective_date=None, expire_date=None) -> ModifyFileEffectivePeriodReq:
    return ModifyFileEffectivePeriodReq(knowledge_id=5, file_id=11, effective_date=effective_date, expire_date=expire_date)


async def test_write_permission_denied_propagates():
    """AC-17: without knowledge write access the edit never happens."""
    file_model = SimpleNamespace(id=11, knowledge_id=5, effective_date=None, expire_date=None, updater_id=None)
    knowledge_model = SimpleNamespace(id=5, user_id=1)
    service, file_repo = _make_service(file_model, knowledge_model, access_allowed=True)

    login_user = SimpleNamespace(user_id=7)
    with patch.object(
        KnowledgePermissionService, "check_access_async", AsyncMock(return_value=False)
    ) as perm_mock:
        with pytest.raises(UnAuthorizedError):
            await service.modify_file_effective_period(login_user, _req(effective_date=datetime(2026, 9, 6)))

    perm_mock.assert_awaited_once()
    assert perm_mock.await_args.kwargs["access_type"] == AccessType.KNOWLEDGE_WRITE
    file_repo.update.assert_not_called()


async def test_write_permission_granted_sets_dates_without_reparse():
    """AC-08: with write access the dates are stored on the row only — the
    update goes through the plain ORM repository, no pipeline/re-parse."""
    file_model = SimpleNamespace(id=11, knowledge_id=5, effective_date=None, expire_date=None, updater_id=None)
    knowledge_model = SimpleNamespace(id=5, user_id=1)
    service, file_repo = _make_service(file_model, knowledge_model, access_allowed=True)

    effective = datetime(2026, 9, 1)
    expire = datetime(2026, 12, 31)
    login_user = SimpleNamespace(user_id=7)
    with patch.object(KnowledgePermissionService, "check_access_async", AsyncMock(return_value=True)):
        result = await service.modify_file_effective_period(login_user, _req(effective, expire))

    assert result is file_model
    assert file_model.effective_date == effective
    assert file_model.expire_date == expire
    assert file_model.updater_id == 7
    file_repo.update.assert_awaited_once_with(file_model)


async def test_null_dates_mean_unlimited():
    """AC-06: null effective/expire dates are accepted and stored as null."""
    file_model = SimpleNamespace(id=11, knowledge_id=5, effective_date=None, expire_date=None, updater_id=None)
    knowledge_model = SimpleNamespace(id=5, user_id=1)
    service, file_repo = _make_service(file_model, knowledge_model, access_allowed=True)

    login_user = SimpleNamespace(user_id=7)
    with patch.object(KnowledgePermissionService, "check_access_async", AsyncMock(return_value=True)):
        await service.modify_file_effective_period(login_user, _req())

    assert file_model.effective_date is None
    assert file_model.expire_date is None


async def test_unknown_file_or_kb_mismatch_raises():
    """Foreign file_id / file not in the KB → KnowledgeFileNotExistError."""
    service, _ = _make_service(None, None, access_allowed=True)
    login_user = SimpleNamespace(user_id=7)

    with patch.object(KnowledgePermissionService, "check_access_async", AsyncMock(return_value=True)):
        with pytest.raises(KnowledgeFileNotExistError):
            await service.modify_file_effective_period(login_user, _req())

    mismatched = SimpleNamespace(id=11, knowledge_id=999, effective_date=None, expire_date=None, updater_id=None)
    service, _ = _make_service(mismatched, None, access_allowed=True)
    with patch.object(KnowledgePermissionService, "check_access_async", AsyncMock(return_value=True)):
        with pytest.raises(KnowledgeFileNotExistError):
            await service.modify_file_effective_period(login_user, _req())
