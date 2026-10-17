"""Unit tests for the F086 context-usage snapshot (context_usage.py).

Locks the contract the client relies on:
- real per-call token usage is accumulated (main vs subagents kept apart);
- ``last_input_tokens`` tracks the newest MAIN call (== current context size);
- the composition breakdown is a character-count estimate over the request;
- recording is best-effort — a dead Redis never breaks / raises into the task;
- the resilience middleware accepts (and only then calls) the recorder.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from bisheng.linsight.domain.services.context_usage import (
    ContextUsageRecorder,
    compose_breakdown,
)
from bisheng.linsight.domain.services.resilience_middleware import build_resilience_middleware


class _StubRedis:
    def __init__(self):
        self.store: dict = {}
        self.expirations: dict = {}

    async def aset(self, key, value, expiration=None):
        self.store[key] = value
        self.expirations[key] = expiration


class _BoomRedis:
    async def aset(self, *args, **kwargs):
        raise RuntimeError("redis down")


def _request(*, system="S" * 50, messages=None, tools=None):
    return SimpleNamespace(
        system_message=SimpleNamespace(content=system),
        messages=messages if messages is not None else [],
        tools=tools or [],
    )


async def test_recorder_accumulates_main_and_sub_usage_and_persists():
    recorder = ContextUsageRecorder("sv-1")
    stub = _StubRedis()
    recorder._redis = stub

    await recorder.record(is_subagent=False, usage={"input_tokens": 1000, "output_tokens": 50}, request=_request())
    await recorder.record(is_subagent=False, usage={"input_tokens": 1200, "output_tokens": 80}, request=_request())
    await recorder.record(is_subagent=True, usage={"input_tokens": 400, "output_tokens": 30}, request=None)

    main = recorder._snapshot["main"]
    assert main["calls"] == 2
    assert main["cum_input_tokens"] == 2200
    assert main["cum_output_tokens"] == 130
    assert main["last_input_tokens"] == 1200  # newest main call == current context
    assert main["last_output_tokens"] == 80
    assert recorder._snapshot["sub"] == {"calls": 1, "cum_input_tokens": 400, "cum_output_tokens": 30}

    assert recorder.key == "linsight:tasks:sv-1:context_usage"
    assert recorder.key in stub.store
    assert stub.expirations[recorder.key] == 7 * 24 * 3600


async def test_record_is_best_effort_when_redis_fails():
    recorder = ContextUsageRecorder("sv-2")
    recorder._redis = _BoomRedis()

    await recorder.record(is_subagent=False, usage=None, request=None)  # must not raise
    assert recorder._snapshot["main"]["calls"] == 1
    assert recorder._snapshot["main"]["last_input_tokens"] == 0


def test_compose_breakdown_splits_system_history_and_tools():
    class _Args(BaseModel):
        x: str = ""

    tool = SimpleNamespace(name="t", description="d" * 10, args_schema=_Args)
    request = _request(
        system="S" * 50,
        messages=[SystemMessage("sys"), HumanMessage("h" * 30)],
        tools=[tool],
    )

    breakdown = compose_breakdown(request)
    assert breakdown is not None
    assert breakdown["system_chars"] == 50 + len("sys")
    assert breakdown["history_chars"] == 30
    assert breakdown["tool_schema_chars"] > 0


def test_compose_breakdown_tolerates_missing_request():
    assert compose_breakdown(None) is None


def test_build_resilience_middleware_carries_the_recorder():
    conf = SimpleNamespace()
    recorder = ContextUsageRecorder("sv-3")

    bound = build_resilience_middleware(conf, is_subagent=True, usage_recorder=recorder)
    assert bound._usage_recorder is recorder
    assert build_resilience_middleware(conf, is_subagent=True)._usage_recorder is None


async def test_get_redis_resolves_the_real_helper(monkeypatch):
    """Regression (live 2026-10-16): the lazy import must name the REAL module.

    A wrong module path made every record() fail silently in the worker
    ("cannot import name ..." swallowed by the best-effort guard) and the stub
    redis in the other tests never exercised this path.
    """
    import bisheng.core.cache.redis_manager as redis_manager

    sentinel = object()

    async def _fake_get_redis_client():
        return sentinel

    monkeypatch.setattr(redis_manager, "get_redis_client", _fake_get_redis_client)
    recorder = ContextUsageRecorder("sv-import")
    assert await recorder._get_redis() is sentinel


class _FakeModelRequest:
    def __init__(self, *, system_message=None, messages=None, tools=None):
        self.system_message = system_message
        self.messages = messages if messages is not None else []
        self.tools = tools or []

    def override(self, **kwargs):
        clone = _FakeModelRequest(
            system_message=self.system_message, messages=list(self.messages), tools=list(self.tools)
        )
        for key, value in kwargs.items():
            setattr(clone, key, value)
        return clone


class _RecordingSink:
    def __init__(self):
        self.calls: list[dict] = []

    async def record(self, *, is_subagent, usage, request=None):
        self.calls.append({"is_subagent": is_subagent, "usage": usage, "request": request})


async def test_middleware_records_usage_on_every_successful_call():
    sink = _RecordingSink()
    conf = SimpleNamespace()
    middleware = build_resilience_middleware(conf, is_subagent=False, usage_recorder=sink)
    request = _FakeModelRequest(messages=[HumanMessage("hi")], tools=[])

    async def handler(_request):
        return AIMessage(
            content="done",
            usage_metadata={"input_tokens": 123, "output_tokens": 7, "total_tokens": 130},
        )

    await middleware.awrap_model_call(request, handler)

    assert len(sink.calls) == 1
    assert sink.calls[0]["is_subagent"] is False
    assert sink.calls[0]["usage"]["input_tokens"] == 123
