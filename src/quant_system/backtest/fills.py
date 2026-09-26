"""Fill feasibility and pricing at the open (ADR-002).

The execution day's full bar may only *restrict* a fill (lock detection,
participation cap, whether the price band is in force); it never feeds
sizing or selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

import numpy as np

from ..domain.money import half_up
from ..domain.rules import MarketRules
from .market_data import RISK_DELISTING, RISK_NAMES, MarketData

ONE_PRICE_LOCK_MOVE = 0.045  # a one-price bar moving >= 4.5% is treated as locked


def reference_price_fen(data: MarketData, i: int, j: int) -> int | None:
    """Previous close carried to session ``i`` through any ex-rights step.

    ref = close[last bar before i] * hfq[that bar] / hfq[i]; None when the
    symbol has no earlier bar (listing day).
    """
    last = int(data.last_bar[i - 1, j]) if i > 0 else -1
    if last < 0:
        return None
    close = int(data.close[last, j])
    before, now = data.hfq[last, j], data.hfq[i, j]
    if not (np.isfinite(before) and np.isfinite(now)) or now <= 0:
        return close
    return max(1, int(np.floor(close * before / now + 0.5)))


def mark_price_fen(data: MarketData, i: int, j: int) -> int | None:
    """Close on a bar day; otherwise the last close carried through ex-rights."""
    if data.has_bar[i, j]:
        return int(data.close[i, j])
    last = int(data.last_bar[i, j])
    if last < 0:
        return None
    close = int(data.close[last, j])
    before, now = data.hfq[last, j], data.hfq[i, j]
    if not (np.isfinite(before) and np.isfinite(now)) or now <= 0:
        return close
    return max(1, int(np.floor(close * before / now + 0.5)))


@dataclass
class OpenState:
    has_bar: bool
    open_fen: int = 0
    up_fen: int | None = None
    down_fen: int | None = None
    band_in_force: bool = False
    lock_buy: str | None = None  # rule id when buying is blocked
    lock_sell: str | None = None
    flags: list[str] = field(default_factory=list)


def open_state(data: MarketData, rules: MarketRules, i: int, j: int) -> OpenState:
    if not data.has_bar[i, j]:
        return OpenState(has_bar=False)
    day: date = data.sessions[i]
    board = str(data.board[j])
    o, h, l, c = (int(data.open[i, j]), int(data.high[i, j]), int(data.low[i, j]), int(data.close[i, j]))
    state = OpenState(has_bar=True, open_fen=o)
    reference = reference_price_fen(data, i, j)
    if reference is None:
        state.flags.append("NO_REFERENCE_PRICE")  # listing day: no band
        return state
    known = bool(data.risk_known[j])
    code = int(data.risk[i, j]) if known else 0
    risk = RISK_NAMES.get(code, "normal")
    list_index = int(data.list_index[j])
    since_listing = i - list_index if list_index >= 0 else None
    no_limit = rules.is_no_limit_session(board, data.list_date[j], since_listing)
    if code == RISK_DELISTING and i > 0 and int(data.risk[i - 1, j]) != RISK_DELISTING:
        no_limit = no_limit or rules.delisting_first_session_unlimited(day)
    up, down = rules.limit_prices(reference, rules.limit_ratio(board, risk, day))
    state.up_fen, state.down_fen = up, down
    if no_limit:
        state.flags.append("NO_LIMIT_SESSION")
        return state
    tick = rules.tick_fen
    state.band_in_force = l >= down - tick and h <= up + tick
    if not state.band_in_force:
        state.flags.append("LIMIT_BAND_NOT_IN_FORCE")
    elif o >= up:
        state.lock_buy = "LIMIT_UP_AT_OPEN"
    elif o <= down:
        state.lock_sell = "LIMIT_DOWN_AT_OPEN"
    if o == h == l == c:
        move = c / reference - 1
        if move >= ONE_PRICE_LOCK_MOVE and state.lock_buy is None:
            state.lock_buy = "ONE_PRICE_LIMIT_UP"
        if move <= -ONE_PRICE_LOCK_MOVE and state.lock_sell is None:
            state.lock_sell = "ONE_PRICE_LIMIT_DOWN"
    if not known:
        inferred = rules.inferred_risk_ratio(board, day)
        if inferred is not None:
            up5, down5 = rules.limit_prices(reference, inferred)
            if o == up5 and o == h and state.lock_buy is None:
                state.lock_buy = "LIMIT_LOCK_INFERRED_ST"
            if o == down5 and o == l and state.lock_sell is None:
                state.lock_sell = "LIMIT_LOCK_INFERRED_ST"
    return state


def fill_price_fen(side: str, state: OpenState, slippage: Fraction) -> int:
    """Open price with slippage against us, clamped to the price band."""
    if side == "buy":
        price = half_up(Fraction(state.open_fen) * (1 + slippage))
        if state.band_in_force and state.up_fen is not None:
            price = min(price, state.up_fen)
    else:
        price = half_up(Fraction(state.open_fen) * (1 - slippage))
        if state.band_in_force and state.down_fen is not None:
            price = max(price, state.down_fen)
    return max(price, 1)
