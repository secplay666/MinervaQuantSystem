"""Holdings monitoring, run every session (design §2 step 3).

Alerts become events of the account: risk-warning changes, suspensions,
ex-dates, limit-down closes and large drops, approaching delistings, newly
published periodic reports, and drift from the latest target portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..backtest.fills import reference_price_fen
from ..backtest.market_data import RISK_DELISTING, RISK_NAMES, MarketData
from ..domain.rules import MarketRules
from .accounts import MarkedAccount
from .intents import closed_at_limit
from .reference import FrameReference

LARGE_DROP = -0.07
DELIST_WARNING_DAYS = 30
DRIFT_ALERT = 0.10
PERIODS = {3: "一季报", 6: "中报", 9: "三季报", 12: "年报"}


def period_label(period: date) -> str:
    return f"{period.year} 年{PERIODS.get(period.month, f'{period:%m-%d} 报告')}"


@dataclass(frozen=True)
class Alert:
    rule: str
    level: str  # info | warning | critical
    title: str
    body: str
    symbol: str | None = None
    hint: str | None = None


def monitor_holdings(marked: MarkedAccount, market: MarketData, rules: MarketRules, i: int, next_day: date | None,
                     reference: FrameReference, target_weights: dict[str, float] | None,
                     manual: bool) -> list[Alert]:
    alerts: list[Alert] = []
    ex_next = reference.ex_dates(next_day) if next_day else set()
    suspended_next = reference.suspended(next_day) if next_day else set()
    risk_next = reference.risk_starting(next_day) if next_day else {}
    day = market.sessions[i]
    reports = reference.reports_published(market.sessions[i - 1] if i > 0 else None, day)
    for symbol in sorted(marked.quantities):
        j = market.symbol_index(symbol)
        known = bool(market.risk_known[j])
        now = int(market.risk[i, j]) if known else 0
        before = int(market.risk[i - 1, j]) if known and i > 0 else now
        if now != before:
            alerts.append(Alert("M1", "warning", f"{symbol} 风险警示状态变化",
                                f"{RISK_NAMES.get(before)} → {RISK_NAMES.get(now)}（{day}）", symbol))
        if symbol in risk_next:
            alerts.append(Alert("M2", "warning", f"{symbol} 次日起实施风险警示", f"状态：{risk_next[symbol]}", symbol))
        if not market.has_bar[i, j]:
            alerts.append(Alert("M3", "info", f"{symbol} 今日停牌", f"{day} 无行情", symbol))
        if symbol in suspended_next:
            alerts.append(Alert("M4", "warning", f"{symbol} 次日停牌", f"已公告 {next_day} 停牌", symbol))
        if symbol in ex_next:
            alerts.append(Alert("M5", "info", f"{symbol} 次日除权除息", f"除权日 {next_day}", symbol,
                                "除权后请核对持仓数量" if manual else None))
        if market.has_bar[i, j]:
            if closed_at_limit(market, rules, i, j) == "down":
                alerts.append(Alert("M6", "warning", f"{symbol} 今日跌停", f"{day} 收盘跌停", symbol))
            else:
                reference_fen = reference_price_fen(market, i, j)
                if reference_fen:
                    change = int(market.close[i, j]) / reference_fen - 1
                    if change <= LARGE_DROP:
                        alerts.append(Alert("M7", "warning", f"{symbol} 今日大跌 {change:.1%}", f"{day} 收盘", symbol))
        if symbol in reports:
            labels = "、".join(period_label(p) for p in reports[symbol])
            alerts.append(Alert("M10", "info", f"{symbol} 发布 {labels}", "财务数据从下一个交易日起用于决策",
                                symbol, "在个股页查看财务"))
        delist = market.delist_date[j]
        if now == RISK_DELISTING:
            alerts.append(Alert("M8", "critical", f"{symbol} 处于退市整理期", "尽快处置", symbol))
        elif delist is not None and day < delist <= day + timedelta(days=DELIST_WARNING_DAYS):
            alerts.append(Alert("M8", "critical", f"{symbol} 将于 {delist} 退市", f"距今 {(delist - day).days} 天",
                                symbol, "退市后只能按最后价格结算"))
    if target_weights:
        current = marked.weights
        drift = 0.5 * sum(abs(current.get(s, 0.0) - target_weights.get(s, 0.0))
                          for s in set(current) | set(target_weights))
        if drift > DRIFT_ALERT:
            alerts.append(Alert("M9", "info", f"组合偏离最新目标 {drift:.1%}", "按权重计算的单边偏离",
                                hint="下一个调仓日会重新生成交易清单"))
    return alerts
