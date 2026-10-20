"""Non-destructive task event replay; reading events never executes the graph."""

import json
import re
from uuid import uuid4

_APPEND = """
if ARGV[5] ~= '' and redis.call('GET', KEYS[3]) ~= ARGV[5] then return false end
local prior = redis.call('HGET', KEYS[2], ARGV[1])
if prior and #redis.call('XRANGE', KEYS[1], prior, prior) > 0 then return prior end
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[4]) then
    local oldest = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', 1)
    if #oldest > 0 then
        local fields = oldest[1][2]
        for i = 1, #fields, 2 do
            if fields[i] == 'producer_id' then redis.call('HDEL', KEYS[2], fields[i+1]) end
        end
    end
end
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '=', ARGV[4], '*', 'payload', ARGV[2], 'producer_id', ARGV[1])
redis.call('HSET', KEYS[2], ARGV[1], id)
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return id
"""
_SNAPSHOT = """
local first = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', 1)
local last = redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)
if #first == 0 then return {} end
return {first[1][1], last[1][1]}
"""
TERMINAL_EVENTS = {"final_result", "error_message", "task_terminated"}


def decode(value):
    return value.decode() if isinstance(value, bytes) else value


def parse_rows(rows):
    events = []
    for event_id, fields in rows:
        if isinstance(fields, dict):
            payload = fields.get(b"payload", fields.get("payload"))
        else:
            payload = fields[1]
        event = json.loads(decode(payload))
        event["event_id"] = decode(event_id)
        events.append(event)
    return events


class EventReplayService:
    TTL = 7 * 24 * 3600
    MAX_EVENTS = 100000
    SNAPSHOT_BATCH = 512

    def __init__(self, redis, tenant_id: int, task_id: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", task_id):
            raise ValueError("Invalid task identifier")
        self.redis = redis
        # Hash tags keep both keys in the same Redis Cluster slot.
        prefix = f"linsight:replay:{{{tenant_id}:{task_id}}}"
        self.key = prefix + ":events"
        self.dedup_key = prefix + ":dedup"

    async def append(self, event: dict, producer_id: str | None = None):
        from bisheng.linsight.domain.services.recovery_context import RecoveryLeaseLost, execution_fence

        fence = execution_fence.get()
        if fence:
            event = {**event, "execution_epoch": fence.epoch}
        result = await self.redis.eval(
            _APPEND,
            3,
            self.key,
            self.dedup_key,
            fence.lease_key if fence else self.key,
            producer_id or uuid4().hex,
            json.dumps(event, ensure_ascii=False),
            self.TTL,
            self.MAX_EVENTS,
            fence.token if fence else "",
        )
        if result is None or result is False:
            raise RecoveryLeaseLost("Event writer lost its execution lease")
        return decode(result)

    async def snapshot(self, *, compact=False):
        from bisheng.linsight.domain.services.replay_compaction import ReplayCompactor

        bounds = await self.redis.eval(_SNAPSHOT, 1, self.key)
        if not bounds:
            await self.append({"event_type": "replay_gap", "data": {}}, "gap")
            bounds = await self.redis.eval(_SNAPSHOT, 1, self.key)
        if not bounds:
            return {
                "snapshot": {"events": []},
                "last_event_id": "0-0",
                "complete_history": False,
                "terminal": False,
                "compacted": compact,
            }
        first, last = map(decode, bounds)
        events = []
        projection = ReplayCompactor() if compact else None
        cursor = "-"
        complete = True
        while True:
            rows = await self.redis.xrange(self.key, min=cursor, max=last, count=self.SNAPSHOT_BATCH)
            if not rows:
                complete = False
                break
            page = parse_rows(rows)
            if cursor == "-" and page[0]["event_id"] != first:
                complete = False
            for event in page:
                if projection is not None:
                    projection.append(event)
                else:
                    events.append(event)
            tail = page[-1]["event_id"]
            if tail == last:
                break
            cursor = f"({tail}"
        # Appends cannot move the captured upper bound. Trimming may invalidate
        # it or the lower bound; never advertise such a projection as complete.
        complete = complete and await self.valid_cursor(first) and await self.valid_cursor(last)
        if projection is not None:
            events = projection.finish()
        return {
            "snapshot": {"events": events},
            "last_event_id": last,
            "complete_history": bool(complete and events and events[0]["event_type"] == "replay_init"),
            "terminal": bool(events and events[-1]["event_type"] in TERMINAL_EVENTS),
            "compacted": compact,
        }

    async def valid_cursor(self, cursor: str):
        if not re.fullmatch(r"\d+-\d+", cursor):
            return False
        rows = await self.redis.xrange(self.key, min=cursor, max=cursor, count=1)
        return bool(rows)

    async def read_after(self, cursor: str):
        streams = await self.redis.xread({self.key: cursor}, count=100, block=5000)
        return parse_rows(streams[0][1]) if streams else []
