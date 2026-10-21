from datetime import datetime, timedelta

from redis.exceptions import RedisError

import bisheng.evaluation.domain.services.evaluation_service as evaluation_service
from bisheng.evaluation.domain.models.evaluation import Evaluation, EvaluationTaskStatus, ExecType
from bisheng.evaluation.domain.repositories.evaluation_repository import EvaluationRepository

NOW = datetime(2026, 10, 21, 12, 0, 0)


def _make(session, status, updated_at):
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


class _FakeRedis:
    def __init__(self, existing_keys=()):
        self.existing_keys = set(existing_keys)

    def exists(self, key):
        return 1 if key in self.existing_keys else 0


def _lease_key(evaluation_id):
    return f"evaluation_task_progress_{evaluation_id}"


def test_sweep_fails_orphan_but_keeps_leased_long_run(sqlite_session_patch, monkeypatch):
    running = EvaluationTaskStatus.running.value
    orphan = _make(sqlite_session_patch, running, NOW - timedelta(hours=2))
    # Older than the threshold but still leased: a legitimately long run (many
    # questions) must survive the sweep.
    long_run = _make(sqlite_session_patch, running, NOW - timedelta(hours=5))
    fresh = _make(sqlite_session_patch, running, NOW - timedelta(minutes=5))
    done = _make(sqlite_session_patch, EvaluationTaskStatus.success.value, NOW - timedelta(hours=5))
    fake_redis = _FakeRedis({_lease_key(long_run.id)})
    monkeypatch.setattr(evaluation_service, "get_redis_client_sync", lambda: fake_redis)

    assert evaluation_service.sweep_stale_running_evaluations(now=NOW) == 1

    repaired = EvaluationRepository.get_one_evaluation(orphan.id)
    assert repaired.status == EvaluationTaskStatus.failed.value
    assert repaired.description == evaluation_service.STALE_RUNNING_DESCRIPTION
    assert EvaluationRepository.get_one_evaluation(long_run.id).status == running
    assert EvaluationRepository.get_one_evaluation(fresh.id).status == running
    assert EvaluationRepository.get_one_evaluation(done.id).status == EvaluationTaskStatus.success.value


def test_sweep_ignores_row_exactly_at_cutoff(sqlite_session_patch, monkeypatch):
    running = EvaluationTaskStatus.running.value
    row = _make(
        sqlite_session_patch,
        running,
        NOW - timedelta(seconds=evaluation_service.STALE_RUNNING_SECONDS),
    )
    monkeypatch.setattr(evaluation_service, "get_redis_client_sync", lambda: _FakeRedis())

    assert evaluation_service.sweep_stale_running_evaluations(now=NOW) == 0
    assert EvaluationRepository.get_one_evaluation(row.id).status == running


class _LeaseRedis:
    def __init__(self):
        self.sets = []
        self.deleted = []

    def set(self, key, value, **kwargs):
        self.sets.append((key, value, kwargs))

    def delete(self, key):
        self.deleted.append(key)


class _FlowEvaluation:
    file_path = "dataset.csv"
    exec_type = ExecType.FLOW.value
    status = EvaluationTaskStatus.running.value
    description = ""


async def test_runner_writes_progress_lease_before_first_question(monkeypatch):
    fake_redis = _LeaseRedis()
    monkeypatch.setattr(evaluation_service.EvaluationRepository, "get_one_evaluation", lambda **_: _FlowEvaluation())
    monkeypatch.setattr(evaluation_service.EvaluationService, "read_dataset_csv", lambda _: [{"question": "q"}])
    monkeypatch.setattr(evaluation_service.EvaluationRepository, "update_evaluation", lambda evaluation: evaluation)
    monkeypatch.setattr(evaluation_service, "get_redis_client_sync", lambda: fake_redis)

    await evaluation_service.add_evaluation_task(1)

    # RedisClient.set takes ``expiration`` (not redis-py's ``ex``); the lease
    # must be written before the first question so a long run is never swept.
    assert fake_redis.sets[0][:2] == ("evaluation_task_progress_1", 0)
    assert fake_redis.sets[0][2] == {"expiration": evaluation_service.EVALUATION_PROGRESS_TTL_SECONDS}
    assert fake_redis.deleted == ["evaluation_task_progress_1"]


def test_sweep_skips_everything_when_redis_is_down(sqlite_session_patch, monkeypatch):
    running = EvaluationTaskStatus.running.value
    orphan = _make(sqlite_session_patch, running, NOW - timedelta(hours=2))

    def _redis_down():
        raise RedisError("redis unavailable")

    monkeypatch.setattr(evaluation_service, "get_redis_client_sync", _redis_down)

    # Without the lease check a live run cannot be told apart from a corpse, so
    # the safe action is to leave every row alone and retry on the next tick.
    assert evaluation_service.sweep_stale_running_evaluations(now=NOW) == 0
    assert EvaluationRepository.get_one_evaluation(orphan.id).status == running
