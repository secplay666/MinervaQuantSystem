"""Money in integer fen (0.01 CNY) with exchange-style half-up rounding.

A-share prices, fees and cash are all whole fen, so integer arithmetic makes
accounting identities exact and comparisons against limit prices unambiguous.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Union

Rate = Fraction
Number = Union[int, float, str, Fraction]

FEN_PER_YUAN = 100


def rate(value: Number) -> Fraction:
    """Parse a rate from a decimal string (preferred) or number, exactly."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(repr(value))
    return Fraction(str(value))


def half_up(value: Fraction) -> int:
    """Round a non-negative rational half-up to an integer."""
    if value < 0:
        return -half_up(-value)
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def mul_half_up(amount: int, factor: Fraction) -> int:
    return half_up(Fraction(amount) * factor)


def yuan_to_fen(value: Number) -> int:
    return half_up(rate(value) * FEN_PER_YUAN)


def price_to_fen(price: float) -> int:
    """Vendor prices are floats on a 0.01 grid; snap to the nearest fen."""
    return int(round(price * FEN_PER_YUAN))


def fen_to_yuan(amount: int) -> float:
    return amount / FEN_PER_YUAN
