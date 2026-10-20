"""F077 触发计划的纯函数内核（design 决策 7；spec AC-06 / AC-07 / AC-09）。

四个纯函数，不读真实时钟、不碰 DB / Redis / celery app：

- ``validate_and_parse`` —— **唯一**解析入口（``crontab.from_string``）：非 5 段 /
  字段越界 / ``@别名`` 一律 11903 拒绝，绝不静默改写；
- ``normalize`` —— 折叠连续空白为单空格（存库 ``cron_expression`` 的格式，也是
  解析的前置步骤：``from_string`` 按字面量单空格切分字段，多空白会直接报"字段数不对"）；
- ``describe`` —— 人类可读解释（AC-07：预览端点与列表「下次执行」共用同一口径）；
- ``next_runs`` —— 严格大于锚点的未来 n 次触发时刻，**自算**。

**为什么自算**：``remaining_estimate`` / ``is_due().next`` 内部读真实时钟（以
now() 为基准返回相对值），拿它们回填 ``next_run_at`` 或做预览会随时间漂移
（design §5 坑 3）。本模块只复用 ``crontab.from_string`` 解析出的字段集合，按
「日粗筛 + 当天时分展开」推进，并复刻 DOM/DOW 同时受限时的 OR 语义
（crontab(5) 标准；与 celery 引擎的 AND 有意不同，见 ``_day_matches``）。

**为什么不用 ``crontab(str)``**：该构造器把字符串当"周字面量"——``crontab("30 2 * * *")``
抛 ``ValueError``、``crontab("sun")`` 静默解析成 ``minute={0}``（design §5 坑 1）。

时区：入参 tz-aware，统一折算到服务器时区 ``Asia/Shanghai`` 计算，出参带同一时区；
本模块不做任务级时区（spec AC-09）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from celery.schedules import ParseException, crontab

from bisheng.common.errcode.scheduled_task import ScheduledTaskCronInvalidError

#: 服务器时区（spec AC-09）：触发时刻一律按它解释与展示，显式构造、不做本地化推断。
SERVER_TZ = ZoneInfo("Asia/Shanghai")

#: ``next_runs`` 的扫描上限（年）。兜底 `0 0 29 2 *` 这类闰日表达式：闰日最大间隔
#: 为 8 年（如 2096 → 2104，2100 非闰年），超限即判表达式不可用，而不是死循环。
MAX_SCAN_YEARS = 8

#: celery/cron 口径的星期名（周日=0，周一=1 … 周六=6）——描述输出与字段集合同口径。
_WEEKDAY_NAMES = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")

#: 描述里枚举字段的展开上限：超过则回退结构化描述（不追求完美自然语言，也不引描述库）。
_MAX_ENUMERATED = 8


@dataclass(frozen=True)
class ParsedCron:
    """``validate_and_parse`` 的结果：归一化原文 + celery 解析出的字段集合。

    ``fields`` 是归一化后按「分 时 日 月 周」顺序的五段原文，只用于形态判断与
    描述（单空格切分无歧义）；各集合来自 ``crontab.from_string``，是触发语义的
    唯一来源（``days_of_week`` 为 celery 口径：周日=0，周一=1 … 周六=6）。
    """

    expression: str
    fields: tuple[str, str, str, str, str]  # (minute, hour, day_of_month, month_of_year, day_of_week)
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months_of_year: frozenset[int]
    days_of_week: frozenset[int]


def normalize(expression: str) -> str:
    """折叠连续空白为单空格并去掉首尾空白（spec AC-06：多余空白按归一化处理）。

    这是存库格式（``cron_expression`` 列）与解析前置：``crontab.from_string``
    仍按字面量单空格切分字段，原样传多空白会直接报字段数不对。
    """
    return " ".join(expression.split())


def validate_and_parse(expression: str) -> ParsedCron:
    """唯一解析入口：归一化后用 ``crontab.from_string`` 解析，失败 → 11903。

    拒绝：非 5 段（秒级 / ``@daily`` 别名 / 空串）、字段越界（周 7、月 13、
    小时 24…）、无法解释的字面量（``mon-fr``）。解析成功后返回归一化原文与字段
    集合——解析与执行唯一同源，绝不另写一套正则/规则。
    """
    if not isinstance(expression, str):
        # cron_expression 列可空、上游类型不受信：非字符串一律按"表达式非法"拒绝，
        # 不让副作用（AttributeError）穿透到调用方。
        raise ScheduledTaskCronInvalidError()
    normalized = normalize(expression)
    try:
        schedule = crontab.from_string(normalized)
    except (ValueError, ParseException) as exc:
        # ParseException 不是 ValueError 子类（`*/` 这类"空过滤器"走它），两个都要收。
        raise ScheduledTaskCronInvalidError() from exc
    minute, hour, day_of_month, month_of_year, day_of_week = normalized.split(" ")
    return ParsedCron(
        expression=normalized,
        fields=(minute, hour, day_of_month, month_of_year, day_of_week),
        minutes=frozenset(schedule.minute),
        hours=frozenset(schedule.hour),
        days_of_month=frozenset(schedule.day_of_month),
        months_of_year=frozenset(schedule.month_of_year),
        days_of_week=frozenset(schedule.day_of_week),
    )


def describe(expression: str) -> str:
    """人类可读的触发解释（spec AC-07 的"人类可读解释"）。

    覆盖常见形态：每分钟 / 每 N 分钟 / 每小时的第 M 分钟 / 每天 H:MM / 每周X H:MM /
    每月 D 日 H:MM / 每年 M 月 D 日 H:MM；枚举过长的复杂表达式回退结构化描述
    （design 决策 7：不追求完美自然语言，不引入 cron 描述库）。非法表达式与
    ``validate_and_parse`` 同口径（11903）。
    """
    parsed = validate_and_parse(expression)
    if all(field == "*" for field in parsed.fields):
        return "每分钟"
    day_text = _describe_day(parsed)
    if day_text is None:
        return _structured(parsed)
    time_text = _describe_time(parsed)
    if time_text is None:
        return _structured(parsed)
    if day_text == "每天" and time_text.startswith("每"):
        # 「每天 每 5 分钟」不是人话；重复性时间表述本身已含"每天"语义。
        return time_text
    return f"{day_text} {time_text}"


def describe_parts(expression: str) -> dict:
    """Human-readable schedule as **semantic tokens** (AC-59 i18n).

    Unlike :func:`describe` (which renders Chinese directly), this returns a
    structure the frontend can render in any locale:

    - ``{"time": {"kind": ...}}`` / ``{"day": {...}, "time": {...}}``
    - ``{"raw": {...}}`` for expressions outside the natural-language templates.

    The branch tree mirrors :func:`describe`; keep the two in sync (the test
    suite asserts both agree on which expressions fall back to ``raw``).
    """
    parsed = validate_and_parse(expression)
    if all(field == "*" for field in parsed.fields):
        return {"time": {"kind": "every_minute"}}

    day = _describe_day_parts(parsed)
    if day is None:
        return {"raw": _raw_fields(parsed)}
    time_part = _describe_time_parts(parsed)
    if time_part is None:
        return {"raw": _raw_fields(parsed)}

    # Mirror describe(): “每天 每 N 分钟” is not natural language; the repeating
    # time expression already carries the daily semantics.
    if day.get("kind") == "every_day" and time_part["kind"] in _SELF_CONTAINED_TIME_KINDS:
        return {"time": time_part}
    return {"day": day, "time": time_part}


#: 这些时间表述自带“每天”语义，前置 day=every_day 时省略（与 describe() 的
#: `time_text.startswith("每")` 分支同口径）。
_SELF_CONTAINED_TIME_KINDS = frozenset(
    {"every_minute", "every_n_minutes", "minutes_of_hour", "every_n_hours_at_minute"}
)


def _raw_fields(parsed: ParsedCron) -> dict:
    minute, hour, day_of_month, month_of_year, day_of_week = parsed.fields
    return {
        "minute": minute,
        "hour": hour,
        "day_of_month": day_of_month,
        "month_of_year": month_of_year,
        "day_of_week": day_of_week,
    }


def _describe_day_parts(parsed: ParsedCron) -> dict | None:
    """日/月/周三段 → semantic tokens；枚举过长返回 None（交 raw 兜底）。"""
    _, _, day_of_month_field, month_of_year_field, day_of_week_field = parsed.fields
    day_of_month_restricted = day_of_month_field != "*"
    month_of_year_restricted = month_of_year_field != "*"
    day_of_week_restricted = day_of_week_field != "*"
    if not (day_of_month_restricted or month_of_year_restricted or day_of_week_restricted):
        return {"kind": "every_day"}
    if (day_of_month_restricted and len(parsed.days_of_month) > _MAX_ENUMERATED) or (
        day_of_week_restricted and len(parsed.days_of_week) > _MAX_ENUMERATED
    ):
        return None
    if month_of_year_restricted and len(parsed.months_of_year) > _MAX_ENUMERATED:
        return None

    months = sorted(parsed.months_of_year)
    if day_of_month_restricted:
        days = sorted(parsed.days_of_month)
        if month_of_year_restricted:
            day_part: dict = {"kind": "yearly_days", "months": months, "days": days}
        else:
            day_part = {"kind": "month_days", "days": days}
        if day_of_week_restricted:
            # 日/周同时受限 → OR（与 next_runs/describe 同口径）。
            return {"kind": "or", "parts": [day_part, _weekday_part(sorted(parsed.days_of_week))]}
        return day_part
    if day_of_week_restricted:
        weekdays = sorted(parsed.days_of_week)
        if month_of_year_restricted:
            return {
                "kind": "yearly_weekdays",
                "months": months,
                "weekdays": weekdays,
                "range": _is_weekday_range(weekdays),
            }
        return _weekday_part(weekdays)
    return {"kind": "every_day_of_months", "months": months}


def _weekday_part(weekdays: list[int]) -> dict:
    return {"kind": "weekdays", "weekdays": weekdays, "range": _is_weekday_range(weekdays)}


def _is_weekday_range(weekdays: list[int]) -> bool:
    """与 _weekdays_text 的折叠规则一致：>=3 个连续星期折叠为区间。"""
    return len(weekdays) >= 3 and weekdays == list(range(weekdays[0], weekdays[-1] + 1))


def _describe_time_parts(parsed: ParsedCron) -> dict | None:
    """时/分两段 → semantic tokens；形态超出模板返回 None（交 raw 兜底）。"""
    minute_field, hour_field, _, _, _ = parsed.fields
    minute_numbers = None if minute_field == "*" else _number_list(minute_field)
    hour_numbers = None if hour_field == "*" else _number_list(hour_field)
    minute_step = _even_step(minute_field, 60)
    hour_step = _even_step(hour_field, 24)

    if hour_field == "*":
        if minute_field == "*":
            return {"kind": "every_minute"}
        if minute_step is not None:
            return {"kind": "every_n_minutes", "n": minute_step}
        if minute_numbers is not None and len(minute_numbers) <= _MAX_ENUMERATED:
            return {"kind": "minutes_of_hour", "minutes": minute_numbers}
        return None
    if hour_step is not None and minute_numbers is not None and len(minute_numbers) == 1:
        return {"kind": "every_n_hours_at_minute", "n": hour_step, "minute": minute_numbers[0]}
    if minute_step is not None and hour_numbers is not None and len(hour_numbers) == 1:
        return {"kind": "every_n_minutes_of_hour", "n": minute_step, "hour": hour_numbers[0]}
    if minute_field == "*" and hour_numbers is not None and len(hour_numbers) <= _MAX_ENUMERATED:
        return {"kind": "every_minute_of_hours", "hours": hour_numbers}
    if minute_numbers is not None and len(minute_numbers) == 1:
        minute_value = minute_numbers[0]
        if hour_numbers is not None and len(hour_numbers) <= _MAX_ENUMERATED:
            return {
                "kind": "at",
                "times": [{"hour": hour, "minute": minute_value} for hour in hour_numbers],
            }
        return None
    if hour_numbers is not None and len(hour_numbers) == 1 and minute_numbers is not None:
        if len(minute_numbers) <= _MAX_ENUMERATED:
            return {
                "kind": "at",
                "times": [{"hour": hour_numbers[0], "minute": minute} for minute in minute_numbers],
            }
    return None


def next_runs(expression_or_parsed: str | ParsedCron, anchor: datetime, n: int) -> list[datetime]:
    """锚点之后（**严格大于**）的前 ``n`` 个触发时刻，秒与微秒归零。

    - ``anchor`` 必须 tz-aware（调用侧用 ``datetime.now(ZoneInfo("Asia/Shanghai"))``
      或读库值补 ``ZoneInfo`` 后传入），先折算到服务器时区再计算，出参带同一时区；
    - 先按日粗筛（月 / 日 / 周），命中日再按 时 × 分 展开；日与周同时受限时取 OR
      （见 ``_day_matches``）；
    - 扫描超过 ``MAX_SCAN_YEARS`` 年仍凑不齐 ``n`` 个触发点（如 ``0 0 30 2 *``
      这类永远不存在的日期）按 11903 抛错，而不是死循环。
    """
    if not isinstance(anchor, datetime) or anchor.tzinfo is None:
        raise ValueError("anchor must be a timezone-aware datetime")
    if n <= 0:
        return []
    if isinstance(expression_or_parsed, ParsedCron):
        parsed = expression_or_parsed
    else:
        parsed = validate_and_parse(expression_or_parsed)

    # 归一到整分后 +1 分钟：之后所有整分候选时刻都严格大于锚点（"严格大于"的实现点），
    # 也顺带把秒/微秒归零。锚点恰在槽位上时不会重复取该槽位。
    start = anchor.astimezone(SERVER_TZ).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day_of_month_restricted = parsed.fields[2] != "*"
    day_of_week_restricted = parsed.fields[4] != "*"
    hours = sorted(parsed.hours)
    minutes = sorted(parsed.minutes)

    found: list[datetime] = []
    last_year = start.year + MAX_SCAN_YEARS
    day = start.date()
    while day.year <= last_year:
        if day.month in parsed.months_of_year and _day_matches(
            day, parsed, day_of_month_restricted, day_of_week_restricted
        ):
            for hour in hours:
                for minute in minutes:
                    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SERVER_TZ)
                    if candidate < start:  # 只会命中首日前半段
                        continue
                    found.append(candidate)
                    if len(found) == n:
                        return found
        day += timedelta(days=1)
    if found:
        # 窗口内凑不齐 n 个（如 `0 0 29 2 *` 求未来 5 次：闰日间隔 4 年，8 年窗口
        # 最多 2 个）——返回**已找到的**（≤n）。spec AC-07 的措辞是"未来**至多** 5 次"，
        # 返回更少是合法的；此前这里是直接抛错，会让合法的闰日表达式在预览处报
        # 11903（复核者 T-07 首轮实测）。
        return found
    # 上限内没有任何触发点（如 2 月 30 日）：与解析失败同一出口——对调用方而言
    # 都是"这个计划永远不会有下一跳"，比 500 更可诊断。
    raise ScheduledTaskCronInvalidError()


def _day_matches(day: date, parsed: ParsedCron, day_of_month_restricted: bool, day_of_week_restricted: bool) -> bool:
    """该日是否命中（crontab(5) 的日/周取或语义，design 决策 7）。

    日、周两个字段**同时受限**（都不是 ``*``）时取 OR——任一命中即触发；只有一个
    受限时只看它；都不受限时每天都命中。celery 引擎在此处是 AND（``0 9 13 * 2``
    只匹配"既是周二又是 13 号"），本模块按标准 cron 语义执行是有意偏离：实际派发
    由 ``next_runs`` 的下一跳驱动（beat 只按固定节拍扫描），这里就是调度语义的真相。
    """
    day_of_month_hit = day.day in parsed.days_of_month
    day_of_week_hit = _celery_weekday(day) in parsed.days_of_week
    if day_of_month_restricted and day_of_week_restricted:
        return day_of_month_hit or day_of_week_hit
    if day_of_month_restricted:
        return day_of_month_hit
    if day_of_week_restricted:
        return day_of_week_hit
    return True


def _celery_weekday(day: date) -> int:
    """Python 口径（周一=0 … 周日=6）→ celery/cron 口径（周日=0，周一=1 … 周六=6）。"""
    return (day.weekday() + 1) % 7


def _describe_time(parsed: ParsedCron) -> str | None:
    """时/分两段的自然语言；形态超出模板（如多段混合的步进）返回 None 交结构化兜底。"""
    minute_field, hour_field, _, _, _ = parsed.fields
    minute_numbers = None if minute_field == "*" else _number_list(minute_field)
    hour_numbers = None if hour_field == "*" else _number_list(hour_field)
    minute_step = _even_step(minute_field, 60)
    hour_step = _even_step(hour_field, 24)

    if hour_field == "*":
        if minute_field == "*":
            return "每分钟"
        if minute_step is not None:
            return f"每 {minute_step} 分钟"
        if minute_numbers is not None:
            if len(minute_numbers) == 1:
                return f"每小时的第 {minute_numbers[0]} 分钟"
            if len(minute_numbers) <= _MAX_ENUMERATED:
                return "每小时的第 " + "、".join(str(value) for value in minute_numbers) + " 分钟"
        return None
    if hour_step is not None and minute_numbers is not None and len(minute_numbers) == 1:
        return f"每 {hour_step} 小时的第 {minute_numbers[0]} 分钟"
    if minute_step is not None and hour_numbers is not None and len(hour_numbers) == 1:
        return f"{hour_numbers[0]} 点的每 {minute_step} 分钟"
    if minute_field == "*" and hour_numbers is not None and len(hour_numbers) <= _MAX_ENUMERATED:
        return "、".join(str(hour) for hour in hour_numbers) + " 点的每分钟"
    if minute_numbers is not None and len(minute_numbers) == 1:
        minute_value = minute_numbers[0]
        if hour_numbers is not None and len(hour_numbers) == 1:
            return f"{hour_numbers[0]}:{minute_value:02d}"
        if hour_numbers is not None and len(hour_numbers) <= _MAX_ENUMERATED:
            return "、".join(f"{hour}:{minute_value:02d}" for hour in hour_numbers)
        return None
    if hour_numbers is not None and len(hour_numbers) == 1 and minute_numbers is not None:
        if len(minute_numbers) <= _MAX_ENUMERATED:
            return "、".join(f"{hour_numbers[0]}:{minute:02d}" for minute in minute_numbers)
    return None


def _describe_day(parsed: ParsedCron) -> str | None:
    """日/月/周三段的自然语言；枚举过长返回 None 交结构化兜底。"""
    _, _, day_of_month_field, month_of_year_field, day_of_week_field = parsed.fields
    day_of_month_restricted = day_of_month_field != "*"
    month_of_year_restricted = month_of_year_field != "*"
    day_of_week_restricted = day_of_week_field != "*"
    if not (day_of_month_restricted or month_of_year_restricted or day_of_week_restricted):
        return "每天"
    if (day_of_month_restricted and len(parsed.days_of_month) > _MAX_ENUMERATED) or (
        day_of_week_restricted and len(parsed.days_of_week) > _MAX_ENUMERATED
    ):
        return None
    if month_of_year_restricted and len(parsed.months_of_year) > _MAX_ENUMERATED:
        return None
    if day_of_month_restricted:
        days_text = "、".join(str(day) for day in sorted(parsed.days_of_month))
        if month_of_year_restricted:
            day_text = f"每年 {_months_text(sorted(parsed.months_of_year))} {days_text} 日"
        else:
            day_text = f"每月 {days_text} 日"
        if day_of_week_restricted:
            # 日/周同时受限 → OR（与 next_runs 同口径）：两个条件都如实说出。
            return f"{day_text} 或 每{_weekdays_text(sorted(parsed.days_of_week))}"
        return day_text
    if day_of_week_restricted:
        weekdays_text = "每" + _weekdays_text(sorted(parsed.days_of_week))
        if month_of_year_restricted:
            # 月字段恒为 AND 语义：`0 0 * 2 1` 只在**2 月**的周一触发（复核者首轮
            # 实测 describe 曾丢掉月份，文案与真实计划矛盾——AC-07 要求二者同口径）。
            return f"每年 {_months_text(sorted(parsed.months_of_year))}的{weekdays_text}"
        return weekdays_text
    return f"每年 {_months_text(sorted(parsed.months_of_year))}的每天"


def _structured(parsed: ParsedCron) -> str:
    """复杂形态的结构化兜底：五个字段逐个回显（归一化原文），不做自然语言推断。"""
    minute, hour, day_of_month, month_of_year, day_of_week = parsed.fields
    return f"分钟 {minute}；小时 {hour}；日 {day_of_month}；月 {month_of_year}；周 {day_of_week}"


def _number_list(field: str) -> list[int] | None:
    """纯数字列表（如 ``1,15``）返回升序 int 列表；含 ``-`` / ``*`` / ``/`` 时返回 None。"""
    values: list[int] = []
    for part in field.split(","):
        if not part.isdigit():
            return None
        values.append(int(part))
    return sorted(values)


def _even_step(field: str, span: int) -> int | None:
    """``*/n`` 且能整除整个跨度时返回 n，否则 None。

    不能整除时（如分钟的 ``*/7``）每个小时边界会重启计数（56 分之后 4 分钟就是
    下一小时的 0 分），"每 7 分钟"不是准确说法——交结构化兜底，不硬编。
    """
    if not field.startswith("*/"):
        return None
    step_text = field[2:]
    if not step_text.isdigit():
        return None
    step = int(step_text)
    if step <= 0 or span % step:
        return None
    return step


def _weekdays_text(days_of_week: list[int]) -> str:
    """celery 口径（周日=0 … 周六=6）的星期枚举 → 中文；连续段折叠为「X至Y」。"""
    names = [_WEEKDAY_NAMES[value] for value in days_of_week]
    if len(names) >= 3 and days_of_week == list(range(days_of_week[0], days_of_week[-1] + 1)):
        return f"{names[0]}至{names[-1]}"
    return "、".join(names)


def _months_text(months_of_year: list[int]) -> str:
    """月份枚举 → 中文（如 ``2`` → 「2 月」）。"""
    return "、".join(f"{month} 月" for month in months_of_year)
