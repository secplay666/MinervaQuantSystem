"""Append-only audit log (ARCHITECTURE §13): who did what, before and after, and why."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .db.models import AuditLog


def audit(session: Session, actor: str, action: str, subject_type: str, subject_id: str, *,
          before: Any = None, after: Any = None, reason: str | None = None, user_id: int | None = None,
          ip: str | None = None, request_id: str | None = None) -> AuditLog:
    row = AuditLog(actor=actor, action=action, subject_type=subject_type, subject_id=subject_id, before=before,
                   after=after, reason=reason, user_id=user_id, ip=ip, request_id=request_id)
    session.add(row)
    return row
