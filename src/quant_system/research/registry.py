"""Experiment registry (SQLite): every research run is recorded (ADR-009).

The registry exists to keep research honest: every trial of every sweep is
logged, so a reported result always shows how many trials stand behind it,
and the out-of-sample window can be opened only once per configuration.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id    TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    hypothesis       TEXT,
    is_start         TEXT, is_end TEXT, oos_start TEXT, oos_end TEXT,
    protocol_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    experiment_id    TEXT REFERENCES experiments(experiment_id),
    kind             TEXT NOT NULL,          -- factor_eval | backtest | trial | stress | oos
    parent_run_id    TEXT,
    sweep_id         TEXT,
    config_hash      TEXT,
    config_json      TEXT,
    strategy_id      TEXT, strategy_version TEXT,
    git_sha          TEXT, dirty INTEGER,
    features_hash    TEXT,
    data_version     TEXT,
    market_fp        TEXT, research_fp TEXT,
    cache_key        TEXT,
    sample           TEXT,                   -- IS | OOS | FULL
    period_start     TEXT, period_end TEXT,
    status           TEXT NOT NULL,          -- running | complete | failed
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    artifacts_path   TEXT,
    error            TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    name   TEXT NOT NULL,
    value  REAL,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id      TEXT PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    grid_json     TEXT NOT NULL,
    n_trials      INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oos_access (
    access_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT REFERENCES experiments(experiment_id),
    config_hash   TEXT NOT NULL,
    run_id        TEXT,
    at            TEXT NOT NULL,
    reason        TEXT,
    forced        INTEGER NOT NULL DEFAULT 0
);
"""

RUN_FIELDS = (
    "experiment_id", "kind", "parent_run_id", "sweep_id", "config_hash", "config_json", "strategy_id",
    "strategy_version", "git_sha", "dirty", "features_hash", "data_version", "market_fp", "research_fp",
    "cache_key", "sample", "period_start", "period_end", "artifacts_path",
)


class OutOfSampleAlreadyUsed(RuntimeError):
    """The out-of-sample window was already opened for this configuration."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
            con.commit()
        finally:
            con.close()

    # -- experiments ---------------------------------------------------------

    def ensure_experiment(self, name: str, *, hypothesis: str = "", is_period: tuple[str, str] | None = None,
                          oos_period: tuple[str, str] | None = None, protocol_version: str = "1") -> str:
        """Return the experiment id for ``name``, creating it on first use.

        The sample windows are fixed at creation: re-registering with
        different windows raises, so the out-of-sample boundary cannot drift.
        """
        is_start, is_end = is_period or (None, None)
        oos_start, oos_end = oos_period or (None, None)
        with self._connect() as con:
            row = con.execute("SELECT experiment_id, is_start, is_end, oos_start, oos_end FROM experiments "
                              "WHERE name = ?", (name,)).fetchone()
            if row is not None:
                if (is_period or oos_period) and tuple(row[1:]) != (is_start, is_end, oos_start, oos_end):
                    raise ValueError(f"experiment {name!r} already registered with windows {row[1:]}")
                return row[0]
            experiment_id = f"exp-{len(con.execute('SELECT 1 FROM experiments').fetchall()) + 1:04d}"
            con.execute(
                "INSERT INTO experiments (experiment_id, name, hypothesis, is_start, is_end, oos_start, oos_end, "
                "protocol_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (experiment_id, name, hypothesis, is_start, is_end, oos_start, oos_end, protocol_version, _now()),
            )
            return experiment_id

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return dict(row)

    # -- runs ------------------------------------------------------------------

    def start_run(self, run_id: str, kind: str, **fields: Any) -> None:
        unknown = set(fields) - set(RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run fields {sorted(unknown)}")
        if isinstance(fields.get("config_json"), Mapping):
            fields["config_json"] = json.dumps(fields["config_json"], ensure_ascii=False, sort_keys=True)
        if "dirty" in fields and fields["dirty"] is not None:
            fields["dirty"] = int(bool(fields["dirty"]))
        columns = ["run_id", "kind", "status", "started_at", *fields]
        values = [run_id, kind, "running", _now(), *fields.values()]
        with self._connect() as con:
            con.execute(f"INSERT INTO runs ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", values)

    def finish_run(self, run_id: str, *, status: str = "complete", metrics: Mapping[str, float] | None = None,
                   artifacts_path: str | None = None, error: str | None = None) -> None:
        with self._connect() as con:
            con.execute("UPDATE runs SET status = ?, finished_at = ?, error = ?, "
                        "artifacts_path = COALESCE(?, artifacts_path) WHERE run_id = ?",
                        (status, _now(), error, artifacts_path, run_id))
            for name, value in (metrics or {}).items():
                value = None if value is None else float(value)
                con.execute("INSERT OR REPLACE INTO metrics (run_id, name, value) VALUES (?, ?, ?)",
                            (run_id, name, value))

    def runs(self, experiment_id: str | None = None, kind: str | None = None) -> pd.DataFrame:
        clauses, params = [], []
        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            params.append(experiment_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as con:
            return pd.read_sql_query(f"SELECT * FROM runs {where} ORDER BY started_at, run_id", con, params=params)

    def metrics(self, run_ids: list[str] | None = None) -> pd.DataFrame:
        with self._connect() as con:
            frame = pd.read_sql_query("SELECT * FROM metrics ORDER BY run_id, name", con)
        return frame if run_ids is None else frame[frame["run_id"].isin(run_ids)]

    def trial_count(self, experiment_id: str) -> int:
        """Completed in-sample runs behind any result of this experiment."""
        with self._connect() as con:
            (count,) = con.execute(
                "SELECT count(*) FROM runs WHERE experiment_id = ? AND status = 'complete' "
                "AND kind IN ('trial', 'backtest', 'factor_eval') AND COALESCE(sample, 'IS') = 'IS'",
                (experiment_id,),
            ).fetchone()
        return int(count)

    # -- sweeps ----------------------------------------------------------------

    def record_sweep(self, sweep_id: str, experiment_id: str, grid: Mapping[str, Any], n_trials: int) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO sweeps (sweep_id, experiment_id, grid_json, n_trials, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sweep_id, experiment_id, json.dumps(grid, ensure_ascii=False, sort_keys=True),
                         n_trials, _now()))

    # -- out-of-sample gate -----------------------------------------------------

    def open_out_of_sample(self, experiment_id: str, config_hash: str, run_id: str, *, reason: str = "",
                           force: bool = False) -> None:
        """Record opening the OOS window; refuse a second opening of the same
        configuration unless ``force`` with a written reason."""
        with self._connect() as con:
            earlier = con.execute("SELECT at FROM oos_access WHERE experiment_id = ? AND config_hash = ?",
                                  (experiment_id, config_hash)).fetchall()
            if earlier and not force:
                raise OutOfSampleAlreadyUsed(
                    f"out-of-sample already evaluated for this configuration at {earlier[0][0]}")
            if earlier and not reason.strip():
                raise ValueError("forcing a repeated out-of-sample run needs a reason")
            con.execute("INSERT INTO oos_access (experiment_id, config_hash, run_id, at, reason, forced) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (experiment_id, config_hash, run_id, _now(), reason, int(bool(earlier))))

    def out_of_sample_accesses(self, experiment_id: str | None = None) -> pd.DataFrame:
        with self._connect() as con:
            if experiment_id is None:
                return pd.read_sql_query("SELECT * FROM oos_access ORDER BY access_id", con)
            return pd.read_sql_query("SELECT * FROM oos_access WHERE experiment_id = ? ORDER BY access_id",
                                     con, params=[experiment_id])
