"""
trading/market/crypto.py — Binance candle fetcher (HARDENED)
=============================================================
HARDENING CHANGES:
  - _get_with_retry: catches non-JSON / HTML responses explicitly
  - _get_with_retry: exponential backoff (not linear)
  - get_candles: validates response is a non-empty list before iterating
  - get_candles: malformed candles are skipped with debug log (not crash)
  - get_candles: returns None (not empty list) on complete failure so engine skips
  - get_ticker_price: explicit validation of returned price value
  - All functions: explicit timeout on every request
  - BINANCE_BASE_URL validated at import (warns if empty)
"""
from __future__ import annotations

import time
from typing import Optional

import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

from core.config import BINANCE_BASE_URL
from core.logger import get_logger

log = get_logger(__name__)

if not BINANCE_BASE_URL:
    log.warning("crypto.py: BINANCE_BASE_URL is not set — all crypto fetches will fail")

_TF_MAP = {
    "1h": "1h",
    "15m": "15m",
    "4h": "4h",
    "1d": "1d",
}

_MAX_RETRIES = 3
_BASE_RETRY_DELAY = 2.0  # seconds; doubled each retry (exponential backoff)
_REQUEST_TIMEOUT = 10    # seconds


def _binance_tf(tf: str) -> str:
    return _TF_MAP.get(tf, tf)


def _get_with_retry(url: str, params: dict, timeout: int = _REQUEST_TIMEOUT) -> Optional[dict | list]:
    """
    HTTP GET with exponential backoff retries.
    Returns parsed JSON or None on all failures.
    Never raises.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()

            # Guard against non-JSON responses (Cloudflare errors, maintenance pages)
            try:
                return resp.json()
            except ValueError:
                log.warning(
                    "Binance: non-JSON response (HTTP %s, attempt %d/%d): %s",
                    resp.status_code, attempt, _MAX_RETRIES, resp.text[:200],
                )
                # Non-JSON is likely a gateway error — worth retrying
                raise RequestException("Non-JSON response")

        except Timeout:
            log.warning("Binance: timeout on attempt %d/%d for %s", attempt, _MAX_RETRIES, url)
        except ConnectionError as exc:
            log.warning("Binance: connection error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
        except RequestException as exc:
            log.warning("Binance: request error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
        except Exception as exc:
            log.error("Binance: unexpected error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)

        if attempt < _MAX_RETRIES:
            wait = _BASE_RETRY_DELAY * (2 ** (attempt - 1))  # exponential: 2, 4, 8...
            log.info("Binance: retrying in %.1fs...", wait)
            time.sleep(wait)
        else:
            log.error("Binance: all %d attempts failed for %s params=%s", _MAX_RETRIES, url, params)

    return None


def get_candles(symbol: str, timeframe: str, limit: int = 100) -> Optional[list[dict]]:
    """
    Fetch OHLCV candles from Binance public API.
    Returns list of dicts or None on failure.
    Sorted oldest → newest.
    """
    if not symbol or not isinstance(symbol, str):
        log.warning("crypto.get_candles: invalid symbol %r", symbol)
        return None

    interval = _binance_tf(timeframe)
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": symbol.upper().strip(), "interval": interval, "limit": limit}

    raw = _get_with_retry(url, params)
    if raw is None:
        return None

    if not isinstance(raw, list):
        log.warning("Binance: unexpected response type for %s: %s (expected list)", symbol, type(raw))
        return None

    if len(raw) == 0:
        log.warning("Binance: empty candle list for %s", symbol)
        return None

    candles = []
    for i, k in enumerate(raw):
        try:
            if not isinstance(k, (list, tuple)) or len(k) < 6:
                log.debug("Binance: skipping malformed kline at index %d for %s", i, symbol)
                continue
            candles.append({
                "timestamp": int(k[0]),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })
        except (IndexError, ValueError, TypeError) as exc:
            log.debug("Binance: skipping malformed candle at index %d for %s: %s", i, symbol, exc)

    if not candles:
        log.warning("Binance: no valid candles parsed for %s (raw had %d entries)", symbol, len(raw))
        return None

    return candles


def get_ticker_price(symbol: str) -> Optional[float]:
    """Return current last price for a symbol."""
    if not symbol:
        return None

    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    data = _get_with_retry(url, {"symbol": symbol.upper().strip()}, timeout=5)

    if not data or not isinstance(data, dict):
        log.error("Binance ticker: invalid response for %s", symbol)
        return None

    price_raw = data.get("price")
    if price_raw is None:
        log.error("Binance ticker: 'price' field missing for %s: %s", symbol, data)
        return None

    try:
        price = float(price_raw)
        if price <= 0:
            log.error("Binance ticker: non-positive price for %s: %s", symbol, price)
            return None
        return price
    except (ValueError, TypeError) as exc:
        log.error("Binance ticker: could not parse price for %s: %s", symbol, exc)
        return None
