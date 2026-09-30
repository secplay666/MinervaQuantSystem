"""ETF flow dashboard: net subscriptions into broad-index ETFs (app/etf.py)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request

from ..deps import Principal, api_error, require
from ..etf import EtfDataMissing

router = APIRouter(tags=["ETF 资金"])
view = require("market:view")


def _etf(request: Request):
    return request.app.state.etf


def _call(request: Request, method: str, *args):
    try:
        return getattr(_etf(request), method)(*args)
    except EtfDataMissing as exc:
        raise api_error(503, "no_etf_data", f"ETF 数据不可用：{exc}") from exc
    except KeyError as exc:
        raise api_error(404, "unknown_group", f"没有这个分组：{exc}") from exc


@router.get("/etf/overview")
def overview(request: Request, _: Principal = Depends(view)) -> dict:
    return _call(request, "overview")


@router.get("/etf/groups/{group_id}/series")
def series(group_id: str, request: Request, start: date | None = None, end: date | None = None,
           _: Principal = Depends(view)) -> dict:
    return _call(request, "series", group_id, start, end)


@router.get("/etf/marks/{symbol}")
def marks(symbol: str, request: Request, _: Principal = Depends(view)) -> list:
    """Abnormal ETF subscription days to mark on an index chart (empty when none)."""
    try:
        return _etf(request).marks(symbol)
    except EtfDataMissing:
        return []


@router.get("/etf/groups/{group_id}/funds")
def funds(group_id: str, request: Request, day: date | None = None, _: Principal = Depends(view)) -> dict:
    return _call(request, "funds", group_id, day)
