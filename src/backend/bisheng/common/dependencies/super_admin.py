"""Super-admin-only dependency for platform-level resources (F059).

Why this exists instead of reusing an existing dependency
---------------------------------------------------------
`UserPayload.get_tenant_admin_user` is the obvious candidate and it is WRONG
here. Its contract is "global super admin **OR** the current tenant's Child
Admin" — it deliberately admits tenant admins so they can manage their own
tenant. Attaching it to the platform word library would let any tenant
administrator read and edit the library that is enforced across EVERY tenant,
which is precisely the boundary F059 exists to hold (release-contract INV-10).

So platform-level endpoints depend on this module instead: it admits a global
super admin and nobody else.
"""

from __future__ import annotations

from fastapi import Depends

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.http_error import UnAuthorizedError
from bisheng.utils.http_middleware import _check_is_global_super


async def require_global_super_admin(
    login_user: UserPayload = Depends(UserPayload.get_login_user),
) -> UserPayload:
    """Admit only a global super admin; reject everyone else with 403.

    The check is `user:{id} super_admin system:global` (with a legacy AdminRole
    fallback), Redis-cached — the same predicate the tenant middleware uses to
    decide whether to widen the tenant filter, so "who is a super admin" has one
    definition in the codebase rather than two that can drift apart.

    Fails CLOSED: if the check itself errors we reject rather than admit. The
    cached-FGA path already degrades to the DB fallback internally; reaching a
    rejection here means both the FGA check and the role fallback were
    unavailable, and for a cross-tenant write surface that is a denial, not a
    pass.
    """
    user_id = getattr(login_user, 'user_id', None)
    if not user_id:
        # Rejected in the same shape as the other two paths: an anonymous or
        # malformed identity is a 403, not an unhandled exception. `login_user`
        # is only reached through an auth dependency, so this guards against a
        # misconfigured dependency rather than a normal request.
        raise UnAuthorizedError.http_exception(
            msg='Only the platform super admin may manage the platform-wide word library'
        )

    try:
        is_super = await _check_is_global_super(int(user_id))
    except Exception:
        is_super = False

    if not is_super:
        # Same message for "not an admin" and "could not verify" on purpose: the
        # difference is operationally useful to an attacker probing for an FGA
        # outage, and the operator can tell the two apart from the server logs.
        raise UnAuthorizedError.http_exception(
            msg='Only the platform super admin may manage the platform-wide word library'
        )

    return login_user
