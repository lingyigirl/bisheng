"""智能体发布审批 (agent_publish_request) 测试 — F056-审批扩展。

口径（用户明确要求）：
- 场景未配置/停用 → 直接发布（fail-open，不走审批）
- 场景启用 + flow 路由 → 提交审批（PENDING，本次不发布，通知审批人）
- 场景启用 + pass 路由 → 直接发布
- 场景配置异常（路由缺失）→ 阻止发布（ApprovalScenarioExceptionError）
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from bisheng.api.services.agent_publish import (
    _publish_assistant_snapshot,
    _publish_workflow_snapshot,
    gate_agent_publish,
)
from bisheng.approval.domain.models.approval_instance import (
    ApprovalActionLog,
    ApprovalException,
    ApprovalInstance,
    ApprovalOutbox,
    ApprovalTask,
)
from bisheng.approval.domain.models.approval_scenario import (
    ApprovalFlowDefinition,
    ApprovalFlowVersion,
    ApprovalNodeDefinition,
    ApprovalRouteRule,
    ApprovalScenario,
)
from bisheng.approval.domain.repositories.approval_scenario_repository import ApprovalScenarioRepository
from bisheng.approval.domain.services.agent_publish_handler import AgentPublishApprovalHandler
from bisheng.approval.domain.services.approval_runtime_handler_factory import build_runtime_handler
from bisheng.common.errcode.approval import ApprovalScenarioExceptionError

TABLES = [
    ApprovalScenario.__table__,
    ApprovalRouteRule.__table__,
    ApprovalFlowDefinition.__table__,
    ApprovalFlowVersion.__table__,
    ApprovalNodeDefinition.__table__,
    ApprovalInstance.__table__,
    ApprovalTask.__table__,
    ApprovalException.__table__,
    ApprovalOutbox.__table__,
    ApprovalActionLog.__table__,
]


@pytest_asyncio.fixture
async def approval_db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=TABLES))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def patched_repositories(approval_db_engine, monkeypatch):
    @asynccontextmanager
    async def _factory():
        async with AsyncSession(bind=approval_db_engine) as session:
            yield session

    monkeypatch.setattr(
        "bisheng.approval.domain.repositories.approval_scenario_repository.get_async_db_session", _factory
    )
    monkeypatch.setattr(
        "bisheng.approval.domain.repositories.approval_instance_repository.get_async_db_session", _factory
    )
    monkeypatch.setattr(
        "bisheng.approval.domain.repositories.approval_query_repository.get_async_db_session", _factory
    )
    monkeypatch.setattr(
        "bisheng.database.models.department.UserDepartmentDao.aget_user_primary_department",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bisheng.approval.domain.services.approval_notification_service.ApprovalNotificationService.notify_users",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bisheng.approval.domain.services.approval_notification_service.ApprovalNotificationService.notify_admins",
        AsyncMock(return_value=None),
    )
    # Audit DAO hits the real async engine; keep unit tests off it (cross-loop reuse crashes).
    monkeypatch.setattr(
        "bisheng.database.models.audit_log.AuditLogDao.ainsert_v2",
        AsyncMock(return_value=None),
    )
    yield


def _login_user():
    return SimpleNamespace(user_id=7, tenant_id=1, user_name="tester")


async def _seed_scenario(route_type: str = "flow", with_node: bool = True):
    scenario = await ApprovalScenarioRepository.create_scenario(
        ApprovalScenario(
            tenant_id=1,
            scenario_code="agent_publish_request",
            scenario_name="智能体发布申请",
            enabled=True,
        )
    )
    if route_type == "flow":
        flow_definition = await ApprovalScenarioRepository.create_flow_definition(
            ApprovalFlowDefinition(
                tenant_id=1,
                scenario_id=scenario.id,
                flow_code="publish_flow",
                flow_name="发布审批流",
                is_active=True,
            )
        )
        flow_version = await ApprovalScenarioRepository.create_flow_version(
            ApprovalFlowVersion(
                tenant_id=1,
                flow_definition_id=flow_definition.id,
                version_no=1,
                is_active=True,
                definition_snapshot={"version": 1},
            )
        )
        route = await ApprovalScenarioRepository.create_route_rule(
            ApprovalRouteRule(
                tenant_id=1,
                scenario_id=scenario.id,
                route_name="default publish route",
                route_type="flow",
                sort_order=1,
                flow_definition_id=flow_definition.id,
                match_config={"all": True},
            )
        )
        if with_node:
            await ApprovalScenarioRepository.create_node_definition(
                ApprovalNodeDefinition(
                    tenant_id=1,
                    flow_version_id=flow_version.id,
                    node_code="first_node",
                    node_name="一级审批",
                    node_order=1,
                    node_mode="or",
                    approver_config={
                        "sources": [{"type": "direct_user", "user_ids": [7], "user_names": ["tester"]}]
                    },
                    extra_config={},
                )
            )
    else:
        route = await ApprovalScenarioRepository.create_route_rule(
            ApprovalRouteRule(
                tenant_id=1,
                scenario_id=scenario.id,
                route_name="direct pass",
                route_type="pass",
                sort_order=1,
                match_config={"all": True},
            )
        )
    return scenario, route


async def test_disabled_scenario_publishes_directly(patched_repositories):
    """场景未配置 → 直接发布 (fail-open)."""
    outcome = await gate_agent_publish(
        _login_user(),
        app_type="workflow",
        app_id="flow-1",
        business_name="风控助手",
        version_id=3,
    )
    assert outcome.publish_now is True
    assert outcome.pending is False


async def test_enabled_flow_route_creates_pending(patched_repositories):
    """场景启用 + flow 路由 → PENDING, 本次不发布, 通知审批人."""
    await _seed_scenario(route_type="flow")

    outcome = await gate_agent_publish(
        _login_user(),
        app_type="workflow",
        app_id="flow-1",
        business_name="风控助手",
        version_id=3,
    )

    assert outcome.publish_now is False
    assert outcome.pending is True
    assert outcome.instance_id is not None
    assert outcome.task_ids  # 首节点任务已创建


async def test_enabled_pass_route_publishes_now(patched_repositories):
    """场景启用 + pass 路由 → 直接发布."""
    await _seed_scenario(route_type="pass")

    outcome = await gate_agent_publish(
        _login_user(),
        app_type="assistant",
        app_id="assistant-1",
        business_name="客服助手",
    )

    assert outcome.publish_now is True
    assert outcome.pending is False


async def test_enabled_scenario_without_routes_blocks(patched_repositories):
    """场景启用但无路由 → 配置异常, 阻止发布."""
    await ApprovalScenarioRepository.create_scenario(
        ApprovalScenario(
            tenant_id=1,
            scenario_code="agent_publish_request",
            scenario_name="智能体发布申请",
            enabled=True,
        )
    )

    with pytest.raises(ApprovalScenarioExceptionError):
        await gate_agent_publish(
            _login_user(),
            app_type="workflow",
            app_id="flow-1",
            business_name="风控助手",
        )


async def test_handler_on_approved_invokes_publish_app():
    called = []

    async def fake_publish(snapshot):
        called.append(snapshot)

    handler = AgentPublishApprovalHandler(publish_app=fake_publish)
    result = await handler.on_approved(1, {"app_type": "workflow", "flow_id": "f1"})
    assert result == {"status": "published"}
    assert called == [{"app_type": "workflow", "flow_id": "f1"}]


async def test_handler_on_approved_raises_without_snapshot_or_executor():
    handler = AgentPublishApprovalHandler()
    with pytest.raises(RuntimeError):
        await handler.on_approved(1, {"app_type": "workflow"})

    async def fake_publish(snapshot):
        return None

    handler = AgentPublishApprovalHandler(publish_app=fake_publish)
    with pytest.raises(ValueError):
        await handler.on_approved(1, {})


async def test_factory_builds_agent_publish_handler():
    handler = await build_runtime_handler("agent_publish_request")
    assert isinstance(handler, AgentPublishApprovalHandler)
    assert handler.publish_app is not None


async def test_publish_workflow_snapshot_sets_online(monkeypatch):
    version_changed = []

    class _FakeVersion:
        flow_id = "flow-1"
        data = {"nodes": []}

    class _FakeFlow:
        user_id = 7
        name = "风控助手"
        status = 1

    async def _aget_flow(flow_id):
        return _FakeFlow()

    async def _aget_version(version_id):
        return _FakeVersion()

    async def _change_current(flow_id, version_info):
        version_changed.append(flow_id)

    updated = []

    async def _aupdate(flow):
        updated.append(flow)
        return flow

    class _FakeWorkflow:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("bisheng.database.models.flow.FlowDao.aget_flow_by_id", _aget_flow)
    monkeypatch.setattr("bisheng.database.models.flow_version.FlowVersionDao.aget_version_by_id", _aget_version)
    monkeypatch.setattr("bisheng.database.models.flow_version.FlowVersionDao.change_current_version", _change_current)
    monkeypatch.setattr("bisheng.database.models.flow.FlowDao.aupdate_flow", _aupdate)
    monkeypatch.setattr("bisheng.workflow.graph.workflow.Workflow", _FakeWorkflow)
    monkeypatch.setattr("bisheng.api.services.agent_publish._log_publish_telemetry", AsyncMock())

    await _publish_workflow_snapshot(
        {"app_type": "workflow", "flow_id": "flow-1", "version_id": 5, "applicant_user_id": 7}
    )

    assert updated and updated[0].status == 2  # ONLINE (snapshot re-fetches the flow itself)
    assert version_changed == ["flow-1"]


async def test_publish_assistant_snapshot_sets_online(monkeypatch):
    class _FakeAssistant:
        id = "assistant-1"
        user_id = 7
        status = 1

    assistant = _FakeAssistant()
    monkeypatch.setattr(
        "bisheng.database.models.assistant.AssistantDao.get_one_assistant", lambda assistant_id: assistant
    )
    monkeypatch.setattr(
        "bisheng.database.models.assistant.AssistantDao.update_assistant", lambda a: a
    )

    class _FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        async def init_assistant(self):
            return None

    monkeypatch.setattr("bisheng.api.services.assistant_agent.AssistantAgent", _FakeAgent)
    monkeypatch.setattr("bisheng.api.services.agent_publish._log_publish_telemetry", AsyncMock())

    await _publish_assistant_snapshot(
        {"app_type": "assistant", "assistant_id": "assistant-1", "applicant_user_id": 7}
    )

    assert assistant.status == 2  # ONLINE
