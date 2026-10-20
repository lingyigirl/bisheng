"""F077 T-08：调度判定纯函数 ``run_planner`` 的单测。

覆盖 spec AC-11 / AC-12 / AC-13 / AC-15 / AC-16 / AC-17 / AC-22 / AC-31 / AC-38
的**决策口径**：四种处置变体（NORMAL / SKIP / FAIL / DEFER）各含正反例、
同步入口（manual / event）的 REJECT、租约边界（``lease_expires_at == now``
两侧）、一次性任务"唯一一次机会"不被重叠/下线/排队消耗、DEFER 的不写回断言。

全部用例使用显式 ``now`` 与显式构造的快照；**文件中不得出现
``datetime.now()``**——判定与真实时钟无关是纯函数的前提（design 决策 4）。
时间一律显式 ``ZoneInfo("Asia/Shanghai")``（AC-09 的服务器时区口径）。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from bisheng.scheduled_task.domain.models.scheduled_task import RunStatus, ScheduleType, TaskStatus
from bisheng.scheduled_task.domain.services.trigger_guard import IdentityState
from bisheng.scheduled_task.domain.services.run_planner import (
    AppState,
    LeaseState,
    PlanDecision,
    PlanReason,
    PlanVariant,
    RecheckOutcome,
    TaskSnapshot,
    TriggerKind,
    cap_room,
    classify,
    decide_due,
    is_catchup,
    lease_state,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 16, 9, 0, 0, tzinfo=TZ)
SLOT = datetime(2026, 10, 16, 8, 55, 0, tzinfo=TZ)  # 本应触发的槽位（已到期、早于 now）
NEXT = datetime(2026, 10, 16, 9, 5, 0, tzinfo=TZ)  # 注入的 next_fire 返回值（固定）
PERIOD = timedelta(minutes=5)



def _ok_recheck():
    """全绿复检（显式构造——trigger_guard 的 DTO 刻意不给"OK"默认值，避免 fail-open）。"""
    return RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.OK)

def _cron_snapshot(**overrides) -> TaskSnapshot:
    base = {"id": 1, "schedule_type": ScheduleType.CRON, "next_run_at": SLOT}
    return TaskSnapshot(**(base | overrides))


def _once_snapshot(**overrides) -> TaskSnapshot:
    base = {"id": 2, "schedule_type": ScheduleType.ONCE, "next_run_at": SLOT}
    return TaskSnapshot(**(base | overrides))


def _fire_spy(value: datetime = NEXT):
    """注入的 next_fire：记录收到的锚点并返回固定值（planner 不得自己算 cron）。"""
    calls: list[datetime] = []

    def _fire(anchor: datetime) -> datetime:
        calls.append(anchor)
        return value

    return _fire, calls


def _no_fire(*_args, **_kwargs):
    """不允许被调用的 next_fire——keep/DEFER/REJECT 路径一旦调用即测试失败。"""
    raise AssertionError("next_fire must not be called on this path")


def _assert_no_writeback(decision: PlanDecision) -> None:
    """DEFER / REJECT 的"什么都不写"断言（"不消耗唯一机会"的落点）。"""
    assert decision.occupy_lease is False
    assert decision.next_run_at is None
    assert decision.last_planned_at is None
    assert decision.last_status is None
    assert decision.last_error is None
    assert decision.count_failure is False
    assert decision.disable is False
    assert decision.has_plan_writeback is False


# ── 租约三相与边界（AC-15 的互斥判据）────────────────────────────


def test_lease_state_free_when_no_lease():
    assert lease_state(_cron_snapshot(), NOW) == LeaseState.FREE


@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [
        (NOW + timedelta(seconds=1), LeaseState.HELD),
        (NOW, LeaseState.EXPIRED),  # 边界：== now 即已过期
        (NOW - timedelta(seconds=1), LeaseState.EXPIRED),
    ],
)
def test_lease_state_boundary_around_now(expires_at, expected):
    assert lease_state(_cron_snapshot(lease_expires_at=expires_at), NOW) == expected


def test_lease_expired_at_now_is_treated_as_free_by_classify():
    """租约自 now 起已失效 → 不构成重叠（claim 的 <= :now 口径；回收偶发没跑也不永久互斥）。"""
    fire, calls = _fire_spy()
    decision = classify(_cron_snapshot(lease_expires_at=NOW), NOW, _ok_recheck(), cap_room=5, next_fire=fire)
    assert decision.variant == PlanVariant.NORMAL
    assert calls == [NOW]


# ── 到期判定（AC-10 的扫描依据）──────────────────────────────────


@pytest.mark.parametrize(
    ("status", "next_run_at", "expected"),
    [
        (TaskStatus.ENABLED, SLOT, True),
        (TaskStatus.ENABLED, NOW, True),  # 边界：== now 即到期
        (TaskStatus.ENABLED, NOW + timedelta(seconds=1), False),
        (TaskStatus.ENABLED, None, False),
        (TaskStatus.DISABLED, SLOT, False),
    ],
)
def test_decide_due(status, next_run_at, expected):
    snapshot = _cron_snapshot(status=status, next_run_at=next_run_at)
    assert decide_due(snapshot, NOW) is expected


# ── NORMAL（正常占用）────────────────────────────────────────────


def test_normal_cron_advances_next_run_at_anchored_at_now():
    fire, calls = _fire_spy()
    decision = classify(_cron_snapshot(), NOW, _ok_recheck(), cap_room=3, next_fire=fire)
    assert decision.variant == PlanVariant.NORMAL
    assert decision.occupy_lease is True
    assert decision.keep_next_run_at is False
    assert calls == [NOW]  # 锚定注入的 now（跨 N 个周期只补跑一次），而非槽位
    assert decision.next_run_at == NEXT  # 用注入的 next_fire，不自算 cron
    assert decision.last_planned_at == SLOT  # 留痕"本应触发"的槽位（AC-10 对账）
    assert decision.last_status is None  # 本槽未被记成 skipped/failed
    assert decision.count_failure is False
    assert decision.notify is False
    assert decision.disable is False
    assert decision.reason is None
    assert decision.has_plan_writeback is True


def test_normal_once_keeps_plan_and_never_calls_next_fire():
    decision = classify(_once_snapshot(), NOW, _ok_recheck(), cap_room=3, next_fire=_no_fire)
    assert decision.variant == PlanVariant.NORMAL
    assert decision.occupy_lease is True
    assert decision.keep_next_run_at is True  # 唯一一次机会：不推进计划（AC-08）
    assert decision.next_run_at is None
    assert decision.last_planned_at is None


def test_normal_manual_keeps_plan():
    decision = classify(
        _cron_snapshot(),
        NOW,
        _ok_recheck(),
        cap_room=3,
        trigger_kind=TriggerKind.MANUAL,
        next_fire=_no_fire,
    )
    assert decision.variant == PlanVariant.NORMAL
    assert decision.keep_next_run_at is True  # AC-17：不改变既定计划与「下次执行」
    assert decision.occupy_lease is True
    assert decision.has_plan_writeback is True  # 占租约本身就是写回


# ── SKIP（周期跳过：不占租约、消耗本槽、不计失败）─────────────────


def test_skip_cron_on_held_lease():
    fire, calls = _fire_spy()
    snapshot = _cron_snapshot(lease_expires_at=NOW + timedelta(minutes=30))
    decision = classify(snapshot, NOW, _ok_recheck(), cap_room=3, next_fire=fire)
    assert decision.variant == PlanVariant.SKIP
    assert decision.reason == PlanReason.OVERLAP
    assert decision.occupy_lease is False
    assert calls == [NOW]
    assert decision.next_run_at == NEXT
    assert decision.last_planned_at == SLOT
    assert decision.last_status == RunStatus.SKIPPED
    assert decision.last_error == "overlap"
    assert decision.count_failure is False  # AC-11：跳过不计失败
    assert decision.notify is False


def test_skip_cron_when_app_offline():
    fire, _ = _fire_spy()
    decision = classify(_cron_snapshot(), NOW, RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK), cap_room=3, next_fire=fire)
    assert decision.variant == PlanVariant.SKIP
    assert decision.reason == PlanReason.APP_OFFLINE
    assert decision.last_status == RunStatus.SKIPPED
    assert decision.last_error == "app_offline"
    assert decision.count_failure is False  # AC-16：不算失败
    assert decision.mark_offline is False  # 等待上线标记只属于一次性任务


# ── DEFER（不消费：什么都不写）──────────────────────────────────


def test_defer_once_on_held_lease_does_not_consume_the_single_chance():
    snapshot = _once_snapshot(lease_expires_at=NOW + timedelta(minutes=30))
    decision = classify(snapshot, NOW, _ok_recheck(), cap_room=3, next_fire=_no_fire)
    assert decision.variant == PlanVariant.DEFER
    assert decision.reason == PlanReason.OVERLAP
    assert decision.mark_offline is False
    assert decision.keep_next_run_at is False
    _assert_no_writeback(decision)


def test_defer_once_when_app_offline_sets_blocked_without_consuming():
    decision = classify(
        _once_snapshot(), NOW, RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK), cap_room=3, next_fire=_no_fire
    )
    assert decision.variant == PlanVariant.DEFER
    assert decision.reason == PlanReason.APP_OFFLINE
    assert decision.mark_offline is True  # 调用方幂等置 blocked_reason=app_offline（AC-16）
    _assert_no_writeback(decision)  # 不占租约、不推进 next_run_at、不动 last_*


@pytest.mark.parametrize("snapshot_factory", [_cron_snapshot, _once_snapshot])
def test_defer_when_cap_room_full_keeps_the_round(snapshot_factory):
    decision = classify(snapshot_factory(), NOW, _ok_recheck(), cap_room=0, next_fire=_no_fire)
    assert decision.variant == PlanVariant.DEFER
    assert decision.reason == PlanReason.CAP_FULL
    assert decision.mark_offline is False
    _assert_no_writeback(decision)


def test_normal_contrast_when_cap_room_positive():
    # DEFER(cap_full) 的反例：只要还有一个名额就走 NORMAL
    fire, _ = _fire_spy()
    decision = classify(_cron_snapshot(), NOW, _ok_recheck(), cap_room=1, next_fire=fire)
    assert decision.variant == PlanVariant.NORMAL


def test_cap_room_helper_clamps_at_zero():
    assert cap_room(3, 10) == 7
    assert cap_room(10, 10) == 0
    assert cap_room(12, 10) == 0


# ── REJECT（同步入口：拒绝且不写任何东西）────────────────────────


def test_manual_rejects_on_held_lease_instead_of_skip():
    snapshot = _cron_snapshot(lease_expires_at=NOW + timedelta(minutes=30))
    decision = classify(
        snapshot, NOW, _ok_recheck(), cap_room=3, trigger_kind=TriggerKind.MANUAL, next_fire=_no_fire
    )
    assert decision.variant == PlanVariant.REJECT
    assert decision.reason == PlanReason.OVERLAP  # 端点映射 11909
    _assert_no_writeback(decision)


def test_event_rejects_on_held_lease_instead_of_idempotent_accept():
    snapshot = _cron_snapshot(lease_expires_at=NOW + timedelta(minutes=30))
    decision = classify(snapshot, NOW, _ok_recheck(), cap_room=3, trigger_kind=TriggerKind.EVENT, next_fire=_no_fire)
    assert decision.variant == PlanVariant.REJECT  # AC-38：拒绝而非幂等接受
    assert decision.reason == PlanReason.OVERLAP  # 端点映射 11910
    _assert_no_writeback(decision)


@pytest.mark.parametrize("trigger_kind", [TriggerKind.MANUAL, TriggerKind.EVENT])
def test_manual_and_event_reject_when_cap_room_full(trigger_kind):
    decision = classify(
        _cron_snapshot(), NOW, _ok_recheck(), cap_room=0, trigger_kind=trigger_kind, next_fire=_no_fire
    )
    assert decision.variant == PlanVariant.REJECT
    assert decision.reason == PlanReason.CAP_FULL  # 端点映射 11909
    _assert_no_writeback(decision)


@pytest.mark.parametrize("trigger_kind", [TriggerKind.MANUAL, TriggerKind.EVENT])
def test_manual_and_event_reject_when_app_offline(trigger_kind):
    decision = classify(
        _cron_snapshot(),
        NOW,
        RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.OK),
        cap_room=3,
        trigger_kind=trigger_kind,
        next_fire=_no_fire,
    )
    assert decision.variant == PlanVariant.REJECT
    assert decision.reason == PlanReason.APP_OFFLINE  # 端点映射 11907
    _assert_no_writeback(decision)


# ── FAIL（记账失败）──────────────────────────────────────────────


def test_fail_identity_unavailable_counts_failure():
    fire, calls = _fire_spy()
    decision = classify(
        _cron_snapshot(),
        NOW,
        RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP),
        cap_room=3,
        next_fire=fire,
    )
    assert decision.variant == PlanVariant.FAIL
    assert decision.reason == PlanReason.IDENTITY_UNAVAILABLE
    assert decision.occupy_lease is False
    assert decision.last_status == RunStatus.FAILED
    # last_error 写**细分原因**（trigger_guard.describe_failure），11905 才能指到具体哪一项失权
    assert decision.last_error == "no_use_app"
    assert decision.detail == "no_use_app"
    assert decision.count_failure is True  # AC-22：计入连续失败
    assert decision.notify is True  # 首败一条由调用方按计数转移决定
    assert decision.disable is False
    assert calls == [NOW]  # 周期任务推进计划，下轮按计划自然重试
    assert decision.next_run_at == NEXT
    assert decision.last_planned_at == SLOT


@pytest.mark.parametrize("trigger_kind", [TriggerKind.MANUAL, TriggerKind.EVENT])
def test_sync_entry_rejects_identity_unavailable_without_writeback(trigger_kind):
    """失败记账的单一写入者 = 计划轮次（W2 复核裁定）。

    事件触发 / 立即运行遇身份不可用时一律 REJECT（端点回 11905）、不写任务行——
    否则同一故障会被"事件 + 下一轮扫描"重复计数，提前触发 AC-55 的自动停用。
    """
    decision = classify(
        _cron_snapshot(),
        NOW,
        RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP),
        cap_room=3,
        trigger_kind=trigger_kind,
        next_fire=_no_fire,
    )
    assert decision.variant == PlanVariant.REJECT
    assert decision.reason == PlanReason.IDENTITY_UNAVAILABLE
    assert decision.detail == "no_use_app"  # 细分原因留给 11905 文案/日志
    _assert_no_writeback(decision)


def test_fail_app_deleted_disables_and_notifies():
    fire, _ = _fire_spy()
    decision = classify(_cron_snapshot(), NOW, RecheckOutcome(app_state=AppState.DELETED, identity_state=IdentityState.OK), cap_room=3, next_fire=fire)
    assert decision.variant == PlanVariant.FAIL
    assert decision.reason == PlanReason.APP_DELETED
    assert decision.disable is True  # AC-31：fail-closed 且自动停用
    assert decision.notify is True  # AC-31 明文要求通知
    assert decision.last_error == "app_deleted"
    assert decision.next_run_at == NEXT


def test_fail_app_deleted_once_keeps_next_run_at():
    decision = classify(
        _once_snapshot(), NOW, RecheckOutcome(app_state=AppState.DELETED, identity_state=IdentityState.OK), cap_room=3, next_fire=_no_fire
    )
    assert decision.variant == PlanVariant.FAIL
    assert decision.disable is True
    assert decision.keep_next_run_at is True  # 一次性任务没有下一跳，推进无从谈起
    assert decision.next_run_at is None


def test_fail_contrast_identity_ok_walks_normal():
    # FAIL 的反例：身份可用时不得记账失败
    fire, _ = _fire_spy()
    decision = classify(_cron_snapshot(), NOW, _ok_recheck(), cap_room=3, next_fire=fire)
    assert decision.variant == PlanVariant.NORMAL


# ── 复检原因的优先级（写死在 classify 的唯一口径）────────────────


def test_identity_failure_takes_precedence_over_offline():
    fire, _ = _fire_spy()
    decision = classify(
        _cron_snapshot(),
        NOW,
        RecheckOutcome(app_state=AppState.OFFLINE, identity_state=IdentityState.NO_USE_APP),
        cap_room=3,
        next_fire=fire,
    )
    assert decision.variant == PlanVariant.FAIL
    assert decision.reason == PlanReason.IDENTITY_UNAVAILABLE


def test_deleted_takes_precedence_over_identity_failure():
    fire, _ = _fire_spy()
    decision = classify(
        _cron_snapshot(),
        NOW,
        RecheckOutcome(app_state=AppState.DELETED, identity_state=IdentityState.NO_USE_APP),
        cap_room=3,
        next_fire=fire,
    )
    assert decision.variant == PlanVariant.FAIL
    assert decision.reason == PlanReason.APP_DELETED
    assert decision.disable is True


# ── 漏跑合并判定（is_catchup，审计/日志口径）────────────────────


@pytest.mark.parametrize(
    ("next_run_at", "expected"),
    [
        (NOW - PERIOD + timedelta(seconds=1), False),  # 迟到不足一个周期：正常轮
        (NOW - PERIOD, True),  # 边界：迟到整一个周期即补跑轮（后继槽位也已到期）
        (NOW - 7 * PERIOD, True),  # 跨 N 个周期仍只产出这一轮
    ],
)
def test_is_catchup_boundary(next_run_at, expected):
    assert is_catchup(_cron_snapshot(next_run_at=next_run_at), NOW, PERIOD) is expected


def test_is_catchup_false_for_once_and_missing_next_run_at():
    assert is_catchup(_once_snapshot(), NOW, PERIOD) is False
    assert is_catchup(_cron_snapshot(next_run_at=None), NOW, PERIOD) is False


def test_is_catchup_rejects_non_positive_period_hint():
    with pytest.raises(ValueError):
        is_catchup(_cron_snapshot(), NOW, timedelta(0))


# ── next_fire 注入契约（缺失而需推进时必须炸，不允许写 NULL）──────


def test_classify_requires_next_fire_to_advance_a_cron_task():
    with pytest.raises(ValueError):
        classify(_cron_snapshot(), NOW, _ok_recheck(), cap_room=3)  # NORMAL 推进
    with pytest.raises(ValueError):
        classify(
            _cron_snapshot(lease_expires_at=NOW + timedelta(minutes=1)),
            NOW,
            _ok_recheck(),
            cap_room=3,  # SKIP 同样推进
        )
    with pytest.raises(ValueError):
        classify(
            _cron_snapshot(),
            NOW,
            RecheckOutcome(app_state=AppState.OK, identity_state=IdentityState.NO_USE_APP),
            cap_room=3,  # FAIL 同样推进
        )


def test_once_and_manual_do_not_require_next_fire():
    # 上一条的反例：keep 语义的路径不推进计划，因此无需 next_fire
    decision = classify(_once_snapshot(), NOW, _ok_recheck(), cap_room=3)
    assert decision.variant == PlanVariant.NORMAL
    decision = classify(_cron_snapshot(), NOW, _ok_recheck(), cap_room=3, trigger_kind=TriggerKind.MANUAL)
    assert decision.variant == PlanVariant.NORMAL
