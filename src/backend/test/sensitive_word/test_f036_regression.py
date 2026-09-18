"""AC-18 (F056): the two pre-existing F036 sensitive-word scenarios keep working.

F056 rebuilt the shared infrastructure the F036 scenarios sit on —
``check_texts`` was re-routed through a new ``build_matcher`` facade and
``find_all`` was rewritten to delegate to the incremental ``find_all_matches``.
Both are F036's own entry points, so a regression here would silently change
behaviour for knowledge-file parsing and subscription articles, which is
exactly what spec AC-18 forbids.

The feature's two covered paths are tested elsewhere
(``test_sensitive_word_policy_service.py`` covers knowledge-file parsing). What
is asserted here is the part that had no coverage:

  * the ``check_text`` / ``check_texts`` contract as F036 callers consume it —
    result per input text, ``enabled``/``auto_reply`` semantics on the
    not-effective path, and the ``max_hits`` cap;
  * the subscription-article path (``ChannelService``), which had no test;
  * ``find_all`` equivalence, since the rewrite could have changed counting.

These are behaviour assertions on purpose: they must fail if the refactor
changed what callers observe, regardless of how the internals are arranged.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bisheng.channel.domain.services.channel_service import ChannelService
from bisheng.sensitive_word.domain.schemas import SensitiveWordBusinessType
from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton
from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import (
    DEFAULT_AUTO_REPLY,
    SensitiveWordPolicyService,
)

POLICY_DAO = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'
ARTICLE_TYPE = SensitiveWordBusinessType.CHANNEL_ARTICLE
PARSE_TYPE = SensitiveWordBusinessType.KNOWLEDGE_SPACE_FILE_PARSE


def _policy(*, enabled=True, words_types=None, custom_words='', auto_reply=None, extra_config=None):
    return SimpleNamespace(
        enabled=enabled,
        words_types=words_types or ['custom'],
        custom_words=custom_words,
        auto_reply=auto_reply,
        extra_config=extra_config or {},
    )


def _article(**fields):
    return SimpleNamespace(**fields)


# --- check_texts: one result per input, in order -----------------------------


def test_check_texts_returns_one_result_per_text_in_order(monkeypatch):
    """F036 callers zip inputs with results (ChannelService does exactly this),
    so a length or ordering change would silently mis-assign every verdict."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='违规词'))

    results = SensitiveWordPolicyService.check_texts(
        tenant_id=1,
        business_type=ARTICLE_TYPE,
        texts=['干净的标题', '这里含违规词', '又是违规词和违规词'],
    )

    assert len(results) == 3
    assert [r.enabled for r in results] == [True, True, True]
    assert [len(r.hits) for r in results] == [0, 1, 1]
    assert [r.hits[0].count for r in results if r.hits] == [1, 2]


# --- the not-effective path keeps its old shape ------------------------------


def test_not_effective_still_yields_one_disabled_result_per_text(monkeypatch):
    """Before F056 this returned a list of `enabled=False` results, one per
    input; the refactor introduced an early `None` from build_matcher that has
    to be expanded back. Callers index into the list, so dropping it would
    raise IndexError rather than merely change a flag."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(enabled=False))

    results = SensitiveWordPolicyService.check_texts(
        tenant_id=1,
        business_type=ARTICLE_TYPE,
        texts=['a', 'b', 'c'],
    )

    assert len(results) == 3
    for result in results:
        assert result.enabled is False
        assert result.hits == []
        assert result.auto_reply == DEFAULT_AUTO_REPLY


def test_empty_word_list_is_not_effective(monkeypatch):
    """An enabled policy with no words must behave like a disabled one."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(enabled=True, custom_words=''))

    result = SensitiveWordPolicyService.check_text(tenant_id=1, business_type=PARSE_TYPE, text='任意内容')

    assert result.enabled is False
    assert result.hits == []


# --- auto_reply semantics ----------------------------------------------------


def test_auto_reply_falls_back_to_the_shared_default(monkeypatch):
    """F036 relied on the module-level default when the policy set none."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='违规词', auto_reply=None))

    result = SensitiveWordPolicyService.check_text(tenant_id=1, business_type=ARTICLE_TYPE, text='违规词')

    assert result.auto_reply == DEFAULT_AUTO_REPLY


def test_auto_reply_is_the_policy_text_when_set(monkeypatch):
    monkeypatch.setattr(
        POLICY_DAO, lambda **kwargs: _policy(custom_words='违规词', auto_reply='自定义话术')
    )

    result = SensitiveWordPolicyService.check_text(tenant_id=1, business_type=ARTICLE_TYPE, text='违规词')

    assert result.auto_reply == '自定义话术'


# --- max_hits cap ------------------------------------------------------------


def test_max_hits_caps_the_reported_words(monkeypatch):
    """`max_hits` is an extra_config knob F036 exposed; it must still apply now
    that the value is read off the matcher instead of the policy row."""
    monkeypatch.setattr(
        POLICY_DAO,
        lambda **kwargs: _policy(custom_words='甲词\n乙词\n丙词', extra_config={'max_hits': 2}),
    )

    result = SensitiveWordPolicyService.check_text(
        tenant_id=1, business_type=ARTICLE_TYPE, text='甲词乙词丙词'
    )

    assert len(result.hits) == 2


def test_absent_max_hits_reports_every_word(monkeypatch):
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='甲词\n乙词\n丙词'))

    result = SensitiveWordPolicyService.check_text(
        tenant_id=1, business_type=ARTICLE_TYPE, text='甲词乙词丙词'
    )

    assert {hit.word for hit in result.hits} == {'甲词', '乙词', '丙词'}


# --- case sensitivity --------------------------------------------------------


def test_case_insensitive_by_default_and_reports_the_normalized_word(monkeypatch):
    """The reported word is the word list's own spelling, not the matched text —
    F036 relied on this so the review shows the configured term."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='Forbidden'))

    result = SensitiveWordPolicyService.check_text(
        tenant_id=1, business_type=ARTICLE_TYPE, text='this is FORBIDDEN content'
    )

    assert [hit.word for hit in result.hits] == ['Forbidden']


def test_case_sensitive_when_configured(monkeypatch):
    monkeypatch.setattr(
        POLICY_DAO,
        lambda **kwargs: _policy(custom_words='Forbidden', extra_config={'case_sensitive': True}),
    )

    lower = SensitiveWordPolicyService.check_text(tenant_id=1, business_type=ARTICLE_TYPE, text='forbidden')
    exact = SensitiveWordPolicyService.check_text(tenant_id=1, business_type=ARTICLE_TYPE, text='Forbidden')

    assert lower.hits == []
    assert len(exact.hits) == 1


# --- the subscription-article path (previously untested) ---------------------


@pytest.mark.asyncio
async def test_article_review_marks_a_hit_and_reports_it(monkeypatch):
    """ChannelService.apply_article_sensitive_reviews is F036's other entry
    point. It had no test at all, so nothing would have caught a change in how
    it maps results onto articles."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='违规词', auto_reply='不可查看'))

    with patch.object(
        ChannelService, '_can_view_sensitive_article', new=AsyncMock(return_value=False)
    ), patch.object(ChannelService, '_current_tenant_id', return_value=1):
        articles = [_article(title='正常标题', content='正常正文'), _article(title='含违规词', content='正文')]
        await ChannelService.apply_article_sensitive_reviews(articles, login_user=SimpleNamespace())

    clean, dirty = articles
    assert clean.sensitive_review.violated is False
    assert dirty.sensitive_review.violated is True
    assert [hit.word for hit in dirty.sensitive_review.hits] == ['违规词']
    assert dirty.sensitive_review.can_view is False
    assert dirty.sensitive_review.auto_reply == '不可查看'


@pytest.mark.asyncio
async def test_article_review_reads_title_and_body_together(monkeypatch):
    """The review text is title + body; a hit in either must be found."""
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: _policy(custom_words='违规词'))

    with patch.object(
        ChannelService, '_can_view_sensitive_article', new=AsyncMock(return_value=True)
    ), patch.object(ChannelService, '_current_tenant_id', return_value=1):
        in_title = [_article(title='违规词标题', content='正文')]
        in_body = [_article(title='标题', content='正文含违规词')]
        await ChannelService.apply_article_sensitive_reviews(in_title + in_body, login_user=SimpleNamespace())

    assert in_title[0].sensitive_review.violated is True
    assert in_body[0].sensitive_review.violated is True


@pytest.mark.asyncio
async def test_empty_article_list_is_a_no_op(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(POLICY_DAO, lambda **kwargs: called())

    await ChannelService.apply_article_sensitive_reviews([], login_user=SimpleNamespace())

    called.assert_not_called()


# --- find_all equivalence ----------------------------------------------------


def test_find_all_counts_the_same_as_a_single_incremental_pass():
    """`find_all` was rewritten to delegate to `find_all_matches`. Both are live
    (F036 uses find_all; the chat guard uses the incremental form), so a
    divergence in counting would show up as inconsistent verdicts between the
    two scenarios."""
    words = ['敏感词', '敏感', 'ab', 'abc']
    text = 'x敏感词yabc敏感zab敏感词'

    automaton = ACAutomaton(words)
    counter = automaton.find_all(text)
    _, matches, _ = automaton.find_all_matches(None, text)

    assert dict(counter) == {word: sum(1 for _, w in matches if w == word) for _, word in matches}


def test_find_all_finds_overlapping_patterns():
    """Both a short word and the longer word containing it are reported; F036's
    verdicts depend on this (a hit list that dropped the short word would
    under-report)."""
    automaton = ACAutomaton(['敏感', '敏感词'])

    assert dict(automaton.find_all('敏感词')) == {'敏感': 1, '敏感词': 1}


def test_find_all_on_an_empty_automaton_and_empty_text():
    assert dict(ACAutomaton([]).find_all('任意文本')) == {}
    assert dict(ACAutomaton(['敏感词']).find_all('')) == {}
