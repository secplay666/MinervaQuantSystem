"""Risk and attribution report for factor portfolios (ARCHITECTURE §5.11).

For each rebalance target (the strategy's records):

* style exposures: weighted mean of style factors standardized within the
  universe (no neutralization, natural sign: +size = larger caps), portfolio
  minus the equal-weight universe benchmark;
* industry active weights (Shenwan L1) and concentration;
* low-price exposure (weight in names under 10 CNY) — minimum-lot effects;
* Brinson industry attribution of the target portfolio over each holding
  period (entry at the next open, exit at the open after the next
  rebalance; idealized: target weights, no costs).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..evaluation.factor_eval import EvaluationSpec, forward_returns
from ..features.context import FactorContext
from ..features.processing import winsorize
from ..features.registry import compute, get

STYLE_FACTORS = {
    "size": "ln_float_mcap", "value": "bp", "earnings_yield": "ep_ttm", "momentum": "mom_12_1",
    "volatility": "vol_60", "liquidity": "turn_20", "growth": "ni_yoy", "quality": "roe_ttm",
    "leverage": "leverage",
}
LOW_PRICE_YUAN = 10.0


def _standardize(raw: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(raw.shape, np.nan)
    valid = mask & np.isfinite(raw)
    if valid.sum() < 10:
        return out
    x = winsorize(raw[valid], 3.0)
    std = x.std()
    out[valid] = (x - x.mean()) / std if std > 0 else 0.0
    return out


def exposure_tables(strategy, rules) -> dict[str, pd.DataFrame]:
    research = strategy.research
    records = strategy.records
    if not records:
        return {}
    rows = np.array([r["t"] for r in records], dtype=np.int64)
    ctx = FactorContext(research)
    close = ctx.close()[rows]
    codes = research.industry_codes
    styles, industries, concentration = [], [], []
    style_raw = {name: compute(get(factor_id), ctx, rows) for name, factor_id in STYLE_FACTORS.items()}
    for k, record in enumerate(records):
        w, universe = record["weights"], record["universe"]
        bench = universe / max(universe.sum(), 1)
        row = {"session": record["session"]}
        for name, raw in style_raw.items():
            z = _standardize(raw[k], universe)
            held = (w > 0) & np.isfinite(z)
            portfolio = float((w[held] * z[held]).sum() / w[held].sum()) if held.any() else np.nan
            in_universe = universe & np.isfinite(z)
            base = float(z[in_universe].mean()) if in_universe.any() else np.nan
            row[name] = portfolio - base
        styles.append(row)
        industry = research.industry[rows[k]]
        for code in sorted(set(industry[universe]) | set(industry[w > 0])):
            label = f"{codes[code]} {research.industry_names[code]}" if code >= 0 else "unclassified"
            industries.append({"session": record["session"], "industry": label,
                               "portfolio": float(w[industry == code].sum()),
                               "benchmark": float(bench[industry == code].sum())})
        weights = np.sort(w[w > 0])[::-1]
        low = np.nan_to_num(close[k]) < LOW_PRICE_YUAN
        concentration.append({
            "session": record["session"], "names": int((w > 0).sum()), "invested": float(w.sum()),
            "top10": float(weights[:10].sum()), "effective_n": float(1 / (weights ** 2).sum()) if len(weights) else 0,
            "low_price_weight": float(w[low].sum() / max(w.sum(), 1e-12)),
            "low_price_benchmark": float(bench[low].sum()),
            **{f"diag_{key}": value for key, value in record["diagnostics"].items()
               if isinstance(value, (int, float))},
        })
    industry_frame = pd.DataFrame(industries)
    industry_frame["active"] = industry_frame["portfolio"] - industry_frame["benchmark"]
    tables = {"style_exposures": pd.DataFrame(styles), "industry_weights": industry_frame,
              "concentration": pd.DataFrame(concentration)}
    tables["brinson"] = brinson(strategy, rules, rows)
    return tables


def brinson(strategy, rules, rows: np.ndarray) -> pd.DataFrame:
    research = strategy.research
    ctx = FactorContext(research)
    sessions = research.sessions
    schedule = strategy.rows  # every rebalance row the strategy computed (exits use the next one)
    universe = np.vstack([r["universe"] for r in strategy.records])
    spec = EvaluationSpec("FULL", sessions[0], sessions[-1], horizons=(1,), min_names=1)
    forward = forward_returns(ctx, rules, schedule, rows, universe, spec).returns[1]
    out = []
    for k, record in enumerate(strategy.records):
        fwd = forward[k]
        w = np.where(np.isfinite(fwd), record["weights"], 0.0)
        b = np.where(np.isfinite(fwd) & record["universe"], 1.0, 0.0)
        if w.sum() <= 0 or b.sum() <= 0:
            continue
        w, b = w / w.sum(), b / b.sum()
        industry = research.industry[rows[k]]
        r = np.nan_to_num(fwd)
        total_b = float((b * r).sum())
        allocation = selection = 0.0
        for code in np.unique(industry[(w > 0) | (b > 0)]):
            members = industry == code
            wp, wb = w[members].sum(), b[members].sum()
            rb = float((b[members] * r[members]).sum() / wb) if wb > 0 else total_b
            rp = float((w[members] * r[members]).sum() / wp) if wp > 0 else rb
            allocation += (wp - wb) * (rb - total_b)
            selection += wp * (rp - rb)
        out.append({"session": record["session"], "portfolio": float((w * r).sum()), "benchmark": total_b,
                    "allocation": allocation, "selection": selection})
    return pd.DataFrame(out)


def render_risk_section(tables: dict[str, pd.DataFrame], periods_per_year: float = 12.0) -> str:
    if not tables:
        return ""
    styles = tables["style_exposures"].drop(columns=["session"])
    concentration = tables["concentration"]
    industry = tables["industry_weights"]
    brinson_frame = tables["brinson"]
    lines = ["", "## 风险暴露与归因", "",
             "风格暴露为组合相对股票池等权基准的平均暴露（股票池内标准化，单位：标准差）。", "",
             "| 风格 | 平均暴露 | 最小 | 最大 |", "|---|---:|---:|---:|"]
    for name in styles.columns:
        column = styles[name].astype(float)
        if column.notna().any():
            lines.append(f"| {name} | {column.mean():+.2f} | {column.min():+.2f} | {column.max():+.2f} |")
        else:
            lines.append(f"| {name} | — | — | — |")
    deviation = industry.assign(abs_active=industry["active"].abs()).groupby("session")["abs_active"].sum() / 2
    largest = industry["active"].abs().max()
    lines += ["", f"- 持股数 {concentration['names'].mean():.0f}，前 10 大合计 {concentration['top10'].mean():.1%}，"
                  f"有效持股数 {concentration['effective_n'].mean():.0f}；行业总偏离（各行业主动权重绝对值之和的一半）"
                  f"平均 {deviation.mean():.1%}、最大 {deviation.max():.1%}，单个行业的最大偏离 {largest:.1%}。",
              f"- 低价股（收盘价 < {LOW_PRICE_YUAN:.0f} 元）权重 {concentration['low_price_weight'].mean():.1%}，"
              f"股票池 {concentration['low_price_benchmark'].mean():.1%}。"]
    if "diag_lot_skipped" in concentration:
        lines.append(f"- 因一手金额过高被替补的股票平均每期 {concentration['diag_lot_skipped'].mean():.1f} 只。")
    if not brinson_frame.empty:
        active = brinson_frame["portfolio"] - brinson_frame["benchmark"]
        lines += ["", "行业归因（Brinson，目标权重、次日开盘建仓、不计费用，年化）：", "",
                  "| 项目 | 年化 |", "|---|---:|",
                  f"| 组合相对基准 | {active.mean() * periods_per_year:+.2%} |",
                  f"| 行业配置 | {brinson_frame['allocation'].mean() * periods_per_year:+.2%} |",
                  f"| 行业内选股 | {brinson_frame['selection'].mean() * periods_per_year:+.2%} |"]
    return "\n".join(lines) + "\n"
