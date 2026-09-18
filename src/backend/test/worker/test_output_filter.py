"""Chat history must store the FILTERED answer, not the model's raw output.

The websocket guard runs in the API process; the history row is written by the
worker. So the guard's rewrite never reached the database, and a user who read a
masked answer could scroll back and read the original. These tests cover the
worker-side filter that closes that gap.

Scope note, asserted below rather than left implicit: this covers the TENANT
policy layer only. The platform-wide library (F059) is checked by an async
service a sync worker path cannot call; answers it blocks still reach history
in full.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bisheng.worker.workflow.output_filter import (
    filter_answer_text,
    filter_chat_response_message,
)

POLICY_DAO_PATH = 'bisheng.sensitive_word.domain.models.sensitive_word_policy.SensitiveWordPolicyDao.get_policy'


def _policy(*, enabled=True, custom_words='敏感词', auto_reply='命中敏感词', action='mask'):
    return SimpleNamespace(
        enabled=enabled,
        words_types=['custom'],
        custom_words=custom_words,
        auto_reply=auto_reply,
        extra_config={'action': action},
    )


# --- the two actions ---------------------------------------------------------


def test_block_stores_the_prefix_plus_close_out_phrase():
    """A blocked answer is stored as exactly what the user read: the safe prefix
    and the close-out phrase. The phrase is the point -- it tells the reader why
    the answer stopped, which a silently dropped message would not."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='block')):
        assert filter_answer_text('这是敏感词内容', tenant_id=1) == '这是命中敏感词'


def test_mask_stores_equal_length_asterisks():
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='mask')):
        assert filter_answer_text('这是敏感词内容', tenant_id=1) == '这是***内容'


def test_block_cuts_at_the_EARLIEST_hit():
    """Everything from the first hit onward goes, not just the hit itself --
    matching the streamed guard, and keeping any second occurrence out too."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='block', custom_words='甲词\n乙词')):
        assert filter_answer_text('前甲词中乙词后', tenant_id=1) == '前命中敏感词'


def test_mask_replaces_every_occurrence():
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='mask', custom_words='甲')):
        assert filter_answer_text('甲和甲还有甲', tenant_id=1) == '*和*还有*'


# --- the action comes from the MATCHER, and defaulting matters ---------------


def test_an_unknown_action_falls_back_to_mask_not_to_passthrough():
    """`SensitiveWordCheckResult` has no action field; the action lives on the
    matcher's extra_config. Reading it from the wrong place would silently make
    every tenant a mask tenant -- including one configured to block, which is
    the more protective setting."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='nonsense')):
        assert filter_answer_text('这是敏感词内容', tenant_id=1) == '这是***内容'


def test_action_is_not_hardcoded_to_mask():
    """Guards the mistake directly: if the implementation ignored extra_config
    and always masked, a block-configured tenant would store masked text."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action='block')):
        result = filter_answer_text('这是敏感词内容', tenant_id=1)
        assert '*' not in result, f'block policy produced a mask: {result!r}'


# --- not-effective and error paths -------------------------------------------


def test_disabled_policy_stores_the_answer_unchanged():
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(enabled=False)):
        assert filter_answer_text('这是敏感词内容', tenant_id=1) == '这是敏感词内容'


def test_no_hit_stores_the_answer_unchanged():
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='别的词')):
        assert filter_answer_text('这是正常内容', tenant_id=1) == '这是正常内容'


def test_a_policy_lookup_failure_stores_the_answer_rather_than_losing_it():
    """Best-effort on purpose: losing the user's answer is worse than storing one
    the websocket path would have masked."""

    def boom(**kwargs):
        raise RuntimeError('db down')

    with patch(POLICY_DAO_PATH, boom):
        assert filter_answer_text('这是敏感词内容', tenant_id=1) == '这是敏感词内容'


@pytest.mark.parametrize('text', ['', '   '])
def test_empty_text_is_returned_as_is(text):
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy()):
        assert filter_answer_text(text, tenant_id=1) == text


def test_missing_tenant_skips_filtering():
    """No tenant means no policy to apply; guessing one would filter against the
    wrong word list."""
    assert filter_answer_text('这是敏感词内容', tenant_id=None) == '这是敏感词内容'


# --- message shapes are preserved --------------------------------------------


def test_string_message_is_filtered():
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy()):
        assert filter_chat_response_message('这是敏感词内容', 1) == '这是***内容'


def test_dict_message_keeps_its_other_keys():
    """The worker serializes this dict into the stored row and the frontend reads
    fields out of it; rebuilding it would drop them."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy()):
        out = filter_chat_response_message({'msg': '这是敏感词内容', 'unique_id': 'u', 'n': 3}, 1)

    assert out['msg'] == '这是***内容'
    assert out['unique_id'] == 'u' and out['n'] == 3


def test_unexpected_shapes_are_returned_untouched():
    """A shape we do not recognise is passed through rather than guessed at."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy()):
        marker = object()
        assert filter_chat_response_message(marker, 1) is marker
        assert filter_chat_response_message({'msg': 42}, 1) == {'msg': 42}


def test_an_unchanged_message_is_returned_as_the_same_object():
    """Cheap identity check that also documents intent: when nothing matched we
    did not rebuild the message."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(custom_words='别的词')):
        original = {'msg': '正常内容'}
        assert filter_chat_response_message(original, 1) is original


# --- idempotence, because the API-layer guard filters the same object --------


@pytest.mark.parametrize('action', ['mask', 'block'])
def test_filtering_is_idempotent(action):
    """The worker filters the message, then the API-layer guard may filter the
    same object again on its way to the client. A second pass must not change
    anything -- including for `block`, whose output is the close-out phrase and
    must not accumulate a second one."""
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(action=action)):
        once = filter_answer_text('这是敏感词内容', tenant_id=1)
        twice = filter_answer_text(once, tenant_id=1)

    assert once == twice, f'not idempotent for {action}: {once!r} -> {twice!r}'


# --- the known gap, asserted so it cannot be mistaken for covered ------------


def test_platform_layer_hits_are_not_filtered_here():
    """Documents the boundary rather than pretending it is closed.

    The platform library (F059) is checked by an async service this sync path
    cannot call, so a platform-only hit is stored verbatim. If this test starts
    failing because the text IS filtered, the gap has been closed and both this
    test and the module docstring should be updated.
    """
    with patch(POLICY_DAO_PATH, lambda **kwargs: _policy(enabled=False)):
        result = filter_answer_text('平台词命中内容', tenant_id=1)

    assert result == '平台词命中内容', (
        'platform-layer filtering appears to have been added; update the scope '
        'note in bisheng/worker/workflow/output_filter.py'
    )
