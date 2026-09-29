"""/invitations: invitation codes for self-registration (user:manage).

An administrator creates a code with the roles it grants, a number of uses
and a validity period, and passes it on; the user registers with it at
/auth/register.  The plain code is returned once at creation; only its hash
is stored.  Codes cannot grant the admin role or user management: accounts
with those rights are created directly by an administrator.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db.base import utc_now
from ..db.models import Invitation, Role, RolePermission, User
from ..deps import Principal, api_error, get_session, require
from ..security import new_invitation_code, normalize_invitation_code, token_hash

router = APIRouter(tags=["用户与权限"])
manage = require("user:manage")
FORBIDDEN_PERMISSIONS = {"user:manage"}


class InvitationCreate(BaseModel):
    roles: list[str] = Field(default_factory=lambda: ["viewer"], min_length=1)
    max_uses: int = Field(1, ge=1, le=50)
    days: int = Field(7, ge=1, le=30)
    note: str | None = Field(None, max_length=200)


def invitation_state(invitation: Invitation, now: datetime) -> str:
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.used_count >= invitation.max_uses:
        return "used_up"
    if invitation.expires_at <= now:
        return "expired"
    return "active"


def invitation_view(session: Session, invitation: Invitation, now: datetime) -> dict:
    users = list(session.scalars(select(User.username).where(User.invitation_id == invitation.id)
                                 .order_by(User.id)))
    return {"id": invitation.id, "hint": invitation.hint, "roles": invitation.roles, "note": invitation.note,
            "max_uses": invitation.max_uses, "used_count": invitation.used_count,
            "expires_at": invitation.expires_at.isoformat(), "created_by": invitation.created_by,
            "created_at": invitation.created_at.isoformat(),
            "revoked_at": invitation.revoked_at.isoformat() if invitation.revoked_at else None,
            "state": invitation_state(invitation, now), "users": users}


@router.get("/invitations")
def list_invitations(_: Principal = Depends(manage), session: Session = Depends(get_session)) -> list[dict]:
    now = utc_now()
    return [invitation_view(session, i, now)
            for i in session.scalars(select(Invitation).order_by(Invitation.id.desc()).limit(200))]


@router.post("/invitations", status_code=201)
def create_invitation(body: InvitationCreate, principal: Principal = Depends(manage),
                      session: Session = Depends(get_session)) -> dict:
    roles = sorted(set(body.roles))
    known = set(session.scalars(select(Role.code).where(Role.code.in_(roles))))
    if unknown := sorted(set(roles) - known):
        raise api_error(400, "unknown_role", f"未知角色：{', '.join(unknown)}")
    privileged = set(session.scalars(select(RolePermission.role_code).where(
        RolePermission.role_code.in_(roles), RolePermission.permission_code.in_(FORBIDDEN_PERMISSIONS))))
    if "admin" in roles or privileged:
        raise api_error(400, "privileged_role", "邀请码不能授予管理员或用户管理权限；这类账号请在用户列表中直接创建")
    now = utc_now()
    code = new_invitation_code()
    invitation = Invitation(code_hash=token_hash(normalize_invitation_code(code)), hint=code[-4:], roles=roles,
                            note=body.note, max_uses=body.max_uses, used_count=0,
                            expires_at=now + timedelta(days=body.days), created_by=principal.actor, created_at=now)
    session.add(invitation)
    session.flush()
    audit(session, principal.actor, "invitation.create", "invitation", str(invitation.id),
          after={"roles": roles, "max_uses": body.max_uses, "days": body.days, "hint": invitation.hint},
          **principal.audit_kwargs())
    session.commit()
    return {**invitation_view(session, invitation, now), "code": code}  # shown once


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: int, principal: Principal = Depends(manage),
                      session: Session = Depends(get_session)) -> dict:
    invitation = session.get(Invitation, invitation_id)
    if invitation is None:
        raise api_error(404, "not_found", "邀请码不存在")
    now = utc_now()
    if invitation.revoked_at is None:
        invitation.revoked_at = now
        audit(session, principal.actor, "invitation.revoke", "invitation", str(invitation.id),
              **principal.audit_kwargs())
        session.commit()
    return invitation_view(session, invitation, now)
