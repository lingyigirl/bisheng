"""Durable tool-call receipts; ambiguous writes are never replayed automatically."""

import base64
import hashlib
import json

from langchain.agents.middleware import AgentMiddleware
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_config

from bisheng.linsight.domain.services.recovery_context import RecoveryBlocked, RecoveryLeaseLost, execution_fence

_SAVE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return false end
if redis.call('EXISTS', KEYS[3]) == 0 then return false end
local added = redis.call('HSET', KEYS[2], ARGV[2], ARGV[3])
if added == 1 then redis.call('INCR', KEYS[3]) end
redis.call('EXPIRE', KEYS[2], 604800)
redis.call('EXPIRE', KEYS[3], 604800)
return 1
"""
# Only built-in workspace reads are approved here. User tools never inherit this
# policy merely by using a matching name. Arbitrary code and connectors are unsafe.
READ_ONLY = frozenset({"ls", "glob", "grep", "read_file"})
CONTROL = frozenset({"task", "ask_user", "write_todos"})


class RecoveryMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        fence = execution_fence.get()
        if fence:
            await fence.check()
        result = await handler(request)
        if fence:
            await fence.check()
        return result

    async def awrap_tool_call(self, request, handler):
        fence = execution_fence.get()
        if fence is None:
            return await handler(request)
        await fence.check()
        call = request.tool_call
        namespace = get_config().get("configurable", {}).get("checkpoint_ns", "")
        identity = hashlib.sha256(f"{namespace}:{call['id']}".encode()).hexdigest()
        digest = hashlib.sha256(
            json.dumps(
                {"name": call["name"], "args": call.get("args", {})}, sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        function = getattr(request.tool, "coroutine", None) or getattr(request.tool, "func", None)
        module = getattr(function, "__module__", "")
        is_builtin = not hasattr(request.tool, "tool_id") and module in {
            "deepagents.middleware.filesystem",
            "deepagents.middleware.subagents",
            "langchain.agents.middleware.todo",
            "bisheng.linsight.domain.services.agent_factory",
        }
        policy = (
            "read"
            if is_builtin and call["name"] in READ_ONLY
            else ("control" if is_builtin and call["name"] in CONTROL else "unsafe")
        )
        key = fence.prefix + ":receipts"
        raw = await fence.redis.hget(key, identity)
        if raw:
            previous = json.loads(raw)
            if previous["digest"] != digest:
                raise RecoveryBlocked("Tool call identity reused with different arguments")
            if previous["state"] == "done" and policy != "control":
                return JsonPlusSerializer().loads_typed((previous["type"], base64.b64decode(previous["value"])))
            if previous["state"] == "started" and policy == "unsafe":
                raise RecoveryBlocked("A tool call has an unknown external outcome")
        record = {
            "digest": digest,
            "policy": policy,
            "state": "started",
            "name": call["name"],
            "epoch": fence.epoch,
            "stateful": call["name"] == "bisheng_code_interpreter",
        }

        async def save():
            if not await fence.redis.eval(
                _SAVE,
                3,
                fence.lease_key,
                key,
                fence.prefix + ":receipt-count",
                fence.token,
                identity,
                json.dumps(record),
            ):
                raise RecoveryLeaseLost("Tool result writer lost its lease")

        await save()
        result = await handler(request)
        kind, value = JsonPlusSerializer().dumps_typed(result)
        record.update(state="done", type=kind, value=base64.b64encode(value).decode())
        await save()
        return result


async def validate_receipts(fence, *, minimum_epoch=0):
    count = await fence.redis.get(fence.prefix + ":receipt-count")
    receipts = await fence.redis.hgetall(fence.prefix + ":receipts")
    if count is None or int(count) != len(receipts):
        raise RecoveryBlocked("Tool recovery metadata is missing or incomplete")
    for raw in receipts.values():
        item = json.loads(raw)
        # A new explicit user turn starts a fresh graph input. Its earlier
        # completed turns do not require reconstructing old sandbox processes.
        if item.get("epoch", 0) < minimum_epoch:
            continue
        if item.get("stateful"):
            raise RecoveryBlocked("Sandbox process state cannot be reconstructed safely")
        if item["state"] == "started" and item["policy"] == "unsafe":
            raise RecoveryBlocked("A tool may have executed without a durable result; automatic replay blocked")
