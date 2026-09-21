"""Matching the turn's question against the term library, and rendering the block.

This is the whole of what the model is told about a tenant's terminology, and it
is written as a function with no state of its own so that every rule about WHO
sees WHAT can be read in one place:

  * **Which terms** — the current tenant's entire library (contract INV-16),
    read through the DAO's tenant filter.
  * **Which of them matter** — those appearing in this turn's USER QUESTION. Not
    the answer, not the conversation: the question is what the model is about to
    interpret, and prompt budget is spent on what was actually asked.
  * **What the department field does here** — NOTHING. It is display metadata
    (contract INV-17), and this module must never read it. That is the opposite
    of the neighbouring knowledge module, where `department_id` decides
    visibility; the contract explicitly warns the two must not be conflated, and
    `test_invariants.py` asserts this file stays free of it.

Nothing here writes: the block is appended to a system prompt for one turn and
persisted nowhere (contract INV-18). That is also why an edit is visible on the
next turn with no cache invalidation to reason about — see the cache note below.

Failure posture: a library read that fails yields an EMPTY block, never an
exception. The term block is an enhancement to a conversation, and a conversation
that cannot be had because a glossary is unreachable would be a bad trade
(design §7.4).
"""

# ruff: noqa: RUF001 -- the block header and alias template are Chinese text the
# MODEL reads, alongside the term definitions themselves; fullwidth punctuation is
# correct in them, not a typo. Same rationale as the tool descriptions.

from __future__ import annotations

import hashlib

from loguru import logger

from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton
from bisheng.term_library.domain.models.dao import TermLibraryTermDao
from bisheng.term_library.domain.models.term import TermLibraryTerm

#: How many characters of rendered block we are willing to add to a system
#: prompt for one turn. A tunable constant rather than a setting: it exists to
#: bound the tail case, and design §9 flags the final value as an
#: implementation-time decision to be settled against real answers.
MAX_BLOCK_CHARS = 2000

#: Header the model sees above the entries. Chinese, like the tool descriptions
#: and citation markers elsewhere in the conversation path — this text goes to
#: the model, not to a user interface.
_BLOCK_HEADER = '【本轮相关术语】'

#: Aliases are rendered with a separator the model reads as "also called".
_ALIAS_TEMPLATE = '（别名：{aliases}）'


def _library_digest(terms: list[TermLibraryTerm]) -> str:
    """A content address for the library, so the cache cannot outlive an edit.

    Hashes exactly what affects the rendered block — standard term, definition
    and aliases — and nothing else. Including `id` or timestamps would defeat the
    purpose: a no-op save would invalidate every entry for no reason, while
    leaving out `definition` would serve a stale block after an edit (spec AC-25).

    Order-independent (sorted lines) because the DAO's ordering is presentational
    and the rendering sorts anyway: a reordering must not look like a change.
    """
    lines = sorted(
        f'{term.standard_term}\x1f{term.definition}\x1f{",".join(term.aliases or [])}'
        for term in terms
    )
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()


#: (tenant_id, question, library_digest) -> rendered block.
#:
#: Keyed on the question AND the library content. The question alone would serve
#: a pre-edit block for a repeated question, breaking AC-25; the library alone
#: would collide across questions.
#:
#: The point of the cache is the assistant path: `create_react_agent`'s prompt
#: callable runs on EVERY model invocation, and one ReAct turn calls the model
#: once per step. Without this, a single turn would rebuild the automaton and
#: re-scan the question several times — and, worse, would need the library read
#: several times.
#:
#: Bounded to a single entry, following the platform word library's precedent:
#: one tenant's current question is the only thing worth keeping, and an
#: unbounded map would accumulate one entry per distinct question asked.
_CACHE_KEY: tuple | None = None
_CACHE_VALUE: str = ''


def _reset_caches() -> None:
    """Drop the memoised block. For tests, and for anything that must not reuse
    a block across a library change in the same process."""
    global _CACHE_KEY, _CACHE_VALUE
    _CACHE_KEY = None
    _CACHE_VALUE = ''


def _build_automaton(terms: list[TermLibraryTerm]) -> tuple[ACAutomaton, dict[str, TermLibraryTerm]]:
    """A matcher over every standard term and alias → the term it belongs to.

    Both go in, because a user typing the shorthand must reach the full
    definition (spec AC-21). The mapping is what turns a matched alias back into
    its standard term, so the block can lead with the standard form.

    When two terms claim the same string the first wins (`setdefault`): the
    service forbids that collision anyway (AC-03), so this only decides what
    happens to a library that predates the rule.

    **Patterns are lowercased here**, because the question is lowercased before
    scanning (see `_match`). `ACAutomaton` lowercases neither side and leaves the
    choice to its caller; doing it on both sides is what makes the match
    case-insensitive. Folding only the question — the obvious half — silently
    breaks every ASCII term: `AB` would never match `ab`, and the failure looks
    like "ASCII terms do not work" rather than like a case bug.
    """
    folded_owner: dict[str, TermLibraryTerm] = {}
    for term in terms:
        for text in (term.standard_term, *(term.aliases or [])):
            folded = text.lower()
            if folded not in folded_owner:
                folded_owner[folded] = term
    return ACAutomaton(folded_owner.keys()), folded_owner


def _match(question: str, automaton: ACAutomaton, owner: dict[str, TermLibraryTerm]) -> list[tuple[int, TermLibraryTerm]]:
    """(first position, term) for every term the question mentions, in order.

    Position is where the term was FIRST seen, which is the ordering key the spec
    fixes (AC-23). Repeats collapse: a term mentioned three times is one entry.

    Matching is case-insensitive: BOTH sides are folded — the question here, the
    patterns in `_build_automaton`. `ACAutomaton` lowercases neither and leaves
    the choice to its caller; folding only the question is the half that looks
    sufficient and silently breaks every ASCII term (see that function's note).
    """
    if not question:
        return []

    # Three values, not two: (state_after, matches, captured_state). `None` for
    # the scan state means "start fresh", and the captured state is only used by
    # masking callers — we are a one-shot scan.
    _state_after, matches, _captured = automaton.find_all_matches(None, question.lower())
    first_position: dict[int, int] = {}
    for end_position, matched_text in matches:
        term = owner.get(matched_text)
        if term is None:
            continue
        # `find_all_matches` reports the END offset of each match; the start is
        # what the ordering rule talks about.
        start = end_position - len(matched_text) + 1
        known = first_position.get(term.id)
        if known is None or start < known:
            first_position[term.id] = start

    by_id = {term.id: term for term in owner.values()}
    return [(pos, by_id[term_id]) for term_id, pos in first_position.items()]


def _sort_key(entry: tuple[int, TermLibraryTerm]) -> tuple[int, int, str]:
    """The spec's total order (AC-23): earliest mention, then the more specific
    (longer) standard term, then the term itself so ties cannot shuffle.

    Total, not merely partial: truncation keeps a prefix, so any two entries that
    compare equal would let the same question render differently between runs.
    """
    position, term = entry
    return (position, -len(term.standard_term), term.standard_term)


def _render(term: TermLibraryTerm) -> str:
    """One entry: the standard term, its aliases, and what it means.

    The aliases are included even when the STANDARD term was what matched: the
    block's job is to connect the user's words to the platform's vocabulary, and
    the model is the one doing the connecting.
    """
    alias_part = ''
    if term.aliases:
        alias_part = _ALIAS_TEMPLATE.format(aliases='、'.join(term.aliases))
    return f'- {term.standard_term}{alias_part}：{term.definition}'


def _render_capped(sorted_entries: list[tuple[int, TermLibraryTerm]]) -> str:
    """Render entries until the character budget runs out, then say what is left.

    Two things this deliberately does NOT do:

    * It does not silently drop the remainder. "These are the relevant terms"
      and "these are SOME of them" lead a model to different answers, so the
      count is stated (AC-23).
    * It does not let a single oversized entry push past the cap. The header line
      and the remainder note are spent FIRST, and an entry larger than what is
      left is skipped rather than truncated mid-definition — a half definition is
      worse than none, and the note already tells the model something was there.
    """
    rendered: list[str] = []
    used = len(_BLOCK_HEADER)

    for _position, term in sorted_entries:
        line = _render(term)
        # Reserve room for the note; it is only spent if something is dropped, so
        # over-reserving costs at most one term in the uncommon case.
        if used + len(line) > MAX_BLOCK_CHARS - _remainder_reserve(len(sorted_entries)):
            break
        rendered.append(line)
        used += len(line)

    if not rendered:
        # Nothing fit. Emitting a bare header would tell the model a term list
        # exists with no content; emitting nothing at all is the honest answer.
        return ''

    dropped = len(sorted_entries) - len(rendered)
    body = '\n'.join([_BLOCK_HEADER, *rendered])
    if dropped:
        body += f'\n（还有 {dropped} 条本轮命中的术语未展开）'
    return body


def _remainder_reserve(total: int) -> int:
    """Room kept for the "N more not shown" note.

    Sized from the count so the note cannot itself overflow the budget — the
    largest note is for the largest library, and the cap is 500 terms (AC-06).
    """
    return len(f'\n（还有 {total} 条本轮命中的术语未展开）')


async def build_term_block(tenant_id: int, user_query: str) -> str:
    """The term block to append to a system prompt this turn, or '' for none.

    `tenant_id` is passed for the cache key and for logging; it is NOT used to
    scope the read — the DAO's tenant filter takes that from the request context,
    which is the only value a caller cannot get wrong.

    Returns '' in every "nothing to say" case: no library, no match, a blank
    question, a library that could not be read. Callers append the result when it
    is non-empty and otherwise leave the prompt exactly as it was (AC-22).
    """
    global _CACHE_KEY, _CACHE_VALUE

    question = (user_query or '').strip()
    if not question:
        return ''

    try:
        terms = await TermLibraryTermDao.alist_all()
    except Exception:
        # Design §7.4: an unreachable glossary must not cost the user their
        # conversation. Warning, not error — nothing is broken, a feature is off.
        logger.opt(exception=True).warning(
            'term library read failed for tenant={}; continuing without terms', tenant_id
        )
        return ''

    if not terms:
        return ''

    cache_key = (tenant_id, question, _library_digest(terms))
    if cache_key == _CACHE_KEY:
        return _CACHE_VALUE

    try:
        automaton, owner = _build_automaton(terms)
        matched = sorted(_match(question, automaton, owner), key=_sort_key)
        block = _render_capped(matched)
    except Exception:
        logger.opt(exception=True).warning(
            'term block construction failed for tenant={}; continuing without terms', tenant_id
        )
        return ''

    _CACHE_KEY = cache_key
    _CACHE_VALUE = block
    return block
