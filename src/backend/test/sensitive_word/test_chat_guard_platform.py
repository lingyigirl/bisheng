"""T009 (F059): the platform layer's precedence over the tenant layer.

The behaviours under test are the ones a future reader is most likely to "fix"
by mistake, so each carries the reasoning inline:

- platform short-circuits input (spec AC-09/AC-12)
- the platform layer applies even when the TENANT layer is off (spec AC-08)
- on output, whichever layer hits governs that chunk, and a block stops at the
  EARLIEST hit of the two (spec AC-12)
- one accepted cross-chunk limitation is asserted as EXPECTED, not as a bug
  (design §3 决策 3 / §8 — user-confirmed 2026-09-17)
"""

from types import SimpleNamespace

import pytest

from bisheng.sensitive_word.domain.models.hit_log import SensitiveWordHitLogDao
from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult, SensitiveWordHit
from bisheng.sensitive_word.domain.services.chat_guard import (
    SOURCE_PLATFORM,
    SOURCE_TENANT,
    ChatGuardService,
    OutputGuardState,
)

TENANT_POLICY_PATH = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'
PLATFORM_MATCHER_PATH = 'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.build_platform_matcher'
PLATFORM_TEXT_PATH = 'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.check_platform_text'


class _LogRecorder:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def stub_hit_log(monkeypatch):
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)
    return recorder


def _tenant_policy(*, enabled=True, custom_words='', auto_reply='租户话术', action='mask'):
    return SimpleNamespace(
        enabled=enabled,
        words_types=['custom'],
        custom_words=custom_words,
        auto_reply=auto_reply,
        extra_config={'action': action},
    )


def _platform_matcher(*, words, auto_reply='平台话术', action='mask'):
    """A real WordMatcher so the automaton logic under test is the production one."""
    from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton
    from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import WordMatcher

    normalized = {word.lower(): word for word in words}
    return WordMatcher(
        automaton=ACAutomaton(list(normalized.keys())),
        normalized_map=normalized,
        case_sensitive=False,
        auto_reply=auto_reply,
        extra_config={'action': action},
        max_word_len=max((len(w) for w in normalized.values()), default=0),
    )


def _stub_platform(monkeypatch, matcher):
    async def _build():
        return matcher

    monkeypatch.setattr(PLATFORM_MATCHER_PATH, _build)


def _stub_platform_text(monkeypatch, *, hits=None, auto_reply='平台话术'):
    async def _check(text):
        if not hits:
            return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)
        return SensitiveWordCheckResult(
            enabled=True,
            hits=[SensitiveWordHit(word=word, count=text.count(word)) for word in hits if word in text],
            auto_reply=auto_reply,
        )

    monkeypatch.setattr(PLATFORM_TEXT_PATH, _check)


# ---------------------------------------------------------------------------
# Input direction: short-circuit
# ---------------------------------------------------------------------------


async def test_platform_input_hit_short_circuits_the_tenant_layer(monkeypatch, stub_hit_log):
    """Spec AC-09/AC-12: platform reply wins and the tenant policy is not consulted.

    Proven by making the tenant policy EXPLODE — if it were still consulted, the
    test would error rather than pass.
    """
    _stub_platform_text(monkeypatch, hits=['信贷违规'], auto_reply='平台话术')

    def _tenant_must_not_be_read(**kwargs):
        raise AssertionError('tenant policy must not be consulted when the platform layer hits')

    monkeypatch.setattr(TENANT_POLICY_PATH, _tenant_must_not_be_read)

    result = await ChatGuardService.check_chat_input(tenant_id=1, text='这里有信贷违规内容')

    assert result.auto_reply == '平台话术'
    assert {hit.word for hit in result.hits} == {'信贷违规'}
    assert stub_hit_log.calls[0]['source'] == SOURCE_PLATFORM


async def test_platform_input_miss_falls_through_to_the_tenant_layer(monkeypatch, stub_hit_log):
    _stub_platform_text(monkeypatch, hits=[])
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(custom_words='租户词'))

    result = await ChatGuardService.check_chat_input(tenant_id=1, text='包含租户词的消息')

    assert result.auto_reply == '租户话术'
    assert {hit.word for hit in result.hits} == {'租户词'}
    assert stub_hit_log.calls[0]['source'] == SOURCE_TENANT


async def test_neither_layer_hits_passes_through(monkeypatch, stub_hit_log):
    _stub_platform_text(monkeypatch, hits=[])
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))

    result = await ChatGuardService.check_chat_input(tenant_id=1, text='普通问题')

    assert result.enabled is False
    assert result.hits == []
    assert stub_hit_log.calls == []


# ---------------------------------------------------------------------------
# Output direction: platform applies even when the tenant layer is off
# ---------------------------------------------------------------------------


async def test_platform_output_layer_works_with_tenant_layer_disabled(monkeypatch, stub_hit_log):
    """Spec AC-08: a tenant cannot switch the platform layer off.

    The tenant policy is disabled, so `start_output_guard` returns an inert state.
    The platform layer must still take effect — this is the whole point of the
    feature, and the reason `enabled` is re-evaluated on the first chunk.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, _platform_matcher(words=['平台禁词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert state.enabled is False  # tenant layer off...

    guarded = await ChatGuardService.check_output_chunk(state, '前面正常内容平台禁词后面内容')

    # ...yet the platform layer blocked it.
    assert guarded.blocked is True
    assert guarded.safe_text == '前面正常内容'
    assert stub_hit_log.calls[0]['source'] == SOURCE_PLATFORM


async def test_output_with_both_layers_off_is_a_passthrough(monkeypatch, stub_hit_log):
    """Spec AC-14: with nothing configured, output is untouched."""
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, None)

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '任意内容')

    assert guarded.safe_text == '任意内容'
    assert guarded.blocked is False
    assert stub_hit_log.calls == []


# ---------------------------------------------------------------------------
# Output direction: which layer governs
# ---------------------------------------------------------------------------


async def test_platform_governs_when_only_it_hits(monkeypatch, stub_hit_log):
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(custom_words='租户词', action='mask'))
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='mask', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '这里平台词出现')

    assert guarded.safe_text == '这里***出现'  # 3 chars, equal-length mask
    assert stub_hit_log.calls[0]['source'] == SOURCE_PLATFORM


async def test_tenant_governs_when_only_it_hits(monkeypatch, stub_hit_log):
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(custom_words='租户词', action='mask'))
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='mask', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '这里租户词出现')

    assert guarded.safe_text == '这里***出现'
    assert stub_hit_log.calls[0]['source'] == SOURCE_TENANT


async def test_platform_action_and_reply_win_when_both_hit_the_same_chunk(monkeypatch, stub_hit_log):
    """Spec AC-12: platform verdict + platform wording when both layers hit."""
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='block', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '开头平台词中间租户词结尾')

    assert guarded.blocked is True
    # Truncated at the EARLIEST of the two hits — the platform word here.
    assert guarded.safe_text == '开头'
    assert stub_hit_log.calls[0]['source'] == SOURCE_PLATFORM
    assert stub_hit_log.calls[0]['action'] == 'block'


async def test_block_truncates_at_the_earliest_hit_across_both_layers(monkeypatch, stub_hit_log):
    """The tenant hit sits EARLIER than the platform hit in the same chunk.

    Truncating at the platform word would emit the tenant-sensitive text in the
    clear, silently disabling the tenant policy on this path. The stop must be at
    the tenant word even though the platform layer governs the wording.
    """
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='block', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '开头租户词中间平台词结尾')

    assert guarded.blocked is True
    assert guarded.safe_text == '开头'  # stops at 租户词, the earlier hit
    assert '租户词' not in guarded.safe_text
    assert '平台词' not in guarded.safe_text


async def test_tenant_mask_keeps_streaming_so_a_later_platform_hit_still_applies(monkeypatch, stub_hit_log):
    """Accepted-boundary counterpart: mask does not stop the stream.

    When the tenant action masks rather than blocks, nothing is irreversibly
    emitted ahead of the platform word, so a platform hit arriving in a later
    chunk takes effect normally.
    """
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='mask', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '开头租户词结束。')
    second = await ChatGuardService.check_output_chunk(state, '后续平台词')

    assert first.blocked is False
    assert '租户词' not in first.safe_text
    assert second.blocked is True
    assert '平台词' not in second.safe_text
    assert stub_hit_log.calls[-1]['source'] == SOURCE_PLATFORM


async def test_tenant_block_earlier_chunk_wins_over_platform_word_later(monkeypatch, stub_hit_log):
    """EXPECTED behaviour, not a defect — do not "fix" this (design §3 / §8).

    Truncation is irreversible: once a chunk has been emitted the user has seen
    it, so a tenant block must stop immediately rather than wait to see whether a
    platform word arrives later. Making the platform word win here would require
    buffering the entire answer before emitting anything, destroying streaming
    (the first-token latency that F056 chose incremental detection to avoid).

    The user reviewed this trade-off on 2026-09-17 and accepted it.
    """
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='block', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '开头租户词')
    second = await ChatGuardService.check_output_chunk(state, '后面平台词')

    assert first.blocked is True
    assert second.blocked is True
    assert second.safe_text == ''  # stream already stopped
    # The tenant wording closes the reply — this is the accepted limitation.
    assert stub_hit_log.calls[0]['source'] == SOURCE_TENANT


# ---------------------------------------------------------------------------
# Matcher snapshot
# ---------------------------------------------------------------------------


async def test_stream_keeps_its_matcher_when_the_library_changes_mid_stream(monkeypatch, stub_hit_log):
    """Spec AC-15: an in-flight round is not rewritten by a mid-stream edit.

    The state holds the matcher object; a later import rebuilds the cache but the
    running stream keeps evaluating against the matcher it started with.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    old_matcher = _platform_matcher(words=['旧词'], action='block', auto_reply='平台话术')
    _stub_platform(monkeypatch, old_matcher)

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '没有命中的开头')
    assert first.blocked is False

    # A super-admin import swaps the library mid-stream.
    _stub_platform(monkeypatch, _platform_matcher(words=['新词'], action='block'))

    second = await ChatGuardService.check_output_chunk(state, '这里有旧词')
    assert second.blocked is True  # still judged by the matcher it started with


async def test_platform_layer_is_resolved_once_per_stream(monkeypatch, stub_hit_log):
    """The lazy lookup must run once, not per chunk — it hits the database."""
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    calls = []

    async def _build():
        calls.append(1)
        return _platform_matcher(words=['平台词'], action='mask')

    monkeypatch.setattr(PLATFORM_MATCHER_PATH, _build)

    state = ChatGuardService.start_output_guard(tenant_id=1)
    for chunk in ('a', 'b', 'c', 'd'):
        await ChatGuardService.check_output_chunk(state, chunk)

    assert len(calls) == 1


async def test_flush_masks_a_held_back_platform_word(monkeypatch, stub_hit_log):
    """The held-back tail is masked against the platform layer at stream end.

    The tail is held back because 'ab' might still grow into 'abc'; once the stream
    ends it cannot, so the completed word is emitted masked rather than revealed.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, _platform_matcher(words=['ab', 'abc'], action='mask'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, 'ab')
    assert first.safe_text == ''  # held back: could still become 'abc'

    flushed = await ChatGuardService.flush_output(state)
    assert flushed.safe_text == '**'


async def test_flush_emits_a_tail_that_never_completed_a_word(monkeypatch, stub_hit_log):
    """A tail that stayed a partial word is emitted VERBATIM, not masked.

    Holding text back must not turn into redaction: if the word the hold-back was
    waiting for never arrives, the original text is what the model produced and
    what the user should see.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, _platform_matcher(words=['abcd'], action='mask'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, 'ab')
    assert first.safe_text == ''

    flushed = await ChatGuardService.flush_output(state)
    assert flushed.safe_text == 'ab'


async def test_block_points_state_auto_reply_at_the_governing_layer(monkeypatch, stub_hit_log):
    """The reply that closes a blocked stream must match the layer that blocked it.

    `api/services/guardrail.py` appends `state.auto_reply` when a chunk comes back
    `blocked`, so `state.auto_reply` — not the matcher's — is what the user
    actually reads. A platform block that left the tenant wording there would
    show the tenant's message for a block the tenant never configured.
    """
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='block', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    guarded = await ChatGuardService.check_output_chunk(state, '开头平台词')

    assert guarded.blocked is True
    assert state.auto_reply == '平台话术'


async def test_block_keeps_tenant_wording_when_the_tenant_layer_blocks(monkeypatch, stub_hit_log):
    """Counterpart: a tenant block still closes with the tenant's own wording."""
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='block', auto_reply='租户话术'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='block', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    await ChatGuardService.check_output_chunk(state, '开头租户词')

    assert state.auto_reply == '租户话术'


async def test_platform_disabled_state_is_reported_not_assumed(monkeypatch):
    """`enabled` starts False and is switched on only after the lookup runs.

    Guards against a caller reading `enabled` before the first chunk and
    concluding the platform layer is absent.
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, None)

    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert state.platform_resolved is False

    await ChatGuardService.check_output_chunk(state, '内容')

    assert state.platform_resolved is True
    assert state.platform_matcher is None
    assert state.enabled is False


async def test_output_guard_state_carries_platform_fields():
    """The platform layer's per-stream bookkeeping is separate from the tenant's."""
    state = OutputGuardState(tenant_id=1)
    assert state.platform_matcher is None
    assert state.platform_resolved is False
    assert state.platform_last_counted_pos == -1
    assert state.platform_action == 'mask'


async def test_mask_still_masks_a_word_held_back_in_a_previous_chunk(monkeypatch, stub_hit_log):
    """A word counted earlier but emitted later must still be masked.

    Regression guard. The two layers keep a per-layer "already counted" watermark
    so a word is not logged twice, and the F059 refactor accidentally reused that
    FILTERED list for the masking pass too. A word counted in one chunk can be held
    back in the tail (it might still extend into a longer pattern) and then
    re-found by the next chunk's rescan — it is already past the watermark, so it
    fell out of the filtered list and was emitted IN THE CLEAR.

    The watermark must gate counting/logging only; masking and the emit-boundary
    decision use the unfiltered match list (which is what F056 passed).
    """
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    # 'ab' and 'abc' together make the automaton mid-pattern at the buffer end,
    # which is what forces 'ab' to be held back in the first chunk's tail.
    _stub_platform(monkeypatch, _platform_matcher(words=['ab', 'abc'], action='mask'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, 'xab')
    second = await ChatGuardService.check_output_chunk(state, 'z')

    # 'ab' was held back (it could still become 'abc'); on re-emit it must be masked.
    assert 'ab' not in (first.safe_text + second.safe_text)
    assert first.safe_text + second.safe_text == 'x**z'


async def test_block_also_stops_on_a_word_held_back_in_a_previous_chunk(monkeypatch, stub_hit_log):
    """Same watermark hazard on the block path: the stream must still stop."""
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(enabled=False))
    _stub_platform(monkeypatch, _platform_matcher(words=['ab', 'abc'], action='block'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    await ChatGuardService.check_output_chunk(state, 'xab')
    second = await ChatGuardService.check_output_chunk(state, 'z')

    assert second.blocked is True
    assert 'ab' not in second.safe_text


async def test_both_layers_hitting_one_chunk_log_two_rows(monkeypatch, stub_hit_log):
    """Each layer's hits are logged under their OWN source.

    A chunk can trip both libraries. A single merged row labelled `platform`
    would have ALL its words stripped from the tenant's log view (the tenant path
    redacts platform rows wholesale), so the tenant would lose sight of matches
    from THEIR OWN policy — breaking spec AC-19 and mis-stating the count.
    """
    monkeypatch.setattr(
        TENANT_POLICY_PATH,
        lambda **kwargs: _tenant_policy(custom_words='租户词', action='mask'),
    )
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='mask', auto_reply='平台话术'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    await ChatGuardService.check_output_chunk(state, '开头平台词中间租户词结尾')

    by_source = {call['source']: call for call in stub_hit_log.calls}
    assert set(by_source) == {SOURCE_PLATFORM, SOURCE_TENANT}, 'one row per layer that hit'
    assert [h['word'] for h in by_source[SOURCE_PLATFORM]['hits']] == ['平台词']
    assert [h['word'] for h in by_source[SOURCE_TENANT]['hits']] == ['租户词']


async def test_a_single_layer_hit_logs_exactly_one_row(monkeypatch, stub_hit_log):
    """The split must not turn every hit into two rows."""
    monkeypatch.setattr(TENANT_POLICY_PATH, lambda **kwargs: _tenant_policy(custom_words='租户词', action='mask'))
    _stub_platform(monkeypatch, _platform_matcher(words=['平台词'], action='mask'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    await ChatGuardService.check_output_chunk(state, '只有平台词')

    assert len(stub_hit_log.calls) == 1
    assert stub_hit_log.calls[0]['source'] == SOURCE_PLATFORM
