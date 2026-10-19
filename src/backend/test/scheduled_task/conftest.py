"""F077 测试目录的公共夹具。

**为什么需要这个 shim**：``test/conftest.py`` 为了隔断 ``settings.py`` 的导入链，
会把 ``celery`` / ``celery.schedules`` 预替换成 MagicMock（``crontab.from_string``
返回 MagicMock、字段集合恒空）。而 F077 的调度链路（``cron_schedule.next_runs``）
依赖**真实**的 cron 解析语义——任何"计划轮次要算下一跳"的用例（run_service 等）
都必须在这个目录里把真件换回去，否则会在空集合上抛错。

做法与 ``test/fixtures/mock_services.py`` 的"真模块先占 sys.modules 即跳过 mock"
同源：临时摘掉被 mock 的名字导入真包，随后立刻把 mock 放回去——同一会话里其它
用例看到的 celery 状态不变。``monkeypatch`` 负责在用例结束后还原被测模块的属性。
"""

import importlib
import sys

import pytest

from bisheng.scheduled_task.domain.services import cron_schedule as cron_schedule_module

_CELERY_MOCKED = ("celery", "celery.schedules", "celery.app", "celery.app.task", "celery.signals")


def _load_real_celery_schedules():
    mocked = {name: sys.modules[name] for name in _CELERY_MOCKED if name in sys.modules}
    for name in mocked:
        del sys.modules[name]
    try:
        return importlib.import_module("celery.schedules")
    finally:
        sys.modules.update(mocked)


_REAL = _load_real_celery_schedules()


@pytest.fixture(autouse=True)
def _real_crontab_shim(monkeypatch):
    """把真实 ``crontab`` / ``ParseException`` 注入被测的 cron 模块。"""
    monkeypatch.setattr(cron_schedule_module, "crontab", _REAL.crontab)
    monkeypatch.setattr(cron_schedule_module, "ParseException", _REAL.ParseException)
