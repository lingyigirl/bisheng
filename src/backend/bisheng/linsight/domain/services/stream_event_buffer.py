"""Bounded stream prefetch and lossless reasoning coalescing before persistence."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from bisheng_langchain.linsight.event import BaseEvent, ExecStep


async def coalesce_stream_events(
    source: AsyncIterator[BaseEvent],
    *,
    interval: float = 0.25,
    max_chars: int = 8192,
    queue_size: int = 16,
) -> AsyncIterator[BaseEvent]:
    """Keep slow storage off the token-read path without an unbounded queue.

    Each namespace/call keeps its own text. Non-thinking events are barriers:
    preceding text is queued first, and tool results/interrupts are never merged.
    A timer flushes short tails even if the provider stops producing tokens.
    """
    queue = asyncio.Queue(maxsize=queue_size)
    finished = object()

    async def produce():
        pending = {}
        chars = 0
        loop = asyncio.get_running_loop()
        last_flush = loop.time()
        next_event = None

        async def flush():
            nonlocal chars, last_flush
            for event in pending.values():
                await queue.put(event)
            pending.clear()
            chars = 0
            # Reset AFTER backpressure clears, otherwise a full queue turns
            # every subsequent token into another expired-interval flush.
            last_flush = loop.time()

        try:
            next_event = asyncio.create_task(anext(source))
            while True:
                timeout = max(0, interval - (loop.time() - last_flush)) if pending else None
                done, _ = await asyncio.wait({next_event}, timeout=timeout)
                if not done:
                    await flush()
                    continue
                completed = next_event
                next_event = None
                try:
                    event = completed.result()
                except StopAsyncIteration:
                    break
                if isinstance(event, ExecStep) and (
                    event.step_type == "thinking"
                    or (event.step_type == "assistant" and (event.extra_info or {}).get("text_mode") == "delta")
                ):
                    key = (event.task_id, event.call_id, (event.extra_info or {}).get("namespace"))
                    previous = pending.get(key)
                    if previous is None:
                        pending[key] = event.model_copy(deep=True)
                    else:
                        previous.output = (previous.output or "") + (event.output or "")
                        received_at = (previous.extra_info or {}).get("stream_received_at")
                        previous.extra_info = {
                            **(previous.extra_info or {}),
                            **(event.extra_info or {}),
                            "truncated": bool(
                                (previous.extra_info or {}).get("truncated")
                                or (event.extra_info or {}).get("truncated")
                            ),
                        }
                        if event.step_type == "assistant":
                            previous.extra_info["stream_received_at"] = received_at
                    chars += len(event.output or "")
                    if chars >= max_chars or loop.time() - last_flush >= interval:
                        await flush()
                else:
                    await flush()
                    await queue.put(event)
                next_event = asyncio.create_task(anext(source))
            await flush()
            await queue.put(finished)
        except Exception as exc:
            await flush()
            await queue.put(exc)
        finally:
            if next_event is not None:
                next_event.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await next_event
            await source.aclose()

    producer = asyncio.create_task(produce())
    receive = None
    try:
        while True:
            receive = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({receive, producer}, return_when=asyncio.FIRST_COMPLETED)
            if receive not in done and queue.empty():
                # Lease loss is a CancelledError, not an ordinary exception.
                # A cancelled producer must wake the consumer instead of
                # leaving it waiting forever for a sentinel that cannot arrive.
                await producer
                return
            event = await receive
            receive = None
            if event is finished:
                return
            if isinstance(event, Exception):
                raise event
            yield event
    finally:
        if receive is not None:
            receive.cancel()
            with suppress(asyncio.CancelledError):
                await receive
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
