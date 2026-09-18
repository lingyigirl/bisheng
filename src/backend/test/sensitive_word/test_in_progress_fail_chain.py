"""`in_progress` must walk the fail chain, or mask mode leaks most of a word.

The bug this guards against is subtle enough to have shipped, and the reason
it survived testing is worth stating: it cannot occur unless the word list
contains a pair where **one word's suffix is another word's prefix**. A test
word list without that structure passes no matter how many chunk boundaries
are tried, because the trigger condition is never met — the earlier round of
verification used `['绝密文件', '密码', 'ab']` and reported "zero leaks" from
25 chunkings that were really 25 repetitions of one non-triggering case.

So every test here first asserts that its word list actually has the trigger,
via `_has_suffix_prefix_pair`. That assertion is the point of this file as
much as the leak checks are: it is what stops this test from joining the
family of tests that cannot fail.

The trigger, concretely. A trie node is a leaf exactly when a word ends
there, so `bool(node.children)` answers "can THIS node extend". But the text
consumed so far can still be a live prefix of a longer pattern through a
SHORTER suffix, reached by the fail link:

    words  'jb'  and  'bbc中文网'
    stream 'jbbc中文网'

After 'jb' the scan sits on the 'jb' node — a leaf, no children. Its fail
link points at the 'b' node, which does extend. Asking only the current node
returns False, the 'b' is emitted, and when 'bbc中文网' completes the next
chunk the mask drops it (its span starts before the emit boundary). The user
sees five of six characters of a matched word.
"""

import pytest

from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton


@pytest.fixture(autouse=True)
def no_platform_layer(monkeypatch):
    """Switch the F059 platform layer off; these tests are about the TENANT layer.

    Not optional. Without it `check_output_chunk` consults the platform policy
    table, which means a real MySQL round trip per chunk through a connection
    bound to the pytest event loop -- observed as
    `AttributeError: 'NoneType' object has no attribute 'send'` from aiomysql
    once an earlier test's loop had closed, which reads like a masking bug and
    is not one. Same fixture and same reasoning as `test_chat_guard.py`.
    """
    from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult

    async def _no_platform_matcher():
        return None

    async def _no_platform_text(text):
        return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)

    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.platform_word_service.PlatformWordService.build_platform_matcher',
        _no_platform_matcher,
        raising=False,
    )
    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.platform_word_service.PlatformWordService.check_platform_text',
        _no_platform_text,
        raising=False,
    )


def _has_suffix_prefix_pair(words) -> bool:
    """True when some word's proper suffix is a proper prefix of another word.

    Without this, the leak this file tests for is UNREACHABLE — no chunking
    schedule can produce it. Asserted rather than assumed so that a future
    edit to the word lists below cannot silently turn these tests vacuous.
    """
    for word in words:
        for start in range(1, len(word)):
            suffix = word[start:]
            for other in words:
                if other != word and other.startswith(suffix) and len(other) > len(suffix):
                    return True
    return False


# (word list, a triggering stream) — each list is asserted to have the trigger.
#
# Passed through pytest as a plain list index rather than as parametrized
# arguments on purpose: this repo runs on Windows, where pytest collects
# parametrize ids and arguments under the console's GBK codec, and a Chinese
# word list arrives mangled (observed: '习尽评吓台' reached the test body as
# 'ϰ������̨', so the leak assertions ran against garbage words and reported
# failures that had nothing to do with the code). Indexing keeps the strings
# inside the module, where they are read as UTF-8 source.
CASES = [
    (['jb', 'bbc中文网'], 'jbbc中文网'),
    (['习尽评吓台', '台独万岁'], '习尽评吓台独万岁'),
    (['敏感', '感词库'], '敏感词库'),
]

CASE_IDS = ['agent-repro', 'real-builtin-pair', 'cn-suffix-sharing']


@pytest.mark.parametrize('case_index', range(len(CASES)), ids=CASE_IDS)
def test_the_word_list_actually_has_the_trigger(case_index):
    """Guards the guards: a list without a suffix/prefix pair cannot expose the
    fail-chain bug, so the leak assertions below would be vacuous."""
    words, _ = CASES[case_index]
    assert _has_suffix_prefix_pair(words), (
        f'{words!r} has no word whose suffix prefixes another word, so the '
        'fail-chain leak is unreachable and the leak tests below prove nothing'
    )


@pytest.mark.parametrize('case_index', range(len(CASES)), ids=CASE_IDS)
def test_in_progress_walks_the_fail_chain(case_index):
    """The unit-level fix: after consuming a prefix, `in_progress` must be True
    whenever ANY suffix is a live pattern prefix — not only when the current
    node happens to have children."""
    words, text = CASES[case_index]
    automaton = ACAutomaton(words)

    for probe_len in range(1, len(text) + 1):
        state, _, _ = automaton.find_all_matches(None, text[:probe_len])
        if automaton.in_progress(state):
            return
    pytest.fail(f'no prefix of {text!r} reported in_progress; fail chain not walked')


def _expected_mask(words, text) -> str:
    """The correct output: every character covered by ANY match becomes ``*``.

    Computed by marking a coverage set rather than by successive `str.replace`.
    The replace form is wrong whenever matches OVERLAP, which is exactly what
    these word lists are built to produce: for `['jb', 'bbc中文网']` on
    `'jbbc中文网'`, replacing the long word first leaves `'j******'` and
    replacing `'jb'` then finds nothing, so it under-masks by one character and
    the test fails against CORRECT behaviour. Marking coverage per position
    handles overlap naturally.
    """
    masked = list(text)
    for word in words:
        start = text.find(word)
        while start != -1:
            for i in range(start, start + len(word)):
                masked[i] = '*'
            start = text.find(word, start + 1)
    return ''.join(masked)


def _matcher(words):
    from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import WordMatcher

    return WordMatcher(
        automaton=ACAutomaton(words),
        normalized_map={w: w for w in words},
        case_sensitive=True,
        auto_reply='[X]',
        extra_config={},
        max_word_len=max(len(w) for w in words),
    )


async def _stream_masked(words, text, chunk_size) -> str:
    """Feed `text` to a real output guard and collect everything it emits.

    Written as a plain `async def` and awaited by the test, matching this
    repo's `asyncio_mode = "auto"`. Calling `asyncio.run()` inside a sync test
    instead would create and close a fresh loop per case, which leaves the
    aiomysql connections the guard opens bound to a dead loop and turns every
    later conftest-level cleanup into a spurious "Event loop is closed".
    """
    from unittest.mock import AsyncMock, patch

    from bisheng.sensitive_word.domain.services.chat_guard import (
        ChatGuardService,
        OutputGuardState,
    )

    state = OutputGuardState(
        enabled=True, action='mask', auto_reply='[X]', matcher=_matcher(words), platform_matcher=None
    )
    out: list[str] = []
    with patch.object(ChatGuardService, '_write_hit_log', new=AsyncMock(return_value=None)):
        for i in range(0, len(text), chunk_size):
            result = await ChatGuardService.check_output_chunk(state, text[i : i + chunk_size])
            out.append(result.safe_text)
        out.append((await ChatGuardService.flush_output(state)).safe_text)
    return ''.join(out)


@pytest.mark.parametrize('chunk_size', [1, 2, 3])
@pytest.mark.parametrize('case_index', range(len(CASES)), ids=CASE_IDS)
async def test_mask_never_emits_a_partial_match(case_index, chunk_size):
    """The user-visible property: a matched word may only ever appear as ``*``.

    Asserted by reconstructing the whole expected string, NOT by searching for
    word suffixes in the output. The substring form is subtly wrong and fails
    on correct behaviour: in `['习尽评吓台', '台独万岁']` streamed as
    `'习尽评吓台独万岁'`, `台独万岁` ends at the final character, so that character
    legitimately appears as the tail of its own (masked) match.

    Comparing full strings also catches a leak of ANY length, including a
    single character, which a substring search over the shorter fragments
    would miss.
    """
    words, text = CASES[case_index]

    emitted = await _stream_masked(words, text, chunk_size)

    assert emitted == _expected_mask(words, text), (
        f'chunk_size={chunk_size}: mask did not cover every match.\n'
        f'  input    {text!r}\n'
        f'  expected {_expected_mask(words, text)!r}\n'
        f'  emitted  {emitted!r}'
    )


async def test_a_normal_answer_is_unchanged():
    """The fix must not start withholding text that has no pattern in it."""
    text = '这是一段完全正常的回答内容'
    assert await _stream_masked(['jb', 'bbc中文网'], text, 1) == text


async def test_case_data_reaches_the_test_body_intact():
    """Diagnostic: the parametrized cases must arrive with their characters
    unchanged. Guards against console/codec mangling being mistaken for a
    masking defect (which is exactly the confusion that occurred here)."""
    assert CASES[0] == (['jb', 'bbc中文网'], 'jbbc中文网'), f'got {CASES[0]!r}'
    assert CASES[1] == (['习尽评吓台', '台独万岁'], '习尽评吓台独万岁'), f'got {CASES[1]!r}'
    for index in range(len(CASES)):
        words, text = CASES[index]
        assert await _stream_masked(words, text, 2) == _expected_mask(words, text)
