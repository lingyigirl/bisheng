"""F076 T-27:开放 API 审计(逐调用 + 管理动作)——有界队列 + 批量落库。

三条硬约束(spec/design 定死,写码时不要"优化"掉):

1. **审计绝不能影响被审的调用**(AC-36):入队 non-blocking,队列满即丢(只记数 +
   限流告警);落库失败只记日志。任何异常都不允许抛回调用链。
2. **不读请求体**(design §5 坑 4):只记依赖/端点已经握在手里的字段。响应体同样
   不读——`/api/v2` 有 SSE 流,任何"缓冲响应取业务码"的做法都会破坏流式。
3. **不写明文**(AC-09 / INV-23):metadata 只放掩码与 id;密钥明文、`token_hash`、
   专用身份的随机口令一律不得出现(整对象序列化 User/ApiCredential 就是踩线)。

落库复用既有 `auditlog` 表与 `AuditLogDao`(design 决策 8,不新增表):批量走
`ainsert_audit_logs`。`open_api.*` **刻意不进** `_UI_VISIBLE_V2_ACTIONS`——审计页
看不到,只落库取证(design §6.1)。

**队列里放纯数据、不放 ORM 对象、不依赖 ContextVar**:请求的租户上下文在依赖
`finally` 里已重置,后台消费端必须自带租户参数;落库在 `bypass_tenant_filter()` 内
按行写显式 `tenant_id`(该上下文下 `before_flush` 不参与,值不会被改写)。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger

from bisheng.core.context.tenant import bypass_tenant_filter
from bisheng.database.models.audit_log import AuditLog, AuditLogDao

#: 队列容量 / 批大小 / 批量窗口:SDD 未定数字(只说"有界、批量、满则丢"),取
#: 能扛突发又不长滞留的折中,已记入 tasks 偏差记录。
QUEUE_MAXSIZE = 1000
BATCH_SIZE = 50
FLUSH_INTERVAL_SECONDS = 2.0
#: 丢包告警限流:队列满时最多每 60s 一条 warning。
DROP_WARN_INTERVAL_SECONDS = 60.0


@dataclass(slots=True)
class _AuditRecord:
    """入队载荷:纯数据(不持有 ORM 对象、不依赖请求上下文)。"""

    tenant_id: int
    operator_id: int
    operator_tenant_id: int
    action: str
    target_type: str | None = None
    target_id: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None
    ip_address: str | None = None
    operator_name: str | None = None
    object_name: str | None = None


_queue: asyncio.Queue[_AuditRecord] | None = None
_consumer: asyncio.Task | None = None
_dropped = 0
_last_drop_warning = 0.0


def _jsonable(value: Any) -> Any:
    """把 metadata 里的值收敛成 JSON 可序列化的形态(datetime/Enum 是常客)。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(one) for one in value]
    return value


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    return {key: _jsonable(value) for key, value in metadata.items() if value is not None}


async def _flush(batch: list[_AuditRecord]) -> None:
    """批量落库;失败只记日志(绝不外抛)。"""
    try:
        rows = [
            AuditLog(
                tenant_id=record.tenant_id,
                operator_id=record.operator_id,
                operator_tenant_id=record.operator_tenant_id,
                action=record.action,
                target_type=record.target_type,
                target_id=record.target_id,
                reason=record.reason,
                audit_metadata=_clean_metadata(record.metadata),
                ip_address=record.ip_address,
                operator_name=record.operator_name,
                object_name=record.object_name,
            )
            for record in batch
        ]
        with bypass_tenant_filter():
            await AuditLogDao.ainsert_audit_logs(rows)
    except Exception:
        logger.exception("open_api: audit flush failed, {} record(s) dropped", len(batch))


async def _consume_forever() -> None:
    """消费循环:攒够一批或窗口到期就落库。"""
    queue = _queue
    if queue is None:
        return
    while True:
        batch: list[_AuditRecord] = []
        try:
            batch.append(await queue.get())
            deadline = time.monotonic() + FLUSH_INTERVAL_SECONDS
            while len(batch) < BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except TimeoutError:
                    break
        except asyncio.CancelledError:
            # 关停:直接丢弃手上的这一批——审计是 best-effort,绝不能在做循环收尾时
            # 发起 DB I/O(会跨事件循环持有 future,污染后续请求)。丢的那批只记日志。
            if batch:
                logger.info("open_api: audit consumer stopped, {} pending record(s) dropped", len(batch))
            raise
        await _flush(batch)


def _get_queue() -> asyncio.Queue[_AuditRecord]:
    """惰性建队列与消费任务:没有审计就不会有后台任务。"""
    global _queue, _consumer
    if _queue is None:
        _queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    if _consumer is None or _consumer.done():
        _consumer = asyncio.create_task(_consume_forever())
    return _queue


def enqueue(record: _AuditRecord) -> None:
    """non-blocking 入队;满则丢并限流告警(AC-36:审计不得改变调用结果)。"""
    global _dropped, _last_drop_warning
    try:
        _get_queue().put_nowait(record)
    except asyncio.QueueFull:
        _dropped += 1
        now = time.monotonic()
        if now - _last_drop_warning >= DROP_WARN_INTERVAL_SECONDS:
            _last_drop_warning = now
            logger.warning("open_api: audit queue full, {} record(s) dropped so far", _dropped)
    except Exception:
        # 连队列都起不来(没有事件循环等)也不能影响调用。
        logger.opt(exception=True).warning("open_api: audit enqueue failed")


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def record_call(
    *,
    tenant_id: int,
    credential_id: int,
    service_account_id: int,
    owner_user_id: int,
    key_mask: str,
    scopes: tuple[str, ...] | list[str],
    http_status: int,
    biz_code: int,
    duration_ms: int,
    ip_address: str | None,
    endpoint: str | None = None,
    method: str | None = None,
    required_scope: str | None = None,
    reason: str | None = None,
    owner_name: str | None = None,
) -> None:
    """逐调用审计(AC-36):掩码 / 服务账号 / 权限位 / HTTP 状态 / 业务码 / 耗时 / IP。"""
    enqueue(
        _AuditRecord(
            tenant_id=tenant_id,
            operator_id=owner_user_id,
            operator_tenant_id=tenant_id,
            operator_name=owner_name,
            action="open_api.call",
            target_type="service_account",
            target_id=str(service_account_id),
            reason=reason,
            ip_address=ip_address,
            metadata={
                # design 决策 1:与真实用户共享身份语义,用 actor_kind + credential_id 区分。
                "actor_kind": "service_account",
                "credential_id": credential_id,
                "key_mask": key_mask,
                "scopes": list(scopes),
                "required_scope": required_scope,
                "http_status": http_status,
                "biz_code": biz_code,
                "duration_ms": duration_ms,
                "endpoint": endpoint,
                "method": method,
            },
        )
    )


def record_management(
    *,
    tenant_id: int,
    operator_id: int,
    operator_tenant_id: int,
    action: str,
    target_type: str,
    target_id: str | int | None,
    target_name: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    ip_address: str | None = None,
    operator_name: str | None = None,
) -> None:
    """管理动作审计(AC-37):含操作者与关键前后取值。

    `before`/`after` 由调用方按各动作自己的字段挑**可安全落库**的键(禁止密钥明文、
    `token_hash`、专用身份口令——见模块 docstring 第 3 条)。
    """
    enqueue(
        _AuditRecord(
            tenant_id=tenant_id,
            operator_id=operator_id,
            operator_tenant_id=operator_tenant_id,
            operator_name=operator_name,
            action=action,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            object_name=target_name,
            ip_address=ip_address,
            metadata={"before": before or {}, "after": after or {}, **(extra or {})},
        )
    )


def record_admin_action(
    *,
    login_user: Any,
    tenant_id: int,
    operator_tenant_id: int,
    action: str,
    target_type: str,
    target_id: str | int | None,
    target_name: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """控制台管理动作的统一入口:操作者口径只在这一处(AC-37)。

    域层不引框架对象——IP 由端点用 `get_request_ip(request)` 取好传进来。
    """
    record_management(
        tenant_id=tenant_id,
        operator_id=int(getattr(login_user, "user_id", 0) or 0),
        operator_tenant_id=operator_tenant_id,
        operator_name=getattr(login_user, "user_name", None),
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        before=before,
        after=after,
        extra=extra,
        ip_address=ip_address,
    )


async def flush_for_test() -> None:
    """测试/关停用:把队列里剩余的记录立刻写掉(生产链路不调用)。"""
    global _dropped
    if _queue is None:
        return
    batch: list[_AuditRecord] = []
    while not _queue.empty():
        try:
            batch.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    await _flush(batch)
    _dropped = 0


def dropped_count() -> int:
    """已丢弃的审计条数(观测用;没有 metrics 系统,靠日志与测试断言)。"""
    return _dropped


def reset_for_test() -> None:
    """测试用:清空模块级状态(队列/消费任务/丢包计数)。

    只在**同一个事件循环**内取消消费任务:跨循环 cancel 会在已关闭的循环上排回调,
    抛出 "Event loop is closed";跨循环时直接丢引用即可(任务随旧循环一起消失)。
    """
    global _queue, _consumer, _dropped, _last_drop_warning
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        _consumer is not None
        and not _consumer.done()
        and loop is not None
        and _consumer.get_loop() is loop
    ):
        _consumer.cancel()
    _queue = None
    _consumer = None
    _dropped = 0
    _last_drop_warning = 0.0
