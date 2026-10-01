"""``quant-app``: API server and administration from the command line.

    quant-app init-secret                        # ~/.config/minerva/app.env with a fresh secret (mode 600)
    quant-app user create --username admin --display-name 管理员 --role admin
    quant-app user reset-password --username admin
    quant-app tls init --ip 8.159.139.145 --ip 127.0.0.1   # private CA + server certificate
    quant-app tls show                                       # fingerprints and the app's SPKI pins
    quant-app serve [--host 127.0.0.1] [--port 8443]         # HTTPS when a server certificate exists
    quant-app openapi --out web/openapi.json     # schema for the frontend's generated client
    quant-app event add --level critical --category data --title "..."   # record an event (scripts)
    quant-app notify status | test | dispatch    # external notifications (app/notify.py)
    quant-app pm daily                           # position manager signals after the data update
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


def _settings(args: argparse.Namespace, require_secret: bool = True):
    try:
        return load_settings(Path(args.root).resolve(), Path(args.env_file) if args.env_file else None,
                             require_secret=require_secret)
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
        user.totp_secret = None  # a lost authenticator is the usual reason for a reset
        for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id,
                                                                RefreshToken.revoked_at.is_(None))):
            from .db.base import utc_now

            token.revoked_at = utc_now()
        session.commit()
    print(f"temporary password for {args.username}: {password}")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    import ssl

    import uvicorn

    from .main import create_app
    from .tls import DEFAULT_TLS_DIR

    tls_dir = Path(args.tls_dir) if args.tls_dir else DEFAULT_TLS_DIR
    cert, key = tls_dir / "server.crt", tls_dir / "server.key"
    use_tls = not args.no_tls and cert.is_file() and key.is_file()
    options = {"ssl_certfile": str(cert), "ssl_keyfile": str(key), "ssl_version": ssl.PROTOCOL_TLS_SERVER,
               "ssl_ciphers": "ECDHE+AESGCM:ECDHE+CHACHA20"} if use_tls else {}
    print(f"serving {'https' if use_tls else 'http'}://{args.host}:{args.port}", flush=True)
    # No proxy headers: frp forwards TCP, so X-Forwarded-For would come from the client itself.
    uvicorn.run(create_app(_settings(args)), host=args.host, port=args.port, proxy_headers=False,
                log_level="info", **options)
    return 0


def command_tls_init(args: argparse.Namespace) -> int:
    from .tls import DEFAULT_TLS_DIR, describe, init_ca, issue_server_certificate

    directory = Path(args.tls_dir) if args.tls_dir else DEFAULT_TLS_DIR
    init_ca(directory)
    issue_server_certificate(directory, args.ip or [], args.dns or [])
    print(json.dumps(describe(directory), ensure_ascii=False, indent=2))
    return 0


def command_tls_show(args: argparse.Namespace) -> int:
    from .tls import DEFAULT_TLS_DIR, describe

    print(json.dumps(describe(Path(args.tls_dir) if args.tls_dir else DEFAULT_TLS_DIR), ensure_ascii=False,
                     indent=2))
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


def command_event_add(args: argparse.Namespace) -> int:
    import secrets
    from datetime import date

    from .db.base import utc_now
    from .db.models import Event

    settings = _settings(args, require_secret=False)
    _, sessions = open_database(settings.db_path)
    now = utc_now()
    event_id = args.id or f"{args.category}-{now:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"
    with sessions() as session:
        if session.get(Event, event_id) is None:
            session.add(Event(event_id=event_id, at=now, level=args.level, category=args.category, title=args.title,
                              body=args.body, trade_date=date.fromisoformat(args.trade_date) if args.trade_date
                              else None, account_id=args.account, action_hint=args.hint))
            session.commit()
    print(event_id)
    return 0


def command_notify_status(args: argparse.Namespace) -> int:
    print(json.dumps(_settings(args, require_secret=False).notify.describe(), ensure_ascii=False, indent=2))
    return 0


def command_notify_test(args: argparse.Namespace) -> int:
    from .notify import send_test

    settings = _settings(args, require_secret=False)
    return _report(settings, send_test(settings.notify, settings.environment_label))


def command_notify_dispatch(args: argparse.Namespace) -> int:
    from .notify import dispatch

    settings = _settings(args, require_secret=False)
    _, sessions = open_database(settings.db_path)
    with sessions() as session:
        return _report(settings, dispatch(session, settings.notify, settings.environment_label))


def command_pm_daily(args: argparse.Namespace) -> int:
    """After the data update: every user's position manager signals (and the daily push events)."""
    from .market import MarketQueries
    from .position import run_daily

    settings = _settings(args, require_secret=False)
    market = MarketQueries(settings.market_db, settings.root / "configs" / "market_rules" / "cn_a_share.json")
    if not market.available():
        print(f"no market database at {settings.market_db}", file=sys.stderr)
        return 1
    _, sessions = open_database(settings.db_path)
    with sessions() as session:
        rows = run_daily(session, market)
    for row in rows:
        if row["error"]:
            print(f"user {row['user_id']}: failed ({row['error']})")
        else:
            print(f"user {row['user_id']}: {row['items']} items, {row['new_signals']} new signals through "
                  f"{row['latest']}" + (f", event {row['event']}" if row["event"] else ""))
    return 1 if any(row["error"] for row in rows) else 0


def _report(settings, results) -> int:  # type: ignore[no-untyped-def]
    for problem in settings.notify.problems:
        print(f"config: {problem}", file=sys.stderr)
    if not results:
        print("no notification channel configured (MINERVA_NOTIFY_WECOM / MINERVA_NOTIFY_SERVERCHAN)")
    for result in results:
        print(f"{result.channel}: {result.status}" + (f", {result.events} events" if result.events else "")
              + (f" ({result.error})" if result.error else ""))
    return 1 if any(r.status == "failed" for r in results) else 0


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
    parser.add_argument("--tls-dir", help="certificate directory (default ~/.config/minerva/tls)")
    tls = sub.add_parser("tls").add_subparsers(dest="tls_command", required=True)
    tls_init = tls.add_parser("init", help="create the CA (once) and (re)issue the server certificate")
    tls_init.add_argument("--ip", action="append")
    tls_init.add_argument("--dns", action="append")
    tls_init.set_defaults(handler=command_tls_init)
    tls.add_parser("show").set_defaults(handler=command_tls_show)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8443)
    serve.add_argument("--no-tls", action="store_true", help="plain HTTP even if a certificate exists")
    serve.set_defaults(handler=command_serve)
    openapi = sub.add_parser("openapi")
    openapi.add_argument("--out", required=True)
    openapi.add_argument("--scratch-db", default="openapi-scratch.sqlite")
    openapi.set_defaults(handler=command_openapi)
    event = sub.add_parser("event").add_subparsers(dest="event_command", required=True)
    event_add = event.add_parser("add", help="record an event in the notification centre")
    event_add.add_argument("--level", choices=["info", "warning", "critical"], required=True)
    event_add.add_argument("--category", choices=["data", "decision", "risk", "account", "system"], required=True)
    event_add.add_argument("--title", required=True)
    event_add.add_argument("--body")
    event_add.add_argument("--hint")
    event_add.add_argument("--account")
    event_add.add_argument("--trade-date")
    event_add.add_argument("--id", help="event id; an existing id is left unchanged")
    event_add.set_defaults(handler=command_event_add)
    notify = sub.add_parser("notify").add_subparsers(dest="notify_command", required=True)
    notify.add_parser("status", help="configured channels (secrets masked)").set_defaults(
        handler=command_notify_status)
    notify.add_parser("test", help="send a test message to every channel").set_defaults(handler=command_notify_test)
    notify.add_parser("dispatch", help="push a digest of new events").set_defaults(handler=command_notify_dispatch)
    pm = sub.add_parser("pm").add_subparsers(dest="pm_command", required=True)
    pm.add_parser("daily", help="position manager: evaluate every user's library, store new signals").set_defaults(
        handler=command_pm_daily)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
