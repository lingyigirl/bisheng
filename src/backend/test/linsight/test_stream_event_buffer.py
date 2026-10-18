"""Slow-consumer regression: text is lossless, semantic barriers stay ordered."""

import asyncio
from collections import defaultdict
from contextlib import aclosing

import pytest

from bisheng.linsight.domain.services.stream_event_buffer import coalesce_stream_events
from bisheng_langchain.linsight.event import ExecStep, NeedUserInput, TaskEnd


def thinking(text, namespace="main", call_id="reasoning"):
    return ExecStep(
        task_id="buffer-test",
        call_id=call_id,
        call_reason="",
        name="thinking",
        step_type="thinking",
        status="end",
        output=text,
        extra_info={"namespace": namespace},
    )


async def test_parallel_long_output_is_lossless_with_slow_storage():
    expected = defaultdict(str)

    async def source():
        for i in range(12000):
            ns = f"child-{i % 3}"
            text = f"{i},"
            expected[ns] += text
            yield thinking(text, ns)
        yield TaskEnd(task_id="buffer-test", name="done", status="success", answer="done", data={})

    actual = defaultdict(str)
    frames = 0
    async for event in coalesce_stream_events(source(), max_chars=2048, queue_size=2):
        if isinstance(event, ExecStep):
            actual[event.extra_info["namespace"]] += event.output
            frames += 1
        await asyncio.sleep(0.002)
    assert actual == expected
    assert frames < 150  # More than 80x fewer slow persistence calls than token writes.


async def test_tools_interrupts_and_terminal_events_are_ordering_barriers():
    tool = ExecStep(
        task_id="buffer-test", call_id="tool-1", call_reason="", name="write_file", status="end", output="receipt"
    )
    interrupt = NeedUserInput(task_id="buffer-test", call_reason="Choose one")
    terminal = TaskEnd(task_id="buffer-test", name="done", status="success", answer="done", data={})

    async def source():
        yield thinking("first")
        yield thinking(" second")
        yield tool
        yield thinking("after tool")
        yield interrupt
        yield thinking("tail", call_id="new-segment")
        yield terminal

    result = [event async for event in coalesce_stream_events(source(), interval=60)]
    assert [event.output if isinstance(event, ExecStep) else event for event in result] == [
        "first second",
        "receipt",
        "after tool",
        interrupt,
        "tail",
        terminal,
    ]
    assert result[1] is tool and result[3] is interrupt and result[-1] is terminal


async def test_timer_flushes_text_while_provider_is_idle_and_closes_reader():
    closed = asyncio.Event()

    async def source():
        try:
            yield thinking("visible without another token")
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with aclosing(coalesce_stream_events(source(), interval=0.01)) as events:
        event = await asyncio.wait_for(anext(events), 1)
        assert event.output == "visible without another token"
    assert closed.is_set()


async def test_source_failure_flushes_tail_then_propagates():
    async def source():
        yield thinking("partial evidence")
        raise RuntimeError("provider interrupted")

    async with aclosing(coalesce_stream_events(source(), interval=60)) as events:
        assert (await anext(events)).output == "partial evidence"
        with pytest.raises(RuntimeError, match="provider interrupted"):
            await anext(events)


async def test_lease_loss_in_reader_cancels_consumer_without_hanging():
    from bisheng.linsight.domain.services.recovery_context import RecoveryLeaseLost

    async def source():
        raise RecoveryLeaseLost("execution replaced")
        yield  # Keep this an async iterator.

    async with aclosing(coalesce_stream_events(source())) as events:
        with pytest.raises(RecoveryLeaseLost):
            await asyncio.wait_for(anext(events), 1)


async def test_consumer_failure_cancels_source_without_draining_pending_work():
    closed = asyncio.Event()

    async def source():
        try:
            while True:
                yield thinking("delta")
                await asyncio.sleep(0)
        finally:
            closed.set()

    with pytest.raises(RuntimeError, match="storage failed"):
        async with aclosing(coalesce_stream_events(source(), max_chars=10, queue_size=1)) as events:
            async for _event in events:
                raise RuntimeError("storage failed")
    assert closed.is_set()


async def test_semantic_event_queue_applies_bounded_backpressure():
    produced = 0

    async def source():
        nonlocal produced
        for _ in range(1000):
            produced += 1
            yield NeedUserInput(task_id="buffer-test", call_reason="barrier")

    async with aclosing(coalesce_stream_events(source(), queue_size=2)) as events:
        await anext(events)
        for _ in range(10):
            await asyncio.sleep(0)
        assert produced <= 4


async def test_distinct_segments_and_truncation_metadata_are_preserved():
    first = thinking("a", call_id="first")
    last = thinking("b", call_id="first")
    last.extra_info["truncated"] = True

    async def source():
        yield first
        yield last
        yield thinking("c", call_id="second")

    result = [event async for event in coalesce_stream_events(source(), interval=60)]
    assert [event.output for event in result] == ["ab", "c"]
    assert result[0].extra_info["truncated"] is True
    assert first.output == "a"  # Never mutate mapper-owned input objects.
