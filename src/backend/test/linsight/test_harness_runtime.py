"""Real graph execution with deterministic models; no provider or sandbox calls."""

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from bisheng.linsight.domain.services.subagent_context import DelegationMiddleware, SubagentIdentityMiddleware


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


async def test_parallel_delegations_keep_actual_invocation_identity():
    calls = [
        {"id": f"call-{i}", "name": "task", "args": {"subagent_type": "general-purpose", "description": f"Goal {i}"}}
        for i in range(2)
    ]
    root = ToolModel(responses=[AIMessage(content="", tool_calls=calls), AIMessage(content="Combined")])
    child = ToolModel(responses=[AIMessage(content="Evidence")])
    graph = create_deep_agent(
        model=root,
        middleware=[DelegationMiddleware({"general-purpose": {"id": "research", "name": "Research"}})],
        subagents=[
            {
                "name": "general-purpose",
                "description": "Research",
                "system_prompt": "Research",
                "model": child,
                "tools": [],
                "middleware": [SubagentIdentityMiddleware()],
            }
        ],
    )
    identities = []
    async for ns, mode, chunk in graph.astream(
        {"messages": [HumanMessage(content="Research two topics")]},
        stream_mode=["custom", "updates"],
        subgraphs=True,
    ):
        if mode == "custom" and chunk.get("type") == "subagent_identity":
            identities.append((ns, chunk))
    children = [(ns, value) for ns, value in identities if ns]
    assert len(children) >= 2
    assert {value["invocation_id"] for _, value in children} == {"call-0", "call-1"}
    assert len({ns for ns, _ in children}) == 2
    assert {value["agent_id"] for _, value in children} == {"research"}


async def test_rebuilt_graph_resumes_human_interrupt_before_delegation():
    """AC-14: reconstruct the graph around the same checkpoint, then delegate."""
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command, interrupt

    @tool
    def ask_user(question: str) -> str:
        """Ask for missing task information."""
        return interrupt(question)

    checkpoint = InMemorySaver()
    config = {"configurable": {"thread_id": "resume-task"}}

    def assemble(responses):
        return create_deep_agent(
            model=ToolModel(responses=responses),
            tools=[ask_user],
            checkpointer=checkpoint,
            middleware=[DelegationMiddleware({"general-purpose": {"id": "research", "name": "Research"}})],
            subagents=[
                {
                    "name": "general-purpose",
                    "description": "Research",
                    "system_prompt": "Research",
                    "model": ToolModel(responses=[AIMessage(content="Evidence")]),
                    "tools": [],
                    "middleware": [SubagentIdentityMiddleware()],
                }
            ],
        )

    first = assemble(
        [AIMessage(content="", tool_calls=[{"id": "ask", "name": "ask_user", "args": {"question": "Which year?"}}])]
    )
    parked = await first.ainvoke({"messages": [HumanMessage(content="Research")]}, config)
    assert parked["__interrupt__"]
    resumed = assemble(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "resumed-call",
                        "name": "task",
                        "args": {"description": "Research 2026", "subagent_type": "general-purpose"},
                    }
                ],
            ),
            AIMessage(content="Combined after clarification"),
        ]
    )
    result = await resumed.ainvoke(Command(resume="2026"), config)
    assert result["messages"][-1].content == "Combined after clarification"
    assert any(getattr(message, "tool_call_id", None) == "resumed-call" for message in result["messages"])
