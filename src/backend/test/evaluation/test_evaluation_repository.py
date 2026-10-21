from datetime import datetime, timedelta

from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType
from bisheng.evaluation.domain.repositories.evaluation_repository import EvaluationRepository


def _make(session, user_id=1, exec_type=ExecType.ASSISTANT.value):
    ev = Evaluation(user_id=user_id, unique_id="u1", exec_type=exec_type)
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def test_get_one_and_update(sqlite_session_patch):
    ev = _make(sqlite_session_patch)
    fetched = EvaluationRepository.get_one_evaluation(ev.id)
    assert fetched is not None and fetched.id == ev.id
    fetched.status = 3
    EvaluationRepository.update_evaluation(fetched)
    assert EvaluationRepository.get_one_evaluation(ev.id).status == 3


def test_list_excludes_flow_and_deleted(sqlite_session_patch):
    _make(sqlite_session_patch, exec_type=ExecType.ASSISTANT.value)
    _make(sqlite_session_patch, exec_type=ExecType.FLOW.value)
    rows, total = EvaluationRepository.get_my_evaluations(user_id=1, page=1, limit=10)
    assert total == 1 and all(r.exec_type != ExecType.FLOW.value for r in rows)


def _make_at(session, status, updated_at):
    ev = Evaluation(
        user_id=1,
        unique_id="u1",
        exec_type=ExecType.ASSISTANT.value,
        status=status,
        update_time=updated_at,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def test_get_running_updated_before_filters_status_cutoff_and_deleted(sqlite_session_patch):
    now = datetime(2026, 10, 21, 12, 0, 0)
    stale = _make_at(sqlite_session_patch, EvaluationTaskStatus.running.value, now - timedelta(hours=3))
    _make_at(sqlite_session_patch, EvaluationTaskStatus.running.value, now - timedelta(minutes=10))
    _make_at(sqlite_session_patch, EvaluationTaskStatus.success.value, now - timedelta(hours=3))
    deleted = _make_at(sqlite_session_patch, EvaluationTaskStatus.running.value, now - timedelta(hours=3))
    deleted.is_delete = 1
    sqlite_session_patch.add(deleted)
    sqlite_session_patch.commit()

    rows = EvaluationRepository.get_running_updated_before(now - timedelta(hours=1))

    assert [row.id for row in rows] == [stale.id]


def test_fail_evaluations_marks_only_given_ids(sqlite_session_patch):
    running = EvaluationTaskStatus.running.value
    failed = EvaluationTaskStatus.failed.value
    first = _make_at(sqlite_session_patch, running, datetime(2026, 10, 21, 10, 0, 0))
    second = _make_at(sqlite_session_patch, running, datetime(2026, 10, 21, 10, 0, 0))

    assert EvaluationRepository.fail_evaluations([first.id], "任务已中断") == 1

    marked = EvaluationRepository.get_one_evaluation(first.id)
    assert marked.status == failed and marked.description == "任务已中断"
    assert EvaluationRepository.get_one_evaluation(second.id).status == running
