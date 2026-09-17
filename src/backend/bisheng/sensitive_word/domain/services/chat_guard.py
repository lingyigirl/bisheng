from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from bisheng.common.schemas.api import PageData
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.sensitive_word.domain.models.hit_log import SensitiveWordHitLogDao
from bisheng.sensitive_word.domain.schemas import (
    SensitiveWordBusinessType,
    SensitiveWordCheckResult,
    SensitiveWordHit,
    SensitiveWordHitLogItem,
)
from bisheng.sensitive_word.domain.services.platform_word_service import PlatformWordService
from bisheng.sensitive_word.domain.services.sensitive_word_policy_service import (
    SensitiveWordPolicyService,
    WordMatcher,
)

if TYPE_CHECKING:
    from bisheng.common.dependencies.user_deps import UserPayload

ACTION_BLOCK = 'block'
ACTION_MASK = 'mask'
DEFAULT_OUTPUT_ACTION = ACTION_MASK
_ALLOWED_ACTIONS = (ACTION_BLOCK, ACTION_MASK)
INPUT_ACTION = ACTION_BLOCK  # 输入方向恒为拦截 (design §4.2)
DIRECTION_INPUT = 'input'
DIRECTION_OUTPUT = 'output'

SOURCE_TENANT = 'tenant'
SOURCE_PLATFORM = 'platform'


@dataclass
class OutputGuardState:
    """Per-chat streaming guard state; created by ``start_output_guard`` and fed
    chunk by chunk into ``check_output_chunk``. Holds the AC automaton scan
    position, the un-emitted tail buffer (mask 模式跨分片缓冲窗口, design 坑 5)
    and dedupe bookkeeping so a word completed inside a held-back tail is not
    double-counted when the tail is rescanned.

    F059 adds a second, higher-priority layer: the platform-wide library
    (``platform_matcher``). Both layers scan the SAME buffered text and therefore
    share one ``tail`` buffer and ``tail_pos``, but each keeps its own scan state
    and dedupe watermark because their automatons differ.

    The platform matcher is resolved LAZILY on the first chunk rather than in
    ``start_output_guard``: that method is synchronous and called from a
    synchronous factory (``api/services/guardrail.py``), and building the platform
    matcher needs a database read. ``platform_resolved`` distinguishes "not looked
    up yet" from "looked up, layer is off".
    """

    enabled: bool = False
    action: str = DEFAULT_OUTPUT_ACTION
    auto_reply: str = ''
    matcher: WordMatcher | None = None
    tail: str = ''
    tail_state: Any = None  # tenant AC scan state before ``tail``
    tail_pos: int = 0  # global position of tail[0]
    last_counted_pos: int = -1  # matches ending at or before this position were counted
    blocked: bool = False
    tenant_id: int | None = None
    flow_id: str | None = None
    chat_id: str | None = None
    user_id: int | None = None

    # --- F059 platform layer ---
    platform_matcher: WordMatcher | None = None
    platform_resolved: bool = False
    platform_tail_state: Any = None  # platform AC scan state before ``tail``
    platform_last_counted_pos: int = -1
    platform_action: str = DEFAULT_OUTPUT_ACTION
    platform_auto_reply: str = ''


@dataclass
class GuardedChunk:
    safe_text: str = ''
    hits: list[SensitiveWordHit] = field(default_factory=list)
    blocked: bool = False


class ChatGuardService:
    """Chat input/output sensitive-word guardrail facade (F056).

    Input checking wraps the existing whole-text scan (spec AC-05..08); output
    guarding scans streamed chunks incrementally with block (truncate + auto_reply
    close-out) or mask (equal-length ``*``) action semantics (spec AC-09..14).
    Hit log writes are best-effort and never raise (spec AC-16, design 决策 5).
    """

    @classmethod
    async def alist_hit_logs(
        cls,
        login_user: UserPayload,
        *,
        direction: str | None = None,
        flow_id: str | None = None,
        source: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        redact_platform_hits: bool = False,
    ) -> PageData[SensitiveWordHitLogItem]:
        """Tenant-scoped hit-log query for the platform log page (spec AC-15).

        With `redact_platform_hits`, rows from the platform library come back
        WITHOUT their hit words. That redaction lives here rather than in the
        endpoint or the browser because the words would otherwise still travel
        over the wire and sit in the response — a frontend-only hide is not a
        boundary.

        The row itself is still returned, and `hit_count` still says how many
        words matched. A tenant administrator needs to distinguish "the guardrail
        blocked this" from "the system failed" (spec AC-18); telling them nothing
        at all just moves the support ticket to the platform team.
        """
        tenant_id = get_current_tenant_id() or login_user.tenant_id
        rows, total = await SensitiveWordHitLogDao.alist(
            tenant_id=tenant_id,
            direction=direction,
            flow_id=flow_id,
            source=source,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
        items = [cls._to_hit_log_item(row, redact_platform_hits=redact_platform_hits) for row in rows]
        return PageData(data=items, total=total)

    @classmethod
    async def alist_hit_logs_for_super_admin(
        cls,
        *,
        direction: str | None = None,
        flow_id: str | None = None,
        source: str | None = None,
        tenant_id: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageData[SensitiveWordHitLogItem]:
        """Cross-tenant hit-log query (spec AC-20). No redaction.

        The automatic tenant filter is bypassed because the platform admin has no
        single current tenant; `tenant_id` narrows the result when they want one.
        Without the bypass they would see only the root tenant's rows, which
        looks like "no hits anywhere" rather than a permission problem.
        """
        from bisheng.core.context.tenant import bypass_tenant_filter

        with bypass_tenant_filter():
            rows, total = await SensitiveWordHitLogDao.alist(
                tenant_id=tenant_id,
                direction=direction,
                flow_id=flow_id,
                source=source,
                start_time=start_time,
                end_time=end_time,
                page=page,
                page_size=page_size,
            )
        items = [cls._to_hit_log_item(row, redact_platform_hits=False) for row in rows]
        return PageData(data=items, total=total)

    @staticmethod
    def _to_hit_log_item(row: object, *, redact_platform_hits: bool) -> SensitiveWordHitLogItem:
        """Build one response row, applying the platform-hit redaction."""
        raw_hits = list(getattr(row, 'hits', None) or [])
        # NULL is treated as `tenant`: the column's server default is 'tenant', so
        # a NULL can only come from an old process mid-deploy, and defaulting the
        # other way would blank out words a tenant is entitled to see.
        source = getattr(row, 'source', None) or SOURCE_TENANT
        hidden = redact_platform_hits and source == SOURCE_PLATFORM

        return SensitiveWordHitLogItem(
            id=row.id,
            business_type=row.business_type,
            direction=row.direction,
            flow_id=row.flow_id,
            chat_id=row.chat_id,
            user_id=row.user_id,
            hits=[] if hidden else [SensitiveWordHit(**hit) for hit in raw_hits],
            action=row.action,
            source=source,
            tenant_id=getattr(row, 'tenant_id', None),
            hit_count=len(raw_hits),
            create_time=row.create_time,
        )

    @classmethod
    async def check_chat_input(
        cls,
        tenant_id: int,
        text: str,
        *,
        flow_id: str | None = None,
        chat_id: str | None = None,
        user_id: int | None = None,
    ) -> SensitiveWordCheckResult:
        """Guard one user message: platform layer first, tenant layer second.

        The platform layer SHORT-CIRCUITS (spec AC-12): when it hits, the tenant
        policy is not consulted at all and the platform reply is what the user
        sees. Falling through would either produce a second, conflicting reply or
        let a tenant-configured message describe a block the tenant cannot see.
        """
        platform_result = await PlatformWordService.check_platform_text(text)
        if platform_result.enabled and platform_result.hits:
            await cls._write_hit_log(
                tenant_id=tenant_id,
                business_type=SensitiveWordBusinessType.CHAT_INPUT.value,
                direction=DIRECTION_INPUT,
                action=INPUT_ACTION,
                hits=platform_result.hits,
                flow_id=flow_id,
                chat_id=chat_id,
                user_id=user_id,
                source=SOURCE_PLATFORM,
            )
            return platform_result

        result = SensitiveWordPolicyService.check_text(
            tenant_id=tenant_id,
            business_type=SensitiveWordBusinessType.CHAT_INPUT,
            text=text,
        )
        if result.enabled and result.hits:
            await cls._write_hit_log(
                tenant_id=tenant_id,
                business_type=SensitiveWordBusinessType.CHAT_INPUT.value,
                direction=DIRECTION_INPUT,
                action=INPUT_ACTION,
                hits=result.hits,
                flow_id=flow_id,
                chat_id=chat_id,
                user_id=user_id,
                source=SOURCE_TENANT,
            )
        return result

    @classmethod
    def start_output_guard(
        cls,
        tenant_id: int,
        *,
        flow_id: str | None = None,
        chat_id: str | None = None,
        user_id: int | None = None,
    ) -> OutputGuardState:
        matcher = SensitiveWordPolicyService.build_matcher(
            tenant_id=tenant_id,
            business_type=SensitiveWordBusinessType.CHAT_OUTPUT,
        )
        if matcher is None:
            # `enabled=False` here means "the TENANT layer is off". The platform
            # layer (F059) is resolved lazily on the first chunk and may switch
            # this back on — a tenant cannot disable the platform layer.
            return OutputGuardState(
                enabled=False, tenant_id=tenant_id, flow_id=flow_id, chat_id=chat_id, user_id=user_id
            )
        action = matcher.extra_config.get('action', DEFAULT_OUTPUT_ACTION)
        if action not in _ALLOWED_ACTIONS:
            action = DEFAULT_OUTPUT_ACTION
        return OutputGuardState(
            enabled=True,
            action=action,
            auto_reply=matcher.auto_reply,
            matcher=matcher,
            tenant_id=tenant_id,
            flow_id=flow_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    @classmethod
    async def check_output_chunk(cls, state: OutputGuardState, chunk: str) -> GuardedChunk:
        """Incrementally guard one streamed chunk; must be called in stream order.

        Two layers scan the same buffered text. The platform layer has priority
        (user-confirmed: platform words win over tenant words), applied per detection point
        rather than globally — see design 决策 3 for why a global rule is
        impossible without buffering the whole answer.
        """
        # Resolve the platform layer BEFORE honouring `enabled`: a stream whose
        # tenant layer is off must still reach the platform lookup, since the
        # platform layer is mandatory per tenant (spec AC-08) and switches
        # `enabled` back on when it is active.
        if not state.platform_resolved:
            await cls._ensure_platform_resolved(state)
        if not state.enabled:
            return GuardedChunk(safe_text=chunk)
        if state.blocked:
            return GuardedChunk(blocked=True)
        if not chunk:
            return GuardedChunk(safe_text='')

        matcher = state.matcher
        platform = state.platform_matcher

        buffered = state.tail + chunk

        # Two DIFFERENT lists per layer, and conflating them is a leak:
        #   `*_all`      — every match in this buffer, used for MASKING and for the
        #                  emit-boundary decision;
        #   `*_matches`  — only those past the watermark, used for COUNTING/logging.
        # A word counted in an earlier chunk can be held back in the tail and then
        # re-found by the rescan; it is already past the watermark, so it drops out
        # of the filtered list — but it still has to be masked when the tail is
        # finally emitted. Masking from the filtered list emits it IN THE CLEAR
        # (regression introduced by the F059 two-layer refactor; F056 passed the
        # unfiltered list here).
        if platform is not None:
            platform_scan = buffered if platform.case_sensitive else buffered.lower()
            p_state_after, p_all, _ = platform.automaton.find_all_matches(state.platform_tail_state, platform_scan)
            platform_all = list(p_all)
            platform_matches = [(pos, word) for pos, word in p_all if pos > state.platform_last_counted_pos]
            if platform_matches:
                state.platform_last_counted_pos = max(pos for pos, _ in platform_matches)
        else:
            p_state_after, platform_scan, platform_all, platform_matches = None, '', [], []

        if matcher is not None:
            scan_text = buffered if matcher.case_sensitive else buffered.lower()
            state_after, tenant_all, _ = matcher.automaton.find_all_matches(state.tail_state, scan_text)
            all_matches = list(tenant_all)
            new_matches = [(pos, word) for pos, word in tenant_all if pos > state.last_counted_pos]
            if new_matches:
                state.last_counted_pos = max(pos for pos, _ in new_matches)
        else:
            state_after, scan_text, all_matches, new_matches = None, '', [], []

        all_new = [(pos, word, platform) for pos, word in platform_matches] + [
            (pos, word, matcher) for pos, word in new_matches
        ]
        # Every match visible in this buffer, regardless of when it was counted —
        # what the masking pass and the emit boundary must use.
        all_seen = [(pos, word, platform) for pos, word in platform_all] + [
            (pos, word, matcher) for pos, word in all_matches
        ]

        hits = cls._collect_hits(all_new)

        # The layer whose action/reply governs this chunk. Platform wins whenever it
        # hit (spec AC-12); otherwise the tenant layer, unchanged from F056.
        governing_platform = bool(platform_matches)
        action = state.platform_action if governing_platform else state.action

        # Blocking considers everything visible, not just newly counted words: a
        # word held back in the tail and re-found here must still stop the stream.
        blocks = action == ACTION_BLOCK and all_seen
        if blocks:
            # Truncate at the EARLIEST hit across BOTH layers: if the tenant layer
            # also caught something in this chunk, stopping only at the platform
            # word would emit the tenant-sensitive text in the clear — the tenant
            # policy would silently stop working on this path (design 决策 3 rule 3).
            first_start = min(pos - len(word) + 1 for pos, word, _ in all_seen)
            # A hit starting inside the held-back tail yields emit_len 0 (defensive
            # lower bound; the tail was already withheld whole in the previous round).
            emit_len = max(0, first_start - state.tail_pos)
            state.blocked = True
            # `state.auto_reply` is what the API-layer guard appends to close the
            # reply (`api/services/guardrail.py` reads `state.auto_reply` on
            # `blocked`). Point it at the GOVERNING layer's wording before the
            # block is reported, so a platform block closes with the platform
            # reply rather than whatever the tenant configured.
            state.auto_reply = (
                state.platform_auto_reply if governing_platform else state.auto_reply
            )
            safe_text = buffered[:emit_len]
            state.tail = ''
            state.tail_pos += emit_len
        else:
            emit_len = len(buffered)
            if matcher is not None:
                emit_len = min(
                    emit_len,
                    cls._resolve_emit_len(matcher, state_after, all_matches, state.tail_pos, buffered),
                )
            if platform is not None:
                emit_len = min(
                    emit_len,
                    cls._resolve_emit_len(platform, p_state_after, platform_all, state.tail_pos, buffered),
                )
            safe_prefix = buffered[:emit_len]
            masked = action == ACTION_MASK and all_seen
            if masked:
                spans = [(pos, word) for pos, word, _ in all_seen]
                safe_text = cls._mask(safe_prefix, spans, state.tail_pos, emit_len)
            else:
                safe_text = safe_prefix
            tail_next = buffered[emit_len:]
            if matcher is not None:
                state.tail_state = cls._rewind_scan_state(
                    matcher, state.tail_state, scan_text, state.tail_pos, emit_len, tail_next, state_after
                )
            if platform is not None:
                state.platform_tail_state = cls._rewind_scan_state(
                    platform,
                    state.platform_tail_state,
                    platform_scan,
                    state.tail_pos,
                    emit_len,
                    tail_next,
                    p_state_after,
                )
            state.tail = tail_next
            state.tail_pos += emit_len

        # One row PER LAYER that hit, not a single merged row attributed to the
        # governing layer. A chunk can trip both libraries at once, and a merged
        # row labelled `platform` would have its words stripped from the tenant's
        # own log view (`_to_hit_log_item`) — the tenant would lose sight of words
        # from THEIR policy, breaking the "tenant rows show their words" contract
        # (spec AC-19) and mis-stating the count.
        if hits:
            if platform_matches:
                await cls._write_hit_log(
                    tenant_id=state.tenant_id,
                    business_type=SensitiveWordBusinessType.CHAT_OUTPUT.value,
                    direction=DIRECTION_OUTPUT,
                    action=action,
                    hits=cls._collect_hits([(pos, word, platform) for pos, word in platform_all]),
                    flow_id=state.flow_id,
                    chat_id=state.chat_id,
                    user_id=state.user_id,
                    source=SOURCE_PLATFORM,
                )
            if new_matches:
                await cls._write_hit_log(
                    tenant_id=state.tenant_id,
                    business_type=SensitiveWordBusinessType.CHAT_OUTPUT.value,
                    direction=DIRECTION_OUTPUT,
                    action=action,
                    hits=cls._collect_hits([(pos, word, matcher) for pos, word in all_matches]),
                    flow_id=state.flow_id,
                    chat_id=state.chat_id,
                    user_id=state.user_id,
                    source=SOURCE_TENANT,
                )
        return GuardedChunk(safe_text=safe_text, hits=hits, blocked=state.blocked)

    @classmethod
    async def _ensure_platform_resolved(cls, state: OutputGuardState) -> None:
        """Look up the platform layer once per stream, on the first chunk.

        Deferred to here because `start_output_guard` is synchronous while the
        lookup needs a database read. A tenant-disabled stream still resolves the
        platform layer — that is the whole point of the layer being mandatory.
        """
        if state.platform_resolved:
            return
        state.platform_resolved = True
        matcher = await PlatformWordService.build_platform_matcher()
        if matcher is not None:
            state.platform_matcher = matcher
            state.platform_action = matcher.extra_config.get('action', DEFAULT_OUTPUT_ACTION)
            if state.platform_action not in _ALLOWED_ACTIONS:
                state.platform_action = DEFAULT_OUTPUT_ACTION
            state.platform_auto_reply = matcher.auto_reply
            state.enabled = True

    @staticmethod
    def _collect_hits(all_new: list[tuple[int, str, WordMatcher]]) -> list[SensitiveWordHit]:
        counts: Counter[str] = Counter()
        for _, word, matcher in all_new:
            counts[matcher.normalized_map.get(word, word)] += 1
        return [SensitiveWordHit(word=word, count=count) for word, count in counts.items()]

    @staticmethod
    def _rewind_scan_state(
        matcher: WordMatcher,
        prior_state: Any,
        scan_text: str,
        tail_pos: int,
        emit_len: int,
        tail_next: str,
        state_after: Any,
    ) -> Any:
        """Scan state to carry forward after emitting ``emit_len`` chars.

        When nothing is held back the post-scan state is already correct. When a
        tail remains, the state must be rewound to the emit boundary so the tail is
        not re-processed (and its words not double-counted) on the next chunk.

        The fallback when no boundary was captured is the PRIOR state, not
        ``state_after``: that case means ``emit_len`` was 0, i.e. the entire buffer
        was withheld, so the next chunk re-scans it in full and the scan must resume
        from where this chunk started. Falling back to ``state_after`` would advance
        the scan position past text that is about to be rescanned, and the rescanned
        words would then be reported at positions past the end of the buffer.
        """
        if not tail_next:
            return state_after
        _, _, captured = matcher.automaton.find_all_matches(
            prior_state,
            scan_text[:emit_len],
            capture_at=tail_pos + emit_len - 1,
        )
        return captured if captured is not None else prior_state

    @classmethod
    async def flush_output(cls, state: OutputGuardState) -> GuardedChunk:
        """Emit the held-back tail when the stream ends.

        Mask-mode hold-back never emits a partially resolved word; once the stream
        is over no longer pattern can arrive, so the tail is emitted with any
        completed word inside it masked. Hits were already counted when the words
        completed, so the flush reports none. No-op when nothing is pending.
        """
        if not state.enabled or state.blocked or not state.tail:
            return GuardedChunk()
        buffered = state.tail
        matches: list[tuple[int, str]] = []
        if state.matcher is not None:
            matcher = state.matcher
            scan_text = buffered if matcher.case_sensitive else buffered.lower()
            _, tenant_matches, _ = matcher.automaton.find_all_matches(state.tail_state, scan_text)
            matches.extend(tenant_matches)
        if state.platform_matcher is not None:
            platform = state.platform_matcher
            platform_scan = buffered if platform.case_sensitive else buffered.lower()
            _, platform_matches, _ = platform.automaton.find_all_matches(
                state.platform_tail_state, platform_scan
            )
            matches.extend(platform_matches)
        safe_text = cls._mask(buffered, matches, state.tail_pos, len(buffered))
        state.tail = ''
        state.tail_pos += len(buffered)
        return GuardedChunk(safe_text=safe_text)

    @staticmethod
    def _resolve_emit_len(matcher: WordMatcher, state_after: Any, matches: list[tuple[int, str]], tail_pos: int, buffered: str) -> int:
        """How many buffered chars are safe to emit.

        Holds back an incomplete suffix that could extend into a longer pattern
        (``in_progress``), and never splits a matched word across the emit boundary —
        a word ending inside the held-back tail is held back whole so its mask is
        applied when it is finally emitted (design 坑 5).
        """
        buf_len = len(buffered)
        hold = 0
        if matcher.automaton.in_progress(state_after):
            hold = min(matcher.max_word_len - 1, buf_len)
        emit_len = buf_len - hold
        for pos, word in matches:
            start = pos - len(word) + 1 - tail_pos
            end = pos - tail_pos
            if end >= emit_len and start < emit_len:
                hold = buf_len - start
                emit_len = buf_len - hold
        return emit_len

    @staticmethod
    def _mask(prefix: str, matches: list[tuple[int, str]], tail_pos: int, emit_len: int) -> str:
        """Replace matched words inside the emitted prefix with equal-length ``*``."""
        if emit_len <= 0 or not matches:
            return prefix
        spans: list[tuple[int, int]] = []
        for pos, word in matches:
            start = pos - len(word) + 1 - tail_pos
            end = pos - tail_pos
            if start >= 0 and end < emit_len:
                spans.append((start, end))
        if not spans:
            return prefix
        spans.sort()
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        chars = list(prefix)
        for start, end in merged:
            for index in range(start, min(end, emit_len - 1) + 1):
                chars[index] = '*'
        return ''.join(chars)

    @classmethod
    async def _write_hit_log(
        cls,
        *,
        tenant_id: int | None,
        business_type: str,
        direction: str,
        action: str,
        hits: list[SensitiveWordHit],
        flow_id: str | None,
        chat_id: str | None,
        user_id: int | None,
        source: str = SOURCE_TENANT,
    ) -> None:
        await SensitiveWordHitLogDao.acreate_async_best_effort(
            tenant_id=tenant_id,
            business_type=business_type,
            direction=direction,
            action=action,
            hits=[hit.model_dump() for hit in hits],
            flow_id=flow_id,
            chat_id=chat_id,
            user_id=user_id,
            source=source,
        )
