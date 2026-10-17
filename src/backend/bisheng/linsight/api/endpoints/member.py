"""Saved collaborator templates (F087) — per-user library for the picker.

Plain login auth (the library is personal); all rows are scoped by the
requesting user in the service/DAO, so one user can never read or mutate
another user's templates even inside the same tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from bisheng.common.dependencies.user_deps import UserPayload
from bisheng.common.errcode.http_error import NotFoundError
from bisheng.common.schemas.api import UnifiedResponseModel, resp_200
from bisheng.linsight.domain.schemas.member_schema import MemberDefaultRequest, MemberSaveRequest
from bisheng.linsight.domain.services.member_service import MemberService

router = APIRouter(prefix="/member", tags=["LinsightMember"])


@router.get("", summary="List the current user's saved collaborator templates")
async def list_members(
    login_user: UserPayload = Depends(UserPayload.get_login_user),
) -> UnifiedResponseModel:
    return resp_200(await MemberService().list_members(login_user))


@router.post("", summary="Save (upsert by name) a collaborator template")
async def save_member(
    payload: MemberSaveRequest,
    login_user: UserPayload = Depends(UserPayload.get_login_user),
) -> UnifiedResponseModel:
    result = await MemberService().save_member(login_user, payload)
    if result is None:
        return NotFoundError.return_resp()
    return resp_200(result)


@router.post("/{member_id}/default", summary="Toggle the auto-carry flag of a saved member")
async def set_member_default(
    payload: MemberDefaultRequest,
    member_id: int = Path(..., ge=1),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
) -> UnifiedResponseModel:
    updated = await MemberService().set_default(login_user, member_id, payload.is_default)
    if not updated:
        return NotFoundError.return_resp()
    return resp_200({"updated": True, "is_default": payload.is_default})


@router.delete("/{member_id}", summary="Delete one saved collaborator template")
async def delete_member(
    member_id: int = Path(..., ge=1),
    login_user: UserPayload = Depends(UserPayload.get_login_user),
) -> UnifiedResponseModel:
    return resp_200({"deleted": await MemberService().delete_member(login_user, member_id)})
