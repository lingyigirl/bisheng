"""F055 T-11: tag deletion cascades to resource links (AC-04).

Deleting a knowledge-base tag must also delete its TagLink rows so the
associated documents auto-detach (AC-04). The cascade lives in
``TagDao.delete_business_tag`` (shared by the document-KB and knowledge-space
callers). Service-level tests pin the write-permission gate + delegation; the
session-level tests pin the delete-tag + delete-links transaction.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.common.errcode.knowledge import KnowledgeTagExistError
from bisheng.database.models.tag import Tag, TagBusinessTypeEnum, TagDao
from bisheng.knowledge.domain.services.knowledge_service import KnowledgeService

# ---------------------------------------------------------------------------
# Fake async session — records exec/commit without a real DB
# ---------------------------------------------------------------------------


class _FakeSession:
    """Async context manager recording exec() statements; select-first result."""

    def __init__(self, first_result):
        self.exec_calls = []
        self.committed = False
        self._first_result = first_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def exec(self, statement):
        self.exec_calls.append(statement)
        result = MagicMock()
        result.first = MagicMock(return_value=self._first_result)
        return result

    async def commit(self):
        self.committed = True


def _patch_session(fake_session):
    """Patch tag.py's module-level get_async_db_session to yield fake_session."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=fake_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return patch("bisheng.database.models.tag.get_async_db_session", return_value=mock_cm)


def _compiled(statement) -> str:
    """Rough compiled SQL text (dialect-less) — enough to tell SELECT from DELETE."""
    from sqlalchemy.dialects import mysql

    return str(statement.compile(dialect=mysql.dialect()))


# ---------------------------------------------------------------------------
# DAO level — delete_business_tag cascade
# ---------------------------------------------------------------------------


async def test_delete_business_tag_deletes_tag_and_links():
    """Tag row + all its TagLink rows deleted in one transaction (AC-04)."""
    fake_tag = MagicMock(spec=Tag)
    fake_tag.id = 11
    session = _FakeSession(first_result=fake_tag)

    with _patch_session(session):
        result = await TagDao.delete_business_tag(11, business_type=TagBusinessTypeEnum.KNOWLEDGE, business_id="5")

    assert result is True
    assert session.committed is True
    # 1) scoped SELECT verifies ownership, 2) delete Tag, 3) cascade delete links
    assert len(session.exec_calls) == 3
    assert _compiled(session.exec_calls[0]).upper().startswith("SELECT")
    assert _compiled(session.exec_calls[1]).upper().startswith("DELETE")
    assert _compiled(session.exec_calls[2]).upper().startswith("DELETE")
    assert "tag" in _compiled(session.exec_calls[2]).lower()


async def test_delete_business_tag_foreign_tag_no_cascade():
    """A tag_id outside the business must NOT cascade-delete anything.

    The ownership SELECT comes back empty, so the link deletion never runs
    (cross-tenant link data must not be touched) and the call stays idempotent.
    """
    session = _FakeSession(first_result=None)

    with _patch_session(session):
        result = await TagDao.delete_business_tag(999, business_type=TagBusinessTypeEnum.KNOWLEDGE, business_id="5")

    assert result is True  # idempotent delete semantics (legacy callers expect True)
    assert session.committed is False
    assert len(session.exec_calls) == 1  # only the ownership SELECT


# ---------------------------------------------------------------------------
# Service level — delete_knowledge_tag
# ---------------------------------------------------------------------------


async def test_delete_knowledge_tag_requires_write_and_delegates():
    """Write permission gate first, then the DAO cascade with the KB scope."""
    login_user = SimpleNamespace(user_id=7)
    with (
        patch.object(KnowledgeService, "_get_writable_knowledge", AsyncMock(return_value=MagicMock())) as perm_mock,
        patch("bisheng.database.models.tag.TagDao.delete_business_tag", AsyncMock(return_value=True)) as delete_mock,
    ):
        result = await KnowledgeService.delete_knowledge_tag(login_user, knowledge_id=5, tag_id=11)

    assert result is True
    perm_mock.assert_awaited_once_with(login_user=login_user, knowledge_id=5)
    delete_mock.assert_awaited_once_with(11, business_id="5", business_type=TagBusinessTypeEnum.KNOWLEDGE)


async def test_delete_knowledge_tag_no_write_permission_propagates():
    """AC-17: without knowledge write access the cascade never runs."""
    login_user = SimpleNamespace(user_id=7)
    with (
        patch.object(
            KnowledgeService, "_get_writable_knowledge", AsyncMock(side_effect=UnAuthorizedError(msg="no permission"))
        ),
        patch("bisheng.database.models.tag.TagDao.delete_business_tag", AsyncMock()) as delete_mock,
    ):
        with pytest.raises(UnAuthorizedError):
            await KnowledgeService.delete_knowledge_tag(login_user, knowledge_id=5, tag_id=11)

    delete_mock.assert_not_called()


# ---------------------------------------------------------------------------
# AC-04 modify half — rename + duplicate-name rejection
# ---------------------------------------------------------------------------


async def test_update_knowledge_tag_renames_scoped_tag():
    """AC-04: renaming a KB tag updates the scoped tag (modify half of AC-04)."""
    login_user = SimpleNamespace(user_id=7)
    tag = Tag(id=11, name="旧名", business_type=TagBusinessTypeEnum.KNOWLEDGE, business_id="5")
    with (
        patch.object(KnowledgeService, "_get_writable_knowledge", AsyncMock(return_value=MagicMock())) as perm_mock,
        patch("bisheng.database.models.tag.TagDao.get_tag", AsyncMock(return_value=tag)),
        patch("bisheng.database.models.tag.TagDao.get_tags_by_business", AsyncMock(return_value=[])) as same_name_mock,
        patch("bisheng.database.models.tag.TagDao.aupdate_tag", AsyncMock(return_value=tag)) as update_mock,
    ):
        result = await KnowledgeService.update_knowledge_tag(login_user, knowledge_id=5, tag_id=11, tag_name="新名")

    assert result is tag
    perm_mock.assert_awaited_once_with(login_user=login_user, knowledge_id=5)
    same_name_mock.assert_awaited_once()
    update_mock.assert_awaited_once_with(11, name="新名")


async def test_update_knowledge_tag_duplicate_name_rejected():
    """AC-04: renaming onto an existing tag name → KnowledgeTagExistError."""

    login_user = SimpleNamespace(user_id=7)
    tag = Tag(id=11, name="旧名", business_type=TagBusinessTypeEnum.KNOWLEDGE, business_id="5")
    existing = [Tag(id=12, name="监管法规")]
    with (
        patch.object(KnowledgeService, "_get_writable_knowledge", AsyncMock(return_value=MagicMock())),
        patch("bisheng.database.models.tag.TagDao.get_tag", AsyncMock(return_value=tag)),
        patch("bisheng.database.models.tag.TagDao.get_tags_by_business", AsyncMock(return_value=existing)),
        patch("bisheng.database.models.tag.TagDao.aupdate_tag", AsyncMock()) as update_mock,
    ):
        with pytest.raises(KnowledgeTagExistError):
            await KnowledgeService.update_knowledge_tag(login_user, knowledge_id=5, tag_id=11, tag_name="监管法规")

    update_mock.assert_not_called()


async def test_update_knowledge_tag_self_name_is_not_a_duplicate():
    """Renaming a tag to its own name is a no-op rename, not a duplicate."""

    login_user = SimpleNamespace(user_id=7)
    tag = Tag(id=11, name="监管法规", business_type=TagBusinessTypeEnum.KNOWLEDGE, business_id="5")
    with (
        patch.object(KnowledgeService, "_get_writable_knowledge", AsyncMock(return_value=MagicMock())),
        patch("bisheng.database.models.tag.TagDao.get_tag", AsyncMock(return_value=tag)),
        patch("bisheng.database.models.tag.TagDao.get_tags_by_business", AsyncMock(return_value=[tag])),
        patch("bisheng.database.models.tag.TagDao.aupdate_tag", AsyncMock(return_value=tag)) as update_mock,
    ):
        result = await KnowledgeService.update_knowledge_tag(login_user, knowledge_id=5, tag_id=11, tag_name="监管法规")

    assert result is tag
    update_mock.assert_awaited_once()
