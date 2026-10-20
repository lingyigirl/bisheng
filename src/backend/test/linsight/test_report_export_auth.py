"""覆盖 AC: AC-13, AC-20 - authorize before reading any workspace bytes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.linsight.domain.repositories.agent_config_repository import AgentConfigRepository
from bisheng.linsight.domain.services.report_export import ReportExportService
from bisheng.permission.domain.services.permission_service import PermissionService


async def test_save_as_denies_unowned_task_before_storage(monkeypatch):
    monkeypatch.setattr("bisheng.core.context.tenant.get_current_tenant_id", lambda: 1)
    owned = AsyncMock(return_value=SimpleNamespace(id="other-task", user_id=1, tenant_id=1, session_id="chat"))
    monkeypatch.setattr(AgentConfigRepository, "task", owned)
    request = SimpleNamespace(
        session_version_id="other-task", file_url="workspace/other-task/output/a.md", file_name="a.md"
    )
    with pytest.raises(UnAuthorizedError):
        await ReportExportService().save_as(request, "pdf", SimpleNamespace(user_id=2, is_admin=lambda: False))
    owned.assert_awaited_once()


async def test_save_as_uses_owned_workspace_and_shared_renderer(monkeypatch):
    monkeypatch.setattr("bisheng.core.context.tenant.get_current_tenant_id", lambda: 1)
    import bisheng.core.storage.minio.minio_manager as storage
    import bisheng.linsight.domain.services.report_export as service

    monkeypatch.setattr(
        AgentConfigRepository,
        "task",
        AsyncMock(
            return_value=SimpleNamespace(id="owned", user_id=1, tenant_id=1, session_id="chat", output_result={})
        ),
    )
    monkeypatch.setattr(storage, "get_minio_storage", AsyncMock(return_value=SimpleNamespace(bucket="test-bucket")))
    render = AsyncMock(return_value=(b"%PDF verified", {"pages": 1}))
    monkeypatch.setattr(service, "export_workspace_report", render)
    request = SimpleNamespace(session_version_id="owned", file_url="workspace/owned/output/a.md", file_name="a.md")
    filename, data, manifest = await ReportExportService().save_as(
        request, "pdf", SimpleNamespace(user_id=1, is_admin=lambda: False)
    )
    assert filename == "a.pdf" and manifest["pages"] == 1 and data.startswith(b"%PDF")
    assert render.await_args.args[1:] == ("output/a.md", "pdf")


async def test_save_as_denies_forged_artifact_of_owned_task(monkeypatch):
    monkeypatch.setattr("bisheng.core.context.tenant.get_current_tenant_id", lambda: 1)
    import bisheng.core.storage.minio.minio_manager as storage

    monkeypatch.setattr(
        AgentConfigRepository,
        "task",
        AsyncMock(
            return_value=SimpleNamespace(
                id="owned", user_id=1, tenant_id=1, session_id="chat", output_result={"final_files": []}
            )
        ),
    )
    monkeypatch.setattr(storage, "get_minio_storage", AsyncMock(return_value=SimpleNamespace(bucket="test-bucket")))
    request = SimpleNamespace(
        session_version_id="owned", file_url="linsight/final_result/other/private.md", file_name="a.md"
    )
    with pytest.raises(UnAuthorizedError):
        await ReportExportService().save_as(request, "docx", SimpleNamespace(user_id=1, is_admin=lambda: False))


@pytest.mark.parametrize("mode", ["owner", "admin", "share", "wrong-share", "expired-share", "foreign-tenant"])
def test_read_export_scope_matches_existing_share_boundary(tenant_context, mode):
    tenant_context(1)
    task = SimpleNamespace(id="task", session_id="chat", user_id=10, tenant_id=1)
    user = SimpleNamespace(user_id=10 if mode in {"owner", "foreign-tenant"} else 20, is_admin=lambda: mode == "admin")
    share = None
    if mode in {"share", "wrong-share", "expired-share"}:
        share = SimpleNamespace(
            tenant_id=1,
            resource_type="workbench_chat",
            status="active",
            resource_id="other" if mode == "wrong-share" else "chat",
            expire_time=1 if mode == "expired-share" else 0,
            meta_data=None,
        )
    if mode == "foreign-tenant":
        tenant_context(2)
    assert PermissionService.can_read_task_artifact(user, task, share) == (mode in {"owner", "admin", "share"})
