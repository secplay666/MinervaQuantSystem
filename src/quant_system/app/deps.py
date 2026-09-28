"""FastAPI dependencies: database session, the authenticated principal and
permission checks.  Errors are ``{"detail": {"code": ..., "message": ...}}``."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db.base import utc_now
from .db.models import User
from .rbac import user_permissions, user_roles
from .security import TokenError, decode_access_token
from .settings import AppSettings


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def settings_of(request: Request) -> AppSettings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and host in settings_of(request).trusted_proxies:
        return forwarded.split(",")[0].strip()
    return host


@dataclass
class Principal:
    user: User
    roles: list[str]
    permissions: set[str]
    ip: str | None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def actor(self) -> str:
        return self.user.username

    def audit_kwargs(self) -> dict:
        return {"user_id": self.user.id, "ip": self.ip, "request_id": self.request_id}


def _principal(request: Request, session: Session) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise api_error(401, "not_authenticated", "需要登录")
    try:
        claims = decode_access_token(settings_of(request).secret_key, token)
    except TokenError:
        raise api_error(401, "invalid_token", "登录已过期或无效，请重新登录") from None
    user = session.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise api_error(401, "inactive_user", "账号不存在或已停用")
    if user.locked_until is not None and user.locked_until > utc_now():
        raise api_error(401, "locked", "账号暂时锁定")
    return Principal(user, user_roles(session, user.id), user_permissions(session, user.id), client_ip(request),
                     request.headers.get("x-request-id") or uuid.uuid4().hex)


def authenticated(request: Request, session: Session = Depends(get_session)) -> Principal:
    """Any logged-in user, even one that still has to change the password."""
    return _principal(request, session)


def require(*permissions: str):
    """Dependency: a logged-in user with all ``permissions`` and no pending password change."""

    def dependency(request: Request, session: Session = Depends(get_session)) -> Principal:
        principal = _principal(request, session)
        if principal.user.must_change_password:
            raise api_error(403, "password_change_required", "请先修改初始密码")
        missing = [p for p in permissions if p not in principal.permissions]
        if missing:
            raise api_error(403, "forbidden", f"缺少权限：{', '.join(missing)}")
        return principal

    return dependency
