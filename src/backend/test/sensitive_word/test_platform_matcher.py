"""T008 (F059): platform matcher construction + its dedicated automaton cache.

Covers the layer's own on/off semantics (spec AC-14) and the immediate-effect
contract (spec AC-07), plus the properties that make the cache safe to share
across every tenant.
"""

import pytest

from bisheng.sensitive_word.domain.models.platform_policy import (
    DEFAULT_ACTION,
    PlatformSensitiveWordPolicy,
)
from bisheng.sensitive_word.domain.models.platform_word import PlatformSensitiveWordDao
from bisheng.sensitive_word.domain.services.platform_word_service import PlatformWordService

POLICY_DAO_PATH = 'bisheng.sensitive_word.domain.models.platform_policy.PlatformSensitiveWordPolicyDao.aget_policy'
WORDS_DAO_PATH = 'bisheng.sensitive_word.domain.models.platform_word.PlatformSensitiveWordDao.alist_all_words'


def _policy(*, enabled=True, action='mask', auto_reply='平台话术'):
    return PlatformSensitiveWordPolicy(
        id=1, enabled=enabled, action=action, auto_reply=auto_reply
    )


def _stub_policy(monkeypatch, policy):
    """Patch the policy DAO with an async stub (the real method is a coroutine)."""

    async def _aget():
        return policy

    monkeypatch.setattr(POLICY_DAO_PATH, _aget)


def _stub_words(monkeypatch, words):
    """Patch the word DAO with an async stub, reads closing over `words`."""

    async def _alist():
        return words

    monkeypatch.setattr(WORDS_DAO_PATH, _alist)


@pytest.fixture(autouse=True)
def clear_platform_cache():
    """The automaton cache is process-global; keep tests independent."""
    PlatformWordService.clear_cache()
    yield
    PlatformWordService.clear_cache()


async def test_disabled_policy_yields_no_matcher(monkeypatch):
    """Spec AC-14: the layer does nothing at all when switched off."""
    _stub_policy(monkeypatch, _policy(enabled=False))
    _stub_words(monkeypatch, ['敏感词'])

    assert await PlatformWordService.build_platform_matcher() is None


async def test_enabled_policy_with_empty_library_yields_no_matcher(monkeypatch):
    """AC-14 again, reached by the other route: an empty library detects nothing.

    This is the state right after the feature ships and before any import, so it
    must be as inert as "disabled" rather than, say, raising.
    """
    _stub_policy(monkeypatch, _policy(enabled=True))
    _stub_words(monkeypatch, [])

    assert await PlatformWordService.build_platform_matcher() is None


async def test_enabled_policy_with_words_yields_a_working_matcher(monkeypatch):
    _stub_policy(monkeypatch, _policy(action='block', auto_reply='命中'))
    _stub_words(monkeypatch, ['敏感词'])

    matcher = await PlatformWordService.build_platform_matcher()

    assert matcher is not None
    assert matcher.auto_reply == '命中'
    assert matcher.extra_config['action'] == 'block'
    assert matcher.automaton.find_all('这是一段敏感词内容')


async def test_check_platform_text_reports_hits_and_reply(monkeypatch):
    _stub_policy(monkeypatch, _policy(auto_reply='平台话术'))
    _stub_words(monkeypatch, ['敏感词', '违规'])

    result = await PlatformWordService.check_platform_text('这里敏感词和违规都有，敏感词两次')  # noqa: RUF001

    assert result.enabled is True
    assert result.auto_reply == '平台话术'
    assert {hit.word: hit.count for hit in result.hits} == {'敏感词': 2, '违规': 1}


async def test_check_platform_text_is_inert_when_disabled(monkeypatch):
    _stub_policy(monkeypatch, _policy(enabled=False))
    _stub_words(monkeypatch, ['敏感词'])

    result = await PlatformWordService.check_platform_text('这里敏感词')

    assert result.enabled is False
    assert result.hits == []


async def test_matching_is_case_insensitive(monkeypatch):
    _stub_policy(monkeypatch, _policy())
    _stub_words(monkeypatch, ['Alpha'])

    result = await PlatformWordService.check_platform_text('contains ALPHA here')

    assert {hit.word for hit in result.hits} == {'Alpha'}


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


async def test_automaton_is_reused_when_the_word_set_is_unchanged(monkeypatch):
    """A no-op re-import must not rebuild a potentially 10k-word automaton."""
    _stub_policy(monkeypatch, _policy())
    _stub_words(monkeypatch, ['a', 'b'])

    first = await PlatformWordService.build_platform_matcher()
    second = await PlatformWordService.build_platform_matcher()

    assert first.automaton is second.automaton


async def test_changing_the_words_rebuilds_the_automaton(monkeypatch):
    """Spec AC-07: a library change takes effect on the next scan."""
    _stub_policy(monkeypatch, _policy())
    _stub_words(monkeypatch, ['旧词'])

    before = await PlatformWordService.build_platform_matcher()
    assert before.automaton.find_all('旧词')  # matches the OLD set
    assert not before.automaton.find_all('新词')

    # Simulate an import: the DAO now returns a different set, and the write
    # path called clear_cache() (covered separately below).
    _stub_words(monkeypatch, ['新词'])
    PlatformWordService.clear_cache()

    after = await PlatformWordService.build_platform_matcher()
    assert after is not before
    assert after.automaton.find_all('新词')  # matches the NEW set
    assert not after.automaton.find_all('旧词')


async def test_cache_holds_only_the_current_word_set(monkeypatch):
    """The cache is bounded — an import storm must not accumulate automatons."""
    _stub_policy(monkeypatch, _policy())

    for words in (['a'], ['b'], ['c']):
        _stub_words(monkeypatch, words)
        PlatformWordService.clear_cache()
        await PlatformWordService.build_platform_matcher()

    assert len(PlatformWordService._platform_automaton_cache) == 1


def test_platform_cache_is_separate_from_the_tenant_cache():
    """The platform library is global: sharing F056's tenant-keyed cache would
    store one identical automaton per tenant and invalidate N copies per import.
    """
    from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import (
        SensitiveWordPolicyService,
    )

    assert PlatformWordService._platform_automaton_cache is not SensitiveWordPolicyService._automaton_cache


# ---------------------------------------------------------------------------
# Write paths invalidate the cache (spec AC-07)
# ---------------------------------------------------------------------------


async def test_add_word_clears_the_cache(monkeypatch):
    async def _no_existing(word):
        return False

    async def _create(word, source='manual', created_by=None):
        return None

    monkeypatch.setattr(PlatformSensitiveWordDao, 'aexists', staticmethod(_no_existing))
    monkeypatch.setattr(PlatformSensitiveWordDao, 'acreate', staticmethod(_create))

    PlatformWordService._platform_automaton_cache['stale'] = (None, {})
    assert await PlatformWordService.add_word('新词', operator_id=1) is True
    assert PlatformWordService._platform_automaton_cache == {}


async def test_add_word_rejects_blank_and_duplicate(monkeypatch):
    async def _exists(word):
        return True

    monkeypatch.setattr(PlatformSensitiveWordDao, 'aexists', staticmethod(_exists))

    assert await PlatformWordService.add_word('   ', operator_id=1) is False
    assert await PlatformWordService.add_word('已有词', operator_id=1) is False


async def test_delete_word_clears_the_cache(monkeypatch):
    async def _delete(word_id):
        return True

    monkeypatch.setattr(PlatformSensitiveWordDao, 'adelete_by_id', staticmethod(_delete))

    PlatformWordService._platform_automaton_cache['stale'] = (None, {})
    assert await PlatformWordService.delete_word(1) is True
    assert PlatformWordService._platform_automaton_cache == {}


async def test_policy_update_clears_the_cache(monkeypatch):
    async def _upsert(**kwargs):
        return _policy(**{k: v for k, v in kwargs.items() if k in ('enabled', 'action', 'auto_reply')})

    monkeypatch.setattr(
        'bisheng.sensitive_word.domain.models.platform_policy.PlatformSensitiveWordPolicyDao.aupsert',
        _upsert,
    )

    PlatformWordService._platform_automaton_cache['stale'] = (None, {})
    await PlatformWordService.aupsert_policy(enabled=True, action='block', auto_reply='x', operator_id=1)
    assert PlatformWordService._platform_automaton_cache == {}


def test_default_action_is_mask():
    """The documented default: mask is the less destructive of the two actions."""
    assert DEFAULT_ACTION == 'mask'


async def test_empty_reply_falls_back_to_the_requirement_default(monkeypatch):
    """A cleared reply must NOT silently disable platform enforcement.

    The reply is what the guard returns to block a round, and callers decide to
    block on its TRUTHINESS (`api/services/guardrail.py`: `if blocked_reply:
    return result.auto_reply`; same at the ws and OpenAI-endpoint call sites). An
    empty string is falsy, so an empty reply makes every platform hit fall
    through to the model — and because `check_chat_input` short-circuits on a
    platform hit before consulting the tenant layer, a message hitting BOTH
    libraries would be blocked by NEITHER.

    The endpoint displays the requirement's wording for an unset reply
    (`auto_reply_is_default: true`), so the admin page looks correctly
    configured while enforcement is off. That is the dangerous part: the
    misconfiguration is invisible.

    The tenant path has always applied this fallback (both on write and on
    matcher construction); the platform path must match it.
    """
    from bisheng.sensitive_word.domain.models.platform_policy import DEFAULT_AUTO_REPLY

    _stub_policy(monkeypatch, _policy(enabled=True, action='block', auto_reply=''))
    _stub_words(monkeypatch, ['违禁词'])

    matcher = await PlatformWordService.build_platform_matcher()

    assert matcher is not None
    assert matcher.auto_reply == DEFAULT_AUTO_REPLY
    assert matcher.auto_reply, 'a falsy reply would make the guard fall through'


async def test_a_custom_reply_is_kept_as_is(monkeypatch):
    """The fallback must not overwrite a reply the admin actually set."""
    _stub_policy(monkeypatch, _policy(enabled=True, auto_reply='自定义话术'))
    _stub_words(monkeypatch, ['违禁词'])

    matcher = await PlatformWordService.build_platform_matcher()

    assert matcher.auto_reply == '自定义话术'
