"""Order intents from a target portfolio (ADR-008 §5, design §2.2).

Quantities come from ``portfolio.sizing.size_orders`` with the backtest's
sizing policy, at T's close (the backtest sizes at T+1's pre-open reference
price, which differs only on an ex-date: flagged by R3).  Every intent gets
its pre-trade checks; the worst decision becomes its risk level, and a
reject makes it ``rejected_by_risk`` (not approvable without an override).

Checks (pass | warn | reject):
  R1  closed at the limit in the trade's direction (may not fill tomorrow)   warn
  R2  suspended today or announced for tomorrow                  buy reject / sell warn
  R3  ex-rights or ex-dividend tomorrow (quantity may need adjusting)          warn
  R4  quantity above the participation cap of the 20-day average volume       warn
  R5  post-trade weight above the single-name limit              1.25x warn / 2x reject
      post-trade industry weight above the benchmark + deviation limit (buys in that industry)
                                                                  1.25x warn / 2x reject
      an industry underweight by more than 1.25x the limit (run level)       warn
  R6  buys plus fees above cash plus sell proceeds (run level)        reject all buys
  R7  buying a risk-warning or delisting-period security (or one turning so)   reject
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from ..backtest.fills import mark_price_fen, reference_price_fen
from ..backtest.market_data import RISK_NAMES, MarketData
from ..domain.entities import TargetPortfolio
from ..domain.fees import FeeSchedule
from ..domain.rules import MarketRules
from ..portfolio.sizing import SizingPolicy, size_orders
from .accounts import MarkedAccount
from .reference import FrameReference

SEVERITY = {"pass": 0, "warn": 1, "reject": 2}
ADV_WINDOW = 20
R5_WARN, R5_REJECT = 1.25, 2.0


@dataclass(frozen=True)
class Check:
    rule_id: str
    decision: str
    message: str
    actual: str | None = None
    limit: str | None = None


@dataclass
class IntentDraft:
    seq: int
    symbol: str
    side: str
    qty: int
    rank: int | None
    reason: str
    ref_price_fen: int
    limit_up_fen: int | None
    limit_down_fen: int | None
    est_notional_fen: int
    est_fees_fen: int
    checks: list[Check] = field(default_factory=list)

    @property
    def risk(self) -> str:
        return max((c.decision for c in self.checks), key=SEVERITY.__getitem__, default="pass")


@dataclass(frozen=True)
class IndustryLimits:
    """The construction's industry bounds: benchmark weights (name shares of
    the eligible universe, Shenwan L1) and the deviation it allowed."""
    of: dict[str, str]  # symbol -> industry name
    bench: dict[str, float]
    delta: float


def industry_check(label: str, weight: float, limits: IndustryLimits) -> Check | None:
    """R5 on one industry's post-trade weight, for the buys in it."""
    bench = limits.bench.get(label, 0.0)
    active = weight - bench
    ceiling = f"{bench + limits.delta:.4f}"
    if active > limits.delta * R5_REJECT:
        return Check("R5", "reject", f"买入后{label}行业权重远超基准加偏离上限", f"{weight:.4f}", ceiling)
    if active > limits.delta * R5_WARN:
        return Check("R5", "warn", f"买入后{label}行业权重超过基准加偏离上限", f"{weight:.4f}", ceiling)
    return None


def industry_values(quantities: dict[str, int], prices_fen: dict[str, int], of: dict[str, str]) -> dict[str, int]:
    values: dict[str, int] = {}
    for symbol, qty in quantities.items():
        if qty > 0:
            label = of.get(symbol, "未分类")
            values[label] = values.get(label, 0) + qty * int(prices_fen.get(symbol) or 0)
    return values


@dataclass
class IntentPlan:
    intents: list[IntentDraft]
    run_checks: list[Check]
    target_qty: dict[str, int]
    skipped: list[tuple[str, float, str]]
    within_band: list[str]
    limits: dict = field(default_factory=dict)  # what quantity_checks needs to re-check a modified intent


def quantity_checks(side: str, qty: int, price_fen: int, held: int, nav_fen: int, volume_cap: float | None,
                    max_participation: float, max_weight: float | None) -> list[Check]:
    """The quantity-dependent checks R4 and R5 of one intent."""
    checks = []
    if volume_cap is not None and qty > volume_cap:
        checks.append(Check("R4", "warn", f"数量超过 {ADV_WINDOW} 日均量的 {max_participation:.0%}", str(qty),
                            str(int(volume_cap))))
    if side == "buy" and max_weight and nav_fen:
        after = (held + qty) * price_fen / nav_fen
        if after > max_weight * R5_REJECT:
            checks.append(Check("R5", "reject", "买入后单股权重远超上限", f"{after:.4f}", f"{max_weight:.4f}"))
        elif after > max_weight * R5_WARN:
            checks.append(Check("R5", "warn", "取整后单股权重超过上限", f"{after:.4f}", f"{max_weight:.4f}"))
    return checks


def cash_check(buys_fen: int, available_fen: int) -> Check:
    """R6: buys plus fees against cash plus sell proceeds (run level)."""
    if buys_fen > available_fen:
        return Check("R6", "reject", "买入金额加费用超过可用资金", str(buys_fen), str(available_fen))
    return Check("R6", "pass", "资金充足", str(buys_fen), str(available_fen))


def _risk_name(market: MarketData, i: int, j: int) -> str:
    return RISK_NAMES.get(int(market.risk[i, j]), "normal") if market.risk_known[j] else "normal"


def next_session_limits(market: MarketData, rules: MarketRules, i: int, j: int, next_day: date,
                        risk_next: str | None) -> tuple[int | None, int | None]:
    """Price band of the next session around today's close (None inside IPO no-limit windows)."""
    reference = mark_price_fen(market, i, j)
    if reference is None:
        return None, None
    list_index = int(market.list_index[j])
    since = i + 1 - list_index if list_index >= 0 else None
    board = str(market.board[j])
    if rules.is_no_limit_session(board, market.list_date[j], since):
        return None, None
    risk = risk_next or _risk_name(market, i, j)
    ratio = rules.symbol_limit_ratio(str(market.symbols[j]), next_day) or rules.limit_ratio(board, risk, next_day)
    return rules.limit_prices(reference, ratio)


def closed_at_limit(market: MarketData, rules: MarketRules, i: int, j: int) -> str | None:
    """'up' / 'down' when today's close sits on the band, else None."""
    if not market.has_bar[i, j]:
        return None
    reference = reference_price_fen(market, i, j)
    list_index = int(market.list_index[j])
    since = i - list_index if list_index >= 0 else None
    board = str(market.board[j])
    if reference is None or rules.is_no_limit_session(board, market.list_date[j], since):
        return None
    day = market.sessions[i]
    ratio = rules.symbol_limit_ratio(str(market.symbols[j]), day) or rules.limit_ratio(
        board, _risk_name(market, i, j), day)
    up, down = rules.limit_prices(reference, ratio)
    close = int(market.close[i, j])
    return "up" if close >= up else "down" if close <= down else None


def average_volume(market: MarketData, i: int, j: int, window: int = ADV_WINDOW) -> float:
    start = max(0, i - window + 1)
    has = market.has_bar[start:i + 1, j]
    volume = market.volume[start:i + 1, j][has]
    return float(volume.mean()) if volume.size else 0.0


def plan_intents(target: TargetPortfolio, marked: MarkedAccount, market: MarketData, rules: MarketRules,
                 sizing: SizingPolicy, fees: FeeSchedule, max_participation: float, max_weight: float | None,
                 i: int, next_day: date, reference: FrameReference,
                 industry: IndustryLimits | None = None) -> IntentPlan:
    columns = {}
    for symbol in sorted(set(target.weights) | set(marked.quantities)):
        columns[symbol] = market.symbol_index(symbol)
    tradable = {s: w for s, w in target.weights.items()
                if market.delist_date[columns[s]] is None or market.delist_date[columns[s]] > next_day}
    prices = {s: mark_price_fen(market, i, j) for s, j in columns.items()}
    lot_rules = {s: rules.lot_rule(str(market.board[j])) for s, j in columns.items()}
    ranks = {s: int(target.explanations.get(s, {}).get("rank", 0)) for s in tradable}
    sized = size_orders(tradable, ranks, dict(marked.quantities), prices, marked.nav_fen, lot_rules, sizing)
    investable = marked.nav_fen * (1 - sizing.cash_buffer)
    target_qty = {s: int(w * investable // prices[s]) if prices[s] else 0 for s, w in sorted(tradable.items())}

    ex_next, suspended_next = reference.ex_dates(next_day), reference.suspended(next_day)
    risk_next = reference.risk_starting(next_day)
    drafts: list[IntentDraft] = []
    volume_caps: dict[str, int | None] = {}
    for seq, order in enumerate(sized.orders, start=1):
        j = columns[order.symbol]
        price = int(prices[order.symbol] or 0)
        notional = order.quantity * price
        exchange = str(market.exchange[j])
        up, down = next_session_limits(market, rules, i, j, next_day, risk_next.get(order.symbol))
        draft = IntentDraft(seq, order.symbol, order.side, order.quantity, order.rank or None, order.reason, price,
                            up, down, notional, fees.fees(order.side, exchange, notional, next_day).total_fen)
        buy = order.side == "buy"
        at_limit = closed_at_limit(market, rules, i, j)
        if (buy and at_limit == "up") or (not buy and at_limit == "down"):
            draft.checks.append(Check("R1", "warn", "今日收盘涨停，次日可能买不进" if buy else "今日收盘跌停，次日可能卖不出",
                                      at_limit))
        if not market.has_bar[i, j] or order.symbol in suspended_next:
            when = "今日停牌" if not market.has_bar[i, j] else "已公告次日停牌"
            draft.checks.append(Check("R2", "reject" if buy else "warn", f"{when}，{'不能买入' if buy else '可能无法卖出'}"))
        if order.symbol in ex_next:
            draft.checks.append(Check("R3", "warn", "次日除权除息，按除权价重新核对数量和价格"))
        cap = average_volume(market, i, j) * max_participation
        volume_caps[order.symbol] = int(cap) if math.isfinite(cap) else None
        draft.checks += quantity_checks(order.side, order.quantity, price, marked.quantities.get(order.symbol, 0),
                                        marked.nav_fen, cap, max_participation, max_weight)
        risky = _risk_name(market, i, j) != "normal" or order.symbol in risk_next
        if buy and risky:
            draft.checks.append(Check("R7", "reject", "风险警示或退市整理期证券，不买入",
                                      risk_next.get(order.symbol) or _risk_name(market, i, j)))
        drafts.append(draft)

    run_checks: list[Check] = []
    industry_limits = None
    if industry is not None and marked.nav_fen > 0:
        run_checks, industry_limits = _industry_checks(drafts, marked, prices, industry)

    buys = sum(d.est_notional_fen + d.est_fees_fen for d in drafts if d.side == "buy")
    proceeds = sum(d.est_notional_fen - d.est_fees_fen for d in drafts if d.side == "sell")
    available = marked.cash_fen + proceeds
    check = cash_check(buys, available)
    if check.decision == "reject":
        for draft in drafts:
            if draft.side == "buy":
                draft.checks.append(check)
    limits = {"max_participation": max_participation, "max_weight": max_weight, "volume_cap": volume_caps}
    if industry_limits is not None:
        limits["industry"] = industry_limits
    return IntentPlan(drafts, [check, *run_checks], target_qty, sized.skipped, sized.within_band, limits)


def _industry_checks(drafts: list[IntentDraft], marked: MarkedAccount, prices: dict[str, int | None],
                     industry: IndustryLimits) -> tuple[list[Check], dict]:
    """R5 industry checks on the holdings after every intent not already
    rejected, and what a modify needs to re-check them (``base_fen``: the
    value of holdings without an intent, per industry)."""
    after = dict(marked.quantities)
    for d in drafts:
        if d.risk != "reject":
            after[d.symbol] = after.get(d.symbol, 0) + (d.qty if d.side == "buy" else -d.qty)
    price_fen = {s: int(p or 0) for s, p in prices.items()}
    weights = {label: value / marked.nav_fen for label, value in industry_values(after, price_fen, industry.of).items()}
    for d in drafts:
        if d.side == "buy":
            label = industry.of.get(d.symbol, "未分类")
            check = industry_check(label, weights.get(label, 0.0), industry)
            if check is not None:
                d.checks.append(check)
    under = sorted(label for label, bench in industry.bench.items()
                   if bench - weights.get(label, 0.0) > industry.delta * R5_WARN)
    run_checks = [Check("R5", "warn", f"行业低配超过偏离上限：{'、'.join(under)}", str(len(under)),
                        f"{industry.delta:.4f}")] if under else []
    traded = {d.symbol for d in drafts}
    base = industry_values({s: q for s, q in marked.quantities.items() if s not in traded}, price_fen, industry.of)
    symbols = traded | set(marked.quantities)
    return run_checks, {"delta": industry.delta, "bench": industry.bench, "base_fen": base,
                        "of": {s: industry.of.get(s, "未分类") for s in sorted(symbols)}}


def worst(checks: list[Check]) -> str:
    return max((c.decision for c in checks), key=SEVERITY.__getitem__, default="pass")


def turnover(drafts: list[IntentDraft], nav_fen: int) -> float:
    traded = sum(d.est_notional_fen for d in drafts)
    return float(traded / nav_fen) if nav_fen else float(np.nan)
