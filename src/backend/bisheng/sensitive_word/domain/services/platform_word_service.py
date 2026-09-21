from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

from bisheng.common.errcode.platform_sensitive_word import PlatformWordFileParseError
from bisheng.sensitive_word.domain.models.platform_policy import (
    DEFAULT_AUTO_REPLY,
    PlatformSensitiveWordPolicyDao,
)
from bisheng.sensitive_word.domain.models.platform_word import PlatformSensitiveWordDao
from bisheng.sensitive_word.domain.schemas import SensitiveWordCheckResult, SensitiveWordHit
from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton
from bisheng.sensitive_word.domain.services.tabular_import import (
    HEADER_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    TabularImportError,
    looks_like_header,
    read_rows,
)

if TYPE_CHECKING:
    from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import WordMatcher

MAX_WORD_LENGTH = 255
"""Matches the `platform_sensitive_word.word` column width.

Stays here rather than moving to `tabular_import`: it is a property of THIS
library's column width, not of reading a file.
"""


class ImportMode(str, Enum):  # noqa: UP042 - matches the module's existing str-Enum idiom
    OVERWRITE = 'overwrite'
    APPEND = 'append'


@dataclass
class ParsedWordFile:
    """Outcome of parsing an uploaded file.

    Carries the parse statistics alongside the words so the confirmation dialog
    can explain why the numbers look the way they do — a file can contain more
    tokens than the library will end up with, and silently showing only the
    final count reads like words were lost.
    """

    words: list[str] = field(default_factory=list)
    duplicates: int = 0
    dropped_overlong: int = 0


@dataclass
class ImportPreview:
    """What an import WOULD change, shown before an overwrite is confirmed."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    incoming: int = 0
    duplicates: int = 0
    dropped_overlong: int = 0


class PlatformWordService:
    """Platform-wide (cross-tenant) sensitive word library.

    Holds the word library itself and the single-row policy that turns it on
    (spec AC-01..AC-09). Authentication is NOT done here — every mutating entry
    point is super-admin only at the endpoint layer (design K9).

    The library has no `tenant_id` column by design: it is one library shared by
    every tenant, and adding the column would make the rows subject to the
    automatic tenant filter (unreadable + unwritable). See `models/platform_word.py`.
    """

    # A dedicated automaton cache. Deliberately NOT F056's `SensitiveWordPolicyService
    # ._automaton_cache`: that cache is keyed partly on `tenant_id`, so reusing it for a
    # global library would store one identical (potentially 10k-word) automaton per
    # tenant and invalidate N copies on every import. Here one entry serves everybody.
    _platform_automaton_cache: dict[str, tuple[ACAutomaton, dict[str, str]]] = {}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse_words_file(cls, raw: bytes, filename: str) -> ParsedWordFile:
        """Turn an uploaded file into de-duplicated words.

        Raises `PlatformWordFileParseError` (11301) when the file cannot be read
        or yields nothing usable — the caller must leave the library untouched in
        that case (spec AC-05).

        The reading half is delegated to `tabular_import` (F060 decision 8), which
        the term library also uses: same extension list, same encoding fallback,
        same NUL trap, so a file that reads there cannot fail to read here. This
        method keeps two things of its own — the 11301 error code callers expect,
        and the word-per-cell column semantics. The shared reader's exception is
        translated, so callers of THIS service see exactly what they always did.
        """
        try:
            cells = read_rows(raw, filename, allowed=SUPPORTED_EXTENSIONS)
        except TabularImportError as exc:
            raise PlatformWordFileParseError(exception=exc) from exc

        extension = (filename or '').rsplit('.', 1)[-1].lower()
        return cls._cells_to_parsed(cells, strip_header=extension in HEADER_EXTENSIONS)

    @classmethod
    def _cells_to_parsed(cls, rows: list[list[str]], *, strip_header: bool) -> ParsedWordFile:
        if strip_header and rows and cls._looks_like_header(rows[0]):
            rows = rows[1:]

        parsed = ParsedWordFile()
        seen: set[str] = set()
        for row in rows:
            # Cells inside a row are separate words (a two-column sheet is a word
            # plus a note, not one phrase), so each cell goes through the same
            # separator rules F056 uses for pasted word lists.
            for cell in row:
                for word in cls._split_cell(cell):
                    if len(word) > MAX_WORD_LENGTH:
                        parsed.dropped_overlong += 1
                        continue
                    if word in seen:
                        parsed.duplicates += 1
                        continue
                    seen.add(word)
                    parsed.words.append(word)

        if not parsed.words:
            raise PlatformWordFileParseError()
        return parsed

    @classmethod
    def _looks_like_header(cls, row: list[str]) -> bool:
        """True when the first row is a header rather than data.

        Delegates to the shared detector, passing THIS feature's cell splitter —
        the header question is answered with the same rules that will later read
        the values (see `tabular_import.looks_like_header`).
        """
        return looks_like_header(row, split_cell=cls._split_cell)

    @staticmethod
    def _split_cell(cell: str) -> list[str]:
        """Split one cell into raw tokens, KEEPING duplicates.

        Deliberately not `SensitiveWordPolicyService.normalize_words` — that helper
        de-dupes internally, which would hide the number of repeated tokens the
        preview needs to report. The separator pattern is imported from there so
        both paths still split identically.
        """
        from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import (
            WORD_SEPARATOR_RE,
        )

        return [token.strip() for token in WORD_SEPARATOR_RE.split(cell or '') if token.strip()]

    # ------------------------------------------------------------------
    # Preview / import
    # ------------------------------------------------------------------

    @classmethod
    def preview_import(
        cls,
        parsed: ParsedWordFile,
        mode: ImportMode,
        *,
        existing: list[str],
    ) -> ImportPreview:
        """Compute what `mode` would change, without writing anything.

        `added`/`removed` are returned as lists (sorted for a stable dialog), while
        the membership tests use sets — a library can hold tens of thousands of
        words and the dialog is shown on every import.
        """
        incoming = set(parsed.words)
        current = set(existing)

        if mode == ImportMode.APPEND:
            added = sorted(incoming - current)
            removed: list[str] = []
        else:
            added = sorted(incoming - current)
            removed = sorted(current - incoming)

        return ImportPreview(
            added=added,
            removed=removed,
            incoming=len(parsed.words),
            duplicates=parsed.duplicates,
            dropped_overlong=parsed.dropped_overlong,
        )

    @classmethod
    async def import_words(
        cls,
        parsed: ParsedWordFile,
        mode: ImportMode,
        *,
        operator_id: int | None,
    ) -> int:
        """Apply an import. Returns the number of words stored.

        Both modes run in a single transaction (see the DAO) so a failure cannot
        leave the library in a state the confirmed preview did not describe.
        """
        if mode == ImportMode.APPEND:
            written = await PlatformSensitiveWordDao.acreate_many(parsed.words, source='import', created_by=operator_id)
        else:
            written = await PlatformSensitiveWordDao.areplace_all(parsed.words, source='import', created_by=operator_id)
        cls.clear_cache()
        return written

    # ------------------------------------------------------------------
    # Library CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def alist_words(cls, *, keyword: str | None = None, page: int = 1, page_size: int = 20):
        rows = await PlatformSensitiveWordDao.alist(keyword=keyword, page=page, page_size=page_size)
        total = await PlatformSensitiveWordDao.acount(keyword=keyword)
        return rows, total

    @classmethod
    async def add_word(cls, word: str, *, operator_id: int | None) -> bool:
        """Add one word. Returns False when it is blank or already present."""
        cleaned = (word or '').strip()
        if not cleaned or len(cleaned) > MAX_WORD_LENGTH:
            return False
        if await PlatformSensitiveWordDao.aexists(cleaned):
            return False
        await PlatformSensitiveWordDao.acreate(cleaned, source='manual', created_by=operator_id)
        cls.clear_cache()
        return True

    @classmethod
    async def delete_word(cls, word_id: int) -> bool:
        deleted = await PlatformSensitiveWordDao.adelete_by_id(word_id)
        if deleted:
            cls.clear_cache()
        return deleted

    @classmethod
    async def alist_all_words(cls) -> list[str]:
        return await PlatformSensitiveWordDao.alist_all_words()

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    @classmethod
    async def aget_policy(cls):
        return await PlatformSensitiveWordPolicyDao.aget_policy()

    @classmethod
    async def aupsert_policy(cls, *, enabled: bool, action: str, auto_reply: str, operator_id: int | None):
        policy = await PlatformSensitiveWordPolicyDao.aupsert(
            enabled=enabled,
            action=action,
            auto_reply=auto_reply,
            operator_id=operator_id,
        )
        cls.clear_cache()
        return policy

    # ------------------------------------------------------------------
    # Matcher (spec AC-07 immediate effect, AC-14 skip when off)
    # ------------------------------------------------------------------

    @classmethod
    def clear_cache(cls) -> None:
        """Drop the built automatons. Call after ANY change to words or policy."""
        cls._platform_automaton_cache.clear()

    @classmethod
    async def build_platform_matcher(cls) -> WordMatcher | None:
        """Effective platform matcher, or None when the layer is off.

        None means "this layer does no detection at all" — reached when the policy
        is disabled or the library is empty, which is exactly the condition spec
        AC-14 requires to leave every other behaviour untouched.
        """
        from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import WordMatcher

        policy = await PlatformSensitiveWordPolicyDao.aget_policy()
        if not policy.enabled:
            return None

        words = await PlatformSensitiveWordDao.alist_all_words()
        if not words:
            return None

        case_sensitive = False
        automaton, normalized_map = cls._get_automaton(words, case_sensitive)
        return WordMatcher(
            automaton=automaton,
            normalized_map=normalized_map,
            case_sensitive=case_sensitive,
            # Fall back to the requirement's wording when the admin cleared the
            # box. This is NOT cosmetic: callers decide to block on the reply's
            # TRUTHINESS (`api/services/guardrail.py` does `if blocked_reply:`),
            # so an empty string would let every platform hit through to the
            # model while the admin page (which displays this same default with
            # `auto_reply_is_default`) looks correctly configured. The tenant
            # path has always applied this fallback — see
            # `sensitive_word_policy_service.py` build_matcher/aupsert_policy.
            auto_reply=policy.auto_reply or DEFAULT_AUTO_REPLY,
            extra_config={'action': policy.action},
            max_word_len=max((len(word) for word in normalized_map.values()), default=0),
        )

    @classmethod
    def _get_automaton(cls, words: list[str], case_sensitive: bool) -> tuple[ACAutomaton, dict[str, str]]:
        """Build (or reuse) the automaton for this word set.

        Keyed on a content digest rather than on a "version" counter: the digest
        changes exactly when the effective word set does, so a no-op re-import
        (identical file) reuses the automaton instead of rebuilding it.
        """
        normalized_map: dict[str, str] = {}
        normalized_words: list[str] = []
        for word in words:
            key = word if case_sensitive else word.lower()
            if key in normalized_map:
                continue
            normalized_map[key] = word
            normalized_words.append(key)

        digest = hashlib.sha256('\n'.join(normalized_words).encode('utf-8')).hexdigest()
        cache_key = f'{digest}:{case_sensitive}'
        cached = cls._platform_automaton_cache.get(cache_key)
        if cached is not None:
            return cached

        automaton = ACAutomaton(normalized_words)
        entry = (automaton, normalized_map)
        # Bounded: keep only the current build. An import storm would otherwise
        # accumulate one large automaton per distinct word-set revision.
        cls._platform_automaton_cache.clear()
        cls._platform_automaton_cache[cache_key] = entry
        logger.debug('platform sensitive word automaton built: {} words', len(normalized_words))
        return entry

    @classmethod
    async def check_platform_text(cls, text: str) -> SensitiveWordCheckResult:
        """Whole-text scan against the platform library (input direction).

        Mirrors `SensitiveWordPolicyService.check_text`'s result shape so the
        caller can treat the two layers uniformly.
        """
        matcher = await cls.build_platform_matcher()
        if matcher is None:
            return SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=None)

        scan_text = '' if text is None else str(text)
        counter = (
            matcher.automaton.find_all(scan_text if matcher.case_sensitive else scan_text.lower())
            if scan_text
            else {}
        )
        hits = [
            SensitiveWordHit(word=matcher.normalized_map.get(word, word), count=count)
            for word, count in counter.items()
        ]
        return SensitiveWordCheckResult(enabled=True, hits=hits, auto_reply=matcher.auto_reply)
