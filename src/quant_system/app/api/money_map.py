"""Money map: how crowded each SW L1 industry's trading is, week by week (app/money_map.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import Principal, api_error, require
from ..money_map import DEFAULT_WEEKS, MoneyMapMissing

router = APIRouter(tags=["钱去哪地图"])
view = require("market:view")


def _call(request: Request, method: str, *args):
    try:
        return getattr(request.app.state.money_map, method)(*args)
    except MoneyMapMissing as exc:
        raise api_error(503, "no_money_map", f"钱去哪地图的数据不可用：{exc}") from exc
    except KeyError as exc:
        raise api_error(404, "unknown_industry", f"没有这个行业：{exc}") from exc


@router.get("/moneymap")
def overview(request: Request, weeks: int = Query(DEFAULT_WEEKS, ge=0, le=2000),
             _: Principal = Depends(view)) -> dict:
    """The last ``weeks`` weekly frames (0 = the whole history) with the statistics known each week."""
    return _call(request, "overview", weeks)


@router.get("/moneymap/industries/{code}")
def industry(code: str, request: Request, _: Principal = Depends(view)) -> dict:
    return _call(request, "industry", code)
