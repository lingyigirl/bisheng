"""Opt-in real HTTP side effects, process death and durable Redis receipts.

Set F074_EFFECT_REDIS_URL explicitly. Only UUID-scoped keys are mutated; no
business connector or worker is interrupted. The HTTP ledger survives child
process death, so duplicate sends cannot hide behind a mocked call count.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import ToolMessage
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from bisheng.linsight.domain.services import recovery_middleware
from bisheng.linsight.domain.services.recovery_context import (
    ExecutionFence,
    RecoveryBlocked,
    RecoveryLeaseLost,
    execution_fence,
)


@pytest.fixture
def external_ledger():
    redis_url = os.getenv("F074_EFFECT_REDIS_URL")
    if not redis_url:
        pytest.skip("Explicit isolated receipt test Redis URL required")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if self.path == "/disconnect":
                self.close_connection = True
                return
            body = json.dumps({"receipt": "synthetic-effect-1", "accepted": payload}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    task = "effect-test-" + uuid4().hex
    redis = Redis.from_url(redis_url)
    prefix = f"linsight:replay:{{1:{task}}}"
    assert redis.set(prefix + ":lease", "owner-1", nx=True, ex=120)
    assert redis.set(prefix + ":receipt-count", "0", nx=True, ex=120)

    def run(mode, *, owner="owner-1", name="external_action"):
        env = {**os.environ, "F074_EFFECT_URL": f"http://127.0.0.1:{server.server_port}"}
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child", task, owner, mode, name],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    try:
        yield SimpleNamespace(run=run, calls=calls, redis=redis, prefix=prefix)
    finally:
        # Delete only the three exact keys created by this UUID-scoped probe.
        redis.delete(prefix + ":lease", prefix + ":receipt-count", prefix + ":receipts")
        redis.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_completed_external_effect_survives_process_death_without_resend(external_ledger):
    ledger = external_ledger
    assert ledger.run("crash-after-done").returncode == 74
    ledger.redis.set(ledger.prefix + ":lease", "owner-2", ex=120)
    replay = ledger.run("replay", owner="owner-2")
    assert replay.returncode == 0, replay.stderr
    assert "synthetic-effect-1" in replay.stdout
    assert len(ledger.calls) == 1


@pytest.mark.parametrize("mode", ["crash-after-accepted", "disconnect"])
def test_unknown_external_outcome_blocks_new_owner_without_resend(external_ledger, mode):
    ledger = external_ledger
    first = ledger.run(mode)
    assert first.returncode == (73 if mode == "crash-after-accepted" else 75), first.stderr
    assert len(ledger.calls) == 1
    ledger.redis.set(ledger.prefix + ":lease", "owner-2", ex=120)
    replay = ledger.run("blocked", owner="owner-2")
    assert replay.returncode == 0, replay.stderr
    assert "BLOCKED_VALIDATE_AND_CALL" in replay.stdout
    assert len(ledger.calls) == 1


def test_lost_owner_cannot_publish_external_result_or_resend(external_ledger):
    ledger = external_ledger
    first = ledger.run("lose-lease")
    assert first.returncode == 76, first.stderr
    records = [json.loads(raw) for raw in ledger.redis.hvals(ledger.prefix + ":receipts")]
    assert len(records) == 1 and records[0]["state"] == "started"
    replay = ledger.run("blocked", owner="owner-2")
    assert replay.returncode == 0, replay.stderr
    assert len(ledger.calls) == 1


def test_external_read_named_write_is_not_treated_as_builtin_read(external_ledger):
    ledger = external_ledger
    assert ledger.run("crash-after-accepted", name="read_file").returncode == 73
    ledger.redis.set(ledger.prefix + ":lease", "owner-2", ex=120)
    replay = ledger.run("blocked", owner="owner-2", name="read_file")
    assert replay.returncode == 0, replay.stderr
    assert len(ledger.calls) == 1


async def _child(task, owner, mode, name):
    redis = AsyncRedis.from_url(os.environ["F074_EFFECT_REDIS_URL"])
    fence = ExecutionFence(task, 1, owner, 1 if owner == "owner-1" else 2, redis, None)
    token = execution_fence.set(fence)
    recovery_middleware.get_config = lambda: {"configurable": {"checkpoint_ns": "effect-probe"}}
    request = SimpleNamespace(
        tool_call={"id": "effect-call-1", "name": name, "args": {"marker": task}},
        tool=SimpleNamespace(tool_id=987654321),
    )

    async def handler(_request):
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            path = "/disconnect" if mode == "disconnect" else "/effect"
            response = await client.post(os.environ["F074_EFFECT_URL"] + path, json={"marker": task})
            response.raise_for_status()
        if mode == "crash-after-accepted":
            os._exit(73)
        if mode == "lose-lease":
            await redis.set(fence.lease_key, "owner-2", ex=120)
        return ToolMessage(content=response.text, tool_call_id="effect-call-1")

    try:
        middleware = recovery_middleware.RecoveryMiddleware()
        if mode == "blocked":
            with pytest.raises(RecoveryBlocked):
                await recovery_middleware.validate_receipts(fence)
            with pytest.raises(RecoveryBlocked):
                await middleware.awrap_tool_call(request, handler)
            print("BLOCKED_VALIDATE_AND_CALL")
            return
        if mode == "replay":
            await recovery_middleware.validate_receipts(fence)
        result = await middleware.awrap_tool_call(request, handler)
        if mode == "crash-after-done":
            os._exit(74)
        print(result.content)
    except httpx.RemoteProtocolError:
        if mode != "disconnect":
            raise
        raise SystemExit(75) from None
    except RecoveryLeaseLost:
        if mode != "lose-lease":
            raise
        raise SystemExit(76) from None
    finally:
        execution_fence.reset(token)
        await redis.aclose()


if __name__ == "__main__":
    assert sys.argv[1] == "--child"
    asyncio.run(_child(*sys.argv[2:]))
