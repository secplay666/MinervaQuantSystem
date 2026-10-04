"""Position manager services: the library, labels, levels and evaluation
(docs/design/position-manager.md).  The engines are pure (position/stages.py,
position/rules.py); this module loads what they need from the business
database and the market catalog, and converts prices.

Prices: the rules compare hfq closes with hfq levels, which stay valid across
ex-dates.  A level entered on a forward-adjusted (qfq) or unadjusted chart is
converted with the security's latest hfq factor at that time (both equal the
latest raw price scale); shown back in qfq with the latest factor now.
Indices and ETFs are not adjusted.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..position.rules import (
    BASE, EXHAUSTED, LABEL_NAMES, LEFT, PHASE_NAMES, RIGHT, TOP, UNDECIDED, IndexDay, RuleParams, Structure,
    Zone,
    base_prompts, final_remaining, phases, replay,
)
from ..position.paths import (
    BaseZone, Sentinel, base_zone_entry, days_away, mean_range, sentinel_crossing, suggest_path,
)
from ..position.stages import PRESETS, STAGE_KEYS, StageParams, classify, stage_view
from .db.base import utc_now
from .db.models import PmItem, PmLabelChange, PmLevel, PmQuality, PmSentinel, PmSettings
from .market import MarketQueries

LABELS = (RIGHT, TOP, BASE, LEFT, UNDECIDED)
LEVEL_KINDS = ("neckline", "target", "top_neckline", "base_zone", "buyback_zone", "reference")
ZONE_KINDS = ("base_zone", "buyback_zone")  # levels with a lower edge
MAX_SENTINELS = 2
STAGE_TO_LABEL = {"advance": RIGHT, "top": TOP, "base": BASE, "decline": LEFT, "unknown": UNDECIDED}
INDEX_CODE = re.compile(r"^((sh|sz|bj)\d{6}|H\d{5})$")
MAIN_INDICES = ("sh000001", "sh000300", "sh000905", "sh000852", "sz399006", "sh000688")
INDEX_NAMES = {"sh000001": "上证指数", "sh000300": "沪深300", "sh000905": "中证500", "sh000852": "中证1000",
               "sz399006": "创业板指", "sh000688": "科创50", "sh000016": "上证50", "sh000510": "中证A500"}
WEIGHT_INDEX = {"000300": "sh000300", "000905": "sh000905", "000852": "sh000852"}


class PositionError(ValueError):
    pass


# -- market series --------------------------------------------------------------------------------


@dataclass
class Series:
    symbol: str
    kind: str
    dates: list[date]
    close: np.ndarray      # hfq for stocks, raw for indices and ETFs
    open: np.ndarray
    volume: np.ndarray
    factor: float          # the latest hfq factor (1 for indices and ETFs)
    high: np.ndarray = field(default_factory=lambda: np.array([]))
    low: np.ndarray = field(default_factory=lambda: np.array([]))

    @property
    def latest(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def shown(self, price: float | None) -> float | None:
        """An hfq price as a forward-adjusted (qfq) one."""
        return None if price is None else price / self.factor


_connection: ContextVar[duckdb.DuckDBPyConnection | None] = ContextVar("pm_market_connection", default=None)


@contextmanager
def one_connection(market: MarketQueries) -> Iterator[None]:
    """One read-only connection for the many small queries of a board, a detail
    or the daily job (opening the catalog costs more than most queries)."""
    if _connection.get() is not None or not market.available():
        yield
        return
    with duckdb.connect(str(market.database), read_only=True) as con:
        token = _connection.set(con)
        try:
            yield
        finally:
            _connection.reset(token)


def _query(market: MarketQueries, sql: str, params: list) -> pd.DataFrame:
    con = _connection.get()
    try:
        return con.execute(sql, params).fetchdf() if con is not None else market._query(sql, params)
    except duckdb.CatalogException:
        return pd.DataFrame()


def _shared(fn):
    """Run ``fn(session, market, ...)`` on one market connection (see one_connection)."""
    @functools.wraps(fn)
    def wrapper(session, market, *args, **kwargs):
        with one_connection(market):
            return fn(session, market, *args, **kwargs)
    return wrapper


def _security(market: MarketQueries, symbol: str) -> dict[str, Any] | None:
    """Name and board of a stock (MarketQueries.security, on the shared connection)."""
    rows = _query(market, "SELECT name, board FROM security_master WHERE symbol = ?", [symbol])
    return {"name": rows["name"].iloc[0], "board": rows["board"].iloc[0]} if len(rows) else None


def instrument_kind(market: MarketQueries, symbol: str) -> tuple[str, str | None]:
    """(stock | index | etf, name), or a PositionError for an unknown code."""
    if INDEX_CODE.match(symbol):
        rows = _query(market, "SELECT any_value(name) AS name FROM index_bars WHERE symbol = ?", [symbol])
        if len(rows) and rows["name"].iloc[0] is not None:
            return "index", str(rows["name"].iloc[0])
        raise PositionError(f"{symbol} 不在指数库中")
    if not re.fullmatch(r"\d{6}", symbol):
        raise PositionError(f"{symbol} 不是股票、指数或 ETF 代码")
    info = _security(market, symbol)
    if info is not None:
        return "stock", info["name"]
    rows = _query(market, "SELECT name FROM etf_master WHERE symbol = ? ORDER BY listed_run_id DESC LIMIT 1", [symbol])
    if len(rows):
        return "etf", str(rows["name"].iloc[0])
    raise PositionError(f"{symbol} 不在证券主数据中")


def load_series(market: MarketQueries, symbol: str, kind: str) -> Series:
    if kind == "stock":
        frame = _query(market, "SELECT trade_date, hfq_open AS open, hfq_high AS high, hfq_low AS low, hfq_close AS close, "
                               "volume_shares AS volume, hfq_factor FROM daily_bars_adjusted WHERE symbol = ? "
                               "ORDER BY trade_date", [symbol])
    else:
        table = "index_bars" if kind == "index" else "etf_bars"
        frame = _query(market, f"SELECT trade_date, open, high, low, close, volume_shares AS volume FROM {table} "
                               "WHERE symbol = ? ORDER BY trade_date", [symbol])
    if frame.empty:
        return Series(symbol, kind, [], np.array([]), np.array([]), np.array([]), 1.0)
    factor = 1.0
    if kind == "stock":
        factors = frame["hfq_factor"].dropna()
        factor = float(factors.iloc[-1]) if len(factors) else 1.0
    return Series(symbol, kind, list(pd.to_datetime(frame["trade_date"]).dt.date),
                  frame["close"].to_numpy(dtype=float), frame["open"].to_numpy(dtype=float),
                  frame["volume"].to_numpy(dtype=float), factor, frame["high"].to_numpy(dtype=float),
                  frame["low"].to_numpy(dtype=float))


def to_hfq(price: float, basis: str, series: Series) -> tuple[float, float]:
    """(hfq price, factor used) of a price entered on a chart in ``basis``."""
    if series.kind != "stock" or basis == "hfq":
        return float(price), 1.0
    if basis not in ("qfq", "none"):
        raise PositionError(f"未知的复权口径 {basis}")
    return float(price) * series.factor, series.factor


def default_index(market: MarketQueries, item: PmItem) -> str | None:
    """STAR -> 科创50, ChiNext -> 创业板指, else the CSI 300/500/1000 member
    index, else CSI 1000; an ETF follows its tracking index; indices have none."""
    if item.kind == "index":
        return None
    if item.kind == "etf":
        rows = _query(market, "SELECT index_code FROM etf_master WHERE symbol = ? ORDER BY listed_run_id DESC LIMIT 1",
                      [item.symbol])
        code = str(rows["index_code"].iloc[0]) if len(rows) else ""
        return {"000300": "sh000300", "399300": "sh000300", "000905": "sh000905", "399905": "sh000905",
                "000852": "sh000852", "000016": "sh000016", "399006": "sz399006", "000688": "sh000688",
                "000510": "sh000510"}.get(code, "sh000300")
    info = _security(market, item.symbol) or {}
    if info.get("board") == "STAR":
        return "sh000688"
    if info.get("board") == "CHINEXT":
        return "sz399006"
    rows = _query(market, "SELECT index_code FROM index_weights WHERE symbol = ? "
                          "QUALIFY row_number() OVER (PARTITION BY index_code ORDER BY as_of_date DESC) = 1",
                  [item.symbol])
    for code in ("000300", "000905", "000852"):
        if code in set(rows.get("index_code", pd.Series(dtype=str)).astype(str)):
            return WEIGHT_INDEX[code]
    return "sh000852"


# -- settings, labels, levels -----------------------------------------------------------------------


def user_settings(session: Session, user_id: int) -> PmSettings:
    row = session.get(PmSettings, user_id)
    if row is None:
        row = PmSettings(user_id=user_id, stage_preset="steady", stage_params={}, rule_params={},
                         label_mode="suggest", push_daily=False)
        session.add(row)
        session.flush()
    return row


def stage_params(settings: PmSettings) -> StageParams:
    preset = PRESETS.get(settings.stage_preset, PRESETS["steady"])[1]
    return StageParams.from_dict(settings.stage_params or {}, preset)


def rule_params(settings: PmSettings) -> RuleParams:
    return RuleParams.from_dict(settings.rule_params or {})


def label_history(session: Session, item_id: int) -> list[tuple[date, str]]:
    rows = session.scalars(select(PmLabelChange).where(PmLabelChange.item_id == item_id)
                           .order_by(PmLabelChange.effective_date, PmLabelChange.id))
    return [(r.effective_date, r.label) for r in rows]


def active_levels(session: Session, item_id: int) -> list[PmLevel]:
    return list(session.scalars(select(PmLevel).where(PmLevel.item_id == item_id, PmLevel.status == "active")
                                .order_by(PmLevel.round_no, PmLevel.kind)))


def current_round(levels: list[PmLevel]) -> int:
    rounds = [lv.round_no for lv in levels if lv.kind in ("neckline", "target")]
    return max(rounds) if rounds else 1


def structure_of(levels: list[PmLevel]) -> Structure | None:
    """The latest round with both a neckline and a target."""
    by_round: dict[int, dict[str, PmLevel]] = {}
    for lv in levels:
        by_round.setdefault(lv.round_no, {})[lv.kind] = lv
    for round_no in sorted(by_round, reverse=True):
        kinds = by_round[round_no]
        if "neckline" in kinds and "target" in kinds:
            neck, target = kinds["neckline"], kinds["target"]
            top = kinds.get("top_neckline")
            zone = kinds.get("buyback_zone")
            return Structure(
                neckline=neck.price, target=target.price, effective=max(neck.effective_date, target.effective_date),
                version=neck.version * 1000 + target.version, top_neckline=top.price if top else None,
                top_mode=top.top_mode if top else None,
                top_confirmed=top.effective_date if top is not None and top.top_mode == "confirmed" else None,
                buyback=None if zone is None else Zone(zone.lower or zone.price, zone.price, zone.fraction or 0.5,
                                                       zone.effective_date, zone.version))
    return None


def base_zone_of(levels: list[PmLevel]) -> BaseZone | None:
    """The latest 起涨区 (path B is drawn after a round, whatever round it is filed in)."""
    zones = [lv for lv in levels if lv.kind == "base_zone"]
    if not zones:
        return None
    zone = max(zones, key=lambda lv: (lv.round_no, lv.effective_date, lv.id))
    return BaseZone(upper=zone.price, lower=zone.lower, effective=zone.effective_date, version=zone.id)


def confirm_level(session: Session, item: PmItem, series: Series, kind: str, price: float, basis: str, *,
                  actor: str, source: str = "manual", lower: float | None = None, new_round: bool = False,
                  top_mode: str | None = None, effective: date | None = None, note: str | None = None,
                  fraction: float | None = None) -> PmLevel:
    """Write a confirmed level; the same values as the active version change nothing."""
    if kind not in LEVEL_KINDS:
        raise PositionError(f"未知的价位类型 {kind}")
    if price is None or not np.isfinite(price) or price <= 0 or (lower is not None and not 0 < lower <= price):
        raise PositionError("价格须为正数，区间的下沿不能高于上沿")
    if kind == "top_neckline" and top_mode not in ("observe", "confirmed"):
        raise PositionError("头部颈线须选择“先观察”或“立即确认”")
    if kind == "buyback_zone" and (lower is None or fraction is None or not 0 < fraction <= 1):
        raise PositionError("回撤买入区须填上沿、下沿和买回成数（1%～100%）")
    if kind != "buyback_zone":
        fraction = None
    if series.latest is None:
        raise PositionError("没有行情数据，无法确认价位")
    effective = effective or series.latest
    if effective > series.latest:
        raise PositionError("生效日不能晚于最新交易日")
    levels = active_levels(session, item.id)
    round_no = current_round(levels) + (1 if new_round else 0)
    hfq, factor = to_hfq(price, basis, series)
    hfq_lower = to_hfq(lower, basis, series)[0] if lower is not None else None
    current = next((lv for lv in levels if lv.round_no == round_no and lv.kind == kind), None)
    if current is not None and abs(current.price - hfq) < 1e-6 * hfq and current.top_mode == top_mode \
            and current.fraction == fraction and (
            (current.lower is None and hfq_lower is None)
            or (current.lower is not None and hfq_lower is not None and abs(current.lower - hfq_lower) < 1e-6 * hfq)):
        return current  # idempotent: no new version
    version = 1
    if current is not None:
        current.status = "superseded"
        version = current.version + 1
    level = PmLevel(item_id=item.id, round_no=round_no, kind=kind, price=hfq, lower=hfq_lower, entered_price=price,
                    entered_lower=lower, basis=basis if series.kind == "stock" else "index", factor=factor,
                    version=version, status="active", source=source, effective_date=effective, top_mode=top_mode,
                    note=note, created_by=actor, fraction=fraction)
    session.add(level)
    session.flush()
    return level


def active_sentinels(session: Session, item_id: int) -> list[PmSentinel]:
    """The item's sentinels that are not removed (active or crossed)."""
    return list(session.scalars(select(PmSentinel).where(PmSentinel.item_id == item_id, PmSentinel.status != "removed")
                                .order_by(PmSentinel.id)))


def _check_sentinel(series: Series, hfq: float, direction: str) -> None:
    if direction not in ("up", "down"):
        raise PositionError("哨兵方向只能是向上站上或向下到达")
    if series.latest is None:
        raise PositionError("没有行情数据，无法设置哨兵")
    close = float(series.close[-1])
    if direction == "up" and hfq <= close:
        raise PositionError("向上的哨兵须高于现价（现价已在它上方）")
    if direction == "down" and hfq >= close:
        raise PositionError("向下的哨兵须低于现价（现价已在它下方）")


def add_sentinel(session: Session, item: PmItem, series: Series, price: float, direction: str, basis: str, *,
                 actor: str, source_ref: str | None = None, note: str | None = None) -> PmSentinel:
    """A new sentinel (design §11.3): at most MAX_SENTINELS that are not removed."""
    if len(active_sentinels(session, item.id)) >= MAX_SENTINELS:
        raise PositionError(f"每个标的最多 {MAX_SENTINELS} 条哨兵，先删除或重设一条")
    hfq, factor = to_hfq(price, basis, series)
    _check_sentinel(series, hfq, direction)
    sentinel = PmSentinel(item_id=item.id, price=hfq, entered_price=price, factor=factor, direction=direction,
                          basis=basis if series.kind == "stock" else "index", source_ref=source_ref, note=note,
                          status="active", version=1, effective_date=series.latest, created_by=actor)
    session.add(sentinel)
    session.flush()
    return sentinel


def reset_sentinel(session: Session, sentinel: PmSentinel, series: Series, price: float, basis: str,
                   direction: str | None = None) -> PmSentinel:
    """A new price (dragged on the chart, or after a crossing): active again from the latest session."""
    direction = direction or sentinel.direction
    hfq, factor = to_hfq(price, basis, series)
    _check_sentinel(series, hfq, direction)
    sentinel.price, sentinel.entered_price, sentinel.factor, sentinel.direction = hfq, price, factor, direction
    sentinel.status, sentinel.crossed_on, sentinel.effective_date = "active", None, series.latest
    sentinel.version += 1
    sentinel.updated_at = utc_now()
    session.flush()
    return sentinel


def set_label(session: Session, item: PmItem, label: str, *, source: str, actor: str, effective: date,
              reason: str | None = None) -> PmLabelChange:
    if label not in LABELS:
        raise PositionError(f"未知的方向标签 {label}")
    change = PmLabelChange(item_id=item.id, label=label, source=source, effective_date=effective,
                           stage_reason=reason, created_by=actor)
    session.add(change)
    item.label, item.label_source = label, source
    session.flush()
    return change


# -- evaluation -------------------------------------------------------------------------------------


def stage_labels(series: Series, params: StageParams) -> dict[date, str]:
    """The stage view on every session, as labels (for an index without the user's label)."""
    if not series.dates:
        return {}
    stages = classify(series.close, params.for_index() if series.kind == "index" else params)
    return {d: STAGE_TO_LABEL[STAGE_KEYS[int(s)]] for d, s in zip(series.dates, stages)}


def index_days(session: Session, market: MarketQueries, user_id: int, symbol: str, stage: StageParams,
               rules: RuleParams) -> dict[date, IndexDay]:
    """The main index on each session: the user's label and structure if the
    index is in the library, else its stage view."""
    series = load_series(market, symbol, "index")
    views = stage_labels(series, stage)
    item = session.scalar(select(PmItem).where(PmItem.user_id == user_id, PmItem.symbol == symbol,
                                               PmItem.archived_at.is_(None)))
    labels = label_history(session, item.id) if item is not None else []
    structure = structure_of(active_levels(session, item.id)) if item is not None else None
    progress = phases(series.dates, series.close, structure, rules) if structure is not None else {}
    out: dict[date, IndexDay] = {}
    current = UNDECIDED
    k = 0
    for day in series.dates:
        while k < len(labels) and labels[k][0] <= day:
            current = labels[k][1]
            k += 1
        label = current if current != UNDECIDED else views.get(day, UNDECIDED)
        phase, completion = progress.get(day, (None, None))
        out[day] = IndexDay(label, completion, phase)
    return out


@_shared
def evaluate(session: Session, market: MarketQueries, item: PmItem, settings: PmSettings,
             index_cache: dict | None = None, series: Series | None = None) -> dict[str, Any]:
    """Stage view, rule outcome and base prompts of one item (prices shown in qfq)."""
    stage, rules = stage_params(settings), rule_params(settings)
    series = series or load_series(market, item.symbol, item.kind)
    view = stage_view(series.close, stage, is_index=item.kind == "index") if len(series.close) else None
    levels = active_levels(session, item.id)
    structure = structure_of(levels)
    labels = label_history(session, item.id)
    main = item.primary_index or default_index(market, item)
    index = None
    if main is not None:
        cache = index_cache if index_cache is not None else {}
        if main not in cache:
            cache[main] = index_days(session, market, item.user_id, main, stage, rules)
        index = cache[main]
    outcome = replay(series.dates, series.close, structure, labels, index, rules) if structure and series.dates \
        else None
    base_zone = next((lv for lv in levels if lv.kind == "base_zone" and lv.round_no == current_round(levels)), None)
    prompts = base_prompts(series.dates, series.close, series.open, series.volume, labels,
                           structure.neckline if structure else None, stage) if series.dates else []
    last = float(series.close[-1]) if len(series.close) else None
    previous = float(series.close[-2]) if len(series.close) > 1 else None
    gate = index.get(series.latest) if index and series.latest else None
    zone = base_zone_of(levels)
    zone_event = base_zone_entry(series.dates, series.close, labels, zone, settings.auto_base) \
        if zone is not None and series.dates else None
    sentinels = active_sentinels(session, item.id)
    crossings = []
    for row in sentinels:
        if row.status == "active" and series.dates:
            event = sentinel_crossing(series.dates, series.close, Sentinel(
                row.id, row.price, row.direction, row.effective_date, row.source_ref, row.version))
            if event is not None:
                crossings.append((row, event))
    quality = session.get(PmQuality, item.symbol) if item.kind == "stock" else None
    return {"series": series, "view": view, "structure": structure, "levels": levels, "labels": labels,
            "outcome": outcome, "prompts": prompts, "main_index": main, "index_today": gate,
            "last_close": series.shown(last), "change": (last / previous - 1) if last and previous else None,
            "base_zone": base_zone, "stage_params": stage, "rule_params": rules,
            "zone": zone, "zone_event": zone_event, "sentinels": sentinels, "crossings": crossings,
            "daily_range": mean_range(series.high, series.low, series.close) if len(series.high) > 1 else None,
            "path": suggest_path(structure, outcome, last) if outcome is not None else None,
            "previous_completion": structure.completion(previous) if structure and previous else None,
            "quality": quality}


POOLS = {"hold": "持有", "buyback": "回撤关注", "ready": "就绪", "watch": "观察", "archived": "归档"}


def pool_of(item: PmItem, result: dict[str, Any]) -> str:
    """The item's pool (design §11.2), derived from its state: nothing to record."""
    outcome = result["outcome"]
    if item.archived_at is not None:
        return "archived"
    if outcome is None:
        return "watch"
    if outcome.weight > 0:
        return "hold"
    if outcome.buyback == "waiting" and (outcome.segments or outcome.phase == EXHAUSTED):
        return "buyback"
    if not outcome.round_over and outcome.phase != EXHAUSTED and item.label in (RIGHT, BASE):
        return "ready"
    return "watch"


def sentinel_row(row: PmSentinel, result: dict[str, Any]) -> dict[str, Any]:
    """A sentinel with its distance and "about N days" (prices in qfq)."""
    series: Series = result["series"]
    close = float(series.close[-1]) if len(series.close) else None
    active = row.status == "active"
    return {"id": row.id, "price": series.shown(row.price), "entered_price": row.entered_price, "basis": row.basis,
            "direction": row.direction, "source_ref": row.source_ref, "note": row.note, "status": row.status,
            "version": row.version, "effective_date": row.effective_date, "crossed_on": row.crossed_on,
            "distance": row.price / close - 1 if close else None,
            "days": days_away(row.price, close, result["daily_range"]) if active and close else None}


def _distance(price: float | None, result: dict[str, Any]) -> float | None:
    """How far an hfq trigger price is from the last close."""
    series: Series = result["series"]
    close = float(series.close[-1]) if len(series.close) else None
    return price / close - 1 if price and close else None


def summary_row(item: PmItem, result: dict[str, Any], settings: PmSettings) -> dict[str, Any]:
    """One line of the board and the library."""
    series: Series = result["series"]
    outcome = result["outcome"]
    structure: Structure | None = result["structure"]
    view = shown_view(result["view"], series) if settings.label_mode == "suggest" else None
    waiting = outcome.waiting_for if outcome else _no_structure(item.label, result)
    gate: IndexDay | None = result["index_today"]
    return {
        "id": item.id, "symbol": item.symbol, "name": item.name, "kind": item.kind, "groups": item.groups or [],
        "star": item.star, "label": item.label, "label_name": LABEL_NAMES.get(item.label, item.label),
        "label_source": item.label_source, "stage": view, "latest": series.latest,
        "close": result["last_close"], "change": result["change"],
        "phase": outcome.phase if outcome else None, "phase_name": PHASE_NAMES.get(outcome.phase) if outcome else None,
        "activated_on": outcome.activated_on if outcome else None,
        "completion": outcome.completion if outcome else None, "weight": outcome.weight if outcome else 0.0,
        "waiting_for": waiting, "next_price": series.shown(outcome.next_price) if outcome else None,
        "next_step": outcome.next_step if outcome else None,
        "neckline": series.shown(structure.neckline) if structure else None,
        "target": series.shown(structure.target) if structure else None,
        "script_return": outcome.script_return if outcome else None,
        "round_return": outcome.round_return if outcome else None,
        "main_index": result["main_index"], "main_index_name": INDEX_NAMES.get(result["main_index"] or "", None),
        "main_index_label": gate.label if gate else None,
        "top_watch": bool(outcome and outcome.completion is not None
                          and outcome.completion >= result["rule_params"].top_watch and outcome.weight > 0),
        "note": item.note,
        "pool": pool_of(item, result), "pool_name": POOLS[pool_of(item, result)],
        "round_no": current_round(result["levels"]), "buyback": outcome.buyback if outcome else None,
        "next_distance": _distance(outcome.next_price, result) if outcome else None,
        "completion_change": (outcome.completion - result["previous_completion"])
        if outcome and outcome.completion is not None and result["previous_completion"] is not None else None,
        "sentinels": [sentinel_row(r, result) for r in result["sentinels"]],
        "path": result["path"]["path"] if result["path"] else None,
        "quality": None if result["quality"] is None else {"grade": result["quality"].grade,
                                                           "score": result["quality"].score},
        **breakout_facts(result),
    }


EXIT_RULES = {"false_break": "假突破", "trailing": "移动止盈", "top_break": "头部颈线跌破", "top_confirmed": "头部确认",
              "exhausted": "结构衰竭", "buyback_stop": "买回止损", "buyback_trailing": "买回后止盈"}


def breakout_facts(result: dict[str, Any]) -> dict[str, Any]:
    """For 突破确立 (design §11.5): the breakout close, whether the round entered
    (at half size late in the index move), and the latest exit."""
    outcome, series = result["outcome"], result["series"]
    events = outcome.events if outcome else []
    activated = next((e for e in events if e.rule == "activated"), None)
    entry = next((e for e in reversed(events) if e.rule == "entry"), None)
    exit_event = next((e for e in reversed(events) if e.rule in EXIT_RULES), None)
    return {"breakout_close": series.shown(activated.close) if activated else None,
            "entered": bool(outcome and (outcome.segments or outcome.weight > 0)),
            "half_entry": bool(entry and outcome and outcome.entry_weight < 1),
            "last_exit": None if exit_event is None else {"date": exit_event.trade_date, "rule": exit_event.rule,
                                                          "name": EXIT_RULES[exit_event.rule]}}


def _no_structure(label: str, result: dict[str, Any]) -> str:
    if label == LEFT:
        zone = result["base_zone"]
        close = result["last_close"]
        if zone is not None and close:
            upper = result["series"].shown(zone.price)
            return f"左侧：距起涨区上沿 {upper / close - 1:+.1%}"
        return "左侧：规则休眠"
    if label == UNDECIDED:
        return "未定：先确认方向标签"
    return "未画结构：在看盘中画颈线和量度目标"


# -- what the item page shows (prices in qfq) --------------------------------------------------------


def shown_view(view: dict[str, Any] | None, series: Series) -> dict[str, Any] | None:
    """The stage view with its moving average on the chart's scale, plus the label it suggests."""
    if view is None:
        return None
    return dict(view, ma=series.shown(view.get("ma")), label=STAGE_TO_LABEL.get(view["stage"], UNDECIDED))


def event_row(event, series: Series) -> dict[str, Any]:
    return {"trade_date": event.trade_date, "rule": event.rule, "priority": event.priority,
            "priority_name": PRIORITY_NAMES.get(event.priority), "message": event.message,
            "close": series.shown(event.close), "weight": event.weight, "completion": event.completion}


def level_row(level: PmLevel, series: Series) -> dict[str, Any]:
    return {"id": level.id, "round_no": level.round_no, "kind": level.kind, "price": series.shown(level.price),
            "lower": series.shown(level.lower), "entered_price": level.entered_price,
            "entered_lower": level.entered_lower, "basis": level.basis, "version": level.version,
            "source": level.source, "effective_date": level.effective_date, "top_mode": level.top_mode,
            "note": level.note, "created_by": level.created_by, "fraction": level.fraction}


def ladder_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The reduction steps of the structure: price, position kept and the session each was reached."""
    structure: Structure | None = result["structure"]
    if structure is None:
        return []
    series: Series = result["series"]
    rules: RuleParams = result["rule_params"]
    events = result["outcome"].events if result["outcome"] else []

    def reached(rules_hit: tuple[str, ...], completion: float) -> date | None:
        return next((e.trade_date for e in events if e.rule in rules_hit and e.completion is not None
                     and e.completion >= completion - 1e-6), None)

    # A step passed before a late entry counts as done on the entry session (the entry was sized by it).
    rows = [{"completion": c, "keep": keep, "price": series.shown(structure.price_at(c)),
             "done_on": reached(("ladder", "entry"), c)} for c, keep in rules.ladder]
    rows.append({"completion": 1.0, "keep": final_remaining(result["index_today"], rules),
                 "price": series.shown(structure.target), "done_on": reached(("target",), 1.0)})
    return rows


def segment_row(segment: dict, series: Series) -> dict[str, Any]:
    return {"entry_date": segment["entry_date"], "entry_close": series.shown(segment["entry_close"]),
            "entry_weight": segment["entry_weight"], "exit_date": segment["exit_date"],
            "exit_close": series.shown(segment["exit_close"]), "return": segment["return"],
            "steps": [dict(s, close=series.shown(s["close"])) for s in segment["steps"]]}


def item_detail(session: Session, item: PmItem, result: dict[str, Any], settings: PmSettings) -> dict[str, Any]:
    series: Series = result["series"]
    outcome = result["outcome"]
    gate: IndexDay | None = result["index_today"]
    changes = session.scalars(select(PmLabelChange).where(PmLabelChange.item_id == item.id)
                              .order_by(PmLabelChange.effective_date.desc(), PmLabelChange.id.desc()))
    return {
        "item": summary_row(item, result, settings),
        "label_mode": settings.label_mode,
        "round_no": current_round(result["levels"]),
        "levels": [level_row(lv, series) for lv in result["levels"]],
        "ladder": ladder_rows(result),
        "events": [event_row(e, series) for e in reversed(outcome.events)] if outcome else [],
        "prompts": [event_row(e, series) for e in reversed(result["prompts"])],
        "segments": [segment_row(s, series) for s in outcome.segments] if outcome else [],
        "activated_on": outcome.activated_on if outcome else None,
        "peak_close": series.shown(outcome.peak_close) if outcome else None,
        "index_today": None if gate is None else {"symbol": result["main_index"], "label": gate.label,
                                                  "label_name": LABEL_NAMES.get(gate.label),
                                                  "completion": gate.completion, "phase": gate.phase},
        "label_history": [{"label": c.label, "label_name": LABEL_NAMES.get(c.label, c.label), "source": c.source,
                           "effective_date": c.effective_date, "reason": c.stage_reason,
                           "created_by": c.created_by} for c in changes],
        "path": shown_path(result["path"], series),
        "zone_event": event_row(result["zone_event"], series) if result["zone_event"] else None,
        "daily_range": result["daily_range"],
        "auto_base": settings.auto_base,
        "quality": None if result["quality"] is None else {
            "grade": result["quality"].grade, "score": result["quality"].score, "as_of": result["quality"].as_of,
            "dims": result["quality"].dims},
    }


def shown_path(path: dict[str, Any] | None, series: Series) -> dict[str, Any] | None:
    """The path suggestion with its pre-filled prices in qfq."""
    if path is None:
        return None
    levels = {}
    for key, value in path["levels"].items():
        if key == "fraction":
            levels[key] = value
        elif isinstance(value, tuple):
            levels[key] = [series.shown(v) for v in value]
        else:
            levels[key] = series.shown(value)
    return dict(path, levels=levels)


# -- signals and the daily report ---------------------------------------------------------------------

PRIORITY_NAMES = {1: "清仓警报", 2: "减仓", 3: "入场与共振", 4: "提示"}


def sync_signals(session: Session, user_id: int, results: list[tuple[PmItem, dict[str, Any]]],
                 settings: PmSettings) -> list[PmSignal]:
    """Store the events on or after the last evaluated session (once per item
    and dedupe key; a new level version gives new keys) and move the mark."""
    from .db.models import PmSignal

    latest = max((r["series"].latest for _, r in results if r["series"].latest), default=None)
    if latest is None:
        return []
    cutoff = min(settings.evaluated_through or latest, latest)
    new: list[PmSignal] = []
    for item, result in results:
        for row, event in result.get("crossings", []):  # a crossing is final for this version of the sentinel
            row.status, row.crossed_on = "crossed", event.trade_date
        events = (result["outcome"].events if result["outcome"] else []) + result["prompts"]
        events += [e for _, e in result.get("crossings", [])]
        if result.get("zone_event") is not None:
            events.append(result["zone_event"])
        wanted = [e for e in events if e.trade_date >= cutoff]
        if not wanted:
            continue
        known = set(session.scalars(select(PmSignal.dedupe_key).where(PmSignal.item_id == item.id)))
        series: Series = result["series"]
        for event in wanted:
            if event.key in known:
                continue
            known.add(event.key)
            signal = PmSignal(user_id=user_id, item_id=item.id, trade_date=event.trade_date, rule=event.rule,
                              priority=event.priority, message=event.message, dedupe_key=event.key,
                              payload={"close": series.shown(event.close), "completion": event.completion,
                                       "weight": event.weight})
            session.add(signal)
            new.append(signal)
            if event.rule == "base_zone" and settings.auto_base and item.label == LEFT:
                # The only automatic label change (design §11.1): pure arithmetic on the user's own zone.
                set_label(session, item, BASE, source="auto", actor="system", effective=event.trade_date,
                          reason=event.message)
    settings.evaluated_through = latest
    session.flush()
    return new


def report_lines(signals: list[PmSignal], names: dict[int, str]) -> tuple[str, list[str]]:
    """Title and lines of a user's daily report, most urgent first."""
    ordered = sorted(signals, key=lambda s: (s.priority, s.trade_date, s.item_id))
    counts: dict[int, int] = {}
    for s in ordered:
        counts[s.priority] = counts.get(s.priority, 0) + 1
    title = "仓位管家：" + "，".join(f"{PRIORITY_NAMES[p]} {n} 条" for p, n in sorted(counts.items()))
    lines = [f"- 【{PRIORITY_NAMES[s.priority]}】{s.trade_date:%m-%d} {names.get(s.item_id, s.item_id)}：{s.message}"
             for s in ordered[:30]]
    if len(ordered) > 30:
        lines.append(f"- …另有 {len(ordered) - 30} 条")
    return title, lines


def today_signals(session: Session, user_id: int, day: date | None) -> dict[int, list[dict[str, Any]]]:
    """The signals of one session by item (tier 1 of the report)."""
    from .db.models import PmSignal

    out: dict[int, list[dict[str, Any]]] = {}
    if day is None:
        return out
    for signal in session.scalars(select(PmSignal).where(PmSignal.user_id == user_id, PmSignal.trade_date == day)):
        out.setdefault(signal.item_id, []).append({"priority": signal.priority, "rule": signal.rule,
                                                   "message": signal.message})
    return out


TIERS = {1: "今日触发", 2: "临门一脚", 3: "逼近中", 4: "有变化", 5: "其余"}
NEAR, APPROACHING, CHANGED = 0.03, 0.08, 0.10


def report_tiers(rows: list[dict[str, Any]], today: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The five tiers of the daily report (design §11.4); each item in the first it fits.
    ``today``: item id -> the signals of the latest session."""
    tiers: dict[int, list[dict[str, Any]]] = {k: [] for k in TIERS}
    for row in rows:
        distances = [abs(d) for d in [row.get("next_distance")] + [
            s["distance"] for s in row.get("sentinels", []) if s["status"] == "active"] if d is not None]
        nearest = min(distances) if distances else None
        entry = {"id": row["id"], "symbol": row["symbol"], "name": row["name"], "label": row["label"],
                 "star": row["star"], "quality": row["quality"], "distance": nearest,
                 "waiting_for": row["waiting_for"], "completion": row["completion"],
                 "completion_change": row.get("completion_change")}
        if row["id"] in today:
            signals = sorted(today[row["id"]], key=lambda s: s["priority"])
            tiers[1].append(dict(entry, priority=signals[0]["priority"], messages=[s["message"] for s in signals]))
        elif nearest is not None and nearest <= NEAR:
            tiers[2].append(entry)
        elif nearest is not None and nearest <= APPROACHING:
            tiers[3].append(entry)
        elif row.get("completion_change") is not None and abs(row["completion_change"]) >= CHANGED:
            tiers[4].append(entry)
        else:
            tiers[5].append(entry)

    def order(entry: dict[str, Any]) -> tuple:
        score = entry["quality"]["score"] if entry["quality"] and entry["quality"]["score"] is not None else -1
        return (entry.get("priority", 9), -entry["star"], -score, entry["distance"] if entry["distance"] is not None else 9)

    return [{"tier": k, "name": name, "items": sorted(tiers[k], key=order)} for k, name in TIERS.items()]


def top_facts(item: PmItem, result: dict[str, Any]) -> dict[str, Any] | None:
    """For 头部确立 (design §11.5): watching, observing a top neckline, or confirmed,
    with the change since the confirmation and its verdict (a +-1% neutral band)."""
    series: Series = result["series"]
    outcome = result["outcome"]
    top = next((lv for lv in result["levels"] if lv.kind == "top_neckline"
                and lv.round_no == current_round(result["levels"])), None)
    events = outcome.events if outcome else []
    broken = next((e for e in events if e.rule == "top_break"), None)
    close = float(series.close[-1]) if len(series.close) else None
    base = {"id": item.id, "symbol": item.symbol, "name": item.name, "close": series.shown(close),
            "completion": outcome.completion if outcome else None}
    if top is not None and (top.top_mode == "confirmed" or broken is not None):
        day = broken.trade_date if broken is not None else top.effective_date
        at = broken.close if broken is not None else _close_on(series, day)
        change = close / at - 1 if close and at else None
        verdict = None if change is None else ("判对" if change <= -0.01 else "判早" if change >= 0.01 else "中性")
        return dict(base, state="confirmed", confirmed_on=day, confirmed_close=series.shown(at), change=change,
                    verdict=verdict, how="跌破头部颈线" if broken is not None else "立即确认")
    if top is not None:
        return dict(base, state="observing", top_neckline=series.shown(top.price),
                    distance=top.price / close - 1 if close else None)
    if item.label == TOP or (outcome and outcome.completion is not None and outcome.weight > 0
                             and outcome.completion >= result["rule_params"].top_watch):
        return dict(base, state="watching", label=item.label)
    return None


def _close_on(series: Series, day: date) -> float | None:
    for d, c in zip(reversed(series.dates), reversed(series.close)):
        if d <= day:
            return float(c)
    return None


def rounds_of(levels: list[PmLevel]) -> list[tuple[int, Structure, date | None]]:
    """Every round with a neckline and a target, and the session its successor starts."""
    out = []
    numbers = sorted({lv.round_no for lv in levels})
    for k, number in enumerate(numbers):
        structure = structure_of([lv for lv in levels if lv.round_no == number])
        if structure is None:
            continue
        later = [structure_of([lv for lv in levels if lv.round_no == n]) for n in numbers[k + 1:]]
        until = next((s.effective for s in later if s is not None), None)
        out.append((number, structure, until))
    return out


@_shared
def campaign_rows(session: Session, market: MarketQueries, user_id: int) -> list[dict[str, Any]]:
    """The simulated campaign ledger (design §11.5): every round of every item replayed
    up to the next round's start, with its trades and return (prices in qfq)."""
    settings = user_settings(session, user_id)
    rules, stage = rule_params(settings), stage_params(settings)
    items = list(session.scalars(select(PmItem).where(PmItem.user_id == user_id).order_by(PmItem.id)))
    cache: dict = {}
    rows = []
    for item in items:
        levels = list(session.scalars(select(PmLevel).where(PmLevel.item_id == item.id, PmLevel.status == "active")))
        rounds = rounds_of(levels)
        if not rounds:
            continue
        series = load_series(market, item.symbol, item.kind)
        labels = label_history(session, item.id)
        main = item.primary_index or default_index(market, item)
        if main is not None and main not in cache:
            cache[main] = index_days(session, market, user_id, main, stage, rules)
        for number, structure, until in rounds:
            cut = len(series.dates) if until is None else sum(1 for d in series.dates if d < until)
            if cut == 0:
                continue
            outcome = replay(series.dates[:cut], series.close[:cut], structure, labels, cache.get(main), rules)
            if not outcome.segments:
                continue
            rows.append({"item_id": item.id, "symbol": item.symbol, "name": item.name, "round_no": number,
                         "archived": item.archived_at is not None, "until": until,
                         "segments": [segment_row(seg, series) for seg in outcome.segments],
                         "open": outcome.segments[-1]["exit_date"] is None,
                         "round_return": outcome.round_return, "script_return": outcome.script_return})
    return rows


@_shared
def evaluate_user(session: Session, market: MarketQueries, user_id: int) -> tuple[list[tuple[PmItem, dict]],
                                                                                    PmSettings]:
    settings = user_settings(session, user_id)
    items = list(session.scalars(select(PmItem).where(PmItem.user_id == user_id, PmItem.archived_at.is_(None))
                                 .order_by(PmItem.id)))
    cache: dict = {}
    return [(item, evaluate(session, market, item, settings, cache)) for item in items], settings


@_shared
def index_band(session: Session, market: MarketQueries, user_id: int, settings: PmSettings) -> list[dict[str, Any]]:
    """The main indices at the top of the board, as the entry gate sees them:
    the user's label of an index in the library, else its stage view."""
    stage = stage_params(settings)
    rows = []
    for symbol in MAIN_INDICES:
        series = load_series(market, symbol, "index")
        if not series.dates:
            continue
        view = stage_view(series.close, stage, is_index=True)
        item = session.scalar(select(PmItem).where(PmItem.user_id == user_id, PmItem.symbol == symbol,
                                                   PmItem.archived_at.is_(None)))
        own = item is not None and item.label != UNDECIDED
        label = item.label if own else STAGE_TO_LABEL.get(view["stage"], UNDECIDED)
        close = float(series.close[-1])
        previous = float(series.close[-2]) if len(series.close) > 1 else None
        rows.append({"symbol": symbol, "name": INDEX_NAMES.get(symbol, symbol), "latest": series.latest,
                     "close": close, "change": close / previous - 1 if previous else None,
                     "label": label, "label_name": LABEL_NAMES.get(label, label),
                     "label_source": "manual" if own else "system", "item_id": item.id if item else None,
                     "stage": shown_view(view, series) if settings.label_mode == "suggest" else None})
    return rows


@_shared
def run_daily(session: Session, market: MarketQueries) -> list[dict[str, Any]]:
    """The evening job: evaluate every user's library and store the new
    signals; for a user who asked for the daily push, one event per session
    with the counts.  Events and pushes are shared by everybody while the
    library is not, so an event names no security (the board has the
    details).  Commits per user; one user's failure does not stop the others."""
    import logging

    from .db.models import Event, User

    log = logging.getLogger(__name__)
    out: list[dict[str, Any]] = []
    user_ids = sorted(set(session.scalars(select(PmItem.user_id).where(PmItem.archived_at.is_(None)))))
    for user_id in user_ids:
        try:
            results, settings = evaluate_user(session, market, user_id)
            new = sync_signals(session, user_id, results, settings)
            latest, event_id = settings.evaluated_through, None
            if settings.push_daily and latest is not None:
                today = today_signals(session, user_id, latest)
                tiers = report_tiers([summary_row(item, result, settings) for item, result in results], today)
                counts = {t["name"]: len(t["items"]) for t in tiers if t["tier"] <= 3 and t["items"]}
                if today or counts.get(TIERS[2]):
                    event_id = f"pm-{user_id}-{latest:%Y%m%d}"
                    if session.get(Event, event_id) is None:
                        user = session.get(User, user_id)
                        urgent = any(s["priority"] <= 2 for signals in today.values() for s in signals)
                        title = "仓位管家：" + " · ".join(f"{name} {n}" for name, n in counts.items())
                        session.add(Event(event_id=event_id, category="position",
                                          level="warning" if urgent else "info",
                                          title=f"{title}（{user.display_name or user.username}）",
                                          body="详情只在本人的仓位看板中显示", trade_date=latest,
                                          action_hint="打开“看盘与仓位 → 仓位看板”"))
            session.commit()
            out.append({"user_id": user_id, "items": len(results), "new_signals": len(new), "latest": latest,
                        "event": event_id, "error": None})
        except Exception as exc:  # reported per user; the others still run
            session.rollback()
            log.exception("position manager: user %s failed", user_id)
            out.append({"user_id": user_id, "items": None, "new_signals": None, "latest": None, "event": None,
                        "error": f"{type(exc).__name__}: {exc}"})
    return out
