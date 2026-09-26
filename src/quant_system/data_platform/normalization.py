from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

import numpy as np
import pandas as pd

from .symbols import (
    clean_name,
    infer_board,
    infer_exchange,
    instrument_id,
    is_a_share,
    market_prefix,
    risk_status_from_name,
    security_type,
)

SCHEMA_VERSION = "2.0"

__all__ = [
    "SCHEMA_VERSION",
    "as_date",
    "infer_exchange",
    "normalize_security_master",
    "calendar_open_dates",
    "normalize_calendar",
    "normalize_daily_bars",
    "drop_invalid_price_rows",
    "clip_bars",
    "merge_daily_bars",
    "recompute_bar_derived_fields",
    "normalize_index_bars",
    "merge_index_bars",
    "normalize_adjustment_factors",
    "normalize_market_snapshot",
    "normalize_tfp_suspensions",
    "normalize_baidu_suspensions",
    "merge_suspension_events",
    "normalize_sz_name_changes",
    "risk_intervals_from_names",
]

SOURCE_EASTMONEY_DAILY = "akshare.stock_zh_a_hist.eastmoney"
SOURCE_TENCENT_DAILY = "akshare.stock_zh_a_hist_tx.tencent"
SOURCE_SINA_DAILY = "akshare.stock_zh_a_daily.sina"
SOURCE_SINA_RAW_DAILY = "akshare.stock_zh_a_cdr_daily.sina"
SOURCE_TENCENT_INDEX = "akshare.stock_zh_a_hist_tx.tencent"
SOURCE_EASTMONEY_INDEX = "akshare.stock_zh_index_daily_em.eastmoney"
SOURCE_CSINDEX_INDEX = "akshare.stock_zh_index_hist_csindex.csindex"

DAILY_BAR_COLUMNS = [
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
    "volume_scale",
    "source",
    "ingested_at",
    "run_id",
    "schema_version",
]

PRICE_COLUMNS = ["open", "high", "low", "close"]


def _first_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str:
    candidates = list(candidates)
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    raise KeyError(
        f"None of the expected columns {candidates} exist; "
        f"received {list(frame.columns)}"
    )


def as_date(value: object) -> date | None:
    """``date`` or ``None``; note ``isinstance(pd.NaT, date)`` is True."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value if isinstance(value, date) else pd.to_datetime(value).date()


def _to_date(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip().replace({"-": pd.NA, "": pd.NA})
    parsed = pd.to_datetime(text, errors="coerce")
    return pd.Series([as_date(item) for item in parsed], index=values.index, dtype="object")


def _lineage(frame: pd.DataFrame, source: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    frame["source"] = source
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return frame


# ---------------------------------------------------------------------------
# Security master
# ---------------------------------------------------------------------------

MASTER_COLUMNS = [
    "instrument_id",
    "symbol",
    "name",
    "exchange",
    "board",
    "security_type",
    "list_date",
    "delist_date",
    "status",
    "source",
    "ingested_at",
    "run_id",
    "schema_version",
]


def _master_rows(
    frame: pd.DataFrame,
    code_col: str,
    name_col: str,
    list_col: str | None,
    delist_col: str | None,
    source: str,
    board_col: str | None = None,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["symbol", "name", "list_date", "delist_date", "board", "source"])
    result = pd.DataFrame(
        {
            "symbol": frame[code_col].astype(str).str.strip().str.zfill(6),
            "name": frame[name_col].map(clean_name),
            "list_date": _to_date(frame[list_col]) if list_col else None,
            "delist_date": _to_date(frame[delist_col]) if delist_col else None,
        }
    )
    if board_col:
        result["board"] = frame[board_col].map({"主板": "SZSE_MAIN", "创业板": "CHINEXT"})
    else:
        result["board"] = None
    result["board"] = result["board"].fillna(result["symbol"].map(infer_board))
    result["source"] = source
    return result[result["symbol"].map(is_a_share)]


def normalize_security_master(
    lists: Mapping[str, pd.DataFrame],
    manual_delistings: Sequence[Mapping[str, object]],
    run_id: str,
    ingested_at: str,
) -> pd.DataFrame:
    """Build a survivorship-free master from exchange listing endpoints.

    ``lists`` keys: ``sse_main``, ``sse_star``, ``szse``, ``bse``,
    ``sse_delisted``, ``szse_delisted`` (raw AKShare frames).
    """
    listed = pd.concat(
        [
            _master_rows(lists.get("sse_main"), "证券代码", "证券简称", "上市日期", None,
                         "akshare.stock_info_sh_name_code.main"),
            _master_rows(lists.get("sse_star"), "证券代码", "证券简称", "上市日期", None,
                         "akshare.stock_info_sh_name_code.star"),
            _master_rows(lists.get("szse"), "A股代码", "A股简称", "A股上市日期", None,
                         "akshare.stock_info_sz_name_code", board_col="板块"),
            _master_rows(lists.get("bse"), "证券代码", "证券简称", "上市日期", None,
                         "akshare.stock_info_bj_name_code"),
        ],
        ignore_index=True,
    )
    listed["status"] = np.where(
        listed["name"].map(risk_status_from_name) == "DELISTING",
        "delisting_period",
        "listed",
    )
    listed["delist_date"] = None
    delisted = pd.concat(
        [
            _master_rows(lists.get("sse_delisted"), "公司代码", "公司简称", "上市日期",
                         "暂停上市日期", "akshare.stock_info_sh_delist"),
            _master_rows(lists.get("szse_delisted"), "证券代码", "证券简称", "上市日期",
                         "终止上市日期", "akshare.stock_info_sz_delist"),
        ],
        ignore_index=True,
    )
    manual = pd.DataFrame(
        [
            {
                "symbol": str(item["symbol"]).zfill(6),
                "name": clean_name(item["name"]),
                "list_date": pd.to_datetime(item.get("list_date"), errors="coerce"),
                "delist_date": pd.to_datetime(item["delist_date"], errors="coerce"),
                "board": infer_board(str(item["symbol"])),
                "source": f"manual:{item.get('source', 'config')}",
            }
            for item in manual_delistings
        ],
        columns=["symbol", "name", "list_date", "delist_date", "board", "source"],
    )
    for column in ("list_date", "delist_date"):
        manual[column] = manual[column].map(as_date).astype("object")
    delisted = pd.concat([delisted, manual], ignore_index=True)
    delisted = delisted[delisted["delist_date"].notna()].copy()
    # SSE reports A+B companies twice under the company code and AKShare drops
    # the field that tells the rows apart.  All six such companies listed in
    # the 1990s, so either date lies before the 2020+ ingestion window.
    delisted = (
        delisted.sort_values(["symbol", "list_date"], na_position="last")
        .drop_duplicates("symbol", keep="first")
    )
    delisted["status"] = "delisted"
    # A code present in the live lists belongs to the live security; an older
    # delisted security that used the same code is a different instrument.
    delisted = delisted[~delisted["symbol"].isin(listed["symbol"])]
    frame = pd.concat([listed, delisted], ignore_index=True)
    frame = frame.drop_duplicates("symbol", keep="first")
    frame["exchange"] = frame["symbol"].map(infer_exchange)
    frame["security_type"] = frame["symbol"].map(security_type)
    frame["instrument_id"] = frame["symbol"].map(instrument_id)
    frame["ingested_at"] = ingested_at
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    frame["list_date"] = frame["list_date"].map(as_date).astype("object")
    frame["delist_date"] = frame["delist_date"].map(as_date).astype("object")
    return frame[MASTER_COLUMNS].sort_values(["exchange", "symbol"], ignore_index=True)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def calendar_open_dates(raw: pd.DataFrame) -> list[date]:
    date_col = _first_column(raw, ("trade_date", "日期", "date"))
    dates = pd.to_datetime(raw[date_col], errors="coerce").dropna().dt.date
    return sorted(set(dates))


def normalize_calendar(
    raw: pd.DataFrame,
    start_date: str | date,
    end_date: str | date,
    run_id: str,
    ingested_at: str,
) -> pd.DataFrame:
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    dates = [item for item in calendar_open_dates(raw) if start <= item <= end]
    frame = pd.DataFrame({"trade_date": dates})
    frame["exchange"] = "CN"
    frame["is_open"] = True
    return _lineage(frame, "akshare.tool_trade_date_hist_sina", run_id, ingested_at)


# ---------------------------------------------------------------------------
# Daily bars
# ---------------------------------------------------------------------------


def tencent_volume_scale(symbol: str) -> float:
    """Undo AKShare 1.18.78's lot/share mix-up in ``stock_zh_a_hist_tx``.

    The adapter multiplies by 100 unless the code starts with sh688, sz399,
    sh000 or sz000.  Tencent reports lots for every A-share except STAR (688)
    and CDRs (689), so sz000xxx stocks stay in lots and sh689xxx gets
    multiplied twice.
    """
    prefixed = market_prefix(symbol)
    if prefixed.startswith("sz000"):
        return 100.0
    if prefixed.startswith("sh689"):
        return 0.01
    return 1.0


def normalize_daily_bars(
    raw: pd.DataFrame,
    symbol: str,
    run_id: str,
    ingested_at: str,
    source: str = SOURCE_EASTMONEY_DAILY,
) -> pd.DataFrame:
    """Map one vendor response to canonical unadjusted daily bars.

    Volume is converted to shares; ``volume_scale`` records the multiplier
    applied to the adapter's value so the repair rule stays auditable.
    Derived fields are recomputed by :func:`recompute_bar_derived_fields`.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=DAILY_BAR_COLUMNS)
    if {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"}.issubset(raw.columns):
        scale = 100.0  # Eastmoney reports lots
        frame = pd.DataFrame(
            {
                "trade_date": raw["日期"],
                "open": raw["开盘"],
                "high": raw["最高"],
                "low": raw["最低"],
                "close": raw["收盘"],
                "volume_raw": raw["成交量"],
                "turnover_cny": raw["成交额"],
                "turnover_rate_pct": raw.get("换手率"),
            }
        )
    elif {"date", "open", "close", "high", "low", "volume"}.issubset(raw.columns):
        scale = tencent_volume_scale(symbol) if "tencent" in source else 1.0
        frame = pd.DataFrame(
            {
                "trade_date": raw["date"],
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "volume_raw": raw["volume"],
                "turnover_cny": raw["amount"] if "amount" in raw.columns else np.nan,
            }
        )
        # AKShare exposes the turnover rate as a ratio for Sina and Tencent.
        frame["turnover_rate_pct"] = (
            pd.to_numeric(raw["turnover"], errors="coerce") * 100
            if "turnover" in raw.columns
            else np.nan
        )
    else:
        raise KeyError(f"Unsupported daily bar columns: {list(raw.columns)}")
    for column in PRICE_COLUMNS + ["volume_raw", "turnover_cny", "turnover_rate_pct"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume_shares"] = (frame.pop("volume_raw") * scale).round().astype("Int64")
    frame["volume_scale"] = scale
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame["symbol"] = symbol
    frame["exchange"] = infer_exchange(symbol)
    frame["price_adjustment"] = "NONE"
    frame = _lineage(frame, source, run_id, ingested_at)
    frame = (
        frame.dropna(subset=["trade_date"])
        .drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values("trade_date", ignore_index=True)
    )
    return recompute_bar_derived_fields(frame)


def drop_invalid_price_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove rows whose OHLC contains a non-positive or missing price.

    Sina's NEEQ-era history has negotiated-trade days with all-zero OHLC; they
    are not tradable prices.  The raw layer keeps the original rows.
    """
    if frame.empty:
        return frame, 0
    invalid = (frame[PRICE_COLUMNS] <= 0).any(axis=1) | frame[PRICE_COLUMNS].isna().any(axis=1)
    return frame.loc[~invalid].reset_index(drop=True), int(invalid.sum())


def clip_bars(
    frame: pd.DataFrame, start: date | None, end: date | None
) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame, 0
    keep = pd.Series(True, index=frame.index)
    if start is not None:
        keep &= frame["trade_date"] >= start
    if end is not None:
        keep &= frame["trade_date"] <= end
    return frame.loc[keep].reset_index(drop=True), int((~keep).sum())


def recompute_bar_derived_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """Raw close-to-close fields, identical in full and incremental mode.

    These are unadjusted: on ex-rights dates ``pct_change`` includes the
    distribution.  Research returns must use hfq-adjusted prices.
    """
    if frame.empty:
        return frame.reindex(columns=DAILY_BAR_COLUMNS)
    frame = frame.sort_values("trade_date", ignore_index=True)
    previous_close = frame["close"].shift(1).where(lambda value: value > 0)
    frame["change_cny"] = frame["close"] - previous_close
    frame["pct_change"] = frame["change_cny"] / previous_close * 100
    frame["amplitude_pct"] = (frame["high"] - frame["low"]) / previous_close * 100
    return frame[DAILY_BAR_COLUMNS]


def merge_daily_bars(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Overlay ``incoming`` on ``existing``; the newer vendor row wins."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=DAILY_BAR_COLUMNS)
    frame = pd.concat([part.reindex(columns=DAILY_BAR_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.drop_duplicates(["symbol", "trade_date"], keep="last")
    return recompute_bar_derived_fields(frame)


# ---------------------------------------------------------------------------
# Index bars
# ---------------------------------------------------------------------------

INDEX_BAR_COLUMNS = [
    "symbol",
    "name",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_cny",
    "volume_scale",
    "source",
    "ingested_at",
    "run_id",
    "schema_version",
]


def normalize_index_bars(
    raw: pd.DataFrame,
    symbol: str,
    name: str,
    start_date: str | date,
    end_date: str | date,
    run_id: str,
    ingested_at: str,
    source: str = SOURCE_TENCENT_INDEX,
) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=INDEX_BAR_COLUMNS)
    aliases = {
        "date": ("date", "日期"),
        "open": ("open", "开盘"),
        "close": ("close", "收盘"),
        "high": ("high", "最高"),
        "low": ("low", "最低"),
        "volume": ("volume", "成交量"),
    }
    resolved = {key: _first_column(raw, values) for key, values in aliases.items()}
    if "csindex" in source:
        scale = 1.0  # CSIndex volume is in shares, amount in 100 million CNY
    elif "tencent" in source:
        # Tencent index volume is in lots; AKShare only converts codes outside
        # its sh000/sz399 exemption, so mirror that rule.
        scale = 100.0 if symbol.startswith(("sh000", "sz399")) else 1.0
    else:
        scale = 100.0  # Eastmoney kline volume is in lots
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(raw[resolved["date"]], errors="coerce").dt.date,
            "open": pd.to_numeric(raw[resolved["open"]], errors="coerce"),
            "high": pd.to_numeric(raw[resolved["high"]], errors="coerce"),
            "low": pd.to_numeric(raw[resolved["low"]], errors="coerce"),
            "close": pd.to_numeric(raw[resolved["close"]], errors="coerce"),
            "volume_shares": (
                pd.to_numeric(raw[resolved["volume"]], errors="coerce") * scale
            ).round().astype("Int64"),
        }
    )
    amount_column = next((item for item in ("amount", "成交额", "成交金额") if item in raw.columns), None)
    frame["turnover_cny"] = (
        pd.to_numeric(raw[amount_column], errors="coerce") if amount_column else np.nan
    )
    if "csindex" in source:
        frame["turnover_cny"] = frame["turnover_cny"] * 1e8
    frame["volume_scale"] = scale
    # CSIndex total-return series publish only the close; open/high/low are
    # set to it so bar validation still applies (only the close is used).
    close_only = frame[["open", "high", "low"]].isna().all(axis=1) & frame["close"].notna()
    for column in ("open", "high", "low"):
        frame.loc[close_only, column] = frame.loc[close_only, "close"]
    frame = frame.dropna(subset=["trade_date"])
    start = pd.to_datetime(start_date).date()
    end = pd.to_datetime(end_date).date()
    frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)].copy()
    frame["symbol"] = symbol
    frame["name"] = name
    frame = _lineage(frame, source, run_id, ingested_at)
    return (
        frame.drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values("trade_date", ignore_index=True)[INDEX_BAR_COLUMNS]
    )


def merge_index_bars(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=INDEX_BAR_COLUMNS)
    frame = pd.concat([part.reindex(columns=INDEX_BAR_COLUMNS) for part in parts], ignore_index=True)
    return (
        frame.drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values("trade_date", ignore_index=True)
    )


# ---------------------------------------------------------------------------
# Adjustment factors
# ---------------------------------------------------------------------------

FACTOR_COLUMNS = [
    "symbol",
    "effective_date",
    "hfq_factor",
    "qfq_factor",
    "factor_as_of",
    "source",
    "ingested_at",
    "run_id",
    "schema_version",
]


def normalize_adjustment_factors(
    raw: pd.DataFrame,
    symbol: str,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_a_daily.sina",
    factor_as_of: date | None = None,
) -> pd.DataFrame:
    """Normalize event-style adjustment factors to one multiplier convention.

    ``hfq_factor`` is the canonical factor: adjusted = raw * hfq_factor, and
    it is stable across refreshes.  Sina's own ``qfq_factor`` is a divisor
    re-anchored at every new event, so it is ignored; ``qfq_factor`` here is
    ``hfq_factor / latest hfq_factor`` (a multiplier anchored at the latest
    event known on ``factor_as_of``, i.e. not point-in-time).
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=FACTOR_COLUMNS)
    if not {"date", "hfq_factor"}.issubset(raw.columns):
        raise KeyError(f"Unsupported adjustment factor columns: {list(raw.columns)}")
    frame = pd.DataFrame(
        {
            "effective_date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "hfq_factor": pd.to_numeric(raw["hfq_factor"], errors="coerce"),
        }
    )
    frame = (
        frame.dropna(subset=["effective_date"])
        .drop_duplicates("effective_date", keep="last")
        .sort_values("effective_date", ignore_index=True)
    )
    latest = frame["hfq_factor"].dropna()
    frame["qfq_factor"] = frame["hfq_factor"] / latest.iloc[-1] if not latest.empty else np.nan
    frame["symbol"] = symbol
    frame["factor_as_of"] = factor_as_of
    frame = _lineage(frame, source, run_id, ingested_at)
    return frame[FACTOR_COLUMNS]


# ---------------------------------------------------------------------------
# Market snapshot
# ---------------------------------------------------------------------------


def normalize_market_snapshot(
    raw: pd.DataFrame,
    snapshot_date: str | date,
    run_id: str,
    ingested_at: str,
    source: str = "akshare.stock_zh_a_spot_em.eastmoney",
    fetched_at: str | None = None,
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
                else np.nan
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
            frame[column] = np.nan
    else:
        raise KeyError(f"Unsupported market snapshot columns: {list(raw.columns)}")
    frame["exchange"] = frame["symbol"].map(infer_exchange)
    frame["snapshot_date"] = pd.to_datetime(snapshot_date).date()
    frame["is_st"] = frame["name"].map(risk_status_from_name).isin(["*ST", "ST"])
    frame["fetched_at"] = fetched_at
    frame = _lineage(frame, source, run_id, ingested_at)
    return frame.drop_duplicates("symbol", keep="last").sort_values(
        "symbol", ignore_index=True
    )


# ---------------------------------------------------------------------------
# Suspensions
# ---------------------------------------------------------------------------

SUSPENSION_COLUMNS = [
    "symbol",
    "name",
    "suspend_start",
    "suspend_end",
    "expected_resume",
    "suspension_type",
    "reason",
    "source",
    "observed_date",
    "ingested_at",
    "run_id",
    "schema_version",
]

_TFP_TYPES = {
    "停牌一天": "full_day",
    "连续停牌": "continuous",
    "停牌半天": "half_day",
    "盘中停牌": "intraday",
}


def normalize_tfp_suspensions(
    raw: pd.DataFrame, observed_date: date, run_id: str, ingested_at: str
) -> pd.DataFrame:
    """Eastmoney ``stock_tfp_em``: one row per stock (latest suspension only)."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=SUSPENSION_COLUMNS)
    frame = pd.DataFrame(
        {
            "symbol": raw["代码"].astype(str).str.zfill(6),
            "name": raw["名称"].map(clean_name),
            "suspend_start": _to_date(raw["停牌时间"]),
            "suspend_end": _to_date(raw["停牌截止时间"]),
            "expected_resume": _to_date(raw["预计复牌时间"]),
            "suspension_type": raw["停牌期限"].map(_TFP_TYPES).fillna("unknown"),
            "reason": raw["停牌原因"].astype("string"),
        }
    )
    frame["observed_date"] = observed_date
    frame = _lineage(frame, "akshare.stock_tfp_em.eastmoney", run_id, ingested_at)
    frame = frame[frame["symbol"].map(is_a_share) & frame["suspend_start"].notna()]
    return frame[SUSPENSION_COLUMNS].reset_index(drop=True)


def normalize_baidu_suspensions(
    raw: pd.DataFrame, observed_date: date, run_id: str, ingested_at: str
) -> pd.DataFrame:
    """Baidu ``news_trade_notify_suspend_baidu``: suspensions starting on a day.

    It has no duration field; intraday halts show up with start == resume or a
    reason naming an intraday trigger.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=SUSPENSION_COLUMNS)
    frame = raw[raw["交易所代码"].isin(["SH", "SZ", "BJ"])].copy()
    if frame.empty:
        return pd.DataFrame(columns=SUSPENSION_COLUMNS)
    start = _to_date(frame["停牌时间"])
    resume = _to_date(frame["复牌时间"])
    reason = frame["停牌事项说明"].astype("string").fillna("")
    intraday = (start == resume) | reason.str.contains("盘中|临时停牌", regex=True)
    result = pd.DataFrame(
        {
            "symbol": frame["股票代码"].astype(str).str.zfill(6),
            "name": frame["股票简称"].map(clean_name),
            "suspend_start": start,
            "suspend_end": None,
            "expected_resume": resume,
            "suspension_type": np.where(intraday, "intraday", "unknown"),
            "reason": reason,
        }
    )
    result["observed_date"] = observed_date
    result = _lineage(
        result, "akshare.news_trade_notify_suspend_baidu", run_id, ingested_at
    )
    result = result[result["symbol"].map(is_a_share) & result["suspend_start"].notna()]
    return result[SUSPENSION_COLUMNS].reset_index(drop=True)


def merge_suspension_events(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Append-only merge: vendors overwrite their own history, we do not."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=SUSPENSION_COLUMNS)
    frame = pd.concat([part.reindex(columns=SUSPENSION_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values(["observed_date", "run_id"], kind="stable")
    frame = frame.drop_duplicates(["symbol", "suspend_start", "source"], keep="last")
    return frame.sort_values(["suspend_start", "symbol"], ignore_index=True)


# ---------------------------------------------------------------------------
# Names and risk-warning status
# ---------------------------------------------------------------------------

NAME_CHANGE_COLUMNS = [
    "symbol",
    "effective_date",
    "old_name",
    "new_name",
    "source",
    "ingested_at",
    "run_id",
    "schema_version",
]

RISK_INTERVAL_COLUMNS = [
    "symbol",
    "status",
    "start_date",
    "end_date",
    "method",
    "source",
    "run_id",
    "schema_version",
]


def normalize_sz_name_changes(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=NAME_CHANGE_COLUMNS)
    frame = pd.DataFrame(
        {
            "symbol": raw["证券代码"].astype(str).str.zfill(6),
            "effective_date": _to_date(raw["变更日期"]),
            "old_name": raw["变更前简称"].map(clean_name),
            "new_name": raw["变更后简称"].map(clean_name),
        }
    )
    frame = frame[frame["symbol"].map(is_a_share) & frame["effective_date"].notna()]
    frame = _lineage(frame, "akshare.stock_info_sz_change_name", run_id, ingested_at)
    return frame[NAME_CHANGE_COLUMNS].sort_values(
        ["symbol", "effective_date"], kind="stable", ignore_index=True
    )


def risk_intervals_from_names(
    name_changes: pd.DataFrame,
    current_names: Mapping[str, str],
    run_id: str,
    method: str,
) -> pd.DataFrame:
    """Turn dated name changes into risk-warning intervals.

    Each change's new name holds from its effective date until the next change.
    The name before the first recorded change holds since an unknown start
    (``start_date`` is null).  Symbols without recorded changes contribute
    their current name with an unknown start.  ``end_date`` is exclusive.
    """
    rows: list[dict[str, object]] = []
    covered: set[str] = set()
    for symbol, group in name_changes.groupby("symbol", sort=True):
        covered.add(symbol)
        segments: list[tuple[object, str | None]] = [
            (None, risk_status_from_name(group["old_name"].iloc[0]))
        ]
        for row in group.itertuples(index=False):
            segments.append((row.effective_date, risk_status_from_name(row.new_name)))
        rows.extend(_segments_to_intervals(symbol, segments))
    for symbol, name in current_names.items():
        if symbol in covered:
            continue
        status = risk_status_from_name(name)
        if status:
            rows.append({"symbol": symbol, "status": status, "start_date": None, "end_date": None})
    frame = pd.DataFrame(rows, columns=["symbol", "status", "start_date", "end_date"])
    frame["method"] = method
    frame["source"] = "derived"
    frame["run_id"] = run_id
    frame["schema_version"] = SCHEMA_VERSION
    return frame[RISK_INTERVAL_COLUMNS]


def _segments_to_intervals(
    symbol: str, segments: list[tuple[object, str | None]]
) -> list[dict[str, object]]:
    intervals: list[dict[str, object]] = []
    for index, (start, status) in enumerate(segments):
        end = segments[index + 1][0] if index + 1 < len(segments) else None
        if status is None:
            continue
        if intervals and intervals[-1]["status"] == status and intervals[-1]["end_date"] == start:
            intervals[-1]["end_date"] = end
            continue
        intervals.append({"symbol": symbol, "status": status, "start_date": start, "end_date": end})
    return intervals

