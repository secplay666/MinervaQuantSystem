"""/users, /roles, /sessions, /audit: administration (user:manage, audit:view).

Built-in roles follow the matrix in ``rbac.py``; custom roles can be created
and their permissions switched on and off.  An administrator cannot disable
their own account or remove their own admin role, and the last active admin
cannot be removed, so nobody locks the system out by accident.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..db.base import utc_now
from ..db.models import AuditLog, Permission, RefreshToken, Role, RolePermission, User, UserRole
from ..deps import Principal, api_error, get_session, require
from ..rbac import PERMISSIONS, ROLES
from ..security import hash_password, password_problems, temporary_password
from .auth import user_view

router = APIRouter(tags=["用户与权限"])
manage = require("user:manage")


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$")
    display_name: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
    password: str | None = None


class UserPatch(BaseModel):
    display_name: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


class RoleCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class RolePermissions(BaseModel):
    permissions: list[str]


def _check_roles(session: Session, roles: list[str]) -> None:
    known = set(session.scalars(select(Role.code)))
    unknown = sorted(set(roles) - known)
    if unknown:
        raise api_error(400, "unknown_role", f"未知角色：{', '.join(unknown)}")


def _active_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).join(UserRole, UserRole.user_id == User.id)
                          .where(UserRole.role_code == "admin", User.is_active.is_(True))) or 0


@router.get("/users")
def list_users(_: Principal = Depends(manage), session: Session = Depends(get_session)) -> list[dict]:
    return [user_view(session, user) for user in session.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, principal: Principal = Depends(manage),
                session: Session = Depends(get_session)) -> dict:
    if session.scalar(select(User).where(User.username == body.username)):
        raise api_error(409, "exists", "用户名已存在")
    _check_roles(session, body.roles)
    password = body.password or temporary_password()
    if body.password:
        problems = password_problems(body.password, body.username, 10)
        if problems:
            raise api_error(400, "weak_password", "密码不符合要求：" + "；".join(problems))
    user = User(username=body.username, display_name=body.display_name, password_hash=hash_password(password),
                must_change_password=True)
    session.add(user)
    session.flush()
    session.add_all(UserRole(user_id=user.id, role_code=code) for code in sorted(set(body.roles)))
    audit(session, principal.actor, "user.create", "user", str(user.id),
          after={"username": user.username, "roles": sorted(set(body.roles))}, **principal.audit_kwargs())
    session.commit()
    view = user_view(session, user)
    if not body.password:
        view["temporary_password"] = password  # shown once; the user must change it at first login
    return view


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, principal: Principal = Depends(manage),
               session: Session = Depends(get_session)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise api_error(404, "not_found", "用户不存在")
    before = user_view(session, user)
    self_edit = user.id == principal.user.id
    if body.is_active is False and self_edit:
        raise api_error(400, "self_lockout", "不能停用自己的账号")
    if body.roles is not None:
        _check_roles(session, body.roles)
        if self_edit and "admin" in before["roles"] and "admin" not in body.roles:
            raise api_error(400, "self_lockout", "不能移除自己的管理员角色")
        if "admin" in before["roles"] and "admin" not in body.roles and _active_admins(session) <= 1:
            raise api_error(400, "last_admin", "至少保留一名启用的管理员")
        session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        session.add_all(UserRole(user_id=user_id, role_code=code) for code in sorted(set(body.roles)))
    if body.is_active is not None:
        if not body.is_active and "admin" in before["roles"] and _active_admins(session) <= 1:
            raise api_error(400, "last_admin", "至少保留一名启用的管理员")
        user.is_active = body.is_active
        if not body.is_active:
            session.execute(update(RefreshToken).where(RefreshToken.user_id == user_id,
                                                       RefreshToken.revoked_at.is_(None)).values(revoked_at=utc_now()))
    if body.display_name is not None:
        user.display_name = body.display_name
    session.flush()
    after = user_view(session, user)
    audit(session, principal.actor, "user.update", "user", str(user_id), before=before, after=after,
          **principal.audit_kwargs())
    session.commit()
    return after


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, principal: Principal = Depends(manage),
                   session: Session = Depends(get_session)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise api_error(404, "not_found", "用户不存在")
    password = temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.failed_logins, user.locked_until = 0, None
    had_totp, user.totp_secret = bool(user.totp_secret), None  # a lost authenticator is the usual reason
    session.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=utc_now()))
    audit(session, principal.actor, "user.reset_password", "user", str(user_id), after={"totp_cleared": had_totp},
          **principal.audit_kwargs())
    session.commit()
    return {"temporary_password": password}


@router.post("/users/{user_id}/unlock")
def unlock(user_id: int, principal: Principal = Depends(manage), session: Session = Depends(get_session)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise api_error(404, "not_found", "用户不存在")
    user.failed_logins, user.locked_until = 0, None
    audit(session, principal.actor, "user.unlock", "user", str(user_id), **principal.audit_kwargs())
    session.commit()
    return {"ok": True}


@router.get("/permissions")
def list_permissions(_: Principal = Depends(manage), session: Session = Depends(get_session)) -> list[dict]:
    return [{"code": p.code, "name": p.name, "group": p.group_name}
            for p in session.scalars(select(Permission).order_by(Permission.group_name, Permission.code))]


@router.get("/roles")
def list_roles(_: Principal = Depends(manage), session: Session = Depends(get_session)) -> list[dict]:
    out = []
    for role in session.scalars(select(Role).order_by(Role.code)):
        granted = sorted(session.scalars(select(RolePermission.permission_code)
                                         .where(RolePermission.role_code == role.code)))
        out.append({"code": role.code, "name": role.name, "description": role.description, "permissions": granted,
                    "builtin": role.code in ROLES})
    return out


@router.post("/roles", status_code=201)
def create_role(body: RoleCreate, principal: Principal = Depends(manage),
                session: Session = Depends(get_session)) -> dict:
    if session.get(Role, body.code) is not None:
        raise api_error(409, "exists", "角色已存在")
    unknown = sorted(set(body.permissions) - set(PERMISSIONS))
    if unknown:
        raise api_error(400, "unknown_permission", f"未知权限：{', '.join(unknown)}")
    session.add(Role(code=body.code, name=body.name, description=body.description))
    session.flush()
    session.add_all(RolePermission(role_code=body.code, permission_code=p) for p in sorted(set(body.permissions)))
    audit(session, principal.actor, "role.create", "role", body.code, after=body.model_dump(),
          **principal.audit_kwargs())
    session.commit()
    return {"code": body.code}


@router.put("/roles/{code}/permissions")
def set_role_permissions(code: str, body: RolePermissions, principal: Principal = Depends(manage),
                         session: Session = Depends(get_session)) -> dict:
    if session.get(Role, code) is None:
        raise api_error(404, "not_found", "角色不存在")
    if code in ROLES:
        raise api_error(400, "builtin_role", "内置角色的权限由系统定义；请新建自定义角色")
    unknown = sorted(set(body.permissions) - set(PERMISSIONS))
    if unknown:
        raise api_error(400, "unknown_permission", f"未知权限：{', '.join(unknown)}")
    before = sorted(session.scalars(select(RolePermission.permission_code).where(RolePermission.role_code == code)))
    session.execute(delete(RolePermission).where(RolePermission.role_code == code))
    session.add_all(RolePermission(role_code=code, permission_code=p) for p in sorted(set(body.permissions)))
    audit(session, principal.actor, "role.permissions", "role", code, before=before,
          after=sorted(set(body.permissions)), **principal.audit_kwargs())
    session.commit()
    return {"code": code, "permissions": sorted(set(body.permissions))}


@router.get("/sessions")
def list_sessions(user_id: int | None = None, _: Principal = Depends(manage),
                  session: Session = Depends(get_session)) -> list[dict]:
    now = utc_now()
    query = select(RefreshToken, User.username).join(User, User.id == RefreshToken.user_id).where(
        RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now)
    if user_id is not None:
        query = query.where(RefreshToken.user_id == user_id)
    return [{"id": token.id, "user_id": token.user_id, "username": name, "issued_at": token.issued_at.isoformat(),
             "expires_at": token.expires_at.isoformat(), "ip": token.ip, "user_agent": token.user_agent}
            for token, name in session.execute(query.order_by(RefreshToken.issued_at.desc()))]


@router.delete("/sessions/{token_id}")
def revoke_session(token_id: int, principal: Principal = Depends(manage),
                   session: Session = Depends(get_session)) -> dict:
    token = session.get(RefreshToken, token_id)
    if token is None:
        raise api_error(404, "not_found", "会话不存在")
    token.revoked_at = utc_now()
    audit(session, principal.actor, "session.revoke", "user", str(token.user_id), **principal.audit_kwargs())
    session.commit()
    return {"ok": True}


@router.get("/audit")
def list_audit(actor: str | None = None, action: str | None = None, subject_id: str | None = None,
               before_id: int | None = None, limit: int = 100, _: Principal = Depends(require("audit:view")),
               session: Session = Depends(get_session)) -> list[dict]:
    query = select(AuditLog)
    if actor:
        query = query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))
    if subject_id:
        query = query.where(AuditLog.subject_id == subject_id)
    if before_id:
        query = query.where(AuditLog.id < before_id)
    rows = session.scalars(query.order_by(AuditLog.id.desc()).limit(min(limit, 500)))
    return [{"id": r.id, "at": r.at.isoformat(), "actor": r.actor, "action": r.action,
             "subject_type": r.subject_type, "subject_id": r.subject_id, "before": r.before, "after": r.after,
             "reason": r.reason, "ip": r.ip} for r in rows]

