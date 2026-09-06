"""F047 favorite repository tests (AC-12, AC-13, AC-15, AC-23).

Test-first: these fail (ImportError) until FavoriteRepository is implemented.
"""
import pytest
from sqlalchemy import text

from bisheng.workstation.domain.repositories.favorite_repository import FavoriteRepository


@pytest.fixture(autouse=True)
def _tenant(tenant_context):
    """Register the tenant filter events (production behavior; idempotent)
    and set an active tenant context so the before_flush auto-fill sets
    tenant_id=1 and the do_orm_execute SELECT filter matches the same tenant."""
    from bisheng.core.database.tenant_filter import register_tenant_filter_events

    register_tenant_filter_events()
    tenant_context(1)


def _create_table(session):
    session.exec(text("""
        CREATE TABLE IF NOT EXISTS user_app_favorite (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            app_id INTEGER NOT NULL,
            app_type INTEGER NOT NULL,
            tenant_id INTEGER NOT NULL DEFAULT 1,
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            UNIQUE (user_id, app_id, app_type)
        )
    """))
    session.commit()


def test_add_favorite_returns_created(db_session):
    """AC-12: adding a favorite persists one row and reports creation."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    created = repo.add(user_id=1, app_id=10, app_type=5)

    assert created is True
    rows = repo.list_by_user(user_id=1)
    assert len(rows) == 1
    assert rows[0].app_id == '10'
    assert rows[0].app_type == 5


def test_add_duplicate_favorite_is_idempotent(db_session):
    """AC-12: same user+app+type is a single row, second add reports exists."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    assert repo.add(user_id=1, app_id=10, app_type=5) is True
    assert repo.add(user_id=1, app_id=10, app_type=5) is False
    assert len(repo.list_by_user(user_id=1)) == 1


def test_same_app_different_type_is_separate_favorite(db_session):
    """AC-12: uniqueness key includes app_type (assistant vs workflow)."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    repo.add(user_id=1, app_id=10, app_type=5)
    repo.add(user_id=1, app_id=10, app_type=10)

    assert len(repo.list_by_user(user_id=1)) == 2


def test_remove_favorite_is_idempotent(db_session):
    """AC-13: removing an existing / missing favorite behaves idempotently."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    repo.add(user_id=1, app_id=10, app_type=5)
    assert repo.remove(user_id=1, app_id=10, app_type=5) is True
    assert repo.remove(user_id=1, app_id=10, app_type=5) is False
    assert repo.list_by_user(user_id=1) == []


def test_list_by_user_only_returns_own_favorites(db_session):
    """AC-15: favorites are user-scoped — no cross-user leakage."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    repo.add(user_id=1, app_id=10, app_type=5)
    repo.add(user_id=2, app_id=11, app_type=5)

    rows = repo.list_by_user(user_id=1)
    assert [r.app_id for r in rows] == ['10']
    rows2 = repo.list_by_user(user_id=2)
    assert [r.app_id for r in rows2] == ['11']


def test_favorites_are_tenant_scoped(db_session):
    """AC-23: rows carry tenant_id so multi-tenant filtering applies."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    repo.add(user_id=1, app_id=10, app_type=5)
    rows = repo.list_by_user(user_id=1)
    assert rows[0].tenant_id is not None


def test_ids_by_user_returns_ordered_ids(db_session):
    """AC-14: ids list for enrichment, newest first (update_time desc)."""
    _create_table(db_session)
    repo = FavoriteRepository(db_session)

    repo.add(user_id=1, app_id=10, app_type=5)
    repo.add(user_id=1, app_id=11, app_type=5)

    ids = repo.ids_by_user(user_id=1)
    assert ids == ['11', '10']
