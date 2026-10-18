"""Repair missing Skill model registration and existing owner compensation.

Run from src/backend using the service's config environment variable:
  python scripts/migrate_fga_skill_permissions.py --report /protected/plan.json
  python scripts/migrate_fga_skill_permissions.py --expected-model-id OLD --apply --report /protected/apply.json
Restart API/workers (or pin the printed model id), then repair one tenant:
  python scripts/migrate_fga_skill_permissions.py --repair-owners --tenant-id 1 --apply --report /protected/owners.json

Default is read-only after normal app-context bootstrap. Requires an existing
store/model; bootstrap follows the service's existing initialization behavior.
Models are immutable; rollback pins the old id. Never resets dead retry rows or
replays deleted resources, changed owners, other relations, or other tenants.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

_BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

SKILL_TYPE = "linsight_skill"


def canonical(value):
    """Ignore only optional protobuf defaults, preserving userset semantics."""
    if isinstance(value, dict):
        return {
            k: canonical(v)
            for k, v in value.items()
            if not (k in {"condition", "relation", "object", "module", "source_info", "conditions"} and not v)
        }
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def add_skill_type(current, shipped):
    """Preserve every live type/condition; add only the missing Skill type."""
    expected = next(t for t in shipped["type_definitions"] if t["type"] == SKILL_TYPE)
    existing = [t for t in current["type_definitions"] if t["type"] == SKILL_TYPE]
    if existing:
        if len(existing) != 1 or canonical(existing[0]) != canonical(expected):
            raise ValueError("Existing Skill definition differs; manual compatibility review required")
        return None
    result = copy.deepcopy(current)
    result.pop("id", None)
    result["type_definitions"].append(copy.deepcopy(expected))
    return result


def owner_matches(item, skill, tenant_id):
    """Only restore the exact original owner of a still-existing tenant Skill."""
    return bool(
        skill is not None
        and skill.tenant_id == tenant_id
        and item.tenant_id == tenant_id
        and item.action == "write"
        and item.relation == "owner"
        and item.object == f"{SKILL_TYPE}:{skill.id}"
        and item.fga_user == f"user:{skill.created_by}"
        and item.status in {"dead", "pending"}
    )


async def repair_owners(fga, tenant_id, apply, report):
    from sqlmodel import select

    from bisheng.core.context.tenant import current_tenant_id, set_current_tenant_id
    from bisheng.core.database import get_async_db_session
    from bisheng.database.models.failed_tuple import FailedTuple
    from bisheng.linsight.domain.models.linsight_skill import LinsightSkill
    from bisheng.permission.domain.schemas.permission_schema import AuthorizeGrantItem
    from bisheng.permission.domain.services.permission_service import PermissionService

    token = set_current_tenant_id(tenant_id)
    try:
        async with get_async_db_session() as session:
            ids = list(
                (
                    await session.exec(
                        select(FailedTuple.id)
                        .where(
                            FailedTuple.tenant_id == tenant_id,
                            FailedTuple.object.like(f"{SKILL_TYPE}:%"),
                            FailedTuple.status.in_(["dead", "pending"]),
                        )
                        .order_by(FailedTuple.id)
                    )
                ).all()
            )
        for tuple_id in ids:
            # Lock the metadata and compensation row during validation/write so
            # concurrent deletion or ownership edits cannot resurrect access.
            async with get_async_db_session() as session:
                item = (
                    await session.exec(
                        select(FailedTuple)
                        .where(FailedTuple.id == tuple_id, FailedTuple.tenant_id == tenant_id)
                        .with_for_update()
                    )
                ).one_or_none()
                if item is None:
                    continue
                suffix = item.object.removeprefix(f"{SKILL_TYPE}:")
                skill = None
                if suffix.isdecimal():
                    skill = (
                        await session.exec(
                            select(LinsightSkill)
                            .where(LinsightSkill.id == int(suffix), LinsightSkill.tenant_id == tenant_id)
                            .with_for_update()
                        )
                    ).one_or_none()
                entry = {"tuple_id": tuple_id, "object": item.object}
                report["owners"].append(entry)
                if not owner_matches(item, skill, tenant_id):
                    entry["status"] = "skipped_stale_or_mismatch"
                    continue
                if not apply:
                    entry["status"] = "would_repair"
                    continue
                rows = await fga.read_tuples(user=item.fga_user, relation="owner", object=item.object)
                if not rows:
                    await PermissionService.authorize(
                        object_type=SKILL_TYPE,
                        object_id=str(skill.id),
                        grants=[AuthorizeGrantItem(subject_type="user", subject_id=skill.created_by, relation="owner")],
                        enforce_fga_success=True,
                    )
                rows = await fga.read_tuples(user=item.fga_user, relation="owner", object=item.object)
                if not rows or not await fga.check(
                    item.fga_user, "can_read", item.object, consistency="HIGHER_CONSISTENCY"
                ):
                    raise RuntimeError("Owner tuple verification failed")
                item.status = "succeeded"
                item.error_message = None
                session.add(item)
                await session.commit()
                entry["status"] = "succeeded"
    finally:
        current_tenant_id.reset(token)


async def run(args, report):
    from bisheng.core.openfga.authorization_model import MODEL_VERSION, get_authorization_model
    from bisheng.core.openfga.manager import aget_fga_client

    fga = await aget_fga_client()
    if fga is None:
        raise RuntimeError("OpenFGA is unavailable")
    report.update({"old_model_id": fga.model_id, "store_id": fga.store_id, "code_model_version": MODEL_VERSION})
    if args.expected_model_id and args.expected_model_id != fga.model_id:
        raise ValueError("Active model differs from expected model; no changes made")
    current = await fga.read_authorization_model(fga.model_id)
    updated = add_skill_type(current, get_authorization_model())
    if args.repair_owners:
        if updated is not None:
            raise ValueError("Publish the Skill model and restart services before owner repair")
        report["owners"] = []
        await repair_owners(fga, args.tenant_id, args.apply, report)
        report["status"] = "owners_checked"
    elif updated is None:
        report["status"] = "already_present"
    elif not args.apply:
        report["status"] = "would_add_skill_type"
    else:
        if not args.expected_model_id:
            raise ValueError("Model publication requires --expected-model-id from the preview")
        model_id = await fga.write_authorization_model(updated)
        report["new_model_id"] = model_id
        actual = await fga.read_authorization_model(model_id)
        actual.pop("id", None)
        if canonical(actual) != canonical(updated):
            raise RuntimeError("Published model differs from the requested additive model")
        report["status"] = "published_restart_required"


async def main_async(args, report):
    from bisheng.common.services.config_service import settings
    from bisheng.core.context.manager import close_app_context, initialize_app_context
    from bisheng.core.database import get_database_connection

    await initialize_app_context(config=settings)
    try:
        await run(args, report)
    finally:
        database = await get_database_connection()
        await database.close()
        await close_app_context()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-model-id")
    parser.add_argument("--repair-owners", action="store_true")
    parser.add_argument("--tenant-id", type=int)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.repair_owners and (args.tenant_id is None or args.tenant_id < 1):
        parser.error("--repair-owners requires a positive --tenant-id")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {"apply": args.apply, "tenant_id": args.tenant_id}
    code = 0
    try:
        asyncio.run(main_async(args, report))
    except Exception as exc:
        # Persist progress even if the process fails after a model/tuple write.
        report.update({"error_type": type(exc).__name__, "status": "failed"})
        from loguru import logger

        logger.exception("Skill permission maintenance failed")
        code = 1
    finally:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
        print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
