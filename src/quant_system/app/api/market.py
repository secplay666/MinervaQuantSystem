"""/market and /instruments: overview, index and stock bars, fundamentals."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from ..deps import Principal, api_error, require

router = APIRouter(tags=["行情"])
view = require("market:view")


def _market(request: Request):
    market = request.app.state.market
    if not market.available():
        raise api_error(503, "no_market_data", "行情库不可用")
    return market


@router.get("/market/overview")
def overview(request: Request, trade_date: date | None = None, _: Principal = Depends(view)) -> dict:
    return _market(request).overview(trade_date)


@router.get("/market/indices/{symbol}/bars")
def index_bars(symbol: str, request: Request, start: date | None = None, end: date | None = None,
               limit: int = Query(250, le=2000), _: Principal = Depends(view)) -> list:
    return _market(request).index_bars(symbol, start, end, limit)


@router.get("/instruments/search")
def search(request: Request, q: str = Query(min_length=1, max_length=20), _: Principal = Depends(view)) -> list:
    return _market(request).search(q)


@router.get("/instruments/{symbol}")
def instrument(symbol: str, request: Request, _: Principal = Depends(view)) -> dict:
    info = _market(request).instrument(symbol)
    if info is None:
        raise api_error(404, "not_found", "证券不存在")
    return info


@router.get("/instruments/{symbol}/bars")
def bars(symbol: str, request: Request, start: date | None = None, end: date | None = None,
         adjust: str = Query("qfq", pattern="^(none|qfq|hfq)$"), limit: int = Query(250, le=2000),
         _: Principal = Depends(view)) -> list:
    """qfq is anchored at the latest ex-date and only for display (ADR-004)."""
    return _market(request).bars(symbol, start, end, adjust, limit)


@router.get("/instruments/{symbol}/fundamentals")
def fundamentals(symbol: str, request: Request, periods: int = Query(8, le=40),
                 _: Principal = Depends(view)) -> list:
    return _market(request).fundamentals(symbol, periods)
