"""``quant-decision daily``: one decision run per active account (design §2, ADR-008).

Order of work:
  1. expire intents past their review deadline;
  2. G1 (ingest complete for T) and G2 (data version) once for all accounts;
  3. per account, in its own transaction: replay the ledger and mark it at T;
     monitoring alerts every session; on a rebalance (or forced) session G4,
     the strategy target, G3 and G5, then intents with pre-trade checks;
  4. database rows, snapshots, events and the report directory.

A failure in one account is recorded as a ``failed`` run with its message and
does not stop the others.  A second run for the same account and session
needs ``rerun``; it supersedes the earlier one unless any of its intents
were already reviewed or filled.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..app.audit import audit
from ..app.db import DEFAULT_DB, open_database, utc_now
from ..app.db.models import (
    Account,
    Approval,
    DecisionRun,
    Event,
    Fill,
    OrderIntent,
    RiskCheck,
    TargetPosition,
)
from ..backtest.config import BacktestConfig
from ..backtest.market_data import MarketData, load_market_data
from ..backtest.view import PanelView
from ..data_platform.sessions import SHANGHAI_TZ, shanghai_now
from ..data_platform.utils import code_version, json_hash
from ..domain.entities import TargetPortfolio
from ..domain.fees import FeeSchedule
from ..domain.rules import MarketRules
from ..strategy.registry import StrategyContext, build_strategy
from . import gates as G
from .accounts import MarkedAccount, mark, replay, write_snapshots
from .inputs import (
    IngestRecord,
    decision_session,
    exchange_calendar,
    final_time,
    is_rebalance_day,
    latest_ingest,
    next_session,
)
from .intents import IntentPlan, plan_intents, turnover
from .monitor import Alert, monitor_holdings
from .paper import run_paper
from .reference import FrameReference, load_reference
from .report import write_report

LOGGER = logging.getLogger(__name__)
DECISIONS_DIR = Path("artifacts") / "decisions"
REVIEW_CLOSE = dtime(15, 0)
REVIEWED = ("approved", "modified")


class RerunRefused(RuntimeError):
    """The earlier run of this session already has reviewed or filled intents."""


@dataclass
class DecisionLoaders:
    ingest: Callable[[], IngestRecord | None]
    calendar: Callable[[IngestRecord], list[date]]
    market: Callable[[], MarketData]
    research: Callable[[MarketData], Any]
    reference: Callable[[MarketData, date], FrameReference]


def default_loaders(root: Path) -> DecisionLoaders:
    database = root / "data" / "market.duckdb"

    def research(market: MarketData) -> Any:
        from ..research.data import load_research_data

        return load_research_data("duckdb", database, market=market)

    return DecisionLoaders(ingest=lambda: latest_ingest(root),
                           calendar=lambda record: exchange_calendar(root, record.run_id),
                           market=lambda: load_market_data("duckdb", database), research=research,
                           reference=lambda market, since: load_reference(database, since))


@dataclass
class RunOutcome:
    account_id: str
    run_id: str | None
    status: str  # complete | blocked | failed | skipped
    kind: str | None
    message: str
    intents: int = 0
    report_dir: str | None = None


def load_strategy_config(root: Path, relative: str, session: date) -> tuple[BacktestConfig, str]:
    """The account's strategy config with the run window set to the decision
    session (the strategy is built through T, exactly as a backtest ending at
    T), and the hash of the config file as written."""
    payload = json.loads((root / relative).read_text(encoding="utf-8"))
    window = {**payload.get("run", {}), "start": session.isoformat(), "end": session.isoformat()}
    return BacktestConfig.from_payload({**payload, "run": window}, root), json_hash(payload)


class _Shared:
    """Data loaded once per invocation and shared by all accounts."""

    def __init__(self, root: Path, loaders: DecisionLoaders, record: IngestRecord, calendar: list[date],
                 session: date, market: MarketData, next_day: date, reference: FrameReference) -> None:
        self.root, self.loaders, self.record, self.calendar = root, loaders, record, calendar
        self.session, self.market, self.next_day, self.reference = session, market, next_day, reference
        self.i = market.session_index(session)
        self.version = code_version(root)
        self._research: Any = None
        self._strategies: dict[str, Any] = {}
        self._rules: dict[Path, MarketRules] = {}

    def research(self) -> Any:
        if self._research is None:
            self._research = self.loaders.research(self.market)
        return self._research

    def rules(self, path: Path) -> MarketRules:
        if path not in self._rules:
            self._rules[path] = MarketRules.load(path)
        return self._rules[path]

    def strategy(self, relative: str, config: BacktestConfig) -> Any:
        if relative not in self._strategies:
            started = time.perf_counter()
            self._strategies[relative] = build_strategy(config, StrategyContext(self.root, self.market, self.research))
            LOGGER.info("Built strategy %s in %.1f s", relative, time.perf_counter() - started)
        return self._strategies[relative]


# -- database helpers -------------------------------------------------------------------


def expire_intents(session: Session, now: datetime) -> int:
    rows = session.scalars(select(OrderIntent).where(OrderIntent.status == "pending_approval",
                                                     OrderIntent.valid_until < now)).all()
    for intent in rows:
        intent.status = "expired"
        session.add(Approval(intent_id=intent.intent_id, action="expire", qty_before=intent.qty,
                             qty_after=intent.qty, actor="system", reason="审核截止时间已过"))
    return len(rows)


def last_target(session: Session, account_id: str, before: date) -> TargetPortfolio | None:
    run = session.scalars(select(DecisionRun).where(
        DecisionRun.account_id == account_id, DecisionRun.status == "complete",
        DecisionRun.kind.in_(("rebalance", "forced")), DecisionRun.trade_date < before,
    ).order_by(DecisionRun.trade_date.desc(), DecisionRun.created_at.desc())).first()
    if run is None:
        return None
    rows = session.scalars(select(TargetPosition).where(TargetPosition.run_id == run.run_id)).all()
    return TargetPortfolio(run.trade_date, {r.symbol: r.target_weight for r in rows},
                           {r.symbol: dict(r.explanation) for r in rows})


def unfilled_intents(session: Session, account: Account, session_date: date) -> list[str]:
    """Reviewed intents executed by now without a fill and without a later holdings confirmation."""
    filled = select(Fill.intent_id).where(Fill.intent_id.is_not(None), Fill.reversed_by.is_(None))
    query = select(OrderIntent.intent_id).where(
        OrderIntent.account_id == account.account_id, OrderIntent.status.in_(REVIEWED),
        OrderIntent.execute_on <= session_date, OrderIntent.intent_id.not_in(filled))
    if account.holdings_confirmed_date is not None:
        query = query.where(OrderIntent.execute_on > account.holdings_confirmed_date)
    return list(session.scalars(query.order_by(OrderIntent.intent_id)))


def supersede(session: Session, run: DecisionRun, actor: str) -> None:
    intents = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run.run_id)).all()
    ids = [intent.intent_id for intent in intents]
    fills = session.scalar(select(func.count()).select_from(Fill).where(Fill.intent_id.in_(ids))) if ids else 0
    if any(intent.status in REVIEWED for intent in intents) or fills:
        raise RerunRefused(f"{run.run_id} already has reviewed or filled intents; reject them before a rerun")
    run.status = "superseded"
    for intent in intents:
        if intent.status in ("pending_approval", "rejected_by_risk"):
            intent.status = "superseded"
            session.add(Approval(intent_id=intent.intent_id, action="supersede", qty_before=intent.qty,
                                 qty_after=intent.qty, actor=actor, reason="同一交易日重新生成决策"))


def signal_counts(strategy: Any, market: MarketData, i: int) -> tuple[dict[str, int], list[dict[str, int]]] | None:
    """Universe size and scored names at T and at earlier rebalance rows (G3);
    None for strategies without a factor panel."""
    panel = getattr(strategy, "panel", None)
    if panel is None or not hasattr(strategy, "composite"):
        return None

    def counts(t: int) -> dict[str, int]:
        view = PanelView(market, t)
        universe = panel.universe_at(view)
        scores = strategy.composite(view)
        return {"universe": int(universe.sum()), "scored": int((universe & np.isfinite(scores)).sum())}

    earlier = [int(t) for t in strategy.rows if int(t) < i][-G.SIGNAL_HISTORY:]
    return counts(i), [counts(t) for t in earlier]


# -- the run -----------------------------------------------------------------------------


def run_daily(root: Path, *, db_path: Path | None = None, session_date: date | None = None,
              now: datetime | None = None, account_ids: list[str] | None = None, force_reason: str | None = None,
              rerun: bool = False, actor: str = "system", loaders: DecisionLoaders | None = None) -> list[RunOutcome]:
    loaders = loaders or default_loaders(root)
    now = now or shanghai_now()
    _, factory = open_database(db_path or root / DEFAULT_DB)
    with factory() as session:
        query = select(Account).where(Account.is_active.is_(True)).order_by(Account.account_id)
        if account_ids:
            query = query.where(Account.account_id.in_(account_ids))
        accounts = [account.account_id for account in session.scalars(query)]
        expired = expire_intents(session, now)
        session.commit()
    if expired:
        LOGGER.info("Expired %s intents past their review deadline", expired)
    if not accounts:
        LOGGER.warning("No active accounts; nothing to decide")
        return []

    record = loaders.ingest()
    calendar: list[date] = []
    problem = None
    if record is not None:
        try:
            calendar = loaders.calendar(record)
        except FileNotFoundError as exc:
            problem = str(exc)
    session_day = session_date or (decision_session(calendar, now, final_time(root)) if calendar else None)
    if session_day is None:
        message = problem or "无法确定决策日（没有采集记录或交易日历）"
        with factory() as session:
            _event(session, f"decision-{now:%Y%m%dT%H%M%S}-no-session", "critical", "decision", "无法生成决策",
                   message, None, None, None, "检查每日采集是否运行")
            session.commit()
        return [RunOutcome(account, None, "failed", None, message) for account in accounts]

    common = [G.g1_ingest(record, session_day)]
    following = next_session(calendar, session_day) if calendar else None
    if common[0].passed and following is None:
        common[0] = G.GateResult("G1", False, f"交易所日历中没有 {session_day} 之后的交易日", {},
                                 "等待交易所公布新年度日历后重跑")
    if not common[0].passed:
        return [_record_blocked(factory, root, account, session_day, following, record, common, actor, force_reason)
                for account in accounts]
    market = loaders.market()
    common.append(G.g2_data_version(record, market.metadata.get("data_version")))
    if not common[1].passed:
        return [_record_blocked(factory, root, account, session_day, following, record, common, actor, force_reason)
                for account in accounts]
    shared = _Shared(root, loaders, record, calendar, session_day, market, following,
                     loaders.reference(market, session_day))
    outcomes = []
    for account in accounts:
        try:
            outcomes.append(_run_account(factory, shared, account, common, force_reason, rerun, actor))
        except RerunRefused as exc:
            outcomes.append(RunOutcome(account, None, "skipped", None, str(exc)))
        except Exception as exc:  # recorded as a failed run; the other accounts continue
            LOGGER.exception("Decision for %s failed", account)
            outcomes.append(_record_failed(factory, shared, account, exc, actor, force_reason))
    return outcomes


def _new_run_id(session: Session, account_id: str, day: date) -> str:
    runs = session.scalar(select(func.count()).select_from(DecisionRun).where(
        DecisionRun.account_id == account_id, DecisionRun.trade_date == day)) or 0
    base = f"dr-{day:%Y%m%d}-{account_id}"
    return base if runs == 0 else f"{base}-r{runs}"


def _event(session: Session, event_id: str, level: str, category: str, title: str, body: str | None,
           run_id: str | None, trade_date: date | None, account_id: str | None, hint: str | None = None,
           symbol: str | None = None) -> None:
    if session.get(Event, event_id) is None:
        session.add(Event(event_id=event_id, level=level, category=category, title=title, body=body, run_id=run_id,
                          trade_date=trade_date, account_id=account_id, symbol=symbol, action_hint=hint))


def _existing(session: Session, account_id: str, day: date) -> list[DecisionRun]:
    return list(session.scalars(select(DecisionRun).where(
        DecisionRun.account_id == account_id, DecisionRun.trade_date == day,
        DecisionRun.status != "superseded").order_by(DecisionRun.created_at)))


def _record_blocked(factory: sessionmaker[Session], root: Path, account_id: str, day: date, following: date | None,
                    record: IngestRecord | None, gates: list[G.GateResult], actor: str,
                    reason: str | None) -> RunOutcome:
    failed = next(g for g in gates if not g.passed)
    with factory() as session:
        existing = _existing(session, account_id, day)
        complete = [run for run in existing if run.status == "complete"]
        if complete:  # a later failed check never replaces a finished decision
            return RunOutcome(account_id, complete[-1].run_id, "skipped", complete[-1].kind,
                              f"当日已有完成的决策；本次检查未通过：{failed.message}")
        if existing and existing[-1].status == "blocked" and existing[-1].gates == [g.to_dict() for g in gates]:
            return RunOutcome(account_id, existing[-1].run_id, "blocked", existing[-1].kind, failed.message)
        for run in existing:
            supersede(session, run, actor)
        run_id = _new_run_id(session, account_id, day)
        session.add(DecisionRun(run_id=run_id, account_id=account_id, trade_date=day, next_session=following,
                                kind="forced" if reason else "monitor", status="blocked",
                                gates=[g.to_dict() for g in gates], ingest_run_id=record.run_id if record else None,
                                data_version=record.data_version if record else None, code_version=code_version(root),
                                created_by=actor, reason=reason))
        _event(session, f"{run_id}-blocked", "critical", "decision", f"决策被闸门 {failed.gate} 阻断", failed.message,
               run_id, day, account_id, failed.hint)
        audit(session, actor, "decision.run", "decision_run", run_id, after={"status": "blocked", "gate": failed.gate},
              reason=reason)
        session.commit()
    return RunOutcome(account_id, run_id, "blocked", None, failed.message)


def _record_failed(factory: sessionmaker[Session], shared: _Shared, account_id: str, exc: Exception, actor: str,
                   reason: str | None) -> RunOutcome:
    message = f"{type(exc).__name__}: {exc}"
    with factory() as session:
        run_id = _new_run_id(session, account_id, shared.session)
        session.add(DecisionRun(run_id=run_id, account_id=account_id, trade_date=shared.session,
                                next_session=shared.next_day, kind="forced" if reason else "monitor",
                                status="failed", gates=[], ingest_run_id=shared.record.run_id,
                                data_version=shared.record.data_version, code_version=shared.version,
                                created_by=actor,
                                summary={"traceback": traceback.format_exc(limit=8)[-4000:]}, reason=message))
        _event(session, f"{run_id}-failed", "critical", "decision", "决策运行失败", message, run_id, shared.session,
               account_id, "查看日志与运行记录中的错误信息")
        session.commit()
    return RunOutcome(account_id, run_id, "failed", None, message)


def _run_account(factory: sessionmaker[Session], shared: _Shared, account_id: str, common: list[G.GateResult],
                 force_reason: str | None, rerun: bool, actor: str) -> RunOutcome:
    started = time.perf_counter()
    market, day, i, next_day = shared.market, shared.session, shared.i, shared.next_day
    with factory() as session:
        account = session.get(Account, account_id)
        if account.start_date > day:
            return RunOutcome(account_id, None, "skipped", None, f"账户从 {account.start_date} 开始，早于该日不决策")
        existing = _existing(session, account_id, day)
        complete = [run for run in existing if run.status == "complete"]
        if complete and not rerun:
            last = complete[-1]
            return RunOutcome(account_id, last.run_id, "skipped", last.kind, "当日已有决策（需要重跑请加 --rerun）")
        for run in existing:  # blocked / failed runs are replaced without asking
            supersede(session, run, actor)
        run_id = _new_run_id(session, account_id, day)
        config, config_hash = load_strategy_config(shared.root, account.strategy_config, day)
        rules = shared.rules(config.market_rules_path)
        paper = run_paper(session, account, market, rules, config, through=day) if account.mode == "paper" else None
        marked = mark(replay(session, account_id, through=day), market, i)
        rebalance = is_rebalance_day(config.schedule, shared.calendar, market.sessions, day)
        kind = "forced" if force_reason else "rebalance" if rebalance else "monitor"
        previous = last_target(session, account_id, before=day)
        alerts = monitor_holdings(marked, market, rules, i, next_day, shared.reference,
                                  previous.weights if previous else None, account.mode == "manual")
        gates = list(common)
        target: TargetPortfolio | None = None
        plan: IntentPlan | None = None
        strategy = None
        if kind != "monitor":
            gates.append(G.g4_holdings(account.mode, account.holdings_confirmed_date,
                                       unfilled_intents(session, account, day), day))
            if gates[-1].passed:
                strategy = shared.strategy(account.strategy_config, config)
                target = strategy.on_close(PanelView(market, i), marked.portfolio_state(previous))
                counts = signal_counts(strategy, market, i)
                gates.append(G.g3_signal_stability(*counts) if counts else
                             G.GateResult("G3", True, "该策略没有因子面板，跳过"))
                construction = getattr(getattr(strategy, "params", None), "construction", None)
                gates.append(G.g5_target(target.weights, getattr(construction, "n_holdings", None)))
        status = "complete" if all(g.passed for g in gates) else "blocked"
        if status == "complete" and target is not None:
            construction = getattr(getattr(strategy, "params", None), "construction", None)
            fees = FeeSchedule(rules, config.commission_rate, config.commission_min_fen)
            plan = plan_intents(target, marked, market, rules, config.sizing, fees, config.max_participation,
                                getattr(construction, "max_weight", None), i, next_day, shared.reference)
        valid_until = datetime.combine(next_day, REVIEW_CLOSE, SHANGHAI_TZ)
        summary = _summary(marked, target, plan, alerts)
        if paper is not None:
            summary["paper"] = {"sessions": [d.isoformat() for d in paper.sessions], "fills": paper.fills,
                                "closed_unfilled": paper.unfilled}
        summary["seconds"] = round(time.perf_counter() - started, 2)
        report_dir = shared.root / DECISIONS_DIR / run_id
        session.add(DecisionRun(
            run_id=run_id, account_id=account_id, trade_date=day, next_session=next_day, kind=kind, status=status,
            gates=[g.to_dict() for g in gates], ingest_run_id=shared.record.run_id,
            data_version=shared.record.data_version, code_version=shared.version, config_hash=config_hash,
            strategy_id=config.strategy_id, strategy_version=config.strategy_version, nav_fen=marked.nav_fen,
            cash_fen=marked.cash_fen, positions=len(marked.quantities), summary=summary,
            report_dir=report_dir.relative_to(shared.root).as_posix(), reason=force_reason, created_by=actor))
        session.flush()
        targets = _target_rows(target, plan)
        for row in targets:
            session.add(TargetPosition(run_id=run_id, **row))
        intents = plan.intents if plan else []
        for draft in intents:
            intent_id = f"{run_id}-{draft.seq:03d}-{draft.symbol}-{draft.side}"
            session.add(OrderIntent(
                intent_id=intent_id, run_id=run_id, account_id=account_id, trade_date=day, execute_on=next_day,
                seq=draft.seq, symbol=draft.symbol, side=draft.side, qty=draft.qty, proposed_qty=draft.qty,
                ref_price_fen=draft.ref_price_fen, limit_up_fen=draft.limit_up_fen,
                limit_down_fen=draft.limit_down_fen, est_notional_fen=draft.est_notional_fen,
                est_fees_fen=draft.est_fees_fen, reason=draft.reason, rank=draft.rank, risk=draft.risk,
                status="rejected_by_risk" if draft.risk == "reject" else "pending_approval",
                valid_until=valid_until))
            session.flush()
            for check in draft.checks:
                session.add(RiskCheck(run_id=run_id, intent_id=intent_id, rule_id=check.rule_id,
                                      decision=check.decision, actual=check.actual, limit_value=check.limit,
                                      message=check.message))
        for check in plan.run_checks if plan else []:
            session.add(RiskCheck(run_id=run_id, intent_id=None, rule_id=check.rule_id, decision=check.decision,
                                  actual=check.actual, limit_value=check.limit, message=check.message))
        for alert in alerts:
            _event(session, f"{run_id}-{alert.rule}-{alert.symbol or 'account'}", alert.level, "risk", alert.title,
                   alert.body, run_id, day, account_id, alert.hint, alert.symbol)
        if status == "blocked":
            failed = next(g for g in gates if not g.passed)
            _event(session, f"{run_id}-blocked", "critical", "decision", f"决策被闸门 {failed.gate} 阻断",
                   failed.message, run_id, day, account_id, failed.hint)
        elif intents:
            sells = sum(1 for d in intents if d.side == "sell")
            _event(session, f"{run_id}-ready", "info", "decision",
                   f"交易清单已生成：卖出 {sells} 笔，买入 {len(intents) - sells} 笔，待审核",
                   f"执行日 {next_day}，审核截止 {valid_until:%Y-%m-%d %H:%M}", run_id, day, account_id,
                   "在每日决策页审核")
        if paper is not None and (paper.fills or paper.unfilled):
            _event(session, f"{run_id}-paper", "info", "account",
                   f"模拟成交 {paper.fills} 笔" + (f"，{paper.unfilled} 笔未能成交" if paper.unfilled else ""),
                   f"处理交易日 {', '.join(d.isoformat() for d in paper.sessions)}", run_id, day, account_id,
                   "在账户页查看成交与持仓")
        write_snapshots(session, account_id, marked)
        audit(session, actor, "decision.run", "decision_run", run_id,
              after={"status": status, "kind": kind, "intents": len(intents)}, reason=force_reason)
        header = {"run_id": run_id, "account_name": account.name, "trade_date": day.isoformat(), "kind": kind,
                  "status": status, "next_session": next_day.isoformat(),
                  "valid_until": f"{valid_until:%Y-%m-%d %H:%M}" if intents else None, "nav_fen": marked.nav_fen,
                  "cash_fen": marked.cash_fen, "positions": len(marked.quantities),
                  "ingest_run_id": shared.record.run_id, "data_version": shared.record.data_version}
        manifest = {**header, "account_id": account_id, "gates": [g.to_dict() for g in gates],
                    "code_version": shared.version, "config": account.strategy_config, "config_hash": config_hash,
                    "summary": summary, "created_at": utc_now().isoformat(), "created_by": actor,
                    "reason": force_reason}
        write_report(report_dir, header=header, gates=gates, alerts=alerts, targets=targets, intents=intents,
                     manifest=manifest)
        session.commit()
    message = next((g.message for g in gates if not g.passed), f"{len(intents)} 条交易意图")
    return RunOutcome(account_id, run_id, status, kind, message, len(intents), str(report_dir))


def _target_rows(target: TargetPortfolio | None, plan: IntentPlan | None) -> list[dict[str, Any]]:
    if target is None:
        return []
    rows = []
    for symbol in sorted(target.weights, key=lambda s: (target.explanations.get(s, {}).get("rank", 10**9), s)):
        info = dict(target.explanations.get(symbol, {}))
        rows.append({"symbol": symbol, "target_weight": float(target.weights[symbol]),
                     "target_qty": plan.target_qty.get(symbol) if plan else None, "rank": info.get("rank"),
                     "score": info.get("score"), "explanation": info})
    return rows


def _summary(marked: MarkedAccount, target: TargetPortfolio | None, plan: IntentPlan | None,
             alerts: list[Alert]) -> dict[str, Any]:
    summary: dict[str, Any] = {"alerts": len(alerts),
                               "alert_levels": {level: sum(1 for a in alerts if a.level == level)
                                                for level in ("info", "warning", "critical")}}
    if target is not None:
        summary["target_names"] = len(target.weights)
        summary["target_weight_sum"] = round(sum(target.weights.values()), 6)
    if plan is not None:
        buys = [d for d in plan.intents if d.side == "buy"]
        sells = [d for d in plan.intents if d.side == "sell"]
        summary.update({
            "buys": len(buys), "sells": len(sells),
            "buy_notional_fen": sum(d.est_notional_fen for d in buys),
            "sell_notional_fen": sum(d.est_notional_fen for d in sells),
            "fees_fen": sum(d.est_fees_fen for d in plan.intents),
            "turnover": round(turnover(plan.intents, marked.nav_fen), 6),
            "rejected": sum(1 for d in plan.intents if d.risk == "reject"),
            "warned": sum(1 for d in plan.intents if d.risk == "warn"),
            "skipped": [{"symbol": s, "weight": round(w, 6), "reason": r} for s, w, r in plan.skipped],
            "within_band": len(plan.within_band)})
    return summary
