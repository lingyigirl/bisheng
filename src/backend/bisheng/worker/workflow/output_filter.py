"""Filter a workflow answer BEFORE it is persisted to chat history.

Why this exists as its own module, separate from the API-layer guard:

The output guard that protects the websocket lives in the **API process** — it is
a closure built by ``api/services/guardrail.py`` and injected into the chat
client. Chat history is written by the **worker process**, in
``RedisCallback.save_chat_message``. Different process, so the guard object is
not reachable and cannot be reused here.

That split had a consequence: the worker saved the answer the moment the
stream finished, while the guard only rewrote the websocket frame afterwards.
The user saw a filtered answer and the history kept the unfiltered one — the
opposite of what the feature promises (design pitfall 6), and a compliance
problem rather than a cosmetic one, since the point of filtering is that the
text is not retained.

Scope, decided with the user: the TENANT policy layer only. The platform-wide
library (F059) is checked in an async service that a sync worker path cannot
call, and extending it belongs with that feature rather than here. Answers
blocked by the platform layer therefore still reach history in full — recorded
here so the remaining gap is known rather than assumed closed.

Blocked answers are stored as the SAFE PREFIX PLUS THE CLOSE-OUT PHRASE, i.e.
exactly what the user saw. Not dropped: a history with a hole in it reads as a
bug, and the phrase is what tells the user why the answer stopped.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def filter_answer_text(text: str, tenant_id: int | None) -> str:
    """Return ``text`` with the tenant's output policy applied.

    Goes through ``build_matcher`` rather than ``check_text`` on purpose: the
    configured action lives on the MATCHER's ``extra_config``, not on
    ``SensitiveWordCheckResult`` (which carries only enabled/hits/auto_reply).
    Using ``check_text`` here would mean inventing an action, and the obvious
    default — mask — is wrong for a tenant configured to block.

    Best-effort by design: detection is a safety net around the answer, and a
    failure in it must not cost the user their answer. On any error the original
    text is returned and the failure is logged; storing an unfiltered message is
    the lesser harm compared with losing it.
    """
    if not text or tenant_id is None:
        return text

    try:
        from bisheng.sensitive_word.domain.schemas import SensitiveWordBusinessType
        from bisheng.sensitive_word.domain.services.chat_guard import (
            _ALLOWED_ACTIONS,
            DEFAULT_OUTPUT_ACTION,
        )
        from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import (
            SensitiveWordPolicyService,
        )

        matcher = SensitiveWordPolicyService.build_matcher(
            tenant_id=tenant_id,
            business_type=SensitiveWordBusinessType.CHAT_OUTPUT,
        )
    except Exception:
        logger.opt(exception=True).warning(
            'output filter: policy lookup failed for tenant={}; storing unfiltered text',
            tenant_id,
        )
        return text

    if matcher is None:
        return text

    scan_text = text if matcher.case_sensitive else text.lower()
    counter = matcher.automaton.find_all(scan_text)
    if not counter:
        return text

    action = matcher.extra_config.get('action', DEFAULT_OUTPUT_ACTION)
    if action not in _ALLOWED_ACTIONS:
        action = DEFAULT_OUTPUT_ACTION

    # Report the word list's own spelling, matching what the guard logs — the
    # matched text may differ from it in case in a case-insensitive policy.
    hits = [matcher.normalized_map.get(word, word) for word in counter]

    if action == 'block':
        reply = matcher.auto_reply or ''
        # Idempotence guard, and it is load-bearing rather than defensive: the
        # close-out phrase is tenant-configured and typically CONTAINS the very
        # words that trigger the policy (the shipped default is '命中敏感词').
        # This function runs in the worker and the API-layer guard runs on the
        # same message afterwards, so without this the second pass finds the
        # phrase's own words, cuts there, and appends the phrase AGAIN --
        # measured, the stored text grew by one phrase per pass.
        if _ends_the_block(text, hits, reply):
            return text
        # Mirror the websocket behaviour: everything before the first hit, then
        # the configured close-out phrase, so history matches what the user read.
        return text[: _first_hit_start(text, hits)] + reply

    return _mask(text, hits)


def _ends_the_block(text: str, hits, reply: str) -> bool:
    """True when ``text`` is already the result of blocking with this phrase.

    That is the case when it ends with the phrase and nothing before that ending
    still hits the policy -- i.e. the only hits present are the phrase's own.
    """
    if not reply or not text.endswith(reply):
        return False
    prefix = text[: len(text) - len(reply)]
    return not any(word and word in prefix for word in hits)


def _first_hit_start(text: str, hits) -> int:
    """Index of the earliest hit in ``text``, or len(text) when none is found."""
    positions = [found for word in hits if (found := text.find(word)) != -1]
    return min(positions) if positions else len(text)


def _mask(text: str, hits) -> str:
    """Replace each hit with equal-length asterisks."""
    chars = list(text)
    for word in hits:
        if not word:
            continue
        start = text.find(word)
        while start != -1:
            for i in range(start, min(start + len(word), len(chars))):
                chars[i] = '*'
            start = text.find(word, start + 1)
    return ''.join(chars)


def filter_chat_response_message(message: Any, tenant_id: int | None) -> Any:
    """Apply the filter to a ``ChatResponse.message``, preserving its shape.

    The field is a dict for regular answers (text under ``msg``) but can be a
    plain string, and every other consumer of this message -- citation
    extraction, the frontend -- depends on the shape staying what it was. So the
    filtered text goes back into the same slot it came from, and anything that
    is neither a string nor a dict with a string ``msg`` is returned untouched
    rather than guessed at.
    """
    if isinstance(message, str):
        return filter_answer_text(message, tenant_id)

    if isinstance(message, dict) and isinstance(message.get('msg'), str):
        filtered = filter_answer_text(message['msg'], tenant_id)
        if filtered != message['msg']:
            return {**message, 'msg': filtered}
        return message

    if isinstance(message, list):
        # Some frames carry a list of blocks; filter each one's text if present.
        changed = False
        out = []
        for item in message:
            if isinstance(item, dict) and isinstance(item.get('msg'), str):
                filtered = filter_answer_text(item['msg'], tenant_id)
                changed = changed or filtered != item['msg']
                out.append({**item, 'msg': filtered})
            else:
                out.append(item)
        return out if changed else message

    return message
