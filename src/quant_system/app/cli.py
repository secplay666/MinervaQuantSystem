"""``quant-app``: API server and administration from the command line.

    quant-app init-secret                        # ~/.config/minerva/app.env with a fresh secret (mode 600)
    quant-app user create --username admin --display-name 管理员 --role admin
    quant-app user reset-password --username admin
    quant-app serve [--host 127.0.0.1] [--port 8000]
    quant-app openapi --out web/openapi.json     # schema for the frontend's generated client
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .db.base import open_database
from .db.models import RefreshToken, User, UserRole
from .rbac import ROLES, sync_roles
from .security import hash_password, temporary_password
from .settings import DEFAULT_ENV_FILE, SettingsError, init_env_file, load_settings


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _settings(args: argparse.Namespace):
    try:
        return load_settings(Path(args.root).resolve(), Path(args.env_file) if args.env_file else None)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)


def command_init_secret(args: argparse.Namespace) -> int:
    path = Path(args.env_file) if args.env_file else DEFAULT_ENV_FILE
    print(f"created {path}" if init_env_file(path) else f"{path} already exists; left unchanged")
    return 0


def command_user_create(args: argparse.Namespace) -> int:
    settings = _settings(args)
    _, sessions = open_database(settings.db_path)
    with sessions() as session:
        sync_roles(session)
        if session.scalar(select(User).where(User.username == args.username)):
            print(f"user {args.username} exists", file=sys.stderr)
            return 1
        password = temporary_password()
        user = User(username=args.username, display_name=args.display_name or args.username,
                    password_hash=hash_password(password), must_change_password=True)
        session.add(user)
        session.flush()
        for role in args.role:
            session.add(UserRole(user_id=user.id, role_code=role))
        session.commit()
    print(f"created {args.username} ({', '.join(args.role)}); temporary password (change at first login): "
          f"{password}")
    return 0


def command_user_reset(args: argparse.Namespace) -> int:
    settings = _settings(args)
    _, sessions = open_database(settings.db_path)
    with sessions() as session:
        user = session.scalar(select(User).where(User.username == args.username))
        if user is None:
            print(f"no user {args.username}", file=sys.stderr)
            return 1
        password = temporary_password()
        user.password_hash, user.must_change_password = hash_password(password), True
        user.failed_logins, user.locked_until = 0, None
        for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id,
                                                                RefreshToken.revoked_at.is_(None))):
            from .db.base import utc_now

            token.revoked_at = utc_now()
        session.commit()
    print(f"temporary password for {args.username}: {password}")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .main import create_app

    app = create_app(_settings(args))
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                log_level="info")
    return 0


def command_openapi(args: argparse.Namespace) -> int:
    from .main import create_app
    from .settings import AppSettings

    root = Path(args.root).resolve()
    settings = AppSettings(root=root, db_path=Path(args.scratch_db).resolve(), secret_key="x" * 32)
    schema = create_app(settings).openapi()
    Path(args.out).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minerva decision-support API")
    parser.add_argument("--root", default=str(_root()))
    parser.add_argument("--env-file", help=f"settings file (default {DEFAULT_ENV_FILE})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-secret").set_defaults(handler=command_init_secret)
    user = sub.add_parser("user").add_subparsers(dest="user_command", required=True)
    create = user.add_parser("create")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name")
    create.add_argument("--role", action="append", choices=sorted(ROLES), required=True)
    create.set_defaults(handler=command_user_create)
    reset = user.add_parser("reset-password")
    reset.add_argument("--username", required=True)
    reset.set_defaults(handler=command_user_reset)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=command_serve)
    openapi = sub.add_parser("openapi")
    openapi.add_argument("--out", required=True)
    openapi.add_argument("--scratch-db", default="openapi-scratch.sqlite")
    openapi.set_defaults(handler=command_openapi)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
