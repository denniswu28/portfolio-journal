"""Lightweight OLS factor / risk model.

Regresses each asset's excess returns on a small set of free yfinance factor proxies
(market, semis, power, gold, rates, ex-US) to get betas, specific risk, and a
portfolio variance decomposition. Reuses the optimizer's covariance-based risk
contribution so factor analysis and ERC rebalancing speak the same language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.portfolio.optimizer import risk_contribution_pct

# Default factor proxies — all tickers already referenced by the sleeve universe.
DEFAULT_FACTOR_PROXIES = {
    "MKT": "SPY",
    "SEMI": "SMH",
    "POWER": "GRID",
    "GOLD": "GLDM",
    "RATES": "IEF",
    "EXUS": "VXUS",
}


@dataclass
class FactorModel:
    """Per-asset factor betas plus risk decomposition inputs."""

    exposures: pd.DataFrame      # index=asset, columns=factor (betas)
    alpha: pd.Series             # per-asset intercept (per-period)
    r_squared: pd.Series         # per-asset regression fit
    specific_var: pd.Series      # residual (idiosyncratic) variance per period
    factor_cov: pd.DataFrame     # factor return covariance per period
    factors: List[str] = field(default_factory=list)
    periods_per_year: int = 252


def build_factor_model(
    asset_returns: pd.DataFrame,
    factor_returns: pd.DataFrame,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> FactorModel:
    """Fit per-asset OLS betas on factor returns (with intercept).

    Returns and covariances are kept per-period; annualize downstream using
    ``periods_per_year``.
    """
    asset_returns = asset_returns.dropna(how="all")
    factor_returns = factor_returns.reindex(asset_returns.index)
    aligned = pd.concat([asset_returns, factor_returns], axis=1).dropna()
    if aligned.empty:
        raise ValueError("No overlapping observations between assets and factors.")

    factors = list(factor_returns.columns)
    rf_per_period = risk_free_rate / periods_per_year
    factor_excess = aligned[factors] - rf_per_period
    design = np.column_stack([np.ones(len(aligned)), factor_excess.values])

    betas: Dict[str, list] = {}
    alphas: Dict[str, float] = {}
    r2: Dict[str, float] = {}
    specific: Dict[str, float] = {}

    for asset in asset_returns.columns:
        y = (aligned[asset] - rf_per_period).values
        coef, _residuals, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
        fitted = design @ coef
        resid = y - fitted
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        alphas[asset] = float(coef[0])
        betas[asset] = [float(b) for b in coef[1:]]
        r2[asset] = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        specific[asset] = float(np.var(resid, ddof=1)) if len(resid) > 1 else 0.0

    exposures = pd.DataFrame(betas, index=factors).T
    return FactorModel(
        exposures=exposures,
        alpha=pd.Series(alphas),
        r_squared=pd.Series(r2),
        specific_var=pd.Series(specific),
        factor_cov=factor_excess.cov(),
        factors=factors,
        periods_per_year=periods_per_year,
    )


def portfolio_factor_exposure(weights: pd.Series, model: FactorModel) -> pd.Series:
    """Portfolio-level factor betas = weight-weighted asset betas."""
    aligned = model.exposures.reindex(weights.index).fillna(0.0)
    return pd.Series(weights.values @ aligned.values, index=model.factors)


def variance_decomposition(weights: pd.Series, model: FactorModel) -> dict:
    """Split portfolio variance into systematic (factor) vs specific, per period."""
    aligned_beta = model.exposures.reindex(weights.index).fillna(0.0)
    port_beta = weights.values @ aligned_beta.values  # 1 x n_factors
    systematic_var = float(port_beta @ model.factor_cov.values @ port_beta.T)
    spec = model.specific_var.reindex(weights.index).fillna(0.0).values
    specific_var = float(np.sum((weights.values ** 2) * spec))
    total = systematic_var + specific_var
    per_factor = {}
    if total > 0:
        for i, factor in enumerate(model.factors):
            contrib = float(port_beta[i] * (model.factor_cov.values[i] @ port_beta))
            per_factor[factor] = contrib / total * 100.0
    return {
        "systematic_var": systematic_var,
        "specific_var": specific_var,
        "total_var": total,
        "systematic_pct": (systematic_var / total * 100.0) if total > 0 else 0.0,
        "specific_pct": (specific_var / total * 100.0) if total > 0 else 0.0,
        "per_factor_pct": per_factor,
    }


def risk_contributions(weights: pd.Series, covariance: pd.DataFrame) -> pd.Series:
    """Each holding's % contribution to portfolio volatility (reuses optimizer)."""
    aligned_cov = covariance.reindex(index=weights.index, columns=weights.index).fillna(0.0)
    contributions = risk_contribution_pct(weights.values, aligned_cov)
    return pd.Series(contributions, index=weights.index)
