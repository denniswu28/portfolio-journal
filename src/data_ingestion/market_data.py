"""
market_data.py - Live market data retrieval via yfinance.

Provides current price quotes and basic ticker info for portfolio enrichment.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

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


def clear_cache() -> None:
    """Clear the in-memory price cache."""
    _PRICE_CACHE.clear()
