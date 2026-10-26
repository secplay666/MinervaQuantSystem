"""Shareholder-count factor (股东户数), a candidate (ADR-009).

Holders falling means the shares gather in fewer hands; the study of 2026-10-25
(docs/research/accumulation-distribution-2026-10.md §2.2) found the stocks
whose count fell most beat those whose count rose most by about 2.6% over the
next 60 sessions.  Without shareholder counts the factor is all missing.
"""

from __future__ import annotations

import numpy as np

from .context import FactorContext
from .registry import factor


@factor("holders_qoq", "holders", "股东户数季度变化（最新季末 / 上一季末 - 1）", -1, 1, requires=("holders",),
        candidate=True)
def holders_qoq(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    store = ctx.research.holders
    if store is None:
        return np.full((len(rows), ctx.shape[1]), np.nan)
    return store.asof(rows)
