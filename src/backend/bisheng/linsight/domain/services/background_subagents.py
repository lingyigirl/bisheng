"""Background (async) subagent delegation for Linsight task mode (T2, 2026-10-16).

Design — aligned with dsh's background-subagent tasks:

- ``task(..., run_in_background=true)`` returns a **job id immediately**; the
  main agent keeps working instead of blocking on the child. Foreground
  behaviour is untouched (the flag defaults to false and is stripped before the
  real ``task`` tool runs).
- Collection is explicit, via the same job-tool vocabulary dsh uses:
  ``job_output`` / ``job_list`` / ``job_kill``. The prompt teaches "keep the id,
  keep working, collect before finishing" — no busy-wait polling.
- A settled job is announced **once** before the next model call, so a model
  that forgot to collect still learns the result is ready.
- Jobs are OWNED by the run: one watchdog awaits the asyncio task that DRIVES
  the graph (bound by the task_exec drivers via ``bind_run_owner`` — middleware
  hooks themselves may execute in short-lived per-node tasks, so the owner must
  never be captured from inside a hook) and cancels every still-running job when
  the run ends (user stop, crash, normal completion). ``aafter_agent`` is the
  second net for a clean finish. A background child can therefore never outlive
  its run.
- Artifact awareness stays on the single final-text channel (T1): this
  middleware neither diffs nor injects workspace state. A background child's
  final report — including its ``产物:`` line — is exactly what ``job_output``
  returns.

The launch acknowledgement never closes the delegation card. A custom stream
event settles that same invocation with its real result, status and duration;
the executor persists it through the existing replay channel. job_output uses
bounded asynchronous waiting instead of model-driven busy polling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.types import Command
from loguru import logger
from pydantic import BaseModel, Field, create_model

TASK_TOOL = "task"
BACKGROUND_FLAG = "run_in_background"

# Refuse a new background job past this many still-running ones: a weak model
# can otherwise fan out unbounded parallel children each burning its own model
# calls (dsh bounds capacity at the runtime; this is the per-run analogue).
DEFAULT_MAX_BACKGROUND_JOBS = 3

_FLAG_DESCRIPTION = (
    "true = 放到后台执行：立即返回 job id，你继续做其他工作，完成前用 job_output 取回结果。"
    "默认 false = 同步等待该子代理返回。"
)

JobStatus = Literal["running", "completed", "failed", "killed"]

# The asyncio task that owns the current graph run, bound by the task_exec
# drivers (fresh / resume / continue) immediately before they await
# ``agent.astream``. The watchdog below waits on THIS task: middleware hooks can
# run in short-lived per-node tasks, so ``asyncio.current_task()`` inside a hook
# is not a run-lifetime handle (capturing it there made the watchdog fire the
# moment the spawning tool call returned — observed live 2026-10-16, job_1
# killed 2 ms after start).
_run_owner_task: ContextVar[asyncio.Task[Any] | None] = ContextVar("linsight_bg_run_owner", default=None)


def bind_run_owner() -> None:
    """Bind the currently executing task as owner of this run's background jobs.

    Must be called by the code that drives the graph (``task_exec``), inside the
    very task that awaits ``agent.astream`` — NOT from inside a middleware hook,
    whose task is scoped to a single node/tool call. The binding is stored in a
    ContextVar so the middleware (and its watchdog) see it through the graph's
    task tree, exactly like ``recovery_context.execution_fence``.
    """
    _run_owner_task.set(asyncio.current_task())


@dataclass
class _Job:
    """One background subagent run owned by this middleware instance (one run)."""

    job_id: str
    description: str
    created_at: float
    status: JobStatus = "running"
    result_text: str = ""
    error: str = ""
    task: asyncio.Task[Any] | None = None
    notified: bool = False
    ended_at: float | None = None
    call_id: str = ""
    writer: Callable[[Any], None] | None = None

    def age_seconds(self) -> int:
        return int((self.ended_at or time.monotonic()) - self.created_at)


def _flatten_content(content: Any) -> str:
    """Text view of a ToolMessage/Command payload (mirrors the salvage rule)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") in ("image_url", "image"):
                    parts.append("[图片]")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


def _result_text(result: Any) -> str:
    """Extract the child's final text from the task tool's return value.

    deepagents' ``task`` returns a ``Command`` whose ``messages`` update carries
    the subagent's final ``ToolMessage``; defensive branches cover a plain
    ``ToolMessage``/string result in case a future deepagents version changes
    the shape.
    """
    if isinstance(result, Command):
        messages = (result.update or {}).get("messages") or []
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                return _flatten_content(message.content)
        return ""
    if isinstance(result, ToolMessage):
        return _flatten_content(result.content)
    if isinstance(result, str):
        return result
    return _flatten_content(getattr(result, "content", result))


class BackgroundSubagentMiddleware(AgentMiddleware):
    """Run ``task`` delegations in the background and manage their lifecycle.

    One instance per graph (= per run): the job registry is run-scoped, exactly
    like dsh ties background children to their owning agent's cleanup.
    """

    def __init__(
        self, *, max_background_jobs: int = DEFAULT_MAX_BACKGROUND_JOBS, allowed_subagents: set[str] | None = None
    ) -> None:
        super().__init__()
        self._allowed_subagents = frozenset(allowed_subagents) if allowed_subagents is not None else None
        self._max_jobs = max(1, max_background_jobs)
        self._jobs: dict[str, _Job] = {}
        self._seq = 0
        self._watchdog: asyncio.Task[Any] | None = None
        self._extended_source_id: int | None = None
        self._extended_task_tool: BaseTool | None = None
        self.tools = self._build_job_tools()

    # ------------------------------------------------------------------ jobs

    def _running_jobs(self) -> list[_Job]:
        return [job for job in self._jobs.values() if job.status == "running"]

    def _start_job(self, description: str, call: Any, call_id: str = "") -> _Job:
        self._seq += 1
        try:
            writer = get_stream_writer()
        except RuntimeError:
            # Unit harnesses may execute middleware without a graph stream.
            writer = None
        job = _Job(
            job_id=f"job_{self._seq}",
            description=description[:80] or "(无描述)",
            created_at=time.monotonic(),
            call_id=call_id,
            writer=writer,
        )
        task = asyncio.create_task(call)
        job.task = task
        task.add_done_callback(lambda t: self._settle(job, t))
        self._jobs[job.job_id] = job
        self._ensure_watchdog()
        logger.info(
            "[linsight-bg] started {} ({}): {}",
            job.job_id,
            len(self._running_jobs()),
            job.description,
        )
        return job

    def _settle(self, job: _Job, task: asyncio.Task[Any]) -> None:
        if job.status != "running":
            return
        job.ended_at = time.monotonic()
        if task.cancelled():
            job.status = "killed"
            job.result_text = "（后台任务已被终止）"
        else:
            exc = task.exception()
            if exc is not None:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.result_text = f"后台子代理执行失败：{job.error}"
            else:
                result = task.result()
                messages = (result.update or {}).get("messages", []) if isinstance(result, Command) else [result]
                failed = any(isinstance(message, ToolMessage) and message.status == "error" for message in messages)
                job.status = "failed" if failed else "completed"
                job.result_text = _result_text(result)
                if failed:
                    job.error = job.result_text
        if job.writer is not None:
            job.writer(
                {
                    "type": "background_subagent_end",
                    "call_id": job.call_id,
                    "job_id": job.job_id,
                    "status": job.status,
                    "output": job.result_text,
                    "elapsed_seconds": job.age_seconds(),
                }
            )
        logger.info("[linsight-bg] {} settled: {}", job.job_id, job.status)

    def _ensure_watchdog(self) -> None:
        """Cancel every still-running job when the owning run task ends."""
        if self._watchdog is not None and not self._watchdog.done():
            return
        owner = _run_owner_task.get()
        if owner is None or owner.done():
            # No run owner bound (middleware driven outside task_exec, e.g. a
            # bare unit harness): keep aafter_agent as the only cleanup net
            # instead of guessing a short-lived task and killing jobs instantly.
            return

        async def _watch() -> None:
            try:
                await asyncio.wait({owner})
            except Exception:  # pragma: no cover - defensive
                pass
            self._cancel_running_jobs()

        self._watchdog = asyncio.create_task(_watch())

    def _cancel_running_jobs(self) -> None:
        for job in self._running_jobs():
            if job.task is not None and not job.task.done():
                job.task.cancel()
                logger.info("[linsight-bg] cancelled {} (run ended)", job.job_id)

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        self._cancel_running_jobs()
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        return None

    # ------------------------------------------------------- model call hooks

    def _with_extended_task_tool(self, request: Any) -> Any:
        """Present ``task`` with the extra ``run_in_background`` flag.

        Only the SCHEMA the model sees changes here; execution is intercepted
        in ``awrap_tool_call`` (which strips the flag before the real tool runs).
        """
        tools = list(request.tools or [])
        replacement: BaseTool | None = None
        changed = False
        for i, candidate in enumerate(tools):
            if getattr(candidate, "name", None) != TASK_TOOL:
                continue
            if id(candidate) == self._extended_source_id:
                replacement = self._extended_task_tool
            else:
                replacement = self._extend_task_tool(candidate)
                self._extended_source_id = id(candidate)
                self._extended_task_tool = replacement
            if replacement is not None and replacement is not candidate:
                tools[i] = replacement
                changed = True
            break
        return request.override(tools=tools) if changed else request

    @staticmethod
    def _extend_task_tool(candidate: BaseTool) -> BaseTool | None:
        schema = getattr(candidate, "args_schema", None)
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            logger.warning("[linsight-bg] task tool has no pydantic schema; background flag unavailable")
            return None
        if BACKGROUND_FLAG in schema.model_fields:
            return candidate
        try:
            extended_schema = create_model(
                "TaskToolSchemaWithBackground",
                __base__=schema,
                **{BACKGROUND_FLAG: (bool, Field(default=False, description=_FLAG_DESCRIPTION))},
            )
            return candidate.model_copy(update={"args_schema": extended_schema})
        except Exception:
            logger.exception("[linsight-bg] failed to extend the task schema")
            return None

    def _with_completion_notices(self, request: Any) -> Any:
        """Announce settled-but-unnotified jobs once, before the next model call."""
        notices: list[str] = []
        for job in self._jobs.values():
            if job.status == "running" or job.notified:
                continue
            job.notified = True
            if job.status == "completed":
                notices.append(
                    f"[后台任务通知] {job.job_id} 已完成（{job.description}）。"
                    f"用 job_output('{job.job_id}') 取回它的最终报告；若已不再需要，可忽略本通知。"
                )
            elif job.status == "failed":
                notices.append(
                    f"[后台任务通知] {job.job_id} 执行失败（{job.description}）：{job.error[:200]}。"
                    f"可重新委派或改用其他方式完成该部分。"
                )
            else:
                notices.append(f"[后台任务通知] {job.job_id} 已被终止（{job.description}）。")
        if not notices:
            return request
        return request.override(messages=[*request.messages, HumanMessage(content="\n".join(notices))])

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        request = self._with_extended_task_tool(request)
        request = self._with_completion_notices(request)
        return await handler(request)

    # -------------------------------------------------------- tool call hooks

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = request.tool_call or {}
        if tool_call.get("name") != TASK_TOOL:
            return await handler(request)
        args = dict(tool_call.get("args") or {})
        wants_background = bool(args.pop(BACKGROUND_FLAG, False))
        # Deepagents returns unknown names as a successful text result. Reject
        # against the graph's actual registry before starting any child or job.
        if self._allowed_subagents is not None and args.get("subagent_type") not in self._allowed_subagents:
            return ToolMessage(
                content=f"Unknown subagent_type. Use one of: {', '.join(sorted(self._allowed_subagents))}",
                name=TASK_TOOL,
                tool_call_id=tool_call.get("id"),
                status="error",
            )
        if not wants_background:
            # Strip the (false / absent) flag so the real task tool never sees it.
            if BACKGROUND_FLAG in (tool_call.get("args") or {}):
                request = request.override(tool_call={**tool_call, "args": args})
            return await handler(request)

        tool_call_id = tool_call.get("id")
        if len(self._running_jobs()) >= self._max_jobs:
            running = ", ".join(j.job_id for j in self._running_jobs())
            return ToolMessage(
                content=(
                    f"无法再启动后台子代理：当前已有 {len(self._running_jobs())} 个在运行（{running}，"
                    f"上限 {self._max_jobs}）。先用 job_output 收集或 job_kill 终止后再试。"
                ),
                name=TASK_TOOL,
                tool_call_id=tool_call_id,
                status="error",
            )

        description = str(args.get("description") or "")
        cleaned = request.override(tool_call={**tool_call, "args": args})
        job = self._start_job(description, handler(cleaned), tool_call_id)
        return ToolMessage(
            content=(
                f"⏱ 已放入后台：{job.job_id}（{job.description}）。\n"
                f"- 继续做其他不冲突的工作，不要空等；\n"
                f"- 完成前用 job_output('{job.job_id}') 取回它的最终报告；\n"
                f"- 不再需要它时用 job_kill('{job.job_id}') 终止。"
            ),
            name=TASK_TOOL,
            tool_call_id=tool_call_id,
            status="success",
            additional_kwargs={"background_job_id": job.job_id},
        )

    # ------------------------------------------------------------- job tools

    def _build_job_tools(self) -> list[BaseTool]:
        middleware = self

        @tool(
            "job_list",
            description="列出本任务在后台运行的子代理任务（job id / 状态 / 描述 / 已运行时长）。收尾前用它确认没有遗漏未收集的后台任务。",
        )
        async def job_list() -> str:
            if not middleware._jobs:
                return "当前没有后台任务。"
            lines = []
            for job in middleware._jobs.values():
                lines.append(f"{job.job_id}  [{job.status}]  {job.age_seconds()}s  {job.description}")
            return "\n".join(lines)

        @tool(
            "job_output",
            description="取回后台子代理最终报告。默认异步等待最多 60 秒，不消耗模型轮询；wait_seconds 可设 0~120，0 为只查询。没有其他工作时直接等待，不要调用沙箱 sleep 或密集轮询。等待超时不会终止子任务。",
        )
        async def job_output(job_id: str, wait_seconds: int = 60) -> str:
            job = middleware._jobs.get(job_id)
            if job is None:
                known = ", ".join(middleware._jobs) or "（无）"
                return f"没有后台任务 {job_id}。当前后台任务：{known}"
            if job.status == "running" and job.task is not None:
                # asyncio.wait does not cancel the child on timeout or when the
                # collector is cancelled; run ownership remains the cleanup net.
                done, _ = await asyncio.wait({job.task}, timeout=max(0, min(wait_seconds, 120)))
                if done:
                    middleware._settle(job, job.task)
            if job.status == "running":
                return f"{job.job_id} 仍在运行（已 {job.age_seconds()}s）：{job.description}"
            if job.status == "completed":
                return f"【{job.job_id} 结果】\n{job.result_text or '(空结果)'}"
            if job.status == "failed":
                return f"{job.job_id} 执行失败：{job.error}"
            return f"{job.job_id} 已被终止（不再需要其结果时可忽略）。"

        @tool(
            "job_kill",
            description="终止一个还在运行的后台子代理任务（不再需要它时使用；已结束的任务不受影响）。",
        )
        async def job_kill(job_id: str) -> str:
            job = middleware._jobs.get(job_id)
            if job is None:
                known = ", ".join(middleware._jobs) or "（无）"
                return f"没有后台任务 {job_id}。当前后台任务：{known}"
            if job.status != "running":
                return f"{job.job_id} 已经是 {job.status}，无需终止。"
            if job.task is not None and not job.task.done():
                job.task.cancel()
            return f"已终止 {job.job_id}。"

        return [job_list, job_output, job_kill]
