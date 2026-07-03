"""Market data access via yfinance, with a small in-memory cache."""
import time

import pandas as pd
import yfinance as yf

_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_TTL_SECONDS = 300  # daily bars don't change often; keep API calls polite


def get_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    """Return daily OHLCV history for a symbol. Raises ValueError if empty."""
    key = f"{symbol}:{period}"
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _TTL_SECONDS:
        return cached[1]

    df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"No price data for symbol '{symbol}'")
    _CACHE[key] = (time.time(), df)
    return df


def get_last_price(symbol: str) -> float:
    df = get_history(symbol)
    return float(df["Close"].iloc[-1])


def get_last_prices(symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for s in symbols:
        try:
            prices[s] = get_last_price(s)
        except Exception:
            pass  # symbol delisted / network hiccup: skip, caller handles absence
    return prices
