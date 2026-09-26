"""Factor risk model for the optimizer (stage 3 P5; ADR-007).

Exposures (fixed for the month after each month-end row):
    Shenwan L1 industry dummies (they act as the intercept) + style factors
    standardized cap-weighted within the universe, missing values set to 0.
Daily factor returns:
    weighted least squares of bar-to-bar returns on the exposures of the
    latest month-end *before* the day, weights sqrt(float market cap).
At a decision row t (only days <= t are used):
    factor covariance  = EWMA of daily factor returns (half-life 90) x 21
    specific variance  = EWMA of squared residuals (half-life 60, at least
                         40 observations) x 21, shrunk towards the mean of
                         the name's size decile with weight n / (n + 60).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ..features.context import FactorContext
from ..features.processing import winsorize
from ..features.registry import compute, get

LOGGER = logging.getLogger(__name__)
HORIZON_DAYS = 21
DEFAULT_STYLES = ("ln_float_mcap", "mom_12_1", "ivol_60", "turn_20", "bp", "ep_ttm", "ni_yoy", "leverage")


@dataclass(frozen=True)
class RiskModelSpec:
    styles: tuple[str, ...] = DEFAULT_STYLES
    include_beta: bool = True
    factor_half_life: int = 90
    specific_half_life: int = 60
    min_specific_obs: int = 40
    shrink_obs: int = 60
    window: int = 504
    ridge: float = 1e-8

    @classmethod
    def from_payload(cls, payload: dict | None) -> "RiskModelSpec":
        payload = dict(payload or {})
        if "styles" in payload:
            payload["styles"] = tuple(payload["styles"])
        return cls(**payload)


@dataclass
class RiskSnapshot:
    columns: np.ndarray          # symbol columns covered (the estimation universe at the month-end)
    exposures: np.ndarray        # [n, K]
    factor_cov: np.ndarray       # [K, K], monthly
    specific_var: np.ndarray     # [n], monthly
    factor_names: list[str] = field(default_factory=list)

    def total_variance(self) -> np.ndarray:
        return np.einsum("ik,kl,il->i", self.exposures, self.factor_cov, self.exposures) + self.specific_var


def _cap_weighted_z(raw: np.ndarray, weights: np.ndarray) -> np.ndarray:
    out = np.zeros(raw.shape)
    valid = np.isfinite(raw)
    if valid.sum() < 10:
        return out
    x = winsorize(raw[valid], 3.0)
    w = weights[valid] / weights[valid].sum()
    mean = float((w * x).sum())
    std = float(np.sqrt((w * (x - mean) ** 2).sum()))
    out[valid] = (x - mean) / std if std > 0 else 0.0
    return out


class RiskModel:
    """Built once over the calendar; ``at(t)`` is causal in t."""

    def __init__(self, research, month_rows: np.ndarray, universe: np.ndarray, spec: RiskModelSpec,
                 ctx: FactorContext | None = None) -> None:
        self.research = research
        self.spec = spec
        self.ctx = ctx or FactorContext(research)
        self.month_rows = np.asarray(month_rows, dtype=np.int64)
        self.universe = universe
        codes = research.industry_codes
        self.factor_names = [f"ind_{code}" for code in codes] + ["ind_unclassified"] + list(spec.styles)
        if spec.include_beta:
            self.factor_names.append("beta")
        self._estimate()

    # -- exposures ----------------------------------------------------------------

    def _exposures(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        """(columns, X) at month-end row index k."""
        t = self.month_rows[k]
        columns = np.flatnonzero(self.universe[k])
        cap = self.ctx.float_mcap()[t, columns]
        weights = np.where(np.isfinite(cap) & (cap > 0), cap, np.nanmedian(cap))
        industry = self.research.industry[t, columns]
        n_ind = len(self.research.industry_codes)
        X = np.zeros((len(columns), len(self.factor_names)))
        X[np.arange(len(columns)), np.where(industry >= 0, industry, n_ind)] = 1.0
        for s, raw in enumerate(self._style_raw):
            X[:, n_ind + 1 + s] = _cap_weighted_z(raw[k, columns], weights)
        if self.spec.include_beta:
            X[:, -1] = _cap_weighted_z(self._beta[k, columns], weights)
        return columns, X

    def _estimate(self) -> None:
        rows = self.month_rows
        self._style_raw = [compute(get(style), self.ctx, rows) for style in self.spec.styles]
        if self.spec.include_beta:
            from ..features.rolling import rolling_cov_corr

            cov, _, _, var_m = rolling_cov_corr(self.ctx.returns(), self.ctx.market_returns()[:, None], 250, 120)
            with np.errstate(invalid="ignore", divide="ignore"):
                self._beta = (cov / var_m)[rows]
        returns = self.ctx.returns()
        cap = self.ctx.float_mcap()
        T = returns.shape[0]
        K = len(self.factor_names)
        self.factor_returns = np.full((T, K), np.nan)
        self.residuals = np.full(returns.shape, np.nan, dtype=np.float32)  # [T, N]
        exposures = [self._exposures(k) for k in range(len(rows))]
        self._month_exposures = exposures
        for k in range(len(rows)):
            columns, X = exposures[k]
            first = rows[k] + 1
            last = rows[k + 1] if k + 1 < len(rows) else T - 1
            for d in range(first, last + 1):
                r = returns[d, columns]
                valid = np.isfinite(r)
                if valid.sum() < K + 10:
                    continue
                w = np.sqrt(np.where(np.isfinite(cap[d - 1, columns]) & (cap[d - 1, columns] > 0),
                                     cap[d - 1, columns], 1.0))[valid]
                Xv, rv = X[valid], np.clip(r[valid], -0.25, 0.25)
                sw = np.sqrt(w)
                # Drop dummy columns without members (empty industries) from this regression.
                used = np.flatnonzero(np.abs(Xv).sum(axis=0) > 0)
                beta, *_ = np.linalg.lstsq(Xv[:, used] * sw[:, None], rv * sw, rcond=None)
                f = np.zeros(K)
                f[used] = beta
                self.factor_returns[d] = f
                self.residuals[d, columns[valid]] = rv - Xv[:, used] @ beta

    # -- snapshot -------------------------------------------------------------------

    def at(self, t: int) -> RiskSnapshot:
        """Risk model for a decision at the close of row t (month-end row)."""
        position = int(np.searchsorted(self.month_rows, t, side="right") - 1)
        if position < 0:
            raise ValueError(f"no month-end exposures at or before row {t}")
        columns, X = self._month_exposures[position]
        start = max(0, t - self.spec.window + 1)
        days = np.arange(start, t + 1)
        F = self._ewma_cov(self.factor_returns[days], self.spec.factor_half_life)
        F = (F + self.spec.ridge * np.eye(len(F))) * HORIZON_DAYS
        specific = self._specific(columns, days, t)
        return RiskSnapshot(columns=columns, exposures=X, factor_cov=F, specific_var=specific,
                            factor_names=list(self.factor_names))

    @staticmethod
    def _ewma_cov(values: np.ndarray, half_life: int) -> np.ndarray:
        valid = np.isfinite(values).all(axis=1)
        x = values[valid]
        if len(x) < 2:
            return np.eye(values.shape[1]) * 1e-4
        decay = 0.5 ** (1.0 / half_life)
        weights = decay ** np.arange(len(x))[::-1]
        weights /= weights.sum()
        mean = (weights[:, None] * x).sum(axis=0)
        centered = x - mean
        return (weights[:, None] * centered).T @ centered

    def _specific(self, columns: np.ndarray, days: np.ndarray, t: int) -> np.ndarray:
        decay = 0.5 ** (1.0 / self.spec.specific_half_life)
        block = self.residuals[days][:, columns].astype(np.float64)
        present = np.isfinite(block)
        w = (decay ** (t - days))[:, None]
        total = (w * np.where(present, block * block, 0.0)).sum(axis=0)
        weight = (w * present).sum(axis=0)
        count = present.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = np.where(weight > 0, total / weight, np.nan)
        cap = self.ctx.float_mcap()[t, columns]
        deciles = np.digitize(np.nan_to_num(np.log(np.where(cap > 0, cap, np.nan)), nan=0.0),
                              np.nanquantile(np.log(np.where(cap > 0, cap, np.nan)), np.linspace(0.1, 0.9, 9)))
        prior = np.array([np.nanmedian(raw[deciles == g]) if np.isfinite(raw[deciles == g]).any() else np.nanmedian(raw)
                          for g in deciles])
        shrink = count / (count + self.spec.shrink_obs)
        blended = np.where(np.isfinite(raw) & (count >= self.spec.min_specific_obs),
                           shrink * raw + (1 - shrink) * prior, prior)
        floor = np.nanquantile(blended, 0.05) if np.isfinite(blended).any() else 1e-4
        return np.maximum(np.nan_to_num(blended, nan=floor), floor) * HORIZON_DAYS
