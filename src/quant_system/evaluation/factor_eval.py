"""Factor evaluation on rebalance rows (ARCHITECTURE §5.3, §12; ADR-009).

Forward returns follow execution (ADR-002): enter at the open of the
session after the signal (hfq-adjusted), exit at the open after the next
rebalance signal (h periods later).  Names without a bar or locked limit-up
at the entry open are not investable and are excluded (and counted).  A
name without a bar at the exit open (suspended or delisted) is valued at
its last adjusted close.  Every forward window must end inside the sample,
so an in-sample evaluation never reads out-of-sample prices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..backtest.fills import open_state
from ..domain.rules import MarketRules
from ..features.context import FactorContext
from ..features.store import FactorPanel
from ..universe.liquidity import pct_rank


@dataclass(frozen=True)
class EvaluationSpec:
    sample: str                     # "IS" | "OOS"
    start: date
    end: date
    horizons: tuple[int, ...] = (1, 2, 3, 6)
    quantiles: int = 5
    min_names: int = 30  # cross-section size needed for an IC / quantile observation


@dataclass
class ForwardReturns:
    rows: np.ndarray                 # signal rows (sorted)
    returns: dict[int, np.ndarray]   # horizon -> [R, N]; NaN = not evaluable
    investable: np.ndarray           # [R, N] entry possible at the next open
    exit_sessions: dict[int, np.ndarray]


def sample_rows(sessions: tuple[date, ...], rows: np.ndarray, spec: EvaluationSpec) -> np.ndarray:
    return np.array([t for t in rows if spec.start <= sessions[t] <= spec.end], dtype=np.int64)


def forward_returns(ctx: FactorContext, rules: MarketRules, schedule_rows: np.ndarray, signal_rows: np.ndarray,
                    universe: np.ndarray, spec: EvaluationSpec) -> ForwardReturns:
    """``schedule_rows``: every rebalance row of the calendar (for exits);
    ``signal_rows``: the evaluated rows, aligned with ``universe``."""
    market = ctx.market
    sessions = market.sessions
    last_session = max(i for i, day in enumerate(sessions) if day <= spec.end)
    adj_open = np.where(market.has_bar, market.open.astype(np.float64) * market.hfq, np.nan)
    adj_last = ctx.adj_last()
    position = {int(t): k for k, t in enumerate(schedule_rows)}
    R, N = len(signal_rows), market.shape[1]
    investable = np.zeros((R, N), dtype=bool)
    entry_price = np.full((R, N), np.nan)
    for k, t in enumerate(signal_rows):
        entry = t + 1
        if entry > last_session:
            continue
        for j in np.flatnonzero(universe[k] & market.has_bar[entry]):
            if open_state(market, rules, entry, j).lock_buy is None:
                investable[k, j] = True
        entry_price[k] = np.where(investable[k], adj_open[entry], np.nan)
    returns, exits = {}, {}
    for h in spec.horizons:
        out = np.full((R, N), np.nan)
        exit_rows = np.full(R, -1)
        for k, t in enumerate(signal_rows):
            later = position[int(t)] + h
            if later >= len(schedule_rows):
                continue
            exit_ = int(schedule_rows[later]) + 1
            if exit_ > last_session:
                continue
            exit_rows[k] = exit_
            exit_price = np.where(market.has_bar[exit_], adj_open[exit_], adj_last[exit_])
            with np.errstate(invalid="ignore", divide="ignore"):
                out[k] = exit_price / entry_price[k] - 1.0
        returns[h], exits[h] = out, exit_rows
    return ForwardReturns(np.asarray(signal_rows), returns, investable, exits)


def _corr(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return np.nan
    x, y = x - x.mean(), y - y.mean()
    denominator = math.sqrt(float((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / denominator) if denominator > 0 else np.nan


def _normal_p(t: float) -> float:
    return float(math.erfc(abs(t) / math.sqrt(2))) if np.isfinite(t) else np.nan


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    p = p_values.dropna().sort_values()
    m = len(p)
    if m == 0:
        return pd.Series(np.nan, index=p_values.index)
    q = (p * m / np.arange(1, m + 1)).iloc[::-1].cummin().iloc[::-1].clip(upper=1.0)
    return q.reindex(p_values.index)


def _ratio(series: pd.Series) -> float:
    series = series.dropna()
    return float(series.mean() / series.std()) if len(series) > 1 and series.std() > 0 else np.nan


def _t_stat(series: pd.Series) -> float:
    series = series.dropna()
    if len(series) < 2 or series.std() == 0:
        return np.nan
    return float(series.mean() / series.std() * math.sqrt(len(series)))


def evaluate_factors(panel: FactorPanel, forward: ForwardReturns, spec: EvaluationSpec,
                     periods_per_year: float) -> dict[str, pd.DataFrame]:
    """Per-row series and summary tables for every factor in ``panel``."""
    rows_index = [panel.row(t) for t in forward.rows]
    universe = panel.universe[rows_index]
    records, quantile_records = [], []
    fwd1 = forward.returns[spec.horizons[0]]
    for factor_id, values in panel.values.items():
        z = values[rows_index]
        previous_top: set[int] | None = None
        for k, t in enumerate(forward.rows):
            base = universe[k] & forward.investable[k]
            record = {"factor": factor_id, "row": int(t), "universe": int(universe[k].sum()),
                      "investable": int(base.sum())}
            for h in spec.horizons:
                fwd = forward.returns[h][k]
                valid = base & np.isfinite(z[k]) & np.isfinite(fwd)
                record[f"n_h{h}"] = int(valid.sum())
                enough = valid.sum() >= spec.min_names
                record[f"ic_h{h}"] = _corr(z[k, valid], fwd[valid]) if enough else np.nan
                record[f"rank_ic_h{h}"] = _corr(pct_rank(z[k, valid]), pct_rank(fwd[valid])) if enough else np.nan
            valid = base & np.isfinite(z[k]) & np.isfinite(fwd1[k])
            if valid.sum() >= max(spec.min_names, 2 * spec.quantiles):
                names = np.flatnonzero(valid)
                order = names[np.lexsort((names, z[k, names]))]  # ascending score, ties by symbol
                groups = np.array_split(order, spec.quantiles)
                means = [float(np.mean(fwd1[k, g])) for g in groups]
                universe_mean = float(np.mean(fwd1[k, names]))
                top = set(groups[-1].tolist())
                turnover = np.nan if previous_top is None else 1 - len(top & previous_top) / max(len(top), 1)
                previous_top = top
                quantile_records.append({"factor": factor_id, "row": int(t), **{f"q{i + 1}": m for i, m in
                                                                                   enumerate(means)},
                                         "universe_mean": universe_mean, "top_minus_bottom": means[-1] - means[0],
                                         "top_minus_universe": means[-1] - universe_mean,
                                         "top_turnover": turnover})
            records.append(record)
    series = pd.DataFrame(records)
    quantiles = pd.DataFrame(quantile_records)
    summary_rows = []
    h1 = spec.horizons[0]
    for factor_id, group in series.groupby("factor", sort=False):
        q = quantiles[quantiles["factor"] == factor_id] if not quantiles.empty else pd.DataFrame()
        row = {
            "factor": factor_id, "periods": int(group[f"ic_h{h1}"].notna().sum()),
            "ic_mean": group[f"ic_h{h1}"].mean(), "ic_ir": _ratio(group[f"ic_h{h1}"]),
            "rank_ic_mean": group[f"rank_ic_h{h1}"].mean(), "rank_ic_t": _t_stat(group[f"rank_ic_h{h1}"]),
            "rank_ic_hit": float((group[f"rank_ic_h{h1}"].dropna() > 0).mean()),
            "coverage": float((group[f"n_h{h1}"] / group["investable"].where(group["investable"] > 0)).mean()),
        }
        for h in spec.horizons[1:]:
            row[f"rank_ic_h{h}"] = group[f"rank_ic_h{h}"].mean()
        if not q.empty:
            row["top_minus_universe_ann"] = q["top_minus_universe"].mean() * periods_per_year
            row["top_minus_universe_t"] = _t_stat(q["top_minus_universe"])
            row["top_minus_bottom_ann"] = q["top_minus_bottom"].mean() * periods_per_year
            row["top_turnover"] = q["top_turnover"].mean()
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary["rank_ic_p"] = summary["rank_ic_t"].map(_normal_p)
        summary["rank_ic_q_bh"] = benjamini_hochberg(summary["rank_ic_p"])
        summary["significant"] = (summary["rank_ic_t"].abs() > 3) | (summary["rank_ic_q_bh"] < 0.05)
    correlation = factor_correlation(panel, rows_index, universe)
    excluded = 1 - forward.investable.sum() / max(int(universe.sum()), 1)
    return {"series": series, "quantiles": quantiles, "summary": summary, "correlation": correlation,
            "meta": pd.DataFrame([{"sample": spec.sample, "start": spec.start, "end": spec.end,
                                   "rows": len(forward.rows), "not_investable_share": excluded}])}


def factor_correlation(panel: FactorPanel, rows_index: list[int], universe: np.ndarray) -> pd.DataFrame:
    """Mean cross-sectional rank correlation between processed factors."""
    ids = list(panel.values)
    total = np.zeros((len(ids), len(ids)))
    counts = np.zeros((len(ids), len(ids)))
    for k, r in enumerate(rows_index):
        ranks = [pct_rank(np.where(universe[k], panel.values[f][r], np.nan)) for f in ids]
        for a in range(len(ids)):
            for b in range(a, len(ids)):
                valid = np.isfinite(ranks[a]) & np.isfinite(ranks[b])
                if valid.sum() >= 30:
                    value = _corr(ranks[a][valid], ranks[b][valid])
                    if np.isfinite(value):
                        total[a, b] += value
                        counts[a, b] += 1
    with np.errstate(invalid="ignore"):
        mean = total / counts
    mean = np.triu(mean) + np.triu(mean, 1).T
    return pd.DataFrame(mean, index=ids, columns=ids)
