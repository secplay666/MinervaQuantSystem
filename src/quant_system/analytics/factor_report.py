"""Markdown report for a factor evaluation run (Chinese, like the backtest report)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..features.registry import get

FAMILY_NAMES = {"value": "价值", "quality": "质量", "growth": "成长", "momentum": "动量", "volatility": "波动",
                "liquidity": "流动性", "size": "规模", "technical": "技术"}


def _fmt(value: Any, digits: int = 3, pct: bool = False) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    if isinstance(value, (bool, np.bool_)):
        return "是" if value else ""
    if pct:
        return f"{value:.1%}"
    return f"{value:.{digits}f}"


def render_factor_report(manifest: dict[str, Any], results: dict[str, pd.DataFrame]) -> str:
    summary = results["summary"].copy()
    meta = results["meta"].iloc[0]
    config = manifest["config"]
    lines = [
        f"# 因子评估：{config['name']}",
        "",
        f"- 样本：**{manifest['sample']}** {manifest['window'][0]} 至 {manifest['window'][1]}，"
        f"{int(meta['rows'])} 个调仓期（每期的远期窗口都在样本截止日以内）",
        f"- 实验：{manifest['experiment_id']}（{config['experiment']}）；含本次共登记 "
        f"{manifest['trials_in_experiment']} 次样本内运行，结论需按试验次数打折",
        f"- 数据版本 `{manifest['data_version']}`，因子代码哈希 `{manifest['features_hash'][:12]}`，"
        f"运行编号 `{manifest['run_id']}`",
        f"- 远期收益：信号次日开盘买入（后复权），下一次调仓后首个开盘卖出；T+1 停牌或开盘涨停锁板的股票剔除"
        f"（占股票池 {_fmt(meta['not_investable_share'], pct=True)}）",
        "- 因子值：股票池内 MAD 去极值 → 行业与对数流通市值中性化 → 标准化，并统一为“越大越好”",
        "",
        "## 汇总",
        "",
        "| 因子 | 类别 | 期数 | Rank IC | t | 胜率 | IC IR | 2/3/6 期 Rank IC | 顶部超额（年化） | t | "
        "多空（年化） | 顶部换手 | 覆盖 | BH q | 显著 |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    if not summary.empty:
        summary["family"] = summary["factor"].map(lambda f: get(f).family)
        summary = summary.sort_values(["family", "factor"], key=lambda s: s.map(
            lambda v: list(FAMILY_NAMES).index(v) if v in FAMILY_NAMES else v) if s.name == "family" else s)
        for row in summary.itertuples(index=False):
            decay = "/".join(_fmt(getattr(row, f"rank_ic_h{h}", np.nan)) for h in (2, 3, 6))
            lines.append(
                f"| {row.factor} | {FAMILY_NAMES.get(row.family, row.family)} | {row.periods} | "
                f"{_fmt(row.rank_ic_mean)} | {_fmt(row.rank_ic_t, 2)} | {_fmt(row.rank_ic_hit, pct=True)} | "
                f"{_fmt(row.ic_ir, 2)} | {decay} | {_fmt(getattr(row, 'top_minus_universe_ann', np.nan), pct=True)} | "
                f"{_fmt(getattr(row, 'top_minus_universe_t', np.nan), 2)} | "
                f"{_fmt(getattr(row, 'top_minus_bottom_ann', np.nan), pct=True)} | "
                f"{_fmt(getattr(row, 'top_turnover', np.nan), pct=True)} | {_fmt(row.coverage, pct=True)} | "
                f"{_fmt(row.rank_ic_q_bh)} | {_fmt(bool(row.significant))} |")
    correlation = results["correlation"]
    pairs = []
    ids = list(correlation.index)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            value = correlation.iloc[a, b]
            if np.isfinite(value) and abs(value) >= 0.6:
                pairs.append((abs(value), ids[a], ids[b], value))
    lines += ["", "## 高相关因子对（|平均截面秩相关| ≥ 0.6）", ""]
    if pairs:
        lines += ["| 因子 A | 因子 B | 相关系数 |", "|---|---|---:|"]
        lines += [f"| {a} | {b} | {v:.2f} |" for _, a, b, v in sorted(pairs, reverse=True)]
    else:
        lines.append("无。")
    universe = results.get("universe")
    if universe is not None and not universe.empty:
        lines += ["", "## 股票池", "",
                  f"每期规模 {int(universe['universe'].min())}–{int(universe['universe'].max())} 只；"
                  f"筛选前（有行情、板块内）平均 {universe['after_has_bar'].mean():.0f} 只，"
                  f"满足全部条件平均 {universe['after_share_data'].mean():.0f} 只。"]
    lines += ["", "## 说明", "",
              "- 显著性：|t| > 3，或 Benjamini–Hochberg 校正后 q < 0.05（按本次评估的因子数校正）。",
              "- 因子方向是事前假设；方向为负的因子已乘以 −1，表中 IC 为负表示假设方向错误。",
              "- 顶部超额 = 得分最高五分之一的等权收益 − 股票池（可投资部分）等权收益，未扣费用。", ""]
    return "\n".join(lines)
