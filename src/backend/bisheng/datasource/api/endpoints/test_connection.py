"""Test-connection endpoint (spec AC-01 / AC-03).

Returns `200` with `{ok: false, category, message, detail}` rather than an error
response when the database cannot be reached: a failed test-connection is the
expected outcome of a mistyped password or a wrong port, not an API failure,
and the frontend branches on `ok` to show the reason next to the form. Error
code 11701 stays reserved for a connection that fails at query time.

Admin-gated, and that gating is load-bearing rather than cosmetic: `detail`
carries the target database's own error text, which is what makes a failure
diagnosable for the person who typed the connection details. `ConnectionTester`
scrubs the password out of it, but nothing else about that text is promised --
so it does not belong on a path every user can reach.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.schemas.api import resp_200
from bisheng.datasource.domain.services.connection_test import (
    ConnectionTester,
    ConnectionTestPayload,
    result_dict,
)

router = APIRouter(prefix="/datasource", tags=["datasource"])


@router.post("/test-connection")
async def test_connection(
    payload: ConnectionTestPayload,
    login_user: UserPayload = Depends(UserPayload.get_tenant_admin_user),
):
    """Probe the given connection, then close it. Persists nothing."""
    result = await ConnectionTester.test(payload)
    return resp_200(result_dict(result))
