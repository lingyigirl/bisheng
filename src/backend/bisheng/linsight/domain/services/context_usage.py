"""Live context-usage snapshot for one task run (2026-10-16).

The resilience middleware observes every successful model call together with the
provider's REAL ``usage_metadata`` (input/output tokens). This recorder keeps a
small per-run snapshot and persists it to Redis under the same task-scoped
namespace the replay stream uses, so:

- the client can poll it WHILE the task runs (live context display), and
- a completed turn stays queryable after a reload (7-day TTL, same as replay).

Honesty rules (no fake precision):

- token numbers are the provider's real usage: ``last_input_tokens`` is the
  CURRENT context size (input tokens of the newest main-graph call) and the
  cumulative fields sum every call of the run, main graph vs subagents kept
  apart;
- the composition breakdown (system prompt / conversation / tool schemas) is a
  CHARACTER-count estimate used to show PROPORTIONS only — the API labels it as
  such and it is never presented as exact tokens.

Recording is best-effort by contract: usage visibility must never break a task,
so every failure here is swallowed after a debug log.
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

_KEY = "linsight:tasks:{svid}:context_usage"
# Same retention as the replay stream: long enough for a reload, short enough
# that per-run keys never accumulate unbounded.
_TTL_SECONDS = 7 * 24 * 3600


def _content_text(content: Any) -> str:
    """Text view of a message content (str or content-block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _message_chars(message: Any) -> int:
    return len(_content_text(getattr(message, "content", "")))


def _tool_schema_chars(tool: Any) -> int:
    """Rough character cost of one tool definition (name + doc + JSON schema)."""
    try:
        if isinstance(tool, dict):
            return len(json.dumps(tool, ensure_ascii=False, default=str))
        name = getattr(tool, "name", "") or ""
        description = getattr(tool, "description", "") or ""
        schema_chars = 0
        schema = getattr(tool, "args_schema", None)
        if schema is not None and hasattr(schema, "model_json_schema"):
            schema_chars = len(json.dumps(schema.model_json_schema(), ensure_ascii=False, default=str))
        return len(name) + len(description) + schema_chars
    except Exception:  # a bad tool must not break accounting
        return 0


def compose_breakdown(request: Any) -> dict[str, int] | None:
    """Character-count composition of one model request (estimate, see module doc)."""
    if request is None:
        return None
    try:
        system_chars = _message_chars(getattr(request, "system_message", None))
        history_chars = 0
        for message in getattr(request, "messages", None) or []:
            role = getattr(message, "type", None) or getattr(message, "role", None)
            if role == "system":
                system_chars += _message_chars(message)
            else:
                history_chars += _message_chars(message)
        tool_chars = sum(_tool_schema_chars(tool) for tool in getattr(request, "tools", None) or [])
        return {
            "system_chars": system_chars,
            "history_chars": history_chars,
            "tool_schema_chars": tool_chars,
        }
    except Exception:  # breakdown is a nicety, never required
        return None


class ContextUsageRecorder:
    """Accumulate token usage for one run and persist the snapshot (best-effort)."""

    def __init__(self, svid: str) -> None:
        self.svid = svid
        self._redis = None
        self._snapshot: dict[str, Any] = {
            "main": {
                "calls": 0,
                "cum_input_tokens": 0,
                "cum_output_tokens": 0,
                "last_input_tokens": None,
                "last_output_tokens": None,
            },
            "sub": {"calls": 0, "cum_input_tokens": 0, "cum_output_tokens": 0},
            "breakdown": None,
            "updated_at": None,
        }

    @property
    def key(self) -> str:
        return _KEY.format(svid=self.svid)

    async def _get_redis(self):
        if self._redis is None:
            from bisheng.core.cache.redis_manager import get_redis_client

            self._redis = await get_redis_client()
        return self._redis

    async def record(self, *, is_subagent: bool, usage: dict[str, Any] | None, request: Any = None) -> None:
        """Fold one successful model call into the snapshot, then persist it."""
        try:
            usage = usage or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            bucket = self._snapshot["sub" if is_subagent else "main"]
            bucket["calls"] += 1
            bucket["cum_input_tokens"] += input_tokens
            bucket["cum_output_tokens"] += output_tokens
            if not is_subagent:
                # The newest main call's input IS the current context size.
                bucket["last_input_tokens"] = input_tokens
                bucket["last_output_tokens"] = output_tokens
                self._snapshot["breakdown"] = compose_breakdown(request)
            self._snapshot["updated_at"] = time.time()
            client = await self._get_redis()
            await client.aset(
                self.key,
                json.dumps(self._snapshot, ensure_ascii=False),
                expiration=_TTL_SECONDS,
            )
        except Exception as exc:  # visibility must not break tasks
            logger.debug(f"[linsight-context] usage record failed for {self.svid}: {exc}")
