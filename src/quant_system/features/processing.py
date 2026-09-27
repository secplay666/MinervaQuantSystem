"""Cross-sectional factor processing inside the research universe.

Per session row: MAD winsorization -> OLS neutralization on industry
dummies (+ log float market cap) -> z-score -> factor direction applied,
so that for every processed factor higher means "expected better".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .registry import FactorSpec

MAD_SCALE = 1.4826  # MAD -> standard deviation under normality
RESIDUAL_TOL = 1e-10  # relative to max |x|; rounding noise is ~1e-15


@dataclass(frozen=True)
class ProcessingSpec:
    winsorize_k: float = 3.0
    neutralize: bool = True
    min_names: int = 30

    @classmethod
    def from_payload(cls, payload: dict | None) -> "ProcessingSpec":
        payload = dict(payload or {})
        return cls(winsorize_k=float(payload.get("winsorize_k", 3.0)),
                   neutralize=bool(payload.get("neutralize", True)),
                   min_names=int(payload.get("min_names", 30)))


def winsorize(x: np.ndarray, k: float) -> np.ndarray:
    median = np.median(x)
    mad = np.median(np.abs(x - median)) * MAD_SCALE
    if mad > 0:
        low, high = median - k * mad, median + k * mad
    else:  # a mass point at the median: fall back to percentile clipping
        low, high = np.percentile(x, [1, 99])
    return np.clip(x, low, high)


def neutralize(x: np.ndarray, industry: np.ndarray | None, ln_size: np.ndarray | None) -> np.ndarray:
    """Residual of x on industry dummies (the intercept) and ln size."""
    columns = []
    if industry is not None:
        groups, inverse = np.unique(industry, return_inverse=True)
        dummies = np.zeros((len(x), len(groups)))
        dummies[np.arange(len(x)), inverse] = 1.0
        columns.append(dummies)
    else:
        columns.append(np.ones((len(x), 1)))
    if ln_size is not None:
        centered = ln_size - ln_size.mean()
        columns.append(centered[:, None])
    design = np.hstack(columns)
    beta, *_ = np.linalg.lstsq(design, x, rcond=None)
    residual = x - design @ beta
    # Names the fit reproduces exactly (alone in their industry, or a group of
    # identical values) have a zero residual in theory; lstsq leaves BLAS
    # rounding noise whose sign differs between CPUs and would decide their rank.
    residual[np.abs(residual) <= RESIDUAL_TOL * np.abs(x).max()] = 0.0
    return residual


def zscore(x: np.ndarray) -> np.ndarray:
    std = x.std()
    return (x - x.mean()) / std if std > 0 else np.zeros_like(x)


def process_row(raw: np.ndarray, mask: np.ndarray, industry: np.ndarray, ln_size: np.ndarray,
                spec: FactorSpec, processing: ProcessingSpec) -> np.ndarray:
    out = np.full(raw.shape, np.nan)
    use_size = processing.neutralize and "size" in spec.neutralize
    valid = mask & np.isfinite(raw)
    if use_size:
        valid &= np.isfinite(ln_size)
    if valid.sum() < processing.min_names:
        return out
    x = winsorize(raw[valid], processing.winsorize_k)
    if processing.neutralize and spec.neutralize:
        x = neutralize(x, industry[valid] if "industry" in spec.neutralize else None,
                       ln_size[valid] if use_size else None)
    out[valid] = spec.direction * zscore(x)
    return out


def process_panel(raw: np.ndarray, universe: np.ndarray, industry: np.ndarray, ln_size: np.ndarray,
                  spec: FactorSpec, processing: ProcessingSpec) -> np.ndarray:
    """Processed values [R, N] (float64; NaN outside the universe or missing)."""
    out = np.full(raw.shape, np.nan)
    for k in range(raw.shape[0]):
        out[k] = process_row(raw[k], universe[k], industry[k], ln_size[k], spec, processing)
    return out
