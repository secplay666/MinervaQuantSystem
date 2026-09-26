from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd


def returns_from_values(values: pd.Series) -> pd.Series:
    return values.astype(float).pct_change().dropna()


def max_drawdown(values: pd.Series) -> dict[str, Any]:
    series = values.astype(float)
    peaks = series.cummax()
    drawdown = series / peaks - 1
    trough = drawdown.idxmin()
    peak = series.loc[:trough].idxmax()
    recovered = series.loc[trough:][series.loc[trough:] >= series.loc[peak]]
    return {
        "max_drawdown": float(drawdown.min()),
        "peak": peak,
        "trough": trough,
        "recovery": recovered.index[0] if len(recovered) else None,
    }


def performance(values: pd.Series, periods_per_year: int = 244, risk_free_rate: float = 0.0) -> dict[str, Any]:
    """Metrics for a value series indexed by session date."""
    values = values.dropna().astype(float)
    if len(values) < 2:
        return {}
    returns = returns_from_values(values)
    years = len(returns) / periods_per_year
    total = values.iloc[-1] / values.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else float("nan")
    daily_rf = risk_free_rate / periods_per_year
    excess = returns - daily_rf
    volatility = returns.std(ddof=1) * math.sqrt(periods_per_year)
    downside = returns[returns < daily_rf] - daily_rf
    downside_dev = math.sqrt((downside ** 2).sum() / len(returns)) * math.sqrt(periods_per_year)
    drawdown = max_drawdown(values)
    monthly = values.groupby([values.index.map(lambda d: (d.year, d.month))]).last()
    monthly_returns = monthly.pct_change().dropna()
    return {
        "start": values.index[0],
        "end": values.index[-1],
        "sessions": int(len(values)),
        "total_return": float(total),
        "cagr": float(cagr),
        "volatility": float(volatility),
        "sharpe": float(excess.mean() / returns.std(ddof=1) * math.sqrt(periods_per_year)) if returns.std() > 0 else float("nan"),
        "sortino": float(excess.mean() * periods_per_year / downside_dev) if downside_dev > 0 else float("nan"),
        "max_drawdown": drawdown["max_drawdown"],
        "max_drawdown_peak": drawdown["peak"],
        "max_drawdown_trough": drawdown["trough"],
        "max_drawdown_recovery": drawdown["recovery"],
        "calmar": float(cagr / abs(drawdown["max_drawdown"])) if drawdown["max_drawdown"] < 0 else float("nan"),
        "monthly_win_rate": float((monthly_returns > 0).mean()) if len(monthly_returns) else float("nan"),
    }


def relative(strategy: pd.Series, benchmark: pd.Series, periods_per_year: int = 244) -> dict[str, float]:
    aligned = pd.concat([strategy, benchmark], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return {}
    returns = aligned.pct_change().dropna()
    active = returns.iloc[:, 0] - returns.iloc[:, 1]
    tracking = active.std(ddof=1) * math.sqrt(periods_per_year)
    years = len(returns) / periods_per_year
    growth = aligned.iloc[-1] / aligned.iloc[0]
    excess_cagr = growth.iloc[0] ** (1 / years) - growth.iloc[1] ** (1 / years)
    return {
        "excess_cagr": float(excess_cagr),
        "tracking_error": float(tracking),
        "information_ratio": float(active.mean() * periods_per_year / tracking) if tracking > 0 else float("nan"),
    }


def yearly_returns(values: pd.Series) -> pd.Series:
    values = values.dropna().astype(float)
    year_end = values.groupby(values.index.map(lambda d: d.year)).last()
    first = values.iloc[0]
    previous = year_end.shift(1)
    previous.iloc[0] = first
    return year_end / previous - 1


def trading_stats(nav: pd.DataFrame, fills: pd.DataFrame, periods_per_year: int = 244) -> dict[str, float]:
    if nav.empty:
        return {}
    mean_nav = nav["nav_fen"].mean()
    years = max(len(nav) - 1, 1) / periods_per_year
    traded = float(fills["notional_fen"].sum()) if not fills.empty else 0.0
    fees = float(nav["fees_cum_fen"].iloc[-1])
    return {
        "annual_turnover": traded / mean_nav / years / 2,  # one-sided: (buys + sells) / 2
        "annual_cost_drag": fees / mean_nav / years,
        "fees_cny": fees / 100,
        "traded_cny": traded / 100,
    }


def to_series(frame: pd.DataFrame, column: str) -> pd.Series:
    series = frame.set_index("session")[column]
    series.index = [value if isinstance(value, date) else pd.Timestamp(value).date() for value in series.index]
    return series.astype(float)

