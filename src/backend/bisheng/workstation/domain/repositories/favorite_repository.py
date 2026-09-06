"""F047 favorite repository — CRUD for user_app_favorite.

Session is constructor-injected (matches the workstation module style):
callers open a session and pass it in, so tests can bind an in-memory
SQLite session without patching the global session factory.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlmodel import select

from bisheng.workstation.domain.models.user_app_favorite import UserAppFavorite


class FavoriteRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, *, user_id: int, app_id: int | str, app_type: int) -> bool:
        """Create a favorite row. Idempotent — returns True if newly created,
        False if the (user, app, type) row already existed. The unique
        constraint backs this up against concurrent double-clicks.
        ``app_id`` is normalized to str (UUID for workflows, int for assistants)."""
        app_id = str(app_id)
        if self._exists(user_id, app_id, app_type):
            return False
        self.session.add(UserAppFavorite(user_id=user_id, app_id=app_id, app_type=app_type))
        try:
            self.session.commit()
        except IntegrityError:
            # Concurrent duplicate insert lost the race — treat as already favorited.
            self.session.rollback()
            return False
        return True

    def remove(self, *, user_id: int, app_id: int | str, app_type: int) -> bool:
        """Delete a favorite row. Idempotent — returns True if a row was
        deleted, False if none matched."""
        app_id = str(app_id)
        row = self._get(user_id, app_id, app_type)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def list_by_user(self, user_id: int) -> list[UserAppFavorite]:
        """All favorites of a user, newest first."""
        statement = (
            select(UserAppFavorite)
            .where(UserAppFavorite.user_id == user_id)
            .order_by(UserAppFavorite.update_time.desc(), UserAppFavorite.id.desc())
        )
        return list(self.session.exec(statement).all())

    def ids_by_user(self, user_id: int) -> list[str]:
        """App ids of a user's favorites, newest first (enrichment input)."""
        return [row.app_id for row in self.list_by_user(user_id)]

    def _get(self, user_id: int, app_id: str, app_type: int) -> UserAppFavorite | None:
        statement = select(UserAppFavorite).where(
            UserAppFavorite.user_id == user_id,
            UserAppFavorite.app_id == app_id,
            UserAppFavorite.app_type == app_type,
        )
        return self.session.exec(statement).first()

    def _exists(self, user_id: int, app_id: str, app_type: int) -> bool:
        return self._get(user_id, app_id, app_type) is not None
