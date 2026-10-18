"""Exercise the actual Redis Lua protocol using an in-process Redis emulator."""

import pytest

from bisheng.linsight.domain.services.event_replay import EventReplayService

fakeredis = pytest.importorskip("fakeredis.aioredis")


async def test_retries_do_not_duplicate_and_readers_do_not_consume():
    async with fakeredis.FakeRedis() as redis:
        replay = EventReplayService(redis, 1, "task")
        first = await replay.append({"event_type": "replay_init", "data": {}}, "init")
        event = {"event_type": "task_execute_step", "data": {"call_id": "a", "output": "x"}}
        saved = await replay.append(event, "producer")
        assert await replay.append(event, "producer") == saved
        assert await replay.read_after(first) == await replay.read_after(first)
        snapshot = await replay.snapshot()
        assert len(snapshot["snapshot"]["events"]) == 2
        assert snapshot["last_event_id"] == saved
        assert snapshot["complete_history"] is True


async def test_trimmed_and_lost_streams_have_explicit_reset_cursor():
    async with fakeredis.FakeRedis() as redis:
        replay = EventReplayService(redis, 1, "task")
        replay.MAX_EVENTS = 2
        first = await replay.append({"event_type": "replay_init", "data": {}}, "init")
        for i in range(3):
            await replay.append({"event_type": "task_execute_step", "data": {"i": i}})
        assert not await replay.valid_cursor(first)
        assert not (await replay.snapshot())["complete_history"]
        await redis.delete(replay.key)
        empty = await replay.snapshot()
        assert not empty["complete_history"]
        assert empty["last_event_id"] != "0-0"
        assert await replay.valid_cursor(empty["last_event_id"])


async def test_scope_and_future_cursor():
    async with fakeredis.FakeRedis() as redis:
        one = EventReplayService(redis, 1, "task")
        two = EventReplayService(redis, 2, "task")
        await one.append({"event_type": "replay_init", "data": {}})
        assert not (await two.snapshot())["complete_history"]
        assert not await one.valid_cursor("999999999999999999-0")
        assert not await one.valid_cursor("../key")


async def test_batched_compaction_keeps_text_timestamps_epochs_and_tail_cursor():
    async with fakeredis.FakeRedis() as redis:
        replay = EventReplayService(redis, 1, "compact")
        replay.SNAPSHOT_BATCH = 2
        await replay.append({"event_type": "replay_init", "data": {}})
        for epoch, text, timestamp in [(1, "same", 10), (1, "same", 20), (2, "new", 30)]:
            await replay.append(
                {
                    "event_type": "task_execute_step",
                    "execution_epoch": epoch,
                    "data": {
                        "call_id": "model",
                        "step_type": "thinking",
                        "output": text,
                        "timestamp": timestamp,
                    },
                }
            )
        final = await replay.append({"event_type": "final_result", "data": {"answer": "done"}})
        raw = await replay.snapshot()
        compact = await replay.snapshot(compact=True)
        assert len(raw["snapshot"]["events"]) == 5
        events = compact["snapshot"]["events"]
        assert len(events) == 4
        assert events[1]["data"]["output"] == "samesame"
        assert events[1]["data"]["started_at"] == 10
        assert events[1]["data"]["timestamp"] == 20
        assert events[1]["event_id"] == raw["snapshot"]["events"][2]["event_id"]
        assert events[2]["data"]["output"] == "new"
        assert compact["last_event_id"] == final
        assert compact["terminal"] and compact["complete_history"]


async def test_appends_during_paging_are_delivered_after_fixed_snapshot_boundary(monkeypatch):
    async with fakeredis.FakeRedis() as redis:
        replay = EventReplayService(redis, 1, "paging")
        replay.SNAPSHOT_BATCH = 1
        first = await replay.append({"event_type": "replay_init", "data": {}})
        boundary = await replay.append({"event_type": "task_execute_step", "data": {}})
        original = redis.xrange
        added = None

        async def xrange(*args, **kwargs):
            nonlocal added
            if kwargs.get("count") == 1 and kwargs.get("min") == "-" and added is None:
                added = await replay.append({"event_type": "final_result", "data": {}})
            return await original(*args, **kwargs)

        monkeypatch.setattr(redis, "xrange", xrange)
        snapshot = await replay.snapshot(compact=True)
        assert [e["event_id"] for e in snapshot["snapshot"]["events"]] == [first, boundary]
        assert snapshot["last_event_id"] == boundary
        assert snapshot["complete_history"] and not snapshot["terminal"]
        assert [e["event_id"] for e in await replay.read_after(boundary)] == [added]


async def test_trim_during_paging_never_claims_complete_history(monkeypatch):
    async with fakeredis.FakeRedis() as redis:
        replay = EventReplayService(redis, 1, "trimming")
        replay.SNAPSHOT_BATCH = 1
        await replay.append({"event_type": "replay_init", "data": {}})
        await replay.append({"event_type": "task_execute_step", "data": {}})
        original = redis.xrange

        async def xrange(*args, **kwargs):
            if kwargs.get("min", "").startswith("("):
                await redis.xtrim(replay.key, maxlen=1, approximate=False)
            return await original(*args, **kwargs)

        monkeypatch.setattr(redis, "xrange", xrange)
        assert not (await replay.snapshot())["complete_history"]
