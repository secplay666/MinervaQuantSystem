"""/health, /meta and /system: liveness, environment label and data health."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DecisionRun
from ..deps import Principal, get_session, require, settings_of

router = APIRouter(tags=["系统"])


def read_tsv(path: Path, limit: int) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return rows[-limit:][::-1]


def latest_manifests(root: Path, limit: int) -> list[dict]:
    out = []
    for path in sorted((root / "data" / "manifests").glob("*.json"), reverse=True)[:limit]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.append({k: payload.get(k) for k in ("run_id", "status", "mode", "started_at", "finished_at",
                                                 "expected_latest_date", "quality_summary", "catalog_status")})
    return out


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok", "environment": settings_of(request).environment}


@router.get("/meta")
def meta(request: Request) -> dict:
    settings = settings_of(request)
    return {"environment": settings.environment, "environment_label": settings.environment_label,
            "name": "Minerva 决策辅助", "api_version": "v1"}


@router.get("/system/status")
def status(request: Request, _: Principal = Depends(require("data:view")),
           session: Session = Depends(get_session)) -> dict:
    root = settings_of(request).root
    market = request.app.state.market
    runs = session.scalars(select(DecisionRun).where(DecisionRun.status != "superseded")
                           .order_by(DecisionRun.created_at.desc()).limit(10))
    return {
        "latest_session": market.latest_session().isoformat() if market.available() else None,
        "ingest_runs": latest_manifests(root, 5),
        "daily_jobs": read_tsv(root / "logs" / "daily" / "history.tsv", 15),
        "backups": read_tsv(root / "logs" / "backup" / "history.tsv", 15),
        "decisions": [{"run_id": r.run_id, "account_id": r.account_id, "trade_date": r.trade_date.isoformat(),
                       "kind": r.kind, "status": r.status,
                       "failed_gate": next((g["gate"] for g in r.gates if not g.get("passed")), None)}
                      for r in runs],
    }
