from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import pandas as pd

Severity = Literal["warning", "blocking"]


@dataclass(frozen=True)
class QualityIssue:
    dataset: str
    rule: str
    severity: Severity
    message: str
    affected_rows: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_instruments(frame: pd.DataFrame) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if frame.empty:
        return [
            QualityIssue(
                "instruments", "not_empty", "blocking", "证券主数据为空"
            )
        ]
    duplicate_count = int(frame.duplicated("instrument_id").sum())
    if duplicate_count:
        issues.append(
            QualityIssue(
                "instruments",
                "unique_instrument_id",
                "blocking",
                "证券内部标识重复",
                duplicate_count,
            )
        )
    unknown_count = int((frame["exchange"] == "UNKNOWN").sum())
    if unknown_count:
        issues.append(
            QualityIssue(
                "instruments",
                "known_exchange",
                "warning",
                "部分证券代码无法推断交易所",
                unknown_count,
            )
        )
    return issues


def validate_calendar(frame: pd.DataFrame) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if frame.empty:
        return [
            QualityIssue(
                "trading_calendar", "not_empty", "blocking", "交易日历为空"
            )
        ]
    duplicate_count = int(frame.duplicated(["exchange", "trade_date"]).sum())
    if duplicate_count:
        issues.append(
            QualityIssue(
                "trading_calendar",
                "unique_exchange_date",
                "blocking",
                "交易日历存在重复日期",
                duplicate_count,
            )
        )
    return issues


def validate_bars(
    frame: pd.DataFrame, dataset: str, symbol: str
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if frame.empty:
        return [
            QualityIssue(
                dataset,
                "not_empty",
                "blocking",
                f"{symbol} 没有返回行情数据",
            )
        ]
    duplicate_count = int(frame.duplicated(["symbol", "trade_date"]).sum())
    if duplicate_count:
        issues.append(
            QualityIssue(
                dataset,
                "unique_symbol_date",
                "blocking",
                f"{symbol} 存在重复交易日",
                duplicate_count,
            )
        )
    missing_key_count = int(frame[["symbol", "trade_date"]].isna().any(axis=1).sum())
    if missing_key_count:
        issues.append(
            QualityIssue(
                dataset,
                "key_not_null",
                "blocking",
                f"{symbol} 存在空主键",
                missing_key_count,
            )
        )
    price_columns = ["open", "high", "low", "close"]
    negative_price_count = int((frame[price_columns] < 0).any(axis=1).sum())
    if negative_price_count:
        issues.append(
            QualityIssue(
                dataset,
                "non_negative_prices",
                "blocking",
                f"{symbol} 存在负价格",
                negative_price_count,
            )
        )
    invalid_ohlc = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
    )
    invalid_ohlc_count = int(invalid_ohlc.sum())
    if invalid_ohlc_count:
        issues.append(
            QualityIssue(
                dataset,
                "valid_ohlc_relationship",
                "blocking",
                f"{symbol} 的OHLC价格关系不合法",
                invalid_ohlc_count,
            )
        )
    if "volume_shares" in frame.columns:
        negative_volume_count = int((frame["volume_shares"] < 0).sum())
    else:
        negative_volume_count = int((frame["volume"] < 0).sum())
    if negative_volume_count:
        issues.append(
            QualityIssue(
                dataset,
                "non_negative_volume",
                "blocking",
                f"{symbol} 存在负成交量",
                negative_volume_count,
            )
        )
    null_price_count = int(frame[price_columns].isna().any(axis=1).sum())
    if null_price_count:
        issues.append(
            QualityIssue(
                dataset,
                "price_not_null",
                "warning",
                f"{symbol} 存在空价格",
                null_price_count,
            )
        )
    return issues


def quality_summary(issues: list[QualityIssue]) -> dict[str, int]:
    return {
        "blocking": sum(issue.severity == "blocking" for issue in issues),
        "warning": sum(issue.severity == "warning" for issue in issues),
        "affected_rows": sum(issue.affected_rows for issue in issues),
    }

