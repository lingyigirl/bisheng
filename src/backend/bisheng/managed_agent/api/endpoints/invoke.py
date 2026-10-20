"""F078 unified invocation endpoint `POST /api/v2/managed-agent/invoke`.

Always key-authenticated: even when the deployment-wide `enforce_keys` switch is
off, this endpoint refuses an anonymous caller because it is a new face with no
legacy behaviour to preserve (same stance as F077's trigger endpoint).

The body is a strict whitelist: `agent_id`, `input`, optional `session_id`.
Everything else is 12114, including a caller-supplied identity (which F076's
dependency catches even earlier as 11817).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

from bisheng.common.errcode.managed_agent import ManagedAgentInvokePayloadInvalidError
from bisheng.common.errcode.open_api import OpenApiKeyMissingError
from bisheng.common.schemas.api import resp_200
from bisheng.managed_agent.domain.services import invoke_service
from bisheng.open_api.domain.context import get_open_api_principal
from bisheng.open_api.domain.scopes import open_api_scope

router = APIRouter(prefix="/managed-agent", tags=["OpenApi"])

_ALLOWED_BODY_FIELDS = frozenset({"agent_id", "input", "session_id"})


@router.post("/invoke")
@open_api_scope("managed_agent:invoke")
async def invoke_managed_agent(request: Request, payload: dict = Body(...)):
    principal = get_open_api_principal()
    if principal is None:
        # The deployment switch may be off; this face still requires a key.
        raise OpenApiKeyMissingError()

    extra = set(payload) - _ALLOWED_BODY_FIELDS if isinstance(payload, dict) else set()
    if extra:
        raise ManagedAgentInvokePayloadInvalidError(exception=f"unexpected fields: {sorted(extra)}")

    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, int) or isinstance(agent_id, bool):
        raise ManagedAgentInvokePayloadInvalidError(msg="agent_id must be an integer")
    if "input" not in payload:
        raise ManagedAgentInvokePayloadInvalidError(msg="input is required")
    session_id = payload.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ManagedAgentInvokePayloadInvalidError(msg="session_id must be a string")

    result = await invoke_service.invoke(
        principal.owner_payload,
        agent_id,
        payload["input"],
        session_id=session_id,
        caller_type="api_key",
        caller_id=str(principal.service_account_id),
        service_account_id=principal.service_account_id,
    )
    return resp_200(result)
