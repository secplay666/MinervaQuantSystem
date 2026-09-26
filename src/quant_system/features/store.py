"""Factor cache and the per-rebalance factor panel.

Raw factor values are cached under ``data/features/<key>/`` where the key
covers the research-data fingerprint, the factor definition, the rows and
the source of every module that computes factors (not the git sha, which
misses uncommitted edits).  Cross-sectional processing is cheap and is
redone on every load.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..backtest.view import LookAheadError
from ..research.data import ResearchData
from .context import FactorContext
from .processing import ProcessingSpec, process_panel
from .registry import FactorSpec, compute, get

LOGGER = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CODE_DIRECTORIES = ("features", "fundamentals")
CODE_FILES = ("research/data.py", "universe/liquidity.py")


def code_hash() -> str:
    digest = hashlib.sha256()
    files = sorted(p for d in CODE_DIRECTORIES for p in (PACKAGE_ROOT / d).glob("*.py"))
    files += [PACKAGE_ROOT / name for name in CODE_FILES]
    for path in files:
        digest.update(path.relative_to(PACKAGE_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class FactorCache:
    """``root=None`` disables caching (tests, one-off runs)."""

    def __init__(self, root: Path | None) -> None:
        self.root = Path(root) if root is not None else None
        self._code = code_hash()
        self.hits = self.misses = 0

    def key(self, research_fp: str, spec: FactorSpec, rows: np.ndarray) -> str:
        payload = [research_fp, spec.definition_hash(), hashlib.sha256(rows.tobytes()).hexdigest(), self._code]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:32]

    def raw(self, spec: FactorSpec, ctx: FactorContext, rows: np.ndarray, research_fp: str) -> np.ndarray:
        rows = np.asarray(rows, dtype=np.int64)
        if self.root is None:
            return compute(spec, ctx, rows)
        directory = self.root / self.key(research_fp, spec, rows)
        path = directory / "raw.npy"
        if path.exists():
            self.hits += 1
            return np.load(path)
        self.misses += 1
        values = compute(spec, ctx, rows)
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / "raw.tmp.npy"
        np.save(temp, values)
        temp.replace(path)
        (directory / "manifest.json").write_text(json.dumps({
            "factor": spec.id, "version": spec.version, "definition_hash": spec.definition_hash(),
            "research_fingerprint": research_fp, "code_hash": self._code, "rows": len(rows),
            "shape": list(values.shape)}, indent=1), encoding="utf-8")
        return values


@dataclass
class FactorPanel:
    """Processed factor values at computed session rows.

    ``at(view)`` returns the cross-section of ``view.t`` only (read-only),
    so a strategy reading factors through it cannot see any other row.
    """

    rows: np.ndarray
    symbols: np.ndarray
    values: dict[str, np.ndarray]
    raw: dict[str, np.ndarray] = field(default_factory=dict)
    universe: np.ndarray | None = None

    def __post_init__(self) -> None:
        self._index = {int(t): k for k, t in enumerate(self.rows)}
        for panel in (*self.values.values(), *self.raw.values()):
            panel.setflags(write=False)
        if self.universe is not None:
            self.universe.setflags(write=False)

    def row(self, t: int) -> int:
        try:
            return self._index[int(t)]
        except KeyError:
            raise LookAheadError(f"factors were not computed for session row {t}") from None

    def at(self, view) -> dict[str, np.ndarray]:
        k = self.row(view.t)
        return {factor_id: panel[k] for factor_id, panel in self.values.items()}

    def raw_at(self, view) -> dict[str, np.ndarray]:
        k = self.row(view.t)
        return {factor_id: panel[k] for factor_id, panel in self.raw.items()}

    def universe_at(self, view) -> np.ndarray:
        if self.universe is None:
            raise ValueError("no universe stored in this panel")
        return self.universe[self.row(view.t)]


def build_factor_panel(research: ResearchData, rows: np.ndarray, factor_ids: list[str], universe: np.ndarray,
                       processing: ProcessingSpec, cache: FactorCache | None = None,
                       ctx: FactorContext | None = None, keep_raw: bool = False) -> FactorPanel:
    rows = np.asarray(rows, dtype=np.int64)
    ctx = ctx or FactorContext(research)
    cache = cache or FactorCache(None)
    fingerprint = research.fingerprint() if cache.root is not None else ""
    industry = research.industry[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        cap = ctx.float_mcap()[rows]
        ln_size = np.where(cap > 0, np.log(cap), np.nan)
    values, raws = {}, {}
    for factor_id in factor_ids:
        spec = get(factor_id)
        raw = cache.raw(spec, ctx, rows, fingerprint)
        values[factor_id] = process_panel(raw, universe, industry, ln_size, spec, processing)
        if keep_raw:
            raws[factor_id] = raw
    LOGGER.info("factor panel: %d factors x %d rows (cache hits %d, misses %d)", len(factor_ids), len(rows),
                cache.hits, cache.misses)
    return FactorPanel(rows=rows, symbols=research.symbols, values=values, raw=raws, universe=universe.copy())
