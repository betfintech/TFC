"""
trading/market/unified.py — Market data router
================================================
Routes candle requests to the appropriate provider:
  - Forex symbols → Deriv CandleEngine (via forex.py)
  - Crypto symbols → Binance (crypto.py) with Deriv fallback

Single source of truth: data/candle_engine.py
"""
from __future__ import annotations

from typing import Optional

from core.utils import is_crypto_symbol
from core.logger import get_logger
from trading.market import crypto, forex

log = get_logger(__name__)


def get_candles(symbol: str, timeframe: str, limit: int = 100):
    if is_crypto_symbol(symbol):
        log.debug("Routing %s → crypto (Deriv CandleEngine primary, Binance fallback)", symbol)
        # Try Deriv CandleEngine first
        try:
            from data.candle_engine import get_candles as engine_get
            from data.candle_engine import _SYMBOL_MAP, _fetch_historical
            tf_norm = {"1h":"H1","15m":"M15","1m":"M1","5m":"M5","M1":"M1","M5":"M5","M15":"M15","H1":"H1"}.get(timeframe, "M15")
            candles = engine_get(symbol, tf_norm, limit=limit)
            if candles:
                return candles
            # Try direct historical fetch
            key = symbol.upper().strip()
            deriv_sym = _SYMBOL_MAP.get(key)
            if deriv_sym:
                hist = _fetch_historical(deriv_sym, tf_norm, count=limit)
                if hist:
                    return hist[-limit:]
        except Exception as exc:
            log.warning("Deriv engine error for crypto %s: %s", symbol, exc)
        # Binance fallback
        log.debug("Falling back to Binance for %s", symbol)
        return crypto.get_candles(symbol, timeframe, limit)
    else:
        log.debug("Routing %s → forex (Deriv)", symbol)
        return forex.get_candles(symbol, timeframe, limit)


def get_price(symbol: str) -> Optional[float]:
    """Return current price for any market symbol."""
    if is_crypto_symbol(symbol):
        return crypto.get_ticker_price(symbol)
    return forex.get_ticker_price(symbol)


def market_type(symbol: str) -> str:
    """Return 'crypto' or 'forex'."""
    return "crypto" if is_crypto_symbol(symbol) else "forex"
