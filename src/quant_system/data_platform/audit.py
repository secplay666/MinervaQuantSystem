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

# Board from the security master, falling back to the code range (mirrors
# symbols.infer_board) so the rule still works before a master exists.
BOARD_SQL = """
COALESCE(m.board, CASE
    WHEN x.symbol LIKE '688%' OR x.symbol LIKE '689%' THEN 'STAR'
    WHEN x.symbol LIKE '300%' OR x.symbol LIKE '301%' OR x.symbol LIKE '302%' THEN 'CHINEXT'
    WHEN x.symbol LIKE '92%' OR x.symbol LIKE '43%' OR x.symbol LIKE '83%'
         OR x.symbol LIKE '87%' OR x.symbol LIKE '88%' THEN 'BSE'
    ELSE 'MAIN'
END)
"""

# Daily price-limit ratios by board; ST names have tighter limits, so using
# the ordinary limit only under-reports breaches.  The backtest's
# effective-dated rules (domain/rules.py, configs/market_rules) are the
# authoritative model; this audit heuristic should move onto them.
LIMIT_SQL = f"""
CASE
    WHEN {BOARD_SQL} = 'STAR' THEN 0.20
    WHEN {BOARD_SQL} = 'CHINEXT' AND x.trade_date >= DATE '2020-08-24' THEN 0.20
    WHEN {BOARD_SQL} = 'BSE' THEN 0.30
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
    universe: set[str] | None = None,
) -> AuditResult:
    """``universe`` limits the latest-session coverage rule (configured mode)."""
    result = AuditResult()
    bars_glob = _glob(root, "daily_bars")
    if bars_glob is None:
        result.issues.append(QualityIssue("daily_bars", "not_empty", "blocking", "没有任何日线分区"))
        return result
    con = duckdb.connect()
    try:
        return _audit(con, root, bars_glob, expected_latest, start_date, min_latest_coverage, run_id, result,
                      universe)
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
    universe: set[str] | None = None,
) -> AuditResult:
    source = f"read_parquet('{bars_glob}', union_by_name=true, hive_partitioning=false)"
    columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
    run_id_column = "run_id" if "run_id" in columns else "CAST(NULL AS VARCHAR) AS run_id"
    con.execute(
        f"""
        CREATE TEMP TABLE bars AS
        SELECT symbol, trade_date, open, high, low, close, volume_shares, turnover_cny, {run_id_column}
        FROM {source}
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
    _check_latest_coverage(con, expected_latest, min_latest_coverage, result, universe)
    _find_gaps(con, expected_latest, start_date, run_id, result)
    _check_limit_breaches(con, root, result)
    _check_factor_freshness(con, root, expected_latest, result)
    _check_calendar_against_index(con, root, start_date, expected_latest, result)
    _check_risk_history(root, result)
    _check_reference_coverage(con, root, expected_latest, result)
    return result


def _check_units(con: duckdb.DuckDBPyConnection, result: AuditResult) -> None:
    frame = con.execute(
        """
        SELECT symbol, run_id, median(turnover_cny / (volume_shares * close)) AS ratio, count(*) AS rows
        FROM bars
        WHERE volume_shares > 0 AND close > 0 AND turnover_cny > 0
        GROUP BY symbol, run_id
        HAVING ratio NOT BETWEEN 0.5 AND 2.0
        ORDER BY symbol, run_id
        """
    ).fetchdf()
    result.summary["volume_unit_outliers"] = int(frame["symbol"].nunique())
    if not frame.empty:
        result.issues.append(
            QualityIssue(
                "daily_bars",
                "volume_unit_consistency",
                "blocking",
                f"{frame['symbol'].nunique()} 只证券存在成交量单位异常的批次: "
                f"{_examples(frame, ['symbol', 'run_id', 'ratio'])}",
                int(frame["rows"].sum()),
            )
        )


def _check_latest_coverage(
    con: duckdb.DuckDBPyConnection,
    expected_latest: date,
    min_latest_coverage: float,
    result: AuditResult,
    universe: set[str] | None = None,
) -> None:
    con.register("scope_frame", pd.DataFrame({"symbol": sorted(universe or [])}, dtype="object"))
    frame = con.execute(
        """
        WITH active AS (
            SELECT m.symbol
            FROM master m
            WHERE m.status <> 'delisted'
              AND (m.delist_date IS NULL OR m.delist_date > $d)
              AND COALESCE(m.list_date, DATE '1900-01-01') <= $d
              AND (NOT $scoped OR m.symbol IN (SELECT CAST(symbol AS VARCHAR) FROM scope_frame))
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
        {"d": expected_latest, "scoped": universe is not None},
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
                   CASE WHEN m.status = 'delisted' AND m.delist_date IS NULL THEN b.last_bar
                        WHEN m.status = 'delisted'
                            THEN least($latest, CAST(m.delist_date - INTERVAL 1 DAY AS DATE))
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
               (SELECT string_agg(DISTINCT s.source, ',' ORDER BY s.source) FROM suspensions s
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
          -- Exchanges round limit prices half-up; the epsilon keeps binary
          -- floats such as 4.345 from rounding down.
          AND (x.close > round(x.prev_close * (1 + {LIMIT_SQL}) + 1e-6, 2) + 0.0001
               OR x.close < round(x.prev_close * (1 - {LIMIT_SQL}) + 1e-6, 2) - 0.0001)
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
    columns = {
        row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{factors_glob}', union_by_name=true, hive_partitioning=false)"
        ).fetchall()
    }
    as_of = "CAST(factor_as_of AS DATE)" if "factor_as_of" in columns else "CAST(NULL AS DATE)"
    frame = con.execute(
        f"""
        WITH f AS (
            SELECT symbol, max({as_of}) AS factor_as_of
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


def _check_risk_history(root: Path, result: AuditResult) -> None:
    """Risk-warning intervals vs independent evidence (ADR-004).

    * names in the latest end-of-day snapshot must match the modeled status;
    * SSE main-board risk-warned sessions before 2026-07-06 must stay inside
      the 5% band.
    """
    from .risk_history import MAIN_BOARD_RISK_LIMIT_CHANGE, sse_main_price_evidence
    from .symbols import risk_status_from_name

    intervals_path = canonical_path(root, "risk_warning_intervals")
    if not intervals_path.exists():
        return
    intervals = pd.read_parquet(intervals_path)
    intervals = intervals[intervals["start_date"].notna()]
    for column in ("start_date", "end_date"):
        intervals[column] = pd.to_datetime(intervals[column])

    def status_on(symbol_rows: pd.DataFrame, day: pd.Timestamp) -> str | None:
        hit = symbol_rows[(symbol_rows["start_date"] <= day)
                          & (symbol_rows["end_date"].isna() | (symbol_rows["end_date"] > day))]
        return hit["status"].iloc[0] if len(hit) else None

    grouped = {symbol: rows for symbol, rows in intervals.groupby("symbol")}
    snapshots = sorted((root / "data" / "canonical" / "market_snapshot").glob("snapshot_date=*/data.parquet"))
    if snapshots:
        snapshot = pd.read_parquet(snapshots[-1], columns=["symbol", "name", "snapshot_date"])
        day = pd.Timestamp(snapshot["snapshot_date"].iloc[0])
        mismatched = []
        for symbol, name in zip(snapshot["symbol"], snapshot["name"]):
            named = risk_status_from_name(name)
            modeled = status_on(grouped[symbol], day) if symbol in grouped else None
            if (named or None) != (modeled or None):
                mismatched.append(f"{symbol}:{name}/{modeled}")
        result.summary["risk_name_mismatches"] = {"snapshot": str(day.date()), "count": len(mismatched)}
        if mismatched:
            result.issues.append(QualityIssue(
                "risk_warning_intervals", "matches_snapshot_names", "warning",
                f"{len(mismatched)} 只证券的风险警示状态与 {day.date()} 快照名称不一致: " + "; ".join(mismatched[:20]),
                len(mismatched)))
    master_path = canonical_path(root, "security_master")
    calendar_path = canonical_path(root, "trading_calendar")
    if not master_path.exists() or not calendar_path.exists():
        return
    master = pd.read_parquet(master_path, columns=["symbol", "board"])
    main = set(master.loc[master["board"] == "SSE_MAIN", "symbol"])
    warned = intervals[intervals["symbol"].isin(main) & intervals["status"].isin(["ST", "*ST"])
                       & (intervals["start_date"] < pd.Timestamp(MAIN_BOARD_RISK_LIMIT_CHANGE))]
    if warned.empty:
        return
    sessions = sorted(pd.to_datetime(pd.read_parquet(calendar_path)["trade_date"]).dt.strftime("%Y-%m-%d"))
    evidence = sse_main_price_evidence(root, sessions, sorted(set(warned["symbol"])))
    evidence = evidence[evidence["beyond"]]
    evidence["trade_date"] = pd.to_datetime(evidence["trade_date"])
    breaks = []
    for row in warned.itertuples(index=False):
        rows = evidence[(evidence["symbol"] == row.symbol) & (evidence["trade_date"] >= row.start_date)]
        if not pd.isna(row.end_date):
            rows = rows[rows["trade_date"] < row.end_date]
        breaks.extend(f"{row.symbol}:{d.date()}" for d in rows["trade_date"])
    result.summary["risk_price_band_breaks"] = len(breaks)
    if breaks:
        result.issues.append(QualityIssue(
            "risk_warning_intervals", "consistent_with_price_limits", "warning",
            f"上交所主板风险警示区间内有 {len(breaks)} 个交易日超出 5% 涨跌幅: " + ", ".join(breaks[:20]),
            len(breaks)))


def _check_reference_coverage(con: duckdb.DuckDBPyConnection, root: Path, expected_latest: date,
                              result: AuditResult) -> None:
    """Stage-3 reference data: every stock trading on the latest session
    should have a share-capital history and an industry class."""
    traded = {row[0] for row in con.execute(
        "SELECT DISTINCT symbol FROM bars WHERE trade_date = ?", [expected_latest]).fetchall()}
    if not traded:
        return
    checks = []
    shares_path = canonical_path(root, "share_capital")
    if shares_path.exists():
        shares = pd.read_parquet(shares_path, columns=["symbol", "change_date"])
        known = set(shares.loc[shares["change_date"] <= expected_latest, "symbol"])
        checks.append(("share_capital", "coverage_latest_session", sorted(traded - known), "没有股本记录"))
    industry_path = canonical_path(root, "industry_sw")
    if industry_path.exists():
        industry = pd.read_parquet(industry_path, columns=["symbol", "start_date", "end_date", "l1_code"])
        active = industry[(industry["start_date"] <= expected_latest)
                          & (industry["end_date"].isna() | (industry["end_date"] > expected_latest))
                          & (industry["l1_code"] != "000000")]
        checks.append(("industry_sw", "coverage_latest_session", sorted(traded - set(active["symbol"])),
                       "没有申万行业分类"))
    summary = {}
    for dataset, rule, missing, text in checks:
        summary[dataset] = {"traded": len(traded), "missing": len(missing)}
        if missing:
            result.issues.append(QualityIssue(
                dataset, rule, "warning",
                f"{expected_latest} 有行情的 {len(missing)} 只股票{text}: " + ", ".join(missing[:30]), len(missing)))
    if summary:
        result.summary["reference_coverage"] = summary
