"""Roles and permission codes (design §6).  Built-in roles are synchronised
into the database at startup, so the matrix below is the source of truth."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db.models import Permission, Role, RolePermission, UserRole

PERMISSIONS: dict[str, tuple[str, str]] = {
    "dashboard:view": ("查看首页", "查看"),
    "market:view": ("查看行情", "查看"),
    "data:view": ("查看数据健康", "查看"),
    "event:view": ("查看通知", "查看"),
    "decision:view": ("查看决策", "决策"),
    "decision:approve": ("审核交易意图", "决策"),
    "decision:trigger": ("强制调仓与风控覆盖", "决策"),
    "account:view": ("查看账户", "账户"),
    "account:edit": ("回填成交与录入持仓", "账户"),
    "account:manage": ("新建与停用账户", "账户"),
    "user:manage": ("管理用户与角色", "系统"),
    "audit:view": ("查看审计日志", "系统"),
    "notify:manage": ("查看通知渠道与发送测试消息", "系统"),
    "position:use": ("使用仓位管家（自己的标的库、标签和结构）", "仓位管家"),
}
VIEW = {code for code in PERMISSIONS if code.endswith(":view") and code != "audit:view"}
ROLES: dict[str, tuple[str, str, set[str]]] = {
    "admin": ("管理员", "全部权限", set(PERMISSIONS)),
    "reviewer": ("审核员", "查看、审核交易意图、回填成交与持仓", VIEW | {"decision:approve", "account:edit",
                                                                    "position:use"}),
    # Everyone keeps their own library in the position manager.
    "viewer": ("只读", "只能查看；可以使用自己的仓位管家", VIEW | {"position:use"}),
}


def sync_roles(session: Session) -> None:
    for code, (name, group) in PERMISSIONS.items():
        permission = session.get(Permission, code)
        if permission is None:
            session.add(Permission(code=code, name=name, group_name=group))
        else:
            permission.name, permission.group_name = name, group
    session.flush()
    for code, (name, description, granted) in ROLES.items():
        role = session.get(Role, code)
        if role is None:
            session.add(Role(code=code, name=name, description=description))
        else:
            role.name, role.description = name, description
        session.flush()
        session.execute(delete(RolePermission).where(RolePermission.role_code == code))
        session.add_all(RolePermission(role_code=code, permission_code=p) for p in sorted(granted))
    session.flush()


def user_roles(session: Session, user_id: int) -> list[str]:
    return sorted(session.scalars(select(UserRole.role_code).where(UserRole.user_id == user_id)))


def user_permissions(session: Session, user_id: int) -> set[str]:
    query = (select(RolePermission.permission_code)
             .join(UserRole, UserRole.role_code == RolePermission.role_code)
             .where(UserRole.user_id == user_id))
    return set(session.scalars(query))
