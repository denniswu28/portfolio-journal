"""
market_data.py - Live market data retrieval via yfinance.

Provides current price quotes and basic ticker info for portfolio enrichment,
plus option-chain, risk-free-rate, and realized-volatility helpers that feed the
deterministic options analytics harness.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf


# Simple in-memory cache: {ticker: (price, timestamp)}
_PRICE_CACHE: Dict[str, tuple[float, float]] = {}
CACHE_TTL = 300  # seconds

# Default annualized risk-free rate used when ^IRX cannot be fetched.
DEFAULT_RISK_FREE_RATE = 0.04
# Trading days per year for annualizing realized volatility.
TRADING_DAYS_PER_YEAR = 252


def get_current_price(ticker: str, use_cache: bool = True, cache_ttl: int = CACHE_TTL) -> Optional[float]:
    """
    Fetch the current price for a single ticker.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        use_cache: Whether to use the in-memory price cache.
        cache_ttl: Cache time-to-live in seconds.

    Returns:
        Current price as a float, or None if unavailable.
    """
    ticker = ticker.upper()
    now = time.time()

    if use_cache and ticker in _PRICE_CACHE:
        cached_price, cached_time = _PRICE_CACHE[ticker]
        if now - cached_time < cache_ttl:
            return cached_price

    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.last_price)
        _PRICE_CACHE[ticker] = (price, now)
        return price
    except Exception:
        # Fall back to history-based price if fast_info fails
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                _PRICE_CACHE[ticker] = (price, now)
                return price
        except Exception:
            pass
        return None


def get_current_prices(tickers: List[str], use_cache: bool = True, cache_ttl: int = CACHE_TTL) -> Dict[str, Optional[float]]:
    """
    Fetch current prices for multiple tickers in a single batch request.

    Args:
        tickers: List of ticker symbols.
        use_cache: Whether to use the in-memory price cache.
        cache_ttl: Cache time-to-live in seconds.

    Returns:
        Dict mapping ticker -> price (or None if unavailable).
    """
    tickers = [t.upper() for t in tickers]
    now = time.time()

    results: Dict[str, Optional[float]] = {}
    to_fetch: List[str] = []

    for ticker in tickers:
        if use_cache and ticker in _PRICE_CACHE:
            cached_price, cached_time = _PRICE_CACHE[ticker]
            if now - cached_time < cache_ttl:
                results[ticker] = cached_price
                continue
        to_fetch.append(ticker)

    if to_fetch:
        try:
            data = yf.download(
                to_fetch,
                period="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            close_data = data["Close"] if "Close" in data.columns else data

            if len(to_fetch) == 1:
                ticker = to_fetch[0]
                if not close_data.empty:
                    price = float(close_data.iloc[-1])
                    results[ticker] = price
                    _PRICE_CACHE[ticker] = (price, now)
                else:
                    results[ticker] = None
            else:
                for ticker in to_fetch:
                    if ticker in close_data.columns and not close_data[ticker].isna().all():
                        price = float(close_data[ticker].dropna().iloc[-1])
                        results[ticker] = price
                        _PRICE_CACHE[ticker] = (price, now)
                    else:
                        results[ticker] = None
        except Exception:
            for ticker in to_fetch:
                results[ticker] = None

        # Fall back to the reliable singular fast_info path for any ticker the batch
        # download could not price (yf.download can be flaky for some symbols/sessions).
        for ticker in to_fetch:
            if results.get(ticker) is None:
                price = get_current_price(ticker, use_cache=use_cache, cache_ttl=cache_ttl)
                results[ticker] = price

    return results


def get_ticker_info(ticker: str) -> Dict:
    """
    Fetch basic information about a ticker (name, sector, etc.).

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with ticker metadata (may be empty on failure).
    """
    try:
        info = yf.Ticker(ticker.upper()).info
        return {
            "ticker": ticker.upper(),
            "name": info.get("longName") or info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
        }
    except Exception:
        return {"ticker": ticker.upper(), "name": ticker}


def get_price_history(
    tickers: List[str],
    period: str = "3y",
    interval: str = "1wk",
) -> pd.DataFrame:
    """
    Fetch adjusted close price history for multiple tickers.

    Args:
        tickers: List of ticker symbols.
        period: yfinance history period, for example "3y".
        interval: yfinance interval, for example "1d", "1wk", or "1mo".

    Returns:
        DataFrame indexed by date with uppercase ticker columns.
    """
    unique_tickers = []
    for ticker in tickers:
        normalized = ticker.upper().strip()
        if normalized and normalized not in unique_tickers:
            unique_tickers.append(normalized)
    if not unique_tickers:
        return pd.DataFrame()

    try:
        data = yf.download(
            unique_tickers,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        return pd.DataFrame()

    if data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        first_level = data.columns.get_level_values(0)
        if "Close" in first_level:
            close_data = data["Close"]
        elif "Adj Close" in first_level:
            close_data = data["Adj Close"]
        else:
            return pd.DataFrame()
    elif "Close" in data.columns:
        close_data = data[["Close"]].rename(columns={"Close": unique_tickers[0]})
    elif "Adj Close" in data.columns:
        close_data = data[["Adj Close"]].rename(columns={"Adj Close": unique_tickers[0]})
    else:
        close_data = data

    if isinstance(close_data, pd.Series):
        close_data = close_data.to_frame(name=unique_tickers[0])
    close_data = close_data.rename(columns=lambda column: str(column).upper().strip())
    ordered_columns = [ticker for ticker in unique_tickers if ticker in close_data.columns]
    return close_data.loc[:, ordered_columns].dropna(how="all")


def get_ohlc_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Fetch adjusted OHLC history for a single ticker (for ATR and range studies).

    Returns:
        DataFrame indexed by date with lowercase columns open/high/low/close/volume,
        or an empty DataFrame on any error.
    """
    symbol = ticker.strip().upper()
    if not symbol:
        return pd.DataFrame()
    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()

    if data is None or data.empty:
        return pd.DataFrame()

    # Collapse a (field, ticker) MultiIndex down to single-level field columns.
    if isinstance(data.columns, pd.MultiIndex):
        try:
            data = data.xs(symbol, axis=1, level=1)
        except (KeyError, ValueError):
            data.columns = data.columns.get_level_values(0)

    rename_map = {col: str(col).strip().lower() for col in data.columns}
    data = data.rename(columns=rename_map)
    wanted = [c for c in ("open", "high", "low", "close", "volume") if c in data.columns]
    return data.loc[:, wanted].dropna(how="all")


# ── OPTIONS & RATES (deterministic harness inputs) ───────────────────────────

@dataclass
class OptionChain:
    """A single-expiry option chain snapshot for one underlying."""

    ticker: str
    expiry: str  # resolved expiry, ISO format YYYY-MM-DD
    spot: Optional[float]
    calls: pd.DataFrame = field(default_factory=pd.DataFrame)
    puts: pd.DataFrame = field(default_factory=pd.DataFrame)

    def side(self, right: str) -> pd.DataFrame:
        """Return the calls or puts frame for an option right ('C'/'CALL' or 'P'/'PUT')."""
        token = right.strip().upper()
        if token in ("C", "CALL", "CALLS"):
            return self.calls
        if token in ("P", "PUT", "PUTS"):
            return self.puts
        raise ValueError(f"right must be call or put, got '{right}'")


def list_option_expiries(ticker: str) -> List[str]:
    """
    Return available option expiry dates (ISO strings) for a ticker, soonest first.

    Returns an empty list if the ticker has no listed options or on any error.
    """
    try:
        expiries = yf.Ticker(ticker.strip().upper()).options
        return [str(exp) for exp in expiries]
    except Exception:
        return []


def nearest_expiry(
    expiries: List[str],
    target: date,
    min_dte: int = 0,
) -> Optional[str]:
    """
    Pick the listed expiry closest to ``target`` (a pure, network-free helper).

    Args:
        expiries: ISO expiry strings (e.g. from ``list_option_expiries``).
        target: The desired expiry date to aim for.
        min_dte: Minimum days-to-expiry from today; expiries sooner are skipped.

    Returns:
        The best-matching expiry string, or None if none qualify.
    """
    today = date.today()
    best: Optional[str] = None
    best_gap = math.inf
    for raw in expiries:
        try:
            exp = datetime.strptime(str(raw), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if (exp - today).days < min_dte:
            continue
        gap = abs((exp - target).days)
        if gap < best_gap:
            best_gap = gap
            best = str(raw)
    return best


def get_option_chain(ticker: str, expiry: Optional[str] = None) -> Optional[OptionChain]:
    """
    Fetch the option chain for a ticker and expiry via yfinance.

    Note: yfinance supplies ``impliedVolatility`` per contract but NOT greeks;
    greeks are computed deterministically in ``src/options/pricing.py``.

    Args:
        ticker: Underlying symbol.
        expiry: ISO expiry (YYYY-MM-DD). If None, the soonest expiry is used.

    Returns:
        An ``OptionChain``, or None if the ticker has no options / on error.
    """
    symbol = ticker.strip().upper()
    try:
        handle = yf.Ticker(symbol)
        expiries = list(handle.options)
        if not expiries:
            return None
        resolved = str(expiry) if expiry and str(expiry) in expiries else str(expiries[0])
        chain = handle.option_chain(resolved)
        spot = get_current_price(symbol)
        return OptionChain(
            ticker=symbol,
            expiry=resolved,
            spot=spot,
            calls=chain.calls.copy() if chain.calls is not None else pd.DataFrame(),
            puts=chain.puts.copy() if chain.puts is not None else pd.DataFrame(),
        )
    except Exception:
        return None


def get_risk_free_rate(default: float = DEFAULT_RISK_FREE_RATE) -> float:
    """
    Return the annualized risk-free rate as a decimal (e.g. 0.045 for 4.5%).

    Uses the 13-week T-bill yield index (^IRX, quoted in percent) and converts to
    a decimal. Falls back to ``default`` if the index cannot be fetched.
    """
    try:
        hist = yf.Ticker("^IRX").history(period="5d")
        if not hist.empty:
            latest = float(hist["Close"].dropna().iloc[-1])
            rate = latest / 100.0
            if 0.0 <= rate < 0.25:  # sanity bound
                return rate
    except Exception:
        pass
    return default


def realized_volatility_from_prices(
    prices: pd.Series,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """
    Annualized realized volatility (decimal) from a price series (pure helper).

    Computes the sample standard deviation of log returns scaled by
    sqrt(periods_per_year). Returns None if there are too few observations.
    """
    if prices is None or len(prices) < 3:
        return None
    closes = pd.Series(prices).astype(float).dropna()
    if len(closes) < 3:
        return None
    log_returns = np.log(closes / closes.shift(1)).dropna()
    if len(log_returns) < 2:
        return None
    sigma = float(log_returns.std(ddof=1))
    return sigma * math.sqrt(periods_per_year)


def realized_volatility(
    ticker: str,
    window_days: int = 30,
    period: str = "3mo",
) -> Optional[float]:
    """
    Fetch daily history and return annualized realized volatility (decimal).

    Args:
        ticker: Underlying symbol.
        window_days: Trailing trading days of returns to use (most recent).
        period: yfinance lookback period to download.

    Returns:
        Annualized volatility as a decimal, or None if unavailable.
    """
    history = get_price_history([ticker], period=period, interval="1d")
    if history.empty:
        return None
    symbol = ticker.strip().upper()
    if symbol not in history.columns:
        return None
    closes = history[symbol].dropna()
    if window_days and len(closes) > window_days + 1:
        closes = closes.iloc[-(window_days + 1):]
    return realized_volatility_from_prices(closes)


def clear_cache() -> None:
    """Clear the in-memory price cache."""
    _PRICE_CACHE.clear()
