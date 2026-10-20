"""F077 调度判定纯函数（design 决策 4 的「纯决策」，T-08 交付物）。

**职责**：把「一轮触发该不该跑、跑前/跳后写什么」收敛成一次纯函数调用——
输入是本模块的 DTO（``TaskSnapshot`` / ``RecheckOutcome``）与显式 ``now``，
输出是 ``PlanDecision``（四种处置变体之一 + 写回内容，或同步入口的 REJECT）。
调用方 ``run_service``（T-10）只负责「按决策执行单条原子写 + 派发」。

**为什么是纯函数**（design 决策 4 的编排原则）：
- ORM 对象、DB 会话、Redis、celery app 一律不进入判定（本模块不 import 它们
  任何 DB/连接件，只复用表模块的 StrEnum 常量）——单测直调即可锁定
  AC-11/12/13/15/16/17/22/31/38 的决策口径，无需任何中间件；
- ``now`` / ``next_fire`` 一律显式注入，**绝不读真实时钟**——AC-10 的偏差对账
  与 AC-13 的漏跑合并都依赖「同一锚点只算一次」的可复现性；
- 入参出参的 datetime 一律 tz-aware（调用方读库的 naive 本地时间后补
  ``ZoneInfo("Asia/Shanghai")`` 再传入，design 决策 7 的时区边界）。

**四种处置变体速查**（详表见 design §3 决策 4）：

- ``NORMAL``（正常占用）：复检通过且名额未满——占租约；周期任务把
  ``next_run_at`` 推进到 ``next_fire(now)``（**锚定 now**：跨 N 个周期只补跑
  一次，天然免补跑风暴，AC-13），并留痕本槽位 ``last_planned_at``；一次性任务
  与手动立即运行不推进计划（``keep_next_run_at``）。
- ``SKIP``（周期跳过）：上一轮未结束（``overlap``）或应用下线（``app_offline``）
  ——不占租约、消耗本槽、记 ``last_status='skipped'``；不计失败、不通知。
- ``FAIL``（记账失败）：运行身份不可用或应用已删除——不占租约、记
  ``last_status='failed'`` 并推进连续失败计数；应用已删除额外自动停用（AC-31）。
- ``DEFER``（不消费、下轮再试）：一次性任务遇重叠/下线，或全局名额已满——
  **什么都不写**（"只有一次机会"不因重叠、等待或排队被消耗）；一次性任务遇
  下线时另给 ``mark_offline=True``，由调用方幂等置 ``blocked_reason``（AC-16）。
- ``REJECT``：同步入口（manual / event）的拒绝——同样不写任何东西，由端点
  返回可区分的 119 段错误（人触发的动作要即时反馈，不排队，AC-17/AC-38）。

**判定优先级**写死在本模块一处：应用已删除 → 身份不可用 → 应用下线 →
租约未释放（互斥）→ 全局名额 → NORMAL。调用方不得另立口径。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from bisheng.scheduled_task.domain.models.scheduled_task import RunStatus, ScheduleType, TaskStatus
from bisheng.scheduled_task.domain.services.trigger_guard import (
    AppState,
    RecheckOutcome,
    describe_failure,
)


class LeaseState(StrEnum):
    """单任务互斥状态（``lease_expires_at`` 与 ``now`` 的三相判别）。

    ``FREE``（无租约）与 ``EXPIRED``（租约已过期）在**互斥角度都视同空闲**
    ——这正是 claim 的 WHERE 条件（``lease_expires_at IS NULL OR <= :now``，
    design 决策 3 的「第二道保险」）。单独区分 ``EXPIRED`` 是为了让回收/审计
    能识别「有一轮死掉了」，不把"从没跑过"与"跑挂了"混成一种状态。
    """

    FREE = "free"
    HELD = "held"
    EXPIRED = "expired"


class TriggerKind(StrEnum):
    """触发入口（三入口共用同一条编排链，design §4.1）。"""

    SCHEDULED = "scheduled"
    EVENT = "event"
    MANUAL = "manual"


class PlanVariant(StrEnum):
    """处置变体。前四种是 design 决策 4 定稿的「四种处置变体」；``REJECT`` 是
    manual / event 同步入口的拒绝结果——不写库，由端点回 119 段错误码。
    """

    NORMAL = "normal"
    SKIP = "skip"
    FAIL = "fail"
    DEFER = "defer"
    REJECT = "reject"


class PlanReason(StrEnum):
    """决策原因（稳定的机器可读标识，SKIP/FAIL 时写入 ``last_error`` 列）。

    ``CAP_FULL`` 只出现在 DEFER / REJECT 上（不写库），仅用于调用方选错误码
    与日志。
    """

    OVERLAP = "overlap"
    APP_OFFLINE = "app_offline"
    APP_DELETED = "app_deleted"
    IDENTITY_UNAVAILABLE = "identity_unavailable"
    CAP_FULL = "cap_full"


@dataclass(frozen=True)
class TaskSnapshot:
    """决策所需的任务策略字段快照（**不传 ORM 对象**，避免纯函数被会话绑定）。

    只取判定要用的列；``created_by`` / ``run_identity_user_id`` /
    ``service_account_id`` 本模块不用，但保留在快照里供调用方在派发/通知时
    与决策一并流转（同一快照贯穿一轮）。
    """

    id: int
    schedule_type: ScheduleType = ScheduleType.CRON
    next_run_at: datetime | None = None
    lease_expires_at: datetime | None = None
    status: TaskStatus = TaskStatus.ENABLED
    last_status: RunStatus | None = None
    last_planned_at: datetime | None = None
    blocked_reason: str | None = None
    name: str = ""
    created_by: int = 0
    run_identity_user_id: int = 0
    service_account_id: int = 0


@dataclass(frozen=True)
class PlanDecision:
    """一次触发的决策结果（变体 + 写回内容）。

    写回字段仅在 ``NORMAL`` / ``SKIP`` / ``FAIL`` 上有意义；``DEFER`` 与
    ``REJECT`` 必须整体保持默认值（``has_plan_writeback`` 为 ``False`` 是它们
    的语义核心——"什么都不写"）。

    - ``keep_next_run_at``：True 表示**调用方不得写** ``next_run_at`` /
      ``last_planned_at`` 两列（列保持原值）——一次性任务与**任何非计划轮次
      触发**（manual / event）的 keep 语义；它不是写回，是"禁写"标志。
    - ``detail``：复检失败的**细分原因**（``trigger_guard.describe_failure``
      的短标记，如 ``app_offline,owner_privileged``）。``reason`` 是稳定的机器
      token，``detail`` 供任务行 ``last_error`` 与 11905 文案用——只存标识与
      摘要，不含任何正文（AC-51/INV-11）。
    - ``count_failure``：调用方是否推进 ``consecutive_fail_count``（**只有计划
      轮次会写失败状态**：同步入口一律 REJECT、不记账，见下）。
    - ``notify``：本决策是否属于「按 AC-54 口径可通知」的失败——首败一条由
      调用方按计数转移（0→1）决定，planner 只给标志。
    - ``mark_offline``：DEFER 专用——调用方应幂等置 ``blocked_reason='app_offline'``
      （AC-16 的"等待上线"标注）；它只写非消费性阻塞标记，不算计划写回。

    **失败记账的单一写入者**：`FAIL` 只可能由**计划轮次**产生。事件触发与立即
    运行遇身份不可用 / 应用已删除时一律回 ``REJECT``（同步 11905/11907），不写
    任务行——否则同一故障会被多个入口重复计数（一次事件 + 下一轮扫描），提前
    触发 AC-55 的自动停用。任务的健康由计划轮次在一个 tick 内必然判到。
    """

    variant: PlanVariant
    trigger_kind: TriggerKind
    reason: PlanReason | None = None
    detail: str = ""
    occupy_lease: bool = False
    keep_next_run_at: bool = False
    next_run_at: datetime | None = None
    last_planned_at: datetime | None = None
    last_status: RunStatus | None = None
    last_error: str | None = None
    count_failure: bool = False
    notify: bool = False
    disable: bool = False
    mark_offline: bool = False

    @property
    def has_plan_writeback(self) -> bool:
        """该决策是否携带对任务行计划状态的写回（DEFER / REJECT 恒为 False）。

        ``keep_next_run_at`` 是"禁写"标志、``mark_offline`` 只置非消费性的
        ``blocked_reason``，两者都不计入。
        """
        return (
            self.occupy_lease
            or self.next_run_at is not None
            or self.last_planned_at is not None
            or self.last_status is not None
            or self.last_error is not None
            or self.count_failure
            or self.disable
        )


def lease_state(snapshot: TaskSnapshot, now: datetime) -> LeaseState:
    """租约三相判别（**单任务互斥的唯一判据**）。

    - ``lease_expires_at is None`` → ``FREE``；
    - ``<= now`` → ``EXPIRED``（互斥角度视同空闲——回收偶发没跑也不造成永久
      互斥，design 决策 3）；
    - ``> now`` → ``HELD``（有未释放的轮次，不可再占）。
    """
    if snapshot.lease_expires_at is None:
        return LeaseState.FREE
    if snapshot.lease_expires_at <= now:
        return LeaseState.EXPIRED
    return LeaseState.HELD


def decide_due(snapshot: TaskSnapshot, now: datetime) -> bool:
    """计划轮次准入：启用中 + ``next_run_at`` 非空且已到期（扫描唯一依据）。

    到期边界含等号（``== now`` 即到期），与 DAO 的 ``next_run_at <= now`` 查询
    以及 claim 的 WHERE 条件同口径。
    """
    return snapshot.status == TaskStatus.ENABLED and snapshot.next_run_at is not None and snapshot.next_run_at <= now


def cap_room(running: int, cap: int) -> int:
    """本平台剩余并发名额 = ``max(cap - running, 0)``。

    ``running`` 由 DAO 的 ``acount_running(now)`` 数出——它只数**未过期的
    租约**（过期租约不计，计数自愈，与互斥判据同源）；名额不为负，供
    ``classify`` 直接使用。
    """
    return max(cap - running, 0)


def is_catchup(snapshot: TaskSnapshot, now: datetime, period_hint: timedelta) -> bool:
    """判断本轮是否为「补跑轮」（AC-13 的漏跑合并，仅供审计/日志区分）。

    漏跑合并本身由「锚定 ``now`` 的 ``next_fire``」结构性满足——跨 N 个周期
    也只产出这一轮、且下一跳从 ``now`` 起算，不会再排队补发。本函数只回答
    "这一轮迟到了几个周期"：``now - next_run_at >= period_hint`` 即视为补跑轮
    （迟到整整一个周期时，后继槽位也已到期，"本轮"已晚于计划）。

    非周期任务（``once``）与无 ``next_run_at`` 的快照返回 ``False``；
    ``period_hint`` 必须为正——由调用方从 cron 表达式推导（如 ``*/5`` → 5 分钟），
    推导不出（列表/范围等不规则表达式）时不应调用本函数。
    """
    if period_hint <= timedelta(0):
        raise ValueError("period_hint must be positive")
    if snapshot.schedule_type != ScheduleType.CRON or snapshot.next_run_at is None:
        return False
    return now - snapshot.next_run_at >= period_hint


def classify(
    snapshot: TaskSnapshot,
    now: datetime,
    recheck: RecheckOutcome,
    *,
    cap_room: int,
    trigger_kind: TriggerKind = TriggerKind.SCHEDULED,
    next_fire: Callable[[datetime], datetime] | None = None,
) -> PlanDecision:
    """决定本轮触发进哪个处置变体，并给出该变体的写回内容。

    **参数**：
    - ``snapshot``：任务策略字段快照（非 ORM 对象）；
    - ``now``：本轮锚点（tz-aware，Asia/Shanghai）；计划轮次的「应触发槽位」
      是 ``snapshot.next_run_at``，不是 now；
    - ``recheck``：T-09 复检结果（应用状态 + 身份可用）；
    - ``cap_room``：本平台剩余并发名额（``cap_room()`` 算出，0 即已满）；
    - ``trigger_kind``：触发入口；manual / event 在重叠与名额问题上一律
      ``REJECT``（同步拒绝，不回退为 SKIP/DEFER，AC-17/AC-38）；
    - ``next_fire``：把锚点映射到「下一次计划时刻」的注入回调（周期任务推进
      ``next_run_at`` 时必须提供——由 ``cron_schedule.next_runs(anchor, 1)``
      实现；一次性任务恒不调用）。缺失而需推进时直接 ``ValueError``——宁可
      炸在调用点，也不能把 NULL 写进 ``next_run_at`` 造成任务永久静默停摆。

    **返回**：``PlanDecision``；调用方按 ``variant`` 执行对应的**单条原子写**
    （claim / skip / fail / blocked 标记），``DEFER`` 与 ``REJECT`` 不写任何
    东西（``has_plan_writeback`` 为 ``False``）。

    **前提**：调度轮次的 due/enabled 过滤由调用方先做（``decide_due``，扫描
    查询已按此收敛）；manual/event 的任务不存在/停用防枚举、频控与防重放属
    端点层，不在本函数。
    """
    # 优先级 1：应用已删除——sync 入口只拒绝；计划轮次 fail-closed 并自动停用
    # （AC-31）。删除是比身份失效更强的事实，先判它保证任务被停用而不是反复记账。
    if recheck.app_state == AppState.DELETED:
        if trigger_kind in (TriggerKind.MANUAL, TriggerKind.EVENT):
            return PlanDecision(
                variant=PlanVariant.REJECT,
                trigger_kind=trigger_kind,
                reason=PlanReason.APP_DELETED,
                detail=describe_failure(recheck),
            )
        return _fail(
            snapshot,
            now,
            trigger_kind,
            next_fire,
            PlanReason.APP_DELETED,
            disable=True,
            detail=describe_failure(recheck),
        )

    # 优先级 2：运行身份不可用（不存在/停用/被提权/无 use_app）——计划轮次记账失败
    # （AC-22）；sync 入口（manual / event）只拒绝、不写库（失败记账的单一写入者，
    # 避免同一故障被两个入口重复计数——见 PlanDecision docstring）。
    # 放在「应用下线」之前：即使应用随后重新上线，身份问题不修则每轮必败，
    # 应当走可见的失败/通知路径而不是被下线的"跳过"静默吞掉。
    if not recheck.identity_ok:
        if trigger_kind in (TriggerKind.MANUAL, TriggerKind.EVENT):
            return PlanDecision(
                variant=PlanVariant.REJECT,
                trigger_kind=trigger_kind,
                reason=PlanReason.IDENTITY_UNAVAILABLE,
                detail=describe_failure(recheck),
            )
        return _fail(
            snapshot,
            now,
            trigger_kind,
            next_fire,
            PlanReason.IDENTITY_UNAVAILABLE,
            disable=False,
            detail=describe_failure(recheck),
        )

    # 优先级 3：应用下线。周期 SKIP；一次性 DEFER（不消耗唯一机会，AC-16）；
    # manual/event 拒绝（AC-16：人触发要即时反馈）。
    if recheck.app_state == AppState.OFFLINE:
        if trigger_kind in (TriggerKind.MANUAL, TriggerKind.EVENT):
            return PlanDecision(variant=PlanVariant.REJECT, trigger_kind=trigger_kind, reason=PlanReason.APP_OFFLINE)
        if snapshot.schedule_type == ScheduleType.ONCE:
            return PlanDecision(
                variant=PlanVariant.DEFER,
                trigger_kind=trigger_kind,
                reason=PlanReason.APP_OFFLINE,
                mark_offline=True,  # 调用方幂等置 blocked_reason；上线后由 NORMAL 占租约时清除
            )
        return _skip(snapshot, now, trigger_kind, next_fire, PlanReason.APP_OFFLINE)

    # 优先级 4：上一轮未结束（租约 HELD——互斥的唯一判据；EXPIRED 视同空闲）。
    # 放在「名额」之前：周期任务的重叠由 AC-11 明确"跳过本轮"，该轮不再保留，
    # 名额状态无关紧要。
    if lease_state(snapshot, now) == LeaseState.HELD:
        if trigger_kind in (TriggerKind.MANUAL, TriggerKind.EVENT):
            # AC-17/AC-38：同步入口拒绝而非排队；拒绝不写库
            return PlanDecision(variant=PlanVariant.REJECT, trigger_kind=trigger_kind, reason=PlanReason.OVERLAP)
        if snapshot.schedule_type == ScheduleType.ONCE:
            return PlanDecision(variant=PlanVariant.DEFER, trigger_kind=trigger_kind, reason=PlanReason.OVERLAP)
        return _skip(snapshot, now, trigger_kind, next_fire, PlanReason.OVERLAP)

    # 优先级 5：全局并发名额已满。计划轮次一律 DEFER——不丢弃该轮，下轮再试
    # （AC-12：周期与一次性同样适用）；manual/event 拒绝（AC-17）。
    if cap_room <= 0:
        if trigger_kind in (TriggerKind.MANUAL, TriggerKind.EVENT):
            return PlanDecision(variant=PlanVariant.REJECT, trigger_kind=trigger_kind, reason=PlanReason.CAP_FULL)
        return PlanDecision(variant=PlanVariant.DEFER, trigger_kind=trigger_kind, reason=PlanReason.CAP_FULL)

    # 复检通过且有名额——正常占用
    return _normal(snapshot, now, trigger_kind, next_fire)


def _keep_plan(snapshot: TaskSnapshot, trigger_kind: TriggerKind) -> bool:
    """是否保持既定计划不推进（``keep_next_run_at=True`` 的语义）。

    三种情形（design 决策 4 + W2 复核裁定）：
    - **一次性任务**：``next_run_at`` 即 ``run_at``（唯一一次机会），claim 时不
      推进；终态时才自动停用（AC-08）。
    - **任何非计划轮次触发**（manual / **event**）：不改变既定触发计划与
      「下次执行」时间——事件触发是"额外跑一轮"，不是"提前消费掉计划槽位"。
      （AC-17 只写到 manual；把 event 并入是同一条原则：只有计划轮次推进计划。）
    """
    return snapshot.schedule_type == ScheduleType.ONCE or trigger_kind in (
        TriggerKind.MANUAL,
        TriggerKind.EVENT,
    )


def _next_fire_value(now: datetime, next_fire: Callable[[datetime], datetime] | None) -> datetime:
    """以 ``now`` 为锚调用注入的 ``next_fire``（planner 不自己算 cron）。"""
    if next_fire is None:
        raise ValueError("next_fire is required to advance next_run_at of a cron task")
    return next_fire(now)


def _normal(
    snapshot: TaskSnapshot,
    now: datetime,
    trigger_kind: TriggerKind,
    next_fire: Callable[[datetime], datetime] | None,
) -> PlanDecision:
    """NORMAL：占租约；周期任务消耗本槽并锚定 now 推进下一跳（AC-13 只补一次）。"""
    if _keep_plan(snapshot, trigger_kind):
        return PlanDecision(
            variant=PlanVariant.NORMAL,
            trigger_kind=trigger_kind,
            occupy_lease=True,
            keep_next_run_at=True,
        )
    return PlanDecision(
        variant=PlanVariant.NORMAL,
        trigger_kind=trigger_kind,
        occupy_lease=True,
        next_run_at=_next_fire_value(now, next_fire),
        last_planned_at=snapshot.next_run_at,  # 本应触发的槽位（AC-10 偏差对账）
    )


def _skip(
    snapshot: TaskSnapshot,
    now: datetime,
    trigger_kind: TriggerKind,
    next_fire: Callable[[datetime], datetime] | None,
    reason: PlanReason,
) -> PlanDecision:
    """SKIP：只发生在周期任务上（once/manual/event 分别走 DEFER/REJECT）。

    不占租约、消耗本槽、记跳过原因；不计失败、不通知（AC-11/AC-16）。
    """
    return PlanDecision(
        variant=PlanVariant.SKIP,
        trigger_kind=trigger_kind,
        reason=reason,
        next_run_at=_next_fire_value(now, next_fire),
        last_planned_at=snapshot.next_run_at,
        last_status=RunStatus.SKIPPED,
        last_error=reason.value,
    )


def _fail(
    snapshot: TaskSnapshot,
    now: datetime,
    trigger_kind: TriggerKind,
    next_fire: Callable[[datetime], datetime] | None,
    reason: PlanReason,
    *,
    disable: bool,
    detail: str = "",
) -> PlanDecision:
    """FAIL：不占租约、记账失败（AC-22 / AC-31）——**只由计划轮次产生**。

    周期任务推进 ``next_run_at``（下轮按计划自然重试，不排队补发）；一次性
    任务没有下一跳——保持 due 由连续失败阈值收敛为自动停用（AC-55），不静默
    消耗其唯一机会。

    应用已删除的失败恒通知（AC-31 明文）且 ``disable=True``。``detail`` 是复检
    给出的细分原因（``trigger_guard.describe_failure``），写进 ``last_error`` 用
    于 11905 的可诊断性。
    """
    count_failure = True
    last_error = detail or reason.value
    if _keep_plan(snapshot, trigger_kind):
        return PlanDecision(
            variant=PlanVariant.FAIL,
            trigger_kind=trigger_kind,
            reason=reason,
            detail=detail,
            keep_next_run_at=True,
            last_status=RunStatus.FAILED,
            last_error=last_error,
            count_failure=count_failure,
            notify=count_failure or disable,
            disable=disable,
        )
    return PlanDecision(
        variant=PlanVariant.FAIL,
        trigger_kind=trigger_kind,
        reason=reason,
        detail=detail,
        next_run_at=_next_fire_value(now, next_fire),
        last_planned_at=snapshot.next_run_at,
        last_status=RunStatus.FAILED,
        last_error=last_error,
        count_failure=count_failure,
        notify=count_failure or disable,
        disable=disable,
    )
