"""Whole-dataset audit over the canonical layer.

Ingestion validates each partition it writes; this module checks properties
that only make sense across the full store: coverage of the latest session,
gaps against the trading calendar, unit consistency, unexplained limit
breaches and factor freshness.  It runs after every ingestion and on demand
(``quant-data audit``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .quality import QualityIssue
from .storage import canonical_path

# Daily price-limit ratios by board; ST names have tighter limits, so using
# the ordinary limit only under-reports breaches.
LIMIT_SQL = """
CASE
    WHEN m.board = 'STAR' THEN 0.20
    WHEN m.board = 'CHINEXT' AND x.trade_date >= DATE '2020-08-24' THEN 0.20
    WHEN m.board = 'BSE' THEN 0.30
    ELSE 0.10
END
"""


@dataclass
class AuditResult:
    issues: list[QualityIssue] = field(default_factory=list)
    summary: dict[str, object] = field(default_factory=dict)
    gaps: pd.DataFrame = field(default_factory=pd.DataFrame)


def _glob(root: Path, dataset: str) -> str | None:
    directory = root / "data" / "canonical" / dataset
    if not directory.exists() or not any(directory.rglob("*.parquet")):
        return None
    return (directory / "**" / "*.parquet").resolve().as_posix()


def _as_datetimes(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Object columns of ``date``/None/NaT do not scan reliably into DuckDB."""
    frame = frame.copy()
    for column in columns:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def _examples(frame: pd.DataFrame, columns: list[str], limit: int = 15) -> str:
    if frame.empty:
        return ""
    return "; ".join(
        " ".join(str(row[column]) for column in columns) for _, row in frame.head(limit).iterrows()
    )


def run_audit(
    root: Path,
    expected_latest: date,
    start_date: date,
    min_latest_coverage: float,
    run_id: str,
) -> AuditResult:
    result = AuditResult()
    bars_glob = _glob(root, "daily_bars")
    if bars_glob is None:
        result.issues.append(QualityIssue("daily_bars", "not_empty", "blocking", "没有任何日线分区"))
        return result
    con = duckdb.connect()
    try:
        return _audit(con, root, bars_glob, expected_latest, start_date, min_latest_coverage, run_id, result)
    finally:
        con.close()


def _audit(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    bars_glob: str,
    expected_latest: date,
    start_date: date,
    min_latest_coverage: float,
    run_id: str,
    result: AuditResult,
) -> AuditResult:
    con.execute(
        f"""
        CREATE TEMP TABLE bars AS
        SELECT symbol, trade_date, open, high, low, close, volume_shares, turnover_cny
        FROM read_parquet('{bars_glob}', union_by_name=true, hive_partitioning=false)
        """
    )
    calendar = _as_datetimes(
        pd.read_parquet(canonical_path(root, "trading_calendar"), columns=["trade_date"]),
        ["trade_date"],
    )
    con.register("calendar_frame", calendar)
    con.execute(
        "CREATE TEMP TABLE calendar AS SELECT * FROM "
        "(SELECT CAST(trade_date AS DATE) AS trade_date FROM calendar_frame) "
        "WHERE trade_date <= ?",
        [expected_latest],
    )
    master_path = canonical_path(root, "security_master")
    if master_path.exists():
        master = pd.read_parquet(
            master_path, columns=["symbol", "board", "list_date", "delist_date", "status"]
        )
    else:
        master = pd.DataFrame(columns=["symbol", "board", "list_date", "delist_date", "status"])
    con.register("master_frame", _as_datetimes(master, ["list_date", "delist_date"]))
    con.execute(
        """
        CREATE TEMP TABLE master AS
        SELECT CAST(symbol AS VARCHAR) AS symbol, CAST(board AS VARCHAR) AS board,
               CAST(list_date AS DATE) AS list_date, CAST(delist_date AS DATE) AS delist_date,
               CAST(status AS VARCHAR) AS status
        FROM master_frame
        """
    )
    suspensions_path = canonical_path(root, "suspension_events")
    if suspensions_path.exists():
        events = pd.read_parquet(
            suspensions_path,
            columns=["symbol", "suspend_start", "suspend_end", "expected_resume", "suspension_type", "source"],
        )
    else:
        events = pd.DataFrame(
            columns=["symbol", "suspend_start", "suspend_end", "expected_resume", "suspension_type", "source"]
        )
    con.register(
        "events_frame", _as_datetimes(events, ["suspend_start", "suspend_end", "expected_resume"])
    )
    # Baidu events often lack a resumption date; such an event covers until
    # the day before the stock's next bar (or is ongoing if it has none).
    con.execute(
        """
        CREATE TEMP TABLE suspensions AS
        WITH e AS (
            SELECT CAST(symbol AS VARCHAR) AS symbol, CAST(suspend_start AS DATE) AS start_date,
                   COALESCE(CAST(suspend_end AS DATE),
                            CAST(CAST(expected_resume AS DATE) - INTERVAL 1 DAY AS DATE)) AS end_date,
                   CAST(source AS VARCHAR) AS source
            FROM events_frame
            WHERE CAST(suspension_type AS VARCHAR) <> 'intraday' AND suspend_start IS NOT NULL
        )
        SELECT e.symbol, e.start_date,
               COALESCE(e.end_date,
                        CAST((SELECT min(b.trade_date) FROM bars b
                              WHERE b.symbol = e.symbol AND b.trade_date > e.start_date)
                             - INTERVAL 1 DAY AS DATE)) AS end_date,
               e.source
        FROM e
        """
    )
    _check_units(con, result)
    _check_latest_coverage(con, expected_latest, min_latest_coverage, result)
    _find_gaps(con, expected_latest, start_date, run_id, result)
    _check_limit_breaches(con, root, result)
    _check_factor_freshness(con, root, expected_latest, result)
    _check_calendar_against_index(con, root, start_date, expected_latest, result)
    return result


def _check_units(con: duckdb.DuckDBPyConnection, result: AuditResult) -> None:
    frame = con.execute(
        """
        SELECT symbol, median(turnover_cny / (volume_shares * close)) AS ratio, count(*) AS rows
        FROM bars
        WHERE volume_shares > 0 AND close > 0 AND turnover_cny > 0
        GROUP BY symbol
        HAVING ratio NOT BETWEEN 0.5 AND 2.0
        ORDER BY symbol
        """
    ).fetchdf()
    result.summary["volume_unit_outliers"] = int(len(frame))
    if not frame.empty:
        result.issues.append(
            QualityIssue(
                "daily_bars",
                "volume_unit_consistency",
                "blocking",
                f"{len(frame)} 只证券成交量单位异常: {_examples(frame, ['symbol', 'ratio'])}",
                int(frame["rows"].sum()),
            )
        )


def _check_latest_coverage(
    con: duckdb.DuckDBPyConnection,
    expected_latest: date,
    min_latest_coverage: float,
    result: AuditResult,
) -> None:
    frame = con.execute(
        """
        WITH active AS (
            SELECT m.symbol
            FROM master m
            WHERE m.status <> 'delisted'
              AND (m.delist_date IS NULL OR m.delist_date > $d)
              AND COALESCE(m.list_date, DATE '1900-01-01') <= $d
        ),
        latest_bar AS (
            SELECT symbol, max(trade_date) AS last_bar, bool_or(trade_date = $d) AS has_bar
            FROM bars GROUP BY symbol
        ),
        suspended AS (
            SELECT DISTINCT symbol FROM suspensions
            WHERE start_date <= $d AND COALESCE(end_date, $d) >= $d
        )
        SELECT a.symbol,
               COALESCE(l.has_bar, false) AS has_bar,
               s.symbol IS NOT NULL AS suspended,
               l.last_bar
        FROM active a
        LEFT JOIN latest_bar l USING (symbol)
        LEFT JOIN suspended s USING (symbol)
        """,
        {"d": expected_latest},
    ).fetchdf()
    if frame.empty:
        return
    active = len(frame)
    with_bar = int(frame["has_bar"].sum())
    suspended = int((~frame["has_bar"] & frame["suspended"]).sum())
    unexplained = frame[~frame["has_bar"] & ~frame["suspended"]].sort_values("symbol")
    denominator = max(active - suspended, 1)
    coverage = with_bar / denominator
    result.summary["latest_coverage"] = {
        "session": str(expected_latest),
        "active": active,
        "with_bar": with_bar,
        "suspended": suspended,
        "unexplained_missing": int(len(unexplained)),
        "coverage": round(coverage, 6),
    }
    if unexplained.empty:
        return
    severity = "blocking" if coverage < min_latest_coverage else "warning"
    result.issues.append(
        QualityIssue(
            "daily_bars",
            "latest_session_coverage",
            severity,
            f"{expected_latest} 有 {len(unexplained)} 只在市证券无日线且无停牌记录"
            f"（覆盖率 {coverage:.2%}）: {_examples(unexplained, ['symbol', 'last_bar'], 30)}",
            int(len(unexplained)),
            trade_date=str(expected_latest),
        )
    )


def _find_gaps(
    con: duckdb.DuckDBPyConnection,
    expected_latest: date,
    start_date: date,
    run_id: str,
    result: AuditResult,
) -> None:
    gaps = con.execute(
        """
        WITH cal AS (
            SELECT trade_date, row_number() OVER (ORDER BY trade_date) AS idx FROM calendar
        ),
        bounds AS (
            SELECT symbol, min(trade_date) AS first_bar, max(trade_date) AS last_bar
            FROM bars GROUP BY symbol
        ),
        win AS (
            SELECT b.symbol,
                   greatest($start, COALESCE(m.list_date, b.first_bar)) AS win_start,
                   CASE WHEN m.status = 'delisted' THEN b.last_bar
                        ELSE least($latest, COALESCE(m.delist_date, $latest)) END AS win_end
            FROM bounds b LEFT JOIN master m USING (symbol)
        ),
        expected AS (
            SELECT w.symbol, w.win_start, w.win_end, c.trade_date, c.idx
            FROM win w JOIN cal c ON c.trade_date BETWEEN w.win_start AND w.win_end
        ),
        missing AS (
            SELECT e.* FROM expected e
            ANTI JOIN bars b ON e.symbol = b.symbol AND e.trade_date = b.trade_date
        ),
        runs AS (
            SELECT *, idx - row_number() OVER (PARTITION BY symbol ORDER BY idx) AS grp FROM missing
        ),
        grouped AS (
            SELECT symbol, min(trade_date) AS gap_start, max(trade_date) AS gap_end,
                   count(*) AS sessions, any_value(win_start) AS win_start, any_value(win_end) AS win_end
            FROM runs GROUP BY symbol, grp
        )
        SELECT g.symbol, g.gap_start, g.gap_end, g.sessions,
               CASE WHEN g.gap_start = g.win_start THEN 'head'
                    WHEN g.gap_end = g.win_end THEN 'tail'
                    ELSE 'internal' END AS position,
               (SELECT string_agg(DISTINCT s.source, ',') FROM suspensions s
                 WHERE s.symbol = g.symbol AND s.start_date <= g.gap_end
                   AND COALESCE(s.end_date, g.gap_end) >= g.gap_start) AS explained_by
        FROM grouped g
        ORDER BY g.symbol, g.gap_start
        """,
        {"start": start_date, "latest": expected_latest},
    ).fetchdf()
    for column in ("gap_start", "gap_end"):
        gaps[column] = pd.to_datetime(gaps[column]).dt.date
    gaps["explained"] = gaps["explained_by"].notna()
    gaps["run_id"] = run_id
    result.gaps = gaps
    if gaps.empty:
        result.summary["gaps"] = {"runs": 0, "sessions": 0}
        return
    buckets = pd.cut(gaps["sessions"], [0, 1, 5, 20, 60, 250, 10_000],
                     labels=["1", "2-5", "6-20", "21-60", "61-250", ">250"])
    unexplained = gaps[~gaps["explained"]]
    result.summary["gaps"] = {
        "runs": int(len(gaps)),
        "sessions": int(gaps["sessions"].sum()),
        "symbols": int(gaps["symbol"].nunique()),
        "explained_runs": int(gaps["explained"].sum()),
        "unexplained_runs": int(len(unexplained)),
        "by_length": {str(key): int(value) for key, value in buckets.value_counts().sort_index().items()},
        "unexplained_by_year": {
            str(key): int(value)
            for key, value in pd.to_datetime(unexplained["gap_start"]).dt.year.value_counts().sort_index().items()
        },
    }
    internal = unexplained[unexplained["position"] == "internal"]
    if not internal.empty:
        result.issues.append(
            QualityIssue(
                "daily_bars",
                "calendar_gaps_unexplained",
                "warning",
                f"{len(internal)} 段交易日缺口无停牌记录可解释（{internal['symbol'].nunique()} 只证券；"
                "停牌事件源仅覆盖 2023 年起，早期缺口多为停牌）: "
                f"{_examples(internal.sort_values('gap_start', ascending=False), ['symbol', 'gap_start', 'sessions'])}",
                int(internal["sessions"].sum()),
            )
        )


def _check_limit_breaches(con: duckdb.DuckDBPyConnection, root: Path, result: AuditResult) -> None:
    factors_glob = _glob(root, "adjustment_factors")
    if factors_glob is None:
        return
    con.execute(
        f"""
        CREATE TEMP TABLE factor_events AS
        SELECT DISTINCT symbol, effective_date
        FROM read_parquet('{factors_glob}', union_by_name=true, hive_partitioning=false)
        """
    )
    frame = con.execute(
        f"""
        WITH cal AS (
            SELECT trade_date, lag(trade_date) OVER (ORDER BY trade_date) AS prev_session FROM calendar
        ),
        x AS (
            SELECT symbol, trade_date, close,
                   lag(close) OVER w AS prev_close,
                   lag(trade_date) OVER w AS prev_date,
                   row_number() OVER w AS rn
            FROM bars
            WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
        )
        SELECT x.symbol, x.trade_date, x.prev_close, x.close,
               round((x.close / x.prev_close - 1) * 100, 2) AS pct
        FROM x
        JOIN cal ON cal.trade_date = x.trade_date
        LEFT JOIN master m USING (symbol)
        WHERE x.rn > 5
          AND x.prev_date = cal.prev_session
          AND (x.close > round(x.prev_close * (1 + {LIMIT_SQL}), 2) + 0.0001
               OR x.close < round(x.prev_close * (1 - {LIMIT_SQL}), 2) - 0.0001)
          AND NOT EXISTS (SELECT 1 FROM factor_events f
                          WHERE f.symbol = x.symbol AND f.effective_date = x.trade_date)
        ORDER BY x.trade_date DESC
        """
    ).fetchdf()
    result.summary["unexplained_limit_breaches"] = int(len(frame))
    if not frame.empty:
        result.issues.append(
            QualityIssue(
                "daily_bars",
                "limit_breach_unexplained",
                "warning",
                f"{len(frame)} 根日线涨跌幅超出板块限制且当日无复权事件（可能缺失复权因子或特殊规则日）: "
                f"{_examples(frame, ['symbol', 'trade_date', 'pct'])}",
                int(len(frame)),
            )
        )


def _check_factor_freshness(
    con: duckdb.DuckDBPyConnection, root: Path, expected_latest: date, result: AuditResult
) -> None:
    factors_glob = _glob(root, "adjustment_factors")
    if factors_glob is None:
        return
    frame = con.execute(
        f"""
        WITH f AS (
            SELECT symbol, max(factor_as_of) AS factor_as_of
            FROM read_parquet('{factors_glob}', union_by_name=true, hive_partitioning=false)
            GROUP BY symbol
        ),
        b AS (SELECT symbol, max(trade_date) AS last_bar FROM bars GROUP BY symbol)
        SELECT b.symbol, b.last_bar, f.factor_as_of, m.status
        FROM b LEFT JOIN f USING (symbol) LEFT JOIN master m USING (symbol)
        WHERE f.symbol IS NULL OR f.factor_as_of IS NULL OR f.factor_as_of < b.last_bar
        ORDER BY b.symbol
        """
    ).fetchdf()
    missing = frame[frame["factor_as_of"].isna()]
    stale = frame[frame["factor_as_of"].notna()]
    result.summary["factor_freshness"] = {"missing": int(len(missing)), "stale": int(len(stale))}
    if not missing.empty:
        result.issues.append(
            QualityIssue(
                "adjustment_factors",
                "factor_present",
                "warning",
                f"{len(missing)} 只证券没有可用复权因子，复权价格为空: {_examples(missing, ['symbol'], 30)}",
                int(len(missing)),
            )
        )
    if not stale.empty:
        result.issues.append(
            QualityIssue(
                "adjustment_factors",
                "factor_fresh",
                "warning",
                f"{len(stale)} 只证券复权因子早于最新日线: {_examples(stale, ['symbol', 'factor_as_of', 'last_bar'])}",
                int(len(stale)),
            )
        )


def _check_calendar_against_index(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    start_date: date,
    expected_latest: date,
    result: AuditResult,
) -> None:
    path = canonical_path(root, "index_bars", "symbol=sh000001")
    if not path.exists():
        return
    index = _as_datetimes(pd.read_parquet(path, columns=["trade_date"]), ["trade_date"])
    con.register("index_frame", index)
    frame = con.execute(
        """
        WITH i AS (SELECT * FROM (SELECT CAST(trade_date AS DATE) AS trade_date FROM index_frame)
                   WHERE trade_date BETWEEN $start AND $latest),
             c AS (SELECT trade_date FROM calendar WHERE trade_date >= $start)
        SELECT 'calendar_only' AS side, trade_date FROM c ANTI JOIN i USING (trade_date)
        UNION ALL
        SELECT 'index_only' AS side, trade_date FROM i ANTI JOIN c USING (trade_date)
        ORDER BY trade_date
        """,
        {"start": start_date, "latest": expected_latest},
    ).fetchdf()
    result.summary["calendar_index_mismatches"] = int(len(frame))
    if not frame.empty:
        result.issues.append(
            QualityIssue(
                "trading_calendar",
                "matches_index_dates",
                "warning",
                f"交易日历与上证指数日期不一致: {_examples(frame, ['side', 'trade_date'])}",
                int(len(frame)),
            )
        )
