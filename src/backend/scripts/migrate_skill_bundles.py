"""Explicit Skill bundle migration/restore; preview is the default."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from bisheng.core.context.manager import close_app_context  # noqa: E402
from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id  # noqa: E402
from bisheng.core.database import get_database_connection  # noqa: E402
from bisheng.linsight.domain.repositories.skill_repository import SkillRepository  # noqa: E402
from bisheng.linsight.domain.services.skill_bundle_cache import (  # noqa: E402
    check_ancestors,
    content_hash,
    install,
    read_directory,
)
from bisheng.linsight.domain.services.skill_store import SkillStore  # noqa: E402


async def migrate_one(repository, store, row, apply=False, restore_root=None):
    item = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "previous_object_path": row.object_path,
        "previous_content_hash": row.content_hash,
    }
    try:
        if restore_root is not None:
            if not row.content_hash:
                return {**item, "status": "already_done"}
            files = await asyncio.to_thread(store.load_bundle, row.tenant_id, row.name, row.content_hash)
            destination = Path(restore_root).resolve() / store.object_path(row.tenant_id, row.name)
            check_ancestors(destination, Path(restore_root).resolve())
            if destination.exists() and content_hash(read_directory(destination)) != content_hash(files):
                return {**item, "status": "conflict", "category": "LegacyDestinationDiffers"}
            if not apply:
                return {**item, "status": "pending_restore"}
            if not destination.exists():
                await asyncio.to_thread(install, destination, files, Path(restore_root).resolve())
            values = {"content_hash": None, "object_path": store.object_path(row.tenant_id, row.name)}
            ok = await repository.publish(row, values)
            return {**item, "status": "restored" if ok else "conflict"}
        if row.content_hash:
            # Verify the authority, not a cache hit, before declaring completion.
            await asyncio.to_thread(store._download, row.tenant_id, row.name, row.content_hash)
            return {**item, "status": "already_done"}
        files = await asyncio.to_thread(store.load_bundle, row.tenant_id, row.name)
        if not apply:
            return {**item, "status": "pending", "bytes": sum(map(len, files.values()))}
        ref = await asyncio.to_thread(store.write_bundle, row.tenant_id, row.name, files)
        ok = await repository.publish(
            row, {"content_hash": ref.content_hash, "object_path": ref.object_key, "size": ref.size}
        )
        return {
            **item,
            "status": "migrated" if ok else "conflict",
            "object_path": ref.object_key,
            "content_hash": ref.content_hash,
        }
    except Exception as exc:
        # This is a per-record operational report; continue with other records.
        return {
            **item,
            "status": "missing" if isinstance(exc, FileNotFoundError) else "failed",
            "category": type(exc).__name__,
        }


async def run(args):
    token = set_current_tenant_id(args.tenant_id)
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting an earlier audit report, which may be needed for rollback.
    descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    failures = 0
    try:
        repository = SkillRepository()
        store = SkillStore(legacy_root=args.legacy_root)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            after = 0
            while rows := await repository.page_after(after):
                for row in rows:
                    item = await migrate_one(repository, store, row, args.apply, args.restore_legacy)
                    stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                    stream.flush()
                    print(json.dumps({"id": row.id, "status": item["status"], "category": item.get("category")}))
                    failures += item["status"] in {"failed", "missing", "conflict"}
                after = rows[-1].id
    finally:
        current_tenant_id.reset(token)
        # Lazy registration does not initialize ApplicationContext's close order.
        # Dispose the database explicitly before asyncio closes the loop.
        database = await get_database_connection()
        await database.close()
        await close_app_context()
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--legacy-root", type=Path)
    parser.add_argument("--report", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-legacy", type=Path)
    args = parser.parse_args()
    if args.tenant_id < 1:
        parser.error("tenant-id must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
