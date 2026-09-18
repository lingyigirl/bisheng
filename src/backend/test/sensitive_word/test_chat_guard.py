"""ChatGuardService unit tests — policy resolution, tenant isolation, action
semantics, incremental stream guarding, and best-effort hit logging.

Covers AC-01, AC-02, AC-03, AC-04, AC-07, AC-09, AC-13, AC-16.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from bisheng.sensitive_word.domain.models.hit_log import SensitiveWordHitLogDao
from bisheng.sensitive_word.domain.services.chat_guard import ChatGuardService, GuardedChunk, OutputGuardState

POLICY_DAO_PATH = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'


@pytest.fixture(autouse=True)
def stub_hit_log(monkeypatch):
    """Unit tests never touch a real DB: stub the best-effort log write.

    Tests that assert log contents request this fixture for its recorder; the one
    test exercising the real DAO re-patches it with the original classmethod.
    """
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)
    return recorder


@pytest.fixture(autouse=True)
def stub_platform_layer_off(monkeypatch):
    """Switch the F059 platform layer off for these F056 tests.

    Every test here is about the TENANT layer. The platform layer now runs first on
    both directions (F059), so leaving it live would send each test to a real
    database for the platform policy table. The platform layer has its own suite:
    `test_chat_guard_platform.py`.
    """
    from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult

    async def _no_platform_matcher():
        return None

    async def _no_platform_text(text):
        return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)

    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.build_platform_matcher',
        _no_platform_matcher,
    )
    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.services.chat_guard.PlatformWordService.check_platform_text',
        _no_platform_text,
    )


def _policy(*, enabled=True, words_types=None, custom_words='', auto_reply='命中敏感词', extra_config=None):
    return SimpleNamespace(
        enabled=enabled,
        words_types=words_types or ['custom'],
        custom_words=custom_words,
        auto_reply=auto_reply,
        extra_config=extra_config or {},
    )


class _LogRecorder:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


async def test_input_hit_uses_chat_input_policy_and_logs(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)

    result = await ChatGuardService.check_chat_input(
        tenant_id=1, text='包含敏感词的内容', flow_id='flow-1', chat_id='chat-1', user_id=7
    )

    assert result.enabled is True
    assert {hit.word: hit.count for hit in result.hits} == {'敏感词': 1}
    assert len(recorder.calls) == 1
    assert recorder.calls[0]['tenant_id'] == 1
    assert recorder.calls[0]['business_type'] == 'chat_input'
    assert recorder.calls[0]['direction'] == 'input'
    assert recorder.calls[0]['action'] == 'block'
    assert recorder.calls[0]['flow_id'] == 'flow-1'
    assert recorder.calls[0]['hits'] == [{'word': '敏感词', 'count': 1}]


async def test_input_miss_does_not_log(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)

    result = await ChatGuardService.check_chat_input(tenant_id=1, text='正常内容')

    assert result.enabled is True
    assert result.hits == []
    assert recorder.calls == []


async def test_output_guard_disabled_policy_is_passthrough(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(enabled=False, custom_words='敏感词'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert state.enabled is False

    result = await ChatGuardService.check_output_chunk(state, '任意内容')
    assert result == GuardedChunk(safe_text='任意内容')


def test_output_guard_empty_words_are_passthrough(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words=''))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert state.enabled is False


def test_output_action_default_and_validation(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))
    assert ChatGuardService.start_output_guard(tenant_id=1).action == 'mask'

    monkeypatch.setattr(
        POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'})
    )
    assert ChatGuardService.start_output_guard(tenant_id=1).action == 'block'

    monkeypatch.setattr(
        POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'bogus'})
    )
    assert ChatGuardService.start_output_guard(tenant_id=1).action == 'mask'


async def test_output_block_truncates_at_first_hit(monkeypatch):
    monkeypatch.setattr(
        POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'})
    )
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)

    state = ChatGuardService.start_output_guard(tenant_id=1, flow_id='flow-1')
    result = await ChatGuardService.check_output_chunk(state, '这是敏感词的内容')

    assert result.safe_text == '这是'
    assert result.blocked is True
    assert {hit.word: hit.count for hit in result.hits} == {'敏感词': 1}
    assert recorder.calls[0]['business_type'] == 'chat_output'
    assert recorder.calls[0]['direction'] == 'output'
    assert recorder.calls[0]['action'] == 'block'
    assert recorder.calls[0]['flow_id'] == 'flow-1'


async def test_output_block_cross_chunk(monkeypatch):
    monkeypatch.setattr(
        POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词', extra_config={'action': 'block'})
    )

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '这是敏')
    second = await ChatGuardService.check_output_chunk(state, '感词之后')

    assert first.safe_text == '这'
    assert first.blocked is False
    assert second.safe_text == '是'
    assert second.blocked is True
    assert {hit.word: hit.count for hit in second.hits} == {'敏感词': 1}
    # Later chunks are dropped.
    third = await ChatGuardService.check_output_chunk(state, '更多内容')
    assert third.safe_text == ''
    assert third.blocked is True
    assert third.hits == []


async def test_output_mask_basic(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))
    recorder = _LogRecorder()
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', recorder.record)

    state = ChatGuardService.start_output_guard(tenant_id=1)
    result = await ChatGuardService.check_output_chunk(state, '这是敏感词的内容')

    assert result.safe_text == '这是***的内容'
    assert result.blocked is False
    assert {hit.word: hit.count for hit in result.hits} == {'敏感词': 1}
    assert recorder.calls[0]['action'] == 'mask'


async def test_output_mask_cross_chunk(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感词'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '这是敏')
    second = await ChatGuardService.check_output_chunk(state, '感词的内容')

    assert first.safe_text == '这'
    assert first.hits == []
    assert second.safe_text == '是***的内容'
    assert {hit.word: hit.count for hit in second.hits} == {'敏感词': 1}


async def test_output_mask_straddling_word_held_back_and_not_double_counted(monkeypatch):
    # 敏感 is itself a word and also the prefix of 敏感词 — the completed word must be
    # held back until the longer pattern is ruled out, and counted once at its own
    # completion (same totals as F036 whole-text find_all: {敏感: 1, 敏感词: 1}).
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='敏感\n敏感词'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, '敏感')
    second = await ChatGuardService.check_output_chunk(state, '词')

    assert first.safe_text == ''
    assert {hit.word: hit.count for hit in first.hits} == {'敏感': 1}
    assert second.safe_text == '***'
    assert {hit.word: hit.count for hit in second.hits} == {'敏感词': 1}


async def test_output_mask_case_insensitive_by_default(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='abc'))

    state = ChatGuardService.start_output_guard(tenant_id=1)
    first = await ChatGuardService.check_output_chunk(state, 'AB')
    second = await ChatGuardService.check_output_chunk(state, 'C')

    assert first.safe_text == ''
    assert second.safe_text == '***'


async def test_output_guard_respects_case_sensitive_config(monkeypatch):
    monkeypatch.setattr(
        POLICY_DAO_PATH,
        lambda **kwargs: _policy(custom_words='Abc', extra_config={'case_sensitive': True}),
    )

    state = ChatGuardService.start_output_guard(tenant_id=1)
    result = await ChatGuardService.check_output_chunk(state, 'abc')

    assert result.safe_text == 'abc'
    assert result.hits == []


async def test_tenant_policies_are_isolated(monkeypatch):
    def _tenant_policy(*, tenant_id, **kwargs):
        if tenant_id == 1:
            return _policy(custom_words='租户一词')
        return _policy(custom_words='租户二词')

    monkeypatch.setattr(POLICY_DAO_PATH, _tenant_policy)

    result_one = await ChatGuardService.check_output_chunk(
        ChatGuardService.start_output_guard(tenant_id=1), '这是租户一词的内容'
    )
    result_two = await ChatGuardService.check_output_chunk(
        ChatGuardService.start_output_guard(tenant_id=2), '这是租户一词的内容'
    )

    assert result_one.safe_text == '这是****的内容'
    assert result_two.safe_text == '这是租户一词的内容'


async def test_policy_change_is_effective_immediately(monkeypatch):
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='旧词'))
    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert (await ChatGuardService.check_output_chunk(state, '旧词')).safe_text == '**'

    # Policy row changes; a new guard session must use the new words (the automaton
    # cache is keyed by word digest, and clear_cache on upsert keeps it fresh — AC-03).
    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='新词'))
    state = ChatGuardService.start_output_guard(tenant_id=1)
    assert (await ChatGuardService.check_output_chunk(state, '新词')).safe_text == '**'
    assert (await ChatGuardService.check_output_chunk(state, '旧词')).safe_text == '旧词'


async def test_hit_log_dao_swallows_db_errors(monkeypatch):
    async def _broken_session():
        raise SQLAlchemyError('db down')

    # Undo the autouse stub: this test exercises the real DAO with a broken session.
    original = SensitiveWordHitLogDao.__dict__['acreate_async_best_effort']
    monkeypatch.setattr(SensitiveWordHitLogDao, 'acreate_async_best_effort', original)
    monkeypatch.setattr('bisheng.sensitive_word.domain.models.hit_log.get_async_db_session', _broken_session)

    # Must not raise — log writes are best-effort (AC-16).
    result = await SensitiveWordHitLogDao.acreate_async_best_effort(
        tenant_id=1, business_type='chat_input', direction='input', action='block', hits=[{'word': 'x', 'count': 1}]
    )
    assert result is None


async def test_disabled_state_passthrough():
    state = OutputGuardState(enabled=False)
    # Disabled states ignore the automaton entirely.
    assert await ChatGuardService.check_output_chunk(state, 'x') == GuardedChunk(safe_text='x')


# --- H7: the policy is looked up per DIRECTION, and that must be anchored -----


class _PolicyQueryRecorder:
    """Records the `business_type` each policy lookup asks for.

    Added because of a measurement: swapping `CHAT_OUTPUT` for `CHAT_INPUT` in
    `start_output_guard` left 214 tests green. Every stub in this file was
    `lambda **kwargs: _policy(...)`, which swallows `business_type` without
    looking at it, so a guard that read the WRONG direction's word list was
    indistinguishable from a correct one. Spec AC-09 requires the output
    direction to use its own independently configured list; nothing asserted it.
    """

    def __init__(self, policy):
        self.policy = policy
        self.business_types = []

    def __call__(self, **kwargs):
        self.business_types.append(kwargs.get('business_type'))
        return self.policy


async def test_output_guard_queries_the_output_direction(monkeypatch):
    """The anchor for AC-09. Fails if the output path reads the input policy."""
    from bisheng.sensitive_word.domain.schemas import SensitiveWordBusinessType

    recorder = _PolicyQueryRecorder(_policy(custom_words='敏感词', extra_config={'action': 'mask'}))
    monkeypatch.setattr(POLICY_DAO_PATH, recorder)

    state = ChatGuardService.start_output_guard(tenant_id=1, flow_id='f', chat_id='c', user_id=1)

    assert state.enabled is True, 'guard did not activate; the lookup went wrong'
    assert recorder.business_types, 'no policy lookup happened at all'
    assert all(bt == SensitiveWordBusinessType.CHAT_OUTPUT for bt in recorder.business_types), (
        f'output guard queried {recorder.business_types}; it must use CHAT_OUTPUT '
        '(spec AC-09 — the two directions are configured independently)'
    )


async def test_input_guard_queries_the_input_direction(monkeypatch):
    """The mirror anchor: a symmetric mistake on the input side."""
    from bisheng.sensitive_word.domain.schemas import SensitiveWordBusinessType

    recorder = _PolicyQueryRecorder(_policy(custom_words='敏感词'))
    monkeypatch.setattr(POLICY_DAO_PATH, recorder)

    await ChatGuardService.check_chat_input(tenant_id=1, text='含敏感词的内容')

    assert recorder.business_types, 'no policy lookup happened at all'
    assert all(bt == SensitiveWordBusinessType.CHAT_INPUT for bt in recorder.business_types), (
        f'input guard queried {recorder.business_types}; it must use CHAT_INPUT'
    )


async def test_the_two_directions_use_different_word_lists(monkeypatch):
    """The user-visible consequence, asserted end to end rather than on the
    lookup argument: a tenant whose INPUT list contains a word its OUTPUT list
    does not must NOT have that word masked in output.

    Written this way because the lookup-argument assertions above would still
    pass if some later layer re-resolved the policy; this one would not.
    """
    from bisheng.sensitive_word.domain.schemas import SensitiveWordBusinessType

    def _policy_for(business_type):
        # Only the INPUT direction carries '仅输入词'.
        words = '仅输入词' if business_type == SensitiveWordBusinessType.CHAT_INPUT else '仅输出词'
        return _policy(custom_words=words, extra_config={'action': 'mask'})

    monkeypatch.setattr(POLICY_DAO_PATH, lambda **kwargs: _policy_for(kwargs.get('business_type')))

    state = ChatGuardService.start_output_guard(tenant_id=1, flow_id='f', chat_id='c', user_id=1)
    result = await ChatGuardService.check_output_chunk(state, '这句含仅输入词')

    assert '仅输入词' in result.safe_text, (
        'an input-only word was masked on the output path — the directions are '
        'sharing one word list (spec AC-09)'
    )
