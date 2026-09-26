"""Markdown report for a backtest run."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .metrics import yearly_returns

LIMITATIONS = [
    "成交价为 T+1 开盘价加滑点；T+1 当日完整 K 线只用于限制成交（锁板、参与率上限），不参与选股和下单股数计算（ADR-002）。",
    "除权按后复权步长调整持仓股数，不足 1 股的部分按参考价折现：未扣红利税，配股视为零成本认购，送转股视为除权日即可卖出，这些都会让结果偏乐观。",
    "上交所、北交所的 ST 历史由交易所公告推导，并用名称和价格证据校正（ADR-004），不是交易所直接公布的数据。",
    "退市持仓在退市日按最后收盘价结算；报告同时给出按 0 元结算的影响。",
    "沪深 300、中证 500 为价格指数，不含分红；等权全收益基准不计费用，并在调仓日收盘理想化再平衡。",
]


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return ""


def _num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def render_report(summary: dict[str, Any], values: dict[str, pd.Series], result) -> str:
    config = result.config
    lines = [
        f"# 回测报告：{config.name}",
        "",
        f"- 运行编号：`{summary['run_id']}`",
        f"- 区间：{summary['performance']['策略'].get('start')} 至 {summary['performance']['策略'].get('end')}",
        f"- 初始资金：{config.initial_capital_fen / 100:,.0f} 元；佣金 {float(config.commission_rate):.4%}（最低 "
        f"{config.commission_min_fen / 100:.0f} 元）；滑点 {float(config.slippage) * 10000:.1f}bp；"
        f"单日参与率上限 {config.max_participation:.0%}",
        f"- 数据版本：`{summary['data']['data_version']}`；输入指纹：`{summary['data']['fingerprint'][:16]}`",
        f"- 代码版本：`{summary['code_version'].get('git_sha')}`（工作区有未提交修改：{summary['code_version'].get('dirty')}）",
        "",
        "## 绩效",
        "",
        "| 序列 | 累计收益 | 年化收益 | 年化波动 | 夏普 | Sortino | 最大回撤 | 回撤区间 | Calmar | 月胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for name, stats in summary["performance"].items():
        window = f"{stats.get('max_drawdown_peak')} → {stats.get('max_drawdown_trough')}"
        lines.append(
            f"| {name} | {_pct(stats.get('total_return'))} | {_pct(stats.get('cagr'))} | "
            f"{_pct(stats.get('volatility'))} | {_num(stats.get('sharpe'))} | {_num(stats.get('sortino'))} | "
            f"{_pct(stats.get('max_drawdown'))} | {window} | {_num(stats.get('calmar'))} | "
            f"{_pct(stats.get('monthly_win_rate'))} |"
        )
    lines.extend(["", "## 相对基准", "", "| 基准 | 年化超额 | 跟踪误差 | 信息比率 |", "|---|---:|---:|---:|"])
    for name, stats in summary["relative"].items():
        lines.append(f"| {name} | {_pct(stats.get('excess_cagr'))} | {_pct(stats.get('tracking_error'))} | "
                     f"{_num(stats.get('information_ratio'))} |")
    yearly = pd.DataFrame({name: yearly_returns(series) for name, series in values.items()})
    lines.extend(["", "## 分年度收益", "", "| 年份 | " + " | ".join(yearly.columns) + " |",
                  "|---|" + "---:|" * len(yearly.columns)])
    for year, row in yearly.iterrows():
        lines.append(f"| {year} | " + " | ".join(_pct(value) for value in row) + " |")
    trading = summary["trading"]
    lines.extend([
        "",
        "## 交易与执行",
        "",
        f"- 年化单边换手率：{_num(trading.get('annual_turnover'))} 倍；年化费用拖累：{_pct(trading.get('annual_cost_drag'))}；"
        f"累计费用 {trading.get('fees_cny', 0):,.0f} 元",
        f"- 调仓信号 {result.counters.get('rebalance_signals', 0)} 次，执行 {result.counters.get('rebalances_executed', 0)} 次；"
        f"订单 {result.counters.get('orders', 0)} 笔，成交 {result.counters.get('fills', 0)} 笔",
        f"- 除权调整 {result.counters.get('corporate_actions', 0)} 次；退市结算 {result.counters.get('delisting_settlements', 0)} 次"
        f"（按 0 元结算的影响：{summary['delisting']['zero_settlement_impact_cny']:,.0f} 元）",
        "",
        "| 计数项 | 次数 |",
        "|---|---:|",
    ])
    for key, value in result.counters.items():
        if key.startswith(("rejected_", "reduced_", "flag_", "skipped_", "buy_retries")):
            lines.append(f"| {key} | {value} |")
    if not result.skipped.empty:
        skipped = result.skipped.groupby("reason")["weight"].agg(["count", "sum"])
        lines.extend(["", "因整手或缺少参考价而未买入的目标：", ""])
        for reason, row in skipped.iterrows():
            lines.append(f"- {reason}：{int(row['count'])} 次，合计目标权重 {row['sum']:.2%}"
                         f"（平均每次调仓 {row['sum'] / max(result.counters.get('rebalances_executed', 1), 1):.2%}）")
    lines.extend(["", "## 会计与一致性检查", ""])
    for key, value in result.checks.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## 已知局限", ""])
    lines.extend(f"- {item}" for item in LIMITATIONS)
    return "\n".join(lines) + "\n"
