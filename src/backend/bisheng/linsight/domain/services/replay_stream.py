"""A websocket subscriber owns its cursor, never the task or its messages."""

from starlette.websockets import WebSocketDisconnect

from bisheng.linsight.domain.services.event_replay import TERMINAL_EVENTS


async def stream_replay(websocket, replay, after, is_terminal=None):
    await websocket.accept()
    try:
        if not after or not await replay.valid_cursor(after):
            await websocket.send_json({"event_type": "replay_reset", "data": {"reason": "cursor_unavailable"}})
            await websocket.close(code=1000)
            return
        while True:
            if not await replay.valid_cursor(after):
                await websocket.send_json({"event_type": "replay_reset", "data": {"reason": "cursor_expired"}})
                await websocket.close(code=1000)
                return
            events = await replay.read_after(after)
            if not await replay.valid_cursor(after):
                await websocket.send_json({"event_type": "replay_reset", "data": {"reason": "cursor_expired"}})
                await websocket.close(code=1000)
                return
            for event in events:
                await websocket.send_json(event)
                after = event["event_id"]
                if event["event_type"] in TERMINAL_EVENTS:
                    await websocket.close(code=1000)
                    return
            if not events:
                if is_terminal and await is_terminal():
                    # Completion may have committed before a process died while
                    # publishing the final frame. Rehydrate authoritative DB state.
                    await websocket.send_json({"event_type": "replay_reset", "data": {"reason": "terminal_state"}})
                    await websocket.close(code=1000)
                    return
                await websocket.send_json({"event_type": "heartbeat", "data": {}})
    except WebSocketDisconnect:
        return
