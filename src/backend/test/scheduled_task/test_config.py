"""F077 T-05：``scheduled_task`` 配置段与 beat 条目。

覆盖：
- spec AC-05/AC-12/AC-15/AC-37/AC-38/AC-41/AC-44/AC-55 的**默认值**来自配置类
  （design §3 决策 8 的键表），且可被 ``config.yaml`` 覆盖
- beat 条目 ``scheduled_task_scan`` 的三条硬约束：**数值秒**（不是 cron 字符串——
  字符串会落进 ``settings.py`` 的 ``crontab(str)`` 隐患：``crontab("30 2 * * *")``
  实测抛 ``ValueError``、``crontab("sun")`` 静默解析成 minute={0}）、
  **显式 ``options.queue``**（``task_routers`` 非空即整表替换，靠路由会掉进没人消费的队列）、
  以及 20 秒 ≤ spec AC-10 的 30 秒上界
"""

from bisheng.core.config.scheduled_task import ScheduledTaskConf
from bisheng.core.config.settings import CeleryConf, Settings

BEAT_KEY = "scheduled_task_scan"


def test_defaults_match_design():
    conf = ScheduledTaskConf()
    assert conf.global_max_running == 10
    assert conf.max_tasks_per_app == 20
    assert conf.run_timeout_seconds == 3600
    assert conf.consecutive_failure_threshold == 5
    assert conf.replay_window_seconds == 300
    assert conf.event_rate_limit_per_minute == 10
    assert conf.preset_input_max_items == 20
    assert conf.preset_input_max_chars == 2000
    assert conf.nl_max_chars == 2000
    assert conf.preview_count == 5
    assert conf.dispatch_lock_ttl_seconds == 15


def test_yaml_section_overrides_defaults():
    """`Settings(**section)` 的装载形态：YAML 段覆盖默认值（house 装配方式）。"""
    settings = Settings(scheduled_task={"global_max_running": 3, "max_tasks_per_app": 7})
    assert settings.scheduled_task.global_max_running == 3
    assert settings.scheduled_task.max_tasks_per_app == 7
    # 未覆盖的键保持默认
    assert settings.scheduled_task.run_timeout_seconds == 3600


def test_beat_entry_is_numeric_and_pinned_to_default_queue():
    entry = CeleryConf().beat_schedule[BEAT_KEY]

    assert entry["task"] == "bisheng.worker.scheduled_task.scan.scan_scheduled_tasks"
    # 数值秒：字符串会走 `crontab(str)` 的转换路径（该路径实测对多数表达式直接抛错）
    assert isinstance(entry["schedule"], float)
    assert entry["schedule"] == 20.0
    # 20s 的节拍给 AC-10 的 30s 上界留出轮内处理余量
    assert entry["schedule"] <= 30.0
    # 显式队列：不依赖 task_routers（config.yaml 一给该节，默认路由表整体失效）
    assert entry["options"]["queue"] == "celery"


def test_beat_entry_stays_a_float_after_validate():
    """`CeleryConf.validate` 的 str→crontab 转换不得触碰本条目（它不是字符串）。"""
    conf = CeleryConf()
    conf.validate()
    assert isinstance(conf.beat_schedule[BEAT_KEY]["schedule"], float)
