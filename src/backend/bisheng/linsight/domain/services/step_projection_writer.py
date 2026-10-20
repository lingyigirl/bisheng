"""Keep SQL/cache snapshots off the durable live-event delivery path."""

import asyncio
from contextlib import asynccontextmanager


@asynccontextmanager
async def deferred_step_projections(manager, interval=2.0):
    manager.defer_step_projection = True
    stop = asyncio.Event()

    async def persist():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                await manager.flush_step_projections()

    writer = asyncio.create_task(persist())
    try:
        yield writer
    finally:
        stop.set()
        try:
            await writer
            await manager.flush_step_projections()
        finally:
            manager.defer_step_projection = False
