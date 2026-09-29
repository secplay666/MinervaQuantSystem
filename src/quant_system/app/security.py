"""Passwords, tokens and TOTP (ADR-010 §4).

* passwords: argon2id (argon2-cffi defaults), rehashed when parameters change;
* access tokens: JWT HS256, 15 minutes, carrying only the user id and name
  (permissions are looked up per request, so revocations apply at once);
* refresh tokens: 256-bit random strings, stored as sha256, rotated on every
  use; presenting a revoked one revokes all of the user's sessions (a stolen
  token was used);
* TOTP: RFC 6238, one step of clock drift allowed.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ISSUER = "Minerva"
_HASHER = PasswordHasher()


class TokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _HASHER.check_needs_rehash(password_hash)


def password_problems(password: str, username: str, min_length: int) -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"至少 {min_length} 个字符")
    if password.isdigit() or password.isalpha():
        problems.append("需要同时包含字母和数字或符号")
    if username and username.lower() in password.lower():
        problems.append("不能包含用户名")
    return problems


def create_access_token(secret: str, user_id: int, username: str, now: datetime, minutes: int) -> tuple[str, datetime]:
    expires = now + timedelta(minutes=minutes)
    claims = {"sub": str(user_id), "name": username, "typ": "access", "iat": int(now.timestamp()),
              "exp": int(expires.timestamp()), "jti": uuid.uuid4().hex}
    return jwt.encode(claims, secret, algorithm="HS256"), expires


def decode_access_token(secret: str, token: str) -> dict:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "typ"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != "access":
        raise TokenError("not an access token")
    return claims


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    return bool(code) and pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


INVITATION_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: codes are read out and typed


def new_invitation_code() -> str:
    """XXXX-XXXX-XXXX from 32 letters and digits (60 bits)."""
    chars = "".join(secrets.choice(INVITATION_ALPHABET) for _ in range(12))
    return "-".join(chars[k:k + 4] for k in range(0, 12, 4))


def normalize_invitation_code(code: str) -> str:
    """What is hashed: upper case, without separators and spaces."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def temporary_password() -> str:
    return secrets.token_urlsafe(9) + "7a"  # 14+ chars, letters and digits
