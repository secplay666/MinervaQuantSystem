"""/market and /instruments: overview, index and stock bars, fundamentals,
and the strategy's scores of a stock."""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..db.models import Account, ChartDrawing, DecisionRun, Fill, TargetPosition
from ..deps import Principal, api_error, get_session, require, settings_of
from ..market import records

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


@router.get("/market/indices")
def indices(request: Request, _: Principal = Depends(view)) -> list:
    return _market(request).indices()


@router.get("/market/indices/{symbol}/bars")
def index_bars(symbol: str, request: Request, start: date | None = None, end: date | None = None,
               limit: int = Query(250, le=6000), period: str = Query("day", pattern="^(day|week|month)$"),
               _: Principal = Depends(view)) -> list:
    return _market(request).index_bars(symbol, start, end, limit, period)


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
         period: str = Query("day", pattern="^(day|week|month)$"), _: Principal = Depends(view)) -> list:
    """qfq is anchored at the latest ex-date and only for display (ADR-004).
    ``end`` pages backwards: the chart asks for older bars when scrolled left."""
    return _market(request).bars(symbol, start, end, adjust, limit, period)


@router.get("/instruments/{symbol}/marks")
def marks(symbol: str, request: Request, principal: Principal = Depends(view),
          session: Session = Depends(get_session)) -> dict:
    """Chart marks: ex-dates, reports, risk warnings, suspensions and, for
    users who may see accounts, the fills of every account in this stock."""
    if not re.fullmatch(r"\d{6}", symbol):
        raise api_error(404, "not_found", "证券不存在")
    market = request.app.state.market
    out = market.marks(symbol) if market.available() else {"dividends": [], "reports": [], "risk": [],
                                                            "suspensions": []}
    fills = []
    if "account:view" in principal.permissions:
        rows = session.execute(select(Fill, Account.name).join(Account, Account.account_id == Fill.account_id)
                               .where(Fill.symbol == symbol, Fill.reversed_by.is_(None))
                               .order_by(Fill.trade_date, Fill.fill_id))
        fills = [{"trade_date": f.trade_date.isoformat(), "account_id": f.account_id, "account_name": name,
                  "side": f.side, "qty": f.qty, "price": f.price_fen / 100, "source": f.source} for f, name in rows]
    return {**out, "fills": fills}


INDEX_CODE = re.compile(r"(sh|sz|bj)\d{6}|H\d{5}")


@router.get("/charts/{symbol}/analysis")
def chart_analysis(symbol: str, request: Request, period: str = Query("day", pattern="^(day|week|month)$"),
                   adjust: str = Query("qfq", pattern="^(none|qfq|hfq)$"),
                   sensitivity: str = Query("medium", pattern="^(fine|medium|coarse)$"),
                   bars: int = Query(500, ge=60, le=2000), _: Principal = Depends(view)) -> dict:
    """Automatic lines for the chart (analytics/chart_analysis.py): swing points,
    support and resistance, trend lines and channels, the latest Fibonacci
    leg, chart patterns and candlestick patterns, on the chart's own bars."""
    from ...analytics.chart_analysis import analyse

    market = _market(request)
    if INDEX_CODE.fullmatch(symbol):
        series = market.index_bars(symbol, None, None, bars, period)
    elif re.fullmatch(r"\d{6}", symbol):
        series = market.bars(symbol, None, None, adjust, bars, period)
    else:
        raise api_error(404, "not_found", "证券不存在")
    return {"symbol": symbol, "period": period, "adjust": adjust, **analyse(series, sensitivity)}


class DrawingsIn(BaseModel):
    overlays: list[dict] = Field(max_length=300)


@router.get("/charts/{symbol}/drawings")
def get_drawings(symbol: str, principal: Principal = Depends(view), session: Session = Depends(get_session)) -> dict:
    """The current user's drawings on this stock (their own, not shared)."""
    row = session.scalar(select(ChartDrawing).where(ChartDrawing.user_id == principal.user.id,
                                                    ChartDrawing.symbol == symbol))
    return {"symbol": symbol, "overlays": row.overlays if row else [],
            "updated_at": row.updated_at.isoformat() if row else None}


@router.put("/charts/{symbol}/drawings")
def put_drawings(symbol: str, body: DrawingsIn, principal: Principal = Depends(view),
                 session: Session = Depends(get_session)) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9]{6,8}", symbol):
        raise api_error(400, "invalid_symbol", "代码不合法")
    if len(json.dumps(body.overlays)) > 200_000:
        raise api_error(400, "too_large", "画线太多，请删除一些后再保存")
    row = session.scalar(select(ChartDrawing).where(ChartDrawing.user_id == principal.user.id,
                                                    ChartDrawing.symbol == symbol))
    if row is None:
        row = ChartDrawing(user_id=principal.user.id, symbol=symbol, overlays=body.overlays)
        session.add(row)
    else:
        row.overlays = body.overlays
        row.updated_at = utc_now()
    session.commit()
    return {"symbol": symbol, "count": len(body.overlays), "updated_at": row.updated_at.isoformat()}


@router.get("/instruments/{symbol}/fundamentals")
def fundamentals(symbol: str, request: Request, periods: int = Query(8, le=40),
                 _: Principal = Depends(view)) -> list:
    return _market(request).fundamentals(symbol, periods)


def _scores(request: Request, run: DecisionRun) -> pd.DataFrame | None:
    root = settings_of(request).root.resolve()
    path = (root / run.report_dir / "scores.csv").resolve() if run.report_dir else None
    if path is None or root not in path.parents or not path.is_file():
        return None
    cache = request.app.state.__dict__.setdefault("scores_cache", {})
    key = (str(path), path.stat().st_mtime)
    if key not in cache:
        cache.clear()  # one decision at a time is enough
        cache[key] = pd.read_csv(path, dtype={"symbol": str})
    return cache[key]


@router.get("/instruments/{symbol}/signals")
def signals(symbol: str, request: Request, _: Principal = Depends(require("decision:view")),
            session: Session = Depends(get_session)) -> dict:
    """Composite and family scores at the latest rebalance decision (its
    scores.csv), and the stock's place in recent target portfolios."""
    if not re.fullmatch(r"\d{6}", symbol):
        raise api_error(404, "not_found", "证券不存在")
    rebalances = (DecisionRun.status == "complete", DecisionRun.kind.in_(("rebalance", "forced")))
    run = session.scalars(select(DecisionRun).where(*rebalances)
                          .order_by(DecisionRun.trade_date.desc(), DecisionRun.created_at.desc()).limit(1)).first()
    out: dict = {"run_id": None, "trade_date": None, "account_id": None, "universe": 0, "scored": 0, "row": None}
    frame = _scores(request, run) if run is not None else None
    if frame is not None:
        rows = records(frame[frame["symbol"] == symbol])
        out.update(run_id=run.run_id, trade_date=run.trade_date.isoformat(), account_id=run.account_id,
                   universe=int(frame["in_universe"].sum()), scored=int(frame["rank"].notna().sum()),
                   row=rows[0] if rows else None)
    history = session.execute(select(DecisionRun.trade_date, DecisionRun.account_id, TargetPosition.rank,
                                     TargetPosition.target_weight, TargetPosition.score)
                              .join(DecisionRun, DecisionRun.run_id == TargetPosition.run_id)
                              .where(TargetPosition.symbol == symbol, *rebalances)
                              .order_by(DecisionRun.trade_date.desc()).limit(24))
    out["history"] = [{"trade_date": d.isoformat(), "account_id": a, "rank": r, "target_weight": w, "score": sc}
                      for d, a, r, w, sc in history]
    return out
