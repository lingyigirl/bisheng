"""F078: deployment switch off means 404 / no-op / fail-closed.

The routers are mounted at import time using the config loaded by the process.
These tests assert the default (off) route table and the runtime fail-closed
branches; the enabled-path tests live in the other files (conftest turns the
flag on for them).
"""

from types import SimpleNamespace

import pytest

from bisheng.api.router import router, router_rpc
from bisheng.common.errcode.scheduled_task import ScheduledTaskConfigInvalidError
from bisheng.common.services.config_service import settings
from bisheng.managed_agent.domain.constants import MANAGED_AGENT_APP_TYPE
from bisheng.managed_agent.domain.services import health_service
from bisheng.scheduled_task.domain.services import run_service, trigger_guard
from bisheng.scheduled_task.domain.services.run_planner import AppState


def test_routes_are_not_mounted_when_flag_is_off(monkeypatch):
    # Import-time snapshot: the default config has managed_agent.enabled=false.
    # The directory conftest turns the flag on for the enabled-path tests; this
    # test opts back out for the runtime assertions.
    monkeypatch.setattr(settings.managed_agent, "enabled", False)
    v2_paths = {getattr(route, "path", "") for route in router_rpc.routes}
    v1_paths = {getattr(route, "path", "") for route in router.routes}
    if "/api/v2/managed-agent/invoke" in v2_paths:
        # The route table is built at import time. A process booted with the
        # site config (enabled=true) cannot test the unmounted state; the
        # default config.yaml covers it.
        pytest.skip("requires a process booted with managed_agent.enabled=false (config.yaml)")
    assert "/api/v2/managed-agent/invoke" not in v2_paths
    assert "/api/v1/managed-agents" not in v1_paths
    assert "/api/v1/managed-agent-taxonomies" not in v1_paths


async def test_scheduling_branches_fail_closed(monkeypatch):
    monkeypatch.setattr(settings.managed_agent, "enabled", False)
    task = SimpleNamespace(
        app_type=MANAGED_AGENT_APP_TYPE,
        app_id="1",
        tenant_id=7,
        service_account_id=5,
        run_identity_user_id=42,
    )
    assert await trigger_guard._app_state(task) is AppState.OFFLINE
    assert await trigger_guard._app_exists("1", MANAGED_AGENT_APP_TYPE) is False
    with pytest.raises(ScheduledTaskConfigInvalidError):
        await run_service._dispatch_managed_agent(task, "trace-1", "cron")


async def test_health_scan_is_a_no_op(monkeypatch):
    monkeypatch.setattr(settings.managed_agent, "enabled", False)
    assert await health_service.scan_once() == {"checked": 0, "failed": 0, "total": 0}
