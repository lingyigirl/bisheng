"""A stalled workflow run is classified before it is reported.

The 10s stall check in ``RedisCallback.get_response_until_break`` used to always
report ``WorkFlowTaskBusyError`` (10540, "server thread count is full"). That
message is an inference, not a measurement — the wait is simply "no worker has
picked the task up yet", which has two very different causes:

* every workflow worker is busy        -> retry later   (10540)
* nothing is consuming the queue       -> start a worker (10541 + reason)

Without the split, a deployment that never started a worker is told its thread
pool is full, which sends the operator after the wrong thing entirely.
"""

import time
from unittest.mock import MagicMock

import pytest

from bisheng.worker.utils.stateful_worker import StatefulWorker
from bisheng.worker.workflow import redis_callback as redis_callback_module
from bisheng.worker.workflow.redis_callback import RedisCallback
from bisheng.workflow.common.workflow import WorkflowStatus

_WORKER_ALIVE_KEY = "celery_worker_alive_queues"


@pytest.fixture()
def real_alive_key(monkeypatch):
    """Restore the production literal for ``WORKER_ALIVE_KEY``.

    The shared import-chain premock stubs ``bisheng.worker.main`` with a
    MagicMock, so ``from bisheng.worker.main import WORKER_ALIVE_KEY`` binds a
    MagicMock rather than the string. Keying the fake redis by that would look up
    a different key than production uses, and every liveness check would read as
    empty. Pin it so these tests exercise the real key name.
    """
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.WORKER_ALIVE_KEY", _WORKER_ALIVE_KEY
    )


class _FakeSyncRedis:
    """Minimal sync redis standing in for the workflow status/stop keys."""

    def __init__(self, alive_queues: dict | None = None, status: dict | None = None):
        self.hashes = {_WORKER_ALIVE_KEY: alive_queues or {}}
        self.store = {"workflow:u1:status": status} if status else {}
        self.writes = []

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, expiration=None):
        self.store[key] = value
        self.writes.append((key, value))

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    def hgetall(self, name):
        return dict(self.hashes.get(name, {}))


def _stalled_status() -> dict:
    return {"status": WorkflowStatus.WAITING.value, "reason": None, "time": time.time() - 60}


def _make_callback(monkeypatch, fake_redis) -> RedisCallback:
    monkeypatch.setattr(redis_callback_module, "get_redis_client_sync", lambda: fake_redis)
    # __init__ reads the workflow timeout from config; the shared import-chain
    # premock leaves redis-backed config unreadable, so stub just that lookup.
    conf = MagicMock()
    conf.get_workflow_conf.return_value.timeout = 10
    monkeypatch.setattr(redis_callback_module, "settings", conf)
    callback = RedisCallback("u1", "flow-1", "chat-1", 1)
    # stop_workflow.delay would pull in the celery app. Record the dispatch flag so
    # tests can assert the stall path passes dispatch=False — patching the method
    # away entirely would hide the very flag under test.
    monkeypatch.setattr(
        RedisCallback,
        "set_workflow_stop",
        lambda self, dispatch=True: fake_redis.writes.append(("stop", dispatch)),
    )
    return callback


# --- StatefulWorker.has_alive_node -------------------------------------------------


def test_has_alive_node_sync_reads_heartbeat_within_window(monkeypatch, real_alive_key):
    fresh = str(int(time.time()))
    fake = _FakeSyncRedis(alive_queues={"workflow_celery": fresh})
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )
    assert StatefulWorker().has_alive_node_sync() is True


def test_has_alive_node_sync_false_when_heartbeat_is_stale(monkeypatch, real_alive_key):
    stale = str(int(time.time()) - 3600)
    fake = _FakeSyncRedis(alive_queues={"workflow_celery": stale})
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )
    assert StatefulWorker().has_alive_node_sync() is False


def test_has_alive_node_sync_false_when_no_worker_ever_registered(monkeypatch, real_alive_key):
    """The reported case: no worker started, so the hash has no entry at all."""
    fake = _FakeSyncRedis(alive_queues={})
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )
    assert StatefulWorker().has_alive_node_sync() is False


def test_has_alive_node_sync_ignores_queues_of_other_prefixes(monkeypatch, real_alive_key):
    """Only workflow queues count — a live knowledge worker is not a live workflow worker."""
    fresh = str(int(time.time()))
    fake = _FakeSyncRedis(alive_queues={"knowledge_celery": fresh})
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )
    assert StatefulWorker().has_alive_node_sync() is False


def test_has_alive_node_sync_does_not_mutate_hash_ring(monkeypatch, real_alive_key):
    """Unlike update_alive_nodes_sync, classifying a stall must not rewire dispatch."""
    fresh = str(int(time.time()))
    fake = _FakeSyncRedis(alive_queues={"workflow_celery": fresh})
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )
    worker = StatefulWorker()
    worker.has_alive_node_sync()
    assert worker.consistent_hash.get_all_nodes() == []


# --- stall classification ----------------------------------------------------------


def test_stall_with_no_live_worker_reports_task_other_not_busy(monkeypatch):
    fake = _FakeSyncRedis(status=_stalled_status())
    callback = _make_callback(monkeypatch, fake)
    monkeypatch.setattr(RedisCallback, "_has_alive_worker", lambda self: False)

    events = list(callback.sync_get_response_until_break())

    assert len(events) == 1
    assert events[0].message["status_code"] == 10541
    assert "no alive workflow worker" in events[0].message["data"]["exception"]


def test_stall_with_live_worker_still_reports_busy(monkeypatch):
    fake = _FakeSyncRedis(status=_stalled_status())
    callback = _make_callback(monkeypatch, fake)
    monkeypatch.setattr(RedisCallback, "_has_alive_worker", lambda self: True)

    events = list(callback.sync_get_response_until_break())

    assert len(events) == 1
    assert events[0].message["status_code"] == 10540


@pytest.mark.parametrize("alive", [True, False])
def test_stall_always_marks_the_run_stopped(monkeypatch, alive):
    """Closes the race where a worker claims the task right after we report failure."""
    fake = _FakeSyncRedis(status=_stalled_status())
    callback = _make_callback(monkeypatch, fake)
    monkeypatch.setattr(RedisCallback, "_has_alive_worker", lambda self: alive)

    list(callback.sync_get_response_until_break())

    assert ("stop", False) in fake.writes


@pytest.mark.parametrize("alive", [True, False])
def test_stall_does_not_dispatch_stop_workflow(monkeypatch, alive):
    """The stall path must only set the stop key, never publish stop_workflow.

    Dispatching would push a task onto the queue we just found nobody consuming;
    when a worker finally runs it, there is no cached workflow object to stop, so
    it overwrites the failure reason with "workflow stop by user" — which
    parse_workflow_failed maps to None, and the user's reconnect shows a silent
    close instead of the error we reported.
    """
    fake = _FakeSyncRedis(status=_stalled_status())
    callback = _make_callback(monkeypatch, fake)
    monkeypatch.setattr(RedisCallback, "_has_alive_worker", lambda self: alive)

    list(callback.sync_get_response_until_break())

    assert [w for w in fake.writes if w[0] == "stop"] == [("stop", False)]


# --- async stall classification ----------------------------------------------------
# This is the path the platform chat and the open API actually take
# (workflow_client._workflow_run / open_endpoints.workflow.invoke_workflow); the
# sync variant above is only used by evaluation. Both need their own coverage.


class _FakeAsyncRedis:
    def __init__(self, alive_queues: dict | None = None, status: dict | None = None):
        self.store = {"workflow:u1:status": status} if status else {}
        self.writes = []

    async def aget(self, key):
        return self.store.get(key)

    async def aset(self, key, value, expiration=None):
        self.store[key] = value
        self.writes.append((key, value))

    async def adelete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    async def alpop(self, key):
        return None


def _make_async_callback(monkeypatch, fake_redis) -> RedisCallback:
    monkeypatch.setattr(redis_callback_module, "get_redis_client_sync", lambda: fake_redis)
    conf = MagicMock()
    conf.get_workflow_conf.return_value.timeout = 10
    monkeypatch.setattr(redis_callback_module, "settings", conf)
    callback = RedisCallback("u1", "flow-1", "chat-1", 1)

    async def _stop(self, dispatch=True):
        fake_redis.writes.append(("stop", dispatch))

    monkeypatch.setattr(RedisCallback, "async_set_workflow_stop", _stop)
    return callback


async def test_async_stall_with_no_live_worker_reports_task_other(monkeypatch):
    fake = _FakeAsyncRedis(status=_stalled_status())
    callback = _make_async_callback(monkeypatch, fake)

    async def _no(self):
        return False

    monkeypatch.setattr(RedisCallback, "_has_alive_worker_async", _no)

    events = [e async for e in callback.get_response_until_break()]

    assert len(events) == 1
    assert events[0].message["status_code"] == 10541
    assert "no alive workflow worker" in events[0].message["data"]["exception"]


async def test_async_stall_with_live_worker_still_reports_busy(monkeypatch):
    fake = _FakeAsyncRedis(status=_stalled_status())
    callback = _make_async_callback(monkeypatch, fake)

    async def _yes(self):
        return True

    monkeypatch.setattr(RedisCallback, "_has_alive_worker_async", _yes)

    events = [e async for e in callback.get_response_until_break()]

    assert len(events) == 1
    assert events[0].message["status_code"] == 10540


async def test_async_stall_always_marks_the_run_stopped(monkeypatch):
    fake = _FakeAsyncRedis(status=_stalled_status())
    callback = _make_async_callback(monkeypatch, fake)

    async def _no(self):
        return False

    monkeypatch.setattr(RedisCallback, "_has_alive_worker_async", _no)

    [e async for e in callback.get_response_until_break()]

    assert [w for w in fake.writes if w[0] == "stop"] == [("stop", False)]


# --- the lazy-import seam ----------------------------------------------------------


def test_liveness_check_resolves_the_shared_dispatcher(monkeypatch, real_alive_key):
    """``_has_alive_worker`` must reach the real StatefulWorker singleton.

    The import is lazy (tasks.py imports this module back), so a broken seam would
    only surface at runtime, inside a generator, on the stall path. Exercise the
    real call rather than a patched stand-in.
    """
    fresh = str(int(time.time()))
    fake = _FakeSyncRedis(alive_queues={"workflow_celery": fresh})
    monkeypatch.setattr(redis_callback_module, "get_redis_client_sync", lambda: fake)
    monkeypatch.setattr(
        "bisheng.worker.utils.stateful_worker.get_redis_client_sync", lambda: fake
    )

    conf = MagicMock()
    conf.get_workflow_conf.return_value.timeout = 10
    monkeypatch.setattr(redis_callback_module, "settings", conf)
    callback = RedisCallback("u1", "flow-1", "chat-1", 1)

    assert callback._has_alive_worker() is True
