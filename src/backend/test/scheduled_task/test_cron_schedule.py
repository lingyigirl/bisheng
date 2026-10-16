"""F077 T-07：触发计划纯函数 ``cron_schedule`` 的行为锁。

覆盖 spec AC-06（接受/拒绝清单 + 归一化）、AC-07（人类可读解释与未来触发时刻）、
AC-09（Asia/Shanghai 口径）。所有锚点都是**显式构造的 tz-aware datetime**，
不允许 ``datetime.now()``——纯函数不读真实时钟（design 决策 7），这是"序列可断言"
的前提。

与 celery 的对拍只挑**日/周不同时受限**的表达式：两者同时受限时本模块按
crontab(5) 的 OR 语义执行、celery 引擎按 AND，属 design 决策 7 的有意偏离，
由 ``test_next_runs_day_of_month_or_day_of_week`` 单独锁定。

注意：``test/conftest.py`` 为隔断 ``settings.py`` 的导入链，会把 ``celery`` /
``celery.schedules`` 预替换成 MagicMock（被 mock 的 ``crontab.from_string`` 返回
MagicMock、字段集合恒为空）。本文件锁的恰是真实解析语义，故在夹具里把真件注入
被测模块（见 ``_real_crontab``）——只影响本模块，不动会话级 mock。
"""

import importlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bisheng.common.errcode.scheduled_task import ScheduledTaskCronInvalidError
from bisheng.scheduled_task.domain.services import cron_schedule as cron_schedule_module
from bisheng.scheduled_task.domain.services.cron_schedule import (
    describe,
    next_runs,
    normalize,
    validate_and_parse,
)

TZ = ZoneInfo("Asia/Shanghai")
# 基准日 2026-10-16 是周五：下面的"跨天/跨周/闰日"断言都以它为锚点（日期已人工核过）。
FRIDAY_MORNING = datetime(2026, 10, 16, 8, 0, tzinfo=TZ)
FRIDAY_NOON = datetime(2026, 10, 16, 12, 0, tzinfo=TZ)

#: conftest 预 mock 的 celery 模块名（mock_services.PREMOCK 段的 celery 子集）。
_CELERY_MOCKED = ("celery", "celery.schedules", "celery.app", "celery.app.task", "celery.signals")


def _load_real_celery_schedules():
    """取到真实的 ``celery.schedules`` 模块（绕开会话级预 mock）。

    做法与 ``test/fixtures/mock_services.py`` 的"真模块先占 sys.modules 即跳过
    mock"同源：临时摘掉被 mock 的名字导入真包，随后立刻把 mock 放回去——
    同一会话里其它用例看到的 celery 状态不变。
    """
    mocked = {name: sys.modules[name] for name in _CELERY_MOCKED if name in sys.modules}
    for name in mocked:
        del sys.modules[name]
    try:
        return importlib.import_module("celery.schedules")
    finally:
        sys.modules.update(mocked)


_REAL_CELERY_SCHEDULES = _load_real_celery_schedules()
_REAL_CRONTAB = _REAL_CELERY_SCHEDULES.crontab
_REAL_PARSE_EXCEPTION = _REAL_CELERY_SCHEDULES.ParseException


@pytest.fixture(autouse=True)
def _real_crontab(monkeypatch):
    """把真 ``crontab`` / ``ParseException`` 注入被测模块（monkeypatch 自动还原）。

    被测模块导入时绑定的是被 mock 的对象（``from celery.schedules import crontab``
    在 conftest 预 mock 之后执行），必须逐用例换回真件，否则解析全是空集合。
    """
    monkeypatch.setattr(cron_schedule_module, "crontab", _REAL_CRONTAB)
    monkeypatch.setattr(cron_schedule_module, "ParseException", _REAL_PARSE_EXCEPTION)


def _celery_next_after(expression: str, when: datetime) -> datetime:
    """celery 在同一 nowfun 注入下"严格大于 when"的下一跳（对拍基准）。

    ``remaining_estimate`` 内部走 ``self.nowfun``（注入固定值后是纯计算）；
    传 ``last_run_at == now == when`` 时 ``remaining(start, ends_in, now=start)``
    退化为 ``ends_in``，故 ``when + remaining_estimate(when)`` 即下一跳。
    """
    schedule = _REAL_CRONTAB.from_string(normalize(expression))
    schedule.nowfun = lambda: when
    return when + schedule.remaining_estimate(when)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0 9 * * *", "0 9 * * *"),
        ("  0   9  *  *  *  ", "0 9 * * *"),
        ("0\t9 *   * *", "0 9 * * *"),
        ("0 9 * * *\n", "0 9 * * *"),
    ],
)
def test_normalize_collapses_whitespace(raw, expected):
    assert normalize(raw) == expected
    assert normalize(normalize(raw)) == expected  # 幂等：存库格式可反复经过


@pytest.mark.parametrize(
    ("expression", "minutes", "hours", "days_of_week"),
    [
        ("0 9 * * *", {0}, {9}, set(range(7))),
        ("*/5 * * * *", {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}, set(range(24)), set(range(7))),
        ("0 9 * * 1-5", {0}, {9}, {1, 2, 3, 4, 5}),
        ("0 9 * * mon-fri", {0}, {9}, {1, 2, 3, 4, 5}),
        ("0 9 * * sun", {0}, {9}, {0}),
        ("1,15 9 * * *", {1, 15}, {9}, set(range(7))),
        ("0 0 29 2 *", {0}, {0}, set(range(7))),
    ],
)
def test_validate_accepts_common_forms(expression, minutes, hours, days_of_week):
    parsed = validate_and_parse(expression)
    assert parsed.expression == expression
    assert parsed.minutes == frozenset(minutes)
    assert parsed.hours == frozenset(hours)
    assert parsed.days_of_week == frozenset(days_of_week)


def test_validate_normalizes_before_parsing():
    # spec AC-06：多余空白按归一化处理（celery 的 from_string 按字面量单空格切分，
    # 不先归一化会直接报"字段数不对"）。
    parsed = validate_and_parse("  0\t9  *   * *  ")
    assert parsed.expression == "0 9 * * *"
    assert next_runs("  0\t9  *   * *  ", FRIDAY_MORNING, 1)[0] == datetime(2026, 10, 16, 9, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    "expression",
    [
        "0 0 9 * * *",  # 秒级：6 段
        "0 9 * * * *",  # 6 段
        "@daily",  # 预定义别名（celery 无 year 字段，别名也不接受）
        "0 9 * * 7",  # 周越界（celery 口径 0–6）
        "0 9 * 13 *",  # 月越界（1–12）
        "0 24 * * *",  # 小时越界（0–23）
        "60 0 * * *",  # 分钟越界（0–59）
        "0 9 0 * *",  # 日下界（1–31）
        "0 9 * 0 *",  # 月下界（1–12）
        "*/ * * * *",  # 空步进（celery 抛 ParseException，非 ValueError）
        "0 9 * * mon-fr",  # 非法周字面量
        "",
        "   ",  # 归一化后为空
        "not a cron",
    ],
)
def test_validate_rejects_invalid_expressions(expression):
    with pytest.raises(ScheduledTaskCronInvalidError) as excinfo:
        validate_and_parse(expression)
    assert excinfo.value.Code == 11903


@pytest.mark.parametrize("expression", [None, 5, ["0 9 * * *"]])
def test_validate_rejects_non_string(expression):
    # cron_expression 列可空、上游类型不受信：非字符串同样按 11903 拒绝（fail-closed）。
    with pytest.raises(ScheduledTaskCronInvalidError):
        validate_and_parse(expression)


def test_next_runs_cross_hour():
    anchor = datetime(2026, 10, 16, 8, 50, tzinfo=TZ)
    assert next_runs("*/15 * * * *", anchor, 3) == [
        datetime(2026, 10, 16, 9, 0, tzinfo=TZ),
        datetime(2026, 10, 16, 9, 15, tzinfo=TZ),
        datetime(2026, 10, 16, 9, 30, tzinfo=TZ),
    ]


def test_next_runs_cross_day():
    anchor = datetime(2026, 10, 16, 10, 0, tzinfo=TZ)
    assert next_runs("0 9 * * *", anchor, 2) == [
        datetime(2026, 10, 17, 9, 0, tzinfo=TZ),
        datetime(2026, 10, 18, 9, 0, tzinfo=TZ),
    ]


def test_next_runs_cross_month():
    assert next_runs("0 0 1 * *", FRIDAY_MORNING, 2) == [
        datetime(2026, 11, 1, 0, 0, tzinfo=TZ),
        datetime(2026, 12, 1, 0, 0, tzinfo=TZ),
    ]


def test_next_runs_cross_year():
    assert next_runs("0 0 1 1 *", FRIDAY_MORNING, 2) == [
        datetime(2027, 1, 1, 0, 0, tzinfo=TZ),
        datetime(2028, 1, 1, 0, 0, tzinfo=TZ),
    ]


def test_next_runs_leap_day():
    # 闰日表达式必须推进到下一个 2 月 29 日（2028 / 2032），而不是死循环或跳过。
    assert next_runs("0 0 29 2 *", FRIDAY_MORNING, 2) == [
        datetime(2028, 2, 29, 0, 0, tzinfo=TZ),
        datetime(2032, 2, 29, 0, 0, tzinfo=TZ),
    ]


def test_next_runs_skips_weekend():
    # 周五 12:00 之后：下一个工作日的槽位是周一 10-19 09:00（周六/周日不命中）。
    assert next_runs("0 9 * * 1-5", FRIDAY_NOON, 2) == [
        datetime(2026, 10, 19, 9, 0, tzinfo=TZ),
        datetime(2026, 10, 20, 9, 0, tzinfo=TZ),
    ]


def test_next_runs_day_of_month_or_day_of_week():
    # 日/周同时受限 → OR（crontab(5) 语义，design 决策 7）：10-18 是周日（仅日命中），
    # 10-23/10-30/11-06/11-13 是周五（仅周命中）。AND 语义下只会有后者，本例即锁定 OR。
    assert next_runs("0 9 18 * 5", FRIDAY_NOON, 5) == [
        datetime(2026, 10, 18, 9, 0, tzinfo=TZ),
        datetime(2026, 10, 23, 9, 0, tzinfo=TZ),
        datetime(2026, 10, 30, 9, 0, tzinfo=TZ),
        datetime(2026, 11, 6, 9, 0, tzinfo=TZ),
        datetime(2026, 11, 13, 9, 0, tzinfo=TZ),
    ]


def test_next_runs_is_strictly_after_anchor_at_slot():
    # 锚点恰在槽位上 → 取下一个槽位（严格大于，不重复取锚点本身）。
    anchor = datetime(2026, 10, 16, 9, 0, tzinfo=TZ)
    assert next_runs("0 9 * * *", anchor, 1)[0] == datetime(2026, 10, 17, 9, 0, tzinfo=TZ)


def test_next_runs_zeroes_seconds_and_microseconds():
    anchor = datetime(2026, 10, 16, 8, 59, 30, 123456, tzinfo=TZ)
    first = next_runs("*/5 * * * *", anchor, 1)[0]
    assert first == datetime(2026, 10, 16, 9, 0, tzinfo=TZ)
    assert (first.second, first.microsecond) == (0, 0)


def test_next_runs_accepts_parsed_cron():
    parsed = validate_and_parse("0 9 * * *")
    assert next_runs(parsed, FRIDAY_MORNING, 1) == [datetime(2026, 10, 16, 9, 0, tzinfo=TZ)]


def test_next_runs_converts_foreign_tz_anchor():
    # 入参 tz-aware 即接受：UTC 00:30 = 上海 08:30，按服务器时区取 09:00，出参带上海时区。
    anchor = datetime(2026, 10, 16, 0, 30, tzinfo=ZoneInfo("UTC"))
    first = next_runs("0 9 * * *", anchor, 1)[0]
    assert first == datetime(2026, 10, 16, 9, 0, tzinfo=TZ)
    assert str(first.tzinfo) == "Asia/Shanghai"


def test_next_runs_rejects_naive_anchor():
    # 契约：纯函数入参必须 tz-aware（design 决策 7 的时区边界）。
    with pytest.raises(ValueError):
        next_runs("0 9 * * *", datetime(2026, 10, 16, 8, 0), 1)


def test_next_runs_empty_for_n_le_zero():
    assert next_runs("0 9 * * *", FRIDAY_MORNING, 0) == []
    assert next_runs("0 9 * * *", FRIDAY_MORNING, -1) == []


def test_next_runs_rejects_never_firing_expression():
    # 2 月 30 日永远不存在：超过扫描上限抛 11903，而不是死循环。
    with pytest.raises(ScheduledTaskCronInvalidError) as excinfo:
        next_runs("0 0 30 2 *", FRIDAY_MORNING, 1)
    assert excinfo.value.Code == 11903


def test_next_runs_chain_skips_exactly_one_period():
    # 连续调用（每次以上一跳为锚点）都恰推进一个 5 分钟周期——严格递增、不重复、
    # 不遗漏（design §7 允许的"对拍难以稳定时"的替代断言，这里与对拍并存）。
    cursor = FRIDAY_MORNING
    runs = []
    for _ in range(10):
        cursor = next_runs("*/5 * * * *", cursor, 1)[0]
        runs.append(cursor)
    assert runs == [datetime(2026, 10, 16, 8, 5 + 5 * step, tzinfo=TZ) for step in range(10)]


@pytest.mark.parametrize(
    ("expression", "anchor"),
    [
        ("0 9 * * *", FRIDAY_MORNING),
        ("0 9 * * *", datetime(2026, 10, 16, 9, 0, tzinfo=TZ)),  # 恰在槽位上
        ("*/5 * * * *", datetime(2026, 10, 16, 8, 57, 30, tzinfo=TZ)),  # 秒非零
        ("0 9 * * 1-5", FRIDAY_NOON),  # 跨周末
        ("1,15 9 * * *", datetime(2026, 10, 16, 9, 1, tzinfo=TZ)),  # 恰在槽位上（列表第二项）
        ("0 9 18 * *", FRIDAY_MORNING),  # 仅日受限
        ("0 0 1 1 *", FRIDAY_MORNING),  # 跨年
        ("0 0 29 2 *", FRIDAY_MORNING),  # 闰日
    ],
)
def test_next_runs_matches_celery_under_injected_nowfun(expression, anchor):
    # 对拍回归：celery 在同一 nowfun 注入下的下一跳 == 本模块的自算结果。
    # 只挑日/周不同时受限的形态（两者同时受限时语义有意不同，见文件头说明）。
    assert next_runs(expression, anchor, 1)[0] == _celery_next_after(expression, anchor)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("* * * * *", "每分钟"),
        ("*/5 * * * *", "每 5 分钟"),
        ("*/2 * * * *", "每 2 分钟"),
        ("0 * * * *", "每小时的第 0 分钟"),
        ("0 9 * * *", "每天 9:00"),
        ("0 9 * * 1-5", "每周一至周五 9:00"),
        ("0 9 * * mon-fri", "每周一至周五 9:00"),
        ("0 9 * * sun", "每周日 9:00"),
        ("0 9 * * 0,2", "每周日、周二 9:00"),
        ("0 0 13 * *", "每月 13 日 0:00"),
        ("0 9 1,15 * *", "每月 1、15 日 9:00"),
        ("0 0 29 2 *", "每年 2 月 29 日 0:00"),
        ("1,15 9 * * *", "每天 9:01、9:15"),
        ("0 9,15 * * *", "每天 9:00、15:00"),
        ("0 */2 * * *", "每 2 小时的第 0 分钟"),
        ("*/15 9 * * *", "每天 9 点的每 15 分钟"),
        ("0 9 18 * 5", "每月 18 日 或 每周五 9:00"),  # 日/周同时受限 → 描述如实说 OR
    ],
)
def test_describe_common_forms(expression, expected):
    assert describe(expression) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # 复杂形态回退"字段逐个回显"的结构化描述（不追求完美自然语言）。
        ("1,15 */6 * * *", "分钟 1,15；小时 */6；日 *；月 *；周 *"),
        ("0-30 9 * * *", "分钟 0-30；小时 9；日 *；月 *；周 *"),
        # 枚举超过上限（_MAX_ENUMERATED=8）也不生成超长枚举句，直接结构化。
        ("0 0,3,6,9,12,15,18,21,23 * * *", "分钟 0；小时 0,3,6,9,12,15,18,21,23；日 *；月 *；周 *"),
    ],
)
def test_describe_falls_back_to_structured(expression, expected):
    assert describe(expression) == expected


def test_describe_rejects_invalid_expression():
    with pytest.raises(ScheduledTaskCronInvalidError) as excinfo:
        describe("@daily")
    assert excinfo.value.Code == 11903


def test_multiple_days_of_month_or_day_of_week():
    """回归（W2 对抗复核 finding-1）：合法但稀疏的表达式凑不齐 n 个时必须**返回已找到的**。

    `0 0 29 2 *`（闰日）在 8 年扫描窗口内最多 2 个触发点；此前实现把它当"永不触发"
    抛 11903，会让 AC-07 的"未来至多 5 次"预览对这条合法表达式直接报错。
    spec 的措辞是"至多"，返回更少合法（复核者实测 n=3 起必炸）。
    """
    anchor = datetime(2026, 10, 16, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert [d.year for d in next_runs("0 0 29 2 *", anchor, 1)] == [2028]
    assert [d.year for d in next_runs("0 0 29 2 *", anchor, 2)] == [2028, 2032]
    # 关键：不再抛错，返回窗口内能找到的全部（≤n）
    assert [d.year for d in next_runs("0 0 29 2 *", anchor, 5)] == [2028, 2032]

    # 但"窗口内一个都没有"（2 月 30 日）仍然是 11903
    with pytest.raises(ScheduledTaskCronInvalidError):
        next_runs("0 0 30 2 *", anchor, 1)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # 回归（W2 对抗复核 finding-2）：月字段恒为 AND 语义，dow 分支不得丢掉月份
        ("0 0 * 2 1", "每年 2 月的每周一 0:00"),
        ("0 0 * 1,7 5", "每年 1 月、7 月的每周五 0:00"),
    ],
)
def test_describe_keeps_month_restriction_with_day_of_week(expression, expected):
    assert describe(expression) == expected
