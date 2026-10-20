"""F078 test fixtures.

``test/conftest.py`` replaces celery with MagicMock to keep the settings import
chain light. The managed-agent NL draft tests exercise the real cron parser, so
this directory restores the real ``celery.schedules`` for the duration of each
test (same shim as ``test/scheduled_task/conftest.py``).
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
    monkeypatch.setattr(cron_schedule_module, "crontab", _REAL.crontab)
    monkeypatch.setattr(cron_schedule_module, "ParseException", _REAL.ParseException)


@pytest.fixture(autouse=True)
def _enable_managed_agent(monkeypatch):
    """Most F078 tests exercise the enabled path; the flag-specific tests
    explicitly override this fixture."""
    from bisheng.common.services.config_service import settings

    monkeypatch.setattr(settings.managed_agent, "enabled", True)
