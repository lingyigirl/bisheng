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
