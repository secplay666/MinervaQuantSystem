"""/monitor: 盘中大资金 (app/monitor.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import Principal, api_error, require
from ..monitor import BigMoneyMissing

router = APIRouter(tags=["盘中监测"])
view = require("market:view")


@router.get("/monitor/big-money")
def big_money(request: Request, day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              _: Principal = Depends(view)) -> dict:
    """The broad-index ETF groups' flows of a session, minute by minute (default: the latest monitored day)."""
    try:
        return request.app.state.big_money.day(day)
    except BigMoneyMissing as exc:
        raise api_error(404, "no_monitor_data", str(exc)) from exc
