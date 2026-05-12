"""
trading/market/forex.py — Deriv-only candle provider
=====================================================
TwelveData has been fully removed.

All candle data is sourced from the live CandleEngine (data/candle_engine.py).
"""
from __future__ import annotations

from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)

_TF_NORM: dict[str, str] = {
    "1m":    "M1",
    "M1":    "M1",
    "5m":    "M5",
    "M5":    "M5",
    "15m":   "M15",
    "M15":   "M15",
    "15min": "M15",
    "1h":    "H1",
    "H1":    "H1",
    "60m":   "H1",
    "4h":    "H1",
    "1d":    "H1",
}


def _norm_tf(tf: str) -> str:
    return _TF_NORM.get(tf, "M15")


def get_candles(symbol: str, timeframe: str, limit: int = 100) -> Optional[list[dict]]:
    """
    Return OHLCV candles from the Deriv CandleEngine.
    Falls back to a one-shot Deriv historical fetch if engine has no data.
    Returns None on complete failure.
    """
    if not symbol:
        log.warning("forex.get_candles: empty symbol")
        return None

    tf = _norm_tf(timeframe)

    try:
        from data.candle_engine import get_candles as engine_get
        candles = engine_get(symbol, tf, limit=limit)
        if candles:
            log.debug("forex: %d %s %s candles from engine", len(candles), symbol, tf)
            return candles
        log.debug("forex: engine empty for %s %s — trying direct fetch", symbol, tf)
    except Exception as exc:
        log.warning("forex: engine error for %s %s: %s", symbol, tf, exc)

    # One-shot fallback
    try:
        from data.candle_engine import _fetch_historical, _SYMBOL_MAP
        key = symbol.upper().strip().replace("_", "/")
        deriv_sym = _SYMBOL_MAP.get(key)
        if deriv_sym:
            hist = _fetch_historical(deriv_sym, tf, count=limit)
            if hist:
                log.info("forex: direct Deriv fetch: %d %s %s candles", len(hist), symbol, tf)
                return hist[-limit:]
    except Exception as exc:
        log.warning("forex: direct fetch error for %s %s: %s", symbol, tf, exc)

    log.warning("forex: no data for %s %s", symbol, tf)
    return None


def get_ticker_price(symbol: str) -> Optional[float]:
    """Return latest known price for a forex pair."""
    if not symbol:
        return None
    try:
        from data.candle_engine import get_latest_price
        price = get_latest_price(symbol)
        if price and price > 0:
            return price
    except Exception as exc:
        log.debug("forex.get_ticker_price: engine error %s: %s", symbol, exc)
    candles = get_candles(symbol, "M1", limit=1)
    if candles:
        return candles[-1].get("close")
    return None
