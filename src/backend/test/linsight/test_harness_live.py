"""Opt-in checks against a running API/worker and real middleware.

F074_LIVE_AUTH_FILE points to an ignored JSON file containing ``cookies``.
F074_LIVE_TASK_ID identifies a completed two-member task created with a real
provider. These checks never submit a model request or mutate that task.
F074_LIVE_REDIS_URL enables the independent, uniquely named Redis probe.
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from websockets.asyncio.client import connect

from bisheng.linsight.domain.services.event_replay import EventReplayService


@pytest.fixture
def live_api():
    auth_file = os.getenv("F074_LIVE_AUTH_FILE")
    task_id = os.getenv("F074_LIVE_TASK_ID")
    if not auth_file or not task_id:
        pytest.skip("A live authenticated Harness task is required")
    cookies = json.loads(Path(auth_file).read_text(encoding="utf-8"))["cookies"]
    base = os.getenv("F074_LIVE_API_URL", "http://127.0.0.1:7860").rstrip("/")
    with httpx.Client(base_url=base, cookies=cookies, timeout=30) as client:
        response = client.get("/api/v1/linsight/workbench/replay-snapshot", params={"session_version_id": task_id})
        response.raise_for_status()
        envelope = response.json()
        assert envelope["status_code"] == 200
        yield base, cookies, task_id, envelope["data"]


def test_live_completed_members_have_distinct_invocations(live_api):
    _, _, _, snapshot = live_api
    assert snapshot["terminal"] and snapshot["complete_history"]
    events = snapshot["snapshot"]["events"]
    assert events[-1]["event_type"] == "final_result"
    identities = {}
    for event in events:
        extra = event.get("data", {}).get("extra_info", {}) or {}
        if extra.get("invocation_id"):
            identities.setdefault(extra["agent_id"], set()).add(extra["invocation_id"])
    assert len(identities) >= 2
    member_calls = list(identities.values())
    assert all(not left.intersection(right) for i, left in enumerate(member_calls) for right in member_calls[i + 1 :])


async def test_live_websocket_reconnect_replays_exact_order(live_api):
    base, cookies, task_id, snapshot = live_api
    events = snapshot["snapshot"]["events"]
    assert len(events) > 20 and snapshot["terminal"]
    url = base.replace("http", "ws", 1) + "/api/v1/linsight/workbench/task-message-stream"
    url += "?session_version_id=" + task_id
    headers = {"Cookie": "; ".join(f"{key}={value}" for key, value in cookies.items())}
    async with asyncio.timeout(60):
        async with connect(url + "&after=" + events[0]["event_id"], additional_headers=headers) as ws:
            received = [json.loads(await ws.recv()) for _ in range(15)]
        async with connect(url + "&after=" + received[-1]["event_id"], additional_headers=headers) as ws:
            async for raw in ws:
                received.append(json.loads(raw))
    assert [event["event_id"] for event in received] == [event["event_id"] for event in events[1:]]


async def test_live_missing_cursor_requests_snapshot(live_api):
    base, cookies, task_id, _ = live_api
    url = base.replace("http", "ws", 1) + "/api/v1/linsight/workbench/task-message-stream"
    headers = {"Cookie": "; ".join(f"{key}={value}" for key, value in cookies.items())}
    async with connect(url + "?session_version_id=" + task_id + "&after=1-0", additional_headers=headers) as ws:
        event = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert event["event_type"] == "replay_reset"


def test_live_anonymous_snapshot_is_denied(live_api):
    base, _, task_id, _ = live_api
    response = httpx.get(
        base + "/api/v1/linsight/workbench/replay-snapshot", params={"session_version_id": task_id}, timeout=10
    )
    assert response.status_code == 401


async def test_live_redis_atomic_replay_and_trim():
    url = os.getenv("F074_LIVE_REDIS_URL")
    if not url:
        pytest.skip("A real Redis endpoint is required")
    redis = Redis.from_url(url)
    replay = EventReplayService(redis, 1, "e2e-f074-" + uuid4().hex)
    replay.MAX_EVENTS = 8
    try:
        first = await replay.append({"event_type": "replay_init", "data": {}}, "init")
        assert first == await replay.append({"event_type": "replay_init", "data": {}}, "init")
        assert (await replay.snapshot())["complete_history"]
        ids = await asyncio.gather(
            *(replay.append({"event_type": "exec_step", "data": {"i": i}}, str(i)) for i in range(6))
        )
        assert len(set(ids)) == 6
        assert len(await replay.read_after(first)) == 6
        await replay.append({"event_type": "final_result", "data": {}}, "end")
        assert (await replay.snapshot())["terminal"]
        await replay.append({"event_type": "exec_step", "data": {}}, "trim")
        assert not await replay.valid_cursor(first)
        assert not (await replay.snapshot())["complete_history"]
        assert await redis.hlen(replay.dedup_key) == 8
    finally:
        await redis.delete(replay.key, replay.dedup_key)
        await redis.aclose()
