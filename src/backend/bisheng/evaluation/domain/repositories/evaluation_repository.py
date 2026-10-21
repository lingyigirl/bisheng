from datetime import datetime

from sqlalchemy import and_, func
from sqlmodel import select

from bisheng.core.database import get_sync_db_session
from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType


class EvaluationRepository:
    @classmethod
    def get_my_evaluations(cls, user_id: int, page: int, limit: int) -> tuple[list[Evaluation], int]:
        with get_sync_db_session() as session:
            statement = select(Evaluation).where(
                Evaluation.is_delete == 0, Evaluation.user_id == user_id, Evaluation.exec_type != ExecType.FLOW.value
            )
            count_statement = session.query(func.count(Evaluation.id)).where(
                Evaluation.is_delete == 0, Evaluation.user_id == user_id, Evaluation.exec_type != ExecType.FLOW.value
            )
            statement = statement.offset((page - 1) * limit).limit(limit).order_by(Evaluation.update_time.desc())
            return session.exec(statement).all(), session.exec(count_statement).scalar()

    @classmethod
    def delete_evaluation(cls, data: Evaluation) -> Evaluation:
        with get_sync_db_session() as session:
            data.is_delete = 1
            session.add(data)
            session.commit()
            return data

    @classmethod
    def get_user_one_evaluation(cls, user_id: int, evaluation_id: int) -> Evaluation:
        with get_sync_db_session() as session:
            statement = select(Evaluation).where(and_(Evaluation.id == evaluation_id, Evaluation.user_id == user_id))
            return session.exec(statement).first()

    @classmethod
    def get_one_evaluation(cls, evaluation_id: int) -> Evaluation:
        with get_sync_db_session() as session:
            return session.exec(select(Evaluation).where(Evaluation.id == evaluation_id)).first()

    @classmethod
    def get_compare_group(cls, group_id: str, user_id: int) -> list[Evaluation]:
        """F053: the (up to two) evaluations of one compare group, scoped to its owner."""
        with get_sync_db_session() as session:
            statement = select(Evaluation).where(
                and_(Evaluation.compare_group_id == group_id, Evaluation.is_delete == 0, Evaluation.user_id == user_id)
            )
            return list(session.exec(statement).all())

    @classmethod
    def get_latest_success_by_app_version(cls, unique_id: str, version: int | None = None) -> Evaluation | None:
        """F066: the most recent successful evaluation of an app (and version).

        Read from the approval detail, whose viewer is **not** the evaluation
        owner — so this is deliberately not owner-scoped. Tenant scoping still
        applies: the global session event injects ``tenant_id`` (constitution C3).
        Pass ``version=None`` for assistants (they carry no version).
        """
        with get_sync_db_session() as session:
            conditions = [
                Evaluation.unique_id == str(unique_id),
                Evaluation.is_delete == 0,
                Evaluation.status == EvaluationTaskStatus.success.value,
            ]
            if version:
                conditions.append(Evaluation.version == int(version))
            statement = select(Evaluation).where(and_(*conditions)).order_by(Evaluation.update_time.desc()).limit(1)
            return session.exec(statement).first()

    @classmethod
    def update_evaluation(cls, evaluation: Evaluation) -> Evaluation:
        with get_sync_db_session() as session:
            session.add(evaluation)
            session.commit()
            session.refresh(evaluation)
            return evaluation

    @classmethod
    def get_running_updated_before(cls, cutoff: datetime) -> list[Evaluation]:
        """F090 audit: ``running`` evaluations untouched since ``cutoff``.

        Candidate rows for the stale sweep. The caller must also require the
        per-run Redis lease to be gone before failing them — elapsed time alone
        cannot distinguish a dead runner from a long legitimate run. Must run
        under ``bypass_tenant_filter()``: the sweep is cross-tenant system
        maintenance invoked without a request tenant context.
        """
        with get_sync_db_session() as session:
            statement = select(Evaluation).where(
                Evaluation.status == EvaluationTaskStatus.running.value,
                Evaluation.is_delete == 0,
                Evaluation.update_time < cutoff,
            )
            return list(session.exec(statement).all())

    @classmethod
    def fail_evaluations(cls, evaluation_ids: list[int], description: str) -> int:
        """Mark the given evaluations failed with a shared reason (stale sweep)."""
        if not evaluation_ids:
            return 0
        with get_sync_db_session() as session:
            statement = select(Evaluation).where(Evaluation.id.in_(evaluation_ids))
            rows = list(session.exec(statement).all())
            for evaluation in rows:
                evaluation.status = EvaluationTaskStatus.failed.value
                evaluation.description = description
                session.add(evaluation)
            session.commit()
            return len(rows)
