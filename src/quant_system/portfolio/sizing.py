"""Convert target weights into lot-legal order quantities.

Sizing uses pre-open reference prices (the previous close carried through
any corporate action on the execution day), never the execution day's bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.rules import LotRule


@dataclass(frozen=True)
class SizingPolicy:
    cash_buffer: float = 0.005
    band_rel: float = 0.25
    min_lot_policy: str = "skip"  # skip names whose minimum lot exceeds the target


@dataclass(frozen=True)
class SizedOrder:
    symbol: str
    side: str
    quantity: int
    rank: int
    reason: str


@dataclass
class SizingResult:
    orders: list[SizedOrder] = field(default_factory=list)
    skipped: list[tuple[str, float, str]] = field(default_factory=list)  # (symbol, weight, reason)
    within_band: list[str] = field(default_factory=list)


def size_orders(
    weights: dict[str, float],
    ranks: dict[str, int],
    holdings: dict[str, int],
    prices_fen: dict[str, int | None],
    nav_fen: int,
    lot_rules: dict[str, LotRule],
    policy: SizingPolicy,
    symbols: set[str] | None = None,
) -> SizingResult:
    """Orders moving ``holdings`` towards ``weights``; sells first, then buys
    by rank.  ``symbols`` restricts sizing to a subset (retries)."""
    result = SizingResult()
    investable = nav_fen * (1 - policy.cash_buffer)
    universe = sorted(set(weights) | set(holdings)) if symbols is None else sorted(symbols)
    sells: list[SizedOrder] = []
    buys: list[SizedOrder] = []
    for symbol in universe:
        current = holdings.get(symbol, 0)
        weight = weights.get(symbol, 0.0)
        lot = lot_rules[symbol]
        if weight <= 0:
            if current > 0:
                sells.append(SizedOrder(symbol, "sell", current, 0, "exit"))
            continue
        price = prices_fen.get(symbol)
        if not price:
            result.skipped.append((symbol, weight, "no_reference_price"))
            continue
        target_value = weight * investable
        if current > 0 and abs(current * price - target_value) <= policy.band_rel * target_value:
            result.within_band.append(symbol)
            continue
        target_quantity = int(target_value // price)
        delta = target_quantity - current
        if delta > 0:
            quantity = lot.round_buy(delta)
            if quantity == 0:
                if current == 0:
                    result.skipped.append((symbol, weight, "min_lot_exceeds_target"))
                continue
            buys.append(SizedOrder(symbol, "buy", quantity, ranks.get(symbol, 0), "rebalance"))
        elif delta < 0:
            quantity = lot.round_sell(-delta, current)
            if quantity > 0:
                sells.append(SizedOrder(symbol, "sell", quantity, ranks.get(symbol, 0), "rebalance"))
    result.orders = sells + sorted(buys, key=lambda order: (order.rank, order.symbol))
    return result
