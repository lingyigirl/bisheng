"""F061 T-42 / R4: does the legacy `sql_agent` tool block the event loop?

**Answer: no. R4's premise does not hold, measured three ways below.**

R4 (design decision 12 and the release-contract note) says the legacy
`sql_agent` blocks the loop, citing
`bisheng_langchain/gpts/tools/sql_agent/tool.py:75` — `SqlAgentAPIWrapper.arun`
returning `self.run(query)`, where `run` makes a synchronous LangGraph call.

That citation is real, and it is also a dead end: it describes the WRAPPER's
`arun`, and nothing calls it. `SqlAgentTool._run` calls
`self.api_wrapper.run(...)` directly (line 96), and `SqlAgentTool` defines no
`_arun` of its own — so the method an `ainvoke` reaches is langchain's inherited
`BaseTool._arun`, which offloads `_run` via `run_in_executor`.

There is a second, independent offload on the workflow path: `AgentNode.run`
(`agent.py:438`) is the node's only entry, and LangGraph wraps a sync callable as
`RunnableCallable(thing, partial(run_in_executor, None, thing))`. So even though
`AgentNode` never defines `_arun`, an `astream` does not call its `_run` on the
loop either.

Both are langchain/langgraph internals, which is exactly why this is pinned by
measurement rather than by reading: a version bump can change either, and the
failure mode is silent (latency under load, not an error). Each test below fails
if its offload disappears — verified by adding a blocking `_arun` to
`SqlAgentTool`, which turns all three red.

The "fix" R4 asked for would therefore have been a no-op at best. What follows
from it instead: the legacy `sql_agent` path needs NO change for AC-21, and any
future work here should start by re-running this file.
"""

import asyncio
import threading
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from bisheng_langchain.gpts.tools.sql_agent.tool import SqlAgentTool


def test_the_tool_does_not_define_its_own_async_path():
    """Pins the fact the analysis turns on. If someone later adds an `_arun`
    that delegates to the sync `run`, this test still passes but the one below
    is the one that would catch the regression."""
    assert '_arun' not in vars(SqlAgentTool), (
        'SqlAgentTool now defines _arun — re-check whether it still offloads to a '
        'thread, because the blocking analysis below assumed the inherited one'
    )


async def test_a_slow_sql_agent_call_does_not_starve_the_event_loop(monkeypatch):
    """AC-21's actual requirement: other conversations keep running.

    The tool is made slow on purpose and a heartbeat coroutine runs alongside.
    If the call blocked the loop, the heartbeat could not tick during it.
    """
    heartbeat_ticks = 0
    stop = threading.Event()

    async def heartbeat():
        nonlocal heartbeat_ticks
        while not stop.is_set():
            heartbeat_ticks += 1
            await asyncio.sleep(0.01)

    def _slow_run(self, query, run_manager=None):
        # Stands in for the synchronous LangGraph call R4 is about.
        time.sleep(0.3)
        return 'rows'

    monkeypatch.setattr(SqlAgentTool, '_run', _slow_run)

    tool = SqlAgentTool.construct(api_wrapper=None)
    tool.__dict__['api_wrapper'] = None

    beat = asyncio.create_task(heartbeat())
    try:
        result = await tool.ainvoke({'query': 'q'})
        # The `finally: db.dispose()` in `_run` is what the real body does; the
        # stubbed `_run` above skips it, which is fine for this measurement.
    finally:
        stop.set()
        await beat

    assert result == 'rows'
    # 0.3s of work at 10ms per tick would be ~30 ticks if the loop is free, and
    # 0 (or 1, at the boundaries) if it is blocked. The threshold is deliberately
    # far from both.
    assert heartbeat_ticks >= 5, (
        f'the event loop only ticked {heartbeat_ticks} times during a 0.3s tool '
        'call -- the call is blocking the loop, so R4 is real'
    )


async def test_the_inherited_async_path_offloads_to_a_thread(monkeypatch):
    """The mechanism behind the result above, asserted directly so a langchain
    upgrade that changes it fails here rather than in production."""
    seen_thread: list[int] = []

    def _record_thread(self, query, run_manager=None):
        seen_thread.append(threading.get_ident())
        return 'ok'

    monkeypatch.setattr(SqlAgentTool, '_run', _record_thread)

    tool = SqlAgentTool.construct(api_wrapper=None)
    tool.__dict__['api_wrapper'] = None

    await tool.ainvoke({'query': 'q'})

    assert seen_thread, 'the tool body never ran'
    assert seen_thread[0] != threading.get_ident(), (
        'the tool body ran on the event loop thread -- the inherited _arun no '
        'longer offloads (langchain changed?), which makes R4 real'
    )


async def test_langgraph_offloads_a_sync_node_on_the_async_path():
    """The second offload, and the one R4's scenario actually rides on.

    `AgentNode` defines only `_run` (`agent.py:438`), and the workflow engine's
    async entry streams the graph (`graph_engine.py:326`). If LangGraph called a
    sync node on the loop, a `sql_agent` node would block every other workflow in
    the process — the outcome AC-21 forbids.

    Built with a minimal graph rather than the real AgentNode because the claim
    is about the FRAMEWORK's dispatch, and constructing a full agent node would
    drag in an LLM to test something that has nothing to do with one.
    """
    seen_thread: list[int] = []
    heartbeat_ticks = 0
    stop = threading.Event()

    def _sync_node(state):
        seen_thread.append(threading.get_ident())
        time.sleep(0.3)
        return {'n': state['n'] + 1}

    async def heartbeat():
        nonlocal heartbeat_ticks
        while not stop.is_set():
            heartbeat_ticks += 1
            await asyncio.sleep(0.01)

    class _State(TypedDict):
        n: int

    graph = StateGraph(_State)
    graph.add_node('sync', _sync_node)
    graph.add_edge(START, 'sync')
    graph.add_edge('sync', END)
    app = graph.compile()

    beat = asyncio.create_task(heartbeat())
    try:
        await app.ainvoke({'n': 0})
    finally:
        stop.set()
        await beat

    assert seen_thread and seen_thread[0] != threading.get_ident(), (
        'the sync node ran on the event loop thread — langgraph no longer '
        'offloads sync nodes, so a sql_agent workflow node would block the loop '
        'and R4 becomes real (the fix would be to give AgentNode an _arun)'
    )
    assert heartbeat_ticks >= 5, (
        f'the loop only ticked {heartbeat_ticks} times during the node'
    )
