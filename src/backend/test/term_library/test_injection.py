"""F060 T-06: the term block — matching, merging, ordering, capping.

This is the feature's core and it is deliberately a pure-ish function with no
storage of its own: everything that decides what a model is told lives here, so
it can be tested without a request, a conversation or a model.

Four rules are easy to get subtly wrong, so each has its own test rather than
being folded into a happy-path one:

  * **aliases merge into their standard term** (spec AC-21). A user typing the
    shorthand must pull in the full definition, and the block must show BOTH so
    the model can connect the two.
  * **ordering is total and reproducible** (spec AC-23). Truncation decides which
    terms survive, so an unstable order would make the same question answer
    differently between runs.
  * **capping states the remainder** (spec AC-23). Silently dropping terms reads
    as "those are all the terms" when it is not.
  * **no match means no block at all** (spec AC-22) — not an empty section.

The library is stubbed at the DAO boundary (an I/O edge); the matching logic
itself is the subject and is never mocked away.
"""

# ruff: noqa: RUF001 -- the Chinese strings are the terms and questions under test;
# fullwidth punctuation is correct in them.

from unittest.mock import AsyncMock, patch

from bisheng.term_library.domain.services import injection


def _term(term_id: int, standard: str, definition: str, aliases: list[str] | None = None):
    """A row as the DAO returns it."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=term_id,
        standard_term=standard,
        definition=definition,
        aliases=aliases or [],
        department_id=None,
    )


async def _build(query: str, terms: list, monkeypatch=None):
    """Run build_term_block against a stubbed library."""
    with patch.object(
        injection.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=terms)
    ):
        return await injection.build_term_block(tenant_id=1, user_query=query)


# ── AC-20: a hit produces the term and its definition ────────────────────────


async def test_a_hit_puts_the_term_and_its_definition_in_the_block():
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例')]

    block = await _build('请问不良贷款率是多少？', terms)

    assert '不良贷款率' in block
    assert '不良贷款占贷款总额的比例' in block


async def test_a_miss_produces_no_block_at_all():
    """AC-22: not an empty section — an empty expander tells the model a term
    list exists and is empty, which is a different and false claim."""
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例')]

    assert await _build('今天天气怎么样？', terms) == ''


async def test_an_empty_library_produces_no_block():
    assert await _build('不良贷款率是多少？', []) == ''


# ── AC-21: aliases merge into the standard term ──────────────────────────────


async def test_hitting_an_alias_yields_the_standard_terms_definition():
    """The point of aliases: the user's shorthand must reach the real meaning."""
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例', aliases=['不良率', 'NPL'])]

    block = await _build('这个月的不良率如何？', terms)

    assert '不良贷款率' in block
    assert '不良贷款占贷款总额的比例' in block


async def test_the_block_lists_the_alias_that_matched():
    """AC-21's second half. Without the alias shown, the model cannot connect
    what the user typed to the standard term it is being handed."""
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例', aliases=['不良率', 'NPL'])]

    block = await _build('这个月的不良率如何？', terms)

    assert '不良率' in block


async def test_a_term_is_emitted_once_even_when_several_aliases_match():
    """Otherwise a question using two shorthands would repeat the definition."""
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例', aliases=['不良率', 'NPL'])]

    block = await _build('不良率和 NPL 是一回事吗？', terms)

    assert block.count('不良贷款占贷款总额的比例') == 1


async def test_hitting_both_the_standard_term_and_an_alias_emits_it_once():
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例', aliases=['不良率'])]

    block = await _build('不良贷款率与不良率', terms)

    assert block.count('不良贷款占贷款总额的比例') == 1


# ── AC-23: ordering is total and reproducible ────────────────────────────────


async def test_terms_are_ordered_by_where_they_first_appear():
    terms = [
        _term(1, '拨备覆盖率', '拨备与不良贷款的比率'),
        _term(2, '不良贷款率', '不良贷款占贷款总额的比例'),
    ]

    block = await _build('不良贷款率与拨备覆盖率的关系？', terms)

    assert block.index('不良贷款率') < block.index('拨备覆盖率')


async def test_a_longer_standard_term_wins_a_tie_on_position():
    """Two terms starting at the SAME offset, ordered longest-first.

    The reachable tie is a PREFIX pair, not a substring pair: the automaton
    reports the longest match ending at each position, so `不良率` sitting inside
    `不良贷款率` never matches on its own (measured). What does tie is `AB` and
    `ABC` both beginning at offset 0.

    The longer is more specific, so it is kept first when the cap forces a choice
    — and the tie-break has to exist at all, since a partial order would let the
    same question render differently between runs.
    """
    terms = [
        _term(1, 'AB', '短词的定义'),
        _term(2, 'ABC', '长词的定义'),
    ]

    block = await _build('ABC', terms)

    assert block.index('长词的定义') < block.index('短词的定义')


async def test_the_order_is_stable_regardless_of_library_order():
    """The library comes back newest-first, so an implementation that just
    preserved that order would pass a single-run assertion and fail the moment
    rows were inserted differently."""
    forward = [_term(1, '拨备覆盖率', 'A'), _term(2, '不良贷款率', 'B')]
    reversed_lib = list(reversed(forward))

    assert await _build('不良贷款率与拨备覆盖率', forward) == await _build(
        '不良贷款率与拨备覆盖率', reversed_lib
    )


# ── AC-23: the character cap ─────────────────────────────────────────────────


async def test_the_block_is_capped_by_characters(monkeypatch):
    monkeypatch.setattr(injection, 'MAX_BLOCK_CHARS', 120)
    terms = [
        _term(i, f'术语{i}', '这一条释义刚好长到足以把整个区块的字符预算撑爆掉，所以后面的术语不会出现')
        for i in range(1, 11)
    ]

    block = await _build('术语1 术语2 术语3 术语4 术语5 术语6 术语7 术语8 术语9 术语10', terms)

    assert len(block) <= 120


async def test_the_block_says_how_many_terms_did_not_fit(monkeypatch):
    """AC-23: the remainder is STATED. Silently dropping them reads as "those
    are all the relevant terms", which is a different and false claim."""
    monkeypatch.setattr(injection, 'MAX_BLOCK_CHARS', 120)
    terms = [
        _term(i, f'术语{i}', '这一条释义刚好长到足以把整个区块的字符预算撑爆掉，所以后面的术语不会出现')
        for i in range(1, 11)
    ]

    block = await _build('术语1 术语2 术语3 术语4 术语5 术语6 术语7 术语8 术语9 术语10', terms)

    assert '未展开' in block, f'the cap dropped terms without saying so:\n{block}'
    assert any(str(n) in block for n in range(1, 10)), 'the count is not stated'


async def test_a_single_oversized_term_does_not_produce_an_unbounded_block(monkeypatch):
    """The cap must hold even when the FIRST term alone exceeds it — the case
    where a naive `while fits` loop appends nothing and returns a bare header."""
    monkeypatch.setattr(injection, 'MAX_BLOCK_CHARS', 60)
    terms = [_term(1, '超长术语', '释义' * 200)]

    block = await _build('超长术语', terms)

    assert len(block) <= 60
    assert '未展开' in block or '超长术语' not in block


# ── AC-28: no blocking, and the library is read once per call ────────────────


async def test_the_same_question_is_matched_once_not_once_per_model_call():
    """The assistant's prompt callable runs on EVERY model invocation, and one
    ReAct turn calls the model once per step. Without the cache a single turn
    would rebuild the automaton and re-scan the question several times.

    Asserted on the MATCHING, not on the library read. The read has to happen
    every call: the cache key contains the library's content digest, so knowing
    whether the cached block is still valid requires reading the library. That is
    deliberate — it is what makes an edit visible on the next turn even when a
    DIFFERENT process made it, which push-invalidation could not promise.
    """
    injection._reset_caches()
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例')]
    builds = []

    original = injection._build_automaton

    def _spy(all_terms):
        builds.append(all_terms)
        return original(all_terms)

    with (
        patch.object(injection.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=terms)),
        patch.object(injection, '_build_automaton', _spy),
    ):
        first = await injection.build_term_block(1, '不良率与不良贷款率')
        second = await injection.build_term_block(1, '不良率与不良贷款率')

    assert first == second
    assert len(builds) == 1, (
        f'the automaton was rebuilt {len(builds)} times for one question -- the '
        'cache is not preventing repeated matching across a turn\'s model calls'
    )


async def test_a_changed_library_is_reflected_immediately():
    """AC-25 / INV-18: an edit takes effect on the next turn. A cache keyed only
    on (tenant, question) would serve the pre-edit block for a repeated
    question, so the library's content is part of the key."""
    injection._reset_caches()
    before = [_term(1, '不良贷款率', '旧释义')]
    after = [_term(1, '不良贷款率', '新释义')]

    with patch.object(injection.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=before)):
        first = await injection.build_term_block(1, '不良贷款率')
    with patch.object(injection.TermLibraryTermDao, 'alist_all', AsyncMock(return_value=after)):
        second = await injection.build_term_block(1, '不良贷款率')

    assert '旧释义' in first
    assert '新释义' in second, 'an edited definition did not take effect'


async def test_a_failing_library_read_degrades_instead_of_raising(monkeypatch):
    """Design §7.4: the term block is an enhancement. A read failure must return
    an empty block and let the conversation continue — never break the turn."""
    injection._reset_caches()
    with patch.object(
        injection.TermLibraryTermDao, 'alist_all', AsyncMock(side_effect=RuntimeError('db down'))
    ):
        block = await injection.build_term_block(1, '不良贷款率')

    assert block == ''


async def test_a_blank_question_matches_nothing():
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例')]

    assert await _build('', terms) == ''
    assert await _build('   ', terms) == ''


# ── case insensitivity, which CJK terms would never reveal ───────────────────


async def test_an_ascii_term_matches_regardless_of_case():
    """The bug this exists for: lowercasing only the QUESTION (the obvious half)
    makes every ASCII term unmatchable, because the automaton's patterns keep
    their original case. Nothing in a CJK-only test set would notice — case is
    moot there — and the symptom reads as "English terms do not work" rather
    than as a case bug.
    """
    terms = [_term(1, 'NPL', '不良贷款率')]

    for query in ('NPL 是多少', 'npl 是多少', 'Npl 是多少'):
        assert '不良贷款率' in await _build(query, terms), f'{query!r} did not match'


async def test_an_ascii_alias_matches_regardless_of_case():
    terms = [_term(1, '不良贷款率', '不良贷款占贷款总额的比例', aliases=['NPL'])]

    assert '不良贷款占贷款总额的比例' in await _build('npl 是多少', terms)


async def test_a_lowercase_term_is_still_reported_in_its_stored_form():
    """Folding is only for MATCHING. What the model is told keeps the library's
    own spelling, or a lowercase-only library would inject lowercase jargon."""
    terms = [_term(1, 'NPL', '不良贷款率')]

    block = await _build('npl', terms)

    assert 'NPL' in block
