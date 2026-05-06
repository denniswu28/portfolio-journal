"""Portfolio-theory sleeve optimizer for long-term rebalance planning."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import yaml

from src.data_ingestion.models import PortfolioSnapshot


METHODS = ("equal-vol", "erc", "sharpe-weighted", "max-sharpe")
PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}

REBALANCE_FIELDS = [
    "sleeve",
    "proxy",
    "category",
    "role",
    "method",
    "target_weight_pct",
    "current_weight_pct",
    "target_dollars",
    "current_dollars",
    "trade_dollars",
    "action",
    "equal_vol_weight_pct",
    "erc_weight_pct",
    "sharpe_weighted_weight_pct",
    "max_sharpe_weight_pct",
    "target_risk_contribution_pct",
    "annual_return_pct",
    "annual_volatility_pct",
    "sharpe_ratio",
    "min_weight_pct",
    "max_weight_pct",
    "notes",
]


@dataclass(frozen=True)
class SleeveDefinition:
    """A strategic sleeve and its market proxy for optimizer inputs."""

    name: str
    proxy: str
    category: str = ""
    role: str = ""
    holdings: tuple[str, ...] = field(default_factory=tuple)
    min_weight_pct: float = 0.0
    max_weight_pct: float = 100.0
    notes: str = ""

    @property
    def aliases(self) -> set[str]:
        return {self.proxy.upper(), *{holding.upper() for holding in self.holdings}}


@dataclass(frozen=True)
class ReturnEstimates:
    """Annualized return estimates derived from historical price data."""

    returns: pd.DataFrame
    annual_returns: pd.Series
    annual_volatility: pd.Series
    covariance: pd.DataFrame
    sharpe_ratios: pd.Series
    data_start: Optional[pd.Timestamp]
    data_end: Optional[pd.Timestamp]


DEFAULT_UNIVERSE_SETTINGS = {
    "cash_target_pct": 10.0,
    "risk_free_rate": 0.04,
    "min_trade_dollars": 25.0,
}


DEFAULT_SLEEVES = (
    SleeveDefinition(
        name="Boist memory and storage shortage",
        proxy="MU",
        category="Boist core",
        role="DRAM, NAND, HBM, storage, and memory-pricing rerating",
        holdings=("MU", "SNDK", "WDC", "STX", "SIMO"),
        min_weight_pct=8.0,
        max_weight_pct=15.0,
        notes="Primary Boist scarcity sleeve: agentic compute expands memory, storage, and bandwidth demand.",
    ),
    SleeveDefinition(
        name="Boist CPU foundry and packaging",
        proxy="SOXX",
        category="Boist core",
        role="CPU mix shift, foundry capacity, advanced packaging, and semi equipment",
        holdings=("SOXX", "INTC", "AMD", "TSM", "ASML", "AMAT", "LRCX", "ARM"),
        min_weight_pct=8.0,
        max_weight_pct=16.0,
        notes="Keeps the CPU/foundry/packaging thesis central while avoiding a single-stock proxy.",
    ),
    SleeveDefinition(
        name="Boist semiconductor beta",
        proxy="SMH",
        category="Boist core",
        role="Broad AI semiconductor infrastructure and accelerator supply chain",
        holdings=("SMH", "NVDA", "AVGO", "MRVL", "QCOM", "KLAC", "ADI", "MCHP"),
        min_weight_pct=7.0,
        max_weight_pct=18.0,
        notes="Broad semiconductor ETF sleeve remains a core expression, but memory and CPU sleeves drive the thesis.",
    ),
    SleeveDefinition(
        name="AI platforms and ASIC customers",
        proxy="IGV",
        category="Boist core",
        role="AI platforms, ASIC demand, agent software, and cloud monetization",
        holdings=("IGV", "SKYY", "WCLD", "VGT", "IYW", "QQQM", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "ORCL", "PLTR", "SNOW", "DDOG"),
        min_weight_pct=4.0,
        max_weight_pct=12.0,
        notes="Captures the demand side that converts agent use into compute, ASIC, storage, and cloud orders.",
    ),
    SleeveDefinition(
        name="Data-center power grid",
        proxy="GRID",
        category="Boist derivative",
        role="Grid upgrades, transmission, electrification, and power-delivery bottlenecks",
        holdings=("GRID", "PAVE", "IFRA", "ETN", "HUBB", "PWR", "MTZ", "GEV"),
        min_weight_pct=4.0,
        max_weight_pct=10.0,
        notes="Agentic compute turns electricity delivery into a growth constraint rather than a background utility input.",
    ),
    SleeveDefinition(
        name="Electrical and thermal equipment",
        proxy="PAVE",
        category="Boist derivative",
        role="Switchgear, thermal management, power systems, and data-center buildout",
        holdings=("VRT", "ETN", "HUBB", "PAVE", "URI", "GE", "GEV", "JCI", "CARR"),
        min_weight_pct=3.0,
        max_weight_pct=10.0,
        notes="More agents mean denser racks, more heat, more power conversion, and more electrical infrastructure.",
    ),
    SleeveDefinition(
        name="Data-center connectivity and facilities",
        proxy="SRVR",
        category="Boist derivative",
        role="Data-center REITs, optical networking, routing, and physical compute facilities",
        holdings=("SRVR", "EQIX", "DLR", "ANET", "CSCO", "CIEN", "AVGO", "MRVL"),
        min_weight_pct=2.0,
        max_weight_pct=7.0,
        notes="Compute demand spills into leased capacity, interconnect, optical networking, and facility uptime.",
    ),
    SleeveDefinition(
        name="Power generation and uranium",
        proxy="XLE",
        category="Boist derivative",
        role="Reliable generation, natural gas, nuclear, uranium, and utility power supply",
        holdings=("XLE", "URNM", "URA", "CCJ", "CEG", "VST", "NEE", "XOM", "CVX", "DBA"),
        min_weight_pct=3.0,
        max_weight_pct=8.0,
        notes="Data-center electricity load can pull forward gas, nuclear, and reliable-generation demand.",
    ),
    SleeveDefinition(
        name="Industrial metals and materials",
        proxy="DBB",
        category="Boist derivative",
        role="Copper, aluminum, steel, silver inputs, and resource equities for electrification",
        holdings=("DBB", "XME", "XLB", "FCX", "SCCO", "TECK"),
        min_weight_pct=2.0,
        max_weight_pct=6.0,
        notes="Grid, servers, cables, transformers, and energy infrastructure all increase materials intensity.",
    ),
    SleeveDefinition(
        name="Agentic cybersecurity",
        proxy="CIBR",
        category="Boist derivative",
        role="Identity, endpoint, cloud, API, and agent-security controls",
        holdings=("CIBR", "HACK", "IHAK", "PANW", "CRWD", "FTNT", "ZS", "NET", "OKTA", "CYBR"),
        min_weight_pct=1.0,
        max_weight_pct=5.0,
        notes="More autonomous agents expand the attack surface; this remains a small derivative sleeve.",
    ),
    SleeveDefinition(
        name="Industrial automation and edge AI",
        proxy="BOTZ",
        category="Boist derivative",
        role="Robotics, machine vision, factory automation, and edge inference adoption",
        holdings=("BOTZ", "ROBO", "IRBO", "ROK", "TER", "CGNX", "SYM"),
        min_weight_pct=1.0,
        max_weight_pct=4.0,
        notes="A small option on compute moving from data centers into physical workflows and edge devices.",
    ),
    SleeveDefinition(
        name="Defense and aerospace",
        proxy="ITA",
        category="Derivative hedge",
        role="Geopolitical demand, autonomous systems, sensors, and aerospace electronics",
        holdings=("ITA", "XAR", "PPA", "SHLD", "LMT", "NOC", "RTX", "GD", "HWM"),
        min_weight_pct=3.0,
        max_weight_pct=8.0,
        notes="Defense remains a related hedge where compute, autonomy, sensors, and geopolitics overlap.",
    ),
    SleeveDefinition(
        name="Core US equity",
        proxy="VTI",
        category="Core ballast",
        role="Broad US market anchor outside the Boist baskets",
        holdings=("VTI", "SPY", "IVV", "VOO", "FXAIX", "ITOT"),
        min_weight_pct=6.0,
        max_weight_pct=18.0,
        notes="Keeps broad beta present, but no longer lets broad index math dominate the Boist thesis.",
    ),
    SleeveDefinition(
        name="Core ex-US equity",
        proxy="VXUS",
        category="Core ballast",
        role="Small international diversification sleeve",
        holdings=("VXUS", "IXUS", "VEA", "VWO", "IEFA", "IEMG"),
        min_weight_pct=2.0,
        max_weight_pct=8.0,
        notes="A modest diversifier rather than a major secular-growth allocation.",
    ),
    SleeveDefinition(
        name="Gold and precious metals",
        proxy="GLDM",
        category="Risk ballast",
        role="Crisis, currency, and real-rate hedge",
        holdings=("GLDM", "GLD", "IAU", "IAUI", "SGOL", "SLV", "PPLT", "PLTM"),
        min_weight_pct=5.0,
        max_weight_pct=10.0,
        notes="Portfolio stabilizer retained because the Boist core is intentionally concentrated.",
    ),
    SleeveDefinition(
        name="Treasury and TIPS ballast",
        proxy="TIP",
        category="Risk ballast",
        role="Inflation protection, duration ballast, and lower-volatility reserve",
        holdings=("TIP", "VTIP", "SCHP", "STIP", "IEF", "SHY", "BIL", "SGOV"),
        min_weight_pct=3.0,
        max_weight_pct=10.0,
        notes="Dampens drawdowns without turning the portfolio into a bond allocation.",
    ),
)


def load_sleeve_universe(path: str | Path | None = None) -> tuple[dict[str, Any], list[SleeveDefinition]]:
    """Load a YAML sleeve universe, or return the built-in default universe."""
    if path is None:
        default_path = Path(__file__).resolve().parents[2] / "config" / "growth_universe.yaml"
        if default_path.exists():
            return load_sleeve_universe(default_path)
        return dict(DEFAULT_UNIVERSE_SETTINGS), list(DEFAULT_SLEEVES)

    universe_path = Path(path)
    if not universe_path.exists():
        raise FileNotFoundError(f"Sleeve universe not found: {universe_path}")

    loaded = yaml.safe_load(universe_path.read_text(encoding="utf-8")) or {}
    if isinstance(loaded, list):
        settings = dict(DEFAULT_UNIVERSE_SETTINGS)
        sleeve_items = loaded
    else:
        settings = {**DEFAULT_UNIVERSE_SETTINGS, **(loaded.get("settings") or {})}
        sleeve_items = loaded.get("sleeves") or []

    sleeves = [_coerce_sleeve(item) for item in sleeve_items]
    if not sleeves:
        raise ValueError(f"No sleeves found in {universe_path}")
    return settings, sleeves


def estimate_returns(
    price_history: pd.DataFrame,
    periods_per_year: int = 52,
    risk_free_rate: float = 0.04,
    min_observations: int = 52,
) -> ReturnEstimates:
    """Estimate annualized returns, volatility, covariance, and Sharpe ratios."""
    if price_history.empty:
        raise ValueError("Price history is empty.")

    prices = _clean_price_history(price_history)
    period_returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    period_returns = period_returns.dropna(how="all")
    valid_columns = period_returns.count()[period_returns.count() >= min_observations].index.tolist()
    if not valid_columns:
        raise ValueError(
            f"No proxy has at least {min_observations} return observations."
        )

    aligned_returns = period_returns[valid_columns].dropna(how="any")
    if len(aligned_returns) < max(2, min_observations // 2):
        raise ValueError("Not enough overlapping return history after aligning proxies.")

    annual_returns = aligned_returns.mean() * periods_per_year
    annual_volatility = aligned_returns.std(ddof=1) * math.sqrt(periods_per_year)
    covariance = aligned_returns.cov() * periods_per_year
    sharpe_ratios = (annual_returns - risk_free_rate) / annual_volatility.replace(0, np.nan)

    return ReturnEstimates(
        returns=aligned_returns,
        annual_returns=annual_returns,
        annual_volatility=annual_volatility,
        covariance=covariance,
        sharpe_ratios=sharpe_ratios.replace([np.inf, -np.inf], np.nan).fillna(0.0),
        data_start=aligned_returns.index.min(),
        data_end=aligned_returns.index.max(),
    )


def build_rebalance_plan(
    sleeves: list[SleeveDefinition],
    price_history: pd.DataFrame,
    snapshot: Optional[PortfolioSnapshot] = None,
    method: str = "erc",
    cash_target_pct: float = 10.0,
    risk_free_rate: float = 0.04,
    periods_per_year: int = 52,
    min_observations: int = 52,
    min_trade_dollars: float = 25.0,
) -> dict[str, Any]:
    """Build a sleeve-level target allocation and rebalance report."""
    if method not in METHODS:
        raise ValueError(f"Unknown optimization method: {method}")
    if cash_target_pct < 0.0 or cash_target_pct >= 100.0:
        raise ValueError("Cash target must be at least 0% and less than 100%.")
    if min_trade_dollars < 0.0:
        raise ValueError("Minimum trade dollars must be nonnegative.")

    estimates = estimate_returns(
        price_history=price_history,
        periods_per_year=periods_per_year,
        risk_free_rate=risk_free_rate,
        min_observations=min_observations,
    )
    available_sleeves = [sleeve for sleeve in sleeves if sleeve.proxy.upper() in estimates.annual_volatility.index]
    missing_proxies = [sleeve.proxy.upper() for sleeve in sleeves if sleeve not in available_sleeves]
    if not available_sleeves:
        raise ValueError("None of the sleeve proxies had usable price history.")

    proxy_order = [sleeve.proxy.upper() for sleeve in available_sleeves]
    covariance = estimates.covariance.loc[proxy_order, proxy_order]
    annual_returns = estimates.annual_returns.loc[proxy_order]
    annual_volatility = estimates.annual_volatility.loc[proxy_order]
    sharpe_ratios = estimates.sharpe_ratios.loc[proxy_order]
    investable_fraction = max(0.0, min(1.0, (100.0 - cash_target_pct) / 100.0))
    min_bounds, max_bounds = _total_bounds_to_investable_bounds(
        available_sleeves,
        investable_fraction,
    )

    method_weight_arrays = {
        "equal-vol": equal_volatility_weights(annual_volatility, min_bounds, max_bounds),
        "erc": equal_risk_contribution_weights(covariance, min_bounds, max_bounds),
        "sharpe-weighted": sharpe_weighted_weights(
            annual_volatility,
            sharpe_ratios,
            min_bounds,
            max_bounds,
        ),
        "max-sharpe": max_sharpe_weights(
            annual_returns,
            covariance,
            risk_free_rate,
            min_bounds,
            max_bounds,
        ),
    }
    method_weights = {
        method_name: _weights_by_sleeve(available_sleeves, weight_array)
        for method_name, weight_array in method_weight_arrays.items()
    }

    selected_weights = method_weights[method]
    selected_array = method_weight_arrays[method]
    risk_contributions = risk_contribution_pct(selected_array, covariance)
    current_values, unmapped_positions = current_values_by_sleeve(snapshot, sleeves)
    total_value = snapshot.total_portfolio_value if snapshot else None
    cash_current_value = snapshot.cash if snapshot else None

    rows = []
    for sleeve_index, sleeve in enumerate(available_sleeves):
        target_weight_pct = selected_weights[sleeve.name] * investable_fraction * 100.0
        current_value = current_values.get(sleeve.name, 0.0) if snapshot else None
        current_weight_pct = (
            current_value / total_value * 100.0
            if snapshot and total_value
            else None
        )
        target_dollars = total_value * target_weight_pct / 100.0 if total_value else None
        trade_dollars = (
            target_dollars - current_value
            if target_dollars is not None and current_value is not None
            else None
        )
        rows.append(
            {
                "sleeve": sleeve.name,
                "proxy": sleeve.proxy.upper(),
                "category": sleeve.category,
                "role": sleeve.role,
                "method": method,
                "target_weight_pct": target_weight_pct,
                "current_weight_pct": current_weight_pct,
                "target_dollars": target_dollars,
                "current_dollars": current_value,
                "trade_dollars": trade_dollars,
                "action": _rebalance_action(trade_dollars, min_trade_dollars),
                "equal_vol_weight_pct": method_weights["equal-vol"][sleeve.name] * investable_fraction * 100.0,
                "erc_weight_pct": method_weights["erc"][sleeve.name] * investable_fraction * 100.0,
                "sharpe_weighted_weight_pct": method_weights["sharpe-weighted"][sleeve.name] * investable_fraction * 100.0,
                "max_sharpe_weight_pct": method_weights["max-sharpe"][sleeve.name] * investable_fraction * 100.0,
                "target_risk_contribution_pct": risk_contributions[sleeve_index],
                "annual_return_pct": annual_returns[sleeve.proxy.upper()] * 100.0,
                "annual_volatility_pct": annual_volatility[sleeve.proxy.upper()] * 100.0,
                "sharpe_ratio": sharpe_ratios[sleeve.proxy.upper()],
                "min_weight_pct": sleeve.min_weight_pct,
                "max_weight_pct": sleeve.max_weight_pct,
                "notes": sleeve.notes,
            }
        )

    cash_row = {
        "sleeve": "Cash reserve",
        "proxy": "CASH",
        "category": "Cash",
        "role": "Liquidity reserve",
        "method": method,
        "target_weight_pct": cash_target_pct,
        "current_weight_pct": (
            cash_current_value / total_value * 100.0
            if cash_current_value is not None and total_value
            else None
        ),
        "target_dollars": total_value * cash_target_pct / 100.0 if total_value else None,
        "current_dollars": cash_current_value,
        "trade_dollars": (
            total_value * cash_target_pct / 100.0 - cash_current_value
            if total_value and cash_current_value is not None
            else None
        ),
        "action": _rebalance_action(
            total_value * cash_target_pct / 100.0 - cash_current_value
            if total_value and cash_current_value is not None
            else None,
            min_trade_dollars,
        ),
        "equal_vol_weight_pct": cash_target_pct,
        "erc_weight_pct": cash_target_pct,
        "sharpe_weighted_weight_pct": cash_target_pct,
        "max_sharpe_weight_pct": cash_target_pct,
        "target_risk_contribution_pct": "",
        "annual_return_pct": "",
        "annual_volatility_pct": "",
        "sharpe_ratio": "",
        "min_weight_pct": cash_target_pct,
        "max_weight_pct": cash_target_pct,
        "notes": "Target cash reserve from the rebalance settings.",
    }

    return {
        "generated_at": datetime.now(),
        "method": method,
        "cash_target_pct": cash_target_pct,
        "risk_free_rate": risk_free_rate,
        "periods_per_year": periods_per_year,
        "min_observations": min_observations,
        "min_trade_dollars": min_trade_dollars,
        "data_start": estimates.data_start,
        "data_end": estimates.data_end,
        "observation_count": len(estimates.returns),
        "total_value": total_value,
        "snapshot_timestamp": snapshot.timestamp if snapshot else None,
        "rows": sorted(rows, key=lambda row: row["target_weight_pct"], reverse=True),
        "cash_row": cash_row,
        "missing_proxies": missing_proxies,
        "unmapped_positions": unmapped_positions,
        "available_sleeves": available_sleeves,
    }


def equal_volatility_weights(
    annual_volatility: pd.Series,
    min_weights: np.ndarray | None = None,
    max_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Allocate in proportion to inverse annualized volatility."""
    volatility = annual_volatility.to_numpy(dtype=float)
    scores = np.where(np.isfinite(volatility) & (volatility > 0), 1.0 / volatility, 0.0)
    return apply_weight_bounds(scores, min_weights, max_weights)


def sharpe_weighted_weights(
    annual_volatility: pd.Series,
    sharpe_ratios: pd.Series,
    min_weights: np.ndarray | None = None,
    max_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Allocate toward higher positive Sharpe while still penalizing volatility."""
    volatility = annual_volatility.to_numpy(dtype=float)
    sharpe_values = sharpe_ratios.to_numpy(dtype=float)
    scores = np.where(
        np.isfinite(volatility) & (volatility > 0),
        np.maximum(sharpe_values, 0.0) / volatility,
        0.0,
    )
    if float(scores.sum()) <= 0.0:
        scores = np.where(np.isfinite(volatility) & (volatility > 0), 1.0 / volatility, 0.0)
    return apply_weight_bounds(scores, min_weights, max_weights)


def max_sharpe_weights(
    annual_returns: pd.Series,
    covariance: pd.DataFrame,
    risk_free_rate: float = 0.04,
    min_weights: np.ndarray | None = None,
    max_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Approximate a long-only max-Sharpe portfolio with bounded tangency weights."""
    excess_returns = annual_returns.to_numpy(dtype=float) - risk_free_rate
    covariance_array = _regularized_covariance(covariance)
    try:
        raw_weights = np.linalg.pinv(covariance_array).dot(excess_returns)
    except np.linalg.LinAlgError:
        raw_weights = np.maximum(excess_returns, 0.0)
    raw_weights = np.where(np.isfinite(raw_weights) & (raw_weights > 0.0), raw_weights, 0.0)
    if float(raw_weights.sum()) <= 0.0:
        raw_weights = np.ones(len(excess_returns), dtype=float)
    return apply_weight_bounds(raw_weights, min_weights, max_weights)


def equal_risk_contribution_weights(
    covariance: pd.DataFrame,
    min_weights: np.ndarray | None = None,
    max_weights: np.ndarray | None = None,
    max_iterations: int = 2000,
    tolerance: float = 1e-6,
) -> np.ndarray:
    """Approximate equal-risk-contribution weights with multiplicative updates."""
    covariance_array = _regularized_covariance(covariance)
    proxy_count = covariance_array.shape[0]
    diagonal_volatility = pd.Series(np.sqrt(np.diag(covariance_array)))
    weights = equal_volatility_weights(diagonal_volatility, min_weights, max_weights)
    target_contribution = np.full(proxy_count, 1.0 / proxy_count)

    for _iteration in range(max_iterations):
        current_contribution = np.asarray(risk_contribution_pct(weights, covariance_array)) / 100.0
        if np.max(np.abs(current_contribution - target_contribution)) < tolerance:
            break

        clipped_contribution = np.maximum(current_contribution, 1e-8)
        adjustment = np.sqrt(np.clip(target_contribution / clipped_contribution, 0.75, 1.35))
        candidate = apply_weight_bounds(weights * adjustment, min_weights, max_weights)
        if np.linalg.norm(candidate - weights, ord=1) < tolerance:
            weights = candidate
            break
        weights = apply_weight_bounds((weights + candidate) / 2.0, min_weights, max_weights)

    return weights


def risk_contribution_pct(weights: np.ndarray, covariance: pd.DataFrame | np.ndarray) -> list[float]:
    """Return each sleeve's percentage contribution to portfolio volatility."""
    weights_array = np.asarray(weights, dtype=float)
    covariance_array = _regularized_covariance(covariance)
    portfolio_variance = float(weights_array.T.dot(covariance_array).dot(weights_array))
    if portfolio_variance <= 0.0:
        return [0.0 for _value in weights_array]
    marginal_contribution = covariance_array.dot(weights_array)
    contribution = weights_array * marginal_contribution / portfolio_variance
    return [round(float(value * 100.0), 6) for value in contribution]


def apply_weight_bounds(
    raw_weights: Iterable[float],
    min_weights: np.ndarray | None = None,
    max_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Normalize nonnegative weights while respecting lower and upper bounds."""
    raw_array = np.asarray(list(raw_weights), dtype=float)
    proxy_count = len(raw_array)
    if proxy_count == 0:
        return raw_array

    raw_array = np.where(np.isfinite(raw_array) & (raw_array > 0.0), raw_array, 0.0)
    if float(raw_array.sum()) <= 0.0:
        raw_array = np.ones(proxy_count, dtype=float)
    raw_array = raw_array / raw_array.sum()

    lower_bounds = np.zeros(proxy_count, dtype=float) if min_weights is None else np.asarray(min_weights, dtype=float)
    upper_bounds = np.ones(proxy_count, dtype=float) if max_weights is None else np.asarray(max_weights, dtype=float)
    lower_bounds = np.clip(lower_bounds, 0.0, 1.0)
    upper_bounds = np.clip(np.maximum(upper_bounds, lower_bounds), 0.0, 1.0)

    if float(lower_bounds.sum()) > 1.0 + 1e-9:
        raise ValueError("Minimum sleeve weights sum to more than 100% of investable capital.")
    if float(upper_bounds.sum()) < 1.0 - 1e-9:
        raise ValueError("Maximum sleeve weights sum to less than 100% of investable capital.")

    result = lower_bounds.copy()
    active_mask = upper_bounds > lower_bounds + 1e-12

    while True:
        remaining_weight = 1.0 - float(result[~active_mask].sum()) - float(lower_bounds[active_mask].sum())
        if remaining_weight <= 1e-12 or not bool(active_mask.any()):
            break

        active_indices = np.flatnonzero(active_mask)
        active_scores = raw_array[active_indices]
        if float(active_scores.sum()) <= 0.0:
            active_scores = np.ones(len(active_indices), dtype=float)
        proposed_values = lower_bounds[active_indices] + remaining_weight * active_scores / active_scores.sum()
        capped_mask = proposed_values > upper_bounds[active_indices] + 1e-12
        if not bool(capped_mask.any()):
            result[active_indices] = proposed_values
            break

        capped_indices = active_indices[capped_mask]
        result[capped_indices] = upper_bounds[capped_indices]
        active_mask[capped_indices] = False

    if float(result.sum()) <= 0.0:
        result = np.ones(proxy_count, dtype=float) / proxy_count
    else:
        result = result / result.sum()
    return result


def current_values_by_sleeve(
    snapshot: Optional[PortfolioSnapshot],
    sleeves: list[SleeveDefinition],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Map current snapshot positions into sleeve values by ticker aliases."""
    if snapshot is None:
        return {}, []

    alias_to_sleeve: dict[str, str] = {}
    for sleeve in sleeves:
        for alias in sleeve.aliases:
            alias_to_sleeve.setdefault(alias, sleeve.name)

    values = {sleeve.name: 0.0 for sleeve in sleeves}
    unmapped = []
    for position in snapshot.positions:
        ticker = position.ticker.upper()
        sleeve_name = alias_to_sleeve.get(ticker)
        if sleeve_name:
            values[sleeve_name] += position.market_value
        else:
            unmapped.append(
                {
                    "ticker": position.ticker,
                    "company_name": position.company_name,
                    "market_value": position.market_value,
                    "weight_pct": position.weight_pct,
                }
            )
    return values, unmapped


def write_rebalance_csv(plan: dict[str, Any], output_path: str | Path) -> Path:
    """Write the rebalance plan as spreadsheet-compatible CSV."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REBALANCE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in [*plan["rows"], plan["cash_row"]]:
            writer.writerow({field_name: _format_csv_value(row.get(field_name)) for field_name in REBALANCE_FIELDS})
    return path


def write_rebalance_markdown(plan: dict[str, Any], output_path: str | Path) -> Path:
    """Write a human-readable rebalance plan with context and warnings."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Portfolio Theory Rebalance Plan",
        "",
        f"Generated: {_format_markdown_datetime(plan['generated_at'])}",
        f"Method: {plan['method']}",
        f"Price window: {_format_markdown_datetime(plan['data_start'])} to {_format_markdown_datetime(plan['data_end'])} ({plan['observation_count']} aligned observations)",
        f"Cash target: {plan['cash_target_pct']:.2f}%",
        f"Risk-free rate: {plan['risk_free_rate']:.2%}",
    ]
    if plan.get("snapshot_timestamp"):
        lines.append(f"Mapped to snapshot: {_format_markdown_datetime(plan['snapshot_timestamp'])}")
    if plan.get("total_value"):
        lines.append(f"Portfolio value: {_format_money(plan['total_value'])}")

    lines.extend(
        [
            "",
            "## Target Allocation",
            "",
            "| Sleeve | Proxy | Target | Current | Trade | Action | Vol | Sharpe |",
            "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for row in [*plan["rows"], plan["cash_row"]]:
        lines.append(
            "| {sleeve} | {proxy} | {target} | {current} | {trade} | {action} | {vol} | {sharpe} |".format(
                sleeve=row["sleeve"],
                proxy=row["proxy"],
                target=_format_pct(row.get("target_weight_pct")),
                current=_format_pct(row.get("current_weight_pct")),
                trade=_format_money(row.get("trade_dollars")),
                action=row.get("action") or "",
                vol=_format_pct(row.get("annual_volatility_pct")),
                sharpe=_format_number(row.get("sharpe_ratio")),
            )
        )

    lines.extend(
        [
            "",
            "## Boist-Derived Growth Sleeves",
            "",
            "Agentic AI demand is treated as the causal engine: more agents require more CPU cycles, memory, storage, networking, data-center capacity, electricity, cooling, and security. Broad secular themes are not equal-weighted here; unrelated growth sleeves should receive smaller or no funding unless they connect back to those bottlenecks.",
            "",
            "## Derived Demand Map",
            "",
            "| Compute-demand effect | Portfolio expression |",
            "| --- | --- |",
            "| More CPU cycles and heterogeneous compute | CPU/foundry/packaging, semiconductors, ASIC customers |",
            "| Memory and storage scarcity | DRAM, NAND, HBM, storage controllers, and memory baskets |",
            "| Higher rack density and facility buildout | Electrical gear, thermal management, data centers, networking |",
            "| Power becomes a growth bottleneck | Grid, generation, gas, nuclear, uranium, and power infrastructure |",
            "| Electrification raises input intensity | Copper, aluminum, steel, silver, and resource equities |",
            "| Autonomous agents increase system risk | Identity, cloud, endpoint, API, and agent security |",
            "",
            "| Sleeve | Role | Notes |",
            "| --- | --- | --- |",
        ]
    )
    for sleeve in plan["available_sleeves"]:
        lines.append(f"| {sleeve.name} | {sleeve.role} | {sleeve.notes} |")

    warnings = []
    if plan["missing_proxies"]:
        warnings.append("Missing or insufficient price history: " + ", ".join(plan["missing_proxies"]))
    if plan["unmapped_positions"]:
        unmapped = ", ".join(position["ticker"] for position in plan["unmapped_positions"])
        warnings.append("Current holdings not mapped to a sleeve: " + unmapped)
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")

    lines.extend(
        [
            "",
            "## Method Notes",
            "",
            "- equal-vol uses inverse annualized volatility.",
            "- erc estimates equal risk contribution from the covariance matrix.",
            "- sharpe-weighted tilts inverse-vol weights toward positive individual Sharpe ratios.",
            "- max-sharpe uses bounded long-only tangency weights from historical excess returns.",
            "- Boist core and derivative sleeves use min/max bounds so broad ballast cannot crowd out the compute-demand thesis.",
            "- Treat targets as planning inputs; apply tax, liquidity, and account constraints before trading.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _coerce_sleeve(item: dict[str, Any]) -> SleeveDefinition:
    proxy = str(item["proxy"]).upper().strip()
    holdings = tuple(str(value).upper().strip() for value in item.get("holdings", []) if str(value).strip())
    return SleeveDefinition(
        name=str(item["name"]).strip(),
        proxy=proxy,
        category=str(item.get("category", "")).strip(),
        role=str(item.get("role", "")).strip(),
        holdings=holdings,
        min_weight_pct=float(item.get("min_weight_pct", 0.0) or 0.0),
        max_weight_pct=float(item.get("max_weight_pct", 100.0) or 100.0),
        notes=str(item.get("notes", "")).strip(),
    )


def _clean_price_history(price_history: pd.DataFrame) -> pd.DataFrame:
    cleaned = price_history.copy()
    cleaned.columns = [str(column).upper().strip() for column in cleaned.columns]
    cleaned = cleaned.apply(pd.to_numeric, errors="coerce")
    cleaned = cleaned.dropna(how="all").ffill().dropna(how="all")
    cleaned = cleaned.loc[:, cleaned.notna().any(axis=0)]
    if cleaned.empty:
        raise ValueError("Price history contains no numeric close prices.")
    return cleaned


def _regularized_covariance(covariance: pd.DataFrame | np.ndarray) -> np.ndarray:
    covariance_array = np.asarray(covariance, dtype=float)
    covariance_array = np.nan_to_num(covariance_array, nan=0.0, posinf=0.0, neginf=0.0)
    if covariance_array.ndim != 2 or covariance_array.shape[0] != covariance_array.shape[1]:
        raise ValueError("Covariance matrix must be square.")
    diagonal_mean = float(np.mean(np.diag(covariance_array))) if covariance_array.size else 0.0
    ridge = max(diagonal_mean, 1.0) * 1e-8
    return covariance_array + np.eye(covariance_array.shape[0]) * ridge


def _total_bounds_to_investable_bounds(
    sleeves: list[SleeveDefinition],
    investable_fraction: float,
) -> tuple[np.ndarray, np.ndarray]:
    if investable_fraction <= 0.0:
        raise ValueError("Cash target leaves no investable allocation.")
    min_bounds = np.asarray([sleeve.min_weight_pct / 100.0 / investable_fraction for sleeve in sleeves], dtype=float)
    max_bounds = np.asarray([sleeve.max_weight_pct / 100.0 / investable_fraction for sleeve in sleeves], dtype=float)
    return np.clip(min_bounds, 0.0, 1.0), np.clip(max_bounds, 0.0, 1.0)


def _weights_by_sleeve(sleeves: list[SleeveDefinition], weights: np.ndarray) -> dict[str, float]:
    return {sleeve.name: float(weights[sleeve_index]) for sleeve_index, sleeve in enumerate(sleeves)}


def _rebalance_action(trade_dollars: Optional[float], min_trade_dollars: float) -> str:
    if trade_dollars is None:
        return "weight only"
    if abs(trade_dollars) < min_trade_dollars:
        return "hold"
    return "buy" if trade_dollars > 0.0 else "trim"


def _format_csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, float):
        return round(value, 6)
    return value


def _format_markdown_datetime(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().isoformat(sep=" ", timespec="seconds")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _format_pct(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.2f}%"


def _format_money(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"${float(value):,.2f}"


def _format_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{float(value):.3f}"