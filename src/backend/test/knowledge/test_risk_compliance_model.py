"""F055 T-01: risk-compliance KB model fields + preset classification tags.

Covers spec AC-01 (four preset categories available at creation), AC-06
(no effective dates == always effective, i.e. nullable defaults) and the
AC-05 effective-window DAO predicate boundaries (spec section 3 date
semantics — effective FROM the effective date, THROUGH the expire date).
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bisheng.database.models.tag import Tag, TagDao
from bisheng.knowledge.domain.models.knowledge import Knowledge
from bisheng.knowledge.domain.models.knowledge_file import KnowledgeFile, KnowledgeFileDao
from bisheng.knowledge.domain.services.knowledge_service import KnowledgeService

PRESET_TAG_NAMES = KnowledgeService.PRESET_CLASSIFICATION_TAG_NAMES


class TestModelFieldSemantics:
    """AC-09/AC-10 fields default to the legacy behavior; AC-06 dates are nullable."""

    def test_weight_defaults_to_one(self):
        assert Knowledge(name="合规库").weight == 1.0

    def test_priority_defaults_to_zero(self):
        assert Knowledge(name="合规库").priority == 0

    def test_file_effective_dates_default_null(self):
        f = KnowledgeFile(knowledge_id=1, file_name="a.pdf")
        assert f.effective_date is None
        assert f.expire_date is None


class TestPresetClassificationTags:
    """AC-01: creating a KB offers the four preset categories, idempotently."""

    def _login_user(self):
        return SimpleNamespace(user_id=1)

    async def test_init_creates_four_preset_tags(self):
        created: list[str] = []

        async def fake_insert(tag: Tag) -> Tag:
            created.append(tag.name)
            return tag

        with patch.object(
            KnowledgeService, "_get_writable_knowledge", AsyncMock()
        ), patch.object(
            TagDao, "get_tags_by_business", AsyncMock(return_value=[])
        ), patch.object(
            TagDao, "ainsert_tag", AsyncMock(side_effect=fake_insert)
        ):
            await KnowledgeService.ensure_preset_classification_tags(self._login_user(), 1)

        assert created == list(PRESET_TAG_NAMES)

    async def test_init_is_idempotent_when_tags_exist(self):
        existing = [Tag(id=1, name=name) for name in PRESET_TAG_NAMES]

        with patch.object(
            KnowledgeService, "_get_writable_knowledge", AsyncMock()
        ), patch.object(
            TagDao, "get_tags_by_business", AsyncMock(return_value=existing)
        ), patch.object(TagDao, "ainsert_tag", AsyncMock()) as mock_insert:
            await KnowledgeService.ensure_preset_classification_tags(self._login_user(), 1)

        # existing tags are reused, no new rows
        mock_insert.assert_not_called()
        assert len(existing) == 4

    async def test_init_creates_only_missing_tags(self):
        existing = [Tag(id=1, name="行内制度")]

        async def fake_get_tags(**kwargs):
            return existing

        with patch.object(
            KnowledgeService, "_get_writable_knowledge", AsyncMock()
        ), patch.object(
            TagDao, "get_tags_by_business", AsyncMock(side_effect=fake_get_tags)
        ), patch.object(TagDao, "ainsert_tag", AsyncMock()) as mock_insert:
            await KnowledgeService.ensure_preset_classification_tags(self._login_user(), 1)

        # only the three missing presets are created
        inserted_names = [c.args[0].name for c in mock_insert.await_args_list]
        assert sorted(inserted_names) == sorted(["监管法规", "行业分析", "历史案例"])


class TestEffectiveWindowDaoBoundary:
    """AC-05 predicate boundaries (spec section 3 date semantics).

    The DAO predicate must treat the boundary dates inclusively: a file with
    effective_date = today is effective FROM today, and one with expire_date =
    today stays effective THROUGH today — comparing against raw datetime.now()
    would flip both at 00:00. Regression guard for the _f055_today_midnight
    helper.
    """

    KB_ID = 42
    today = datetime.combine(date.today(), datetime.min.time())

    def _dao_session(self, monkeypatch, db_session):
        """Point the DAO's session factory at the test sqlite session."""
        from contextlib import contextmanager

        @contextmanager
        def _session():
            yield db_session

        monkeypatch.setattr("bisheng.knowledge.domain.models.knowledge_file.get_sync_db_session", _session)

    def _insert(self, db_session, tenant_context, **dates) -> KnowledgeFile:
        tenant_context(1)
        f = KnowledgeFile(knowledge_id=self.KB_ID, file_name="boundary.pdf", **dates)
        db_session.add(f)
        db_session.flush()
        return f

    def test_expire_date_today_still_effective(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        """expire_date = today → effective through today (not in the excluded set)."""
        f = self._insert(db_session, tenant_context, expire_date=self.today)
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID])
        assert f.id not in excluded

    def test_expire_date_yesterday_expired(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        f = self._insert(db_session, tenant_context, expire_date=self.today - timedelta(days=1))
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID])
        assert f.id in excluded

    def test_effective_date_today_already_effective(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        """effective_date = today → effective FROM today (not "not yet effective")."""
        f = self._insert(db_session, tenant_context, effective_date=self.today)
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID])
        assert f.id not in excluded

    def test_effective_date_tomorrow_not_yet_effective(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        f = self._insert(db_session, tenant_context, effective_date=self.today + timedelta(days=1))
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID])
        assert f.id in excluded

    def test_null_dates_always_effective(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        """AC-06: legacy rows with NULL dates never enter the excluded set."""
        f = self._insert(db_session, tenant_context)
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID])
        assert f.id not in excluded

    def test_other_knowledge_ids_ignored(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        f = self._insert(db_session, tenant_context, expire_date=self.today - timedelta(days=1))
        excluded = KnowledgeFileDao.get_non_effective_file_ids_by_knowledge_ids([self.KB_ID + 1])
        assert f.id not in excluded

    def test_count_stats_mirror_the_exclusion_predicate(self, db_session, tenant_context, monkeypatch):
        self._dao_session(monkeypatch, db_session)
        """AC-14: the hit-test stats describe exactly what the filter drops."""
        self._insert(db_session, tenant_context, expire_date=self.today - timedelta(days=1))
        self._insert(db_session, tenant_context, effective_date=self.today + timedelta(days=1))
        self._insert(db_session, tenant_context, expire_date=self.today)  # still effective
        expired, not_yet_effective = KnowledgeFileDao.count_non_effective_files_by_knowledge_id(self.KB_ID)
        assert (expired, not_yet_effective) == (1, 1)
