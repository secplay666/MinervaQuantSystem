"""/auth: login, token refresh (rotating), logout, password change, TOTP."""

from __future__ import annotations

import time
from collections import deque
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..db.base import utc_now
from ..db.models import RefreshToken, User
from ..deps import Principal, api_error, authenticated, client_ip, get_session, settings_of
from ..rbac import user_permissions, user_roles
from ..security import (
    create_access_token,
    hash_password,
    needs_rehash,
    new_refresh_token,
    new_totp_secret,
    password_problems,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["认证"])
_DUMMY_HASH = hash_password("timing-equaliser-not-a-password")
IP_WINDOW_SECONDS = 15 * 60
IP_MAX_FAILURES = 20  # per client IP and window, across all usernames (password spraying)


def _ip_failures(request: Request, ip: str | None) -> deque:
    table: dict = request.app.state.__dict__.setdefault("login_failures", {})
    failures = table.setdefault(ip or "?", deque())
    cutoff = time.monotonic() - IP_WINDOW_SECONDS
    while failures and failures[0] < cutoff:
        failures.popleft()
    return failures


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp: str | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(max_length=256)


class TotpEnableIn(BaseModel):
    secret: str
    code: str


class TotpDisableIn(BaseModel):
    password: str


def user_view(session: Session, user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name,
            "roles": user_roles(session, user.id), "permissions": sorted(user_permissions(session, user.id)),
            "must_change_password": user.must_change_password, "totp_enabled": bool(user.totp_secret),
            "is_active": user.is_active,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None}


def issue_tokens(request: Request, session: Session, user: User) -> dict:
    settings = settings_of(request)
    now = utc_now()
    access, expires = create_access_token(settings.secret_key, user.id, user.username, now, settings.access_minutes)
    refresh, digest = new_refresh_token()
    session.add(RefreshToken(user_id=user.id, token_hash=digest, issued_at=now,
                             expires_at=now + timedelta(days=settings.refresh_days), ip=client_ip(request),
                             user_agent=(request.headers.get("user-agent") or "")[:255]))
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "expires_in": settings.access_minutes * 60, "access_expires_at": expires.isoformat(),
            "user": user_view(session, user)}


@router.post("/login")
def login(body: LoginIn, request: Request, session: Session = Depends(get_session)) -> dict:
    settings = settings_of(request)
    now = utc_now()
    ip = client_ip(request)
    failures = _ip_failures(request, ip)
    if len(failures) >= IP_MAX_FAILURES:
        raise api_error(429, "too_many_attempts", "登录失败次数过多，请 15 分钟后再试")
    user = session.scalar(select(User).where(User.username == body.username))
    if user is None:
        verify_password(_DUMMY_HASH, body.password)  # same cost as a real check
        failures.append(time.monotonic())
        raise api_error(401, "invalid_credentials", "用户名或密码错误")
    if user.locked_until is not None and user.locked_until > now:
        minutes = int((user.locked_until - now).total_seconds() // 60) + 1
        raise api_error(423, "locked", f"登录失败次数过多，请 {minutes} 分钟后再试")
    ok = user.is_active and verify_password(user.password_hash, body.password)
    if ok and user.totp_secret:
        if not body.totp:
            raise api_error(401, "totp_required", "请输入两步验证码")
        ok = verify_totp(user.totp_secret, body.totp)
    if not ok:
        failures.append(time.monotonic())
        user.failed_logins += 1
        locked = user.failed_logins >= settings.lockout_threshold
        if locked:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_logins = 0
        audit(session, body.username, "auth.login_failed", "user", str(user.id), ip=ip,
              after={"locked": locked})
        session.commit()
        raise api_error(401, "invalid_credentials", "用户名或密码错误")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    tokens = issue_tokens(request, session, user)
    audit(session, user.username, "auth.login", "user", str(user.id), user_id=user.id, ip=ip)
    session.commit()
    return tokens


@router.post("/refresh")
def refresh(body: RefreshIn, request: Request, session: Session = Depends(get_session)) -> dict:
    now = utc_now()
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(body.refresh_token)))
    if row is None:
        raise api_error(401, "invalid_token", "请重新登录")
    user = session.get(User, row.user_id)
    if row.revoked_at is not None:
        # A rotated-away token came back: assume it was stolen and end every session.
        session.execute(update(RefreshToken).where(RefreshToken.user_id == row.user_id,
                                                   RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        audit(session, user.username if user else "?", "auth.refresh_reuse", "user", str(row.user_id),
              ip=client_ip(request))
        session.commit()
        raise api_error(401, "token_reused", "登录凭证已失效，请重新登录")
    if row.expires_at < now or user is None or not user.is_active:
        raise api_error(401, "invalid_token", "请重新登录")
    row.revoked_at = now
    tokens = issue_tokens(request, session, user)
    session.commit()
    return tokens


@router.post("/logout")
def logout(body: RefreshIn, session: Session = Depends(get_session)) -> dict:
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(body.refresh_token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utc_now()
        session.commit()
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(authenticated), session: Session = Depends(get_session)) -> dict:
    return user_view(session, principal.user)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, principal: Principal = Depends(authenticated),
                    session: Session = Depends(get_session)) -> dict:
    user = session.get(User, principal.user.id)
    if not verify_password(user.password_hash, body.old_password):
        raise api_error(400, "wrong_password", "原密码错误")
    problems = password_problems(body.new_password, user.username, settings_of(request).min_password_length)
    if body.new_password == body.old_password:
        problems.append("新密码不能与原密码相同")
    if problems:
        raise api_error(400, "weak_password", "密码不符合要求：" + "；".join(problems))
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=utc_now()))
    audit(session, user.username, "auth.password_change", "user", str(user.id), **principal.audit_kwargs())
    tokens = issue_tokens(request, session, user)
    session.commit()
    return tokens


@router.post("/totp/setup")
def totp_setup(principal: Principal = Depends(authenticated)) -> dict:
    secret = new_totp_secret()
    return {"secret": secret, "uri": totp_uri(secret, principal.user.username)}


@router.post("/totp/enable")
def totp_enable(body: TotpEnableIn, principal: Principal = Depends(authenticated),
                session: Session = Depends(get_session)) -> dict:
    if not verify_totp(body.secret, body.code):
        raise api_error(400, "invalid_code", "验证码不正确")
    user = session.get(User, principal.user.id)
    user.totp_secret = body.secret
    audit(session, user.username, "auth.totp_enable", "user", str(user.id), **principal.audit_kwargs())
    session.commit()
    return {"ok": True}


@router.post("/totp/disable")
def totp_disable(body: TotpDisableIn, principal: Principal = Depends(authenticated),
                 session: Session = Depends(get_session)) -> dict:
    user = session.get(User, principal.user.id)
    if not verify_password(user.password_hash, body.password):
        raise api_error(400, "wrong_password", "密码错误")
    user.totp_secret = None
    audit(session, user.username, "auth.totp_disable", "user", str(user.id), **principal.audit_kwargs())
    session.commit()
    return {"ok": True}
