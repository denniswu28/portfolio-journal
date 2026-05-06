"""
market_data.py - Live market data retrieval via yfinance.

Provides current price quotes and basic ticker info for portfolio enrichment.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf


# Simple in-memory cache: {ticker: (price, timestamp)}
_PRICE_CACHE: Dict[str, tuple[float, float]] = {}
CACHE_TTL = 300  # seconds


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


def clear_cache() -> None:
    """Clear the in-memory price cache."""
    _PRICE_CACHE.clear()
