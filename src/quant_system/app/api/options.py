"""/options: the options page (app/options.py): underlyings, a T-shaped chain, contract history and minutes."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Path, Query, Request

from ..deps import Principal, api_error, require
from ..options import OptionsMissing

router = APIRouter(tags=["期权"])
view = require("market:view")
CONTRACT = Path(pattern=r"^[0-9A-Z-]{6,24}$")


def _options(request: Request, method: str, **kwargs):
    try:
        return getattr(request.app.state.options, method)(**kwargs)
    except OptionsMissing as exc:
        raise api_error(503, "no_options", f"期权数据不可用：{exc}") from exc
    except LookupError as exc:
        raise api_error(404, "not_found", str(exc.args[0]) if exc.args else "没有找到") from exc


@router.get("/options/underlyings")
def underlyings(request: Request, _: Principal = Depends(view)) -> dict:
    return _options(request, "underlyings")


@router.get("/options/chain")
def chain(request: Request, underlying: str = Query(pattern=r"^[0-9A-Z]{2,6}$"),
          month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), day: date | None = None,
          _: Principal = Depends(view)) -> dict:
    """Calls and puts of one expiry month on one session (default: the nearest live month, its latest session)."""
    return _options(request, "chain", underlying=underlying, month=month, day=day)


@router.get("/options/contracts/{contract}")
def contract(request: Request, contract: str = CONTRACT, _: Principal = Depends(view)) -> dict:
    return _options(request, "contract", contract=contract)


@router.get("/options/contracts/{contract}/minutes")
def minutes(request: Request, day: date, contract: str = CONTRACT, _: Principal = Depends(view)) -> dict:
    return _options(request, "minutes", contract=contract, day=day)
