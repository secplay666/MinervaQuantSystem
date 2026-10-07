"""回购增持 and 市场宽度: information lists (app/company_actions.py, app/breadth.py), not signals."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..breadth import BreadthMissing
from ..company_actions import CompanyActionsMissing
from ..deps import Principal, api_error, require

router = APIRouter(tags=["回购增持、市场宽度"])
view = require("market:view")


def _actions(request: Request, method: str, **kwargs):
    try:
        return getattr(request.app.state.company_actions, method)(**kwargs)
    except CompanyActionsMissing as exc:
        raise api_error(503, "no_company_actions", f"回购增持数据不可用：{exc}") from exc


@router.get("/company-actions/buybacks")
def buybacks(request: Request, days: int = Query(90, ge=1, le=3650), kind: str | None = None,
             progress: str | None = None, industry: str | None = None, min_pct: float | None = None,
             _: Principal = Depends(view)) -> dict:
    """Buyback plans announced or updated in the last ``days`` (kind: cancel / incentive / other)."""
    return _actions(request, "buybacks", days=days, kind=kind, progress=progress, industry=industry, min_pct=min_pct)


@router.get("/company-actions/holders")
def holders(request: Request, days: int = Query(90, ge=1, le=3650), direction: str | None = None,
            industry: str | None = None, min_pct: float | None = None, _: Principal = Depends(view)) -> dict:
    """Holder increases (增持) and decreases (减持) announced in the last ``days``."""
    return _actions(request, "holders", days=days, direction=direction, industry=industry, min_pct=min_pct)


@router.get("/company-actions/industries")
def industries(request: Request, days: int = Query(90, ge=1, le=3650), _: Principal = Depends(view)) -> dict:
    return _actions(request, "industries", days=days)


@router.get("/breadth")
def breadth(request: Request, _: Principal = Depends(view)) -> dict:
    """Market breadth: shares of stocks above their moving averages, highs and lows, by day and by industry."""
    try:
        return request.app.state.breadth.overview()
    except BreadthMissing as exc:
        raise api_error(503, "no_breadth", f"市场宽度数据不可用：{exc}") from exc
