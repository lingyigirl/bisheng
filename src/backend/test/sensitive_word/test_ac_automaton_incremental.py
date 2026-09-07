"""Incremental (stateful) AC automaton scanning for streamed output guarding.

The automaton API under test is ``find_all_matches(state, text, capture_at)``:
resumes scanning from ``state`` (None = fresh start) and returns
``(state_after, matches, captured_state)`` where matches are ``(end_position, word)``
pairs in scan order and ``end_position`` is a global character index accumulated
across calls through the state.
"""

import random
from collections import Counter

from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton


def test_cross_chunk_word_is_detected():
    auto = ACAutomaton(['敏感词'])

    state, matches, _ = auto.find_all_matches(None, '敏')
    assert matches == []
    state, matches, _ = auto.find_all_matches(state, '感')
    assert matches == []
    _, matches, _ = auto.find_all_matches(state, '词')
    assert matches == [(2, '敏感词')]


def test_word_split_mid_chunk():
    auto = ACAutomaton(['敏感词'])

    state, matches, _ = auto.find_all_matches(None, '这是敏')
    assert matches == []
    _, matches, _ = auto.find_all_matches(state, '感词之后的文本')
    # 敏感词 spans 这是敏 感词 (positions 1-4), ending at 4.
    assert matches == [(4, '敏感词')]


def test_multiple_words_across_chunks():
    auto = ACAutomaton(['ab', 'bcd'])

    state, matches, _ = auto.find_all_matches(None, 'a')
    assert matches == []
    _, matches, _ = auto.find_all_matches(state, 'bcd')
    assert matches == [(1, 'ab'), (3, 'bcd')]


def test_overlapping_words_both_reported():
    auto = ACAutomaton(['敏感', '敏感词'])

    _, matches, _ = auto.find_all_matches(None, '敏感词')
    assert matches == [(1, '敏感'), (2, '敏感词')]


def test_repeated_hits_are_reported_per_occurrence():
    auto = ACAutomaton(['禁用词'])

    _, matches, _ = auto.find_all_matches(None, '禁用词和禁用词')
    assert matches == [(2, '禁用词'), (6, '禁用词')]


def test_empty_text_keeps_state_identity():
    auto = ACAutomaton(['敏感词'])

    state, _, _ = auto.find_all_matches(None, '敏')
    state2, matches2, _ = auto.find_all_matches(state, '')
    assert matches2 == []
    assert state2.node is state.node
    assert state2.next_pos == state.next_pos


def test_positions_accumulate_across_chunks():
    auto = ACAutomaton(['abc'])

    state, _, _ = auto.find_all_matches(None, 'ab')
    _, matches, _ = auto.find_all_matches(state, 'c')
    assert matches == [(2, 'abc')]
    # A fresh session restarts from position 0.
    _, matches, _ = auto.find_all_matches(None, 'abc')
    assert matches == [(2, 'abc')]


def test_independent_sessions_do_not_interfere():
    auto = ACAutomaton(['abc'])

    _, matches1, _ = auto.find_all_matches(None, 'ab')
    assert matches1 == []
    state2, matches2, _ = auto.find_all_matches(None, 'xab')
    assert matches2 == []
    _, matches2, _ = auto.find_all_matches(state2, 'c')
    assert matches2 == [(3, 'abc')]
    # The first session must still complete on its own.
    state1, _, _ = auto.find_all_matches(None, 'ab')
    _, matches1, _ = auto.find_all_matches(state1, 'c')
    assert matches1 == [(2, 'abc')]


def test_capture_at_returns_state_after_given_position():
    auto = ACAutomaton(['敏感词'])

    state_after_all, _, captured = auto.find_all_matches(None, '敏感词', capture_at=1)
    # The state after consuming exactly one char is not the final state.
    assert captured is not None
    assert captured.node is not state_after_all.node
    # Captured state must resume to the same matches as a fresh scan of the suffix.
    _, matches, _ = auto.find_all_matches(captured, '词')
    assert matches == [(2, '敏感词')]
    # Roundtrip: capturing the state before any char equals the start state.
    start_state, _, _ = auto.find_all_matches(None, 'x')
    _, _, captured2 = auto.find_all_matches(None, 'x', capture_at=0)
    assert captured2 is not None
    assert captured2.node is start_state.node
    assert captured2.next_pos == 1


def test_capture_before_scan_base_is_none():
    auto = ACAutomaton(['abc'])

    prior, _, _ = auto.find_all_matches(None, 'xxxxx')  # next_pos = 5
    _, _, captured = auto.find_all_matches(prior, 'abc', capture_at=3)
    assert captured is None


def test_random_chunking_consistency_with_find_all():
    auto = ACAutomaton(['alpha', 'beta', 'gamma', 'alphabet', 'bet'])
    text = 'alphabet soup with beta and gamma, no delta, alphabet again'
    rng = random.Random(42)

    for _ in range(100):
        points = sorted(rng.sample(range(len(text) + 1), rng.randint(1, 6)))
        chunks = [text[i:j] for i, j in zip([0, *points], [*points, len(text)], strict=True)]
        counter: Counter[str] = Counter()
        state = None
        for chunk in chunks:
            state, matches, _ = auto.find_all_matches(state, chunk)
            for _, word in matches:
                counter[word] += 1
        assert dict(counter) == dict(auto.find_all(text))
