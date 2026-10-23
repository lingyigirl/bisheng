"""F078 registry lifecycle: create/update/status/delete/list.

Permission model (design 决策 15 / spec §5.1):
- create/delete: global super admin or the current tenant admin;
- view: ``viewer`` (tenant admin short-circuits);
- edit/health: ``manager``;
- invoke: ``editor`` (checked by the invoke service).

All reads are strict-tenant (a child admin must not see ROOT rows), and every
write is a CAS/bulk update with an explicit ``tenant_id``. The owner tuple is
written with ``enforce_fga_success=True``; if FGA is unavailable the row is
removed again so a caller never sees an ownerless resource.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from bisheng.common.errcode.managed_agent import (
    ManagedAgentConfigInvalidError,
    ManagedAgentDuplicateError,
    ManagedAgentInUseError,
    ManagedAgentNotFoundError,
    ManagedAgentProtocolUnsupportedError,
    ManagedAgentTaxonomyInvalidError,
    ManagedAgentUnavailableError,
)
from bisheng.common.security.outbound_url import (
    OutboundUrlRejected,
    avalidate_outbound_url,
    native_endpoint_hash,
)
from bisheng.common.security.outbound_url import (
    endpoint_hash as make_endpoint_hash,
)
from bisheng.common.services.config_service import settings
from bisheng.core.context.tenant import get_current_tenant_id
from bisheng.database.models.audit_log import AuditLogDao
from bisheng.database.models.group_resource import ResourceTypeEnum
from bisheng.database.models.tag import TagDao
from bisheng.managed_agent.domain.constants import ManagedAgentAuditAction
from bisheng.managed_agent.domain.models.dao import (
    ManagedAgentDao,
    ManagedAgentTaxonomyDao,
)
from bisheng.managed_agent.domain.models.managed_agent import (
    AgentProtocol,
    AgentStatus,
    CredentialType,
    HealthMethod,
    HealthStatus,
    ManagedAgent,
    SourceKind,
)
from bisheng.managed_agent.domain.schemas.agent import (
    ManagedAgentCreateReq,
    ManagedAgentUpdateReq,
)
from bisheng.managed_agent.domain.services.agent_card import parse_agent_card
from bisheng.managed_agent.domain.services.credential_service import (
    decrypt_credential,
    encrypt_credential,
    mask_credential,
)
from bisheng.permission.domain.services.owner_service import OwnerService
from bisheng.permission.domain.services.permission_service import PermissionService


def _conf():
    return settings.managed_agent


async def _is_tenant_admin(user) -> bool:
    if getattr(user, "is_admin", lambda: False)():
        return True
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return False
    checker = getattr(user, "has_tenant_admin", None)
    if checker is None:
        return False
    try:
        return bool(await checker(tenant_id))
    except Exception:
        logger.warning("managed_agent: tenant-admin check failed; treating as non-admin")
        return False


async def _assert_perm(user, relation: str, agent_id: int) -> None:
    allowed = await PermissionService.check(
        user.user_id,
        relation,
        "managed_agent",
        str(agent_id),
        login_user=user,
    )
    if not allowed:
        # Anti-enumeration: missing and not-visible are indistinguishable.
        raise ManagedAgentNotFoundError()


async def _assert_manager_or_admin(user, agent_id: int) -> None:
    if await _is_tenant_admin(user):
        return
    await _assert_perm(user, "manager", agent_id)


async def _audit(user, action: ManagedAgentAuditAction, agent: ManagedAgent, **metadata) -> None:
    try:
        await AuditLogDao.ainsert_v2(
            tenant_id=agent.tenant_id,
            operator_id=getattr(user, "user_id", 0),
            operator_tenant_id=agent.tenant_id,
            action=action.value,
            target_type="managed_agent",
            target_id=str(agent.id),
            metadata=metadata or None,
            object_name=agent.name,
        )
    except Exception:
        logger.exception("managed_agent: audit write failed for {} on {}", action.value, agent.id)


async def _assert_taxonomy(kind: str, code: str | None) -> None:
    if not code:
        return
    row = await ManagedAgentTaxonomyDao.aget_by_code(kind, code)
    if row is None or not row.is_enabled:
        raise ManagedAgentTaxonomyInvalidError(msg=f"{kind} code is missing or disabled")


async def _native_app_exists(native_app_id: str, native_app_type: int) -> bool:
    if native_app_type == 5:
        from bisheng.database.models.assistant import AssistantDao

        row = await AssistantDao.aget_one_assistant(native_app_id)
        return row is not None and int(getattr(row, "is_delete", 0) or 0) == 0
    if native_app_type == 10:
        from bisheng.database.models.flow import FlowDao, FlowType

        row = await FlowDao.aget_flow_by_id(str(native_app_id))
        return row is not None and row.flow_type == FlowType.WORKFLOW.value
    return False


async def _validate_endpoint(url: str) -> str:
    try:
        return await avalidate_outbound_url(
            url,
            allowlist=_conf().egress_allowlist,
            allow_private_networks=_conf().allow_private_networks,
        )
    except OutboundUrlRejected as exc:
        from bisheng.common.errcode.managed_agent import ManagedAgentOutboundRejectedError

        raise ManagedAgentOutboundRejectedError(msg=str(exc)) from exc


async def _validate_source(req: ManagedAgentCreateReq | ManagedAgentUpdateReq, current: ManagedAgent | None = None):
    """Return (source_kind, protocol, native ids, endpoint, endpoint_hash)."""
    source_kind = getattr(req, "source_kind", None) or (current.source_kind if current else None)
    protocol = getattr(req, "protocol", None) or (current.protocol if current else None)
    if source_kind not in {item.value for item in SourceKind}:
        raise ManagedAgentProtocolUnsupportedError(msg="invalid source_kind")
    if protocol not in {item.value for item in AgentProtocol}:
        raise ManagedAgentProtocolUnsupportedError(msg="invalid protocol")

    if source_kind == SourceKind.NATIVE.value:
        if protocol != AgentProtocol.NATIVE.value:
            raise ManagedAgentProtocolUnsupportedError(msg="native source requires native protocol")
        native_app_id = getattr(req, "native_app_id", None) or (current.native_app_id if current else None)
        native_app_type = getattr(req, "native_app_type", None)
        if native_app_type is None and current is not None:
            native_app_type = current.native_app_type
        if not native_app_id or native_app_type not in (5, 10):
            raise ManagedAgentConfigInvalidError(msg="native projection needs app id and type (5/10)")
        if not await _native_app_exists(native_app_id, native_app_type):
            raise ManagedAgentConfigInvalidError(msg="native app does not exist or has the wrong type")
        return (
            source_kind,
            protocol,
            native_app_id,
            int(native_app_type),
            None,
            native_endpoint_hash(int(native_app_type), str(native_app_id)),
        )

    if protocol == AgentProtocol.NATIVE.value:
        raise ManagedAgentProtocolUnsupportedError(msg="external source cannot use native protocol")
    endpoint = getattr(req, "endpoint", None) or (current.endpoint if current else None)
    if not endpoint:
        raise ManagedAgentConfigInvalidError(msg="external agent needs an endpoint")
    canonical = await _validate_endpoint(endpoint)
    return source_kind, protocol, None, None, endpoint, make_endpoint_hash(canonical)


def _validate_health(health_method: str, health_url: str | None) -> None:
    if health_method not in {item.value for item in HealthMethod}:
        raise ManagedAgentConfigInvalidError(msg="invalid health_method")
    if health_method == HealthMethod.HEALTH_URL.value and not health_url:
        raise ManagedAgentConfigInvalidError(msg="health_url is required when health_method=health_url")


async def create_agent(user, req: ManagedAgentCreateReq) -> ManagedAgent:
    if not await _is_tenant_admin(user):
        raise ManagedAgentNotFoundError(msg="only tenant administrators may register agents")

    source_kind, protocol, native_app_id, native_app_type, endpoint, endpoint_hash = await _validate_source(req)
    _validate_health(req.health_method, req.health_url)
    if req.health_method == HealthMethod.HEALTH_URL.value and req.health_url:
        await _validate_endpoint(req.health_url)
    parse_agent_card(req.agent_card)
    await _assert_taxonomy("scenario", req.scenario_code)
    await _assert_taxonomy("industry", req.industry_code)

    if await ManagedAgentDao.aget_by_name(req.name):
        raise ManagedAgentDuplicateError(msg="name already exists")
    if await ManagedAgentDao.aget_by_endpoint_hash(endpoint_hash):
        raise ManagedAgentDuplicateError(msg="endpoint already exists")
    if await ManagedAgentDao.acount_by_tenant() >= _conf().max_agents_per_tenant:
        raise ManagedAgentConfigInvalidError(msg="tenant managed-agent limit reached")

    credential_ciphertext, credential_type, has_credential = ("", CredentialType.NONE.value, False)
    if req.credential is not None:
        credential_ciphertext, credential_type, has_credential = encrypt_credential(
            req.credential.auth_type,
            req.credential.secret,
            req.credential.headers,
        )

    agent = ManagedAgent(
        name=req.name,
        description=req.description,
        source_kind=source_kind,
        protocol=protocol,
        status=AgentStatus.DRAFT.value,
        version=req.version,
        native_app_id=native_app_id,
        native_app_type=native_app_type,
        endpoint=endpoint,
        endpoint_hash=endpoint_hash,
        model_name=req.model_name,
        agent_card=req.agent_card,
        credential_ciphertext=credential_ciphertext or None,
        credential_type=credential_type,
        has_credential=has_credential,
        health_url=req.health_url,
        health_method=req.health_method,
        health_status=HealthStatus.UNKNOWN.value,
        scenario_code=req.scenario_code,
        industry_code=req.industry_code,
        user_id=user.user_id,
    )
    agent = await ManagedAgentDao.acreate(agent)
    try:
        if req.tags:
            await TagDao.aupdate_resource_tags(req.tags, str(agent.id), ResourceTypeEnum.MANAGED_AGENT, user.user_id)
        await OwnerService.write_owner_tuple(
            user.user_id,
            "managed_agent",
            str(agent.id),
            enforce_fga_success=True,
        )
    except Exception:
        # Do not leave an ownerless/tag-orphaned resource behind. Tag cleanup is
        # best-effort: the original failure is what the caller must see.
        try:
            await TagDao.aupdate_resource_tags([], str(agent.id), ResourceTypeEnum.MANAGED_AGENT, user.user_id)
        except Exception:
            logger.exception("managed agent tag cleanup failed during create rollback for {}", agent.id)
        await ManagedAgentDao.adelete(agent.id, tenant_id=agent.tenant_id)
        raise
    await _audit(user, ManagedAgentAuditAction.CREATE, agent)
    return agent


async def get_agent(user, agent_id: int) -> ManagedAgent:
    agent = await ManagedAgentDao.aget(agent_id)
    if agent is None:
        raise ManagedAgentNotFoundError()
    if not await _is_tenant_admin(user):
        await _assert_perm(user, "viewer", agent_id)
    return agent


async def update_agent(user, agent_id: int, req: ManagedAgentUpdateReq) -> ManagedAgent:
    current = await get_agent(user, agent_id)
    await _assert_manager_or_admin(user, agent_id)

    source_kind, protocol, native_app_id, native_app_type, endpoint, endpoint_hash = await _validate_source(
        req, current
    )
    health_method = req.health_method or current.health_method
    health_url = req.health_url if req.health_url is not None else current.health_url
    _validate_health(health_method, health_url)
    if health_method == HealthMethod.HEALTH_URL.value and health_url:
        await _validate_endpoint(health_url)
    if req.agent_card is not None:
        parse_agent_card(req.agent_card)
    if req.scenario_code is not None:
        await _assert_taxonomy("scenario", req.scenario_code)
    if req.industry_code is not None:
        await _assert_taxonomy("industry", req.industry_code)

    if req.name and req.name != current.name:
        existing = await ManagedAgentDao.aget_by_name(req.name)
        if existing is not None and existing.id != current.id:
            raise ManagedAgentDuplicateError(msg="name already exists")
    if endpoint_hash != current.endpoint_hash:
        existing = await ManagedAgentDao.aget_by_endpoint_hash(endpoint_hash)
        if existing is not None and existing.id != current.id:
            raise ManagedAgentDuplicateError(msg="endpoint already exists")

    fields = {
        "name": req.name or current.name,
        "description": req.description if req.description is not None else current.description,
        "source_kind": source_kind,
        "protocol": protocol,
        "native_app_id": native_app_id,
        "native_app_type": native_app_type,
        "endpoint": endpoint,
        "endpoint_hash": endpoint_hash,
        "model_name": req.model_name if req.model_name is not None else current.model_name,
        "agent_card": req.agent_card if req.agent_card is not None else current.agent_card,
        "health_url": health_url,
        "health_method": health_method,
        "scenario_code": req.scenario_code if req.scenario_code is not None else current.scenario_code,
        "industry_code": req.industry_code if req.industry_code is not None else current.industry_code,
        "version": req.version if req.version is not None else current.version,
    }

    if req.credential is not None:
        if req.credential.auth_type == CredentialType.NONE.value:
            fields.update(
                credential_ciphertext=None,
                credential_type=CredentialType.NONE.value,
                has_credential=False,
            )
        elif req.credential.secret:
            ciphertext, credential_type, has_credential = encrypt_credential(
                req.credential.auth_type,
                req.credential.secret,
                req.credential.headers,
            )
            fields.update(
                credential_ciphertext=ciphertext,
                credential_type=credential_type,
                has_credential=has_credential,
            )
        elif req.credential.headers is not None and current.credential_ciphertext:
            # Empty secret means "keep it"; headers may still be replaced.
            existing = decrypt_credential(current.credential_ciphertext)
            ciphertext, credential_type, has_credential = encrypt_credential(
                existing.auth_type,
                existing.secret,
                {**existing.headers, **req.credential.headers},
            )
            fields.update(
                credential_ciphertext=ciphertext,
                credential_type=credential_type,
                has_credential=has_credential,
            )

    rowcount = await ManagedAgentDao.aupdate_fields(current.id, fields, tenant_id=current.tenant_id)
    if rowcount != 1:
        raise ManagedAgentUnavailableError(msg="managed agent changed concurrently")
    if req.tags is not None:
        await TagDao.aupdate_resource_tags(req.tags, str(current.id), ResourceTypeEnum.MANAGED_AGENT, user.user_id)
    updated = await ManagedAgentDao.aget(current.id)
    if updated is None:
        raise ManagedAgentNotFoundError()
    await _audit(user, ManagedAgentAuditAction.UPDATE, updated)
    return updated


async def transition(user, agent_id: int, action: str) -> ManagedAgent:
    current = await get_agent(user, agent_id)
    await _assert_manager_or_admin(user, agent_id)
    if action == "publish":
        allowed_from = (AgentStatus.DRAFT.value, AgentStatus.OFFLINE.value)
        target = AgentStatus.ONLINE.value
        audit = ManagedAgentAuditAction.ENABLE
    elif action == "offline":
        allowed_from = (AgentStatus.ONLINE.value,)
        target = AgentStatus.OFFLINE.value
        audit = ManagedAgentAuditAction.DISABLE
    elif action == "disable":
        allowed_from = (
            AgentStatus.DRAFT.value,
            AgentStatus.ONLINE.value,
            AgentStatus.OFFLINE.value,
        )
        target = AgentStatus.DISABLED.value
        audit = ManagedAgentAuditAction.DISABLE
    else:
        raise ManagedAgentUnavailableError(msg=f"unknown transition: {action}")

    moved = 0
    for source in allowed_from:
        moved = await ManagedAgentDao.atransition(
            current.id,
            tenant_id=current.tenant_id,
            from_status=source,
            to_status=target,
        )
        if moved:
            break
    if not moved:
        raise ManagedAgentUnavailableError(msg=f"cannot {action} from {current.status}")
    updated = await ManagedAgentDao.aget(current.id)
    if updated is None:
        raise ManagedAgentNotFoundError()
    # `action` is already the positional audit action; record the transition
    # name under a different metadata key to avoid a duplicate-argument error.
    await _audit(user, audit, updated, transition=action)
    return updated


async def delete_agent(user, agent_id: int) -> None:
    current = await get_agent(user, agent_id)
    if not await _is_tenant_admin(user):
        await _assert_perm(user, "owner", agent_id)

    from bisheng.scheduled_task.domain.models.dao import ScheduledTaskDao

    enabled_tasks = await ScheduledTaskDao.acount_enabled_by_app(str(current.id), 100)
    if enabled_tasks:
        # `count` lands in the response `data` so the UI can render the number
        # instead of the generic localized sentence (report §6 #6).
        raise ManagedAgentInUseError(
            count=enabled_tasks,
            msg=f"managed agent has {enabled_tasks} enabled scheduled task(s)",
        )
    if await ManagedAgentDao.adelete(current.id, tenant_id=current.tenant_id) != 1:
        raise ManagedAgentNotFoundError()
    # Drop tag links after the row is gone; run traces keep their snapshots.
    # Orphaned links are harmless, so a cleanup failure must not fail the delete.
    try:
        await TagDao.aupdate_resource_tags([], str(current.id), ResourceTypeEnum.MANAGED_AGENT, user.user_id)
    except Exception:
        logger.exception("managed agent tag link cleanup failed for {}", current.id)
    await _audit(user, ManagedAgentAuditAction.DELETE, current)


async def list_agents(
    user,
    *,
    keyword: str | None = None,
    source_kind: str | None = None,
    protocol: str | None = None,
    status: str | None = None,
    scenario_code: str | None = None,
    industry_code: str | None = None,
    tag_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ManagedAgent], int]:
    if tag_id is not None:
        # TagLink.resource_id is a string; resolve the tagged set first, then
        # filter/slice in memory (agent counts per tenant are small).
        links = await TagDao.aget_resources_by_tags([tag_id], ResourceTypeEnum.MANAGED_AGENT)
        tagged = {int(link.resource_id) for link in links if str(link.resource_id).isdigit()}
        rows, _ = await ManagedAgentDao.alist(
            keyword=keyword,
            source_kind=source_kind,
            protocol=protocol,
            status=status,
            scenario_code=scenario_code,
            industry_code=industry_code,
            page=1,
            page_size=10_000,
        )
        rows = [row for row in rows if row.id in tagged]
        total = len(rows)
        start = max(page - 1, 0) * page_size
        rows = rows[start : start + page_size]
    else:
        rows, total = await ManagedAgentDao.alist(
            keyword=keyword,
            source_kind=source_kind,
            protocol=protocol,
            status=status,
            scenario_code=scenario_code,
            industry_code=industry_code,
            page=page,
            page_size=page_size,
        )
    if await _is_tenant_admin(user):
        return rows, total
    visible = await asyncio.gather(
        *[
            PermissionService.check(user.user_id, "viewer", "managed_agent", str(row.id), login_user=user)
            for row in rows
        ]
    )
    filtered = [row for row, ok in zip(rows, visible, strict=True) if ok]
    return filtered, len(filtered)


async def list_catalog(
    user,
    *,
    keyword: str | None = None,
    scenario_code: str | None = None,
    industry_code: str | None = None,
    tag_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ManagedAgent], int]:
    if tag_id is not None:
        links = await TagDao.aget_resources_by_tags([tag_id], ResourceTypeEnum.MANAGED_AGENT)
        tagged = {int(link.resource_id) for link in links if str(link.resource_id).isdigit()}
        rows, _ = await ManagedAgentDao.alist_online_for_catalog(
            keyword=keyword,
            scenario_code=scenario_code,
            industry_code=industry_code,
            page=1,
            page_size=10_000,
        )
        rows = [row for row in rows if row.id in tagged]
        start = max(page - 1, 0) * page_size
        rows = rows[start : start + page_size]
    else:
        rows, _ = await ManagedAgentDao.alist_online_for_catalog(
            keyword=keyword,
            scenario_code=scenario_code,
            industry_code=industry_code,
            page=page,
            page_size=page_size,
        )
    visible = await asyncio.gather(
        *[
            PermissionService.check(user.user_id, "viewer", "managed_agent", str(row.id), login_user=user)
            for row in rows
        ]
    )
    filtered = [row for row, ok in zip(rows, visible, strict=True) if ok]
    return filtered, len(filtered)


async def tag_meta_map(agent_ids: list[int]) -> dict[int, tuple[list[int], list[str]]]:
    """Batch tag ids + names for list/detail rendering (resource type = managed_agent).

    Ids are needed by the edit form: it echoes `tags` back on save, so an empty
    list would silently clear the agent's tags.
    """
    if not agent_ids:
        return {}
    links = await TagDao.aget_resource_tag_ids_batch(
        [str(agent_id) for agent_id in agent_ids], ResourceTypeEnum.MANAGED_AGENT
    )
    all_tag_ids = sorted({tag_id for ids in links.values() for tag_id in ids})
    tags = await TagDao.aget_tags_by_ids(all_tag_ids)
    name_by_id = {tag.id: tag.name for tag in tags}
    result: dict[int, tuple[list[int], list[str]]] = {}
    for resource_id, ids in links.items():
        if not resource_id.isdigit():
            continue
        valid_ids = [tag_id for tag_id in ids if tag_id in name_by_id]
        result[int(resource_id)] = (valid_ids, [name_by_id[tag_id] for tag_id in valid_ids])
    return result


async def tag_names_map(agent_ids: list[int]) -> dict[int, list[str]]:
    """Names-only view over :func:`tag_meta_map` (kept for callers/tests)."""
    return {agent_id: names for agent_id, (_, names) in (await tag_meta_map(agent_ids)).items()}


def to_item(
    agent: ManagedAgent,
    *,
    scenario_name: str | None = None,
    industry_name: str | None = None,
    tag_ids: list[int] | None = None,
    tag_names: list[str] | None = None,
) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "source_kind": agent.source_kind,
        "protocol": agent.protocol,
        "status": agent.status,
        "version": agent.version,
        "native_app_id": agent.native_app_id,
        "native_app_type": agent.native_app_type,
        "endpoint": agent.endpoint,
        "model_name": agent.model_name,
        "scenario_code": agent.scenario_code,
        "industry_code": agent.industry_code,
        "scenario_name": scenario_name,
        "industry_name": industry_name,
        "tags": tag_ids or [],
        "tag_names": tag_names or [],
        "health_status": agent.health_status,
        "health_method": agent.health_method,
        "last_health_check_at": agent.last_health_check_at,
        "last_call_at": agent.last_call_at,
        "last_run_status": agent.last_run_status,
        "last_latency_ms": agent.last_latency_ms,
        "credential_type": agent.credential_type,
        "has_credential": agent.has_credential,
        "credential_mask": mask_credential(agent.credential_type, agent.has_credential),
        "create_time": agent.create_time,
        "update_time": agent.update_time,
    }
