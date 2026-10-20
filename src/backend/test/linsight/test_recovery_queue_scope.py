"""覆盖 AC: AC-04, AC-05, AC-13 - shared databases must not mix worker queues."""

from types import SimpleNamespace

from bisheng.linsight.domain.services import recovery_queue_scope as scope


def test_scoped_worker_ignores_foreign_and_legacy_queue(monkeypatch):
    monkeypatch.setattr(scope, "settings", SimpleNamespace(linsight_conf=SimpleNamespace(queue_namespace="isolated")))
    state = scope.scoped_recovery_state({"phase": "running"}, 100)
    assert state["retry_after"] > 2_000_000_000
    assert scope.recovery_scan_due(state, 101, "isolated")
    assert not scope.recovery_scan_due(state, 101, "linsight")
    assert not scope.recovery_scan_due({"retry_after": 0}, 101, "isolated")
    assert not scope.recovery_scan_due(state, 99, "isolated")


def test_default_queue_preserves_legacy_backoff(monkeypatch):
    monkeypatch.setattr(scope, "settings", SimpleNamespace(linsight_conf=SimpleNamespace(queue_namespace="")))
    state = scope.scoped_recovery_state({}, 100)
    assert state["retry_after"] == 100
    assert scope.recovery_scan_due({"retry_after": 100}, 101, "linsight")
