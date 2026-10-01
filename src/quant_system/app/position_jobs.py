"""The stage backtest of the 阶段回测 page as a background job.

Loading the whole market takes seconds and the computation as long again, so
a request queues a job and the page polls it.  One job runs at a time; a
result is kept for the same parameters, period and market catalog (its file
is replaced by every ingest).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd

from ..backtest.market_data import load_market_data
from ..position.stage_backtest import run_stage_backtest
from ..position.stages import StageParams
from .market import MarketQueries
from .position import load_series

log = logging.getLogger(__name__)
GATE_INDEX = "sh000300"  # the index gate of the backtest (CSI 300)
KEEP_RESULTS = 8
KEEP_JOBS = 50


@dataclass
class Job:
    id: str
    key: str
    user_id: int
    params: dict
    start: date | None
    end: date | None
    status: str = "queued"  # queued | running | done | failed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None

    def view(self) -> dict:
        return {"id": self.id, "status": self.status, "params": self.params, "start": self.start, "end": self.end,
                "created_at": self.created_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                "error": self.error, "result": self.result}


class StageBacktests:
    def __init__(self, market: MarketQueries) -> None:
        self.market = market
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stage-backtest")
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._results: OrderedDict[str, dict] = OrderedDict()

    def _key(self, params: StageParams, start: date | None, end: date | None) -> str:
        catalog = self.market.database.stat().st_mtime_ns if self.market.available() else 0
        text = json.dumps([params.to_dict(), str(start), str(end), catalog], sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def submit(self, user_id: int, params: StageParams, start: date | None, end: date | None) -> Job:
        key = self._key(params, start, end)
        with self._lock:
            for job in self._jobs.values():
                if job.key == key and job.user_id == user_id and job.status in ("queued", "running"):
                    return job
            job = Job(uuid.uuid4().hex[:12], key, user_id, params.to_dict(), start, end)
            self._jobs[job.id] = job
            while len(self._jobs) > KEEP_JOBS:
                self._jobs.popitem(last=False)
            if key in self._results:
                job.status, job.result, job.finished_at = "done", self._results[key], job.created_at
                return job
        self._pool.submit(self._run, job, params)
        return job

    def get(self, job_id: str, user_id: int) -> Job | None:
        job = self._jobs.get(job_id)
        return job if job is not None and job.user_id == user_id else None

    def _run(self, job: Job, params: StageParams) -> None:
        job.status = "running"
        try:
            if job.key in self._results:  # an identical job ran while this one waited
                result = self._results[job.key]
            else:
                data = load_market_data("duckdb", self.market.database)
                index = load_series(self.market, GATE_INDEX, "index")
                closes = pd.Series(index.close, index=pd.to_datetime(index.dates)) if index.dates else pd.Series()
                aligned = closes.reindex(pd.to_datetime(list(data.sessions))).to_numpy(dtype=float)
                result = run_stage_backtest(data, aligned, params, job.start, job.end)
                with self._lock:
                    self._results[job.key] = result
                    while len(self._results) > KEEP_RESULTS:
                        self._results.popitem(last=False)
            job.result, job.status = result, "done"
        except Exception as exc:  # reported to the page; the server keeps running
            log.exception("stage backtest %s failed", job.id)
            job.status, job.error = "failed", str(exc) or exc.__class__.__name__
        job.finished_at = datetime.now(timezone.utc)
