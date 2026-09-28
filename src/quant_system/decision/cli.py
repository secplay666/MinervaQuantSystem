"""``quant-decision``: business database, accounts and the daily decision job.

    quant-decision db upgrade
    quant-decision account create --id paper1 --name 模拟账户 --mode paper --cash 10000000 \
        --strategy configs/strategies/multifactor_rules.json --start 2026-09-28
    quant-decision account list
    quant-decision daily [--date 2026-09-30] [--account paper1] [--force-rebalance "建仓"] [--rerun]
    quant-decision show [--account paper1] [--date 2026-09-30]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

from ..app.db import DEFAULT_DB, open_database, upgrade
from ..app.db.models import Account, DecisionRun, OrderIntent
from ..domain.money import yuan_to_fen
from .accounts import create_account, mark, replay


def _root_from_file() -> Path:
    return Path(__file__).resolve().parents[3]


def _db(args: argparse.Namespace) -> Path:
    return Path(args.db).resolve() if args.db else Path(args.root).resolve() / DEFAULT_DB


def command_db(args: argparse.Namespace) -> int:
    upgrade(_db(args))
    print(f"database at head: {_db(args)}")
    return 0


def command_account_create(args: argparse.Namespace) -> int:
    _, factory = open_database(_db(args))
    with factory() as session:
        account = create_account(session, Path(args.root).resolve(), account_id=args.id, name=args.name,
                                 mode=args.mode, strategy_config=args.strategy,
                                 initial_cash_fen=yuan_to_fen(args.cash), start_date=date.fromisoformat(args.start),
                                 actor=args.actor, note=args.note)
        session.commit()
        print(f"created {account.mode} account {account.account_id} ({account.name}), cash {args.cash} CNY")
    return 0


def command_account_list(args: argparse.Namespace) -> int:
    _, factory = open_database(_db(args))
    with factory() as session:
        for account in session.scalars(select(Account).order_by(Account.account_id)):
            ledger = replay(session, account.account_id)
            print(f"{account.account_id:12s} {account.mode:6s} {'active' if account.is_active else 'inactive':8s} "
                  f"cash {ledger.cash_fen / 100:>16,.2f}  positions {len(ledger.positions):4d}  {account.name}  "
                  f"({account.strategy_config})")
    return 0


def command_daily(args: argparse.Namespace) -> int:
    from .job import run_daily

    outcomes = run_daily(Path(args.root).resolve(), db_path=_db(args),
                         session_date=date.fromisoformat(args.date) if args.date else None,
                         account_ids=args.account or None, force_reason=args.force_rebalance, rerun=args.rerun,
                         actor=args.actor)
    for outcome in outcomes:
        print(f"{outcome.account_id:12s} {outcome.status:9s} {outcome.kind or '-':9s} {outcome.run_id or '-'}  "
              f"{outcome.message}")
        if outcome.report_dir:
            print(f"{'':12s} report: {outcome.report_dir}")
    return 1 if any(outcome.status == "failed" for outcome in outcomes) else 0


def command_show(args: argparse.Namespace) -> int:
    _, factory = open_database(_db(args))
    with factory() as session:
        query = select(DecisionRun).where(DecisionRun.status != "superseded")
        if args.account:
            query = query.where(DecisionRun.account_id == args.account)
        if args.date:
            query = query.where(DecisionRun.trade_date == date.fromisoformat(args.date))
        runs = session.scalars(query.order_by(DecisionRun.trade_date.desc(), DecisionRun.created_at.desc())
                               .limit(args.limit)).all()
        for run in runs:
            failed = [g for g in run.gates if not g.get("passed")]
            print(f"{run.trade_date} {run.account_id:12s} {run.kind:9s} {run.status:9s} {run.run_id}"
                  f"{'  ' + failed[0]['gate'] + ': ' + failed[0]['message'] if failed else ''}")
            intents = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run.run_id)
                                      .order_by(OrderIntent.seq)).all()
            for intent in intents[: args.intents]:
                print(f"    {intent.seq:3d} {intent.side:4s} {intent.symbol} {intent.qty:>9,d} @ "
                      f"{intent.ref_price_fen / 100:>9.2f}  {intent.risk:6s} {intent.status}")
            if len(intents) > args.intents:
                print(f"    ... {len(intents) - args.intents} more")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage-4 decision workflow")
    parser.add_argument("--root", default=str(_root_from_file()))
    parser.add_argument("--db", help=f"business database (default <root>/{DEFAULT_DB.as_posix()})")
    parser.add_argument("--actor", default="cli", help="recorded as the operator")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db", help="database maintenance")
    db.add_argument("action", choices=["upgrade"])
    db.set_defaults(handler=command_db)

    account = sub.add_parser("account", help="accounts").add_subparsers(dest="account_command", required=True)
    create = account.add_parser("create")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--mode", choices=["paper", "manual"], required=True)
    create.add_argument("--cash", required=True, help="initial cash in CNY")
    create.add_argument("--strategy", default="configs/strategies/multifactor_rules.json")
    create.add_argument("--start", default=date.today().isoformat())
    create.add_argument("--note")
    create.set_defaults(handler=command_account_create)
    listing = account.add_parser("list")
    listing.set_defaults(handler=command_account_list)

    daily = sub.add_parser("daily", help="run the decision for the latest final session")
    daily.add_argument("--date", help="decision session (default: latest final session)")
    daily.add_argument("--account", action="append", help="limit to these account ids")
    daily.add_argument("--force-rebalance", metavar="REASON", help="rebalance today regardless of the schedule")
    daily.add_argument("--rerun", action="store_true", help="replace today's complete decision")
    daily.set_defaults(handler=command_daily)

    show = sub.add_parser("show", help="recent decisions and their intents")
    show.add_argument("--account")
    show.add_argument("--date")
    show.add_argument("--limit", type=int, default=5)
    show.add_argument("--intents", type=int, default=15)
    show.set_defaults(handler=command_show)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
