"""Unit tests for T2 background subagent delegation (2026-10-16).

``task(run_in_background=true)`` must return a job id immediately, the job
must settle into the run-scoped registry, ``job_output``/``job_list``/
``job_kill`` must manage it, the completion notice must be injected once, and
the run's end must cancel still-running jobs (owner-scoped lifecycle, same
guarantee dsh gives background children).
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from bisheng.linsight.domain.services.background_subagents import (
    BackgroundSubagentMiddleware,
    bind_run_owner,
)


class _TaskSchema(BaseModel):
    """Stand-in for deepagents' TaskToolSchema."""

    description: str = Field(description="task")
    subagent_type: str = Field(description="type")


def _task_tool():
    from langchain_core.tools import StructuredTool

    async def _run(description: str, subagent_type: str) -> str:
        return "done"

    return StructuredTool.from_function(
        name="task",
        coroutine=_run,
        description="fake task tool",
        infer_schema=False,
        args_schema=_TaskSchema,
    )


class _FakeRequest:
    """Minimal ToolCallRequest / ModelRequest stand-in with override()."""

    def __init__(self, tool_call=None, *, tools=None, messages=None, state=None):
        self.tool_call = tool_call or {}
        self.tools = tools or []
        self.messages = messages or []
        self.state = state or {"messages": []}

    def override(self, **kwargs):
        clone = _FakeRequest(self.tool_call, tools=list(self.tools), messages=list(self.messages), state=self.state)
        for key, value in kwargs.items():
            setattr(clone, key, value)
        return clone


def _bg_call(job_desc: str = "调研X") -> _FakeRequest:
    return _FakeRequest(
        {
            "name": "task",
            "args": {
                "description": job_desc,
                "subagent_type": "general-purpose",
                "run_in_background": True,
            },
            "id": "tc1",
        }
    )


async def _call_tool(mw: BackgroundSubagentMiddleware, name: str, args: dict) -> str:
    tool = next(t for t in mw.tools if t.name == name)
    return await tool.ainvoke(args)


async def test_background_start_returns_job_id_and_output_collects_result():
    mw = BackgroundSubagentMiddleware()

    async def handler(_request):
        await asyncio.sleep(0.02)
        return ToolMessage(
            content="子代理最终报告\n产物: scratch/a.xlsx",
            name="task",
            tool_call_id="tc1",
        )

    resp = await mw.awrap_tool_call(_bg_call(), handler)

    assert isinstance(resp, ToolMessage)
    assert "job_1" in resp.content  # id returned immediately
    await asyncio.sleep(0.1)  # let the job settle

    out = await _call_tool(mw, "job_output", {"job_id": "job_1"})
    assert "产物: scratch/a.xlsx" in out
    listing = await _call_tool(mw, "job_list", {})
    assert "job_1" in listing and "completed" in listing


async def test_foreground_call_strips_the_flag_before_the_real_tool():
    mw = BackgroundSubagentMiddleware()
    seen: dict = {}

    async def handler(request):
        seen["args"] = dict(request.tool_call["args"])
        return ToolMessage(content="ok", name="task", tool_call_id="tc1")

    req = _FakeRequest(
        {
            "name": "task",
            "args": {"description": "d", "subagent_type": "general-purpose", "run_in_background": False},
            "id": "tc1",
        }
    )
    resp = await mw.awrap_tool_call(req, handler)

    assert resp.content == "ok"
    assert "run_in_background" not in seen["args"]
    assert seen["args"]["description"] == "d"


async def test_concurrency_limit_rejects_extra_background_jobs():
    mw = BackgroundSubagentMiddleware(max_background_jobs=1)
    gate = asyncio.Event()

    async def handler(_request):
        await gate.wait()
        return ToolMessage(content="slow", name="task", tool_call_id="tc1")

    first = await mw.awrap_tool_call(_bg_call(), handler)
    second = await mw.awrap_tool_call(_bg_call(), handler)

    assert "job_1" in first.content
    assert second.status == "error"
    assert "上限" in second.content
    gate.set()
    await asyncio.sleep(0.05)


async def test_job_kill_cancels_a_running_job():
    mw = BackgroundSubagentMiddleware()

    async def handler(_request):
        await asyncio.sleep(30)
        return ToolMessage(content="never", name="task", tool_call_id="tc1")

    await mw.awrap_tool_call(_bg_call(), handler)
    killed = await _call_tool(mw, "job_kill", {"job_id": "job_1"})
    assert "已终止" in killed
    await asyncio.sleep(0.05)

    out = await _call_tool(mw, "job_output", {"job_id": "job_1"})
    assert "终止" in out
    assert "never" not in out


async def test_settled_job_is_announced_once_before_the_next_model_call():
    mw = BackgroundSubagentMiddleware()

    async def handler(_request):
        await asyncio.sleep(0.02)
        return ToolMessage(content="report body", name="task", tool_call_id="tc1")

    await mw.awrap_tool_call(_bg_call(), handler)
    await asyncio.sleep(0.1)

    captured: dict = {}

    async def model_handler(request):
        captured["messages"] = list(request.messages)
        return "model"

    await mw.awrap_model_call(_FakeRequest(tools=[_task_tool()]), model_handler)
    texts = [str(getattr(m, "content", "")) for m in captured["messages"]]
    assert any("job_1" in t and "job_output" in t for t in texts)

    # Second call: same job must NOT be announced again.
    captured.clear()
    await mw.awrap_model_call(_FakeRequest(tools=[_task_tool()]), model_handler)
    second_texts = [str(getattr(m, "content", "")) for m in captured["messages"]]
    assert all("job_1" not in t for t in second_texts)


async def test_task_schema_gains_the_background_flag():
    mw = BackgroundSubagentMiddleware()
    captured: dict = {}

    async def model_handler(request):
        captured["tool"] = request.tools[0]
        return "model"

    await mw.awrap_model_call(_FakeRequest(tools=[_task_tool()]), model_handler)

    fields = captured["tool"].args_schema.model_fields
    assert "run_in_background" in fields
    assert "description" in fields  # base fields survive the extension
    assert "subagent_type" in fields


async def test_run_end_cancels_still_running_jobs():
    mw = BackgroundSubagentMiddleware()

    async def handler(_request):
        await asyncio.sleep(30)
        return ToolMessage(content="never", name="task", tool_call_id="tc1")

    await mw.awrap_tool_call(_bg_call(), handler)
    await mw.aafter_agent({}, None)
    await asyncio.sleep(0.05)

    out = await _call_tool(mw, "job_output", {"job_id": "job_1"})
    assert "终止" in out


async def test_watchdog_waits_on_the_run_task_not_on_the_spawning_hook_task():
    """Regression (live 2026-10-16): the watchdog must observe the RUN task.

    The background job is started from a short-lived per-node hook task (as in
    a real graph). The job must survive that hook task ending and only die when
    the bound run task (task_exec's astream driver) ends.
    """
    mw = BackgroundSubagentMiddleware()

    async def handler(_request):
        await asyncio.sleep(30)
        return ToolMessage(content="never", name="task", tool_call_id="tc1")

    observed: dict = {}

    async def run_owner():
        bind_run_owner()

        async def hook():
            await mw.awrap_tool_call(_bg_call(), handler)

        await asyncio.create_task(hook())  # per-node task ends right here
        await asyncio.sleep(0.1)  # the run keeps going
        observed["mid_run"] = await _call_tool(mw, "job_output", {"job_id": "job_1"})

    run_task = asyncio.create_task(run_owner())
    await run_task  # the run task ends now
    await asyncio.sleep(0.05)

    out = await _call_tool(mw, "job_output", {"job_id": "job_1"})
    # Survived the hook task's end (this is what the live bug broke)...
    assert "仍在运行" in observed["mid_run"]
    # ...and was cancelled by the watchdog once the RUN task ended.
    assert "终止" in out
