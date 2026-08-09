from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


SCHEMA_VERSION = "1.0"


def _first_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    raise KeyError(
        f"None of the expected columns {list(candidates)} exist; "
        f"received {list(frame.columns)}"
    )


def infer_exchange(symbol: str) -> str:
    if symbol.startswith(("6", "5")):
        return "SSE"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZSE"
    if symbol.startswith(("4", "8", "9")):
        return "BSE"
    return "UNKNOWN"


def normalize_instruments(
    raw: pd.DataFrame, run_id: str, ingested_at: str
) -> pd.DataFrame:
    code_col = _first_column(raw, ("code", "代码", "证券代码"))
    name_col = _first_column(raw, ("name", "名称", "证券简称"))
    frame = pd.DataFrame(
        {
            "symbol": raw[code_col].astype(str).str.zfill(6),
            "name": raw[name_col].astype(str).str.strip(),
        }
    )
    frame["exchange"] = frame["symbol"].map(infer_exchange)
    frame["instrument_id"] = (
        "CN." + frame["exchange"] + "." + frame["symbol"]
    )
    frame["market"] = "A_SHARE"
    frame["source"] = "akshare.stock_info_a_code_name"
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return frame[
        [
            "instrument_id",
            "symbol",
            "name",
            "exchange",
            "market",
            "source",
            "ingested_at",
            "run_id",
            "schema_version",
        ]
    ].sort_values(["exchange", "symbol"], ignore_index=True)


def normalize_calendar(
    raw: pd.DataFrame,
    start_date: str,
    end_date: str,
    run_id: str,
    ingested_at: str,
) -> pd.DataFrame:
    date_col = _first_column(raw, ("trade_date", "日期", "date"))
    dates = pd.to_datetime(raw[date_col], errors="coerce").dt.date
    frame = pd.DataFrame({"trade_date": dates}).dropna()
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
    frame = frame.drop_duplicates("trade_date").sort_values("trade_date")
    frame["exchange"] = "CN"
    frame["is_open"] = True
    frame["source"] = "akshare.tool_trade_date_hist_sina"
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return frame.reset_index(drop=True)


def normalize_daily_bars(
    raw: pd.DataFrame,
    symbol: str,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_a_hist.eastmoney",
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    if {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"}.issubset(
        raw.columns
    ):
        columns = {
            "日期": "trade_date",
            "股票代码": "source_symbol",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume_lots",
            "成交额": "turnover_cny",
            "振幅": "amplitude_pct",
            "涨跌幅": "pct_change",
            "涨跌额": "change_cny",
            "换手率": "turnover_rate_pct",
        }
        frame = raw[list(columns)].rename(columns=columns).copy()
        frame["volume_shares"] = (
            pd.to_numeric(frame["volume_lots"], errors="coerce") * 100
        ).round().astype("Int64")
        frame = frame.drop(columns=["source_symbol", "volume_lots"])
    elif {"date", "open", "close", "high", "low", "volume", "amount"}.issubset(
        raw.columns
    ):
        frame = raw[
            ["date", "open", "close", "high", "low", "volume", "turnover", "amount"]
        ].rename(
            columns={
                "date": "trade_date",
                "volume": "volume_shares",
                "turnover": "turnover_rate_ratio",
                "amount": "turnover_cny",
            }
        )
        for column in ["open", "close", "high", "low", "volume_shares", "turnover_cny"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["volume_shares"] = frame["volume_shares"].round().astype("Int64")
        previous_close = frame["close"].shift(1)
        frame["change_cny"] = frame["close"] - previous_close
        frame["pct_change"] = frame["change_cny"] / previous_close * 100
        frame["amplitude_pct"] = (frame["high"] - frame["low"]) / previous_close * 100
        frame["turnover_rate_pct"] = (
            pd.to_numeric(frame["turnover_rate_ratio"], errors="coerce") * 100
        )
        frame = frame.drop(columns=["turnover_rate_ratio"])
    else:
        raise KeyError(f"Unsupported daily bar columns: {list(raw.columns)}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["symbol"] = symbol
    numeric_columns = [
        "open",
        "close",
        "high",
        "low",
        "turnover_cny",
        "amplitude_pct",
        "pct_change",
        "change_cny",
        "turnover_rate_pct",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["exchange"] = infer_exchange(symbol)
    frame["source"] = source
    frame["price_adjustment"] = "NONE"
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    ordered = [
        "symbol",
        "exchange",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume_shares",
        "turnover_cny",
        "amplitude_pct",
        "pct_change",
        "change_cny",
        "turnover_rate_pct",
        "price_adjustment",
        "source",
        "ingested_at",
        "run_id",
        "schema_version",
    ]
    return (
        frame[ordered]
        .dropna(subset=["trade_date"])
        .drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values("trade_date", ignore_index=True)
    )


def normalize_index_bars(
    raw: pd.DataFrame,
    symbol: str,
    name: str,
    start_date: str,
    end_date: str,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_index_daily_em.eastmoney",
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    aliases = {
        "date": ("date", "日期"),
        "open": ("open", "开盘"),
        "close": ("close", "收盘"),
        "high": ("high", "最高"),
        "low": ("low", "最低"),
        "volume": ("volume", "成交量"),
    }
    resolved = {key: _first_column(raw, values) for key, values in aliases.items()}
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(raw[resolved["date"]], errors="coerce").dt.date,
            "open": pd.to_numeric(raw[resolved["open"]], errors="coerce"),
            "high": pd.to_numeric(raw[resolved["high"]], errors="coerce"),
            "low": pd.to_numeric(raw[resolved["low"]], errors="coerce"),
            "close": pd.to_numeric(raw[resolved["close"]], errors="coerce"),
            "volume": pd.to_numeric(raw[resolved["volume"]], errors="coerce"),
        }
    )
    amount_column = next((item for item in ("amount", "成交额") if item in raw.columns), None)
    frame["turnover_cny"] = (
        pd.to_numeric(raw[amount_column], errors="coerce")
        if amount_column
        else pd.NA
    )
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
    frame["symbol"] = symbol
    frame["name"] = name
    frame["source"] = source
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return (
        frame.dropna(subset=["trade_date"])
        .drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values("trade_date", ignore_index=True)
    )


def normalize_adjustment_factors(
    raw: pd.DataFrame,
    symbol: str,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_a_daily.sina",
) -> pd.DataFrame:
    """Normalize AKShare's event-style qfq/hfq factor response.

    The provider returns one row per factor change, plus a 1900-01-01
    baseline row.  We retain the baseline because DuckDB ASOF joins use it
    when calculating adjusted prices for the earliest available bar.
    """
    if raw.empty:
        return pd.DataFrame()
    required = {"date", "qfq_factor", "hfq_factor"}
    if not required.issubset(raw.columns):
        raise KeyError(
            f"Unsupported adjustment factor columns: {list(raw.columns)}"
        )
    frame = raw[["date", "qfq_factor", "hfq_factor"]].copy()
    frame = frame.rename(columns={"date": "effective_date"})
    frame["effective_date"] = pd.to_datetime(
        frame["effective_date"], errors="coerce"
    ).dt.date
    for column in ["qfq_factor", "hfq_factor"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = symbol
    frame["source"] = source
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return (
        frame.dropna(subset=["effective_date"])
        .drop_duplicates(["symbol", "effective_date"], keep="last")
        .sort_values(["symbol", "effective_date"], ignore_index=True)[
            [
                "symbol",
                "effective_date",
                "qfq_factor",
                "hfq_factor",
                "source",
                "ingested_at",
                "run_id",
                "schema_version",
            ]
        ]
    )


def normalize_market_snapshot(
    raw: pd.DataFrame,
    snapshot_date: str,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_a_spot_em.eastmoney",
) -> pd.DataFrame:
    if {"代码", "名称", "最新价", "成交量", "成交额"}.issubset(raw.columns):
        optional_mapping = {
            "涨跌幅": "pct_change",
            "涨跌额": "change_cny",
            "今开": "open",
            "最高": "high",
            "最低": "low",
            "昨收": "previous_close",
            "换手率": "turnover_rate_pct",
            "总市值": "market_cap_cny",
            "流通市值": "float_market_cap_cny",
        }
        frame = pd.DataFrame(
            {
                "symbol": raw["代码"].astype(str).str.zfill(6),
                "name": raw["名称"].astype(str).str.strip(),
                "last": pd.to_numeric(raw["最新价"], errors="coerce"),
                "volume_shares": (
                    pd.to_numeric(raw["成交量"], errors="coerce") * 100
                ).round().astype("Int64"),
                "turnover_cny": pd.to_numeric(raw["成交额"], errors="coerce"),
            }
        )
        for source_column, target_column in optional_mapping.items():
            frame[target_column] = (
                pd.to_numeric(raw[source_column], errors="coerce")
                if source_column in raw.columns
                else pd.NA
            )
    elif {"code", "name", "zxj", "volume", "turnover"}.issubset(raw.columns):
        frame = pd.DataFrame(
            {
                "symbol": raw["code"].astype(str).str[-6:],
                "name": raw["name"].astype(str).str.strip(),
                "last": pd.to_numeric(raw["zxj"], errors="coerce"),
                "volume_shares": (
                    pd.to_numeric(raw["volume"], errors="coerce") * 100
                ).round().astype("Int64"),
                "turnover_cny": pd.to_numeric(raw["turnover"], errors="coerce")
                * 10_000,
                "pct_change": pd.to_numeric(raw.get("zdf"), errors="coerce"),
                "change_cny": pd.to_numeric(raw.get("zd"), errors="coerce"),
                "turnover_rate_pct": pd.to_numeric(raw.get("hsl"), errors="coerce"),
                "market_cap_cny": pd.to_numeric(raw.get("zsz"), errors="coerce")
                * 100_000_000,
                "float_market_cap_cny": pd.to_numeric(
                    raw.get("ltsz"), errors="coerce"
                )
                * 100_000_000,
            }
        )
        for column in ("open", "high", "low", "previous_close"):
            frame[column] = pd.NA
    else:
        raise KeyError(f"Unsupported market snapshot columns: {list(raw.columns)}")
    frame["exchange"] = frame["symbol"].map(infer_exchange)
    frame["snapshot_date"] = pd.to_datetime(snapshot_date).date()
    frame["is_st"] = frame["name"].str.upper().str.match(r"^(\*?ST|S\*ST)")
    frame["source"] = source
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return frame.drop_duplicates("symbol", keep="last").sort_values(
        "symbol", ignore_index=True
    )
