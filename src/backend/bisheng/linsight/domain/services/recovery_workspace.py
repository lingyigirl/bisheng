"""Attempt-specific object prefixes prevent stale workers overwriting new files."""

import asyncio

from bisheng.linsight.domain.repositories.recovery_repository import RecoveryRepository
from bisheng.linsight.domain.services.recovery_context import RecoveryBlocked
from bisheng.linsight.domain.services.workspace_backend import WorkspaceBackend


async def restore_attempt_workspace(session, minio, directory, fence):
    source_epoch = (session.recovery_state or {}).get("workspace_epoch", 0)
    source_id = f"{session.id}/attempt-{source_epoch}" if source_epoch else session.id
    source = WorkspaceBackend(source_id, minio, directory)
    target = WorkspaceBackend(f"{session.id}/attempt-{fence.epoch}", minio, directory)

    def copy():
        listing = source.ls("")
        if listing.error:
            raise RecoveryBlocked("Persisted workspace cannot be restored")
        for entry in listing.entries:
            path = entry["path"]
            if entry.get("is_dir") or (not source_epoch and path.lstrip("/").startswith("attempt-")):
                continue
            result = source.download_files([path])[0]
            if result.error or result.content is None:
                raise RecoveryBlocked("Persisted workspace file is missing")
            written = target.write(path, result.content)
            if written.error:
                raise RecoveryBlocked("Workspace copy failed")

    await fence.check()
    await asyncio.to_thread(copy)
    await fence.check()
    session.recovery_state = await RecoveryRepository().workspace_ready(session.id, fence.epoch)
    return target
