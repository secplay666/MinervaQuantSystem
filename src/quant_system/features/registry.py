"""Factor definitions and registry.

A factor maps (context, rows) -> raw values [R, N] for the requested session
rows, using only data up to each row.  Cross-sectional processing
(winsorize, neutralize, standardize, direction) is applied afterwards by
``features.processing`` inside the research universe.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .context import FactorContext

Compute = Callable[[FactorContext, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class FactorSpec:
    id: str
    family: str
    description: str
    direction: int          # +1: higher is better; -1: lower is better
    window: int             # sessions of history needed
    compute: Compute
    neutralize: tuple[str, ...] = ("industry", "size")
    requires: tuple[str, ...] = ()   # e.g. ("fundamentals",)
    version: str = "1"

    def __post_init__(self) -> None:
        if self.direction not in (1, -1):
            raise ValueError(f"{self.id}: direction must be +1 or -1")
        unknown = set(self.neutralize) - {"industry", "size"}
        if unknown:
            raise ValueError(f"{self.id}: unknown neutralization {sorted(unknown)}")

    def definition_hash(self) -> str:
        """Changes whenever the definition or its compute function changes."""
        payload = {
            "id": self.id, "family": self.family, "direction": self.direction, "window": self.window,
            "neutralize": list(self.neutralize), "requires": list(self.requires), "version": self.version,
            "compute": inspect.getsource(self.compute),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


_REGISTRY: dict[str, FactorSpec] = {}
FAMILIES = ("value", "quality", "growth", "momentum", "volatility", "liquidity", "size", "technical")


def register(spec: FactorSpec) -> FactorSpec:
    if spec.id in _REGISTRY:
        raise ValueError(f"factor {spec.id!r} registered twice")
    if spec.family not in FAMILIES:
        raise ValueError(f"{spec.id}: unknown family {spec.family!r}")
    _REGISTRY[spec.id] = spec
    return spec


def factor(id: str, family: str, description: str, direction: int, window: int, **options):
    """Decorator registering ``compute(ctx, rows)`` as a factor."""
    def decorator(compute: Compute) -> Compute:
        register(FactorSpec(id=id, family=family, description=description, direction=direction,
                            window=window, compute=compute, **options))
        return compute

    return decorator


def _load_builtin() -> None:
    from . import fundamental, technical  # noqa: F401  (registration side effects)


def get(factor_id: str) -> FactorSpec:
    _load_builtin()
    try:
        return _REGISTRY[factor_id]
    except KeyError:
        raise KeyError(f"unknown factor {factor_id!r}; known: {sorted(_REGISTRY)}") from None


def all_factors(family: str | None = None) -> list[FactorSpec]:
    _load_builtin()
    specs = [spec for spec in _REGISTRY.values() if family is None or spec.family == family]
    return sorted(specs, key=lambda spec: (FAMILIES.index(spec.family), spec.id))


def compute(spec: FactorSpec, ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    """Raw factor values [R, N] (float64; NaN = unavailable)."""
    rows = np.asarray(rows, dtype=np.int64)
    values = np.asarray(spec.compute(ctx, rows), dtype=np.float64)
    if values.shape != (len(rows), ctx.shape[1]):
        raise ValueError(f"{spec.id}: expected shape {(len(rows), ctx.shape[1])}, got {values.shape}")
    values[~np.isfinite(values)] = np.nan
    return values
