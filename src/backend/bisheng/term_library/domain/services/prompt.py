"""Prompt builders that append this turn's term block (F060, spec AC-24 ~ AC-28).

Three conversation paths need the same thing: a system prompt carrying the term
block for the question being asked RIGHT NOW. They differ only in how the
framework lets them supply it, and that is why there are several functions here.

**The block is resolved where the turn is known, and the prompt layer only
concatenates.** That split is forced by the event loop. The assistant and the
workstation run on the loop, and spec AC-28 requires injection not to block it —
so on those paths the library read is awaited where the turn is already in hand,
and what gets stored for the prompt builder is the finished string. A builder
that did its own I/O would have to either block the loop or be async, and
neither framework shape allows the latter:

  * langgraph's `prompt` callable — see `system_message_with_terms`, async;
  * `ConfigurableAssistant`'s `assistant_message` — a plain string, resolved by
    `prompt.partial()`, which calls a callable with NO arguments
    (`BasePromptTemplate._merge_partial_and_user_variables`). There is no turn
    to hand it and no loop to await on, which is why that branch reads the turn
    from a ContextVar instead.

**Nothing here writes.** The block is returned as a string, appended to a
message, and stored nowhere: a user's prompt field keeps exactly the text they
saved (contract INV-18). That is also why an edit takes effect on the very next
turn with no invalidation to reason about.

An empty block is not an empty section: every function here returns the prompt
UNCHANGED when no term matched, so a question that mentions none of the library
produces byte-identical input to the pre-feature build (spec AC-22 / AC-32).
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

from langchain_core.messages import BaseMessage, SystemMessage
from loguru import logger

from bisheng.term_library.domain.services.injection import build_term_block


def user_message_text(message: BaseMessage) -> str:
    """The text of a message, whatever shape its content takes.

    Multimodal messages carry a list of parts (`[{"type": "text", ...}, {...}]`),
    which is what the workstation builds before invoking the graph (the workflow
    Agent node wraps its prompt the same way). A plain `str(content)` on one of
    those yields a Python repr, so the match would run against
    `[{'type': 'text', 'text': '不良率是多少'}]` — the question is still findable
    by substring, but every offset AC-23 orders by is shifted by the repr.
    """
    content = getattr(message, 'content', '')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get('type') == 'text':
                parts.append(str(part.get('text') or ''))
        return '\n'.join(parts)
    return str(content)


def last_user_text(messages: list[BaseMessage] | None) -> str:
    """The most recent human message, or '' when the turn has none.

    Backwards, because the newest is what this turn is asking. Only human
    messages count: tool results arrive as their own message type and are not
    questions, so matching against them would inject a definition the user never
    mentioned.
    """
    for message in reversed(messages or ()):
        if getattr(message, 'type', None) == 'human':
            return user_message_text(message)
    return ''


def append_block(base_prompt: str, block: str) -> str:
    """`base_prompt` with the block appended, or untouched when the block is ''.

    The single place the "empty means unchanged" rule is implemented, so no
    caller has to remember it (spec AC-22).
    """
    if not block:
        return base_prompt
    return f'{base_prompt}\n\n{block}'


async def resolve_term_block(tenant_id: int, messages: list[BaseMessage] | None) -> str:
    """This turn's block, for a caller that is already async.

    The read is awaited rather than bridged: on the event loop a synchronous
    read would block it (AC-28), and `run_async_safe` would refuse outright.
    """
    return await build_term_block(tenant_id, last_user_text(messages))


async def system_message_with_terms(
    tenant_id: int, base_prompt: str, messages: list[BaseMessage]
) -> list[BaseMessage]:
    """`[SystemMessage(base_prompt + terms)] + messages` — the langgraph form.

    Whatever a `prompt` callable returns REPLACES the message list rather than
    being prepended to it, so the turn's messages are carried through explicitly.
    """
    block = await resolve_term_block(tenant_id, messages)
    return [SystemMessage(content=append_block(base_prompt, block)), *messages]


def term_prompt_callable(tenant_id: int, base_prompt: str):
    """An ASYNC `prompt` callable for langgraph, with the base prompt bound.

    **`async def`, not a lambda returning a coroutine.** langgraph picks the
    sync or async path with `inspect.iscoroutinefunction(prompt)`
    (`prebuilt/chat_agent_executor._get_prompt_runnable`); a sync lambda that
    merely returns an awaitable takes the sync path, and the coroutine is
    dropped with a "never awaited" warning and then a ValueError from the model.
    A factory is used rather than `functools.partial` so the coroutine-function
    check is unambiguous.
    """

    async def _prompt(state) -> list[BaseMessage]:
        messages = state.get('messages', []) if isinstance(state, dict) else getattr(state, 'messages', [])
        return await system_message_with_terms(tenant_id, base_prompt, messages)

    return _prompt


# ---------------------------------------------------------------------------
# The turn, for the branch the framework gives no way to pass it to.
# ---------------------------------------------------------------------------
#
# `ConfigurableAssistant` holds its agent message as a string, resolved once at
# construction — long before the node's input is formatted. The executor could
# carry the turn through its graph state, but that means adding a channel to a
# shared, published module for one feature's benefit. Instead the caller stores
# the turn here while it invokes, and the builder reads it.
#
# A ContextVar, not a module global: several runs execute concurrently in one
# event loop, and a global would let one tenant's question be read by another
# tenant's run — the leak INV-16 exists to prevent. `set_turn_messages` returns
# a token that must be reset in a `finally`.

_TURN_MESSAGES: ContextVar[tuple] = ContextVar('term_library_turn_messages', default=())
_TURN_BLOCK: ContextVar[str] = ContextVar('term_library_turn_block', default='')


def set_turn(messages: list[BaseMessage] | None, block: str):
    """Declare this turn: its messages (for the match) and its resolved block."""
    return (_TURN_MESSAGES.set(tuple(messages or ())), _TURN_BLOCK.set(block or ''))


def reset_turn(token) -> None:
    message_token, block_token = token
    _TURN_MESSAGES.reset(message_token)
    _TURN_BLOCK.reset(block_token)


def current_turn_text() -> str:
    """The declared turn's question."""
    return last_user_text(_TURN_MESSAGES.get())


def current_turn_block() -> str:
    """The declared turn's resolved term block, or '' when none was declared.

    For a synchronous prompt callable — the workflow Agent node's langgraph
    branch — which runs on the graph's thread: it may concatenate, but it must
    not read the library itself (see the module note).
    """
    return _TURN_BLOCK.get()


def term_prompt_builder(tenant_id: int, base_prompt: str) -> Callable[[], str]:
    """A zero-argument callable rendering `base_prompt` with the declared block.

    **Zero arguments is the contract, not a simplification.** `prompt.partial()`
    resolves a callable as `v if not callable(v) else v()` — no arguments, and
    no way to pass any. It performs no I/O of its own: the block was resolved by
    `set_turn` before the invoke, on the branch that had the loop to await on.

    `tenant_id` is kept in the signature for the executor's benefit and for
    logging; the block is already tenant-scoped by then.
    """
    del tenant_id

    def _build() -> str:
        return append_block(base_prompt, _TURN_BLOCK.get())

    return _build


def resolve_term_block_sync(tenant_id: int, messages: list[BaseMessage] | None) -> str:
    """The block for a SYNC caller, via the repo's sync-to-async bridge.

    The workflow Agent node is the one such caller: the whole workflow graph runs
    synchronously (`async_mode` is never enabled), so there is no loop to await
    on and `run_async_safe` is the established way to reach async DB code from
    there — it also carries the tenant ContextVar across, which the DAO's filter
    depends on.

    **Degrades to '' on any failure, including "called from a running loop".**
    `run_async_safe` refuses rather than blocking a loop, and spec AC-28 forbids
    blocking it; a workflow that cannot have a glossary must still run (design
    §7.4). The block is an enhancement, and losing it is not an error.
    """
    from bisheng.utils.async_utils import run_async_safe

    try:
        return run_async_safe(build_term_block(tenant_id, last_user_text(messages)))
    except Exception:
        logger.opt(exception=True).warning(
            'term block unavailable for tenant={}; continuing without terms', tenant_id
        )
        return ''
