"""Tests for the OLS factor model and risk decomposition."""

import numpy as np
import pandas as pd
import pytest

from src.quant.factor import (
    build_factor_model,
    portfolio_factor_exposure,
    risk_contributions,
    variance_decomposition,
)


def _factor_returns(n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {
            "MKT": rng.normal(0.0004, 0.01, n),
            "SEMI": rng.normal(0.0005, 0.015, n),
        },
        index=idx,
    )


def test_betas_recovered_for_linear_combo():
    factors = _factor_returns()
    # Asset = 1.5*MKT + 0.5*SEMI exactly (no noise).
    asset = 1.5 * factors["MKT"] + 0.5 * factors["SEMI"]
    asset_returns = pd.DataFrame({"AAA": asset})
    model = build_factor_model(asset_returns, factors, periods_per_year=252)
    assert model.exposures.loc["AAA", "MKT"] == pytest.approx(1.5, abs=1e-6)
    assert model.exposures.loc["AAA", "SEMI"] == pytest.approx(0.5, abs=1e-6)
    assert model.r_squared["AAA"] == pytest.approx(1.0, abs=1e-6)
    assert model.specific_var["AAA"] == pytest.approx(0.0, abs=1e-10)


def test_beta_with_noise_is_close():
    factors = _factor_returns(seed=1)
    rng = np.random.default_rng(2)
    asset = 1.2 * factors["MKT"] + rng.normal(0, 0.002, len(factors))
    model = build_factor_model(pd.DataFrame({"BBB": asset}), factors)
    assert model.exposures.loc["BBB", "MKT"] == pytest.approx(1.2, abs=0.1)
    assert 0.0 <= model.r_squared["BBB"] <= 1.0


def test_portfolio_factor_exposure():
    factors = _factor_returns(seed=3)
    a = 1.0 * factors["MKT"]
    b = 2.0 * factors["MKT"]
    model = build_factor_model(pd.DataFrame({"A": a, "B": b}), factors)
    weights = pd.Series({"A": 0.5, "B": 0.5})
    exposure = portfolio_factor_exposure(weights, model)
    assert exposure["MKT"] == pytest.approx(1.5, abs=1e-6)


def test_variance_decomposition_sums_to_total():
    factors = _factor_returns(seed=4)
    rng = np.random.default_rng(5)
    a = 1.0 * factors["MKT"] + rng.normal(0, 0.003, len(factors))
    b = 0.5 * factors["SEMI"] + rng.normal(0, 0.003, len(factors))
    model = build_factor_model(pd.DataFrame({"A": a, "B": b}), factors)
    weights = pd.Series({"A": 0.6, "B": 0.4})
    decomp = variance_decomposition(weights, model)
    assert decomp["total_var"] == pytest.approx(
        decomp["systematic_var"] + decomp["specific_var"]
    )
    assert decomp["systematic_pct"] + decomp["specific_pct"] == pytest.approx(100.0, abs=1e-6)


def test_risk_contributions_sum_to_100():
    rng = np.random.default_rng(6)
    rets = pd.DataFrame(rng.normal(0, 0.01, (250, 3)), columns=["A", "B", "C"])
    cov = rets.cov()
    weights = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    rc = risk_contributions(weights, cov)
    assert rc.sum() == pytest.approx(100.0, abs=1e-4)


def test_empty_overlap_raises():
    factors = _factor_returns()
    asset = pd.DataFrame(
        {"AAA": [0.01, 0.02]},
        index=pd.date_range("1990-01-01", periods=2, freq="B"),
    )
    with pytest.raises(ValueError):
        build_factor_model(asset, factors)
