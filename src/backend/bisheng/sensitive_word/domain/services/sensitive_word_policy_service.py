from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bisheng.sensitive_word.domain.models.sensitive_word_policy import (
    SensitiveWordPolicy,
    SensitiveWordPolicyDao,
)
from bisheng.sensitive_word.domain.schemas import (
    SensitiveWordBusinessType,
    SensitiveWordCheckResult,
    SensitiveWordHit,
    SensitiveWordPolicyPayload,
    SensitiveWordPolicyResp,
    SensitiveWordScopeType,
)
from bisheng.sensitive_word.domain.services.ac_automaton import ACAutomaton

if TYPE_CHECKING:
    from bisheng.common.dependencies.user_deps import UserPayload

DEFAULT_AUTO_REPLY = '上传内容命中敏感词，已被系统拒绝。'  # noqa: RUF001 (fullwidth punctuation in the Chinese user-facing default reply)
BUILTIN_WORDS_TYPE = 'builtin'
CUSTOM_WORDS_TYPE = 'custom'
WORD_SEPARATOR_RE = re.compile(r'[\r\n,，;；|]+')  # noqa: RUF001 (fullwidth comma/semicolon are required word separators)


@dataclass
class WordMatcher:
    """A ready-to-scan matcher for one effective policy (built by ``build_matcher``).

    Callers scan incrementally via ``automaton.find_all_matches`` and hold it for the
    lifetime of one stream/chat; policy updates invalidate cached automata via
    ``clear_cache``, and new matchers must be rebuilt after a policy change.
    """

    automaton: ACAutomaton
    normalized_map: dict[str, str]
    case_sensitive: bool
    auto_reply: str
    extra_config: dict[str, Any]
    max_word_len: int


class SensitiveWordPolicyService:
    _automaton_cache: dict[tuple, tuple[ACAutomaton, dict[str, str]]] = {}

    @staticmethod
    def _current_tenant_id(login_user: UserPayload) -> int:
        from bisheng.core.context.tenant import get_current_tenant_id

        return get_current_tenant_id() or login_user.tenant_id

    @classmethod
    def normalize_words(cls, text: str) -> list[str]:
        words: list[str] = []
        seen = set()
        for item in WORD_SEPARATOR_RE.split(text or ''):
            word = item.strip()
            if not word or word in seen:
                continue
            seen.add(word)
            words.append(word)
        return words

    @classmethod
    def normalize_words_types(cls, words_types: Iterable[str]) -> list[str]:
        allowed = {BUILTIN_WORDS_TYPE, CUSTOM_WORDS_TYPE}
        result: list[str] = []
        for item in words_types or []:
            if item in allowed and item not in result:
                result.append(item)
        return result

    @classmethod
    @lru_cache(maxsize=1)
    def load_builtin_words(cls) -> tuple[str, ...]:
        words_file = Path(__file__).resolve().parents[1] / 'data' / 'words.txt'
        if not words_file.exists():
            return ()
        return tuple(cls.normalize_words(words_file.read_text(encoding='utf-8')))

    @classmethod
    def clear_cache(cls) -> None:
        cls._automaton_cache.clear()
        cls.load_builtin_words.cache_clear()

    @classmethod
    def default_response(cls, tenant_id: int, business_type: str) -> SensitiveWordPolicyResp:
        return SensitiveWordPolicyResp(
            tenant_id=tenant_id,
            business_type=SensitiveWordBusinessType(business_type),
            scope_type=SensitiveWordScopeType.TENANT,
            scope_id=str(tenant_id),
            enabled=False,
            words_types=[],
            custom_words='',
            auto_reply=DEFAULT_AUTO_REPLY,
            extra_config={},
        )

    @classmethod
    def to_response(cls, policy: SensitiveWordPolicy | None, tenant_id: int, business_type: str) -> SensitiveWordPolicyResp:
        if policy is None:
            return cls.default_response(tenant_id, business_type)
        return SensitiveWordPolicyResp(
            tenant_id=policy.tenant_id,
            business_type=SensitiveWordBusinessType(policy.business_type),
            scope_type=SensitiveWordScopeType(policy.scope_type),
            scope_id=policy.scope_id,
            enabled=bool(policy.enabled),
            words_types=cls.normalize_words_types(policy.words_types),
            custom_words=policy.custom_words or '',
            auto_reply=policy.auto_reply or DEFAULT_AUTO_REPLY,
            extra_config=policy.extra_config or {},
        )

    @classmethod
    async def aget_policy(cls, login_user: UserPayload, business_type: SensitiveWordBusinessType) -> SensitiveWordPolicyResp:
        tenant_id = cls._current_tenant_id(login_user)
        policy = await SensitiveWordPolicyDao.aget_policy(
            tenant_id=tenant_id,
            business_type=business_type.value,
            scope_type=SensitiveWordScopeType.TENANT.value,
            scope_id=str(tenant_id),
        )
        return cls.to_response(policy, tenant_id, business_type.value)

    @classmethod
    async def aupsert_policy(
        cls,
        login_user: UserPayload,
        business_type: SensitiveWordBusinessType,
        payload: SensitiveWordPolicyPayload,
    ) -> SensitiveWordPolicyResp:
        tenant_id = cls._current_tenant_id(login_user)
        policy = await SensitiveWordPolicyDao.aupsert_policy(
            tenant_id=tenant_id,
            business_type=business_type.value,
            enabled=payload.enabled,
            words_types=cls.normalize_words_types(payload.words_types),
            custom_words=payload.custom_words or '',
            auto_reply=(payload.auto_reply or DEFAULT_AUTO_REPLY)[:500],
            extra_config=payload.extra_config or {},
            operator_id=login_user.user_id,
            scope_type=SensitiveWordScopeType.TENANT.value,
            scope_id=str(tenant_id),
        )
        cls.clear_cache()
        return cls.to_response(policy, tenant_id, business_type.value)

    @classmethod
    def _resolve_words(cls, policy: SensitiveWordPolicy | None) -> list[str]:
        if policy is None or not policy.enabled:
            return []
        words_types = cls.normalize_words_types(policy.words_types)
        words: list[str] = []
        if BUILTIN_WORDS_TYPE in words_types:
            words.extend(cls.load_builtin_words())
        if CUSTOM_WORDS_TYPE in words_types:
            words.extend(cls.normalize_words(policy.custom_words or ''))

        deduped: list[str] = []
        seen = set()
        for word in words:
            if word in seen:
                continue
            seen.add(word)
            deduped.append(word)
        return deduped

    @classmethod
    def is_effective(
        cls,
        tenant_id: int,
        business_type: SensitiveWordBusinessType,
        scope_type: SensitiveWordScopeType = SensitiveWordScopeType.TENANT,
        scope_id: str | None = None,
    ) -> bool:
        policy = SensitiveWordPolicyDao.get_policy(
            tenant_id=tenant_id,
            business_type=business_type.value,
            scope_type=scope_type.value,
            scope_id=scope_id or str(tenant_id),
        )
        return bool(cls._resolve_words(policy))

    @classmethod
    def _build_cache_key(
        cls,
        tenant_id: int,
        business_type: str,
        scope_type: str,
        scope_id: str,
        policy: SensitiveWordPolicy,
        words: list[str],
        case_sensitive: bool,
    ) -> tuple:
        words_digest = hashlib.sha256('\n'.join(words).encode('utf-8')).hexdigest()
        return (
            tenant_id,
            business_type,
            scope_type,
            scope_id,
            bool(policy.enabled),
            tuple(cls.normalize_words_types(policy.words_types)),
            case_sensitive,
            words_digest,
        )

    @classmethod
    def _get_automaton(
        cls,
        tenant_id: int,
        business_type: str,
        scope_type: str,
        scope_id: str,
        policy: SensitiveWordPolicy,
        words: list[str],
        case_sensitive: bool,
    ) -> tuple[ACAutomaton, dict[str, str]]:
        normalized_map: dict[str, str] = {}
        normalized_words: list[str] = []
        for word in words:
            normalized = word if case_sensitive else word.lower()
            if not normalized or normalized in normalized_map:
                continue
            normalized_map[normalized] = word
            normalized_words.append(normalized)

        key = cls._build_cache_key(
            tenant_id,
            business_type,
            scope_type,
            scope_id,
            policy,
            normalized_words,
            case_sensitive,
        )
        cached = cls._automaton_cache.get(key)
        if cached:
            return cached
        automaton = ACAutomaton(normalized_words)
        cls._automaton_cache[key] = (automaton, normalized_map)
        return automaton, normalized_map

    @classmethod
    def check_text(
        cls,
        tenant_id: int,
        business_type: SensitiveWordBusinessType,
        text: str,
        scope_type: SensitiveWordScopeType = SensitiveWordScopeType.TENANT,
        scope_id: str | None = None,
    ) -> SensitiveWordCheckResult:
        return cls.check_texts(
            tenant_id=tenant_id,
            business_type=business_type,
            texts=[text],
            scope_type=scope_type,
            scope_id=scope_id,
        )[0]

    @classmethod
    def build_matcher(
        cls,
        tenant_id: int,
        business_type: SensitiveWordBusinessType,
        scope_type: SensitiveWordScopeType = SensitiveWordScopeType.TENANT,
        scope_id: str | None = None,
    ) -> WordMatcher | None:
        """Build a reusable matcher for the effective policy, or None when not effective.

        Reuses the same process cache as ``check_texts``; policy updates invalidate it
        via ``clear_cache``. Used by streamed-output guarding to scan chunk by chunk.
        """
        final_scope_id = scope_id or str(tenant_id)
        policy = SensitiveWordPolicyDao.get_policy(
            tenant_id=tenant_id,
            business_type=business_type.value,
            scope_type=scope_type.value,
            scope_id=final_scope_id,
        )
        words = cls._resolve_words(policy)
        if policy is None or not words:
            return None

        extra_config = policy.extra_config or {}
        case_sensitive = bool(extra_config.get('case_sensitive', False))
        automaton, normalized_map = cls._get_automaton(
            tenant_id,
            business_type.value,
            scope_type.value,
            final_scope_id,
            policy,
            words,
            case_sensitive,
        )
        return WordMatcher(
            automaton=automaton,
            normalized_map=normalized_map,
            case_sensitive=case_sensitive,
            auto_reply=policy.auto_reply or DEFAULT_AUTO_REPLY,
            extra_config=extra_config,
            max_word_len=max((len(word) for word in normalized_map.values()), default=0),
        )

    @classmethod
    def check_texts(
        cls,
        tenant_id: int,
        business_type: SensitiveWordBusinessType,
        texts: list[str],
        scope_type: SensitiveWordScopeType = SensitiveWordScopeType.TENANT,
        scope_id: str | None = None,
    ) -> list[SensitiveWordCheckResult]:
        matcher = cls.build_matcher(tenant_id, business_type, scope_type, scope_id)
        if matcher is None:
            return [
                SensitiveWordCheckResult(enabled=False, hits=[], auto_reply=DEFAULT_AUTO_REPLY)
                for _ in texts
            ]

        max_hits = matcher.extra_config.get('max_hits')
        results: list[SensitiveWordCheckResult] = []
        for text in texts:
            scan_text = '' if text is None else str(text)
            counter = (
                matcher.automaton.find_all(scan_text if matcher.case_sensitive else scan_text.lower())
                if scan_text
                else {}
            )
            hits: list[SensitiveWordHit] = [
                SensitiveWordHit(word=matcher.normalized_map.get(word, word), count=count)
                for word, count in counter.items()
            ]
            if isinstance(max_hits, int) and max_hits > 0:
                hits = hits[:max_hits]
            results.append(SensitiveWordCheckResult(
                enabled=True,
                hits=hits,
                auto_reply=matcher.auto_reply,
            ))
        return results
