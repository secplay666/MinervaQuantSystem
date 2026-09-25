from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

Severity = Literal["warning", "blocking"]


@dataclass(frozen=True)
class QualityIssue:
    dataset: str
    rule: str
    severity: Severity
    message: str
    affected_rows: int = 0
    symbol: str | None = None
    trade_date: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def has_blocking(issues: list[QualityIssue]) -> bool:
    return any(issue.severity == "blocking" for issue in issues)


def validate_security_master(
    frame: pd.DataFrame,
    previous: pd.DataFrame | None,
    max_shrink_ratio: float,
) -> list[QualityIssue]:
    dataset = "security_master"
    if frame.empty:
        return [QualityIssue(dataset, "not_empty", "blocking", "证券主数据为空")]
    issues: list[QualityIssue] = []
    duplicate_count = int(frame.duplicated("symbol").sum())
    if duplicate_count:
        issues.append(
            QualityIssue(dataset, "unique_symbol", "blocking", "证券代码重复", duplicate_count)
        )
    unknown = int((frame["exchange"] == "UNKNOWN").sum())
    if unknown:
        issues.append(
            QualityIssue(dataset, "known_exchange", "warning", "部分证券代码无法推断交易所", unknown)
        )
    live = frame["status"] != "delisted"
    missing_list_date = int((live & frame["list_date"].isna()).sum())
    if missing_list_date:
        issues.append(
            QualityIssue(
                dataset, "list_date_present", "warning", "在市证券缺少上市日期", missing_list_date
            )
        )
    if previous is not None and not previous.empty:
        previous_live = int((previous["status"] != "delisted").sum())
        current_live = int(live.sum())
        if previous_live and current_live < previous_live * (1 - max_shrink_ratio):
            issues.append(
                QualityIssue(
                    dataset,
                    "listing_not_shrunk",
                    "blocking",
                    f"在市证券数量从 {previous_live} 降至 {current_live}，疑似上游列表不完整",
                    previous_live - current_live,
                )
            )
    return issues


def validate_calendar(
    frame: pd.DataFrame, today: date, calendar_max: date | None
) -> list[QualityIssue]:
    dataset = "trading_calendar"
    if frame.empty:
        return [QualityIssue(dataset, "not_empty", "blocking", "交易日历为空")]
    issues: list[QualityIssue] = []
    duplicate_count = int(frame.duplicated(["exchange", "trade_date"]).sum())
    if duplicate_count:
        issues.append(
            QualityIssue(dataset, "unique_exchange_date", "blocking", "交易日历存在重复日期", duplicate_count)
        )
    if calendar_max is None or calendar_max < today:
        issues.append(
            QualityIssue(
                dataset,
                "covers_today",
                "blocking",
                f"上游交易日历只覆盖到 {calendar_max}，无法判断 {today} 是否为交易日",
            )
        )
    return issues


def validate_bars(frame: pd.DataFrame, dataset: str, symbol: str) -> list[QualityIssue]:
    """Row-level integrity rules; any blocking issue prevents the write."""
    issues: list[QualityIssue] = []
    if frame.empty:
        return [QualityIssue(dataset, "not_empty", "blocking", f"{symbol} 没有返回行情数据", symbol=symbol)]
    checks: list[tuple[str, Severity, str, pd.Series]] = []
    checks.append(
        ("unique_symbol_date", "blocking", "存在重复交易日", frame.duplicated(["symbol", "trade_date"]))
    )
    checks.append(
        ("key_not_null", "blocking", "存在空主键", frame[["symbol", "trade_date"]].isna().any(axis=1))
    )
    price_columns = ["open", "high", "low", "close"]
    checks.append(
        ("positive_prices", "blocking", "存在非正价格", (frame[price_columns] <= 0).any(axis=1))
    )
    checks.append(
        (
            "valid_ohlc_relationship",
            "blocking",
            "OHLC价格关系不合法",
            (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
            | (frame["low"] > frame[["open", "close", "high"]].min(axis=1)),
        )
    )
    volume_column = "volume_shares" if "volume_shares" in frame.columns else "volume"
    checks.append(("non_negative_volume", "blocking", "存在负成交量", frame[volume_column] < 0))
    checks.append(("price_not_null", "blocking", "存在空价格", frame[price_columns].isna().any(axis=1)))
    for rule, severity, text, mask in checks:
        count = int(mask.fillna(False).sum())
        if count:
            issues.append(
                QualityIssue(dataset, rule, severity, f"{symbol} {text}", count, symbol=symbol)
            )
    return issues


def volume_unit_ratio(frame: pd.DataFrame) -> pd.Series:
    """turnover / (volume * close): ~1 when volume is in shares and amount in CNY."""
    volume = pd.to_numeric(frame["volume_shares"], errors="coerce").astype("float64")
    valid = (volume > 0) & (frame["close"] > 0) & (frame["turnover_cny"] > 0)
    return (frame["turnover_cny"] / (volume * frame["close"])).where(valid)


def validate_volume_units(frame: pd.DataFrame, symbol: str) -> list[QualityIssue]:
    ratio = volume_unit_ratio(frame).dropna()
    if ratio.empty:
        return []
    median = float(ratio.median())
    if not 0.5 <= median <= 2.0:
        return [
            QualityIssue(
                "daily_bars",
                "volume_unit_consistency",
                "blocking",
                f"{symbol} 成交额/(成交量*收盘价) 中位数为 {median:.4g}，成交量单位不是股",
                int(len(ratio)),
                symbol=symbol,
            )
        ]
    return []


def validate_adjustment_factors(
    frame: pd.DataFrame,
    symbol: str,
    previous: pd.DataFrame | None,
    first_bar_date: date | None,
) -> list[QualityIssue]:
    dataset = "adjustment_factors"
    issues: list[QualityIssue] = []
    if frame.empty:
        return [QualityIssue(dataset, "not_empty", "blocking", f"{symbol} 没有复权因子", symbol=symbol)]
    duplicate_count = int(frame.duplicated(["symbol", "effective_date"]).sum())
    if duplicate_count:
        issues.append(
            QualityIssue(dataset, "unique_symbol_date", "blocking", f"{symbol} 复权因子日期重复",
                         duplicate_count, symbol=symbol)
        )
    null_count = int(frame["hfq_factor"].isna().sum())
    if null_count:
        issues.append(
            QualityIssue(dataset, "factor_not_null", "blocking", f"{symbol} 后复权因子存在空值",
                         null_count, symbol=symbol)
        )
    invalid_count = int((frame["hfq_factor"] <= 0).sum())
    if invalid_count:
        issues.append(
            QualityIssue(dataset, "positive_factor", "blocking", f"{symbol} 后复权因子存在非正数",
                         invalid_count, symbol=symbol)
        )
    if first_bar_date is not None and frame["effective_date"].min() > first_bar_date:
        issues.append(
            QualityIssue(
                dataset,
                "covers_first_bar",
                "warning",
                f"{symbol} 首个复权因子 {frame['effective_date'].min()} 晚于首根日线 {first_bar_date}",
                symbol=symbol,
            )
        )
    if previous is not None and not previous.empty and "hfq_factor" in previous.columns:
        joined = frame[["effective_date", "hfq_factor"]].merge(
            previous[["effective_date", "hfq_factor"]],
            on="effective_date",
            how="outer",
            suffixes=("", "_previous"),
            indicator=True,
        )
        removed = int((joined["_merge"] == "right_only").sum())
        both = joined[joined["_merge"] == "both"]
        changed = int(
            (
                (both["hfq_factor"] - both["hfq_factor_previous"]).abs()
                > 1e-9 * both["hfq_factor_previous"].abs()
            ).sum()
        )
        if removed or changed:
            issues.append(
                QualityIssue(
                    dataset,
                    "history_stable",
                    "warning",
                    f"{symbol} 历史后复权因子被上游修订：变更 {changed} 条，删除 {removed} 条",
                    changed + removed,
                    symbol=symbol,
                )
            )
    return issues


def validate_index_refresh(
    incoming: pd.DataFrame, existing: pd.DataFrame | None, symbol: str
) -> list[QualityIssue]:
    dataset = "index_bars"
    if incoming.empty:
        return [QualityIssue(dataset, "not_empty", "blocking", f"{symbol} 指数没有返回数据", symbol=symbol)]
    issues = validate_bars(incoming, dataset, symbol)
    if existing is not None and not existing.empty:
        if incoming["trade_date"].max() < existing["trade_date"].max():
            issues.append(
                QualityIssue(
                    dataset,
                    "not_regressed",
                    "blocking",
                    f"{symbol} 新数据最新日期 {incoming['trade_date'].max()} 早于已有 {existing['trade_date'].max()}",
                    symbol=symbol,
                )
            )
    return issues


def quality_summary(issues: list[QualityIssue]) -> dict[str, int]:
    return {
        "blocking": sum(issue.severity == "blocking" for issue in issues),
        "warning": sum(issue.severity == "warning" for issue in issues),
        "affected_rows": sum(issue.affected_rows for issue in issues),
    }


def issues_frame(issues: list[QualityIssue]) -> pd.DataFrame:
    columns = ["dataset", "rule", "severity", "message", "affected_rows", "symbol", "trade_date"]
    if not issues:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([issue.to_dict() for issue in issues], columns=columns).replace({np.nan: None})
