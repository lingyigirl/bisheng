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


@dataclass
class OutputGuardState:
    """Per-chat streaming guard state; created by ``start_output_guard`` and fed
    chunk by chunk into ``check_output_chunk``. Holds the AC automaton scan
    position, the un-emitted tail buffer (mask 模式跨分片缓冲窗口, design 坑 5)
    and dedupe bookkeeping so a word completed inside a held-back tail is not
    double-counted when the tail is rescanned."""

    enabled: bool = False
    action: str = DEFAULT_OUTPUT_ACTION
    auto_reply: str = ''
    matcher: WordMatcher | None = None
    tail: str = ''
    tail_state: Any = None  # AC automaton scan state before ``tail``
    tail_pos: int = 0  # global position of tail[0]
    last_counted_pos: int = -1  # matches ending at or before this position were counted
    blocked: bool = False
    tenant_id: int | None = None
    flow_id: str | None = None
    chat_id: str | None = None
    user_id: int | None = None


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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageData[SensitiveWordHitLogItem]:
        """Tenant-scoped hit-log query for the platform log page (spec AC-15)."""
        tenant_id = get_current_tenant_id() or login_user.tenant_id
        rows, total = await SensitiveWordHitLogDao.alist(
            tenant_id=tenant_id,
            direction=direction,
            flow_id=flow_id,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
        items = [
            SensitiveWordHitLogItem(
                id=row.id,
                business_type=row.business_type,
                direction=row.direction,
                flow_id=row.flow_id,
                chat_id=row.chat_id,
                user_id=row.user_id,
                hits=[SensitiveWordHit(**hit) for hit in (row.hits or [])],
                action=row.action,
                create_time=row.create_time,
            )
            for row in rows
        ]
        return PageData(data=items, total=total)

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
        """Incrementally guard one streamed chunk; must be called in stream order."""
        if not state.enabled:
            return GuardedChunk(safe_text=chunk)
        if state.blocked:
            return GuardedChunk(blocked=True)
        if not chunk:
            return GuardedChunk(safe_text='')
        matcher = state.matcher
        assert matcher is not None

        buffered = state.tail + chunk
        scan_text = buffered if matcher.case_sensitive else buffered.lower()
        state_after, matches, _ = matcher.automaton.find_all_matches(state.tail_state, scan_text)

        new_matches = [(pos, word) for pos, word in matches if pos > state.last_counted_pos]
        if new_matches:
            state.last_counted_pos = max(pos for pos, _ in new_matches)
        counts: Counter[str] = Counter()
        for _, word in new_matches:
            counts[matcher.normalized_map.get(word, word)] += 1
        hits = [SensitiveWordHit(word=word, count=count) for word, count in counts.items()]

        if state.action == ACTION_BLOCK and new_matches:
            # 截断在首个命中词开始处, 后续分片全部丢弃 (AC-10)
            first_start = min(pos - len(word) + 1 for pos, word in new_matches)
            # 命中词起点在 hold-back 缓冲内时 emit_len 可为 0 (防御性下限, 缓冲
            # 尾部已在上一轮被完整持有, 正常不会为负)
            emit_len = max(0, first_start - state.tail_pos)
            state.blocked = True
            safe_text = buffered[:emit_len]
            state.tail = ''
            state.tail_pos += emit_len
        else:
            emit_len = cls._resolve_emit_len(matcher, state_after, matches, state.tail_pos, buffered)
            safe_prefix = buffered[:emit_len]
            if state.action == ACTION_MASK:
                safe_text = cls._mask(safe_prefix, matches, state.tail_pos, emit_len)
            else:
                safe_text = safe_prefix
            tail_next = buffered[emit_len:]
            if tail_next:
                # Resume scanning right after the emitted prefix so the tail is not
                # re-processed: capture the scan state at the emit boundary.
                _, _, captured = matcher.automaton.find_all_matches(
                    state.tail_state,
                    scan_text[:emit_len],
                    capture_at=state.tail_pos + emit_len - 1,
                )
                if captured is not None:
                    state.tail_state = captured
            else:
                state.tail_state = state_after
            state.tail = tail_next
            state.tail_pos += emit_len

        if hits:
            await cls._write_hit_log(
                tenant_id=state.tenant_id,
                business_type=SensitiveWordBusinessType.CHAT_OUTPUT.value,
                direction=DIRECTION_OUTPUT,
                action=state.action,
                hits=hits,
                flow_id=state.flow_id,
                chat_id=state.chat_id,
                user_id=state.user_id,
            )
        return GuardedChunk(safe_text=safe_text, hits=hits, blocked=state.blocked)

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
        matcher = state.matcher
        assert matcher is not None
        buffered = state.tail
        scan_text = buffered if matcher.case_sensitive else buffered.lower()
        _, matches, _ = matcher.automaton.find_all_matches(state.tail_state, scan_text)
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
        )
