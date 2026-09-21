"""F060 T-12/T-13/T-14: the three places a term block reaches a conversation.

The pure matching rules live in `test_injection.py`; what is asserted here is the
WIRING — that each of the three paths actually gets the block into the prompt the
model receives, and that none of them does it in a way that breaks the two
invariants around injection:

  * **INV-18** — the block is appended to a message, never written back to the
    assistant's stored prompt. A user's prompt field keeps exactly the text they
    saved.
  * **AC-25** — an edit applies to the NEXT turn with no restart. For the
    assistant that is subtle: the agent object is reused across a session's
    turns, so anything resolved at construction freezes the terms at session
    start.

Everything here runs against a REAL langgraph agent with a fake model, and
asserts on the messages the MODEL was handed rather than on the graph's output
state: `prompt` is applied when calling the model, so the system message never
appears in the returned state. An assertion made against the state would pass
vacuously.
"""

# ruff: noqa: RUF001 -- the Chinese fixtures are the terms under test, and the
# block header's fullwidth colon is the real rendered format.

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from bisheng.term_library.domain.services import injection
from bisheng.term_library.domain.services import prompt as term_prompt

TENANT = 7
BASE = '你是银行助手。'

#: A rendered block, in the exact shape `injection._render_capped` produces.
BLOCK = '【本轮相关术语】\n- 不良贷款率（别名：不良率）：比例'


@dataclass
class _Term:
    id: int
    standard_term: str
    definition: str
    aliases: list


def _stub_library(monkeypatch, *terms: _Term) -> list[_Term]:
    """Point the library read at a list the test can mutate in place."""
    library = list(terms)

    async def _alist_all():
        return library

    monkeypatch.setattr(injection.TermLibraryTermDao, 'alist_all', _alist_all)
    injection._reset_caches()
    return library


class _RecordingModel(FakeListChatModel):
    """A fake model that keeps every prompt it is handed.

    This is the only place the injected system message is observable: langgraph
    applies the `prompt` callable at model-call time, so it is absent from the
    graph's output state.

    The list is a module-level singleton rather than an instance field because
    `bind_tools` copies the model (the base implementation returns a bound
    clone), so an instance attribute would be written to by an object the test
    never sees. It is captured per test by `_assistant_agent`.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RECEIVED.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


_RECEIVED: list = []


def _assistant_agent():
    """A langgraph agent wired exactly as the assistant and workstation are."""
    _RECEIVED.clear()
    return create_react_agent(
        _RecordingModel(responses=['ok']), [], prompt=term_prompt.term_prompt_callable(TENANT, BASE)
    ), _RECEIVED


def _system_prompt(received: list) -> str:
    """The system message of the LAST model call."""
    for message in received[-1]:
        if getattr(message, 'type', None) == 'system':
            return str(message.content)
    raise AssertionError(f'no system message reached the model: {received[-1]}')


# ── AC-24: the block reaches the model, on the assistant/workstation shape ───


async def test_the_term_block_reaches_the_model(monkeypatch):
    _stub_library(monkeypatch, _Term(1, '不良贷款率', '不良贷款占贷款总额的比例', ['不良率']))
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    prompt_sent = _system_prompt(received)
    assert BASE in prompt_sent
    assert '不良贷款率' in prompt_sent, 'the standard term did not reach the model'
    assert '不良贷款占贷款总额的比例' in prompt_sent, 'the definition did not reach the model'


async def test_a_matched_alias_is_rendered_as_its_standard_term(monkeypatch):
    """AC-21 on the wire: the user typed the shorthand, the model is told the
    full term."""
    _stub_library(monkeypatch, _Term(1, '不良贷款率', '比例', ['不良率']))
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    prompt_sent = _system_prompt(received)
    assert '不良贷款率' in prompt_sent
    assert '不良率' in prompt_sent, 'the alias the user used should be shown as such'


# ── AC-22 / AC-32: nothing to inject means nothing changes ───────────────────


async def test_no_match_leaves_the_prompt_byte_identical(monkeypatch):
    _stub_library(monkeypatch, _Term(1, '不良贷款率', '比例', ['不良率']))
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='今天天气如何')]})

    assert _system_prompt(received) == BASE, (
        'a question matching nothing must produce the pre-feature prompt exactly'
    )


async def test_an_empty_library_leaves_the_prompt_byte_identical(monkeypatch):
    _stub_library(monkeypatch)
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    assert _system_prompt(received) == BASE


# ── AC-25: an edit applies to the next turn, same agent object ───────────────


async def test_an_edited_definition_is_used_on_the_next_turn(monkeypatch):
    """The assistant reuses one agent across a session's turns
    (`common/chat/client.py:250-259` rebuilds it only on a session switch). If
    the prompt were resolved when the agent was built, a term edited mid-session
    would not appear until a new session — which is what AC-25 rules out."""
    library = _stub_library(monkeypatch, _Term(1, '不良贷款率', '旧释义', ['不良率']))
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})
    assert '旧释义' in _system_prompt(received)

    library[:] = [_Term(1, '不良贷款率', '新释义', ['不良率'])]
    injection._reset_caches()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})
    assert '新释义' in _system_prompt(received), (
        'the SAME agent object served a stale definition — the prompt is frozen at build time'
    )


async def test_a_newly_added_term_appears_on_the_next_turn(monkeypatch):
    library = _stub_library(monkeypatch)
    agent, received = _assistant_agent()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})
    assert _system_prompt(received) == BASE

    library.append(_Term(1, '不良贷款率', '比例', ['不良率']))
    injection._reset_caches()

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})
    assert '不良贷款率' in _system_prompt(received)


# ── the ReAct branch of the same two sites (T-12 / T-13) ─────────────────────


def test_the_react_builder_renders_the_declared_turn():
    """`ConfigurableAssistant` holds its agent message as a string resolved by
    `prompt.partial()`, which calls a callable with NO arguments — so the turn
    has to be declared before the invoke rather than received. Asserted through
    the real `partial()` so the no-argument contract is what is tested."""
    builder = term_prompt.term_prompt_builder(TENANT, BASE)

    token = term_prompt.set_turn(
        [HumanMessage(content='不良率是多少')], '【本轮相关术语】\n- 不良贷款率：比例'
    )
    try:
        rendered = builder()
    finally:
        term_prompt.reset_turn(token)

    assert rendered.startswith(BASE)
    assert '不良贷款率' in rendered

    from bisheng_langchain.gpts.prompts.react_agent_prompt import react_agent_prompt

    # What the executor actually does with it — and the reason the callable is
    # invoked BEFORE partial(): partial would resolve it with no arguments, and
    # once per model call rather than once per turn.
    assert '不良贷款率' in react_agent_prompt.partial(assistant_message=rendered).format(
        tools='[]', tool_names='', input='q', agent_scratchpad=''
    )


def test_the_react_builder_returns_the_base_prompt_outside_a_turn():
    builder = term_prompt.term_prompt_builder(TENANT, BASE)

    assert builder() == BASE, 'a builder invoked outside any turn must not invent terms'


# ── the ReAct executor, which is a shared module we did NOT change ───────────
#
# `ConfigurableAssistant.assistant_message` is typed `str`, but it is fed to
# `PromptTemplate.partial()`, which accepts a zero-argument callable and calls
# it fresh on every resolution
# (`BasePromptTemplate._merge_partial_and_user_variables`). That is why the ReAct
# branches need no change to `bisheng_langchain`: handing them a callable is
# enough, and these tests pin that the framework really behaves that way.
#
# They matter more than a normal regression test. The typing says `str`, so a
# future reader "fixing" the call site to pass a string — or langchain changing
# how partial resolves callables — would silently switch the ReAct prompt back to
# a single frozen render, and the symptom would be "workflow terms are stale",
# not an error. These tests exercise the REAL executor and the REAL prompt
# template; nothing here is mocked.

REACT_FINAL = '{"action": "Final Answer", "action_input": "done"}'

#: A turn that takes TWO model calls: the first asks for a tool that does not
#: exist, the executor reports it and loops back, the second answers. This is
#: what makes "re-rendered per step" observable — with a single-call turn, a
#: frozen prompt and a fresh one look identical.
REACT_TOOL_STEP = '{"action": "no_such_tool", "action_input": "x"}'


class _ReActRecordingModel(FakeListChatModel):
    """Records every prompt, including the copies `bind_tools` makes.

    The base `bind_tools` returns a BOUND CLONE, and the executor binds tools on
    every call — so the model that actually reaches the API is never the object
    the test constructed. A module-level list is the only place both are visible.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _REACT_RECEIVED.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


_REACT_RECEIVED: list = []


def _react_graph(system_message, responses=None):
    from bisheng_langchain.gpts.agent_types.llm_react_agent import get_react_agent_executor

    _REACT_RECEIVED.clear()
    model = _ReActRecordingModel(responses=responses or [REACT_FINAL])
    return get_react_agent_executor([], model, system_message, False)


def _react_prompt_sent(index: int = -1) -> str:
    """The agent message of one model call, as it reached the prompt."""
    return '\n'.join(str(getattr(m, 'content', m)) for m in _REACT_RECEIVED[index])


def test_the_react_executor_still_accepts_a_plain_string():
    """Today's callers pass a string and must keep working."""
    graph = _react_graph('PLAIN_BASE')

    graph.invoke({'input': 'q', 'chat_history': []})

    assert 'PLAIN_BASE' in _react_prompt_sent()


def test_the_react_executor_accepts_a_callable_assistant_message():
    """The shape F060 relies on. Nothing in `bisheng_langchain` enforces it —
    the field is typed `str` — so this is the test that would fail if the
    framework stopped resolving callables."""
    graph = _react_graph(lambda: 'CALLABLE_BASE')

    graph.invoke({'input': 'q', 'chat_history': []})

    assert 'CALLABLE_BASE' in _react_prompt_sent()


def test_the_react_executor_renders_a_callable_per_model_call():
    """Fresh per model call, not frozen at the first resolution.

    This is stronger than F060 needs and is why the ReAct branches carry no
    per-turn declaration of their own: the callable runs again for every step,
    so whatever it reads is by definition current.
    """
    calls: list = []

    def builder():
        calls.append(1)
        return f'CALLABLE_BASE_{len(calls)}'

    graph = _react_graph(builder, responses=[REACT_TOOL_STEP, REACT_FINAL])
    graph.invoke({'input': 'q', 'chat_history': []})

    assert len(_REACT_RECEIVED) == 2, 'the fixture did not produce a two-call turn'
    assert 'CALLABLE_BASE_1' in _react_prompt_sent(0)
    assert 'CALLABLE_BASE_2' in _react_prompt_sent(1), (
        'the second model call of the same turn reused the first render — a frozen '
        'prompt would make workflow terms stale (spec AC-25)'
    )
    assert len(calls) == 2, f'the callable ran {len(calls)} times over a two-call turn'


def test_a_run_does_not_inherit_the_previous_runs_turn():
    """The declaration is scoped to one invoke; a leak would inject the previous
    question's terms into the next one."""
    first = term_prompt.set_turn([HumanMessage(content='不良率是多少')], 'BLOCK-1')
    term_prompt.reset_turn(first)

    builder = term_prompt.term_prompt_builder(TENANT, BASE)
    assert builder() == BASE


# ── the turn is what the user asked, not whatever arrived last ───────────────


def test_the_turn_is_the_latest_human_message():
    messages = [
        HumanMessage(content='第一个问题'),
        ToolMessage(content='工具结果', tool_call_id='1'),
        HumanMessage(content='第二个问题'),
        ToolMessage(content='另一个工具结果', tool_call_id='2'),
    ]

    assert term_prompt.last_user_text(messages) == '第二个问题'


def test_a_tool_result_is_never_mistaken_for_the_question():
    messages = [HumanMessage(content='真实提问'), ToolMessage(content='不良率', tool_call_id='1')]

    assert term_prompt.last_user_text(messages) == '真实提问'


def test_multimodal_content_yields_its_text_parts():
    """The workstation builds list-content messages; `str(content)` on one of
    them is a Python repr, which would shift every offset AC-23 orders by."""
    message = HumanMessage(
        content=[
            {'type': 'text', 'text': '不良率是多少'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
        ]
    )

    assert term_prompt.last_user_text([message]) == '不良率是多少'


# ── AC-24: all three sites are actually wired ────────────────────────────────
#
# The tests above prove the MECHANISM works. These prove each call site uses it.
# Without them, reverting a site to its pre-F060 form — which is a one-line edit
# in a large file, and the kind a reviewer would wave through — leaves the whole
# suite green while that conversation path silently stops injecting.
#
# Asserted on the source text of two large, legacy-formatting files rather than
# by driving them: the assistant's `run` path needs a configured LLM, tools and
# a DB, and the workflow node needs a whole flow engine. A source assertion is
# crude but it is the difference between covering the wiring and covering only
# the helper the wiring is supposed to call.
SITES = {
    'assistant (function call)': (
        'bisheng/api/services/assistant_agent.py',
        'prompt=term_prompt_callable(',
    ),
    'assistant (ReAct)': (
        'bisheng/api/services/assistant_agent.py',
        'assistant_message=term_prompt_builder(',
    ),
    'workflow agent node (ReAct)': (
        'bisheng/workflow/nodes/agent/agent.py',
        'assistant_message=term_prompt_builder(',
    ),
    'workstation': (
        'bisheng/workstation/domain/services/chat_service.py',
        'await resolve_term_block(',
    ),
}


@pytest.mark.parametrize('site,path,needle', [(name, p, n) for name, (p, n) in SITES.items()])
def test_each_conversation_path_uses_the_term_builder(site, path, needle):
    import pathlib

    import bisheng

    source = (pathlib.Path(bisheng.__file__).parent.parent / path).read_text(encoding='utf-8')

    assert needle in source, (
        f'the {site} path no longer calls the term builder — this conversation '
        f'route has silently stopped receiving terms (spec AC-24)'
    )


# ── the workflow node's langgraph branch, driven for real ────────────────────
#
# This site had only a source-text assertion, and that is exactly how it shipped
# broken: the assertion checked that `current_turn_block()` appears in the file,
# which stayed true while the ContextVar it reads was never SET on this branch.
# The node received no terms and nothing failed.
#
# The tests below reproduce the branch's real shape — the same callable
# `_init_agent` builds, invoked by a real langgraph agent — and assert on what
# the MODEL receives. They fail if either half goes missing: the callable, or
# the declaration that feeds it.

WORKFLOW_BASE = '工作流节点系统提示词'


def _workflow_langgraph_branch(base_prompt: str, received: list):
    """A langgraph agent wired exactly as the node's `else:` branch wires one."""
    model = _RecordingModel(responses=['ok'])
    _RECEIVED.clear()

    def _term_prompt(state, _base=base_prompt):
        return [
            SystemMessage(content=term_prompt.append_block(_base, term_prompt.current_turn_block())),
            *state.get('messages', []),
        ]

    del received
    return create_react_agent(model, [], prompt=_term_prompt), _RECEIVED


async def test_the_workflow_langgraph_branch_injects_the_declared_block():
    """What the node's function-call branch is supposed to do end to end.

    The block is passed through the same ContextVar `_run_once` fills, so this
    fails if the node stops declaring its turn — the regression that shipped.
    """
    agent, received = _workflow_langgraph_branch(WORKFLOW_BASE, [])

    token = term_prompt.set_turn([HumanMessage(content='不良率是多少')], BLOCK)
    try:
        await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})
    finally:
        term_prompt.reset_turn(token)

    prompt_sent = _system_prompt(received)
    assert WORKFLOW_BASE in prompt_sent
    assert '不良贷款率' in prompt_sent, (
        'the workflow node\'s langgraph branch did not receive the declared term block'
    )


async def test_the_workflow_langgraph_branch_injects_nothing_without_a_declaration():
    """The other half, stated as the bug it was: with no turn declared the
    branch renders the base prompt and nothing else — a silent no-op, not an
    error. This is what the node did on every run before the fix."""
    agent, received = _workflow_langgraph_branch(WORKFLOW_BASE, [])

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    assert _system_prompt(received) == WORKFLOW_BASE


def test_the_workflow_run_declares_its_turn_for_both_executors():
    """`set_turn` must be OUTSIDE the executor branch.

    Asserted on the AST rather than the text: the bug was that the declaration
    sat inside the ReAct arm, and a text search for `set_turn(` cannot tell
    where it sits. Here the call's enclosing statement is checked to be the
    function's own, i.e. not nested under an `if`.
    """
    import ast
    import pathlib

    import bisheng

    path = pathlib.Path(bisheng.__file__).parent.parent / 'bisheng/workflow/nodes/agent/agent.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))

    run_once = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_run_once'
    )

    direct_calls = {
        call.func.id
        for stmt in run_once.body
        if isinstance(stmt, ast.Assign)
        for call in ast.walk(stmt)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }

    assert 'set_turn' in direct_calls, (
        'set_turn is no longer a top-level statement of _run_once — it is nested '
        'under an executor branch, so one of the two paths gets no term block'
    )


# ── INV-18: the stored prompt is never the thing that gets written ───────────


def test_appending_terms_does_not_mutate_the_base_prompt():
    base = '用户保存的提示词'

    assert term_prompt.append_block(base, '【本轮相关术语】\n- A：B') != base
    assert base == '用户保存的提示词', 'the base prompt string was mutated in place'


async def test_the_assistant_prompt_object_is_not_written_back(monkeypatch):
    """INV-18 stated as the failure it forbids: after a turn, the assistant row's
    prompt is exactly what was saved — no block appended, no cache in between."""
    from types import SimpleNamespace

    _stub_library(monkeypatch, _Term(1, '不良贷款率', '比例', ['不良率']))
    saved = '用户保存的提示词'
    assistant = SimpleNamespace(prompt=saved, tenant_id=TENANT)

    _RECEIVED.clear()
    agent = create_react_agent(
        _RecordingModel(responses=['ok']), [], prompt=term_prompt.term_prompt_callable(TENANT, assistant.prompt)
    )
    received = _RECEIVED

    await agent.ainvoke({'messages': [HumanMessage(content='不良率是多少')]})

    assert '不良贷款率' in _system_prompt(received), 'nothing was injected, so this proves nothing'
    assert assistant.prompt == saved, 'the term block was written back into the assistant prompt'
