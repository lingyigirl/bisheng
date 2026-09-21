"""F060 T-07: pin the framework behaviour the per-turn injection depends on.

`create_react_agent(prompt=<callable>)` is what makes spec AC-25 true for the
assistant path — the agent is reused across the turns of one session
(`common/chat/client.py:250-259` rebuilds it only when the session switches), so a
prompt baked in at construction time could not reflect a term added mid-session.
Handing it a callable means the prompt is resolved per model invocation instead.

That callable form is a **langgraph public signature, not this project's code**.
If a future upgrade drops it — accepting only `str` / `SystemMessage` / a Runnable
— every per-turn injection silently reverts to build-time, and the symptom is
"newly added terms do not take effect until a new session" rather than an error.
This file exists so that upgrade fails here, loudly, instead of in production.

The test builds a real agent with a fake model: the assertion is about how
langgraph DISPATCHES the prompt, so the model only has to exist, not to be real.
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent


def _fake_model() -> FakeListChatModel:
    return FakeListChatModel(responses=['ok'])


def test_a_callable_prompt_is_accepted():
    """The basic contract: `prompt` takes a callable at all."""
    agent = create_react_agent(
        _fake_model(), [], prompt=lambda state: [HumanMessage(content='x')]
    )

    assert agent is not None


async def test_the_callable_is_invoked_when_the_model_runs():
    """Not merely accepted — actually called. A regression that accepted a
    callable but ignored it would pass the previous test and break AC-25."""
    calls: list[object] = []

    def _prompt(state):
        calls.append(state)
        return [HumanMessage(content='injected')]

    agent = create_react_agent(_fake_model(), [], prompt=_prompt)

    await agent.ainvoke({'messages': [HumanMessage(content='hello')]})

    assert calls, 'the callable prompt was never invoked'


async def test_the_callable_can_read_the_turns_messages():
    """The reason a callable is needed: it must see the CONVERSATION, so it can
    pick out this turn's question and match terms against it."""
    seen: dict = {}

    def _prompt(state):
        seen['messages'] = state.get('messages', [])
        return [HumanMessage(content='p')]

    agent = create_react_agent(_fake_model(), [], prompt=_prompt)

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    texts = [getattr(m, 'content', '') for m in seen.get('messages', [])]
    assert any('不良率' in str(t) for t in texts), (
        'the prompt callable could not see the turn\'s question, so it cannot '
        'match terms against it (spec AC-20)'
    )


async def test_the_callable_is_invoked_on_every_model_call_not_once():
    """The other half of the cost the design records: within ONE turn the model
    may be called several times (a tool call, then the answer), and each call
    re-resolves the prompt. That is why `build_term_block` caches — the callable
    itself must not try to.

    Asserted as "at least once per invocation" rather than an exact count, since
    how many calls a turn makes is langgraph's business; what matters here is
    that it is not a one-shot resolved at construction.
    """
    calls: list[object] = []

    def _prompt(state):
        calls.append(state)
        return [HumanMessage(content='p')]

    agent = create_react_agent(_fake_model(), [], prompt=_prompt)

    await agent.ainvoke({'messages': [HumanMessage(content='q')]})
    first_turn_calls = len(calls)
    await agent.ainvoke({'messages': [HumanMessage(content='q2')]})

    assert first_turn_calls >= 1
    assert len(calls) > first_turn_calls, (
        'the prompt was resolved once and reused for the second turn -- the agent '
        'would never pick up a term added mid-session (spec AC-25)'
    )
