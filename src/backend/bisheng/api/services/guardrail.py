"""F056: build chat guard callables for injection into ``common/chat`` clients.

``common/`` must not import domain services (constitution C1 / arch-guard RULE-1),
so the guard wiring lives here in the API layer and is injected through
``ChatManager.dispatch_client`` at the ws endpoints (design 决策 2/3).
"""

from bisheng.common.chat.types import OutputGuardResult
from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.sensitive_word.domain.services.chat_guard import ChatGuardService


def build_input_guard(login_user: UserPayload, flow_id: str):
    """Build an async ``(chat_id, text) -> auto_reply | None`` input guard.

    Returns the policy auto_reply when ``text`` hits the tenant's chat_input policy,
    None when allowed. Tenant/user context is captured at build time — the ws
    endpoints run in the API process where the tenant ContextVar is populated.
    """
    tenant_id = get_current_tenant_id() or login_user.tenant_id
    user_id = login_user.user_id

    async def guard(chat_id: str, text: str) -> str | None:
        result = await ChatGuardService.check_chat_input(
            tenant_id=tenant_id,
            text=text,
            flow_id=flow_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if result.enabled and result.hits:
            return result.auto_reply
        return None

    return guard


def build_output_guard(login_user: UserPayload, flow_id: str):
    """Build an async ``(chat_id, chunk) -> OutputGuardResult`` stream guard.

    Guard state lives in this closure for the lifetime of one ws connection.
    An empty ``chunk`` signals stream end and flushes the mask-mode hold-back tail.
    ``full_text()`` returns everything emitted so far (guarded), so the client can
    rewrite the final answer frame with the guarded text (design 坑 6).
    ``reset()`` starts a fresh round (new run after a blocked answer).
    """
    tenant_id = get_current_tenant_id() or login_user.tenant_id
    return _build_output_guard(tenant_id=tenant_id, user_id=login_user.user_id, flow_id=flow_id)


def build_output_guard_for_callback(*, tenant_id: int, user_id: int, flow_id: str):
    """Variant for the assistant stream callback (api layer, no ``login_user``)."""
    return _build_output_guard(tenant_id=tenant_id, user_id=user_id, flow_id=flow_id)


def _build_output_guard(*, tenant_id: int, user_id: int, flow_id: str):
    state = None
    accumulated: list[str] = []

    async def guard(chat_id: str, chunk: str) -> OutputGuardResult:
        nonlocal state
        if state is None:
            state = ChatGuardService.start_output_guard(
                tenant_id, flow_id=flow_id, chat_id=chat_id, user_id=user_id
            )
        if chunk == '' and state.enabled and state.tail:
            flushed = await ChatGuardService.flush_output(state)
            if flushed.safe_text:
                accumulated.append(flushed.safe_text)
            return OutputGuardResult(safe_text=flushed.safe_text)
        result = await ChatGuardService.check_output_chunk(state, chunk)
        out_text = result.safe_text
        if result.blocked:
            out_text += state.auto_reply
        if out_text:
            accumulated.append(out_text)
        return OutputGuardResult(safe_text=out_text, blocked=result.blocked)

    async def full_text() -> str:
        """Everything emitted so far, guarded — and COMPLETE.

        Flushes the hold-back tail first. Callers use this to rewrite the final
        answer (design pitfall 6), and without the flush the tail is simply
        dropped: mask mode withholds ``min(max_word_len - 1, len)`` characters
        whenever the text ends on a live pattern prefix, so an answer ending in
        one came back truncated — measured, `'上述内容仅供学习参考'` returned
        `'上述内容仅供学习'`, and a short answer whose whole tail is a prefix
        came back EMPTY and was persisted that way.

        The ordering trap is worth naming, because all three call sites had it:
        they flush with ``guard(chat_id, '')`` BEFORE feeding the answer, which
        is a no-op since nothing is held back yet, and then never flush again
        after the answer has been fed. Pushing the flush here rather than into
        each caller means a future call site cannot get it wrong.

        Idempotent: `flush_output` clears the tail, so a second call adds
        nothing, and it returns early when nothing is pending.
        """
        nonlocal state
        if state is not None and state.enabled and state.tail:
            flushed = await ChatGuardService.flush_output(state)
            if flushed.safe_text:
                accumulated.append(flushed.safe_text)
        return ''.join(accumulated)

    async def reset() -> None:
        nonlocal state
        state = None
        accumulated.clear()

    guard.full_text = full_text  # type: ignore[attr-defined]
    guard.reset = reset  # type: ignore[attr-defined]
    return guard
